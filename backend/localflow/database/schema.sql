-- LocalFlow local database.  Everything here stays on this machine.
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    section     TEXT PRIMARY KEY,
    payload     TEXT NOT NULL,             -- JSON object for that section
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vocabulary (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    term        TEXT NOT NULL UNIQUE,      -- canonical spelling, e.g. "Araxys"
    sounds_like TEXT NOT NULL DEFAULT '[]',-- JSON array of ASR mishearings
    category    TEXT NOT NULL DEFAULT 'general',
    case_sensitive INTEGER NOT NULL DEFAULT 1,
    enabled     INTEGER NOT NULL DEFAULT 1,
    hits        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vocabulary_enabled ON vocabulary(enabled);

CREATE TABLE IF NOT EXISTS learned_corrections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    wrong       TEXT NOT NULL,
    correct     TEXT NOT NULL,
    occurrences INTEGER NOT NULL DEFAULT 1,
    promoted    INTEGER NOT NULL DEFAULT 0, -- 1 once it is actually applied
    enabled     INTEGER NOT NULL DEFAULT 1,
    source      TEXT NOT NULL DEFAULT 'edit',
    created_at  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    UNIQUE(wrong, correct)
);
CREATE INDEX IF NOT EXISTS idx_corrections_promoted ON learned_corrections(promoted, enabled);

CREATE TABLE IF NOT EXISTS snippets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    trigger     TEXT NOT NULL UNIQUE,      -- spoken trigger phrase, normalised
    expansion   TEXT NOT NULL,
    mode        TEXT NOT NULL DEFAULT 'replace_all', -- replace_all | inline
    enabled     INTEGER NOT NULL DEFAULT 1,
    uses        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    app_exe     TEXT NOT NULL DEFAULT '',
    app_name    TEXT NOT NULL DEFAULT '',
    app_category TEXT NOT NULL DEFAULT 'general',
    window_title TEXT NOT NULL DEFAULT '',
    raw_transcript TEXT NOT NULL DEFAULT '',
    final_text  TEXT NOT NULL DEFAULT '',
    language    TEXT NOT NULL DEFAULT '',
    style       TEXT NOT NULL DEFAULT 'neutral',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    used_llm    INTEGER NOT NULL DEFAULT 0,
    latency     TEXT NOT NULL DEFAULT '{}',-- JSON breakdown
    audio_path  TEXT,                      -- NULL unless audio history is on
    inserted    INTEGER NOT NULL DEFAULT 0,
    word_count  INTEGER NOT NULL DEFAULT 0,
    -- Quality tracking. `edited` is the signal behind the zero-edit rate:
    -- the user changed, undid or deleted what LocalFlow produced.
    edited      INTEGER NOT NULL DEFAULT 0,
    undone      INTEGER NOT NULL DEFAULT 0,
    edit_reason TEXT NOT NULL DEFAULT '',
    edited_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_history_created ON history(created_at DESC);
CREATE VIRTUAL TABLE IF NOT EXISTS history_fts USING fts5(
    final_text, raw_transcript, app_name, content='history', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS history_ai AFTER INSERT ON history BEGIN
    INSERT INTO history_fts(rowid, final_text, raw_transcript, app_name)
    VALUES (new.id, new.final_text, new.raw_transcript, new.app_name);
END;
CREATE TRIGGER IF NOT EXISTS history_ad AFTER DELETE ON history BEGIN
    INSERT INTO history_fts(history_fts, rowid, final_text, raw_transcript, app_name)
    VALUES ('delete', old.id, old.final_text, old.raw_transcript, old.app_name);
END;
CREATE TRIGGER IF NOT EXISTS history_au AFTER UPDATE ON history BEGIN
    INSERT INTO history_fts(history_fts, rowid, final_text, raw_transcript, app_name)
    VALUES ('delete', old.id, old.final_text, old.raw_transcript, old.app_name);
    INSERT INTO history_fts(rowid, final_text, raw_transcript, app_name)
    VALUES (new.id, new.final_text, new.raw_transcript, new.app_name);
END;

CREATE TABLE IF NOT EXISTS application_profiles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern     TEXT NOT NULL,             -- exe name or title substring
    match_on    TEXT NOT NULL DEFAULT 'exe', -- exe | title | url
    app_name    TEXT NOT NULL DEFAULT '',
    category    TEXT NOT NULL DEFAULT 'general',
    style       TEXT NOT NULL DEFAULT '',  -- '' -> inherit global default
    llm_enabled INTEGER NOT NULL DEFAULT 1,
    injection_method TEXT NOT NULL DEFAULT 'auto',
    builtin     INTEGER NOT NULL DEFAULT 0,
    enabled     INTEGER NOT NULL DEFAULT 1,
    priority    INTEGER NOT NULL DEFAULT 100,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE(pattern, match_on)
);
