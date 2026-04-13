"""LLM-assisted duplicate detection for stored events."""

import json
import sqlite3

import llm
from pydantic import BaseModel, Field

from .db import is_placeholder_title

DEFAULT_MODEL = "gemini/gemini-flash-lite-latest"
AUTO_DELETE_CONFIDENCE = 0.85
AUTO_DELETE_MIN_FIELDS = 2
MIN_REVIEW_CONFIDENCE = 0.5


class DuplicateGroup(BaseModel):
    ids: list[int] = Field(description="Event IDs that refer to the same real-world event")
    matching_fields: list[str] = Field(
        description="Fields that match across the events (e.g., 'date', 'speaker', 'venue', 'title')"
    )
    confidence: float = Field(description="0.0 to 1.0 confidence they are the same event")
    reason: str = Field(description="One-sentence explanation")


class DuplicateAnalysis(BaseModel):
    groups: list[DuplicateGroup]


SYSTEM_PROMPT = """You are a duplicate-detection assistant for a seminar events database.

The events provided are candidates that share the same date. Identify which events
refer to the SAME underlying real-world seminar/talk/lecture.

Two events ARE the same when they represent the same real-world event, even if:
- One title is a placeholder ("TBD", "Untitled") and the other is a real title
- One title is a garbage section heading (e.g. "2026 Spring Colloquium Schedule")
  and the other is the real talk title
- Titles differ slightly but the speaker and date match

Two events are DIFFERENT when they are parallel sessions on the same date. Signals:
- Different speakers (when both are specified)
- Different locations/rooms (when both are specified)
- Different specific talk titles with different topics

Only group events that genuinely refer to the same real-world event. Do NOT group
events merely because they share a date. Do NOT include groups you considered and
rejected — if you are not reasonably confident (>= 0.5) the events are duplicates,
OMIT the group entirely.

For each group, cite the fields that match in matching_fields. Confidence should
reflect certainty: use >= 0.85 when speaker and venue both align or the title is
clearly a placeholder for the other entry's real title.

Return an empty groups array if no duplicates exist."""


def _candidate_fields(e: dict) -> dict:
    """Trim fields sent to the LLM to keep the prompt compact."""
    desc = e.get("description") or ""
    if len(desc) > 200:
        desc = desc[:200] + "..."
    return {
        "id": e["id"],
        "title": e.get("title"),
        "speaker": e.get("speaker"),
        "date": e.get("date"),
        "time": e.get("time"),
        "location": e.get("location"),
        "venue": e.get("venue"),
        "url": e.get("url"),
        "description": desc,
    }


def find_candidate_buckets(
    conn: sqlite3.Connection, upcoming_only: bool = True
) -> list[list[dict]]:
    """Group events by date; return buckets with >= 2 events.

    By default, restricts to upcoming events (date >= today - 1 day).
    """
    date_filter = "AND date >= date('now', '-1 day')" if upcoming_only else ""
    rows = conn.execute(
        f"""SELECT date FROM events
            WHERE date IS NOT NULL {date_filter}
            GROUP BY date HAVING COUNT(*) > 1"""
    ).fetchall()
    buckets = []
    for r in rows:
        events = conn.execute(
            "SELECT * FROM events WHERE date = ? ORDER BY id", (r["date"],)
        ).fetchall()
        buckets.append([dict(e) for e in events])
    return buckets


def detect_duplicates(
    buckets: list[list[dict]], model_name: str | None = None
) -> list[DuplicateGroup]:
    """Send candidate buckets to the LLM and return proposed duplicate groups."""
    if not buckets:
        return []

    payload = []
    for bucket in buckets:
        payload.append({
            "date": bucket[0]["date"],
            "events": [_candidate_fields(e) for e in bucket],
        })

    model = llm.get_model(model_name or DEFAULT_MODEL)
    response = model.prompt(
        f"Candidate buckets (grouped by date):\n{json.dumps(payload, indent=2)}",
        system=SYSTEM_PROMPT,
        schema=DuplicateAnalysis,
    )
    result = DuplicateAnalysis.model_validate_json(response.text())
    return result.groups


def passes_guards(group_ids: list[int], events_by_id: dict[int, dict]) -> bool:
    """Apply deterministic guards that rule out obvious false positives."""
    events = [events_by_id[i] for i in group_ids if i in events_by_id]
    if len(events) < 2:
        return False

    # All events in a group must share the same date
    dates = {e.get("date") for e in events}
    if len(dates) > 1:
        return False

    # If speakers are present on multiple events and any differ → parallel talks
    speakers = [
        (e.get("speaker") or "").strip().lower()
        for e in events
        if (e.get("speaker") or "").strip()
    ]
    if len(set(speakers)) > 1:
        return False

    # Same check for location
    locations = [
        (e.get("location") or "").strip().lower()
        for e in events
        if (e.get("location") or "").strip()
    ]
    if len(set(locations)) > 1:
        return False

    return True


def pick_winner(events: list[dict]) -> dict:
    """Choose which event to keep when merging a duplicate group."""
    def score(e: dict) -> tuple:
        return (
            0 if is_placeholder_title(e.get("title")) else 1,
            1 if (e.get("speaker") or "").strip() else 0,
            len(e.get("description") or ""),
            e.get("relevance_score") or 0,
            e.get("created_at") or "",
        )
    return max(events, key=score)


def categorize(
    groups: list[DuplicateGroup], events_by_id: dict[int, dict]
) -> tuple[list[dict], list[dict]]:
    """Split LLM-proposed groups into auto-delete and needs-review buckets.

    Returns (auto_delete, review), each a list of dicts with keys:
    group, keep_id, delete_ids.
    """
    auto_delete = []
    review = []
    for g in groups:
        valid_ids = [i for i in g.ids if i in events_by_id]
        if len(valid_ids) < 2:
            continue
        if g.confidence < MIN_REVIEW_CONFIDENCE:
            continue
        if not passes_guards(valid_ids, events_by_id):
            continue

        events = [events_by_id[i] for i in valid_ids]
        keep = pick_winner(events)
        delete_ids = [e["id"] for e in events if e["id"] != keep["id"]]

        entry = {
            "group": g,
            "ids": valid_ids,
            "keep_id": keep["id"],
            "delete_ids": delete_ids,
        }

        confident = (
            g.confidence >= AUTO_DELETE_CONFIDENCE
            and len(g.matching_fields) >= AUTO_DELETE_MIN_FIELDS
        )
        if confident:
            auto_delete.append(entry)
        else:
            review.append(entry)
    return auto_delete, review
