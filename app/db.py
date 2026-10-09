"""Postgres storage for papers and chat messages (Supabase / Neon / any Postgres)."""

import os

import psycopg
from psycopg.rows import dict_row

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    source TEXT NOT NULL,              -- "upload" or the URL it came from
    pdf_bytes BYTEA,                   -- stored copy of the PDF, if we have one
    full_text TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS messages (
    id BIGSERIAL PRIMARY KEY,
    paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_messages_paper ON messages(paper_id, id);
"""


def get_conn() -> psycopg.Connection:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set — point it at a Postgres instance "
            "(see README for free options)."
        )
    return psycopg.connect(url, row_factory=dict_row, autocommit=True)


def init_db() -> None:
    with get_conn() as conn:
        conn.execute(SCHEMA)


# --- papers ---

def insert_paper(title: str, source: str, pdf_bytes: bytes | None,
                  full_text: str, summary: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "INSERT INTO papers (title, source, pdf_bytes, full_text, summary) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (title, source, pdf_bytes, full_text, summary),
        ).fetchone()
        return row["id"]


def list_papers() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, source, created_at, "
            "char_length(full_text) AS text_chars, summary FROM papers "
            "ORDER BY id DESC"
        ).fetchall()
        # datetimes aren't JSON-serializable for FastAPI; render ISO strings.
        for r in rows:
            r["created_at"] = r["created_at"].isoformat()
        return rows


def get_paper(paper_id: int) -> dict | None:
    """Paper row without the pdf_bytes blob (chat/list routes don't need it)."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, title, source, full_text, summary, created_at "
            "FROM papers WHERE id = %s", (paper_id,)
        ).fetchone()


def get_paper_pdf(paper_id: int) -> bytes | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT pdf_bytes FROM papers WHERE id = %s", (paper_id,)
        ).fetchone()
        return row["pdf_bytes"] if row else None


def delete_paper(paper_id: int) -> bool:
    with get_conn() as conn:
        return conn.execute(
            "DELETE FROM papers WHERE id = %s", (paper_id,)
        ).rowcount > 0


# --- chat ---

def get_messages(paper_id: int) -> list[dict]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT role, content FROM messages WHERE paper_id = %s ORDER BY id",
            (paper_id,),
        ).fetchall()


def add_message(paper_id: int, role: str, content: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO messages (paper_id, role, content) VALUES (%s, %s, %s)",
            (paper_id, role, content),
        )
