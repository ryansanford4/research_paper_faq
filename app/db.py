"""SQLite storage for papers and chat messages."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "papers.db"
FILES_DIR = Path(__file__).resolve().parent.parent / "uploads"
FILES_DIR.mkdir(exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    source TEXT NOT NULL,              -- "upload" or the URL it came from
    file_path TEXT,                    -- local copy of the PDF, if we have one
    full_text TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_messages_paper ON messages(paper_id, id);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


# --- papers ---

def insert_paper(title: str, source: str, file_path: str | None,
                  full_text: str, summary: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO papers (title, source, file_path, full_text, summary) "
            "VALUES (?, ?, ?, ?, ?)",
            (title, source, file_path, full_text, summary),
        )
        return cur.lastrowid


def list_papers() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, source, created_at, "
            "length(full_text) AS text_chars, summary FROM papers "
            "ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_paper(paper_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()
        return dict(row) if row else None


def delete_paper(paper_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM papers WHERE id = ?", (paper_id,))
        return cur.rowcount > 0


# --- chat ---

def get_messages(paper_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE paper_id = ? ORDER BY id",
            (paper_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def add_message(paper_id: int, role: str, content: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO messages (paper_id, role, content) VALUES (?, ?, ?)",
            (paper_id, role, content),
        )
