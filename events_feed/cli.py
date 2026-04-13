"""CLI entry point for events-feed."""

import argparse
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

from .db import (
    get_db, insert_event, get_upcoming_events, delete_events,
    replace_placeholder, is_placeholder_title,
)
from .dedupe import (
    find_candidate_buckets, detect_duplicates, categorize,
)
from .scraper import fetch_page
from .extract import extract_events
from .feed import generate_feed, generate_ical
from .sources import load_feed_config, list_feeds


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def cmd_scrape(args):
    """Scrape all active sources and extract events."""
    feeds = [args.feed] if args.feed else list_feeds()

    for feed_name in feeds:
        config = load_feed_config(feed_name)
        print(f"=== Feed: {feed_name} ===\n")

        conn = get_db()

        # Sync sources to DB
        for source in config["sources"]:
            conn.execute(
                "INSERT OR IGNORE INTO sources (name, url, source_type) VALUES (?, ?, ?)",
                (source["name"], source["url"], source.get("source_type", "web")),
            )
        conn.commit()

        # Get active sources
        if args.url:
            sources = [{"url": args.url, "name": args.url}]
        else:
            rows = conn.execute("SELECT * FROM sources WHERE active = 1").fetchall()
            sources = [dict(r) for r in rows]

        for source in sources:
            print(f"Scraping: {source['name']} ({source['url']})")
            try:
                text = fetch_page(source["url"])
                if not text:
                    print(f"  No text extracted from {source['url']}")
                    continue

                # Check if content changed since last scrape
                text_hash = _text_hash(text)
                row = conn.execute(
                    "SELECT last_content_hash FROM sources WHERE url = ?",
                    (source["url"],),
                ).fetchone()
                if row and row["last_content_hash"] == text_hash and not args.force:
                    print(f"  Content unchanged, skipping LLM call")
                    continue

                print(f"  Extracted {len(text)} chars, sending to LLM...")
                events, log_path = extract_events(
                    text, source["url"], config["prompt"], config.get("model"),
                )
                print(f"  Found {len(events)} relevant events (log: {log_path})")

                new_count = 0
                for event in events:
                    replaced_id = replace_placeholder(conn, event)
                    if replaced_id:
                        print(f"    * replaced placeholder #{replaced_id}")
                    if insert_event(conn, event):
                        new_count += 1
                        print(f"    + {event['title']} ({event.get('date', '?')})")
                    else:
                        print(f"    = {event['title']} (duplicate)")

                print(f"  {new_count} new events added")

                # Update last_scraped and content hash
                conn.execute(
                    "UPDATE sources SET last_scraped = ?, last_content_hash = ? WHERE url = ?",
                    (datetime.now(timezone.utc).isoformat(), text_hash, source["url"]),
                )
                conn.commit()

            except Exception as e:
                print(f"  Error: {e}", file=sys.stderr)

        conn.close()


def cmd_feed(args):
    """Generate Atom XML and/or iCal feeds."""
    feeds = [args.feed] if args.feed else list_feeds()

    for feed_name in feeds:
        config = load_feed_config(feed_name)
        feed_id = config["id"]

        out_dir = Path(args.output or "output")
        out_dir.mkdir(parents=True, exist_ok=True)

        xml_path = out_dir / f"{feed_id}.xml"
        ics_path = out_dir / f"{feed_id}.ics"

        generate_feed(config, str(xml_path), upcoming_only=not args.all)
        print(f"Atom feed written to {xml_path}")

        generate_ical(config, str(ics_path), upcoming_only=not args.all)
        print(f"iCal feed written to {ics_path}")

        # Export if configured
        export = config.get("export", {})
        if export.get("s3"):
            _upload_s3(export["s3"], [xml_path, ics_path])


def _upload_s3(s3_config: dict, paths: list[Path]) -> None:
    try:
        import boto3
    except ImportError:
        print("  boto3 not installed. Run: uv pip install boto3", file=sys.stderr)
        return

    bucket = s3_config["bucket"]
    prefix = s3_config.get("prefix", "").strip("/")
    region = s3_config.get("region")

    profile = s3_config.get("profile")
    session = boto3.Session(**({"profile_name": profile} if profile else {}))
    client = session.client("s3", **({"region_name": region} if region else {}))

    content_types = {".xml": "application/atom+xml", ".ics": "text/calendar"}

    for path in paths:
        key = f"{prefix}/{path.name}" if prefix else path.name
        client.upload_file(
            str(path), bucket, key,
            ExtraArgs={"ContentType": content_types.get(path.suffix, "application/octet-stream")},
        )
        print(f"  Uploaded to s3://{bucket}/{key}")


def cmd_list(args):
    """List stored events."""
    conn = get_db()
    events = get_upcoming_events(conn)
    conn.close()

    if not events:
        print("No upcoming events.")
        return

    for e in events:
        score = f" [{e['relevance_score']:.1f}]" if e.get("relevance_score") else ""
        print(f"#{e['id']:<4} {e['date']} {(e.get('time') or ''):>5}  {e['title']}{score}")
        if e.get("speaker"):
            print(f"                       {e['speaker']}")
        if e.get("venue"):
            print(f"                       @ {e['venue']}")
        print()


