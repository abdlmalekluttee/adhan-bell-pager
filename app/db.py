"""SQLite storage for users, roles, sessions, school bells, executions and audit.

One process-wide connection guarded by a re-entrant lock. The application is a
small single-instance service, so serialising database access is simpler and
safer than a pool, and WAL mode keeps readers from blocking the writer on disk.

Schema changes are append-only numbered migrations; ``migrate()`` applies the
pending ones in a transaction at start-up and records the version.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import threading
import time

from . import config

log = logging.getLogger("db")

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_path: str | None = None


def default_path() -> str:
    return os.environ.get("DB_PATH") or os.path.join(
        os.path.dirname(config.CONFIG_PATH), "app.db")


MIGRATIONS: list[tuple[int, str]] = [
    (1, """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        display_name TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'local' CHECK (source IN ('local', 'ad')),
        password_hash TEXT,
        disabled INTEGER NOT NULL DEFAULT 0,
        must_change_password INTEGER NOT NULL DEFAULT 0,
        is_recovery INTEGER NOT NULL DEFAULT 0,
        language TEXT NOT NULL DEFAULT 'en',
        theme TEXT NOT NULL DEFAULT 'auto',
        ad_dn TEXT,
        ad_groups TEXT NOT NULL DEFAULT '[]',
        ad_role_ids TEXT NOT NULL DEFAULT '[]',
        ad_synced_at INTEGER,
        ad_sync_error TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        last_login INTEGER
    );
    CREATE TABLE roles (
        id INTEGER PRIMARY KEY,
        key TEXT UNIQUE,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        description TEXT NOT NULL DEFAULT '',
        builtin INTEGER NOT NULL DEFAULT 0,
        permissions TEXT NOT NULL DEFAULT '[]',
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    );
    CREATE TABLE user_roles (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
        PRIMARY KEY (user_id, role_id)
    );
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        csrf TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        last_seen INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        remember INTEGER NOT NULL DEFAULT 0,
        ip TEXT NOT NULL DEFAULT '',
        user_agent TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX sessions_user ON sessions(user_id);
    CREATE TABLE ad_group_mappings (
        id INTEGER PRIMARY KEY,
        group_dn TEXT NOT NULL COLLATE NOCASE,
        group_name TEXT NOT NULL DEFAULT '',
        role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
        created_at INTEGER NOT NULL,
        UNIQUE (group_dn, role_id)
    );
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at INTEGER NOT NULL
    );
    CREATE TABLE audit_log (
        id INTEGER PRIMARY KEY,
        ts INTEGER NOT NULL,
        actor TEXT NOT NULL DEFAULT '',
        ip TEXT NOT NULL DEFAULT '',
        action TEXT NOT NULL,
        target TEXT NOT NULL DEFAULT '',
        result TEXT NOT NULL DEFAULT 'ok',
        details TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX audit_ts ON audit_log(ts);
    CREATE TABLE bell_timetables (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        description TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    );
    CREATE TABLE bell_events (
        id INTEGER PRIMARY KEY,
        timetable_id INTEGER NOT NULL REFERENCES bell_timetables(id) ON DELETE CASCADE,
        label TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'period'
            CHECK (kind IN ('start_of_day', 'period', 'break', 'end_of_day', 'custom')),
        start_time TEXT NOT NULL,
        end_time TEXT,
        ring_start INTEGER NOT NULL DEFAULT 1,
        ring_end INTEGER NOT NULL DEFAULT 0,
        sound_start TEXT NOT NULL DEFAULT '',
        sound_end TEXT NOT NULL DEFAULT '',
        zone_ids TEXT,
        enabled INTEGER NOT NULL DEFAULT 1,
        sort INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX bell_events_tt ON bell_events(timetable_id);
    CREATE TABLE bell_schedules (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        description TEXT NOT NULL DEFAULT '',
        enabled INTEGER NOT NULL DEFAULT 1,
        effective_from TEXT NOT NULL,
        effective_to TEXT,
        days TEXT NOT NULL DEFAULT '{}',
        zone_ids TEXT NOT NULL DEFAULT '[]',
        sound TEXT NOT NULL DEFAULT '',
        playback_mode TEXT NOT NULL DEFAULT 'once'
            CHECK (playback_mode IN ('once', 'repeat', 'duration')),
        repeat_count INTEGER NOT NULL DEFAULT 1,
        duration_seconds INTEGER NOT NULL DEFAULT 10,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    );
    CREATE TABLE bell_exceptions (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('holiday', 'no_bells', 'override')),
        date_from TEXT NOT NULL,
        date_to TEXT NOT NULL,
        timetable_id INTEGER REFERENCES bell_timetables(id) ON DELETE CASCADE,
        schedule_id INTEGER REFERENCES bell_schedules(id) ON DELETE CASCADE,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    );
    CREATE TABLE executions (
        id INTEGER PRIMARY KEY,
        event_key TEXT NOT NULL UNIQUE,
        module TEXT NOT NULL,
        kind TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        priority INTEGER NOT NULL DEFAULT 0,
        scheduled_for TEXT,
        created_at INTEGER NOT NULL,
        started_at INTEGER,
        finished_at INTEGER,
        status TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        actor TEXT NOT NULL DEFAULT '',
        sound TEXT NOT NULL DEFAULT '',
        targets TEXT NOT NULL DEFAULT '[]',
        results TEXT NOT NULL DEFAULT '[]',
        codec TEXT NOT NULL DEFAULT '',
        delay_seconds REAL
    );
    CREATE INDEX executions_created ON executions(created_at);
    CREATE INDEX executions_module_status ON executions(module, status);
    """),
]


def open_db(path: str | None = None) -> sqlite3.Connection:
    """(Re)open the database at ``path`` and apply migrations."""
    global _conn, _path
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:  # noqa: BLE001
                pass
        _path = path or default_path()
        os.makedirs(os.path.dirname(_path) or ".", exist_ok=True)
        conn = sqlite3.connect(_path, check_same_thread=False, isolation_level=None,
                               timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError:
            pass
        _conn = conn
        _migrate()
        try:
            os.chmod(_path, 0o600)
        except OSError:
            pass
        return conn


def conn() -> sqlite3.Connection:
    if _conn is None:
        open_db()
    return _conn  # type: ignore[return-value]


def path() -> str | None:
    return _path


def _migrate() -> None:
    c = _conn
    c.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = c.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    current = row["v"] or 0
    for version, sql in MIGRATIONS:
        if version <= current:
            continue
        log.info("db: applying migration %d", version)
        c.execute("BEGIN IMMEDIATE")
        try:
            for stmt in _split(sql):
                c.execute(stmt)
            c.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise


def _split(sql: str) -> list[str]:
    return [s.strip() for s in sql.split(";") if s.strip()]


def schema_version() -> int:
    row = one("SELECT MAX(version) AS v FROM schema_version")
    return int(row["v"] or 0) if row else 0


@contextlib.contextmanager
def tx():
    """Serialised write transaction."""
    with _lock:
        c = conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise


def execute(sql: str, args: tuple | list = ()) -> sqlite3.Cursor:
    with _lock:
        return conn().execute(sql, args)


def query(sql: str, args: tuple | list = ()) -> list[dict]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def one(sql: str, args: tuple | list = ()) -> dict | None:
    with _lock:
        r = conn().execute(sql, args).fetchone()
        return dict(r) if r else None


def now() -> int:
    return int(time.time())


def dumps(v) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def loads(s, default=None):
    if s is None or s == "":
        return default
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return default


def get_setting(key: str, default=None):
    row = one("SELECT value FROM settings WHERE key = ?", (key,))
    return loads(row["value"], default) if row else default


def set_setting(key: str, value) -> None:
    execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, dumps(value), now()),
    )
