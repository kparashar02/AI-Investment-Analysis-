"""Analysis history persistence (PRD Phase 5).

A small SQLite store, on the standard-library ``sqlite3`` — no ORM dependency.
It keeps a row per completed analysis (the summary fields for a history list,
plus the full decision JSON and the pre-rendered report) so a past analysis can
be listed and re-opened without re-running the pipeline. A connection is opened
per operation, which keeps it safe under FastAPI's thread pool.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config.settings import REPO_ROOT

DEFAULT_DB = REPO_ROOT / "data" / "history.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    id             TEXT PRIMARY KEY,
    ticker         TEXT,
    company_name   TEXT,
    rating         TEXT,
    composite      REAL,
    basis          TEXT,
    confidence     TEXT,
    created_at     TEXT,
    decision_json  TEXT,
    report_html    TEXT,
    report_md      TEXT
);
"""

_SUMMARY_COLS = ("id", "ticker", "company_name", "rating", "composite",
                 "basis", "confidence", "created_at")


class HistoryStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = str(path if path is not None else DEFAULT_DB)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._shared = sqlite3.connect(self.path) if self.path == ":memory:" else None
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = self._shared or sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        conn = self._conn()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            if self._shared is None:
                conn.close()

    def save(self, record: dict[str, Any]) -> str:
        record = {**record}
        record.setdefault("created_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        conn = self._conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO analyses "
                "(id, ticker, company_name, rating, composite, basis, confidence, "
                " created_at, decision_json, report_html, report_md) "
                "VALUES (:id, :ticker, :company_name, :rating, :composite, :basis, "
                ":confidence, :created_at, :decision_json, :report_html, :report_md)",
                {k: record.get(k) for k in (
                    "id", "ticker", "company_name", "rating", "composite", "basis",
                    "confidence", "created_at", "decision_json", "report_html", "report_md")},
            )
            conn.commit()
        finally:
            if self._shared is None:
                conn.close()
        return record["id"]

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        conn = self._conn()
        try:
            rows = conn.execute(
                f"SELECT {', '.join(_SUMMARY_COLS)} FROM analyses "
                "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            if self._shared is None:
                conn.close()

    def get(self, analysis_id: str) -> dict[str, Any] | None:
        conn = self._conn()
        try:
            row = conn.execute("SELECT * FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
            return dict(row) if row else None
        finally:
            if self._shared is None:
                conn.close()
