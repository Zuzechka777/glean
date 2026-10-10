"""SQLite-ledger — единственный источник истины о статусах обработки.

Vault — лишь представление; повторная обработка решается по ledger.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import Status

SCHEMA = """
CREATE TABLE IF NOT EXISTS reels (
    shortcode   TEXT PRIMARY KEY,
    url         TEXT NOT NULL,
    status      TEXT NOT NULL,
    note_path   TEXT,
    attempts    INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    cost_usd    REAL NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


@dataclass
class LedgerEntry:
    shortcode: str
    url: str
    status: Status
    note_path: Optional[str]
    attempts: int
    error: Optional[str]
    cost_usd: float


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Ledger:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def get(self, shortcode: str) -> Optional[LedgerEntry]:
        row = self.conn.execute(
            "SELECT * FROM reels WHERE shortcode = ?", (shortcode,)
        ).fetchone()
        if not row:
            return None
        return LedgerEntry(
            shortcode=row["shortcode"],
            url=row["url"],
            status=Status(row["status"]),
            note_path=row["note_path"],
            attempts=row["attempts"],
            error=row["error"],
            cost_usd=row["cost_usd"],
        )

    def start_attempt(self, shortcode: str, url: str) -> None:
        now = _now()
        self.conn.execute(
            """
            INSERT INTO reels (shortcode, url, status, attempts, created_at, updated_at)
            VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(shortcode) DO UPDATE SET
                attempts = attempts + 1, updated_at = excluded.updated_at
            """,
            (shortcode, url, Status.failed_download.value, now, now),
        )
        self.conn.commit()

    def commit_result(
        self,
        shortcode: str,
        status: Status,
        note_path: Optional[str],
        cost_usd: float = 0.0,
        error: Optional[str] = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE reels SET status = ?, note_path = ?, error = ?,
                cost_usd = cost_usd + ?, updated_at = ?
            WHERE shortcode = ?
            """,
            (status.value, note_path, error, cost_usd, _now(), shortcode),
        )
        self.conn.commit()

    def pending(self, statuses: list[Status], max_attempts: int) -> list[LedgerEntry]:
        marks = ",".join("?" * len(statuses))
        rows = self.conn.execute(
            f"SELECT shortcode FROM reels WHERE status IN ({marks}) AND attempts < ?",
            [s.value for s in statuses] + [max_attempts],
        ).fetchall()
        return [e for r in rows if (e := self.get(r["shortcode"]))]

    def stats(self) -> dict:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM reels GROUP BY status"
        ).fetchall()
        total_cost = self.conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM reels").fetchone()[0]
        return {
            "by_status": {r["status"]: r["n"] for r in rows},
            "total": sum(r["n"] for r in rows),
            "total_cost_usd": round(total_cost, 4),
        }
