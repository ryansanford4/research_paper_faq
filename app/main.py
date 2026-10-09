"""Research Paper FAQ — FastAPI app.

Upload or link a research paper, get a Haiku-generated summary,
and chat about it. Papers, summaries, PDFs, and chat history persist in
Postgres.
"""

import logging
import re

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app import db, extract, llm

app = FastAPI(title="Research Paper FAQ")

MAX_PDF_BYTES = 32 * 1024 * 1024  # matches the API's 32 MB request limit
MAX_BODY_BYTES = MAX_PDF_BYTES + 1024 * 1024  # multipart overhead headroom


@app.middleware("http")
async def limit_request_body(request, call_next):
    """Reject oversized uploads before Starlette buffers/spools the body."""
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_BODY_BYTES:
        return JSONResponse(status_code=413,
                            content={"detail": "Request body too large (max 32 MB)."})
    return await call_next(request)


@app.on_event("startup")
def startup() -> None:
    db.init_db()


@app.post("/api/papers")
def add_paper(
    file: UploadFile | None = File(None),
    url: str | None = Form(None),
):
    """Ingest a paper from an uploaded PDF or a URL (arXiv abs/pdf or direct .pdf).

    Extracts text, summarizes with Haiku, and stores everything.
    Runs as a plain `def` route so the blocking download/parse/LLM work
    happens in the threadpool, not on the event loop.
    """
    if not file and not url:
        raise HTTPException(400, "Provide a PDF file or a URL.")
    if file and url:
        raise HTTPException(400, "Provide either a file or a URL, not both.")

    try:
        if file:
            # Capped read: one extra byte tells us it overshot the limit.
            data = file.file.read(MAX_PDF_BYTES + 1)
            source = file.filename or "upload"
            if len(data) > MAX_PDF_BYTES:
                raise HTTPException(400, "PDF exceeds the 32 MB limit.")
            if not data[:5].startswith(b"%PDF-"):
                raise HTTPException(400, "Uploaded file does not look like a PDF.")
            title, full_text = extract.extract_pdf_text(data)
        else:
            url = url.strip()
            if not re.match(r"^https?://", url):
                raise HTTPException(400, "URL must start with http:// or https://")
            try:
                pdf_url, original_url, data = extract.fetch_pdf_from_url(url)
            except ValueError as e:
                raise HTTPException(400, str(e))
            source = original_url
            title, full_text = extract.extract_pdf_text(data)

        try:
            title, summary = llm.summarize(full_text)
        except Exception:
            # summarization failure shouldn't lose the paper
            logging.getLogger(__name__).exception("summarize failed")
            summary = "(Summarization failed — check the server log. " \
                      "Is ANTHROPIC_API_KEY set?)"

        paper_id = db.insert_paper(title, source, data, full_text, summary)

        return JSONResponse({"id": paper_id, "title": title, "source": source,
                             "summary": summary}, status_code=201)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/papers")
def list_papers():
    return db.list_papers()


@app.get("/api/papers/{paper_id}")
def get_paper(paper_id: int):
    paper = db.get_paper(paper_id)
    if not paper:
        raise HTTPException(404, "Paper not found.")
    # Don't ship ~400KB of text or the PDF blob to the browser.
    paper.pop("full_text", None)
    paper.pop("pdf_bytes", None)
    paper["created_at"] = paper["created_at"].isoformat()
    paper["messages"] = db.get_messages(paper_id)
    return paper


@app.delete("/api/papers/{paper_id}")
def remove_paper(paper_id: int):
    if not db.get_paper(paper_id):
        raise HTTPException(404, "Paper not found.")
    db.delete_paper(paper_id)
    return {"deleted": paper_id}


@app.get("/api/papers/{paper_id}/pdf")
def get_paper_pdf(paper_id: int):
    if not db.get_paper(paper_id):
        raise HTTPException(404, "Paper not found.")
    pdf = db.get_paper_pdf(paper_id)
    if not pdf:
        raise HTTPException(404, "No stored PDF for this paper.")
    return Response(content=bytes(pdf), media_type="application/pdf",
                    headers={"Content-Disposition":
                             f'inline; filename="{paper_id}.pdf"'})


@app.post("/api/papers/{paper_id}/chat")
def chat(paper_id: int, body: dict):
    question = (body or {}).get("question", "").strip()
    if not question:
        raise HTTPException(400, "Question must not be empty.")
    paper = db.get_paper(paper_id)
    if not paper:
        raise HTTPException(404, "Paper not found.")

    # Serialize whole turns per paper (session-level advisory lock held
    # across the model call): concurrent chats on the same paper would
    # otherwise interleave their user/assistant inserts and corrupt the
    # stored transcript (and the history the model sees next turn).
    conn = db.get_conn()
    conn.execute("SELECT pg_advisory_lock(%s)", (paper_id,))
    try:
        history = conn.execute(
            "SELECT role, content FROM messages WHERE paper_id = %s ORDER BY id",
            (paper_id,),
        ).fetchall()
        conn.execute(
            "INSERT INTO messages (paper_id, role, content) VALUES (%s, %s, %s)",
            (paper_id, "user", question),
        )
        try:
            answer = llm.chat(paper["full_text"], history, question)
        except Exception:
            logging.getLogger(__name__).exception("chat failed")
            raise HTTPException(502, "The model call failed — check the server log.")
        try:
            conn.execute(
                "INSERT INTO messages (paper_id, role, content) VALUES (%s, %s, %s)",
                (paper_id, "assistant", answer),
            )
        except Exception:
            # Paper was deleted mid-chat; nothing left to store for.
            logging.getLogger(__name__).warning(
                "paper %s vanished mid-chat", paper_id)
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (paper_id,))
        conn.close()
    return {"role": "assistant", "content": answer}


app.mount("/", StaticFiles(directory="static", html=True), name="static")
