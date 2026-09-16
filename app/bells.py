"""School bells: timetables, weekly schedules, calendar exceptions, and day plans.

Vocabulary (the same words are used in the UI and docs)
-------------------------------------------------------
* **Timetable** – a reusable day template: periods, breaks, start and end of
  day. Each row rings at its start time and, optionally, at its end time.
* **Schedule** – which timetable runs on each weekday, from an effective start
  date to an optional end date, plus where the bells ring (zones), the default
  sound and how it plays (once, repeated N times, or for N seconds).
* **Calendar exception** – for a date range: a *holiday* or *no bells* day, or
  an *override* that uses a different timetable (exam day, half day).

How a date is resolved
----------------------
1. Enabled schedules whose effective range contains the date are candidates.
   If several overlap, the one with the **latest start date** wins (ties: the
   most recently created). Overlaps are reported as warnings.
2. A *holiday* or *no bells* exception covering the date (for all schedules or
   the winning one) means no bells. These beat overrides.
3. Otherwise an *override* covering the date replaces the weekday's timetable.
4. Otherwise the weekday's timetable is used; a weekday without one is not a
   school day.

Bells at the same minute with the same sound and zones are merged into one
ring, so "end of period 1" and "start of period 2" at 08:45 ring once.
"""
from __future__ import annotations

import datetime
import re

from . import audio, clock, config, db

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]      # index = date.weekday()
KINDS = ("start_of_day", "period", "break", "end_of_day", "custom")
EXCEPTION_KINDS = ("holiday", "no_bells", "override")
PLAYBACK_MODES = ("once", "repeat", "duration")
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _date(s, field: str) -> str:
    try:
        return datetime.date.fromisoformat(str(s)).isoformat()
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a date (YYYY-MM-DD).")


def _hhmm(s, field: str) -> str:
    s = (str(s or "")).strip()
    if len(s) == 4 and s[1] == ":":
        s = "0" + s
    if not _HHMM.match(s):
        raise ValueError(f"{field} must be a time like 08:30.")
    return s


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _zone_ids(value) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Zones must be a list.")
    known = {z.id for z in config.get().zones}
    out = []
    for z in value:
        z = str(z)
        if z not in known:
            raise ValueError("One of the selected zones no longer exists.")
        if z not in out:
            out.append(z)
    return out


def _sound(name: str, field: str, required: bool = False) -> str:
    name = (name or "").strip()
    if not name:
        if required:
            raise ValueError(f"Choose a {field}.")
        return ""
    if not audio.path_for(name, "bells"):
        raise ValueError(f"The {field} '{name}' is not in the bell sound library.")
    return name


# --------------------------------------------------------------------------- #
# Timetables
# --------------------------------------------------------------------------- #
def _event_row(e: dict) -> dict:
    return {
        "id": e["id"], "label": e["label"], "kind": e["kind"], "start_time": e["start_time"],
        "end_time": e["end_time"], "ring_start": bool(e["ring_start"]), "ring_end": bool(e["ring_end"]),
        "sound_start": e["sound_start"], "sound_end": e["sound_end"],
        "zone_ids": db.loads(e["zone_ids"], None), "enabled": bool(e["enabled"]),
    }


def get_timetable(tt_id: int) -> dict | None:
    t = db.one("SELECT * FROM bell_timetables WHERE id = ?", (tt_id,))
    if not t:
        return None
    events = [_event_row(e) for e in db.query(
        "SELECT * FROM bell_events WHERE timetable_id = ? ORDER BY start_time, sort, id", (tt_id,))]
    return {"id": t["id"], "name": t["name"], "description": t["description"], "events": events,
            "updated_at": t["updated_at"], "usage": timetable_usage(tt_id),
            "warnings": timetable_warnings(events)}


def list_timetables() -> list[dict]:
    out = []
    for t in db.query("SELECT * FROM bell_timetables ORDER BY name"):
        events = db.query("SELECT start_time, end_time, ring_start, ring_end, enabled FROM bell_events "
                          "WHERE timetable_id = ? ORDER BY start_time", (t["id"],))
        minutes = set()
        for e in events:
            if not e["enabled"]:
                continue
            if e["ring_start"]:
                minutes.add(e["start_time"])
            if e["ring_end"] and e["end_time"]:
                minutes.add(e["end_time"])
        out.append({"id": t["id"], "name": t["name"], "description": t["description"],
                    "event_count": len(events), "ring_count": len(minutes),
                    "first": min(minutes) if minutes else None,
                    "last": max(minutes) if minutes else None,
                    "usage": timetable_usage(t["id"]), "updated_at": t["updated_at"]})
    return out


