import xml.etree.ElementTree as ET

import requests
import trafilatura

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; events-feed/0.1)"}

NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "atom": "http://www.w3.org/2005/Atom",
}


def _is_feed(content_type: str, body: str) -> bool:
    """Check if response looks like an RSS/Atom feed."""
    if "xml" in content_type or "rss" in content_type or "atom" in content_type:
        return True
    stripped = body.lstrip()
    return stripped.startswith("<?xml") or stripped.startswith("<rss") or stripped.startswith("<feed")


def fetch_feed(url: str, timeout: int = 30) -> list[dict]:
    """Fetch and parse an RSS/Atom feed into a list of items with clean text."""
    resp = _get(url, timeout)
    root = ET.fromstring(resp.text)

    items = []
    # RSS 2.0
    for item in root.findall(".//item"):
        title = item.findtext("title", "")
        link = item.findtext("link", "")
        pub_date = item.findtext("pubDate", "")
        description = item.findtext("description", "")
        content = item.findtext("content:encoded", "", NS)
        # Extract clean text from HTML content
        text = trafilatura.extract(content or description, include_comments=False) or description
        items.append({
            "title": title,
            "link": link,
            "pub_date": pub_date,
            "text": text,
        })

    # Atom
    for entry in root.findall("atom:entry", NS):
        title = entry.findtext("atom:title", "", NS)
        link_el = entry.find("atom:link", NS)
        link = link_el.get("href", "") if link_el is not None else ""
        updated = entry.findtext("atom:updated", "", NS)
        content_el = entry.find("atom:content", NS)
        summary_el = entry.find("atom:summary", NS)
        raw = (content_el.text if content_el is not None else "") or (summary_el.text if summary_el is not None else "")
        text = trafilatura.extract(raw, include_comments=False) or raw
        items.append({
            "title": title,
            "link": link,
            "pub_date": updated,
            "text": text,
        })

    return items


def _get(url: str, timeout: int = 30) -> requests.Response:
    """Fetch URL, retrying without SSL verification if cert fails."""
    try:
        resp = requests.get(url, timeout=timeout, headers=_HEADERS)
    except requests.exceptions.SSLError:
        resp = requests.get(url, timeout=timeout, headers=_HEADERS, verify=False)
    resp.raise_for_status()
    return resp


def fetch_page(url: str, timeout: int = 30) -> str | None:
    """Fetch a URL and extract clean text using trafilatura."""
    resp = _get(url, timeout)

    # Auto-detect feeds
    if _is_feed(resp.headers.get("content-type", ""), resp.text):
        items = fetch_feed(url, timeout)
        # Combine all items into a single text block for LLM
        parts = []
        for item in items:
            parts.append(f"Title: {item['title']}")
            if item["pub_date"]:
                parts.append(f"Date: {item['pub_date']}")
            if item["link"]:
                parts.append(f"URL: {item['link']}")
            parts.append(item["text"])
            parts.append("---")
        return "\n".join(parts)

    text = trafilatura.extract(resp.text, include_comments=False, include_tables=True)
    return text
