"""Execution history: one row per broadcast attempt, keyed by a unique event key.

Statuses
--------
queued       accepted, waiting for the line
running      on the line now
success      played to every destination
partial      played to some destinations, not all
failed       nothing played (no answer, SIP error, no audio, no destinations …)
cancelled    stopped by an operator
interrupted  cut off by a higher-priority broadcast, or the service stopped mid-way
skipped      never started: the line stayed busy longer than the allowed delay
missed       the service was not running (or the clock jumped) at the scheduled time
"""
from __future__ import annotations

import datetime

from . import db

FINAL = ("success", "partial", "failed", "cancelled", "interrupted", "skipped", "missed")
PROBLEM = ("partial", "failed", "interrupted", "skipped", "missed")


def create(key: str, *, module: str, kind: str, title: str, priority: int, scheduled_for: str | None,
           actor: str = "", sound: str = "", targets: list | None = None, status: str = "queued",
           reason: str = "") -> int | None:
    """Insert once. Returns the new id, or None when the key already exists (duplicate)."""
    cur = db.execute(
        "INSERT OR IGNORE INTO executions (event_key, module, kind, title, priority, scheduled_for, created_at,"
        " status, reason, actor, sound, targets) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (key, module, kind, title[:200], priority, scheduled_for, db.now(), status, reason[:500], actor,
         sound, db.dumps(targets or [])))
    return cur.lastrowid if cur.rowcount else None


def exists(key: str) -> bool:
    return db.one("SELECT 1 AS x FROM executions WHERE event_key = ?", (key,)) is not None


def update(exec_id: int, **fields) -> None:
    allowed = {"status", "reason", "started_at", "finished_at", "results", "codec", "delay_seconds", "targets",
               "sound"}
    sets, args = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k in ("results", "targets") and not isinstance(v, str):
            v = db.dumps(v)
        if k == "reason" and v:
            v = str(v)[:500]
        sets.append(f"{k} = ?")
        args.append(v)
    if sets:
        db.execute(f"UPDATE executions SET {', '.join(sets)} WHERE id = ?", [*args, exec_id])


def get(exec_id: int) -> dict | None:
    r = db.one("SELECT * FROM executions WHERE id = ?", (exec_id,))
    return _row(r) if r else None


def _row(r: dict) -> dict:
    r = dict(r)
    r["targets"] = db.loads(r.get("targets"), [])
    r["results"] = db.loads(r.get("results"), [])
    return r


def search(*, modules: list[str], status: str = "", date_from: str = "", date_to: str = "",
           limit: int = 100, offset: int = 0, problems_only: bool = False) -> dict:
    if not modules:
        return {"total": 0, "items": []}
    where = [f"module IN ({','.join('?' * len(modules))})"]
    args: list = list(modules)
    if status:
        where.append("status = ?")
        args.append(status)
    if problems_only:
        where.append(f"status IN ({','.join('?' * len(PROBLEM))})")
        args.extend(PROBLEM)
    if date_from:
        where.append("COALESCE(scheduled_for, datetime(created_at, 'unixepoch')) >= ?")
        args.append(date_from)
    if date_to:
        where.append("COALESCE(scheduled_for, datetime(created_at, 'unixepoch')) < ?")
        args.append(date_to)
    clause = " AND ".join(where)
    total = db.one(f"SELECT COUNT(*) AS n FROM executions WHERE {clause}", args)["n"]
    rows = db.query(f"SELECT * FROM executions WHERE {clause} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                    [*args, max(1, min(limit, 500)), max(0, offset)])
    return {"total": total, "items": [_row(r) for r in rows]}


def by_keys(keys: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(keys), 400):
        chunk = keys[i:i + 400]
        if not chunk:
            continue
        for r in db.query(f"SELECT * FROM executions WHERE event_key IN ({','.join('?' * len(chunk))})", chunk):
            out[r["event_key"]] = _row(r)
    return out


def recent_problems(modules: list[str], hours: int = 48, limit: int = 20) -> list[dict]:
    if not modules:
        return []
    since = db.now() - hours * 3600
    rows = db.query(
        f"SELECT * FROM executions WHERE module IN ({','.join('?' * len(modules))}) AND created_at >= ? AND "
        f"status IN ({','.join('?' * len(PROBLEM))}) ORDER BY created_at DESC LIMIT ?",
        [*modules, since, *PROBLEM, limit])
    return [_row(r) for r in rows]


def counts_today(modules: list[str], day: datetime.date) -> dict:
    if not modules:
        return {}
    rows = db.query(
        f"SELECT status, COUNT(*) AS n FROM executions WHERE module IN ({','.join('?' * len(modules))}) "
        "AND scheduled_for LIKE ? GROUP BY status", [*modules, f"{day.isoformat()}%"])
    return {r["status"]: r["n"] for r in rows}


def mark_unfinished_interrupted() -> int:
    cur = db.execute(
        "UPDATE executions SET status = 'interrupted', finished_at = ?, reason = "
        "'The service stopped while this broadcast was in progress. It was not replayed.' "
        "WHERE status IN ('queued', 'running')", (db.now(),))
    return cur.rowcount or 0


def prune(days: int = 400) -> int:
    cur = db.execute("DELETE FROM executions WHERE created_at < ?", (db.now() - days * 86400,))
    return cur.rowcount or 0
