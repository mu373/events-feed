import re
import sqlite3
from pathlib import Path

_PLACEHOLDER_RE = re.compile(
    r"\b(tbd|tba|untitled)\b|to be (determined|announced)",
    re.IGNORECASE,
)


def is_placeholder_title(title: str | None) -> bool:
    if not title or not title.strip():
        return True
    return bool(_PLACEHOLDER_RE.search(title))

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
    source_type TEXT DEFAULT 'web',
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


def replace_placeholder(conn: sqlite3.Connection, event: dict) -> int | None:
    """Delete a matching placeholder event so a real-titled replacement can be
    inserted. Returns the id that was deleted, or None.

    A placeholder qualifies when it shares (url, date, time) with the new event,
    has a placeholder title, has no speaker, and has either no location or the
    same location as the new event.
    """
    if is_placeholder_title(event.get("title")):
        return None
    date = event.get("date")
    if not date:
        return None

    url = event.get("url")
    time = event.get("time")
    if time:
        rows = conn.execute(
            "SELECT * FROM events WHERE url = ? AND date = ? AND time = ?",
            (url, date, time),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM events WHERE url = ? AND date = ? AND time IS NULL",
            (url, date),
        ).fetchall()

    new_loc = (event.get("location") or "").strip().lower()
    new_speaker = (event.get("speaker") or "").strip().lower()
    for r in rows:
        if not is_placeholder_title(r["title"]):
            continue
        existing_loc = (r["location"] or "").strip().lower()
        if existing_loc and new_loc and existing_loc != new_loc:
            continue
        existing_speaker = (r["speaker"] or "").strip().lower()
        if existing_speaker and new_speaker and existing_speaker != new_speaker:
            continue
        conn.execute("DELETE FROM events WHERE id = ?", (r["id"],))
        conn.commit()
        return r["id"]
    return None


def find_duplicate_groups(conn: sqlite3.Connection) -> list[list[dict]]:
    """Return groups of events sharing (url, date, time) for manual review."""
    rows = conn.execute(
        """SELECT url, date, time FROM events
           WHERE date IS NOT NULL
           GROUP BY url, date, COALESCE(time, '')
           HAVING COUNT(*) > 1
           ORDER BY date, time"""
    ).fetchall()
    groups = []
    for r in rows:
        if r["time"] is None:
            events = conn.execute(
                "SELECT * FROM events WHERE url = ? AND date = ? AND time IS NULL ORDER BY id",
                (r["url"], r["date"]),
            ).fetchall()
        else:
            events = conn.execute(
                "SELECT * FROM events WHERE url = ? AND date = ? AND time = ? ORDER BY id",
                (r["url"], r["date"], r["time"]),
            ).fetchall()
        groups.append([dict(e) for e in events])
    return groups


def delete_events(conn: sqlite3.Connection, ids: list[int]) -> list[dict]:
    """Delete events by ID, returning the rows that were deleted."""
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT * FROM events WHERE id IN ({placeholders})", ids
    ).fetchall()
    conn.execute(f"DELETE FROM events WHERE id IN ({placeholders})", ids)
    conn.commit()
    return [dict(r) for r in rows]
