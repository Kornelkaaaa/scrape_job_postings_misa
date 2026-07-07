"""Relevance filtering: keywords (junior/intern BA, consulting, AI) and
locations (West Virginia + remote)."""
from __future__ import annotations

import re

from .config import Config, Source
from .models import Opportunity


def _matches_any(opp: Opportunity, keywords: list[str]) -> bool:
    # title + tags only: matching descriptions lets unrelated roles through
    # (almost every posting mentions "analyst" or "entry level" somewhere)
    haystack = " ".join([opp.title, " ".join(opp.tags)]).lower()
    # word boundaries so short keywords like "ai" don't match "email"/"available"
    return any(
        re.search(rf"\b{re.escape(keyword.lower())}\b", haystack)
        for keyword in keywords
    )


def _location_matches(opp: Opportunity, locations: list[str]) -> bool:
    if "*" in locations:  # wildcard: source opts out of location filtering
        return True
    return any(
        re.search(rf"\b{re.escape(loc.lower())}\b", opp.location.lower())
        for loc in locations
    )


def filter_relevant(opportunities: list[Opportunity], config: Config, source: Source) -> list[Opportunity]:
    """Per-source lists override globals; an empty include list means keep everything.

    Location rule: items whose location field is empty pass the include check
    (we can't judge them), but exclude still applies when a location exists.
    """
    include = source.include_keywords or config.include_keywords
    exclude = source.exclude_keywords or config.exclude_keywords
    include_loc = source.include_locations or config.include_locations
    exclude_loc = source.exclude_locations or config.exclude_locations

    kept = []
    for opp in opportunities:
        if include and not _matches_any(opp, include):
            continue
        if exclude and _matches_any(opp, exclude):
            continue
        if include_loc and opp.location and not _location_matches(opp, include_loc):
            continue
        if exclude_loc and opp.location and _location_matches(opp, exclude_loc):
            continue
        kept.append(opp)
    return kept
