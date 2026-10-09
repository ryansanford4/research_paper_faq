# Research Paper FAQ

Upload a research paper (PDF) or paste an arXiv link — Claude Haiku 5.5
summarizes it, and you can chat with the paper. Papers, summaries, PDFs, and
chat history all live in Postgres, so everything you add stays available.

## Setup

The app needs two env vars: `ANTHROPIC_API_KEY` (Claude access) and
`DATABASE_URL` (Postgres — Supabase, Neon, or local).

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...   # or run `ant auth login` once
export DATABASE_URL=postgres://...    # see below
.venv/bin/uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000

### Getting a free Postgres

- **Supabase**: create a project → Project Settings → Database → Connection
  string (URI). Use the **connection pooler** URI (port 6543) for serverless-ish
  deployments, or the direct URI (5432) for a long-running server.
- **Neon**: create a project, copy the connection string.
- Local: `docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=dev postgres:16`
  then `DATABASE_URL=postgres://postgres:dev@localhost:5432/postgres`.

Tables are created automatically on startup.

## Deploying to Render

1. Push this repo to GitHub.
2. On Render: **New → Blueprint**, pick the repo — `render.yaml` configures the
   web service.
3. Set the two secret env vars when prompted (`ANTHROPIC_API_KEY`,
   `DATABASE_URL`).
4. Deploy — you get a public `https://<name>.onrender.com` URL, and deploys
   on every push to `main`.

Notes for a shared deployment:
- The library is **shared** — anyone with the URL can add, read, and chat with
  every paper. There is no auth, and **no rate limiting** — every upload and
  chat question spends your `ANTHROPIC_API_KEY` budget, so share the URL only
  with people you trust. Add an access token or rate limit before posting the
  link somewhere public.
- PDFs are stored **in Postgres** (up to 32 MB each). Free tiers cap storage
  around 500 MB — roughly a dozen very large PDFs. If that becomes a problem,
  move PDFs to object storage (S3/R2) or drop the `pdf_bytes` column.
- URL fetching has an SSRF guard (private/loopback/link-local hosts blocked,
  redirects validated hop-by-hop), but DNS rebinding between the check and
  the connect is not fully prevented — a determined attacker on your network
  path could still reach internal hosts.
- Free-tier apps sleep after inactivity; the first request after a nap takes
  ~30–60s to wake.
- Railway/Fly.io work the same way: expose `DATABASE_URL` +
  `ANTHROPIC_API_KEY` and start `uvicorn app.main:app`.

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

- Chat sends the paper's full text as a **prompt-cached** user block, so
  repeat questions don't re-bill the whole paper.
- All state lives in Postgres (`papers` and `messages` tables, PDFs stored as
  `bytea`) — back up the database to keep the library.
- Scanned/image-only PDFs can't be text-extracted and will be rejected.