def timetable_usage(tt_id: int) -> dict:
    schedules = []
    for s in db.query("SELECT id, name, days FROM bell_schedules"):
        days = [d for d, v in (db.loads(s["days"], {}) or {}).items() if v == tt_id]
        if days:
            schedules.append({"id": s["id"], "name": s["name"], "days": [d for d in WEEKDAYS if d in days]})
    exceptions = db.query("SELECT id, name, date_from, date_to FROM bell_exceptions WHERE timetable_id = ?",
                          (tt_id,))
    return {"schedules": schedules, "exceptions": exceptions}


def _clean_events(events) -> list[dict]:
    if not isinstance(events, list):
        raise ValueError("Timetable rows must be a list.")
    if len(events) > 200:
        raise ValueError("A timetable can have at most 200 rows.")
    out = []
    for i, e in enumerate(events, 1):
        if not isinstance(e, dict):
            raise ValueError(f"Row {i} is not valid.")
        label = str(e.get("label") or "").strip()[:80]
        if not label:
            raise ValueError(f"Row {i}: enter a name.")
        kind = e.get("kind") or "period"
        if kind not in KINDS:
            raise ValueError(f"Row {i}: unknown row type.")
        start = _hhmm(e.get("start_time"), f"Row {i} start")
        end = e.get("end_time") or None
        if end:
            end = _hhmm(end, f"Row {i} end")
            if _minutes(end) <= _minutes(start):
                raise ValueError(f"Row {i} ({label}): the end time must be after the start time.")
        ring_start = bool(e.get("ring_start", True))
        ring_end = bool(e.get("ring_end", False)) and bool(end)
        if kind in ("start_of_day", "end_of_day", "custom"):
            end, ring_end = None, False
        out.append({
            "label": label, "kind": kind, "start_time": start, "end_time": end,
            "ring_start": ring_start, "ring_end": ring_end,
            "sound_start": _sound(e.get("sound_start", ""), "sound"),
            "sound_end": _sound(e.get("sound_end", ""), "sound"),
            "zone_ids": _zone_ids(e.get("zone_ids")) if e.get("zone_ids") else None,
            "enabled": bool(e.get("enabled", True)),
        })
    return out


def save_timetable(data: dict, tt_id: int | None = None) -> dict:
    name = str(data.get("name") or "").strip()[:80]
    if not name:
        raise ValueError("Enter a timetable name.")
    description = str(data.get("description") or "").strip()[:300]
    events = _clean_events(data.get("events") or [])
    dup = db.one("SELECT id FROM bell_timetables WHERE name = ? AND id IS NOT ?", (name, tt_id))
    if dup:
        raise ValueError("Another timetable already uses that name.")
    now = db.now()
    with db.tx():
        if tt_id is None:
            tt_id = db.execute("INSERT INTO bell_timetables (name, description, created_at, updated_at) "
                               "VALUES (?, ?, ?, ?)", (name, description, now, now)).lastrowid
        else:
            if not db.one("SELECT id FROM bell_timetables WHERE id = ?", (tt_id,)):
                raise LookupError("Timetable not found.")
            db.execute("UPDATE bell_timetables SET name = ?, description = ?, updated_at = ? WHERE id = ?",
                       (name, description, now, tt_id))
            db.execute("DELETE FROM bell_events WHERE timetable_id = ?", (tt_id,))
        for i, e in enumerate(events):
            db.execute(
                "INSERT INTO bell_events (timetable_id, label, kind, start_time, end_time, ring_start, ring_end,"
                " sound_start, sound_end, zone_ids, enabled, sort) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (tt_id, e["label"], e["kind"], e["start_time"], e["end_time"], int(e["ring_start"]),
                 int(e["ring_end"]), e["sound_start"], e["sound_end"],
                 db.dumps(e["zone_ids"]) if e["zone_ids"] is not None else None, int(e["enabled"]), i))
    return get_timetable(tt_id)


