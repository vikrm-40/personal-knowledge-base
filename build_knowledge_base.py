from __future__ import annotations

from pathlib import Path
from datetime import datetime
import argparse
import hashlib
import json
import re
import sqlite3
import sys

try:
    from openpyxl import load_workbook
except ImportError:
    print("Missing dependency. Run: python -m pip install openpyxl")
    raise

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = BASE_DIR / "Personal_Knowledge_Base_V3_3_MultiSource.xlsx"
DEFAULT_OUTPUT_DIR = BASE_DIR / "gemini_kb_runtime"

CHUNK_TARGET_CHARS = 1800
CHUNK_OVERLAP_CHARS = 250


def clean(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def slug_hash(*parts, prefix="id"):
    raw = "|".join(clean(p) for p in parts)
    return f"{prefix}_{hashlib.sha1(raw.encode('utf-8', errors='ignore')).hexdigest()[:16]}"


def as_iso(value):
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if value in (None, ""):
        return ""
    return clean(value)


def split_multi(value):
    text = clean(value)
    if not text:
        return []
    return [x.strip() for x in re.split(r"\s*\|\s*|\s*;\s*", text) if x.strip()]


def chunk_text(text, target=CHUNK_TARGET_CHARS, overlap=CHUNK_OVERLAP_CHARS):
    """Paragraph/sentence-aware character chunker with small overlap."""
    text = clean(text)
    if not text:
        return []
    if len(text) <= target:
        return [text]

    # Sentence-ish units. This is intentionally dependency-free.
    units = [u.strip() for u in re.split(r"(?<=[.!?])\s+|\n+", text) if u.strip()]
    chunks = []
    current = ""

    for unit in units:
        if not current:
            current = unit
            continue
        candidate = current + " " + unit
        if len(candidate) <= target:
            current = candidate
        else:
            chunks.append(current)
            tail = current[-overlap:] if overlap and len(current) > overlap else current
            current = (tail + " " + unit).strip()
            # Hard split a pathological single giant unit.
            while len(current) > target * 1.35:
                chunks.append(current[:target])
                current = current[max(0, target - overlap):]

    if current:
        chunks.append(current)
    return chunks


def rows_as_dicts(ws):
    it = ws.iter_rows(values_only=True)
    try:
        headers = [clean(x) for x in next(it)]
    except StopIteration:
        return []
    result = []
    for row in it:
        if not any(v not in (None, "") for v in row):
            continue
        d = {}
        for i, h in enumerate(headers):
            if h:
                d[h] = row[i] if i < len(row) else None
        result.append(d)
    return result


def read_master(path: Path):
    if not path.exists():
        raise FileNotFoundError(
            f"Input workbook not found: {path}\n"
            "Place this script beside Personal_Knowledge_Base_V3_3_MultiSource.xlsx "
            "or pass --input with the full path."
        )

    wb = load_workbook(path, read_only=True, data_only=True)
    if "Sources" not in wb.sheetnames or "Knowledge Base" not in wb.sheetnames:
        raise ValueError("Workbook must contain both 'Sources' and 'Knowledge Base' sheets.")

    sources = rows_as_dicts(wb["Sources"])
    kb_rows = rows_as_dicts(wb["Knowledge Base"])
    projects = rows_as_dicts(wb["Projects"]) if "Projects" in wb.sheetnames else []
    return sources, kb_rows, projects


def build_documents(kb_rows):
    docs = []
    for row in kb_rows:
        kb_id = clean(row.get("KB ID"))
        if not kb_id:
            continue

        area = clean(row.get("Knowledge Area"))
        topic = clean(row.get("Knowledge Topic"))
        project = clean(row.get("Project / Thread"))
        klass = clean(row.get("Primary Knowledge Class"))
        summary = clean(row.get("Source-Grounded Summary"))
        questions = clean(row.get("Key Questions / Interests"))
        extracts = clean(row.get("Representative Gemini Extracts"))

        document_text = "\n\n".join(x for x in [
            f"Knowledge Area: {area}" if area else "",
            f"Knowledge Topic: {topic}" if topic else "",
            f"Project / Thread: {project}" if project else "",
            f"Knowledge Class: {klass}" if klass else "",
            f"Summary: {summary}" if summary else "",
            f"Key Questions / Interests: {questions}" if questions else "",
            f"Representative Gemini Material: {extracts}" if extracts else "",
        ] if x)

        docs.append({
            "document_id": kb_id,
            "source_platform": "Gemini",
            "knowledge_area": area,
            "knowledge_topic": topic,
            "project_thread": project,
            "knowledge_class": klass,
            "source_accounts": split_multi(row.get("Source Accounts")),
            "source_records": int(row.get("Source Records") or 0),
            "conversation_count": int(row.get("Conversation Count") or 0),
            "first_activity": as_iso(row.get("First Activity")),
            "last_activity": as_iso(row.get("Last Activity")),
            "confidence": clean(row.get("Confidence")),
            "entities": split_multi(row.get("Entities / Tools")),
            "summary": summary,
            "key_questions": questions,
            "text": document_text,
        })
    return docs


def build_source_records_and_chunks(sources):
    source_records = []
    chunks = []

    for row in sources:
        # Only durable prompted Q&A enters the RAG corpus. Activity/artifact/noise is
        # still represented in the master XLSX, but not injected as answer context.
        if clean(row.get("Record Layer")) != "Prompted Q&A":
            continue

        prompt = clean(row.get("User Prompt / Source Text"))
        response = clean(row.get("Gemini Response"))
        if not prompt and not response:
            continue

        record_no = clean(row.get("Record #"))
        source_account = clean(row.get("Source Account"))
        source_file = clean(row.get("Source File"))
        conv = clean(row.get("Conversation ID"))
        kb_id = clean(row.get("KB ID"))
        area = clean(row.get("New Category"))
        topic = clean(row.get("New Topic"))
        project = clean(row.get("Project / Thread"))
        klass = clean(row.get("New Knowledge Class"))
        date = as_iso(row.get("Date/Time"))
        url = clean(row.get("Gemini URL"))

        source_id = slug_hash(source_account, record_no, conv, prompt, prefix="src")
        qa_text = f"User question/request:\n{prompt}"
        if response:
            qa_text += f"\n\nGemini response:\n{response}"

        source_records.append({
            "source_id": source_id,
            "source_platform": "Gemini",
            "source_account": source_account,
            "source_file": source_file,
            "record_no": record_no,
            "conversation_id": conv,
            "date_time": date,
            "kb_id": kb_id,
            "knowledge_area": area,
            "knowledge_topic": topic,
            "project_thread": project,
            "knowledge_class": klass,
            "confidence": clean(row.get("Confidence")),
            "classification_basis": clean(row.get("Classification Basis")),
            "entities": split_multi(row.get("Entities")),
            "secondary_topics": split_multi(row.get("Secondary Topics")),
            "prompt": prompt,
            "response": response,
            "source_url": url,
            "text": qa_text,
        })

        pieces = chunk_text(qa_text)
        for idx, piece in enumerate(pieces, 1):
            chunks.append({
                "chunk_id": slug_hash(source_id, str(idx), piece[:200], prefix="chunk"),
                "source_id": source_id,
                "kb_id": kb_id,
                "source_platform": "Gemini",
                "source_account": source_account,
                "conversation_id": conv,
                "record_no": record_no,
                "date_time": date,
                "knowledge_area": area,
                "knowledge_topic": topic,
                "project_thread": project,
                "knowledge_class": klass,
                "confidence": clean(row.get("Confidence")),
                "chunk_index": idx,
                "chunk_count": len(pieces),
                "source_url": url,
                "text": piece,
            })

    return source_records, chunks


def write_jsonl(path: Path, rows):
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def build_sqlite(path: Path, documents, source_records, chunks):
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    conn.executescript("""
    CREATE TABLE documents (
        document_id TEXT PRIMARY KEY,
        source_platform TEXT,
        knowledge_area TEXT,
        knowledge_topic TEXT,
        project_thread TEXT,
        knowledge_class TEXT,
        source_accounts_json TEXT,
        source_records INTEGER,
        conversation_count INTEGER,
        first_activity TEXT,
        last_activity TEXT,
        confidence TEXT,
        entities_json TEXT,
        summary TEXT,
        key_questions TEXT,
        text TEXT
    );

    CREATE TABLE source_records (
        source_id TEXT PRIMARY KEY,
        source_platform TEXT,
        source_account TEXT,
        source_file TEXT,
        record_no TEXT,
        conversation_id TEXT,
        date_time TEXT,
        kb_id TEXT,
        knowledge_area TEXT,
        knowledge_topic TEXT,
        project_thread TEXT,
        knowledge_class TEXT,
        confidence TEXT,
        classification_basis TEXT,
        entities_json TEXT,
        secondary_topics_json TEXT,
        prompt TEXT,
        response TEXT,
        source_url TEXT,
        text TEXT
    );

    CREATE TABLE chunks (
        chunk_id TEXT PRIMARY KEY,
        source_id TEXT,
        kb_id TEXT,
        source_platform TEXT,
        source_account TEXT,
        conversation_id TEXT,
        record_no TEXT,
        date_time TEXT,
        knowledge_area TEXT,
        knowledge_topic TEXT,
        project_thread TEXT,
        knowledge_class TEXT,
        confidence TEXT,
        chunk_index INTEGER,
        chunk_count INTEGER,
        source_url TEXT,
        text TEXT
    );

    CREATE INDEX idx_chunks_topic ON chunks(knowledge_topic);
    CREATE INDEX idx_chunks_project ON chunks(project_thread);
    CREATE INDEX idx_chunks_kb ON chunks(kb_id);
    CREATE INDEX idx_chunks_account ON chunks(source_account);
    CREATE INDEX idx_source_conv ON source_records(source_account, conversation_id);
    """)

    conn.executemany(
        "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(
            d["document_id"], d["source_platform"], d["knowledge_area"], d["knowledge_topic"],
            d["project_thread"], d["knowledge_class"], json.dumps(d["source_accounts"], ensure_ascii=False),
            d["source_records"], d["conversation_count"], d["first_activity"], d["last_activity"],
            d["confidence"], json.dumps(d["entities"], ensure_ascii=False), d["summary"], d["key_questions"], d["text"]
        ) for d in documents]
    )

    conn.executemany(
        "INSERT INTO source_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(
            r["source_id"], r["source_platform"], r["source_account"], r["source_file"], r["record_no"],
            r["conversation_id"], r["date_time"], r["kb_id"], r["knowledge_area"], r["knowledge_topic"],
            r["project_thread"], r["knowledge_class"], r["confidence"], r["classification_basis"],
            json.dumps(r["entities"], ensure_ascii=False), json.dumps(r["secondary_topics"], ensure_ascii=False),
            r["prompt"], r["response"], r["source_url"], r["text"]
        ) for r in source_records]
    )

    conn.executemany(
        "INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(
            c["chunk_id"], c["source_id"], c["kb_id"], c["source_platform"], c["source_account"],
            c["conversation_id"], c["record_no"], c["date_time"], c["knowledge_area"], c["knowledge_topic"],
            c["project_thread"], c["knowledge_class"], c["confidence"], c["chunk_index"], c["chunk_count"],
            c["source_url"], c["text"]
        ) for c in chunks]
    )

    # FTS5 is present in normal Python sqlite builds. If unavailable, the core DB
    # remains usable and the script falls back to LIKE-based search.
    try:
        conn.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED, text, knowledge_topic, project_thread, content='')")
        conn.executemany(
            "INSERT INTO chunks_fts(chunk_id,text,knowledge_topic,project_thread) VALUES (?,?,?,?)",
            [(c["chunk_id"], c["text"], c["knowledge_topic"], c["project_thread"]) for c in chunks]
        )
        fts5 = True
    except sqlite3.OperationalError:
        fts5 = False

    conn.commit()
    conn.close()
    return fts5


