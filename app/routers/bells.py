"""School Bells module API."""
from __future__ import annotations

import datetime
import os

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse

from .. import audio, audit, bells, clock, config, dispatcher, executions, planner, scheduler
from ..deps import Authz, Principal, bad_request, not_found
from .adhan import _save_upload, submit_manual

router = APIRouter(prefix="/api/bells", tags=["bells"])


def _day(date: str | None) -> datetime.date:
    try:
        return datetime.date.fromisoformat(date) if date else clock.today()
    except ValueError:
        raise bad_request("date must be YYYY-MM-DD")


def _changed(request: Request, action: str, target: str, details: dict | None = None) -> None:
    audit.from_request(request, action, target=target, details=details)
    scheduler.get().mark_dirty()


# --------------------------------------------------------------------------- #
# Today / day plans
# --------------------------------------------------------------------------- #
@router.get("/day", dependencies=[Depends(Authz("bells.view"))])
def day_plan(request: Request, date: str | None = None):
    p: Principal = request.state.principal
    day = _day(date)
    plan = bells.plan_for(day)
    adhan = planner.adhan_occurrences(day)
    annotated = planner.annotate_conflicts(plan["occurrences"] + adhan)
    rows = [o for o in annotated if o["module"] == "bells"]
    by_key = executions.by_keys([o["key"] for o in rows]) if p.has("bells.history.view") else {}
    now = clock.now()
    for o in rows:
        ex = by_key.get(o["key"])
        o["execution"] = ({"status": ex["status"], "reason": ex["reason"], "id": ex["id"],
                           "delay_seconds": ex["delay_seconds"]} if ex else None)
        o["past"] = datetime.datetime.fromisoformat(o["at"]) <= now
    plan["occurrences"] = rows
    plan["adhan"] = [{"title": a["title"], "at": a["at"], "time": a["time"]} for a in adhan] if p.has("adhan.view") \
        else []
    plan["now"] = now.isoformat()
    plan["timezone"] = config.timezone()
    nxt = next((o for o in rows if not o["past"]), None)
    plan["next"] = nxt
    return plan


@router.get("/calendar", dependencies=[Depends(Authz("bells.view"))])
def calendar(start: str | None = None, days: int = 14):
    first = _day(start)
    out = []
    for plan in bells.plans_between(first, days):
        out.append({"date": plan["date"], "weekday": plan["weekday"], "status": plan["status"],
                    "schedule": plan["schedule"], "timetable": plan["timetable"], "exception": plan["exception"],
                    "bell_count": len(plan["occurrences"]),
                    "first": plan["occurrences"][0]["time"] if plan["occurrences"] else None,
                    "last": plan["occurrences"][-1]["time"] if plan["occurrences"] else None,
                    "overlapping": plan["overlapping"], "warnings": len(plan["warnings"])})
    return {"days": out}


# --------------------------------------------------------------------------- #
# Timetables
# --------------------------------------------------------------------------- #
@router.get("/timetables", dependencies=[Depends(Authz("bells.view"))])
def list_timetables():
    return {"timetables": bells.list_timetables()}


@router.get("/timetables/{tt_id}", dependencies=[Depends(Authz("bells.view"))])
def get_timetable(tt_id: int):
    t = bells.get_timetable(tt_id)
    if not t:
        raise not_found("Timetable not found.")
    return t


@router.post("/timetables/validate", dependencies=[Depends(Authz("bells.schedule.edit"))])
def validate_timetable(body: dict):
    try:
        events = bells._clean_events((body or {}).get("events") or [])
    except (ValueError, TypeError, AttributeError) as e:
        return {"ok": False, "error": str(e), "warnings": []}
    return {"ok": True, "warnings": bells.timetable_warnings(events)}


@router.post("/timetables", dependencies=[Depends(Authz("bells.schedule.edit"))])
def create_timetable(body: dict, request: Request):
    try:
        t = bells.save_timetable(body or {})
    except ValueError as e:
        raise bad_request(str(e))
    _changed(request, "bells.timetable_create", t["name"], {"rows": len(t["events"])})
    return t


@router.put("/timetables/{tt_id}", dependencies=[Depends(Authz("bells.schedule.edit"))])
def update_timetable(tt_id: int, body: dict, request: Request):
    before = bells.get_timetable(tt_id)
    if not before:
        raise not_found("Timetable not found.")
    try:
        t = bells.save_timetable(body or {}, tt_id)
    except ValueError as e:
        raise bad_request(str(e))
    _changed(request, "bells.timetable_update", t["name"], {
        "rows_before": len(before["events"]), "rows_after": len(t["events"]),
        "times_before": [e["start_time"] for e in before["events"]], "times_after": [e["start_time"] for e in t["events"]]})
    return t