def duplicate_timetable(tt_id: int, new_name: str | None = None) -> dict:
    src = get_timetable(tt_id)
    if not src:
        raise LookupError("Timetable not found.")
    base = (new_name or f"{src['name']} (copy)").strip()[:72]
    name = base
    n = 2
    while db.one("SELECT id FROM bell_timetables WHERE name = ?", (name,)):
        name = f"{base} {n}"
        n += 1
    return save_timetable({"name": name, "description": src["description"], "events": src["events"]})


def delete_timetable(tt_id: int) -> dict | None:
    t = db.one("SELECT * FROM bell_timetables WHERE id = ?", (tt_id,))
    if not t:
        return None
    usage = timetable_usage(tt_id)
    if usage["schedules"] or usage["exceptions"]:
        names = [s["name"] for s in usage["schedules"]] + [e["name"] for e in usage["exceptions"]]
        raise ValueError("This timetable is still used by: " + ", ".join(names)
                         + ". Remove it there first.")
    db.execute("DELETE FROM bell_timetables WHERE id = ?", (tt_id,))
    return t


def timetable_warnings(events: list[dict]) -> list[dict]:
    warnings = []
    active = [e for e in events if e.get("enabled", True)]
    spans = sorted([e for e in active if e.get("end_time")], key=lambda e: e["start_time"])
    for a, b in zip(spans, spans[1:], strict=False):
        if _minutes(b["start_time"]) < _minutes(a["end_time"]):
            warnings.append({"code": "overlap", "message":
                             f"'{a['label']}' ({a['start_time']}–{a['end_time']}) overlaps "
                             f"'{b['label']}' (starts {b['start_time']})."})
    rings: dict[str, list[tuple]] = {}
    for e in active:
        if e.get("ring_start", True):
            rings.setdefault(e["start_time"], []).append((e["label"], e.get("sound_start") or "",
                                                          tuple(e.get("zone_ids") or [])))
        if e.get("ring_end") and e.get("end_time"):
            rings.setdefault(e["end_time"], []).append((f"end of {e['label']}", e.get("sound_end") or "",
                                                        tuple(e.get("zone_ids") or [])))
    for t, items in sorted(rings.items()):
        # Rows that leave sound/zones empty use the schedule default, which is unknown here: only
        # explicit, different choices are certain to produce separate bells at the same minute.
        sounds = {s for _, s, _ in items if s}
        zones = {z for _, _, z in items if z}
        if len(sounds) > 1 or len(zones) > 1:
            warnings.append({"code": "same_minute", "message":
                             f"Several different bells are set for {t} ("
                             + ", ".join(label for label, _, _ in items)
                             + "). Only one can play at a time; the others will wait up to the "
                               "maximum delay and may be skipped."})
    return warnings


# --------------------------------------------------------------------------- #
# Schedules
# --------------------------------------------------------------------------- #
def _schedule_row(s: dict) -> dict:
    days = db.loads(s["days"], {}) or {}
    return {
        "id": s["id"], "name": s["name"], "description": s["description"], "enabled": bool(s["enabled"]),
        "effective_from": s["effective_from"], "effective_to": s["effective_to"],
        "days": {d: days.get(d) for d in WEEKDAYS}, "zone_ids": db.loads(s["zone_ids"], []),
        "sound": s["sound"], "playback_mode": s["playback_mode"], "repeat_count": s["repeat_count"],
        "duration_seconds": s["duration_seconds"], "updated_at": s["updated_at"],
    }


def list_schedules() -> list[dict]:
    rows = [_schedule_row(s) for s in db.query("SELECT * FROM bell_schedules ORDER BY effective_from DESC, id DESC")]
    warnings = schedule_overlap_warnings(rows)
    for r in rows:
        r["warnings"] = [w for w in warnings if r["id"] in w["ids"]]
    return rows


def get_schedule(sid: int) -> dict | None:
    s = db.one("SELECT * FROM bell_schedules WHERE id = ?", (sid,))
    if not s:
        return None
    row = _schedule_row(s)
    row["warnings"] = [w for w in schedule_overlap_warnings(list_schedules_raw()) if sid in w["ids"]]
    return row


def list_schedules_raw() -> list[dict]:
    return [_schedule_row(s) for s in db.query("SELECT * FROM bell_schedules")]


