"""Adhan module: prayer times, schedule settings, location, sounds, tests, history."""
from __future__ import annotations

import datetime
import os
import tempfile

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from .. import audio, audit, clock, config, dispatcher, executions, planner, prayer, scheduler, timetable
from ..deps import Authz, Principal, bad_request, not_found
from .system import save_section

router = APIRouter(prefix="/api/adhan", tags=["adhan"])
geo_router = APIRouter(prefix="/api/geo", tags=["adhan"])

METHODS = ["MUSLIM_WORLD_LEAGUE", "EGYPTIAN", "KARACHI", "UMM_AL_QURA", "DUBAI", "MOON_SIGHTING_COMMITTEE",
           "NORTH_AMERICA", "KUWAIT", "QATAR", "SINGAPORE", "UOIF"]


@router.get("/overview", dependencies=[Depends(Authz("adhan.view"))])
def overview(request: Request, date: str | None = None):
    p: Principal = request.state.principal
    try:
        day = datetime.date.fromisoformat(date) if date else clock.today()
    except ValueError:
        raise bad_request("date must be YYYY-MM-DD")
    cfg = config.get()
    times = prayer.times_for_date(day)
    occ = planner.adhan_occurrences(day)
    bell_occ = planner.day_occurrences(day, modules=("bells",)) if p.has("bells.view") else []
    annotated = planner.annotate_conflicts(occ + bell_occ)
    by_key = executions.by_keys([o["key"] for o in occ]) if p.has("adhan.history.view") else {}
    rows = []
    for o in annotated:
        if o["module"] != "adhan":
            continue
        ex = by_key.get(o["key"])
        o["execution"] = {"status": ex["status"], "reason": ex["reason"], "id": ex["id"]} if ex else None
        rows.append(o)
    return {
        "date": day.isoformat(), "now": clock.now().isoformat(), "timezone": cfg.location.timezone,
        "times": {k: (v.isoformat() if v is not None else None) for k, v in times.items()},
        "source": cfg.prayer.source, "occurrences": rows,
        "settings": {"prayer": cfg.prayer.model_dump(), "audio": cfg.audio.model_dump(),
                     "location": cfg.location.model_dump(), "zone_ids": cfg.adhan.zone_ids},
        "manual_timetable": timetable.status(),
    }


@router.get("/settings", dependencies=[Depends(Authz("adhan.view"))])
def get_settings():
    cfg = config.get()
    return {"prayer": cfg.prayer.model_dump(), "location": cfg.location.model_dump(), "audio": cfg.audio.model_dump(),
            "zone_ids": cfg.adhan.zone_ids, "methods": METHODS, "manual_timetable": timetable.status()}


@router.put("/settings", dependencies=[Depends(Authz("adhan.schedule.edit"))])
def put_settings(body: dict, request: Request):
    body = body or {}
    if isinstance(body.get("prayer"), dict):
        data = dict(body["prayer"])
        if data.get("method") and data["method"] not in METHODS:
            raise bad_request("Unknown calculation method.")
        save_section(request, "prayer", data, "adhan.schedule_update")
    if "zone_ids" in body:
        save_section(request, "adhan", {"zone_ids": body["zone_ids"] or []}, "adhan.zones_update")
    scheduler.get().mark_dirty()
    return get_settings()


@router.put("/location", dependencies=[Depends(Authz("adhan.schedule.edit"))])
def put_location(body: dict, request: Request):
    p: Principal = request.state.principal
    data = dict(body or {})
    from .. import geo
    if data.get("use_manual") is False:
        rec = geo.city_record(data.get("country_code", ""), data.get("city", ""),
                              city_id=data.get("city_id", ""), region=data.get("region", ""))
        if not (geo.country_exists(data.get("country_code", "")) and rec):
            data["use_manual"] = True
        else:
            data.update({"city_id": rec["id"], "latitude": rec["lat"], "longitude": rec["lng"],
                         "timezone": rec["tz"], "city": rec["name"], "region": rec["region"]})
            if rec["local_name"]:
                data["city_local_name"] = rec["local_name"]
    new_tz = data.get("timezone")
    if new_tz and new_tz != config.get().location.timezone and not p.has("system.settings.manage"):
        raise HTTPException(403, detail={
            "code": "timezone_permission",
            "message": "This location uses a different timezone. Changing the system timezone affects both "
                       "modules and requires the System settings permission.", "timezone": new_tz})
    save_section(request, "location", data, "adhan.location_update")
    scheduler.get().mark_dirty()
    return {"location": config.get().location.model_dump()}


