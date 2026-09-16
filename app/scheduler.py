"""Scheduler: turns today's occurrences into broadcasts at the right moment.

Safety rules (documented in docs/SCHEDULING.md)
-----------------------------------------------
* Time is the NTP-corrected clock in the configured timezone. The plan for
  today and tomorrow is rebuilt when settings change, at midnight, and every
  minute.
* An occurrence plays only if it was **known before it was due**. Adding a bell
  for 09:00 at 09:05 does not ring it.
* An occurrence due while the service is running plays if it can start within
  the module's **late grace** (Adhan 120 s, bells 60 s). If the process was
  stalled longer, it is recorded as *missed* and never played late.
* After a restart, occurrences due since the last heartbeat and still inside the
  grace window play; older ones are recorded as *missed*.
* Anything that was queued or playing when the service stopped is recorded as
  *interrupted* and is not replayed.
* Every occurrence is recorded under a unique key before playing, so restarts,
  clock corrections, repeated DST hours and duplicate plans cannot replay it.
"""
from __future__ import annotations

import datetime
import logging
import threading
import time

from . import audit, clock, config, db, dispatcher, executions, planner, sip

log = logging.getLogger("scheduler")

HEARTBEAT_KEY = "scheduler.heartbeat"
HEARTBEAT_EVERY = 15
REBUILD_EVERY = 60