def schedule_overlap_warnings(rows: list[dict]) -> list[dict]:
    out = []
    enabled = [r for r in rows if r["enabled"]]
    for i, a in enumerate(enabled):
        for b in enabled[i + 1:]:
            a_end = a["effective_to"] or "9999-12-31"
            b_end = b["effective_to"] or "9999-12-31"
            if a["effective_from"] <= b_end and b["effective_from"] <= a_end:
                winner = max((a, b), key=lambda r: (r["effective_from"], r["id"]))
                out.append({"code": "schedule_overlap", "ids": [a["id"], b["id"]], "message":
                            f"'{a['name']}' and '{b['name']}' are both active on some dates. On those dates "
                            f"'{winner['name']}' is used because it starts later."})
    return out


def save_schedule(data: dict, sid: int | None = None) -> dict:
    name = str(data.get("name") or "").strip()[:80]
    if not name:
        raise ValueError("Enter a schedule name.")
    eff_from = _date(data.get("effective_from"), "Start date")
    eff_to = _date(data["effective_to"], "End date") if data.get("effective_to") else None
    if eff_to and eff_to < eff_from:
        raise ValueError("The end date must be on or after the start date.")
    days_in = data.get("days") or {}
    days = {}
    for d in WEEKDAYS:
        v = days_in.get(d)
        if v in (None, "", 0, "0"):
            days[d] = None
            continue
        try:
            v = int(v)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid timetable for {d}.")
        if not db.one("SELECT id FROM bell_timetables WHERE id = ?", (v,)):
            raise ValueError(f"The timetable chosen for {d} no longer exists.")
        days[d] = v
    mode = data.get("playback_mode") or "once"
    if mode not in PLAYBACK_MODES:
        raise ValueError("Unknown playback mode.")
    try:
        repeat = int(data.get("repeat_count") or 1)
        duration = int(data.get("duration_seconds") or 10)
    except (TypeError, ValueError):
        raise ValueError("Repeat count and duration must be whole numbers.")
    if not 1 <= repeat <= 20:
        raise ValueError("Repeat count must be between 1 and 20.")
    if not 1 <= duration <= 600:
        raise ValueError("Duration must be between 1 and 600 seconds.")
    zone_ids = _zone_ids(data.get("zone_ids") or [])
    sound = _sound(data.get("sound", ""), "bell sound", required=True)
    if db.one("SELECT id FROM bell_schedules WHERE name = ? AND id IS NOT ?", (name, sid)):
        raise ValueError("Another schedule already uses that name.")
    now = db.now()
    values = (name, str(data.get("description") or "").strip()[:300], int(bool(data.get("enabled", True))),
              eff_from, eff_to, db.dumps(days), db.dumps(zone_ids), sound, mode, repeat, duration)
    if sid is None:
        sid = db.execute(
            "INSERT INTO bell_schedules (name, description, enabled, effective_from, effective_to, days, zone_ids,"
            " sound, playback_mode, repeat_count, duration_seconds, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (*values, now, now)).lastrowid
    else:
        cur = db.execute(
            "UPDATE bell_schedules SET name=?, description=?, enabled=?, effective_from=?, effective_to=?, days=?,"
            " zone_ids=?, sound=?, playback_mode=?, repeat_count=?, duration_seconds=?, updated_at=? WHERE id=?",
            (*values, now, sid))
        if not cur.rowcount:
            raise LookupError("Schedule not found.")
    return get_schedule(sid)


def delete_schedule(sid: int) -> dict | None:
    s = db.one("SELECT * FROM bell_schedules WHERE id = ?", (sid,))
    if s:
        db.execute("DELETE FROM bell_schedules WHERE id = ?", (sid,))
    return s


# --------------------------------------------------------------------------- #
# Calendar exceptions
# --------------------------------------------------------------------------- #
def _exception_row(e: dict) -> dict:
    return {"id": e["id"], "name": e["name"], "kind": e["kind"], "date_from": e["date_from"],
            "date_to": e["date_to"], "timetable_id": e["timetable_id"], "schedule_id": e["schedule_id"],
            "updated_at": e["updated_at"]}


def list_exceptions(since: str | None = None) -> list[dict]:
    if since:
        rows = db.query("SELECT * FROM bell_exceptions WHERE date_to >= ? ORDER BY date_from", (since,))
    else:
        rows = db.query("SELECT * FROM bell_exceptions ORDER BY date_from")
    return [_exception_row(e) for e in rows]


def save_exception(data: dict, eid: int | None = None) -> dict:
    name = str(data.get("name") or "").strip()[:80]
    if not name:
        raise ValueError("Enter a name, for example 'National holiday'.")
    kind = data.get("kind")
    if kind not in EXCEPTION_KINDS:
        raise ValueError("Choose holiday, no bells, or use a different timetable.")
    d_from = _date(data.get("date_from"), "Start date")
    d_to = _date(data.get("date_to") or d_from, "End date")
    if d_to < d_from:
        raise ValueError("The end date must be on or after the start date.")
    if (datetime.date.fromisoformat(d_to) - datetime.date.fromisoformat(d_from)).days > 400:
        raise ValueError("An exception can cover at most 400 days.")
    tt = data.get("timetable_id") or None
    if kind == "override":
        if not str(tt or "").isdigit() or not db.one("SELECT id FROM bell_timetables WHERE id = ?", (int(tt),)):
            raise ValueError("Choose the timetable to use on these dates.")
        tt = int(tt)
    else:
        tt = None
    sched = data.get("schedule_id") or None
    if sched is not None:
        if not str(sched).isdigit():
            raise ValueError("The selected schedule no longer exists.")
        sched = int(sched)
        if not db.one("SELECT id FROM bell_schedules WHERE id = ?", (sched,)):
            raise ValueError("The selected schedule no longer exists.")
    if kind == "override":
        clash = db.one(
            "SELECT name FROM bell_exceptions WHERE kind = 'override' AND id IS NOT ? AND date_from <= ? "
            "AND date_to >= ? AND (schedule_id IS ? OR schedule_id IS NULL OR ? IS NULL)",
            (eid, d_to, d_from, sched, sched))
        if clash:
            raise ValueError(f"These dates overlap the override '{clash['name']}'. Two overrides cannot "
                             "apply to the same day.")
    now = db.now()
    if eid is None:
        eid = db.execute("INSERT INTO bell_exceptions (name, kind, date_from, date_to, timetable_id, schedule_id,"
                         " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                         (name, kind, d_from, d_to, tt, sched, now, now)).lastrowid
    else:
        cur = db.execute("UPDATE bell_exceptions SET name=?, kind=?, date_from=?, date_to=?, timetable_id=?,"
                         " schedule_id=?, updated_at=? WHERE id=?", (name, kind, d_from, d_to, tt, sched, now, eid))
        if not cur.rowcount:
            raise LookupError("Exception not found.")
    return _exception_row(db.one("SELECT * FROM bell_exceptions WHERE id = ?", (eid,)))


def delete_exception(eid: int) -> dict | None:
    e = db.one("SELECT * FROM bell_exceptions WHERE id = ?", (eid,))
    if e:
        db.execute("DELETE FROM bell_exceptions WHERE id = ?", (eid,))
    return e


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #
def plan_for(day: datetime.date) -> dict:
    iso = day.isoformat()
    wkey = WEEKDAYS[day.weekday()]
    plan = {"date": iso, "weekday": wkey, "status": "no_schedule", "schedule": None, "timetable": None,
            "exception": None, "overlapping": [], "occurrences": [], "warnings": []}
    cands = [s for s in list_schedules_raw() if s["enabled"] and s["effective_from"] <= iso
             and (s["effective_to"] is None or s["effective_to"] >= iso)]
    if not cands:
        glob = db.one("SELECT * FROM bell_exceptions WHERE date_from <= ? AND date_to >= ? AND schedule_id IS NULL "
                      "AND kind IN ('holiday', 'no_bells') ORDER BY id LIMIT 1", (iso, iso))
        if glob:
            plan["status"] = glob["kind"]
            plan["exception"] = _exception_row(glob)
        return plan
    cands.sort(key=lambda s: (s["effective_from"], s["id"]), reverse=True)
    sched = cands[0]
    plan["schedule"] = {"id": sched["id"], "name": sched["name"]}
    plan["overlapping"] = [{"id": s["id"], "name": s["name"]} for s in cands[1:]]
    excs = [_exception_row(e) for e in db.query(
        "SELECT * FROM bell_exceptions WHERE date_from <= ? AND date_to >= ? AND "
        "(schedule_id IS NULL OR schedule_id = ?) ORDER BY id", (iso, iso, sched["id"]))]
    blocking = [e for e in excs if e["kind"] in ("holiday", "no_bells")]
    if blocking:
        plan["status"] = blocking[0]["kind"]
        plan["exception"] = blocking[0]
        return plan
    override = next((e for e in excs if e["kind"] == "override"), None)
    tt_id = override["timetable_id"] if override else sched["days"].get(wkey)
    if override:
        plan["exception"] = override
    if not tt_id:
        plan["status"] = "not_school_day"
        return plan
    tt = get_timetable(tt_id)
    if not tt:
        plan["status"] = "not_school_day"
        return plan
    plan["status"] = "override" if override else "school_day"
    plan["timetable"] = {"id": tt["id"], "name": tt["name"]}
    plan["occurrences"] = _occurrences(day, sched, tt)
    plan["warnings"] = [w for w in tt["warnings"] if w["code"] != "same_minute"]
    by_time: dict[str, list[dict]] = {}
    for o in plan["occurrences"]:
        by_time.setdefault(o["time"], []).append(o)
    for t, items in sorted(by_time.items()):
        if len(items) > 1:
            plan["warnings"].append({"code": "same_minute", "message":
                                     f"Several different bells are set for {t} ("
                                     + ", ".join(" · ".join(o["labels"]) for o in items)
                                     + "). Only one can play at a time; the others will wait up to the "
                                       "maximum delay and may be skipped."})
    return plan


def _occurrences(day: datetime.date, sched: dict, tt: dict) -> list[dict]:
    playback = {"mode": sched["playback_mode"], "repeat_count": sched["repeat_count"],
                "duration_seconds": sched["duration_seconds"]}
    raw = []
    for e in tt["events"]:
        if not e["enabled"]:
            continue
        edges = []
        if e["ring_start"]:
            edges.append(("start", e["start_time"], e["sound_start"], e["label"]))
        if e["ring_end"] and e["end_time"]:
            edges.append(("end", e["end_time"], e["sound_end"], f"End of {e['label']}"))
        for edge, hhmm, sound, label in edges:
            zones = e["zone_ids"] if e["zone_ids"] else sched["zone_ids"]
            raw.append({"time": hhmm, "label": label, "edge": edge, "kind": e["kind"], "event_id": e["id"],
                        "sound": sound or sched["sound"], "zone_ids": list(zones)})
    raw.sort(key=lambda r: (r["time"], r["edge"] != "end", r["event_id"]))
    merged: list[dict] = []
    groups: dict[tuple, dict] = {}
    for r in raw:
        gkey = (r["time"], r["sound"], tuple(sorted(r["zone_ids"])))
        if gkey in groups:
            groups[gkey]["labels"].append(r["label"])
            groups[gkey]["event_ids"].append(r["event_id"])
            continue
        item = {**r, "labels": [r["label"]], "event_ids": [r["event_id"]]}
        groups[gkey] = item
        merged.append(item)
    out = []
    for m in merged:
        at = clock.at(day, m["time"])
        path = audio.path_for(m["sound"], "bells")
        key = f"bells:{day.isoformat()}:s{sched['id']}:{m['time']}:{'+'.join(map(str, m['event_ids']))}"
        out.append({
            "key": key, "module": "bells", "kind": "bell", "at": at.isoformat(), "time": m["time"],
            "title": " · ".join(m["labels"]), "labels": m["labels"], "edge": m["edge"], "row_kind": m["kind"],
            "event_ids": m["event_ids"], "schedule_id": sched["id"], "timetable_id": tt["id"],
            "sound": m["sound"], "sound_missing": path is None, "zone_ids": m["zone_ids"],
            "playback": playback,
            "est_seconds": audio.playback_seconds(path, playback["mode"], playback["repeat_count"],
                                                  playback["duration_seconds"]),
        })
    return out


def plans_between(start: datetime.date, days: int) -> list[dict]:
    return [plan_for(start + datetime.timedelta(days=i)) for i in range(max(1, min(days, 62)))]
