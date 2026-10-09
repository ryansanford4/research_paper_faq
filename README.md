# Research Paper FAQ

Upload a research paper (PDF) or paste an arXiv link — Claude Haiku 5.5
summarizes it, and you can chat with the paper. Papers, summaries, PDFs, and
chat history are all stored locally (SQLite + `uploads/`), so everything you
add stays available.

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...   # or run `ant auth login` once
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000

## API

| Method | Path | Description |
|---|---|---|
| POST | `/api/papers` | Add a paper (`file` upload or `url` form field) — extracts text, summarizes, stores it |
| GET | `/api/papers` | List all papers |
| GET | `/api/papers/{id}` | One paper (summary + chat history; full text stays server-side) |
| DELETE | `/api/papers/{id}` | Delete a paper, its PDF, and its chat history |
| GET | `/api/papers/{id}/pdf` | The stored PDF |
| POST | `/api/papers/{id}/chat` | `{"question": "..."}` — answers grounded in that paper |

## Notes

- Chat sends the paper's full text as a **prompt-cached** system prefix, so
  repeat questions don't re-bill the whole paper.
- `papers.db` (SQLite) and `uploads/` hold all persisted state — back those up
  to keep your library.
- Scanned/image-only PDFs can't be text-extracted and will be rejected.
