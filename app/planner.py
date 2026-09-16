"""Builds the list of timed broadcasts ("occurrences") for a day, across modules.

Each occurrence carries a stable ``key`` that is recorded in the executions
table before anything plays. Keys make a broadcast happen at most once:

* Adhan:  ``adhan:<date>:<prayer>:<adhan|iqama>``. A small adjustment to a
  prayer time does not create a second Adhan on the same day.
* Bells:  ``bells:<date>:s<schedule>:<HH:MM>:<rows>``. Moving a bell to a new
  time creates a new occurrence at that time.
"""
from __future__ import annotations

import datetime
import logging

from . import audio, bells, clock, config, prayer

log = logging.getLogger("planner")

PRAYER_LABELS = {"fajr": "Fajr", "dhuhr": "Dhuhr", "asr": "Asr", "maghrib": "Maghrib", "isha": "Isha"}
DEFAULT_ADHAN_SECONDS = 180


def adhan_occurrences(day: datetime.date) -> list[dict]:
    cfg = config.get()
    anchor = datetime.datetime.combine(day, datetime.time(12, 0), tzinfo=clock.tz())
    try:
        rows = prayer.today_schedule(anchor)
    except Exception:  # noqa: BLE001
        log.exception("prayer times could not be computed for %s", day)
        return []
    out = []
    for row in rows:
        name = row["prayer"]
        if row["enabled"] and row["time"] is not None:
            path = audio.resolve_for_prayer(name)
            out.append(_adhan_occ(day, name, "adhan", row["time"], path, cfg))
        if row.get("iqama_enabled") and row.get("iqama_time") is not None:
            path = audio.resolve_iqama_for_prayer(name) or audio.resolve_for_prayer(name)
            out.append(_adhan_occ(day, name, "iqama", row["iqama_time"], path, cfg))
    return out


def _adhan_occ(day, name: str, kind: str, at: datetime.datetime, path, cfg) -> dict:
    at = at.replace(microsecond=0)
    label = PRAYER_LABELS.get(name, name.title())
    return {
        "key": f"adhan:{day.isoformat()}:{name}:{kind}", "module": "adhan", "kind": kind,
        "prayer": name, "at": at.isoformat(), "time": at.strftime("%H:%M"),
        "title": f"{label} {'Adhan' if kind == 'adhan' else 'iqama'}",
        "sound": path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] if path else "", "sound_missing": path is None,
        "zone_ids": list(cfg.adhan.zone_ids), "playback": {"mode": "once"},
        "est_seconds": audio.duration_of(path) if path else None,
    }


def day_occurrences(day: datetime.date, modules=("adhan", "bells")) -> list[dict]:
    occ: list[dict] = []
    if "adhan" in modules:
        occ.extend(adhan_occurrences(day))
    if "bells" in modules:
        try:
            occ.extend(bells.plan_for(day)["occurrences"])
        except Exception:  # noqa: BLE001
            log.exception("bell plan could not be built for %s", day)
    occ.sort(key=lambda o: o["at"])
    return occ


def annotate_conflicts(occurrences: list[dict]) -> list[dict]:
    """Mark occurrences that will compete for the line, with the policy outcome."""
    pol = config.get().policy
    items = sorted(occurrences, key=lambda o: o["at"])
    for o in items:
        o.setdefault("conflicts", [])
    for i, a in enumerate(items):
        a_start = datetime.datetime.fromisoformat(a["at"])
        a_len = a.get("est_seconds") or (DEFAULT_ADHAN_SECONDS if a["module"] == "adhan" else 10)
        a_end = a_start + datetime.timedelta(seconds=a_len)
        for b in items[i + 1:]:
            b_start = datetime.datetime.fromisoformat(b["at"])
            if b_start >= a_end:
                break
            winner, loser = _policy_order(a, b)
            max_wait = pol.adhan_max_delay_seconds if loser["module"] == "adhan" else pol.bells_max_delay_seconds
            w_start = datetime.datetime.fromisoformat(winner["at"])
            w_len = winner.get("est_seconds") or (DEFAULT_ADHAN_SECONDS if winner["module"] == "adhan" else 10)
            l_start = datetime.datetime.fromisoformat(loser["at"])
            if winner is a:
                wait = (w_start + datetime.timedelta(seconds=w_len) - l_start).total_seconds()
                outcome = "delayed" if wait <= max_wait else "skipped"
            else:
                outcome = "interrupts" if l_start < w_start else "delayed"
            detail = {
                "with": winner["title"], "with_key": winner["key"], "with_module": winner["module"],
                "outcome": outcome, "max_wait": max_wait,
            }
            loser["conflicts"].append(detail)
            winner["conflicts"].append({"with": loser["title"], "with_key": loser["key"],
                                        "with_module": loser["module"], "outcome": "wins"})
    return items


def _policy_order(a: dict, b: dict) -> tuple[dict, dict]:
    """Return (winner, loser): Adhan beats bells; within a module the earlier one plays first."""
    pa, pb = PRIORITY.get(a["module"], 0), PRIORITY.get(b["module"], 0)
    if pa != pb:
        return (a, b) if pa > pb else (b, a)
    return (a, b) if a["at"] <= b["at"] else (b, a)


PRIORITY = {"manual": 30, "adhan": 20, "bells": 10}
