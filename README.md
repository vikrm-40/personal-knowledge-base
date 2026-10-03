\# Personal Knowledge Base



A privacy-first local application for converting personal AI conversation history into a searchable knowledge base.



The project currently supports Gemini conversation history exported through Google Takeout and provides a local UI for searching, browsing, summarizing, bookmarking, and exploring past conversations.



The application is designed so that private conversation data remains on the user's own computer.



\---



\## Features



\### Local knowledge-base generation



The application can process Gemini activity exports and convert them into a structured personal knowledge base.



It supports:



\- Gemini `MyActivity.html` imports

\- Multiple Gemini source accounts

\- Conversation extraction

\- Prompt and response preservation

\- Topic classification

\- Category and subcategory organization

\- Project / thread grouping

\- Deduplication

\- Activity / artifact separation

\- Source traceability

\- Retrieval-ready chunk generation



\---



\### Search and browse UI



The Streamlit-based interface allows users to explore their knowledge base without needing to remember exact conversation details.



Current UI features include:



\- Free-text search

\- Category dropdown

\- Topic / subcategory dropdown

\- Project / thread filtering

\- Knowledge-type filtering

\- Search suggestions

\- Topic discovery

\- Search-result summaries

\- Conversation-context expansion

\- Related-topic suggestions



\---



\### Ask My KB



The application includes a local \*\*Ask My KB\*\* feature.



Users can ask questions such as:



> What have I explored about Gemini Storybooks?



> What ideas did I discuss for improving my PDF reader?



> What projects have I explored involving AI?



The current implementation uses local retrieval and extractive summarization from the knowledge base.



No external AI API is required for this version.



\---



\### Bookmarks and saved searches



Users can save useful knowledge records for later reference.



Supported features include:



\- Bookmarked records

\- Personal bookmark notes

\- Saved searches

\- Saved Ask My KB questions



Bookmark and UI-state data are stored locally.



\---



\## Privacy



Privacy is a core design principle of this project.



The application is intended to run locally on the user's own computer.



Personal conversation history is not required to leave the user's system.



Private data files should never be committed to GitHub.



Examples include:



```text

MyActivity.html

\*.MyActivity.html

gemini\_kb.sqlite

gemini\_kb\_ui\_state.sqlite

kb\_documents.jsonl

kb\_source\_records.jsonl

kb\_chunks.jsonl

Personal\_Knowledge\_Base\*.xlsx

ChatGPT export files

