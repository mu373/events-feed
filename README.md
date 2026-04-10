# events-feed

Scrapes seminar/event pages, uses an LLM to extract and filter relevant events, and generates Atom XML and iCal feeds.

## Setup

```
uv sync
uv run llm install llm-gemini
# Set API key for Gemini
uv run llm keys set gemini
```

## Usage

```
events-feed scrape                          # scrape all feeds
events-feed scrape --feed boston-compepi    # scrape one feed
events-feed scrape --force                  # ignore content cache
events-feed feed                            # generate output/*.xml and output/*.ics
events-feed list                            # list upcoming events
events-feed sources                         # show source status
events-feed feeds                           # list available feeds
```

## Feed Configuration

Each feed lives in `feeds/<name>/` with two files:
- **feed.yaml**: id, title, description, model, and list of sources
- **prompt.md**: LLM system prompt for relevance filtering

## Pipeline

```
source URL → requests → trafilatura (or RSS parser) → LLM extraction → SQLite → Atom/iCal
```

Content hashing skips LLM calls when a page hasn't changed. Event dedup uses a hash of title + date.

## Example Feed

A live feed tracking computational epidemiology, Bayesian/causal inference, and network science events in the Boston area:

- Atom: https://minami.me/feeds/events/boston-compepi.xml
- iCal: https://minami.me/feeds/events/boston-compepi.ics
- [Google Calendar](https://calendar.google.com/calendar/u/0/embed?src=teg6m88ngb1precshgtfgjsmvtnubpqs@import.calendar.google.com&ctz=America/New_York&mode=AGENDA&hl=en)

## S3 Export

Add an `export` block to `feed.yaml` to upload feeds to S3 after generation:

```yaml
export:
  s3:
    bucket: my-bucket
    prefix: feeds/events
    region: us-east-1
    profile: my-aws-profile  # optional, uses default credentials if omitted
```

Install with S3 support:
```
uv sync --extra s3
```

## License
MIT
