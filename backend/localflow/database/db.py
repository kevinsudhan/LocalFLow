"""SQLite access layer.

A single connection is shared across the process with ``check_same_thread=False``
and guarded by a re-entrant lock.  Dictation traffic is tiny (a few rows per
utterance), so a connection pool would be pure overhead; the lock keeps the
audio, ASR and server threads from interleaving writes.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2
_SCHEMA_FILE = Path(__file__).with_name("schema.sql")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._conn = connect(path)
        self._lock = threading.RLock()
        self._migrate()

    # -- lifecycle ---------------------------------------------------------
    def _migrate(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA_FILE.read_text(encoding="utf-8"))
            self._add_missing_columns()
            row = self._conn.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,)
                )
            self._conn.commit()

    def _add_missing_columns(self) -> None:
        """Add columns introduced after a user's database was created.

        `CREATE TABLE IF NOT EXISTS` in schema.sql never alters an existing
        table, so new columns have to be added explicitly. Doing it by
        inspection keeps this idempotent and avoids a migration table.
        """
        additions = {
            "history": [
                ("edited", "INTEGER NOT NULL DEFAULT 0"),
                ("undone", "INTEGER NOT NULL DEFAULT 0"),
                ("edit_reason", "TEXT NOT NULL DEFAULT ''"),
                ("edited_at", "TEXT"),
            ],
        }
        for table, columns in additions.items():
            existing = {
                row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")
            }
            for name, definition in columns:
                if name not in existing:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
                    log.info("Added column %s.%s", table, name)

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
            finally:
                self._conn.close()

    # -- primitives --------------------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        with self._lock:
            self._conn.executemany(sql, rows)
            self._conn.commit()

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def vacuum(self) -> None:
        with self._lock:
            self._conn.execute("VACUUM")


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def json_loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return fallback
