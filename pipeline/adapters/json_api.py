"""Generic JSON API adapter driven entirely by config (e.g. RemoteOK).

options:
  items_path: dot path to the list of items ("" = payload itself is the list)
  fields: mapping of Opportunity field -> dot path within one item
          (title, org, location, url, description, posted_date, tags)

Items with an empty title are skipped (handles feeds whose first element is
metadata, like RemoteOK's legal notice).
"""
from __future__ import annotations

from ..models import Opportunity, normalize_date


def _dig(data, dot_path: str):
    if not dot_path:
        return data
    current = data
    for part in dot_path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


def parse(source, payload) -> list[Opportunity]:
    items = _dig(payload, source.options.get("items_path", ""))
    if not isinstance(items, list):
        raise ValueError(f"{source.name}: items_path did not resolve to a list")

    fields = source.options.get("fields", {})
    opportunities = []
    for item in items:
        title = str(_dig(item, fields.get("title", "title")) or "").strip()
        if not title:
            continue
        tags = _dig(item, fields["tags"]) if "tags" in fields else []
        opportunities.append(Opportunity(
            opportunity_type=source.opportunity_type,
            source=source.name,
            title=title,
            org=str(_dig(item, fields.get("org", "")) or source.org or source.name),
            location=str(_dig(item, fields.get("location", "")) or ""),
            url=str(_dig(item, fields.get("url", "url")) or ""),
            description=str(_dig(item, fields.get("description", "")) or "")[:1000],
            posted_date=normalize_date(_dig(item, fields.get("posted_date", ""))),
            tags=[str(t) for t in tags] if isinstance(tags, list) else [],
        ))
    return opportunities


def fetch(source, client) -> list[Opportunity]:
    response = client.get(source.url)
    return parse(source, response.json())