@router.get("/prayer-times", dependencies=[Depends(Authz("adhan.view"))])
def prayer_times(date: str | None = None, days: int = 1):
    try:
        start = datetime.date.fromisoformat(date) if date else clock.today()
    except ValueError:
        raise bad_request("date must be YYYY-MM-DD")
    out = []
    for i in range(max(1, min(days, 31))):
        d = start + datetime.timedelta(days=i)
        t = prayer.times_for_date(d)
        out.append({"date": d.isoformat(), "times": {k: (v.isoformat() if v else None) for k, v in t.items()}})
    return {"days": out}


# --------------------------------------------------------------------------- #
# Manual timetable
# --------------------------------------------------------------------------- #
@router.post("/manual-timetable", dependencies=[Depends(Authz("adhan.schedule.edit"))])
async def manual_import(request: Request, file: UploadFile = File(...)):
    data = await file.read(5 * 1024 * 1024 + 1)
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(413, detail={"code": "too_large", "message": "The file is too large (max 5 MB)."})
    try:
        summary = timetable.import_text(data.decode("utf-8-sig", errors="replace"))
    except ValueError as e:
        raise bad_request(str(e), "timetable_invalid")
    config.update_section("prayer", {"source": "manual"})
    audit.from_request(request, "adhan.timetable_import", target=file.filename or "", details={"days": summary["days"]})
    scheduler.get().mark_dirty()
    return summary


# --------------------------------------------------------------------------- #
# Sounds
# --------------------------------------------------------------------------- #
async def _save_upload(file: UploadFile, library: str, replace: bool) -> dict:
    ext = os.path.splitext(file.filename or "")[1].lower()
    fd, tmp = tempfile.mkstemp(prefix=".upload-", suffix=ext or ".bin")
    size = 0
    try:
        with os.fdopen(fd, "wb") as fh:
            while True:
                chunk = await file.read(1024 * 256)
                if not chunk:
                    break
                size += len(chunk)
                if size > audio.MAX_UPLOAD_BYTES:
                    raise HTTPException(413, detail={"code": "too_large", "message": "The file is too large (max 50 MB)."})
                fh.write(chunk)
        try:
            return audio.save_upload(file.filename or "", tmp, library, replace=replace)
        except FileExistsError as e:
            raise HTTPException(409, detail={"code": "exists", "message": f"'{e}' already exists. Replace it?",
                                             "name": str(e)})
        except ValueError as e:
            raise bad_request(str(e), "audio_invalid")
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


@router.get("/sounds", dependencies=[Depends(Authz("adhan.view"))])
def list_sounds():
    files = audio.list_files("adhan")
    for f in files:
        f["used_by"] = audio.adhan_file_usage(f["name"])
    return {"files": files, "assignments": config.get().audio.model_dump(), "ffmpeg": audio.have_ffmpeg()}


@router.post("/sounds", dependencies=[Depends(Authz("adhan.sounds.manage"))])
async def upload_sound(request: Request, file: UploadFile = File(...), replace: bool = False):
    res = await _save_upload(file, "adhan", replace)
    audit.from_request(request, "adhan.sound_upload", target=res["name"], details={"replace": replace})
    return res


@router.delete("/sounds/{name}", dependencies=[Depends(Authz("adhan.sounds.manage"))])
def delete_sound(name: str, request: Request):
    used = audio.adhan_file_usage(name)
    if used:
        raise bad_request(f"'{name}' is assigned to: {', '.join(used)}. Choose another file there first.", "in_use")
    if not audio.delete_file(name, "adhan"):
        raise not_found("File not found.")
    audit.from_request(request, "adhan.sound_delete", target=name)
    return {"ok": True}


@router.get("/sounds/{name}/file", dependencies=[Depends(Authz("adhan.view"))])
def sound_file(name: str):
    path = audio.path_for(name, "adhan")
    if not path:
        raise not_found("File not found.")
    return FileResponse(path, media_type="audio/wav")


