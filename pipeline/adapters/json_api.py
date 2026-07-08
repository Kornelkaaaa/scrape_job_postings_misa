"""Generic JSON API adapter driven entirely by config (RemoteOK, Ashby, ...).

This is the most abstract adapter: instead of hard-coding an API's field
names, the YAML config supplies "dot paths" telling us where to find each
value. That's how OpenAI (Ashby) and RemoteOK share one adapter despite
totally different JSON shapes.

options:
  items_path: dot path to the list of items ("" = payload itself is the list)
  fields: mapping of Opportunity field -> dot path within one item
          (title, org, location, url, description, posted_date, tags)
  flags: mapping of tag label -> dot path; when the value is truthy the
         label is prepended to tags (e.g. {Free: event.free})

Items with an empty title are skipped (handles feeds whose first element is
metadata, like RemoteOK's legal notice).
"""
from __future__ import annotations

from ..models import Opportunity, normalize_date


def _dig(data, dot_path: str):
    """Walk a nested structure by a path like "a.b.0.c".

    _dig({"a": {"b": [{"c": 5}]}}, "a.b.0.c") -> 5
    Returns None the moment any step is missing - so callers never need
    try/except around deep lookups. (Libraries like jmespath/jsonpath do
    fancier versions of this; ours is 15 lines and enough.)
    """
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
        # (_dig treats an empty path as "return the item itself", which once
        # dumped 17KB of raw JSON into the org column - see the regression test)
        path = fields.get(name, default_path)
        return _dig(item, path) if path else None

    opportunities = []
    for item in items:
        title = str(field_value(item, "title", "title") or "").strip()
        if not title:
            continue  # metadata/malformed item - skip it
        tags = field_value(item, "tags")
        tags = [str(t) for t in tags] if isinstance(tags, list) else []
        # boolean flag fields become leading tags ("Free" shows in the meta line)
        for label, path in (source.options.get("flags") or {}).items():
            if _dig(item, path):
                tags = [label] + tags
        opportunities.append(Opportunity(
            opportunity_type=source.opportunity_type,
            source=source.name,
            title=title,
            # str(... or fallback): coerce whatever the API sent to a string,
            # falling back to config-level defaults when missing
            org=str(field_value(item, "org") or source.org or source.name),
            location=str(field_value(item, "location") or ""),
            url=str(field_value(item, "url", "url") or ""),
            description=str(field_value(item, "description") or "")[:1000],
            posted_date=normalize_date(field_value(item, "posted_date")),
            tags=tags,
        ))
    return opportunities


def fetch(source, client) -> list[Opportunity]:
    response = client.get(source.url)
    return parse(source, response.json())
