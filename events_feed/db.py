import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "events.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    speaker TEXT,
    date TEXT,          -- ISO 8601 date (YYYY-MM-DD)
    time TEXT,          -- e.g. "14:00"
    location TEXT,
    venue TEXT,         -- institution / org
    description TEXT,
    url TEXT,           -- source page URL
    event_url TEXT,     -- direct link to event page if available
    tags TEXT,          -- comma-separated
    relevance_score REAL,  -- 0-1, from LLM filtering
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    source_type TEXT DEFAULT 'seminar_page',  -- seminar_page, search, social
    last_scraped TEXT,
    last_content_hash TEXT,  -- hash of extracted text, skip LLM if unchanged
    active INTEGER DEFAULT 1
);
"""


def get_db(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def insert_event(conn: sqlite3.Connection, event: dict) -> bool:
    """Insert an event, return True if new, False if duplicate."""
    defaults = {
        "content_hash": None, "title": None, "speaker": None, "date": None,
        "time": None, "location": None, "venue": None, "description": None,
        "url": None, "event_url": None, "tags": None, "relevance_score": None,
    }
    row = {**defaults, **event}
    try:
        conn.execute(
            """INSERT INTO events (content_hash, title, speaker, date, time, location,
               venue, description, url, event_url, tags, relevance_score)
               VALUES (:content_hash, :title, :speaker, :date, :time, :location,
               :venue, :description, :url, :event_url, :tags, :relevance_score)""",
            row,
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def get_upcoming_events(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    """Get upcoming events ordered by date."""
    rows = conn.execute(
        """SELECT * FROM events
           WHERE date >= date('now', '-1 day')
           ORDER BY date ASC, time ASC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_all_events(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    """Get all events ordered by date descending."""
    rows = conn.execute(
        """SELECT * FROM events ORDER BY date DESC, time ASC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
