"""Dashboard, health, SIP, destinations & zones, time, policy, logs, broadcast control."""
from __future__ import annotations

import datetime
import os
import shutil

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import ValidationError

from .. import __version__, audit, clock, config, db, dispatcher, executions, logbuf, ntp, scheduler, sip
from ..deps import Authz, Principal, bad_request

router = APIRouter(prefix="/api", tags=["system"])

ANY_MODULE = Authz(any_of=["adhan.view", "bells.view", "system.sip.manage", "system.settings.manage",
                           "system.destinations.manage", "system.users.manage"])


def _validation_message(e: Exception) -> str:
    if isinstance(e, ValidationError):
        err = e.errors()[0]
        loc = ".".join(str(x) for x in err.get("loc", ()) if not isinstance(x, int))
        msg = err.get("msg", str(e)).replace("Value error, ", "")
        return f"{loc}: {msg}" if loc else msg
    return str(e)


def save_section(request: Request, section: str, data, action: str, *, secret_fields=()) -> config.AppConfig:
    before = config.get().model_dump()[section]
    try:
        cfg = config.update_section(section, data)
    except (ValidationError, ValueError) as e:
        audit.from_request(request, action, result="error", details={"error": _validation_message(e)})
        raise bad_request(_validation_message(e))
    after = cfg.model_dump()[section]
    if isinstance(before, dict):
        changed = sorted(k for k in after if after.get(k) != before.get(k))
        details = {"changed": [c if c not in secret_fields else f"{c} (secret)" for c in changed]}
    else:
        details = {"count_before": len(before), "count_after": len(after)}
    audit.from_request(request, action, details=details)
    return cfg


# --------------------------------------------------------------------------- #
# Public
# --------------------------------------------------------------------------- #
@router.get("/version")
def version():
    return {"version": __version__}


