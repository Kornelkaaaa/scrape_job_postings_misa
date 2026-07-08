"""Relevance filtering: keywords (junior/intern BA, consulting, AI) and
locations (West Virginia + remote).

LEARNING NOTES:
- Word-boundary regex (\\b): the difference between "does the text CONTAIN
  'ai'" (matches 'email', 'available' - bad) and "does the text contain 'ai'
  AS A WORD" (matches 'AI Engineer' only). \\b matches the invisible position
  between a word character and a non-word character.
- re.escape(): user-supplied keywords may contain regex special characters
  ('C++', 'M.C. Dean'); escaping makes them match literally.
- The include/exclude pattern: an empty include list means "keep everything",
  a non-empty one means "keep ONLY matches". Exclude then removes on top.
  This two-list design shows up in many real systems (firewalls, .gitignore).
"""
from __future__ import annotations

import re

from .config import Config, Source
from .models import Opportunity


def _matches_any(opp: Opportunity, keywords: list[str]) -> bool:
    """True if any keyword appears (as a whole word) in the title or tags.

    We deliberately do NOT search descriptions: almost every posting mentions
    'analyst' or 'entry level' somewhere in its body text, which made the
    filter useless in practice (a groundskeeper job once passed!).
    """
    haystack = " ".join([opp.title, " ".join(opp.tags)]).lower()
    # any() short-circuits: stops at the first matching keyword
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
    """Apply keyword + location rules; returns the survivors.

    Per-source lists override globals ("source.x or config.x" works because
    an empty list is falsy in Python); an empty include list means keep
    everything.

    Location rules: items whose location field is empty pass (we can't judge
    them). When an include list is set it decides alone; exclude_locations only
    applies when no include list exists - otherwise a multi-country posting
    like "London, UK; Remote, United States" would be wrongly dropped for
    mentioning a foreign office even though US applicants are welcome.
    """
    include = source.include_keywords or config.include_keywords
    exclude = source.exclude_keywords or config.exclude_keywords
    include_loc = source.include_locations or config.include_locations
    exclude_loc = source.exclude_locations or config.exclude_locations

    kept = []
    for opp in opportunities:
        # "continue" skips to the next opportunity - each check is a gate
        if include and not _matches_any(opp, include):
            continue  # doesn't mention anything we care about
        if exclude and _matches_any(opp, exclude):
            continue  # mentions something we explicitly don't want (senior...)
        if include_loc and opp.location:
            if not _location_matches(opp, include_loc):
                continue  # has a location, and it's not WV/remote
        elif exclude_loc and opp.location and _location_matches(opp, exclude_loc):
            continue
        kept.append(opp)
    return kept