def build(input_path: Path, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    sources, kb_rows, projects = read_master(input_path)
    documents = build_documents(kb_rows)
    source_records, chunks = build_source_records_and_chunks(sources)

    write_jsonl(output_dir / "kb_documents.jsonl", documents)
    write_jsonl(output_dir / "kb_source_records.jsonl", source_records)
    write_jsonl(output_dir / "kb_chunks.jsonl", chunks)

    fts5 = build_sqlite(output_dir / "gemini_kb.sqlite", documents, source_records, chunks)

    manifest = {
        "schema_version": "1.0",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_platforms": ["Gemini"],
        "input_workbook": input_path.name,
        "documents": len(documents),
        "source_records": len(source_records),
        "chunks": len(chunks),
        "projects_in_workbook": len(projects),
        "fts5_enabled": fts5,
        "chunk_target_chars": CHUNK_TARGET_CHARS,
        "chunk_overlap_chars": CHUNK_OVERLAP_CHARS,
        "future_extension": "ChatGPT conversations can later be normalized into the same source_records/chunks schema.",
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    return manifest


def search_db(db_path: Path, query: str, limit=8):
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}. Run --build first.")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    has_fts = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunks_fts'").fetchone() is not None
    rows = []
    if has_fts:
        try:
            rows = conn.execute(
                """
                SELECT c.chunk_id, c.knowledge_area, c.knowledge_topic, c.project_thread,
                       c.source_account, c.record_no, c.source_url, c.text,
                       bm25(chunks_fts) AS rank
                FROM chunks_fts
                JOIN chunks c ON c.chunk_id = chunks_fts.chunk_id
                WHERE chunks_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []

    if not rows:
        like = f"%{query}%"
        rows = conn.execute(
            """
            SELECT chunk_id, knowledge_area, knowledge_topic, project_thread,
                   source_account, record_no, source_url, text, 0 AS rank
            FROM chunks
            WHERE text LIKE ? OR knowledge_topic LIKE ? OR project_thread LIKE ?
            LIMIT ?
            """,
            (like, like, like, limit),
        ).fetchall()
    conn.close()

    if not rows:
        print("No matches.")
        return
    for i, r in enumerate(rows, 1):
        print("\n" + "=" * 80)
        print(f"#{i}  {r['knowledge_area']} > {r['knowledge_topic']}")
        if r["project_thread"]:
            print(f"Project: {r['project_thread']}")
        print(f"Source: {r['source_account']} / record {r['record_no']}")
        if r["source_url"]:
            print(f"URL: {r['source_url']}")
        print("-" * 80)
        print(r["text"][:1800])


def main():
    parser = argparse.ArgumentParser(description="Build a Gemini-only retrieval/RAG-ready knowledge base from the V3.3 multi-source workbook.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Path to Personal_Knowledge_Base_V3_3_MultiSource.xlsx")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Output directory")
    parser.add_argument("--search", help="Search the already-built local Gemini KB")
    parser.add_argument("--limit", type=int, default=8, help="Maximum search results")
    args = parser.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()

    if args.search:
        search_db(output_dir / "gemini_kb.sqlite", args.search, args.limit)
        return

    print("=" * 78)
    print("GEMINI V3.3 — RETRIEVAL / RAG-READY KB BUILDER")
    print("=" * 78)
    print(f"Input : {input_path}")
    print(f"Output: {output_dir}")
    manifest = build(input_path, output_dir)
    print("\nBUILD COMPLETE")
    for k, v in manifest.items():
        print(f"{k:22}: {v}")
    print("\nTry a local search, for example:")
    print('python build_gemini_rag_ready_kb.py --search "PDF reader"')
    print("=" * 78)


if __name__ == "__main__":
    main()
