"""Feed configuration loader."""

from pathlib import Path

import yaml

FEEDS_DIR = Path(__file__).parent.parent / "feeds"


def load_feed_config(feed_name: str) -> dict:
    """Load a feed's config (metadata, prompt, sources)."""
    feed_dir = FEEDS_DIR / feed_name
    config = yaml.safe_load((feed_dir / "feed.yaml").read_text())
    config["prompt"] = (feed_dir / "prompt.md").read_text().strip()
    config.setdefault("name", feed_name)
    return config


def list_feeds() -> list[str]:
    """List available feed names."""
    return sorted(
        d.name for d in FEEDS_DIR.iterdir()
        if d.is_dir() and (d / "feed.yaml").exists()
    )
