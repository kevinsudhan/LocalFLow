"""Runtime quality metrics - the zero-edit rate above all.

The offline benchmark measures LocalFlow against cases *we* wrote. This measures
it against what the user actually said and what they actually did afterwards,
which is the only number that reflects the product working.

    Zero-edit rate = dictations inserted and left alone
                     ÷ dictations inserted

A dictation counts as edited when the user changes its text in History, says
"undo that", or presses the undo shortcut within a short window of inserting it.
Nothing here leaves the machine; it is all derived from the local history table.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from typing import Any

from ..database.db import Database, utcnow

# An undo long after the fact is a change of mind, not a correction.
UNDO_WINDOW_SECONDS = 120


@dataclass
class QualitySnapshot:
    inserted: int = 0
    edited: int = 0
    undone: int = 0
    zero_edit_rate: float = 0.0
    llm_rate: float = 0.0
    llm_ran_rate: float = 0.0
    latency: dict[str, float] = field(default_factory=dict)
    by_app: list[dict[str, Any]] = field(default_factory=list)
    llm_series: list[dict[str, Any]] = field(default_factory=list)
    window_days: int = 30

    def as_dict(self) -> dict[str, Any]:
        return {
            "inserted": self.inserted,
            "edited": self.edited,
            "undone": self.undone,
            "zero_edit_rate": round(self.zero_edit_rate, 4),
            "llm_rate": round(self.llm_rate, 4),
            "llm_ran_rate": round(self.llm_ran_rate, 4),
            "latency": self.latency,
            "by_app": self.by_app,
            "llm_series": self.llm_series,
            "window_days": self.window_days,
            "sample_is_small": self.inserted < 20,
        }


class QualityMetrics:
    """Reads and writes the per-dictation quality columns."""

    def __init__(self, db: Database):
        self.db = db

    # -- recording outcomes ------------------------------------------------
    def mark_edited(self, history_id: int, reason: str = "edit") -> None:
        """The user changed or removed text LocalFlow produced."""
        self.db.execute(
            "UPDATE history SET edited=1, edit_reason=?, edited_at=? WHERE id=?",
            (reason, utcnow(), history_id),
        )

    def mark_undone(self, history_id: int) -> None:
        self.db.execute(
            "UPDATE history SET undone=1, edited=1, edit_reason='undo', edited_at=? "
            "WHERE id=?",
            (utcnow(), history_id),
        )

    def undo_recent(self) -> int | None:
        """Attribute an undo to the dictation it most likely removed.

        Only the most recent insertion, and only if it is recent enough that an
        undo plausibly refers to it.
        """
        row = self.db.query_one(
            "SELECT id, created_at FROM history WHERE inserted=1 "
            "ORDER BY id DESC LIMIT 1"
        )
        if row is None:
            return None
        age = self.db.query_one(
            "SELECT CAST((julianday(?) - julianday(created_at)) * 86400 AS INTEGER) AS seconds "
            "FROM history WHERE id=?",
            (utcnow(), row["id"]),
        )
        if age is not None and age["seconds"] is not None:
            if age["seconds"] > UNDO_WINDOW_SECONDS:
                return None
        self.mark_undone(int(row["id"]))
        return int(row["id"])

    # -- reading -----------------------------------------------------------
    def snapshot(self, days: int = 30) -> QualitySnapshot:
        window = f"-{days} days" if days > 0 else "-100 years"
        totals = self.db.query_one(
            "SELECT COUNT(*) AS inserted,"
            " COALESCE(SUM(edited),0) AS edited,"
            " COALESCE(SUM(undone),0) AS undone,"
            " COALESCE(SUM(used_llm),0) AS llm"
            " FROM history WHERE inserted=1 AND created_at >= datetime('now', ?)",
            (window,),
        )
        inserted = int(totals["inserted"]) if totals else 0
        edited = int(totals["edited"]) if totals else 0
        undone = int(totals["undone"]) if totals else 0
        llm = int(totals["llm"]) if totals else 0

        snapshot = QualitySnapshot(
            inserted=inserted,
            edited=edited,
            undone=undone,
            zero_edit_rate=(inserted - edited) / inserted if inserted else 0.0,
            llm_rate=llm / inserted if inserted else 0.0,
            window_days=days,
        )
        snapshot.latency = self._latency(window)
        snapshot.by_app = self._by_app(window)
        snapshot.llm_series = self._llm_series(window)
        ran = sum(1 for point in snapshot.llm_series if point["llm_ran"])
        if snapshot.llm_series:
            snapshot.llm_ran_rate = ran / len(snapshot.llm_series)
        return snapshot

    def _latency(self, window: str) -> dict[str, float]:
        rows = self.db.query(
            "SELECT latency FROM history WHERE inserted=1 "
            "AND created_at >= datetime('now', ?) ORDER BY id DESC LIMIT 500",
            (window,),
        )
        buckets: dict[str, list[float]] = {}
        for row in rows:
            try:
                payload = json.loads(row["latency"] or "{}")
            except (ValueError, TypeError):
                continue
            for key, value in payload.items():
                if isinstance(value, (int, float)) and value > 0:
                    buckets.setdefault(key, []).append(float(value))

        out: dict[str, float] = {}
        for key, values in buckets.items():
            if not values:
                continue
            ordered = sorted(values)
            out[f"{key}_median"] = round(statistics.median(ordered), 1)
            index = max(0, int(len(ordered) * 0.95) - 1)
            out[f"{key}_p95"] = round(ordered[index], 1)
        return out

    def _llm_series(self, window: str, limit: int = 40) -> list[dict[str, Any]]:
        """Per-dictation timings, oldest first, for the dashboard graph.

        A median tells you the language model is slow; it does not tell you that
        the slow ones are the first call after it was unloaded. The series does,
        because a reload stands out as a spike rather than being averaged away.

        Dictations that never reached the language model are kept, with
        `llm_ms` of zero. Dropping them would make the graph look like the model
        runs on everything, which is the opposite of what it shows.
        """
        rows = self.db.query(
            "SELECT id, created_at, used_llm, latency FROM history"
            " WHERE inserted=1 AND created_at >= datetime('now', ?)"
            " ORDER BY id DESC LIMIT ?",
            (window, limit),
        )
        out: list[dict[str, Any]] = []
        for row in reversed(rows):
            try:
                payload = json.loads(row["latency"] or "{}")
            except (ValueError, TypeError):
                payload = {}

            def ms(key: str) -> float:
                value = payload.get(key)
                return round(float(value), 1) if isinstance(value, (int, float)) else 0.0

            llm_ms = ms("llm_ms")
            # Time from the end of speech to inserted text. Recording is
            # excluded: that is how long the user chose to speak, not a cost.
            total, record = ms("total_ms"), ms("record_ms")
            response = total - record if total > record else (
                ms("vad_ms") + ms("asr_ms") + ms("process_ms")
            )
            out.append(
                {
                    "id": int(row["id"]),
                    "at": row["created_at"],
                    # Whether the model ran, and whether its output survived the
                    # validator. These are not the same thing: a rewrite that
                    # drops a number is rejected, and the time it cost is real
                    # either way.
                    "llm_ran": llm_ms > 0,
                    "used_llm": bool(row["used_llm"]),
                    "llm_ms": llm_ms,
                    "asr_ms": ms("asr_ms"),
                    "response_ms": round(response, 1),
                }
            )
        return out

    def _by_app(self, window: str, limit: int = 12) -> list[dict[str, Any]]:
        """Where LocalFlow is doing well, and where it is not."""
        rows = self.db.query(
            "SELECT app_name, COUNT(*) AS n, COALESCE(SUM(edited),0) AS edited,"
            " COALESCE(SUM(used_llm),0) AS llm"
            " FROM history WHERE inserted=1 AND created_at >= datetime('now', ?)"
            " GROUP BY app_name ORDER BY n DESC LIMIT ?",
            (window, limit),
        )
        out = []
        for row in rows:
            total = int(row["n"]) or 1
            out.append(
                {
                    "app": row["app_name"] or "Unknown",
                    "dictations": total,
                    "edited": int(row["edited"]),
                    "zero_edit_rate": round((total - int(row["edited"])) / total, 3),
                    "llm_rate": round(int(row["llm"]) / total, 3),
                }
            )
        return out
