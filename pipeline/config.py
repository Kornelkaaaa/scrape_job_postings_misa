"""Load and validate sources.yaml.

LEARNING NOTES:
- Separating CONFIG (sources.yaml) from CODE is the core design decision of
  this project: adding a job source, changing keywords, or renaming newsletter
  categories requires zero programming - just edit the YAML.
- YAML is a human-friendly data format; yaml.safe_load() turns it into plain
  Python dicts/lists. ALWAYS safe_load, never load: plain load can execute
  arbitrary code hidden in a malicious file.
- .get(key, default) is the polite way to read dicts: missing optional keys
  fall back to a default instead of raising KeyError.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .models import OPPORTUNITY_TYPES


@dataclass
class Source:
    """One entry under 'sources:' in the YAML - a single place we scrape."""
    name: str
    type: str  # adapter name: greenhouse, lever, rss, json_api, html, adzuna, usajobs, workday
    url: str = ""
    opportunity_type: str = "job"
    enabled: bool = True   # the kill switch: flip to false if a site blocks us
    org: str = ""          # default org when the feed doesn't carry one
    options: dict = field(default_factory=dict)   # adapter-specific (selectors, paths, ...)
    include_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    include_locations: list[str] = field(default_factory=list)  # ["*"] = opt out of location filter
    exclude_locations: list[str] = field(default_factory=list)


@dataclass
class Config:
    """Everything from sources.yaml, parsed into one typed object.

    The rest of the codebase only ever sees this object - if we switched from
    YAML to TOML or a database tomorrow, only THIS file would change.
    """
    sources: list[Source]
    db_path: str = "data/opportunities.db"
    output_dir: str = "output"
    user_agent: str | None = None
    delay_seconds: float = 2.0
    include_keywords: list[str] = field(default_factory=list)  # global relevance filter
    exclude_keywords: list[str] = field(default_factory=list)
    include_locations: list[str] = field(default_factory=list)
    exclude_locations: list[str] = field(default_factory=list)
    career_fair_orgs: list[str] = field(default_factory=list)  # highlighted in newsletter
    categories: dict = field(default_factory=dict)  # newsletter job topic sections, ordered
    hackathon_categories: dict = field(default_factory=dict)  # same idea, hackathon themes
    internship_keywords: list[str] = field(default_factory=list)  # split into own section


def load_config(path: str | Path = "sources.yaml") -> Config:
    # "or {}" guards against a completely empty file (safe_load returns None)
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    settings = raw.get("settings", {})
    filters = raw.get("filters", {})

    sources = []
    for entry in raw.get("sources", []):
        source = Source(
            name=entry["name"],   # [] not .get(): name/type are REQUIRED -
            type=entry["type"],   # crash loudly if someone forgets them
            url=entry.get("url", ""),
            opportunity_type=entry.get("opportunity_type", "job"),
            enabled=entry.get("enabled", True),
            org=entry.get("org", ""),
            options=entry.get("options", {}),
            include_keywords=entry.get("include_keywords", []),
            exclude_keywords=entry.get("exclude_keywords", []),
            include_locations=entry.get("include_locations", []),
            exclude_locations=entry.get("exclude_locations", []),
        )
        # Validate early, at load time - a typo like "jobb" fails HERE with a
        # clear message instead of producing silently-broken data later.
        if source.opportunity_type not in OPPORTUNITY_TYPES:
            raise ValueError(
                f"source {source.name!r}: unknown opportunity_type {source.opportunity_type!r}"
            )
        sources.append(source)

    return Config(
        sources=sources,
        db_path=settings.get("db_path", "data/opportunities.db"),
        output_dir=settings.get("output_dir", "output"),
        user_agent=settings.get("user_agent"),
        delay_seconds=float(settings.get("delay_seconds", 2.0)),
        include_keywords=filters.get("include_keywords", []),
        exclude_keywords=filters.get("exclude_keywords", []),
        include_locations=filters.get("include_locations", []),
        exclude_locations=filters.get("exclude_locations", []),
        # "x or []" (not .get default) also converts None to [] - happens when
        # the YAML key exists but has no entries under it
        career_fair_orgs=raw.get("career_fair_orgs") or [],
        categories=raw.get("categories") or {},
        hackathon_categories=raw.get("hackathon_categories") or {},
        internship_keywords=raw.get("internship_keywords") or [],
    )