class Scheduler:
    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._dirty = threading.Event()
        self._plan: list[dict] = []
        self._plan_date: datetime.date | None = None
        self._built_mono = 0.0
        self._known: dict[str, str] = {}      # key -> at (iso), for occurrences seen before they were due
        self._handled: set[str] = set()
        self._last_tick: float | None = None
        self._last_heartbeat = 0.0
        self._lock = threading.RLock()
        self._sip_check = time.monotonic()      # first watchdog check after 30 s, not during start-up

    # ----------------------------------------------------------------- control
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.recover()
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(5)
        db.set_setting(HEARTBEAT_KEY, time.time())

    def mark_dirty(self) -> None:
        self._dirty.set()

    # ---------------------------------------------------------------- recovery
    def recover(self) -> None:
        n = executions.mark_unfinished_interrupted()
        if n:
            log.warning("%d broadcast(s) were in progress when the service stopped; marked interrupted", n)
            audit.record("service.recovered_interrupted", result="warning", details={"count": n})
        last = db.get_setting(HEARTBEAT_KEY)
        now = clock.now()
        self._rebuild(now)
        if not last:
            # Fresh install: nothing before now was ever scheduled.
            with self._lock:
                self._known = {o["key"]: o["at"] for o in self._plan
                               if datetime.datetime.fromisoformat(o["at"]) > now}
            return
        last_dt = datetime.datetime.fromtimestamp(float(last), tz=datetime.timezone.utc).astimezone(now.tzinfo)
        candidates = self._plan + self._occurrences_for(now.date() - datetime.timedelta(days=1))
        missed = 0
        with self._lock:
            for o in candidates:
                at = datetime.datetime.fromisoformat(o["at"])
                if at > last_dt:
                    self._known[o["key"]] = o["at"]
        outage = (now - last_dt).total_seconds()
        if outage > 90:
            log.warning("service was not running from %s to %s", last_dt.strftime("%Y-%m-%d %H:%M:%S"),
                        now.strftime("%H:%M:%S"))
            audit.record("service.restarted_after_outage", result="warning",
                         details={"offline_from": last_dt.isoformat(), "offline_to": now.isoformat()})
        for o in sorted(candidates, key=lambda x: x["at"]):
            at = datetime.datetime.fromisoformat(o["at"])
            if last_dt < at <= now and (now - at).total_seconds() > self._grace(o):
                if self._record_missed(o, f"The service was not running at {at.strftime('%H:%M')} "
                                          f"(offline {last_dt.strftime('%H:%M')}–{now.strftime('%H:%M')}). "
                                          "Missed broadcasts are never played late."):
                    missed += 1
        if missed:
            audit.record("service.missed_during_outage", result="warning", details={"count": missed})

    # -------------------------------------------------------------------- loop
    def _loop(self) -> None:
        log.info("scheduler started")
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                log.exception("scheduler tick failed")
            self._stop.wait(self._next_wait())

    def _next_wait(self) -> float:
        """Sleep at most 1 s, but wake exactly when the next occurrence is due."""
        try:
            now = clock.now()
        except Exception:  # noqa: BLE001
            return 1.0
        with self._lock:
            upcoming = [datetime.datetime.fromisoformat(o["at"]) for o in self._plan if o["key"] not in self._handled]
        future = [(at - now).total_seconds() for at in upcoming if at > now]
        return max(0.02, min([1.0, *[f + 0.01 for f in future]]))

    def tick(self, now: datetime.datetime | None = None) -> None:
        now = now or clock.now()
        self._last_tick = time.time()
        if (self._dirty.is_set() or self._plan_date != now.date()
                or time.monotonic() - self._built_mono > REBUILD_EVERY):
            self._dirty.clear()
            self._rebuild(now)
        due = []
        with self._lock:
            for o in self._plan:
                key = o["key"]
                if key in self._handled:
                    continue
                at = datetime.datetime.fromisoformat(o["at"])
                if at > now:
                    self._known[key] = o["at"]
                    continue
                self._handled.add(key)
                if key not in self._known:
                    log.debug("ignoring %s: it was added after its time", key)
                    continue
                due.append((o, (now - at).total_seconds()))
        for o, late in due:
            if executions.exists(o["key"]):
                continue
            if late > self._grace(o):
                self._record_missed(o, f"Not played: it was due {int(late)} s ago (the service was busy, "
                                       "paused or the clock jumped). Missed broadcasts are never played late.")
                continue
            dispatcher.get().submit(dispatcher.from_occurrence(o))
        if time.time() - self._last_heartbeat >= HEARTBEAT_EVERY:
            self._last_heartbeat = time.time()
            db.set_setting(HEARTBEAT_KEY, self._last_heartbeat)
        if time.monotonic() - self._sip_check > 30:
            self._sip_check = time.monotonic()
            self._check_sip()

    def _check_sip(self) -> None:
        eng = sip.get_engine()
        b = eng.backend
        if config.get().sip.enabled and b.available and not b.running() and not getattr(b, "starting", False):
            log.warning("SIP engine is not running; restarting it")
            audit.record("sip.engine_restarted", result="warning")
            eng.start()

    def _grace(self, occ: dict) -> int:
        pol = config.get().policy
        return pol.adhan_late_grace_seconds if occ["module"] == "adhan" else pol.bells_late_grace_seconds

    def _record_missed(self, occ: dict, reason: str) -> bool:
        created = executions.create(occ["key"], module=occ["module"], kind=occ["kind"], title=occ["title"],
                                    priority=planner.PRIORITY[occ["module"]], scheduled_for=occ["at"],
                                    actor="scheduler", sound=occ.get("sound") or "",
                                    targets=occ.get("zone_ids") or [], status="missed", reason=reason)
        if created:
            executions.update(created, finished_at=int(time.time()))
            log.warning("MISSED %s at %s: %s", occ["title"], occ["at"], reason)
            audit.record("broadcast.missed", target=occ["title"], result="warning",
                         details={"key": occ["key"], "reason": reason})
        with self._lock:
            self._handled.add(occ["key"])
        return bool(created)

    def _occurrences_for(self, day: datetime.date) -> list[dict]:
        return planner.day_occurrences(day)

    def _rebuild(self, now: datetime.datetime) -> None:
        today = now.date()
        plan = self._occurrences_for(today) + self._occurrences_for(today + datetime.timedelta(days=1))
        keep_from = (today - datetime.timedelta(days=2)).isoformat()
        with self._lock:
            self._plan = plan
            self._plan_date = today
            self._built_mono = time.monotonic()
            self._known = {k: v for k, v in self._known.items() if v[:10] >= keep_from}
            self._handled = {k for k in self._handled if k.split(":")[1] >= keep_from}

    # ------------------------------------------------------------------ status
    def upcoming(self, modules=("adhan", "bells"), limit: int = 10) -> list[dict]:
        now = clock.now()
        with self._lock:
            items = [dict(o) for o in self._plan if o["module"] in modules
                     and datetime.datetime.fromisoformat(o["at"]) > now]
        return items[:limit]

    def status(self) -> dict:
        age = time.time() - self._last_tick if self._last_tick else None
        return {"running": bool(self._thread and self._thread.is_alive()),
                "last_tick_age": round(age, 1) if age is not None else None,
                "healthy": age is not None and age < 10,
                "planned": len(self._plan)}


_scheduler: Scheduler | None = None


def get() -> Scheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = Scheduler()
    return _scheduler


def set_scheduler(s: Scheduler | None) -> None:
    global _scheduler
    _scheduler = s
