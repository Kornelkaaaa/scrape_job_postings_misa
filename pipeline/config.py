"""Load and validate sources.yaml."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .models import OPPORTUNITY_TYPES


@dataclass
class Source:
    name: str
    type: str  # adapter name: greenhouse, lever, rss, json_api, html, adzuna
    url: str = ""
    opportunity_type: str = "job"
    enabled: bool = True
    org: str = ""                      # default org when the feed doesn't carry one
    options: dict = field(default_factory=dict)   # adapter-specific (selectors, paths, ...)
    include_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)


@dataclass
class Config:
    sources: list[Source]
    db_path: str = "data/opportunities.db"
    output_dir: str = "output"
    user_agent: str | None = None
    delay_seconds: float = 2.0
    include_keywords: list[str] = field(default_factory=list)  # global relevance filter
    exclude_keywords: list[str] = field(default_factory=list)


def load_config(path: str | Path = "sources.yaml") -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    settings = raw.get("settings", {})
    filters = raw.get("filters", {})

    sources = []
    for entry in raw.get("sources", []):
        source = Source(
            name=entry["name"],
            type=entry["type"],
            url=entry.get("url", ""),
            opportunity_type=entry.get("opportunity_type", "job"),
            enabled=entry.get("enabled", True),
            org=entry.get("org", ""),
            options=entry.get("options", {}),
            include_keywords=entry.get("include_keywords", []),
            exclude_keywords=entry.get("exclude_keywords", []),
        )
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
    )