def cmd_sources(args):
    """List all sources with status."""
    conn = get_db()
    rows = conn.execute("""
        SELECT s.*,
            COUNT(e.id) AS event_count,
            MAX(e.date) AS last_event_date,
            MIN(e.date) AS first_event_date
        FROM sources s
        LEFT JOIN events e ON e.url = s.url
        GROUP BY s.id
        ORDER BY s.name
    """).fetchall()
    conn.close()

    for r in rows:
        scraped = r["last_scraped"][:10] if r["last_scraped"] else "never"
        cached = "yes" if r["last_content_hash"] else "no"
        print(f"{'[on]' if r['active'] else '[off]'} {r['name']}")
        print(f"     {r['url']}")
        print(f"     events: {r['event_count']}  |  last scraped: {scraped}  |  cached: {cached}")
        if r["event_count"]:
            print(f"     event range: {r['first_event_date']} to {r['last_event_date']}")
        print()


def cmd_delete(args):
    """Delete events by ID."""
    conn = get_db()
    deleted = delete_events(conn, args.ids)
    conn.close()

    found_ids = {e["id"] for e in deleted}
    missing = [i for i in args.ids if i not in found_ids]

    for e in deleted:
        print(f"deleted #{e['id']}: {e['date']} {(e.get('time') or '')}  {e['title']}")
    for i in missing:
        print(f"no event with id #{i}", file=sys.stderr)


def cmd_dedupe(args):
    """LLM-assisted duplicate detection across events sharing a date."""
    conn = get_db()
    buckets = find_candidate_buckets(conn, upcoming_only=not args.all)
    if not buckets:
        conn.close()
        print("No candidate buckets (no dates have >= 2 events).")
        return

    candidate_count = sum(len(b) for b in buckets)
    print(f"Sending {candidate_count} candidate events across {len(buckets)} date buckets to LLM...")
    groups = detect_duplicates(buckets)

    events_by_id = {}
    for b in buckets:
        for e in b:
            events_by_id[e["id"]] = e

    auto_delete, review = categorize(groups, events_by_id)

    if not auto_delete and not review:
        print("No duplicates found.")
        conn.close()
        return

    def _print_entry(entry):
        g = entry["group"]
        print(f"  confidence: {g.confidence:.2f}  |  matches: {', '.join(g.matching_fields)}")
        print(f"  reason: {g.reason}")
        for i in entry["ids"]:
            e = events_by_id[i]
            marker = "keep" if i == entry["keep_id"] else "delete"
            flag = " [placeholder]" if is_placeholder_title(e["title"]) else ""
            print(f"    [{marker}] #{i:<4} {e['title']}{flag}")
            meta = []
            if e.get("speaker"):
                meta.append(f"speaker: {e['speaker']}")
            if e.get("location"):
                meta.append(f"loc: {e['location']}")
            if e.get("url"):
                meta.append(f"url: {e['url']}")
            if meta:
                print(f"          {'  |  '.join(meta)}")

    if auto_delete:
        header = "AUTO-DELETE (confident)" if args.apply else "WOULD AUTO-DELETE (dry-run)"
        print(f"\n=== {header} — {len(auto_delete)} group(s) ===")
        for entry in auto_delete:
            print()
            _print_entry(entry)

    if review:
        print(f"\n=== REVIEW — {len(review)} group(s) (weaker evidence or lower confidence) ===")
        for entry in review:
            print()
            _print_entry(entry)
        print(f"\n→ review and delete with: events-feed delete <id> ...")

    if args.apply and auto_delete:
        ids_to_delete = [i for entry in auto_delete for i in entry["delete_ids"]]
        deleted = delete_events(conn, ids_to_delete)
        print(f"\nDeleted {len(deleted)} event(s): {[e['id'] for e in deleted]}")
    elif auto_delete and not args.apply:
        print(f"\n(dry-run — re-run with --apply to delete the {sum(len(e['delete_ids']) for e in auto_delete)} event(s) above)")

    conn.close()


def cmd_feeds(args):
    """List available feeds."""
    for name in list_feeds():
        config = load_feed_config(name)
        print(f"  {name} ({len(config['sources'])} sources)")


def main():
    parser = argparse.ArgumentParser(description="Events Feed")
    sub = parser.add_subparsers(dest="command")

    p_scrape = sub.add_parser("scrape", help="Scrape sources for events")
    p_scrape.add_argument("--feed", help="Scrape only this feed (default: all)")
    p_scrape.add_argument("--url", help="Scrape a single URL instead of all sources")
    p_scrape.add_argument("--force", action="store_true", help="Skip content cache, always call LLM")

    p_feed = sub.add_parser("feed", help="Generate Atom XML and iCal feeds")
    p_feed.add_argument("--feed", help="Generate only this feed (default: all)")
    p_feed.add_argument("-o", "--output", help="Output directory (default: output/)")
    p_feed.add_argument("--all", action="store_true", help="Include past events")

    sub.add_parser("list", help="List upcoming events")
    sub.add_parser("sources", help="List all sources with status")
    sub.add_parser("feeds", help="List available feeds")
    p_dedupe = sub.add_parser("dedupe", help="LLM-assisted duplicate detection")
    p_dedupe.add_argument("--apply", action="store_true", help="Actually delete confident duplicates (default: dry-run)")
    p_dedupe.add_argument("--all", action="store_true", help="Include past events (default: upcoming only)")

    p_delete = sub.add_parser("delete", help="Delete events by ID")
    p_delete.add_argument("ids", nargs="+", type=int, help="Event IDs to delete")

    args = parser.parse_args()
    if args.command == "scrape":
        cmd_scrape(args)
    elif args.command == "feed":
        cmd_feed(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "sources":
        cmd_sources(args)
    elif args.command == "feeds":
        cmd_feeds(args)
    elif args.command == "delete":
        cmd_delete(args)
    elif args.command == "dedupe":
        cmd_dedupe(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