@router.post("/timetables/{tt_id}/duplicate", dependencies=[Depends(Authz("bells.schedule.edit"))])
def duplicate_timetable(tt_id: int, request: Request, body: dict | None = None):
    try:
        t = bells.duplicate_timetable(tt_id, (body or {}).get("name"))
    except LookupError:
        raise not_found("Timetable not found.")
    except ValueError as e:
        raise bad_request(str(e))
    _changed(request, "bells.timetable_duplicate", t["name"], {"source_id": tt_id})
    return t


@router.delete("/timetables/{tt_id}", dependencies=[Depends(Authz("bells.schedule.edit"))])
def delete_timetable(tt_id: int, request: Request):
    try:
        t = bells.delete_timetable(tt_id)
    except ValueError as e:
        raise bad_request(str(e), "in_use")
    if not t:
        raise not_found("Timetable not found.")
    _changed(request, "bells.timetable_delete", t["name"])
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Schedules
# --------------------------------------------------------------------------- #
@router.get("/schedules", dependencies=[Depends(Authz("bells.view"))])
def list_schedules():
    return {"schedules": bells.list_schedules()}


@router.post("/schedules", dependencies=[Depends(Authz("bells.schedule.edit"))])
def create_schedule(body: dict, request: Request):
    try:
        s = bells.save_schedule(body or {})
    except ValueError as e:
        raise bad_request(str(e))
    _changed(request, "bells.schedule_create", s["name"], _schedule_audit(s))
    return s


@router.put("/schedules/{sid}", dependencies=[Depends(Authz("bells.schedule.edit"))])
def update_schedule(sid: int, body: dict, request: Request):
    if not bells.get_schedule(sid):
        raise not_found("Schedule not found.")
    try:
        s = bells.save_schedule(body or {}, sid)
    except ValueError as e:
        raise bad_request(str(e))
    _changed(request, "bells.schedule_update", s["name"], _schedule_audit(s))
    return s


@router.delete("/schedules/{sid}", dependencies=[Depends(Authz("bells.schedule.edit"))])
def delete_schedule(sid: int, request: Request):
    s = bells.delete_schedule(sid)
    if not s:
        raise not_found("Schedule not found.")
    _changed(request, "bells.schedule_delete", s["name"])
    return {"ok": True}


def _schedule_audit(s: dict) -> dict:
    return {k: s[k] for k in ("enabled", "effective_from", "effective_to", "days", "zone_ids", "sound",
                              "playback_mode", "repeat_count", "duration_seconds")}


# --------------------------------------------------------------------------- #
# Calendar exceptions
# --------------------------------------------------------------------------- #
@router.get("/exceptions", dependencies=[Depends(Authz("bells.view"))])
def list_exceptions(upcoming: bool = False):
    since = (clock.today() - datetime.timedelta(days=1)).isoformat() if upcoming else None
    return {"exceptions": bells.list_exceptions(since)}


@router.post("/exceptions", dependencies=[Depends(Authz("bells.schedule.edit"))])
def create_exception(body: dict, request: Request):
    try:
        e = bells.save_exception(body or {})
    except ValueError as err:
        raise bad_request(str(err))
    _changed(request, "bells.exception_create", e["name"], e)
    return e


@router.put("/exceptions/{eid}", dependencies=[Depends(Authz("bells.schedule.edit"))])
def update_exception(eid: int, body: dict, request: Request):
    try:
        e = bells.save_exception(body or {}, eid)
    except LookupError:
        raise not_found("Exception not found.")
    except ValueError as err:
        raise bad_request(str(err))
    _changed(request, "bells.exception_update", e["name"], e)
    return e


