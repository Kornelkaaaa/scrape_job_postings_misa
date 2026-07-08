"""SQLite storage. Dedupe happens at the DB layer via a UNIQUE dedupe_key.

LEARNING NOTES:
- SQLite is a full SQL database that lives in a single file (no server to
  install/run) - perfect for small projects. Python ships with it built in.
- The UNIQUE constraint + "INSERT OR IGNORE" pattern pushes deduplication
  into the database itself: inserting a row whose dedupe_key already exists
  silently does nothing. That's simpler AND safer than checking
  "SELECT ... does it exist?" first, which can race with other writers.
- Parameterized queries (the ? placeholders) are non-negotiable: NEVER build
  SQL with f-strings/string concatenation, or a job title containing a quote
  character would break the query (or worse - SQL injection).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Opportunity

# executescript runs multiple statements; IF NOT EXISTS makes this idempotent,
# meaning it's safe to run on every startup - it only creates what's missing.
SCHEMA = """
CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_type TEXT NOT NULL,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    org TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    posted_date TEXT,
    first_seen_at TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    dedupe_key TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_opportunities_first_seen ON opportunities(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_opportunities_type ON opportunities(opportunity_type);
"""
# ^ The two indexes speed up the queries we actually run (filter by date /
#   by type). Without an index, SQLite scans every row.


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open (and if needed create) the database file, ensuring the schema."""
    # Make sure the parent folder (e.g. data/) exists before SQLite tries
    # to create a file inside it. exist_ok=True -> no error if already there.
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    # row_factory=Row lets us access columns by name (row["title"]) instead
    # of by position (row[2]) - much more readable and refactor-proof.
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def insert_new(conn: sqlite3.Connection, opportunities: list[Opportunity]) -> int:
    """Insert only rows whose dedupe_key is unseen. Returns number inserted.

    Already-known rows get their URL refreshed: some sources (Adzuna) serve
    short-lived redirect links, so the newest link is the one most likely to
    still work in the newsletter.
    """
    # One timestamp for the whole batch, in UTC. Storing ISO 8601 strings
    # keeps them sortable with plain string comparison (handy in SQL).
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    inserted = 0
    for opp in opportunities:
        cur = conn.execute(
            """INSERT OR IGNORE INTO opportunities
               (opportunity_type, source, title, org, location, url, description,
                posted_date, first_seen_at, tags, dedupe_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                opp.opportunity_type, opp.source, opp.title.strip(), opp.org.strip(),
                opp.location.strip(), opp.url, opp.description.strip(),
                # SQLite has no list type, so tags are stored as a JSON string
                # ('["a","b"]') and json.loads()-ed when read back.
                opp.posted_date, now, json.dumps(opp.tags), opp.dedupe_key,
            ),
        )
        # rowcount is 1 if the INSERT happened, 0 if IGNORE kicked in
        if cur.rowcount:
            inserted += 1
        elif opp.url:
            # Duplicate row: refresh its link. The "AND url != ?" avoids
            # pointless writes when the URL hasn't changed.
            conn.execute(
                "UPDATE opportunities SET url = ? WHERE dedupe_key = ? AND url != ?",
                (opp.url, opp.dedupe_key, opp.url),
            )
    conn.commit()  # nothing is saved to disk until commit
    return inserted


def list_since(
    conn: sqlite3.Connection,
    since_iso: str,
    opportunity_type: str | None = None,
) -> list[sqlite3.Row]:
    """Rows first seen at/after the given ISO timestamp, optionally one type.

    Note how the query is built: we append SQL *text* conditionally, but the
    VALUES always go through params - the safe way to build dynamic SQL.
    """
    query = "SELECT * FROM opportunities WHERE first_seen_at >= ?"
    params: list = [since_iso]
    if opportunity_type:
        query += " AND opportunity_type = ?"
        params.append(opportunity_type)
    query += " ORDER BY opportunity_type, org, posted_date DESC"
    return conn.execute(query, params).fetchall()
