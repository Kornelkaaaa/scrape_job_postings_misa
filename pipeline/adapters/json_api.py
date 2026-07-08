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

    def field_value(item, name, default_path=None):
        # an unmapped field must yield nothing - never the whole item
        # (_dig treats an empty path as "return the item itself")
        path = fields.get(name, default_path)
        return _dig(item, path) if path else None

    opportunities = []
    for item in items:
        title = str(field_value(item, "title", "title") or "").strip()
        if not title:
            continue
        tags = field_value(item, "tags")
        opportunities.append(Opportunity(
            opportunity_type=source.opportunity_type,
            source=source.name,
            title=title,
            org=str(field_value(item, "org") or source.org or source.name),
            location=str(field_value(item, "location") or ""),
            url=str(field_value(item, "url", "url") or ""),
            description=str(field_value(item, "description") or "")[:1000],
            posted_date=normalize_date(field_value(item, "posted_date")),
            tags=[str(t) for t in tags] if isinstance(tags, list) else [],
        ))
    return opportunities


def fetch(source, client) -> list[Opportunity]:
    response = client.get(source.url)
    return parse(source, response.json())
