import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import llm

from .schema import ExtractionResult

LOG_DIR = Path(__file__).parent.parent / "logs"

DEFAULT_MODEL = "gemini/gemini-2.5-flash-lite"


def _save_log(source_url: str, raw_response: str, result: ExtractionResult) -> Path:
    """Save raw LLM response and parsed events to a log file."""
    LOG_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    host = urlparse(source_url).hostname.replace(".", "_")
    path = LOG_DIR / f"{ts}_{host}.json"
    path.write_text(json.dumps({
        "source_url": source_url,
        "timestamp": ts,
        "raw_response": raw_response,
        "events": result.model_dump()["events"],
    }, indent=2))
    return path


def extract_events(
    text: str, source_url: str, prompt: str, model_name: str | None = None
) -> tuple[list[dict], Path]:
    """Use an LLM to extract structured event data from page text."""
    model = llm.get_model(model_name or DEFAULT_MODEL)
    response = model.prompt(
        f"Today's date: {datetime.now().strftime('%Y-%m-%d')}\n"
        f"Source URL: {source_url}\n\nPage content:\n{text}",
        system=prompt,
        schema=ExtractionResult,
    )
    response_text = response.text()

    # Parse via Pydantic (Gemini's structured output should already be valid)
    result = ExtractionResult.model_validate_json(response_text)

    # Save raw log before any post-processing
    log_path = _save_log(source_url, response_text, result)

    # Convert to dicts for DB insertion
    events = []
    for event in result.events:
        d = event.model_dump()
        d["content_hash"] = event.content_hash()
        d["url"] = source_url
        d["event_url"] = None
        d["tags"] = ", ".join(d["tags"])
        events.append(d)
    return events, log_path
