"""Keyword relevance filtering (e.g. keep junior/intern BA, consulting, AI roles)."""
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


def filter_relevant(opportunities: list[Opportunity], config: Config, source: Source) -> list[Opportunity]:
    """Per-source keywords override globals; empty include list means keep everything."""
    include = source.include_keywords or config.include_keywords
    exclude = source.exclude_keywords or config.exclude_keywords

    kept = []
    for opp in opportunities:
        if include and not _matches_any(opp, include):
            continue
        if exclude and _matches_any(opp, exclude):
            continue
        kept.append(opp)
    return kept
