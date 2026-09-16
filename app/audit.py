"""Audit trail of administrative and operational actions.

Every entry records who did what, to which target, when, and whether it worked.
Details are passed through ``security.redact`` so secrets never reach the table
or the log file.
"""
from __future__ import annotations

import logging

from . import db, security

log = logging.getLogger("audit")


def record(action: str, *, actor: str = "system", target: str = "", result: str = "ok",
           ip: str = "", details: dict | None = None) -> None:
    safe = security.redact(details or {})
    try:
        db.execute(
            "INSERT INTO audit_log (ts, actor, ip, action, target, result, details) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (db.now(), actor or "system", ip or "", action, str(target or "")[:200],
             result, db.dumps(safe)),
        )
    except Exception:  # noqa: BLE001
        log.exception("audit: could not record %s", action)
    log.info("AUDIT actor=%s action=%s target=%s result=%s", actor, action, target, result)


def from_request(request, action: str, *, target: str = "", result: str = "ok",
                 details: dict | None = None) -> None:
    principal = getattr(request.state, "principal", None)
    actor = principal.username if principal else "anonymous"
    ip = request.client.host if request.client else ""
    record(action, actor=actor, target=target, result=result, ip=ip, details=details)


def search(*, limit: int = 200, offset: int = 0, action: str = "", actor: str = "",
           result: str = "", since: int | None = None) -> dict:
    where, args = [], []
    if action:
        where.append("action LIKE ?")
        args.append(f"{action}%")
    if actor:
        where.append("actor LIKE ?")
        args.append(f"%{actor}%")
    if result:
        where.append("result = ?")
        args.append(result)
    if since:
        where.append("ts >= ?")
        args.append(since)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = db.one(f"SELECT COUNT(*) AS n FROM audit_log {clause}", args)["n"]
    rows = db.query(
        f"SELECT * FROM audit_log {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
        [*args, max(1, min(limit, 1000)), max(0, offset)],
    )
    for r in rows:
        r["details"] = db.loads(r["details"], {})
    return {"total": total, "items": rows}
