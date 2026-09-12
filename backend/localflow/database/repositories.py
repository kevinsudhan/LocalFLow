"""Typed repositories over the SQLite tables."""
from __future__ import annotations

import json
from typing import Any

from .db import Database, json_loads, row_to_dict, utcnow


class SettingsRepo:
    def __init__(self, db: Database):
        self.db = db

    def load_all(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for row in self.db.query("SELECT section, payload FROM settings"):
            out[row["section"]] = json_loads(row["payload"], {})
        return out

    def save_section(self, section: str, payload: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT INTO settings(section, payload, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(section) DO UPDATE SET payload=excluded.payload, "
            "updated_at=excluded.updated_at",
            (section, json.dumps(payload, ensure_ascii=False), utcnow()),
        )

    def save_all(self, data: dict[str, Any]) -> None:
        for section, payload in data.items():
            if isinstance(payload, dict):
                self.save_section(section, payload)

    def reset(self) -> None:
        self.db.execute("DELETE FROM settings")


class VocabularyRepo:
    def __init__(self, db: Database):
        self.db = db

    def list(self, search: str = "", limit: int = 1000) -> list[dict[str, Any]]:
        if search:
            rows = self.db.query(
                "SELECT * FROM vocabulary WHERE term LIKE ? OR sounds_like LIKE ? "
                "ORDER BY term COLLATE NOCASE LIMIT ?",
                (f"%{search}%", f"%{search}%", limit),
            )
        else:
            rows = self.db.query(
                "SELECT * FROM vocabulary ORDER BY term COLLATE NOCASE LIMIT ?", (limit,)
            )
        return [self._hydrate(r) for r in rows]

    def active(self) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM vocabulary WHERE enabled=1 ORDER BY term")
        return [self._hydrate(r) for r in rows]

    @staticmethod
    def _hydrate(row) -> dict[str, Any]:
        d = row_to_dict(row) or {}
        d["sounds_like"] = json_loads(d.get("sounds_like"), [])
        d["enabled"] = bool(d.get("enabled", 1))
        d["case_sensitive"] = bool(d.get("case_sensitive", 1))
        return d

    def add(
        self,
        term: str,
        sounds_like: list[str] | None = None,
        category: str = "general",
        case_sensitive: bool = True,
        enabled: bool = True,
    ) -> dict[str, Any] | None:
        term = (term or "").strip()
        if not term:
            return None
        now = utcnow()
        cleaned = sorted({s.strip() for s in (sounds_like or []) if s and s.strip()})
        self.db.execute(
            "INSERT INTO vocabulary(term, sounds_like, category, case_sensitive, enabled,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(term) DO UPDATE SET sounds_like=excluded.sounds_like,"
            " category=excluded.category, case_sensitive=excluded.case_sensitive,"
            " enabled=excluded.enabled, updated_at=excluded.updated_at",
            (term, json.dumps(cleaned), category, int(case_sensitive), int(enabled), now, now),
        )
        return self.get_by_term(term)

    def get_by_term(self, term: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM vocabulary WHERE term=?", (term,))
        return self._hydrate(row) if row else None

    def update(self, item_id: int, **fields: Any) -> dict[str, Any] | None:
        allowed = {"term", "sounds_like", "category", "case_sensitive", "enabled"}
        sets: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "sounds_like":
                value = json.dumps(list(value or []))
            elif key in ("case_sensitive", "enabled"):
                value = int(bool(value))
            sets.append(key + "=?")
            params.append(value)
        if not sets:
            return None
        sets.append("updated_at=?")
        params.extend([utcnow(), item_id])
        self.db.execute("UPDATE vocabulary SET " + ", ".join(sets) + " WHERE id=?", params)
        row = self.db.query_one("SELECT * FROM vocabulary WHERE id=?", (item_id,))
        return self._hydrate(row) if row else None

    def delete(self, item_id: int) -> None:
        self.db.execute("DELETE FROM vocabulary WHERE id=?", (item_id,))

    def clear(self) -> None:
        self.db.execute("DELETE FROM vocabulary")

    def bump_hits(self, terms: list[str]) -> None:
        if not terms:
            return
        self.db.executemany(
            "UPDATE vocabulary SET hits = hits + 1 WHERE term=?", [(t,) for t in terms]
        )


class CorrectionRepo:
    """Learned ``wrong -> correct`` pairs observed from the user's own edits."""

    def __init__(self, db: Database):
        self.db = db

    def list(self, only_promoted: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM learned_corrections"
        if only_promoted:
            sql += " WHERE promoted=1 AND enabled=1"
        sql += " ORDER BY occurrences DESC, last_seen DESC"
        return [self._hydrate(r) for r in self.db.query(sql)]

    def active(self) -> list[dict[str, Any]]:
        return self.list(only_promoted=True)

    @staticmethod
    def _hydrate(row) -> dict[str, Any]:
        d = row_to_dict(row) or {}
        d["promoted"] = bool(d.get("promoted", 0))
        d["enabled"] = bool(d.get("enabled", 1))
        return d

    def observe(
        self, wrong: str, correct: str, threshold: int, source: str = "edit"
    ) -> dict[str, Any] | None:
        wrong = (wrong or "").strip()
        correct = (correct or "").strip()
        if not wrong or not correct or wrong == correct:
            return None
        now = utcnow()
        self.db.execute(
            "INSERT INTO learned_corrections(wrong, correct, occurrences, promoted, source,"
            " created_at, last_seen) VALUES (?,?,1,0,?,?,?) "
            "ON CONFLICT(wrong, correct) DO UPDATE SET occurrences=occurrences+1,"
            " last_seen=excluded.last_seen",
            (wrong, correct, source, now, now),
        )
        self.db.execute(
            "UPDATE learned_corrections SET promoted=1 WHERE wrong=? AND correct=? "
            "AND occurrences>=?",
            (wrong, correct, threshold),
        )
        row = self.db.query_one(
            "SELECT * FROM learned_corrections WHERE wrong=? AND correct=?", (wrong, correct)
        )
        return self._hydrate(row) if row else None

    def set_enabled(self, item_id: int, enabled: bool) -> None:
        self.db.execute(
            "UPDATE learned_corrections SET enabled=? WHERE id=?", (int(enabled), item_id)
        )

    def promote(self, item_id: int, promoted: bool = True) -> None:
        self.db.execute(
            "UPDATE learned_corrections SET promoted=? WHERE id=?", (int(promoted), item_id)
        )

    def delete(self, item_id: int) -> None:
        self.db.execute("DELETE FROM learned_corrections WHERE id=?", (item_id,))

    def clear(self) -> None:
        self.db.execute("DELETE FROM learned_corrections")


class SnippetRepo:
    def __init__(self, db: Database):
        self.db = db

    def list(self) -> list[dict[str, Any]]:
        return [self._hydrate(r) for r in self.db.query("SELECT * FROM snippets ORDER BY name")]

    def active(self) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM snippets WHERE enabled=1 ORDER BY LENGTH(trigger) DESC"
        )
        return [self._hydrate(r) for r in rows]

    @staticmethod
    def _hydrate(row) -> dict[str, Any]:
        d = row_to_dict(row) or {}
        d["enabled"] = bool(d.get("enabled", 1))
        return d

    def add(
        self,
        name: str,
        trigger: str,
        expansion: str,
        mode: str = "replace_all",
        enabled: bool = True,
    ) -> dict[str, Any] | None:
        trigger = " ".join((trigger or "").lower().split())
        if not trigger or not expansion:
            return None
        now = utcnow()
        self.db.execute(
            "INSERT INTO snippets(name, trigger, expansion, mode, enabled, created_at,"
            " updated_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT(trigger) DO UPDATE SET"
            " name=excluded.name, expansion=excluded.expansion, mode=excluded.mode,"
            " enabled=excluded.enabled, updated_at=excluded.updated_at",
            (name or trigger, trigger, expansion, mode, int(enabled), now, now),
        )
        row = self.db.query_one("SELECT * FROM snippets WHERE trigger=?", (trigger,))
        return self._hydrate(row) if row else None

    def update(self, item_id: int, **fields: Any) -> dict[str, Any] | None:
        allowed = {"name", "trigger", "expansion", "mode", "enabled"}
        sets: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "trigger":
                value = " ".join(str(value).lower().split())
            elif key == "enabled":
                value = int(bool(value))
            sets.append(key + "=?")
            params.append(value)
        if not sets:
            return None
        sets.append("updated_at=?")
        params.extend([utcnow(), item_id])
        self.db.execute("UPDATE snippets SET " + ", ".join(sets) + " WHERE id=?", params)
        row = self.db.query_one("SELECT * FROM snippets WHERE id=?", (item_id,))
        return self._hydrate(row) if row else None

    def delete(self, item_id: int) -> None:
        self.db.execute("DELETE FROM snippets WHERE id=?", (item_id,))

    def bump_use(self, trigger: str) -> None:
        self.db.execute("UPDATE snippets SET uses = uses + 1 WHERE trigger=?", (trigger,))


def _fts_query(search: str) -> str:
    """Turn free text into a safe FTS5 prefix query."""
    safe = "".join(c if (c.isalnum() or c.isspace()) else " " for c in search)
    tokens = [t for t in safe.split() if t]
    if not tokens:
        return '""'
    return " ".join('"' + t + '"*' for t in tokens)


class HistoryRepo:
    def __init__(self, db: Database):
        self.db = db

    def add(self, entry: dict[str, Any]) -> int:
        cur = self.db.execute(
            "INSERT INTO history(created_at, app_exe, app_name, app_category, window_title,"
            " raw_transcript, final_text, language, style, duration_ms, used_llm, latency,"
            " audio_path, inserted, word_count) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                entry.get("created_at") or utcnow(),
                entry.get("app_exe", ""),
                entry.get("app_name", ""),
                entry.get("app_category", "general"),
                entry.get("window_title", ""),
                entry.get("raw_transcript", ""),
                entry.get("final_text", ""),
                entry.get("language", ""),
                entry.get("style", "neutral"),
                int(entry.get("duration_ms", 0)),
                int(bool(entry.get("used_llm", False))),
                json.dumps(entry.get("latency", {})),
                entry.get("audio_path"),
                int(bool(entry.get("inserted", False))),
                int(entry.get("word_count", 0)),
            ),
        )
        return int(cur.lastrowid or 0)

    def list(self, search: str = "", limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        if search.strip():
            rows = self.db.query(
                "SELECT h.* FROM history_fts f JOIN history h ON h.id = f.rowid "
                "WHERE history_fts MATCH ? ORDER BY h.created_at DESC LIMIT ? OFFSET ?",
                (_fts_query(search), limit, offset),
            )
        else:
            rows = self.db.query(
                "SELECT * FROM history ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        return [self._hydrate(r) for r in rows]

    @staticmethod
    def _hydrate(row) -> dict[str, Any]:
        d = row_to_dict(row) or {}
        d["latency"] = json_loads(d.get("latency"), {})
        d["used_llm"] = bool(d.get("used_llm", 0))
        d["inserted"] = bool(d.get("inserted", 0))
        return d

    def get(self, item_id: int) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM history WHERE id=?", (item_id,))
        return self._hydrate(row) if row else None

    def latest(self) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM history ORDER BY id DESC LIMIT 1")
        return self._hydrate(row) if row else None

    def delete(self, item_id: int) -> str | None:
        row = self.db.query_one("SELECT audio_path FROM history WHERE id=?", (item_id,))
        self.db.execute("DELETE FROM history WHERE id=?", (item_id,))
        return row["audio_path"] if row else None

    def clear(self) -> list[str]:
        rows = self.db.query("SELECT audio_path FROM history WHERE audio_path IS NOT NULL")
        self.db.execute("DELETE FROM history")
        return [r["audio_path"] for r in rows if r["audio_path"]]

    def prune(self, days: int, audio_days: int) -> list[str]:
        """Delete rows past retention; returns orphaned audio files to unlink."""
        removed: list[str] = []
        if audio_days > 0:
            rows = self.db.query(
                "SELECT audio_path FROM history WHERE audio_path IS NOT NULL "
                "AND created_at < datetime('now', ?)",
                ("-" + str(audio_days) + " days",),
            )
            removed += [r["audio_path"] for r in rows if r["audio_path"]]
            self.db.execute(
                "UPDATE history SET audio_path=NULL WHERE created_at < datetime('now', ?)",
                ("-" + str(audio_days) + " days",),
            )
        if days > 0:
            rows = self.db.query(
                "SELECT audio_path FROM history WHERE audio_path IS NOT NULL "
                "AND created_at < datetime('now', ?)",
                ("-" + str(days) + " days",),
            )
            removed += [r["audio_path"] for r in rows if r["audio_path"]]
            self.db.execute(
                "DELETE FROM history WHERE created_at < datetime('now', ?)",
                ("-" + str(days) + " days",),
            )
        return removed

    def stats(self) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT COUNT(*) AS n, COALESCE(SUM(word_count),0) AS words,"
            " COALESCE(SUM(duration_ms),0) AS ms FROM history"
        )
        today = self.db.query_one(
            "SELECT COUNT(*) AS n, COALESCE(SUM(word_count),0) AS words FROM history"
            " WHERE created_at >= date('now')"
        )
        return {
            "total": row["n"] if row else 0,
            "words": row["words"] if row else 0,
            "speech_ms": row["ms"] if row else 0,
            "today": today["n"] if today else 0,
            "today_words": today["words"] if today else 0,
        }

    def mark_edited(self, item_id: int, new_text: str) -> None:
        self.db.execute("UPDATE history SET final_text=? WHERE id=?", (new_text, item_id))


class ApplicationProfileRepo:
    def __init__(self, db: Database):
        self.db = db

    def list(self) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM application_profiles ORDER BY priority ASC, pattern ASC"
        )
        return [self._hydrate(r) for r in rows]

    def active(self) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM application_profiles WHERE enabled=1 ORDER BY priority ASC"
        )
        return [self._hydrate(r) for r in rows]

    @staticmethod
    def _hydrate(row) -> dict[str, Any]:
        d = row_to_dict(row) or {}
        d["llm_enabled"] = bool(d.get("llm_enabled", 1))
        d["enabled"] = bool(d.get("enabled", 1))
        d["builtin"] = bool(d.get("builtin", 0))
        return d

    def upsert(self, profile: dict[str, Any]) -> dict[str, Any] | None:
        now = utcnow()
        pattern = (profile.get("pattern") or "").strip().lower()
        match_on = profile.get("match_on", "exe")
        if not pattern:
            return None
        self.db.execute(
            "INSERT INTO application_profiles(pattern, match_on, app_name, category, style,"
            " llm_enabled, injection_method, builtin, enabled, priority, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(pattern, match_on) DO UPDATE SET app_name=excluded.app_name,"
            " category=excluded.category, style=excluded.style,"
            " llm_enabled=excluded.llm_enabled,"
            " injection_method=excluded.injection_method, enabled=excluded.enabled,"
            " priority=excluded.priority, updated_at=excluded.updated_at",
            (
                pattern,
                match_on,
                profile.get("app_name", ""),
                profile.get("category", "general"),
                profile.get("style", ""),
                int(bool(profile.get("llm_enabled", True))),
                profile.get("injection_method", "auto"),
                int(bool(profile.get("builtin", False))),
                int(bool(profile.get("enabled", True))),
                int(profile.get("priority", 100)),
                now,
                now,
            ),
        )
        row = self.db.query_one(
            "SELECT * FROM application_profiles WHERE pattern=? AND match_on=?",
            (pattern, match_on),
        )
        return self._hydrate(row) if row else None

    def delete(self, item_id: int) -> None:
        self.db.execute("DELETE FROM application_profiles WHERE id=? AND builtin=0", (item_id,))

    def count(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM application_profiles")
        return int(row["n"]) if row else 0
