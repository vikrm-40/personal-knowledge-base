from pathlib import Path
import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

import streamlit as st


# ============================================================
# PERSONAL KNOWLEDGE BASE — UI PHASE 2
# ============================================================
# Local-only UI over gemini_kb.sqlite.
#
# Phase 2:
# - Search suggestions / autocomplete area
# - Search + browse filters
# - Local source-grounded summaries
# - Ask My KB (extractive, no external API)
# - Related-topic suggestions
# - Persistent record bookmarks
# - Persistent saved questions/searches
# - Multi-platform-ready data model for future ChatGPT records
#
# Expected folder:
#
# D:\Gemini Knowledgebase\
#   gemini_kb_ui_phase2.py
#   gemini_kb_runtime\
#       gemini_kb.sqlite
#
# Run:
#   python -m streamlit run gemini_kb_ui_phase2.py
# ============================================================


APP_TITLE = "My Personal Knowledge Base"
APP_SUBTITLE = "Search, browse, summarize and ask questions across your saved knowledge"

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "gemini_kb_runtime" / "gemini_kb.sqlite"
ALT_DB = BASE_DIR / "gemini_kb.sqlite"
STATE_DB = BASE_DIR / "gemini_kb_ui_state.sqlite"

STOPWORDS = {
    "a","an","and","are","as","at","be","been","but","by","can","could","did",
    "do","does","for","from","had","has","have","how","i","in","into","is","it",
    "its","me","my","of","on","or","our","please","show","tell","that","the",
    "their","them","there","these","they","this","to","was","we","were","what",
    "when","where","which","who","why","will","with","would","you","your",
    "about","all","any","everything","discuss","discussed","asked","ask",
    "history","chat","chats","conversation","conversations"
}


# ---------------------------- Paths / DB ----------------------------

def resolve_db_path() -> Path:
    env_path = os.getenv("GEMINI_KB_DB", "").strip()
    candidates = []
    if env_path:
        candidates.append(Path(env_path).expanduser())
    candidates.extend([DEFAULT_DB, ALT_DB])

    for p in candidates:
        try:
            p = p.resolve()
        except Exception:
            pass
        if p.exists():
            return p
    return DEFAULT_DB


