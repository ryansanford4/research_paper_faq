"""Research Paper FAQ — FastAPI app.

Upload or link a research paper, get a Haiku-generated summary,
and chat about it. Papers and chat history persist in SQLite.
"""

import logging
import re
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import db, extract, llm

app = FastAPI(title="Research Paper FAQ")

MAX_PDF_BYTES = 32 * 1024 * 1024  # matches the API's 32 MB request limit


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

        paper_id = db.insert_paper(title, source, None, full_text, summary)

        # Save the PDF copy under the final id, then record its path.
        path = extract.save_pdf(data, str(paper_id))
        with db.get_conn() as conn:
            conn.execute("UPDATE papers SET file_path = ? WHERE id = ?",
                         (str(path), paper_id))

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
    paper.pop("full_text", None)  # don't ship 400KB of text to the browser
    paper["messages"] = db.get_messages(paper_id)
    return paper


@app.delete("/api/papers/{paper_id}")
def remove_paper(paper_id: int):
    paper = db.get_paper(paper_id)
    if not paper:
        raise HTTPException(404, "Paper not found.")
    if paper.get("file_path"):
        Path(paper["file_path"]).unlink(missing_ok=True)
    db.delete_paper(paper_id)
    return {"deleted": paper_id}


@app.get("/api/papers/{paper_id}/pdf")
def get_paper_pdf(paper_id: int):
    paper = db.get_paper(paper_id)
    if not paper:
        raise HTTPException(404, "Paper not found.")
    if not paper.get("file_path") or not Path(paper["file_path"]).exists():
        raise HTTPException(404, "No stored PDF for this paper.")
    return FileResponse(paper["file_path"], media_type="application/pdf",
                        filename=f"{paper_id}.pdf",
                        content_disposition_type="inline")


@app.post("/api/papers/{paper_id}/chat")
def chat(paper_id: int, body: dict):
    question = (body or {}).get("question", "").strip()
    if not question:
        raise HTTPException(400, "Question must not be empty.")
    paper = db.get_paper(paper_id)
    if not paper:
        raise HTTPException(404, "Paper not found.")

    history = db.get_messages(paper_id)
    db.add_message(paper_id, "user", question)
    try:
        answer = llm.chat(paper["full_text"], history, question)
    except Exception:
        logging.getLogger(__name__).exception("chat failed")
        raise HTTPException(502, "The model call failed — check the server log.")
    try:
        db.add_message(paper_id, "assistant", answer)
    except Exception:
        # Paper was deleted mid-chat; nothing left to store for.
        logging.getLogger(__name__).warning("paper %s vanished mid-chat", paper_id)
        return {"role": "assistant", "content": answer}
    return {"role": "assistant", "content": answer}


app.mount("/", StaticFiles(directory="static", html=True), name="static")
