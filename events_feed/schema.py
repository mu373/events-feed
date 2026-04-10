import hashlib
import re

from pydantic import BaseModel, Field


def _normalize(s: str | None) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", s.lower().strip())


class Event(BaseModel):
    """Schema sent to the LLM for structured output."""
    title: str
    speaker: str | None = None
    date: str | None = Field(None, description="ISO 8601 date (YYYY-MM-DD)")
    time: str | None = Field(None, description="Time in HH:MM 24h format")
    location: str | None = Field(None, description="Room/building")
    venue: str | None = Field(None, description="Institution or organization")
    description: str | None = Field(None, description="1-2 sentence summary")
    tags: list[str] = Field(default_factory=list, description="Relevant topic tags")
    relevance_score: float = Field(
        description="0.0 to 1.0, relevance to computational/mathematical epidemiology"
    )

    def content_hash(self) -> str:
        key = "|".join([
            _normalize(self.title),
            _normalize(self.date),
        ])
        return hashlib.sha256(key.encode()).hexdigest()[:16]


class ExtractionResult(BaseModel):
    events: list[Event]
