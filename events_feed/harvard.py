"""Keep each Harvard epidemiology seminar's schedule with its title link."""
import json
import re
from datetime import datetime
from html import unescape
from urllib.parse import urljoin, urlsplit

from lxml import html
import requests

SOURCE = "https://hsph.harvard.edu/department/epidemiology/seminar-series/"


def _clock(value):
    match = re.search(r"\b(\d{1,2}:\d{2}\s*[ap]m)\b", value, re.I)
    if match:
        try:
            return datetime.strptime(match[1].replace(" ", ""), "%I:%M%p").strftime("%H:%M")
        except ValueError:
            pass
    return None


def _detail_times(url, title, day, timeout):
    response = requests.get(url, timeout=timeout, allow_redirects=False)
    if response.status_code != 200:
        return None, None
    for script in html.fromstring(response.content).xpath('//script[@type="application/ld+json"]/text()'):
        try:
            data = json.loads(script)
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if not isinstance(item, dict) or item.get("@type") != "Event":
                continue
            if unescape(item.get("name", "")).strip().casefold() != title.casefold():
                continue
            start = item.get("startDate") or ""
            end = item.get("endDate") or ""
            if not start.startswith(day + "T"):
                continue
            try:
                start_time = datetime.fromisoformat(start).strftime("%H:%M")
                end_time = datetime.fromisoformat(end).strftime("%H:%M") if end.startswith(day + "T") else None
            except ValueError:
                continue
            return start_time, end_time
    return None, None


def seminar_cards(body, timeout):
    cards = []
    for card in html.fromstring(body).xpath('//li[contains(concat(" ", @class, " "), " tribe_events ")]'):
        anchors = card.xpath('.//h3/a')
        dates = card.xpath('.//time/@datetime')
        if not anchors or not dates:
            continue
        title = anchors[0].text_content().strip()
        link = urljoin(SOURCE, anchors[0].get("href") or "")
        target = urlsplit(link)
        link = link if (target.scheme, target.netloc) == ("https", "hsph.harvard.edu") and "/events/" in target.path else None
        start = _clock(card.xpath('string(.//*[@class="tribe-event-date-start"])'))
        end = _clock(card.xpath('string(.//*[@class="tribe-event-time"])'))
        if link and not start:
            try:
                start, end = _detail_times(link, title, dates[0], timeout)
            except requests.RequestException:
                pass
        cards.append(dict(title=title, date=dates[0], time=start, end_time=end,
                          event_url=link, description=card.xpath('string(.//*[@class="post-item__excerpt"])').strip()))
    return json.dumps(cards, ensure_ascii=False) if cards else None
