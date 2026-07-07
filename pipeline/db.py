"""SQLite storage. Dedupe happens at the DB layer via a UNIQUE dedupe_key."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Opportunity

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


def connect(db_path: str | Path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def insert_new(conn: sqlite3.Connection, opportunities: list[Opportunity]) -> int:
    """Insert only rows whose dedupe_key is unseen. Returns number inserted."""
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
                opp.posted_date, now, json.dumps(opp.tags), opp.dedupe_key,
            ),
        )
        inserted += cur.rowcount
    conn.commit()
    return inserted


def list_since(
    conn: sqlite3.Connection,
    since_iso: str,
    opportunity_type: str | None = None,
) -> list[sqlite3.Row]:
    query = "SELECT * FROM opportunities WHERE first_seen_at >= ?"
    params: list = [since_iso]
    if opportunity_type:
        query += " AND opportunity_type = ?"
        params.append(opportunity_type)
    query += " ORDER BY opportunity_type, org, posted_date DESC"
    return conn.execute(query, params).fetchall()
