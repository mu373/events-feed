import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from feedgen.feed import FeedGenerator
from icalendar import Calendar, Event

from .db import get_db, get_upcoming_events, get_all_events


def _clean_xml(text: str) -> str:
    """Remove control characters that are invalid in XML."""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)


def generate_feed(config: dict, output_path: str = "feed.xml", upcoming_only: bool = True) -> None:
    """Generate an Atom XML feed from stored events."""
    feed_id = config["id"]

    fg = FeedGenerator()
    fg.id(f"urn:events-feed:{feed_id}")
    fg.title(config["title"])
    fg.subtitle(config.get("description", ""))
    fg.language("en")
    fg.updated(datetime.now(timezone.utc))

    conn = get_db()
    events = get_upcoming_events(conn) if upcoming_only else get_all_events(conn)

    for event in events:
        fe = fg.add_entry()
        fe.id(f"urn:events-feed:{feed_id}:{event['content_hash']}")
        fe.title(_clean_xml(event["title"]))

        # Build content as HTML
        parts = []
        if event["speaker"]:
            parts.append(f"<b>Speaker:</b> {event['speaker']}")
        if event["date"]:
            parts.append(f"<b>Date:</b> {event['date']}")
        if event["time"]:
            parts.append(f"<b>Time:</b> {event['time']}")
        if event["location"]:
            parts.append(f"<b>Location:</b> {event['location']}")
        if event["venue"]:
            parts.append(f"<b>Venue:</b> {event['venue']}")
        if event["description"]:
            parts.append(f"<p>{event['description']}</p>")
        if event["tags"]:
            parts.append(f"<b>Tags:</b> {event['tags']}")

        fe.content(_clean_xml("<br>".join(parts)), type="html")

        if event["event_url"]:
            fe.link(href=event["event_url"])
        elif event["url"]:
            fe.link(href=event["url"])

        if event["date"]:
            try:
                dt = datetime.fromisoformat(event["date"])
                fe.updated(dt.replace(tzinfo=timezone.utc))
            except ValueError:
                fe.updated(datetime.now(timezone.utc))

        if event["tags"]:
            for tag in event["tags"].split(", "):
                fe.category(term=tag.strip())

    fg.atom_file(output_path, pretty=True)
    conn.close()


def generate_ical(config: dict, output_path: str = "feed.ics", upcoming_only: bool = True) -> None:
    """Generate an iCal feed from stored events."""
    feed_id = config["id"]

    cal = Calendar()
    cal.add("prodid", f"-//events-feed//{feed_id}//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", config["title"])

    conn = get_db()
    events = get_upcoming_events(conn) if upcoming_only else get_all_events(conn)

    for ev in events:
        event = Event()
        event.add("uid", f"{ev['content_hash']}@{feed_id}.events-feed")
        event.add("summary", ev["title"])

        # Parse date and time
        if ev["date"]:
            try:
                dt = datetime.fromisoformat(ev["date"])
                if ev.get("time"):
                    h, m = ev["time"].split(":")
                    dt = dt.replace(hour=int(h), minute=int(m))
                dt = dt.replace(tzinfo=timezone.utc)
                event.add("dtstart", dt)
                event.add("dtend", dt + timedelta(hours=1))
            except (ValueError, TypeError):
                continue
        else:
            continue

        # Description
        parts = []
        if ev.get("speaker"):
            parts.append(f"Speaker: {ev['speaker']}")
        if ev.get("description"):
            parts.append(ev["description"])
        if ev.get("tags"):
            parts.append(f"Tags: {ev['tags']}")
        if ev.get("url"):
            parts.append(f"Source: {ev['url']}")
        if parts:
            event.add("description", "\n\n".join(parts))

        # Location
        loc_parts = []
        if ev.get("location"):
            loc_parts.append(ev["location"])
        if ev.get("venue"):
            loc_parts.append(ev["venue"])
        if loc_parts:
            event.add("location", ", ".join(loc_parts))

        # URL
        if ev.get("event_url") or ev.get("url"):
            event.add("url", ev.get("event_url") or ev["url"])

        cal.add_component(event)

    Path(output_path).write_bytes(cal.to_ical())
    conn.close()