DB_PATH = resolve_db_path()


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def connect_state_db() -> sqlite3.Connection:
    conn = sqlite3.connect(STATE_DB)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_state_db() -> None:
    with connect_state_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS bookmarks (
                source_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                note TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS saved_queries (
                query_id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_text TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'search',
                category TEXT DEFAULT '',
                topic TEXT DEFAULT '',
                project TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            """
        )


def database_ready() -> bool:
    if not DB_PATH.exists():
        return False
    try:
        with connect_db() as conn:
            required = {"source_records", "chunks", "documents"}
            found = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            return required.issubset(found)
    except Exception:
        return False


# ---------------------------- Metadata ----------------------------

@st.cache_data(show_spinner=False)
def get_stats() -> Dict[str, int]:
    with connect_db() as conn:
        return {
            "records": conn.execute("SELECT COUNT(*) FROM source_records").fetchone()[0],
            "topics": conn.execute(
                "SELECT COUNT(DISTINCT knowledge_topic) "
                "FROM source_records WHERE TRIM(COALESCE(knowledge_topic,'')) <> ''"
            ).fetchone()[0],
            "categories": conn.execute(
                "SELECT COUNT(DISTINCT knowledge_area) "
                "FROM source_records WHERE TRIM(COALESCE(knowledge_area,'')) <> ''"
            ).fetchone()[0],
            "projects": conn.execute(
                "SELECT COUNT(DISTINCT project_thread) "
                "FROM source_records WHERE TRIM(COALESCE(project_thread,'')) <> ''"
            ).fetchone()[0],
            "conversations": conn.execute(
                "SELECT COUNT(DISTINCT conversation_id) "
                "FROM source_records WHERE TRIM(COALESCE(conversation_id,'')) <> ''"
            ).fetchone()[0],
            "platforms": conn.execute(
                "SELECT COUNT(DISTINCT source_platform) "
                "FROM source_records WHERE TRIM(COALESCE(source_platform,'')) <> ''"
            ).fetchone()[0],
        }


@st.cache_data(show_spinner=False)
def get_categories() -> List[Tuple[str, int]]:
    with connect_db() as conn:
        rows = conn.execute(
            """
            SELECT knowledge_area, COUNT(*) AS n
            FROM source_records
            WHERE TRIM(COALESCE(knowledge_area,'')) <> ''
            GROUP BY knowledge_area
            ORDER BY knowledge_area COLLATE NOCASE
            """
        ).fetchall()
    return [(r["knowledge_area"], r["n"]) for r in rows]


@st.cache_data(show_spinner=False)
def get_topics(category: str = "") -> List[Tuple[str, int]]:
    sql = """
        SELECT knowledge_topic, COUNT(*) AS n
        FROM source_records
        WHERE TRIM(COALESCE(knowledge_topic,'')) <> ''
    """
    params: List[str] = []
    if category:
        sql += " AND knowledge_area = ?"
        params.append(category)
    sql += " GROUP BY knowledge_topic ORDER BY knowledge_topic COLLATE NOCASE"

    with connect_db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [(r["knowledge_topic"], r["n"]) for r in rows]


@st.cache_data(show_spinner=False)
def get_topic_category_map() -> Dict[str, str]:
    with connect_db() as conn:
        rows = conn.execute(
            """
            SELECT knowledge_topic, knowledge_area, COUNT(*) AS n
            FROM source_records
            WHERE TRIM(COALESCE(knowledge_topic,'')) <> ''
            GROUP BY knowledge_topic, knowledge_area
            ORDER BY n DESC
            """
        ).fetchall()

    result: Dict[str, str] = {}
    for r in rows:
        result.setdefault(r["knowledge_topic"], r["knowledge_area"])
    return result


@st.cache_data(show_spinner=False)
def get_projects() -> List[Tuple[str, int]]:
    with connect_db() as conn:
        rows = conn.execute(
            """
            SELECT project_thread, COUNT(*) AS n
            FROM source_records
            WHERE TRIM(COALESCE(project_thread,'')) <> ''
            GROUP BY project_thread
            ORDER BY project_thread COLLATE NOCASE
            """
        ).fetchall()
    return [(r["project_thread"], r["n"]) for r in rows]


@st.cache_data(show_spinner=False)
def get_knowledge_classes() -> List[Tuple[str, int]]:
    with connect_db() as conn:
        rows = conn.execute(
            """
            SELECT knowledge_class, COUNT(*) AS n
            FROM source_records
            WHERE TRIM(COALESCE(knowledge_class,'')) <> ''
            GROUP BY knowledge_class
            ORDER BY knowledge_class COLLATE NOCASE
            """
        ).fetchall()
    return [(r["knowledge_class"], r["n"]) for r in rows]


@st.cache_data(show_spinner=False)
def get_popular_topics(limit: int = 18) -> List[Tuple[str, int]]:
    with connect_db() as conn:
        rows = conn.execute(
            """
            SELECT knowledge_topic, COUNT(*) AS n
            FROM source_records
            WHERE TRIM(COALESCE(knowledge_topic,'')) <> ''
            GROUP BY knowledge_topic
            ORDER BY n DESC, knowledge_topic COLLATE NOCASE
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [(r["knowledge_topic"], r["n"]) for r in rows]


def safe_json_list(value: str) -> List[str]:
    if not value:
        return []
    try:
        obj = json.loads(value)
        if isinstance(obj, list):
            return [str(x).strip() for x in obj if str(x).strip()]
    except Exception:
        pass
    return []


@st.cache_data(show_spinner=False)
def get_suggestion_vocabulary() -> List[Dict[str, str]]:
    suggestions: List[Dict[str, str]] = []
    seen = set()

    for name, count in get_categories():
        key = ("Category", name.lower())
        if key not in seen:
            suggestions.append({"kind":"Category","value":name,"label":f"Category · {name}","count":str(count)})
            seen.add(key)

    for name, count in get_topics():
        key = ("Topic", name.lower())
        if key not in seen:
            suggestions.append({"kind":"Topic","value":name,"label":f"Topic · {name}","count":str(count)})
            seen.add(key)

    for name, count in get_projects():
        key = ("Project", name.lower())
        if key not in seen:
            suggestions.append({"kind":"Project","value":name,"label":f"Project · {name}","count":str(count)})
            seen.add(key)

    entity_counts = Counter()
    with connect_db() as conn:
        for row in conn.execute("SELECT entities_json FROM source_records"):
            for entity in safe_json_list(row["entities_json"]):
                entity_counts[entity] += 1

    for entity, count in entity_counts.most_common(120):
        key = ("Entity", entity.lower())
        if key not in seen:
            suggestions.append({"kind":"Entity","value":entity,"label":f"Entity · {entity}","count":str(count)})
            seen.add(key)

    return suggestions


def search_suggestions(query: str, limit: int = 9) -> List[Dict[str, str]]:
    vocab = get_suggestion_vocabulary()
    q = (query or "").strip().lower()

    if not q:
        popular = {name for name, _ in get_popular_topics(limit)}
        return [x for x in vocab if x["kind"] == "Topic" and x["value"] in popular][:limit]

    q_tokens = set(re.findall(r"[a-z0-9+#.-]+", q))
    scored = []

    for item in vocab:
        value = item["value"].lower()
        tokens = set(re.findall(r"[a-z0-9+#.-]+", value))
        score = 0.0

        if value == q:
            score += 100
        elif value.startswith(q):
            score += 50
        elif q in value:
            score += 30

        overlap = len(q_tokens & tokens)
        score += overlap * 8

        if any(t.startswith(q) for t in tokens):
            score += 10

        if score > 0:
            scored.append((score, -len(value), item))

    scored.sort(key=lambda x: (-x[0], x[1], x[2]["value"].lower()))
    return [x[2] for x in scored[:limit]]


# ---------------------------- Search ----------------------------

def meaningful_tokens(text: str, remove_stopwords: bool = True) -> List[str]:
    tokens = [
        x.strip(".-")
        for x in re.findall(r"[A-Za-z0-9_+#.-]+", text or "")
        if x.strip(".-")
    ]
    if remove_stopwords:
        filtered = [x for x in tokens if x.lower() not in STOPWORDS]
        if filtered:
            tokens = filtered
    return tokens


def safe_fts_query(query: str, mode: str = "Any word") -> str:
    if mode == "Exact phrase":
        tokens = meaningful_tokens(query, remove_stopwords=False)
        if not tokens:
            return ""
        phrase = " ".join(tokens).replace('"', '""')
        return f'"{phrase}"'

    tokens = meaningful_tokens(query, remove_stopwords=True)
    if not tokens:
        return ""

    quoted = [f'"{t.replace(chr(34), chr(34)*2)}"' for t in tokens]
    joiner = " AND " if mode == "All words" else " OR "
    return joiner.join(quoted)


def build_filter_sql(
    alias: str,
    category: str,
    topic: str,
    project: str,
    knowledge_class: str,
) -> Tuple[str, List[str]]:
    clauses = []
    params: List[str] = []

    if category:
        clauses.append(f"{alias}.knowledge_area = ?")
        params.append(category)
    if topic:
        clauses.append(f"{alias}.knowledge_topic = ?")
        params.append(topic)
    if project:
        clauses.append(f"{alias}.project_thread = ?")
        params.append(project)
    if knowledge_class:
        clauses.append(f"{alias}.knowledge_class = ?")
        params.append(knowledge_class)

    if not clauses:
        return "", params
    return " AND " + " AND ".join(clauses), params


def search_records(
    query: str,
    category: str = "",
    topic: str = "",
    project: str = "",
    knowledge_class: str = "",
    limit: int = 10,
    search_mode: str = "Any word",
) -> List[sqlite3.Row]:

    query = (query or "").strip()
    filter_sql, filter_params = build_filter_sql(
        "c", category, topic, project, knowledge_class
    )

    with connect_db() as conn:
        has_fts = (
            conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='chunks_fts'"
            ).fetchone()
            is not None
        )

        source_ids: List[str] = []

        if query and has_fts:
            fts = safe_fts_query(query, search_mode)
            if fts:
                try:
                    rows = conn.execute(
                        f"""
                        SELECT c.source_id, MIN(bm25(chunks_fts)) AS rank
                        FROM chunks_fts
                        JOIN chunks c ON c.chunk_id = chunks_fts.chunk_id
                        WHERE chunks_fts MATCH ?
                        {filter_sql}
                        GROUP BY c.source_id
                        ORDER BY rank
                        LIMIT ?
                        """,
                        [fts, *filter_params, limit],
                    ).fetchall()
                    source_ids = [r["source_id"] for r in rows]
                except sqlite3.OperationalError:
                    source_ids = []

        if query and not source_ids:
            terms = meaningful_tokens(query, remove_stopwords=True)
            if terms:
                where_bits = []
                like_params: List[str] = []

                for term in terms:
                    where_bits.append(
                        "(c.text LIKE ? OR c.knowledge_topic LIKE ? "
                        "OR c.project_thread LIKE ?)"
                    )
                    pattern = f"%{term}%"
                    like_params.extend([pattern, pattern, pattern])

                joiner = " AND " if search_mode == "All words" else " OR "
                like_sql = joiner.join(where_bits)

                rows = conn.execute(
                    f"""
                    SELECT c.source_id, MIN(c.date_time) AS dt
                    FROM chunks c
                    WHERE ({like_sql})
                    {filter_sql}
                    GROUP BY c.source_id
                    ORDER BY dt DESC
                    LIMIT ?
                    """,
                    [*like_params, *filter_params, limit],
                ).fetchall()
                source_ids = [r["source_id"] for r in rows]

        if not query:
            browse_filter_sql, browse_params = build_filter_sql(
                "s", category, topic, project, knowledge_class
            )
            rows = conn.execute(
                f"""
                SELECT s.source_id
                FROM source_records s
                WHERE 1=1
                {browse_filter_sql}
                ORDER BY s.date_time DESC
                LIMIT ?
                """,
                [*browse_params, limit],
            ).fetchall()
            source_ids = [r["source_id"] for r in rows]

        if not source_ids:
            return []

        placeholders = ",".join("?" for _ in source_ids)
        result_rows = conn.execute(
            f"""
            SELECT
                source_id,
                source_platform,
                conversation_id,
                record_no,
                date_time,
                kb_id,
                knowledge_area,
                knowledge_topic,
                project_thread,
                knowledge_class,
                confidence,
                entities_json,
                secondary_topics_json,
                prompt,
                response,
                source_url
            FROM source_records
            WHERE source_id IN ({placeholders})
            """,
            source_ids,
        ).fetchall()

        order = {source_id: i for i, source_id in enumerate(source_ids)}
        return sorted(result_rows, key=lambda r: order.get(r["source_id"], 999999))


def get_records_by_ids(source_ids: Sequence[str]) -> List[sqlite3.Row]:
    source_ids = list(dict.fromkeys(source_ids))
    if not source_ids:
        return []

    placeholders = ",".join("?" for _ in source_ids)
    with connect_db() as conn:
        rows = conn.execute(
            f"""
            SELECT
                source_id, source_platform, conversation_id, record_no,
                date_time, kb_id, knowledge_area, knowledge_topic,
                project_thread, knowledge_class, confidence,
                entities_json, secondary_topics_json,
                prompt, response, source_url
            FROM source_records
            WHERE source_id IN ({placeholders})
            """,
            source_ids,
        ).fetchall()

    order = {source_id: i for i, source_id in enumerate(source_ids)}
    return sorted(rows, key=lambda r: order.get(r["source_id"], 999999))


def get_conversation(conversation_id: str) -> List[sqlite3.Row]:
    if not conversation_id:
        return []

    with connect_db() as conn:
        return conn.execute(
            """
            SELECT date_time, record_no, knowledge_area, knowledge_topic,
                   project_thread, knowledge_class, prompt, response
            FROM source_records
            WHERE conversation_id = ?
            ORDER BY date_time, CAST(record_no AS INTEGER)
            """,
            (conversation_id,),
        ).fetchall()


# ---------------------------- Summaries / Ask My KB ----------------------------

def normalize_sentence(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def split_sentences(text: str) -> List[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []

    parts = re.split(r"(?<=[.!?])\s+|(?<=:)\s+(?=[A-Z0-9])", text)
    sentences = []

    for part in parts:
        part = part.strip(" •-\t")
        if len(part) < 28:
            continue
        if len(part) > 520:
            part = part[:520].rsplit(" ", 1)[0] + "…"
        sentences.append(part)

    return sentences


def token_set(text: str) -> set:
    return {
        t.lower()
        for t in meaningful_tokens(text, remove_stopwords=True)
        if len(t) >= 2
    }


def sentence_score(sentence: str, query_tokens: set, topic: str = "", project: str = "") -> float:
    sent_tokens = token_set(sentence)
    if not sent_tokens:
        return 0.0

    overlap = len(sent_tokens & query_tokens)
    score = overlap * 4.0

    if query_tokens:
        score += overlap / max(1, len(query_tokens)) * 4.0

    lower_sent = sentence.lower()
    if topic and topic.lower() in lower_sent:
        score += 2.0
    if project and project.lower() in lower_sent:
        score += 2.0

    # Penalize obvious boilerplate or meta language.
    if any(
        phrase in lower_sent
        for phrase in [
            "as an ai", "i cannot access", "i don't have access",
            "here's a breakdown", "here is a breakdown",
            "certainly!", "absolutely!"
        ]
    ):
        score -= 1.5

    return score


def build_extractive_summary(
    question: str,
    results: Sequence[sqlite3.Row],
    detail: str = "Standard",
) -> Dict[str, object]:

    if not results:
        return {
            "intro": "No matching knowledge records were found.",
            "bullets": [],
            "topics": [],
            "projects": [],
        }

    max_points = {"Brief": 3, "Standard": 5, "Detailed": 8}.get(detail, 5)
    query_tokens = token_set(question)

    topic_counts = Counter(
        r["knowledge_topic"] for r in results if r["knowledge_topic"]
    )
    project_counts = Counter(
        r["project_thread"] for r in results if r["project_thread"]
    )

    candidates = []
    for rank, row in enumerate(results):
        response = row["response"] or ""
        prompt = row["prompt"] or ""

        # Responses carry the useful knowledge; prompts are used as fallback.
        sentences = split_sentences(response)
        if not sentences:
            sentences = split_sentences(prompt)

        for pos, sentence in enumerate(sentences[:18]):
            score = sentence_score(
                sentence,
                query_tokens,
                row["knowledge_topic"] or "",
                row["project_thread"] or "",
            )

            # Prefer earlier sentences and higher-ranked retrieved records.
            score += max(0, 2.0 - rank * 0.12)
            score += max(0, 1.2 - pos * 0.08)

            candidates.append(
                (
                    score,
                    rank,
                    pos,
                    sentence,
                    row["source_id"],
                    row["knowledge_topic"] or "",
                )
            )

    candidates.sort(key=lambda x: (-x[0], x[1], x[2]))

    selected = []
    seen_norm = []
    used_per_source = Counter()

    for score, rank, pos, sentence, source_id, topic in candidates:
        norm = normalize_sentence(sentence)
        if not norm:
            continue

        # Avoid duplicate/near-duplicate sentences.
        duplicate = False
        norm_tokens = set(norm.split())
        for prev in seen_norm:
            prev_tokens = set(prev.split())
            overlap = len(norm_tokens & prev_tokens)
            denom = max(1, min(len(norm_tokens), len(prev_tokens)))
            if overlap / denom >= 0.78:
                duplicate = True
                break
        if duplicate:
            continue

        # Keep synthesis diverse across records.
        if used_per_source[source_id] >= 2:
            continue

        selected.append(sentence)
        seen_norm.append(norm)
        used_per_source[source_id] += 1

        if len(selected) >= max_points:
            break

    top_topics = [x for x, _ in topic_counts.most_common(5)]
    top_projects = [x for x, _ in project_counts.most_common(4)]

    intro = (
        f"I found {len(results)} relevant knowledge record"
        f"{'s' if len(results) != 1 else ''}."
    )

    return {
        "intro": intro,
        "bullets": selected,
        "topics": top_topics,
        "projects": top_projects,
    }


# ---------------------------- Related topics ----------------------------

@st.cache_data(show_spinner=False)
def metadata_rows() -> List[Dict[str, object]]:
    with connect_db() as conn:
        rows = conn.execute(
            """
            SELECT source_id, conversation_id, knowledge_area, knowledge_topic,
                   project_thread, entities_json, secondary_topics_json
            FROM source_records
            """
        ).fetchall()

    result = []
    for r in rows:
        result.append(
            {
                "source_id": r["source_id"],
                "conversation_id": r["conversation_id"] or "",
                "area": r["knowledge_area"] or "",
                "topic": r["knowledge_topic"] or "",
                "project": r["project_thread"] or "",
                "entities": set(safe_json_list(r["entities_json"])),
                "secondary": set(safe_json_list(r["secondary_topics_json"])),
            }
        )
    return result


def get_related_topics(
    source_ids: Sequence[str],
    current_topic: str = "",
    limit: int = 8,
) -> List[Tuple[str, str, float]]:

    ids = set(source_ids)
    if not ids:
        return []

    rows = metadata_rows()
    seeds = [r for r in rows if r["source_id"] in ids]
    if not seeds:
        return []

    seed_entities = set().union(*(r["entities"] for r in seeds))
    seed_projects = {r["project"] for r in seeds if r["project"]}
    seed_conversations = {r["conversation_id"] for r in seeds if r["conversation_id"]}
    explicit_secondary = set().union(*(r["secondary"] for r in seeds))

    scores = defaultdict(float)
    area_for = {}

    for r in rows:
        topic = r["topic"]
        if not topic or topic == current_topic:
            continue

        area_for.setdefault(topic, r["area"])
        score = 0.0

        if topic in explicit_secondary:
            score += 7.0

        shared_entities = len(seed_entities & r["entities"])
        score += shared_entities * 1.5

        if r["project"] and r["project"] in seed_projects:
            score += 4.0

        if r["conversation_id"] and r["conversation_id"] in seed_conversations:
            score += 3.0

        if current_topic and current_topic in r["secondary"]:
            score += 2.0

        if score > 0:
            scores[topic] += score

    ordered = sorted(scores.items(), key=lambda x: (-x[1], x[0].lower()))
    return [(topic, area_for.get(topic, ""), score) for topic, score in ordered[:limit]]


# ---------------------------- Bookmarks / saved queries ----------------------------

def is_bookmarked(source_id: str) -> bool:
    with connect_state_db() as conn:
        return (
            conn.execute(
                "SELECT 1 FROM bookmarks WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            is not None
        )


def toggle_bookmark(source_id: str) -> None:
    with connect_state_db() as conn:
        exists = conn.execute(
            "SELECT 1 FROM bookmarks WHERE source_id = ?",
            (source_id,),
        ).fetchone()

        if exists:
            conn.execute("DELETE FROM bookmarks WHERE source_id = ?", (source_id,))
        else:
            conn.execute(
                "INSERT INTO bookmarks(source_id, created_at, note) VALUES(?,?,?)",
                (source_id, datetime.now().isoformat(timespec="seconds"), ""),
            )


def update_bookmark_note(source_id: str, note: str) -> None:
    with connect_state_db() as conn:
        conn.execute(
            "UPDATE bookmarks SET note = ? WHERE source_id = ?",
            (note, source_id),
        )


def get_bookmarks() -> List[sqlite3.Row]:
    with connect_state_db() as conn:
        return conn.execute(
            "SELECT source_id, created_at, note FROM bookmarks ORDER BY created_at DESC"
        ).fetchall()


def save_query(
    query_text: str,
    mode: str,
    category: str = "",
    topic: str = "",
    project: str = "",
) -> None:
    query_text = (query_text or "").strip()
    if not query_text:
        return

    with connect_state_db() as conn:
        conn.execute(
            """
            INSERT INTO saved_queries(
                query_text, mode, category, topic, project, created_at
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                query_text,
                mode,
                category or "",
                topic or "",
                project or "",
                datetime.now().isoformat(timespec="seconds"),
            ),
        )


def get_saved_queries() -> List[sqlite3.Row]:
    with connect_state_db() as conn:
        return conn.execute(
            """
            SELECT query_id, query_text, mode, category, topic, project, created_at
            FROM saved_queries
            ORDER BY created_at DESC
            """
        ).fetchall()


def delete_saved_query(query_id: int) -> None:
    with connect_state_db() as conn:
        conn.execute("DELETE FROM saved_queries WHERE query_id = ?", (query_id,))


# ---------------------------- Formatting / callbacks ----------------------------

def format_date(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).strftime("%d %b %Y")
    except Exception:
        return str(value)[:10]


def short_text(text: str, max_chars: int = 260) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def result_title(row: sqlite3.Row) -> str:
    topic = row["knowledge_topic"] or "Knowledge record"
    prompt = short_text(row["prompt"] or "", 92)
    return f"{topic} — {prompt}" if prompt else topic


def on_search_category_change() -> None:
    st.session_state["search_topic"] = ""


def on_ask_category_change() -> None:
    st.session_state["ask_topic"] = ""


def apply_suggestion(kind: str, value: str) -> None:
    if kind == "Topic":
        category = get_topic_category_map().get(value, "")
        st.session_state["search_category"] = category
        st.session_state["search_topic"] = value
        st.session_state["search_query"] = ""
        st.session_state["run_filter_search"] = True
    elif kind == "Category":
        st.session_state["search_category"] = value
        st.session_state["search_topic"] = ""
        st.session_state["search_query"] = ""
        st.session_state["run_filter_search"] = True
    elif kind == "Project":
        st.session_state["search_project"] = value
        st.session_state["search_query"] = ""
        st.session_state["run_filter_search"] = True
    else:
        st.session_state["search_query"] = value


def apply_related_topic(topic: str, area: str) -> None:
    st.session_state["search_category"] = area or ""
    st.session_state["search_topic"] = topic
    st.session_state["search_query"] = ""
    st.session_state["run_filter_search"] = True


def use_saved_query(
    text: str,
    mode: str,
    category: str,
    topic: str,
    project: str,
) -> None:
    if mode == "ask":
        st.session_state["ask_question"] = text
        st.session_state["ask_category"] = category or ""
        st.session_state["ask_topic"] = topic or ""
        st.session_state["open_tab_hint"] = "ask"
    else:
        st.session_state["search_query"] = text
        st.session_state["search_category"] = category or ""
        st.session_state["search_topic"] = topic or ""
        st.session_state["search_project"] = project or ""
        st.session_state["open_tab_hint"] = "search"


# ---------------------------- Reusable result display ----------------------------

def render_related_topics(results: Sequence[sqlite3.Row], current_topic: str = "") -> None:
    if not results:
        return

    related = get_related_topics(
        [r["source_id"] for r in results],
        current_topic=current_topic,
        limit=8,
    )

    if not related:
        return

    st.markdown("#### Related topics you may want to explore")
    cols = st.columns(4)

    for i, (topic, area, score) in enumerate(related):
        cols[i % 4].button(
            topic,
            key=f"related_{topic}_{i}",
            use_container_width=True,
            on_click=apply_related_topic,
            args=(topic, area),
        )


def render_results(
    results: Sequence[sqlite3.Row],
    show_summary_button: bool = True,
    summary_query: str = "",
    current_topic: str = "",
    key_prefix: str = "result",
) -> None:

    if not results:
        st.warning("No matching records were found.")
        return

    st.markdown(
        f"### {len(results)} relevant record{'s' if len(results) != 1 else ''} found"
    )
    st.caption(
        "Source-account/file names are hidden from the normal UI. "
        "The original metadata remains preserved internally."
    )

    if show_summary_button:
        if st.button(
            "📝 Summarize these results",
            key=f"{key_prefix}_summary_button",
        ):
            summary = build_extractive_summary(
                summary_query or current_topic or "selected knowledge",
                results,
                detail="Standard",
            )
            st.markdown("#### Quick source-grounded summary")
            st.caption(
                "This is an extractive local summary made only from retrieved records."
            )
            st.write(summary["intro"])
            for bullet in summary["bullets"]:
                st.markdown(f"- {bullet}")

            if summary["topics"]:
                st.caption("Topics: " + " · ".join(summary["topics"]))
            if summary["projects"]:
                st.caption("Projects: " + " · ".join(summary["projects"]))

    for idx, row in enumerate(results, start=1):
        meta_parts = [
            row["knowledge_area"] or "",
            row["knowledge_topic"] or "",
            format_date(row["date_time"]),
        ]

        if row["project_thread"]:
            meta_parts.append(f"Project: {row['project_thread']}")
        if row["knowledge_class"]:
            meta_parts.append(row["knowledge_class"])

        meta = "  •  ".join([x for x in meta_parts if x])

        with st.container(border=True):
            top1, top2 = st.columns([7, 1])

            with top1:
                st.markdown(f"#### {idx}. {result_title(row)}")
                st.markdown(
                    f"<div class='result-meta'>{meta}</div>",
                    unsafe_allow_html=True,
                )

            with top2:
                bookmarked = is_bookmarked(row["source_id"])
                st.button(
                    "★ Saved" if bookmarked else "☆ Save",
                    key=f"{key_prefix}_bookmark_{row['source_id']}_{idx}",
                    use_container_width=True,
                    on_click=toggle_bookmark,
                    args=(row["source_id"],),
                )

            if row["prompt"]:
                st.markdown("**Your question / request**")
                st.markdown(
                    f"<div class='question-box'>{row['prompt']}</div>",
                    unsafe_allow_html=True,
                )

            response = row["response"] or ""
            if response:
                st.markdown("**Response**")
                preview = short_text(response, 650)
                st.write(preview)

                if len(response) > len(preview):
                    with st.expander("View full response"):
                        st.write(response)
            else:
                st.caption("No text response is stored for this record.")

            if row["conversation_id"]:
                with st.expander("View conversation context"):
                    convo = get_conversation(row["conversation_id"])
                    if len(convo) <= 1:
                        st.caption("No additional records are stored for this conversation.")
                    else:
                        for turn, item in enumerate(convo, start=1):
                            label = f"{turn}. {format_date(item['date_time'])}"
                            if item["knowledge_topic"]:
                                label += f" · {item['knowledge_topic']}"
                            st.markdown(f"**{label}**")

                            if item["prompt"]:
                                st.markdown("**You:**")
                                st.write(item["prompt"])
                            if item["response"]:
                                st.markdown("**Assistant:**")
                                st.write(item["response"])
                            if turn < len(convo):
                                st.divider()

    render_related_topics(results, current_topic=current_topic)


# ---------------------------- App ----------------------------

st.set_page_config(
    page_title=APP_TITLE,
    page_icon="🔎",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
      .block-container {max-width: 1240px; padding-top: 1.45rem; padding-bottom: 4rem;}
      .kb-hero {
        padding: 1.25rem 1.45rem;
        border: 1px solid rgba(128,128,128,.22);
        border-radius: 18px;
        margin-bottom: 1rem;
        background: linear-gradient(135deg, rgba(80,110,255,.09), rgba(120,80,255,.03));
      }
      .kb-title {font-size: 2rem; font-weight: 760; margin: 0;}
      .kb-subtitle {opacity: .72; margin-top: .3rem;}
      .result-meta {opacity: .67; font-size: .88rem; margin-bottom: .5rem;}
      .question-box {
        border-left: 4px solid rgba(80,110,255,.65);
        padding: .3rem 0 .3rem .85rem;
        margin: .55rem 0 .8rem 0;
      }
      .answer-box {
        border: 1px solid rgba(128,128,128,.22);
        border-radius: 16px;
        padding: 1rem 1.1rem;
        background: rgba(80,110,255,.035);
      }
      .muted {opacity: .65;}
      div[data-testid="stMetric"] {
        border: 1px solid rgba(128,128,128,.18);
        border-radius: 14px;
        padding: .5rem .75rem;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    f"""
    <div class="kb-hero">
      <div class="kb-title">🔎 {APP_TITLE}</div>
      <div class="kb-subtitle">{APP_SUBTITLE}</div>
    </div>
    """,
    unsafe_allow_html=True,
)

if not database_ready():
    st.error("The knowledge-base database could not be found.")
    st.code(str(DB_PATH))
    st.markdown(
        """
        Expected structure:

        ```text
        D:\\Gemini Knowledgebase\\
        ├── gemini_kb_ui_phase2.py
        └── gemini_kb_runtime\\
            └── gemini_kb.sqlite
        ```
        """
    )
    st.stop()

ensure_state_db()

stats = get_stats()
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Knowledge records", f"{stats['records']:,}")
m2.metric("Topics", f"{stats['topics']:,}")
m3.metric("Categories", f"{stats['categories']:,}")
m4.metric("Projects", f"{stats['projects']:,}")
m5.metric("Conversations", f"{stats['conversations']:,}")

tab_search, tab_ask, tab_bookmarks = st.tabs(
    ["🔎 Search & Browse", "💬 Ask My KB", "★ Bookmarks"]
)


# ============================ Search tab ============================

with tab_search:
    st.markdown("### Search your knowledge")

    if "search_query" not in st.session_state:
        st.session_state["search_query"] = ""
    if "search_category" not in st.session_state:
        st.session_state["search_category"] = ""
    if "search_topic" not in st.session_state:
        st.session_state["search_topic"] = ""
    if "search_project" not in st.session_state:
        st.session_state["search_project"] = ""

    query = st.text_input(
        "What do you remember discussing?",
        key="search_query",
        placeholder="e.g. Gemini Storybook, PDF reader performance, Cloudflare, AI agents…",
    )

    # Search suggestions / autocomplete area.
    suggestions = search_suggestions(query, 9)
    if suggestions:
        st.caption("Suggestions from your own knowledge history")
        suggestion_cols = st.columns(3)
        for i, item in enumerate(suggestions):
            label = f"{item['label']} ({item['count']})"
            suggestion_cols[i % 3].button(
                label,
                key=f"suggestion_{item['kind']}_{item['value']}_{i}",
                use_container_width=True,
                on_click=apply_suggestion,
                args=(item["kind"], item["value"]),
            )

    categories = get_categories()
    category_names = [""] + [name for name, _ in categories]
    category_counts = dict(categories)

    def format_category(x: str) -> str:
        return "All categories" if not x else f"{x} ({category_counts.get(x,0)})"

    f1, f2, f3 = st.columns(3)

    with f1:
        category = st.selectbox(
            "Category",
            category_names,
            key="search_category",
            format_func=format_category,
            on_change=on_search_category_change,
        )

    topics = get_topics(category)
    topic_names = [""] + [name for name, _ in topics]
    topic_counts = dict(topics)

    if st.session_state.get("search_topic") not in topic_names:
        st.session_state["search_topic"] = ""

    def format_topic(x: str) -> str:
        return "All topics" if not x else f"{x} ({topic_counts.get(x,0)})"

    with f2:
        topic = st.selectbox(
            "Topic / Subcategory",
            topic_names,
            key="search_topic",
            format_func=format_topic,
        )

    projects = get_projects()
    project_names = [""] + [name for name, _ in projects]
    project_counts = dict(projects)

    def format_project(x: str) -> str:
        return "All projects" if not x else f"{x} ({project_counts.get(x,0)})"

    with f3:
        project = st.selectbox(
            "Project / Thread",
            project_names,
            key="search_project",
            format_func=format_project,
        )

    knowledge_class = ""
    search_mode = "Any word"
    result_limit = 10

    with st.expander("More filters", expanded=False):
        a1, a2, a3 = st.columns(3)

        classes = get_knowledge_classes()
        class_names = [""] + [name for name, _ in classes]
        class_counts = dict(classes)

        with a1:
            knowledge_class = st.selectbox(
                "Knowledge type",
                class_names,
                format_func=lambda x: "All knowledge types"
                if not x
                else f"{x} ({class_counts.get(x,0)})",
                key="search_knowledge_class",
            )

        with a2:
            search_mode = st.selectbox(
                "Text matching",
                ["Any word", "All words", "Exact phrase"],
                key="search_mode",
            )

        with a3:
            result_limit = st.selectbox(
                "Maximum results",
                [5, 10, 15, 25, 50],
                index=1,
                key="search_limit",
            )

    c1, c2, c3 = st.columns([1.2, 1, 3.8])

    with c1:
        search_clicked = st.button(
            "🔎 Search Knowledge Base",
            type="primary",
            use_container_width=True,
        )

    with c2:
        if query.strip():
            st.button(
                "💾 Save search",
                use_container_width=True,
                on_click=save_query,
                args=(query, "search", category, topic, project),
            )

    with c3:
        if not query and (category or topic or project or knowledge_class):
            st.caption("No text entered — the selected filters will be browsed.")
        elif not query:
            st.caption("Type a search, use a suggestion, or browse by Category → Topic.")

    with st.expander("💡 Topics I have explored", expanded=False):
        popular = get_popular_topics(18)
        cols = st.columns(3)

        for i, (name, count) in enumerate(popular):
            area = get_topic_category_map().get(name, "")
            cols[i % 3].button(
                f"{name} ({count})",
                key=f"popular_{name}_{i}",
                use_container_width=True,
                on_click=apply_related_topic,
                args=(name, area),
            )

    force_filter_search = bool(st.session_state.pop("run_filter_search", False))
    should_search = search_clicked or bool(query.strip()) or force_filter_search

    if should_search:
        with st.spinner("Searching your knowledge base…"):
            search_results = search_records(
                query=query,
                category=category,
                topic=topic,
                project=project,
                knowledge_class=knowledge_class,
                limit=result_limit,
                search_mode=search_mode,
            )

        st.markdown("---")
        render_results(
            search_results,
            show_summary_button=True,
            summary_query=query,
            current_topic=topic,
            key_prefix="search",
        )
    else:
        st.markdown("---")
        st.info(
            "Enter a search, choose a suggestion, or select a Category / Topic / Project."
        )


# ============================ Ask My KB tab ============================

with tab_ask:
    st.markdown("### Ask My KB")
    st.caption(
        "This version answers locally using retrieved records and extractive summaries. "
        "No external AI/API call is made."
    )

    if "ask_question" not in st.session_state:
        st.session_state["ask_question"] = ""
    if "ask_category" not in st.session_state:
        st.session_state["ask_category"] = ""
    if "ask_topic" not in st.session_state:
        st.session_state["ask_topic"] = ""

    question = st.text_area(
        "Ask a question about your past knowledge",
        key="ask_question",
        height=95,
        placeholder=(
            "e.g. What have I explored about Gemini Storybooks?\n"
            "What ideas did I discuss for improving my PDF reader?"
        ),
    )

    ask_suggestions = search_suggestions(question, 6)
    if ask_suggestions and question.strip():
        st.caption("Possibly related concepts")
        cols = st.columns(3)
        for i, item in enumerate(ask_suggestions[:6]):
            cols[i % 3].caption(item["label"])

    ask_categories = get_categories()
    ask_category_names = [""] + [x for x, _ in ask_categories]
    ask_category_counts = dict(ask_categories)

    af1, af2, af3 = st.columns(3)

    with af1:
        ask_category = st.selectbox(
            "Limit to category (optional)",
            ask_category_names,
            key="ask_category",
            format_func=lambda x: "All categories"
            if not x
            else f"{x} ({ask_category_counts.get(x,0)})",
            on_change=on_ask_category_change,
        )

    ask_topics = get_topics(ask_category)
    ask_topic_names = [""] + [x for x, _ in ask_topics]
    ask_topic_counts = dict(ask_topics)

    if st.session_state.get("ask_topic") not in ask_topic_names:
        st.session_state["ask_topic"] = ""

    with af2:
        ask_topic = st.selectbox(
            "Limit to topic (optional)",
            ask_topic_names,
            key="ask_topic",
            format_func=lambda x: "All topics"
            if not x
            else f"{x} ({ask_topic_counts.get(x,0)})",
        )

    with af3:
        answer_detail = st.selectbox(
            "Answer detail",
            ["Brief", "Standard", "Detailed"],
            index=1,
        )

    q1, q2, q3 = st.columns([1.2, 1, 3.8])

    with q1:
        ask_clicked = st.button(
            "💬 Ask My KB",
            type="primary",
            use_container_width=True,
        )

    with q2:
        if question.strip():
            st.button(
                "💾 Save question",
                use_container_width=True,
                on_click=save_query,
                args=(question, "ask", ask_category, ask_topic, ""),
            )

    with q3:
        st.caption(
            "The answer is synthesized only from records retrieved from your local KB."
        )

    if ask_clicked and question.strip():
        evidence_limit = {
            "Brief": 6,
            "Standard": 10,
            "Detailed": 15,
        }[answer_detail]

        with st.spinner("Searching and assembling your local answer…"):
            ask_results = search_records(
                query=question,
                category=ask_category,
                topic=ask_topic,
                project="",
                knowledge_class="",
                limit=evidence_limit,
                search_mode="Any word",
            )

            answer = build_extractive_summary(
                question,
                ask_results,
                detail=answer_detail,
            )

        st.markdown("---")

        if not ask_results:
            st.warning(
                "I couldn't find enough matching records. Try a shorter question "
                "or select a Category / Topic."
            )
        else:
            st.markdown("#### Answer from your knowledge base")
            st.markdown("<div class='answer-box'>", unsafe_allow_html=True)
            st.write(answer["intro"])

            for bullet in answer["bullets"]:
                st.markdown(f"- {bullet}")

            st.markdown("</div>", unsafe_allow_html=True)

            if answer["topics"]:
                st.caption("Main topics: " + " · ".join(answer["topics"]))
            if answer["projects"]:
                st.caption("Related projects: " + " · ".join(answer["projects"]))

            render_related_topics(
                ask_results,
                current_topic=ask_topic,
            )

            with st.expander(
                f"View the {len(ask_results)} supporting record"
                f"{'s' if len(ask_results) != 1 else ''}"
            ):
                for i, row in enumerate(ask_results, start=1):
                    st.markdown(
                        f"**{i}. {row['knowledge_topic'] or 'Knowledge record'} "
                        f"· {format_date(row['date_time'])}**"
                    )
                    if row["prompt"]:
                        st.markdown("**You:**")
                        st.write(row["prompt"])
                    if row["response"]:
                        st.markdown("**Response:**")
                        st.write(row["response"])
                    if i < len(ask_results):
                        st.divider()


# ============================ Bookmarks tab ============================

with tab_bookmarks:
    st.markdown("### Saved knowledge")

    bookmark_tab, query_tab = st.tabs(["★ Bookmarked records", "💾 Saved searches & questions"])

    with bookmark_tab:
        bookmarks = get_bookmarks()

        if not bookmarks:
            st.info(
                "No bookmarks yet. Use ☆ Save on any search result to keep it here."
            )
        else:
            bookmarked_ids = [r["source_id"] for r in bookmarks]
            bookmark_meta = {r["source_id"]: r for r in bookmarks}
            records = get_records_by_ids(bookmarked_ids)

            st.caption(f"{len(records)} bookmarked knowledge record(s)")

            for i, row in enumerate(records, start=1):
                meta = bookmark_meta.get(row["source_id"])
                with st.container(border=True):
                    h1, h2 = st.columns([7,1])

                    with h1:
                        st.markdown(f"#### {i}. {result_title(row)}")
                        st.caption(
                            " · ".join(
                                x for x in [
                                    row["knowledge_area"] or "",
                                    row["knowledge_topic"] or "",
                                    format_date(row["date_time"]),
                                ] if x
                            )
                        )

                    with h2:
                        st.button(
                            "Remove",
                            key=f"remove_bookmark_{row['source_id']}_{i}",
                            use_container_width=True,
                            on_click=toggle_bookmark,
                            args=(row["source_id"],),
                        )

                    if row["prompt"]:
                        st.markdown("**Your question / request**")
                        st.write(row["prompt"])

                    if row["response"]:
                        with st.expander("View response"):
                            st.write(row["response"])

                    note = st.text_input(
                        "Personal note",
                        value=meta["note"] if meta else "",
                        key=f"bookmark_note_{row['source_id']}_{i}",
                        placeholder="Optional note about why this is useful…",
                    )

                    if st.button(
                        "Save note",
                        key=f"save_note_{row['source_id']}_{i}",
                    ):
                        update_bookmark_note(row["source_id"], note)
                        st.success("Note saved.")

    with query_tab:
        saved = get_saved_queries()

        if not saved:
            st.info(
                "Saved searches and Ask My KB questions will appear here."
            )
        else:
            for i, row in enumerate(saved, start=1):
                with st.container(border=True):
                    c1, c2 = st.columns([7,1])

                    with c1:
                        icon = "💬" if row["mode"] == "ask" else "🔎"
                        st.markdown(f"**{icon} {row['query_text']}**")
                        meta_bits = [
                            row["category"] or "",
                            row["topic"] or "",
                            row["project"] or "",
                            format_date(row["created_at"]),
                        ]
                        st.caption(" · ".join(x for x in meta_bits if x))

                    with c2:
                        st.button(
                            "Delete",
                            key=f"delete_saved_{row['query_id']}_{i}",
                            use_container_width=True,
                            on_click=delete_saved_query,
                            args=(row["query_id"],),
                        )

                    # Streamlit tabs cannot be programmatically switched reliably,
                    # but this prepares the selected query for its relevant tab.
                    st.button(
                        "Load into Ask My KB" if row["mode"] == "ask" else "Load into Search",
                        key=f"load_saved_{row['query_id']}_{i}",
                        on_click=use_saved_query,
                        args=(
                            row["query_text"],
                            row["mode"],
                            row["category"],
                            row["topic"],
                            row["project"],
                        ),
                    )

st.markdown("---")
st.caption(
    "Phase 2 · Local retrieval + extractive Ask My KB · "
    "autocomplete suggestions · related topics · persistent bookmarks · "
    "source-account names hidden."
)