@router.get("/health")
def health():
    """Liveness for Docker/monitoring. Public and minimal: no configuration details."""
    ok = True
    try:
        db.one("SELECT 1 AS x")
    except Exception:  # noqa: BLE001
        ok = False
    sched = scheduler.get().status()
    body = {"ok": ok and sched["running"], "db": ok, "scheduler": sched["running"], "version": __version__}
    if not body["ok"]:
        raise HTTPException(503, detail=body)
    return body


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
@router.get("/dashboard", dependencies=[Depends(ANY_MODULE)])
def dashboard(request: Request):
    p: Principal = request.state.principal
    modules = [m for m in ("adhan", "bells") if p.has(f"{m}.view")]
    history_modules = [m for m in ("adhan", "bells") if p.has(f"{m}.history.view")]
    now = clock.now()
    eng = sip.get_engine()
    sipst = eng.status()
    ntpst = ntp.status()
    sched = scheduler.get().status()
    disk = shutil.disk_usage(config.AUDIO_DIR if os.path.isdir(config.AUDIO_DIR) else ".")
    snap = dispatcher.get().snapshot()
    active = snap["active"] if snap["active"] and snap["active"]["module"] in modules else None
    health = {
        "sip": {"ok": sipst["registered"], "state": "simulated" if sipst["simulated"] else
                ("registered" if sipst["registered"] else ("disabled" if not sipst["enabled"] else
                                                            ("unavailable" if not sipst["available"] else "error"))),
                "message": sipst["message"] if sipst["available"] else sipst["detail"],
                "identity": sipst["identity"], "transport": sipst["transport"],
                "last_registered_at": sipst["last_registered_at"], "next_retry_in": sipst["next_retry_in"],
                "failures": sipst["consecutive_failures"]},
        "time": {"ok": (not ntpst["enabled"]) or ntpst["synced"], "enabled": ntpst["enabled"],
                 "offset_ms": ntpst["offset_ms"], "server": ntpst["server"], "error": ntpst["error"],
                 "last_sync": ntpst["last_sync"]},
        "scheduler": {"ok": sched["healthy"], "running": sched["running"]},
        "storage": {"ok": disk.free > 200 * 1024 * 1024, "free_mb": disk.free // (1024 * 1024)},
    }
    upcoming = scheduler.get().upcoming(tuple(modules), limit=8)
    return {
        "now": now.isoformat(), "timezone": config.timezone(), "version": __version__,
        "modules": modules, "health": health, "active": active,
        "queue": [q for q in snap["queue"] if q["module"] in modules],
        "upcoming": upcoming,
        "recent_problems": executions.recent_problems(history_modules, hours=48, limit=8),
        "today_counts": executions.counts_today(history_modules, now.date()),
        "can": {
            "stop": sorted(p.modules_with("broadcast.stop")),
            "broadcast": sorted(p.modules_with("broadcast")),
            "sip": p.has("system.sip.manage"),
        },
    }


# --------------------------------------------------------------------------- #
# Broadcast control
# --------------------------------------------------------------------------- #
@router.get("/broadcast", dependencies=[Depends(Authz(any_of=["adhan.view", "bells.view"]))])
def broadcast_state(request: Request):
    p: Principal = request.state.principal
    snap = dispatcher.get().snapshot()
    visible = {m for m in ("adhan", "bells") if p.has(f"{m}.view")}
    active = snap["active"] if snap["active"] and snap["active"]["module"] in visible else None
    busy_other = bool(snap["active"]) and active is None
    return {"active": active, "line_busy": bool(snap["active"]), "busy_other_module": busy_other,
            "queue": [q for q in snap["queue"] if q["module"] in visible]}


@router.post("/broadcast/stop", dependencies=[Depends(Authz(any_of=["adhan.broadcast.stop",
                                                                    "bells.broadcast.stop"]))])
def broadcast_stop(request: Request):
    p: Principal = request.state.principal
    try:
        return dispatcher.get().stop_active(p.username, p.modules_with("broadcast.stop"))
    except PermissionError as e:
        audit.from_request(request, "broadcast.stop", result="denied", details={"module": str(e)})
        raise HTTPException(403, detail={"code": "forbidden",
                                         "message": "You are not allowed to stop what is playing now."})


# --------------------------------------------------------------------------- #
# SIP
# --------------------------------------------------------------------------- #
@router.get("/system/sip", dependencies=[Depends(Authz("system.sip.manage"))])
def get_sip():
    cfg = config.get()
    eng = sip.get_engine()
    return {"sip": config.public_sip(cfg), "call": cfg.call.model_dump(), "codecs": cfg.codecs.model_dump(),
            "status": eng.status(), "available_codecs": eng.backend.codecs()}


@router.put("/system/sip", dependencies=[Depends(Authz("system.sip.manage"))])
def put_sip(body: dict, request: Request):
    data = dict(body or {})
    data.pop("has_password", None)
    if data.pop("clear_password", False):
        data["password"] = ""
    elif not data.get("password"):
        data.pop("password", None)          # blank = keep the stored password
    cfg = save_section(request, "sip", data, "sip.update", secret_fields=("password",))
    sip.get_engine().restart()
    scheduler.get().mark_dirty()
    return {"sip": config.public_sip(cfg), "status": sip.get_engine().status()}


@router.put("/system/call", dependencies=[Depends(Authz("system.sip.manage"))])
def put_call(body: dict, request: Request):
    cfg = save_section(request, "call", body or {}, "sip.call_update")
    return {"call": cfg.call.model_dump()}


@router.put("/system/codecs", dependencies=[Depends(Authz("system.sip.manage"))])
def put_codecs(body: dict, request: Request):
    pri = (body or {}).get("priorities")
    if not isinstance(pri, dict) or not pri:
        raise bad_request("Send codec priorities.")
    if not any(int(v) > 0 for v in pri.values()):
        raise bad_request("Enable at least one codec.")
    cfg = save_section(request, "codecs", {"priorities": pri}, "sip.codecs_update")
    sip.get_engine().restart()
    return {"codecs": cfg.codecs.model_dump(), "status": sip.get_engine().status()}


@router.post("/system/sip/reconnect", dependencies=[Depends(Authz("system.sip.manage"))])
def sip_reconnect(request: Request):
    sip.get_engine().restart()
    audit.from_request(request, "sip.reconnect")
    return {"status": sip.get_engine().status()}


@router.get("/system/sip/status", dependencies=[Depends(ANY_MODULE)])
def sip_status():
    st = sip.get_engine().status()
    return {k: st[k] for k in ("available", "simulated", "enabled", "running", "registered", "message",
                               "last_registered_at", "next_retry_in", "consecutive_failures", "transport")}


# --------------------------------------------------------------------------- #
# Destinations & zones
# --------------------------------------------------------------------------- #
@router.get("/system/destinations", dependencies=[Depends(Authz(any_of=[
    "system.destinations.manage", "adhan.schedule.edit", "bells.schedule.edit", "adhan.view", "bells.view"]))])
def get_destinations():
    cfg = config.get()
    return {"destinations": [d.model_dump() for d in cfg.destinations], "zones": [z.model_dump() for z in cfg.zones],
            "call_mode": cfg.call.mode}


@router.put("/system/destinations", dependencies=[Depends(Authz("system.destinations.manage"))])
def put_destinations(body: dict, request: Request):
    body = body or {}
    dests = body.get("destinations")
    zones = body.get("zones")
    if not isinstance(dests, list) or not isinstance(zones, list):
        raise bad_request("Send destinations and zones.")
    cfg = config.get()
    current = cfg.model_dump()
    current["destinations"] = dests
    current["zones"] = zones
    try:
        new = config.AppConfig(**current)
    except (ValidationError, ValueError) as e:
        raise bad_request(_validation_message(e))
    removed_zones = {z.id for z in cfg.zones} - {z.id for z in new.zones}
    if removed_zones:
        from .. import bells
        in_use = [s["name"] for s in bells.list_schedules_raw() if set(s["zone_ids"]) & removed_zones]
        if set(cfg.adhan.zone_ids) & removed_zones:
            raise bad_request("A zone you removed is where the Adhan plays. Choose other zones for the Adhan "
                              "first (Adhan › Prayer schedule).", "zone_in_use")
        if in_use:
            raise bad_request("A zone you removed is used by these bell schedules: " + ", ".join(in_use)
                              + ". Change them first.", "zone_in_use")
    config.save(new)
    audit.from_request(request, "destinations.update", details={
        "destinations": [f"{d.name} ({d.uri}){'' if d.enabled else ' off'}" for d in new.destinations],
        "zones": [f"{z.name}: {len(z.destination_ids)}" for z in new.zones]})
    scheduler.get().mark_dirty()
    return get_destinations()


# --------------------------------------------------------------------------- #
# Time, NTP and policy
# --------------------------------------------------------------------------- #
@router.get("/system/time", dependencies=[Depends(Authz("system.settings.manage"))])
def get_time():
    cfg = config.get()
    return {"timezone": cfg.location.timezone, "now": clock.now().isoformat(), "ntp": cfg.ntp.model_dump(),
            "ntp_status": ntp.status(), "policy": cfg.policy.model_dump()}


@router.put("/system/time", dependencies=[Depends(Authz("system.settings.manage"))])
def put_time(body: dict, request: Request):
    body = body or {}
    if "timezone" in body and body["timezone"] != config.get().location.timezone:
        save_section(request, "location", {"timezone": body["timezone"]}, "settings.timezone_update")
        scheduler.get().mark_dirty()
    if isinstance(body.get("ntp"), dict):
        save_section(request, "ntp", body["ntp"], "settings.ntp_update")
    if isinstance(body.get("policy"), dict):
        save_section(request, "policy", body["policy"], "settings.policy_update")
    return get_time()


@router.post("/system/ntp/sync", dependencies=[Depends(Authz("system.settings.manage"))])
def ntp_sync(request: Request):
    if not config.get().ntp.enabled:
        raise bad_request("Time sync is turned off. Turn it on and save first.", "ntp_disabled")
    st = ntp.sync_once()
    audit.from_request(request, "settings.ntp_sync", result="ok" if not st.get("error") else "error")
    scheduler.get().mark_dirty()
    return st


@router.get("/system/timezones", dependencies=[Depends(Authz(any_of=["system.settings.manage",
                                                                     "adhan.schedule.edit"]))])
def timezones():
    import zoneinfo
    return {"timezones": sorted(z for z in zoneinfo.available_timezones() if "/" in z and not z.startswith("Etc/"))}


# --------------------------------------------------------------------------- #
# Logs
# --------------------------------------------------------------------------- #
@router.get("/system/logs", dependencies=[Depends(Authz("system.logs.view"))])
def logs(limit: int = 500):
    return {"lines": logbuf.recent(max(1, min(limit, 2000)))}


@router.get("/system/logs/export", dependencies=[Depends(Authz("system.logs.view"))])
def export_logs(request: Request):
    audit.from_request(request, "logs.export")
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return PlainTextResponse(logbuf.export_text(),
                             headers={"Content-Disposition": f'attachment; filename="adhan-bell-pager-{ts}.log"'})