@router.put("/sounds/assignments", dependencies=[Depends(Authz("adhan.sounds.manage"))])
def put_assignments(body: dict, request: Request):
    data = dict(body or {})
    for key in ("default_file", "iqama_file"):
        if data.get(key) and not audio.path_for(data[key], "adhan"):
            raise bad_request(f"'{data[key]}' is not in the Adhan library.")
    for key in ("per_prayer", "iqama_per_prayer"):
        for v in (data.get(key) or {}).values():
            if v and not audio.path_for(v, "adhan"):
                raise bad_request(f"'{v}' is not in the Adhan library.")
    cfg = save_section(request, "audio", data, "adhan.sound_assignments_update")
    scheduler.get().mark_dirty()
    return {"assignments": cfg.audio.model_dump()}


# --------------------------------------------------------------------------- #
# Broadcast
# --------------------------------------------------------------------------- #
def submit_manual(request: Request, b: dispatcher.Broadcast, force: bool, audit_action: str) -> dict:
    p: Principal = request.state.principal
    if force:
        active_mod = dispatcher.get().active_module()
        if active_mod and not p.has(f"{active_mod}.broadcast.stop"):
            raise HTTPException(403, detail={"code": "cannot_interrupt",
                                             "message": "Something is playing that you are not allowed to stop."})
    try:
        res = dispatcher.get().submit(b, force=force)
    except dispatcher.Conflict as c:
        can_stop = p.has(f"{c.active['module']}.broadcast.stop")
        raise HTTPException(409, detail={"code": "line_busy", "message": f"'{c.active['title']}' is playing now.",
                                         "active": c.active, "can_interrupt": can_stop})
    audit.from_request(request, audit_action, target=b.title, details={"forced": force, "key": b.key})
    return res


@router.post("/test", dependencies=[Depends(Authz("adhan.broadcast"))])
def test_page(body: dict, request: Request):
    p: Principal = request.state.principal
    body = body or {}
    name = body.get("prayer", "dhuhr")
    kind = body.get("kind", "adhan")
    if name not in config.PRAYERS or kind not in ("adhan", "iqama"):
        raise bad_request("Unknown prayer.")
    path = audio.resolve_for_prayer(name) if kind == "adhan" else (
        audio.resolve_iqama_for_prayer(name) or audio.resolve_for_prayer(name))
    if not path:
        raise bad_request("No audio file is assigned for this prayer.", "no_audio")
    label = f"Test: {planner.PRAYER_LABELS[name]} {'Adhan' if kind == 'adhan' else 'iqama'}"
    b = dispatcher.Broadcast(key=dispatcher.manual_key(), module="adhan", kind="test", title=label,
                             priority=planner.PRIORITY["manual"], manual=True, sound_path=path,
                             sound_name=os.path.basename(path), zone_ids=config.get().adhan.zone_ids,
                             scheduled_for=clock.now(), actor=p.username)
    return submit_manual(request, b, bool(body.get("force")), "adhan.test")


@router.post("/play-file", dependencies=[Depends(Authz("adhan.broadcast"))])
def play_file(body: dict, request: Request):
    p: Principal = request.state.principal
    body = body or {}
    path = audio.path_for(body.get("file", ""), "adhan")
    if not path:
        raise bad_request("File not found.", "no_audio")
    b = dispatcher.Broadcast(key=dispatcher.manual_key(), module="adhan", kind="test",
                             title=f"Play: {os.path.basename(path)}", priority=planner.PRIORITY["manual"],
                             manual=True, sound_path=path, sound_name=os.path.basename(path),
                             zone_ids=config.get().adhan.zone_ids, scheduled_for=clock.now(), actor=p.username)
    return submit_manual(request, b, bool(body.get("force")), "adhan.play_file")


@router.get("/history", dependencies=[Depends(Authz("adhan.history.view"))])
def history(status: str = "", date_from: str = "", date_to: str = "", limit: int = 100, offset: int = 0,
            problems: bool = False):
    return executions.search(modules=["adhan"], status=status, date_from=date_from, date_to=date_to, limit=limit,
                             offset=offset, problems_only=problems)


# --------------------------------------------------------------------------- #
# Offline geo (city picker)
# --------------------------------------------------------------------------- #
@geo_router.get("/countries", dependencies=[Depends(Authz("adhan.view"))])
def geo_countries():
    from .. import geo
    return {"countries": geo.countries()}


@geo_router.get("/cities", dependencies=[Depends(Authz("adhan.view"))])
def geo_cities(country: str, q: str = "", limit: int = 50):
    from .. import geo
    return {"cities": geo.cities(country, q[:60], min(max(limit, 1), 100)),
            "recommended_method": geo.recommended_method(country)}