@router.delete("/exceptions/{eid}", dependencies=[Depends(Authz("bells.schedule.edit"))])
def delete_exception(eid: int, request: Request):
    e = bells.delete_exception(eid)
    if not e:
        raise not_found("Exception not found.")
    _changed(request, "bells.exception_delete", e["name"])
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Sounds
# --------------------------------------------------------------------------- #
def _sound_usage_items(name: str) -> list[dict]:
    items = [{"kind": "schedule", "id": s["id"], "name": s["name"]}
             for s in bells.list_schedules_raw() if s["sound"] == name]
    from .. import db
    for r in db.query("SELECT DISTINCT t.id, t.name FROM bell_events e JOIN bell_timetables t ON t.id = e.timetable_id "
                      "WHERE e.sound_start = ? OR e.sound_end = ?", (name, name)):
        items.append({"kind": "timetable", "id": r["id"], "name": r["name"]})
    return items


def _sound_usage(name: str) -> list[str]:
    return [f"{i['kind']} '{i['name']}'" for i in _sound_usage_items(name)]


@router.get("/sounds", dependencies=[Depends(Authz("bells.view"))])
def list_sounds():
    files = audio.list_files("bells")
    for f in files:
        f["usage"] = _sound_usage_items(f["name"])
        f["used_by"] = _sound_usage(f["name"])
        f["builtin"] = f["name"] in audio.BUILTIN_BELLS
    return {"files": files, "ffmpeg": audio.have_ffmpeg()}


@router.post("/sounds", dependencies=[Depends(Authz("bells.sounds.manage"))])
async def upload_sound(request: Request, file: UploadFile = File(...), replace: bool = False):
    res = await _save_upload(file, "bells", replace)
    audit.from_request(request, "bells.sound_upload", target=res["name"], details={"replace": replace})
    return res


@router.delete("/sounds/{name}", dependencies=[Depends(Authz("bells.sounds.manage"))])
def delete_sound(name: str, request: Request):
    used = _sound_usage(name)
    if used:
        raise bad_request(f"'{name}' is used by {', '.join(used)}. Choose another sound there first.", "in_use")
    if not audio.delete_file(name, "bells"):
        raise not_found("File not found.")
    audit.from_request(request, "bells.sound_delete", target=name)
    return {"ok": True}


@router.get("/sounds/{name}/file", dependencies=[Depends(Authz("bells.view"))])
def sound_file(name: str):
    path = audio.path_for(name, "bells")
    if not path:
        raise not_found("File not found.")
    return FileResponse(path, media_type="audio/wav")


# --------------------------------------------------------------------------- #
# Ring now / test
# --------------------------------------------------------------------------- #
@router.post("/ring", dependencies=[Depends(Authz("bells.broadcast"))])
def ring_now(body: dict, request: Request):
    """Ring a bell now. ``test: true`` with ``destination_id`` plays to a single destination."""
    p: Principal = request.state.principal
    body = body or {}
    cfg = config.get()
    sound = body.get("sound") or ""
    path = audio.path_for(sound, "bells")
    if not path:
        raise bad_request("Choose a bell sound.", "no_audio")
    is_test = bool(body.get("test"))
    zone_ids = body.get("zone_ids") or []
    known_zones = {z.id for z in cfg.zones}
    if any(z not in known_zones for z in zone_ids):
        raise bad_request("One of the selected zones no longer exists.")
    destinations = None
    if body.get("destination_id"):
        d = config.destination_map(cfg).get(body["destination_id"])
        if not d:
            raise bad_request("Destination not found.")
        destinations = [d.uri]
    mode = body.get("playback_mode") or "once"
    if mode not in bells.PLAYBACK_MODES:
        raise bad_request("Unknown playback mode.")
    playback = {"mode": mode, "repeat_count": max(1, min(20, int(body.get("repeat_count") or 1))),
                "duration_seconds": max(1, min(600, int(body.get("duration_seconds") or 10)))}
    title = f"{'Test' if is_test else 'Ring now'}: {os.path.splitext(sound)[0]}"
    b = dispatcher.Broadcast(key=dispatcher.manual_key(), module="bells", kind="test" if is_test else "manual",
                             title=title, priority=planner.PRIORITY["manual"], manual=True, sound_path=path,
                             sound_name=sound, zone_ids=zone_ids, destinations=destinations, playback=playback,
                             scheduled_for=clock.now(), actor=p.username)
    return submit_manual(request, b, bool(body.get("force")), "bells.test" if is_test else "bells.ring_now")


@router.get("/history", dependencies=[Depends(Authz("bells.history.view"))])
def history(status: str = "", date_from: str = "", date_to: str = "", limit: int = 100, offset: int = 0,
            problems: bool = False):
    return executions.search(modules=["bells"], status=status, date_from=date_from, date_to=date_to, limit=limit,
                             offset=offset, problems_only=problems)
