"""Broadcast dispatcher: the single owner of the paging line.

Priority policy (documented in docs/SCHEDULING.md)
--------------------------------------------------
Priorities: manual broadcast (ring now, test, play file) 30 > Adhan 20 > school bell 10.

1. One broadcast uses the line at a time.
2. A **scheduled** broadcast with higher priority than the one playing interrupts
   it (the interrupted one is recorded as *interrupted* and is not replayed).
   Example: the Adhan interrupts a school bell.
3. A scheduled broadcast with equal or lower priority waits. If it cannot start
   within its maximum delay (Adhan 300 s, bells 60 s by default) it is recorded
   as *skipped*. If it starts late it is recorded with its delay.
   Example: a bell due during the Adhan waits and is usually skipped.
4. A **manual** broadcast never interrupts silently. If the line is busy the
   request is refused with details of what is playing; the operator may repeat
   it with "interrupt", which requires permission to stop the broadcast that is
   playing. Scheduled broadcasts never interrupt a manual one; they wait.
5. Every broadcast is recorded before it plays, under a unique key, so it can
   never play twice, including across restarts.
"""
from __future__ import annotations

import datetime
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field

from . import audio, audit, clock, config, executions, sip
from .planner import PRIORITY

log = logging.getLogger("dispatcher")

LATE_NOTICE_SECONDS = 5      # smaller delays are normal scheduling latency, not worth reporting


@dataclass
class Broadcast:
    key: str
    module: str                      # adhan | bells
    kind: str                        # adhan | iqama | bell | manual | test
    title: str
    priority: int
    manual: bool
    sound_path: str | None
    sound_name: str
    zone_ids: list | None = None
    destinations: list | None = None   # explicit URIs (e.g. a single test destination)
    playback: dict | None = None
    scheduled_for: datetime.datetime | None = None
    actor: str = "scheduler"
    max_wait: float = 60.0
    enqueued_at: float = field(default_factory=time.monotonic)
    execution_id: int | None = None
    stop: sip.StopToken = field(default_factory=sip.StopToken)
    stop_note: str = ""
    done: threading.Event = field(default_factory=threading.Event)


class Conflict(Exception):
    def __init__(self, active: dict):
        super().__init__("The line is busy")
        self.active = active


class Dispatcher:
    def __init__(self):
        self._cond = threading.Condition()
        self._queue: list[Broadcast] = []
        self._active: Broadcast | None = None
        self._thread: threading.Thread | None = None
        self._stopping = False

    # ------------------------------------------------------------------ public
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stopping = False
        self._thread = threading.Thread(target=self._loop, name="dispatcher", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        with self._cond:
            self._stopping = True
            if self._active:
                self._active.stop_note = "The service is shutting down."
                self._active.stop.set("shutdown")
            self._cond.notify_all()
        if self._thread:
            self._thread.join(10)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with self._cond:
                if self._active is None:
                    break
            time.sleep(0.05)

    def submit(self, b: Broadcast, *, force: bool = False) -> dict:
        """Queue a broadcast. Raises ``Conflict`` for a manual request on a busy line (unless forced)."""
        with self._cond:
            if b.manual and not force and (self._active is not None or self._queue):
                raise Conflict(self._describe(self._active or self._queue[0]))
        sched = (b.scheduled_for or clock.now()).isoformat()
        exec_id = executions.create(
            b.key, module=b.module, kind=b.kind, title=b.title, priority=b.priority, scheduled_for=sched,
            actor=b.actor, sound=b.sound_name, targets=b.zone_ids or b.destinations or [])
        if exec_id is None:
            log.warning("duplicate broadcast ignored: %s", b.key)
            return {"status": "duplicate", "key": b.key}
        b.execution_id = exec_id
        with self._cond:
            active = self._active
            if active is not None:
                if b.manual and force:
                    active.stop_note = f"Stopped by {b.actor} to start '{b.title}'."
                    active.stop.set("stopped")
                elif not b.manual and b.priority > active.priority and not active.manual:
                    active.stop_note = f"Interrupted by '{b.title}' (higher priority)."
                    active.stop.set("preempted")
            if b.manual and force:
                for q in list(self._queue):
                    if q.manual:
                        self._queue.remove(q)
                        self._finish_unstarted(q, "cancelled", f"Replaced by '{b.title}' from {b.actor}.")
            self._queue.append(b)
            self._cond.notify_all()
        log.info("queued %s (%s, priority %s)", b.title, b.key, b.priority)
        return {"status": "queued", "key": b.key, "execution_id": exec_id}

    def stop_active(self, actor: str, allowed_modules: set[str]) -> dict:
        with self._cond:
            a = self._active
            if a is None:
                return {"stopped": False, "message": "Nothing is playing."}
            if a.module not in allowed_modules:
                raise PermissionError(a.module)
            a.stop_note = f"Stopped by {actor}."
            a.stop.set("stopped")
            desc = self._describe(a)
        audit.record("broadcast.stop", actor=actor, target=desc["title"], details={"key": desc["key"]})
        return {"stopped": True, "broadcast": desc}

    def snapshot(self) -> dict:
        with self._cond:
            active = self._describe(self._active) if self._active else None
            queue = [self._describe(q) for q in sorted(self._queue, key=self._order)]
        if active:
            live = sip.get_engine().active()
            if live:
                active["line"] = live
        return {"active": active, "queue": queue}

    def active_module(self) -> str | None:
        with self._cond:
            return self._active.module if self._active else None

    def wait_idle(self, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._cond:
                if self._active is None and not self._queue:
                    return True
            time.sleep(0.02)
        return False

    # ---------------------------------------------------------------- internal
    @staticmethod
    def _order(b: Broadcast):
        return (-b.priority, b.scheduled_for.timestamp() if b.scheduled_for else 0, b.enqueued_at)

    @staticmethod
    def _describe(b: Broadcast) -> dict:
        return {"key": b.key, "module": b.module, "kind": b.kind, "title": b.title, "priority": b.priority,
                "manual": b.manual, "actor": b.actor, "execution_id": b.execution_id,
                "scheduled_for": b.scheduled_for.isoformat() if b.scheduled_for else None,
                "waiting_seconds": round(time.monotonic() - b.enqueued_at, 1)}

    def _loop(self) -> None:
        while True:
            with self._cond:
                if self._stopping:
                    for q in self._queue:
                        self._finish_unstarted(q, "cancelled", "The service shut down before this could play.")
                    self._queue.clear()
                    return
                self._expire_waiting()
                if self._active is None and self._queue:
                    self._queue.sort(key=self._order)
                    nxt = self._queue.pop(0)
                    self._active = nxt
                    threading.Thread(target=self._run_one, args=(nxt,), name="line", daemon=True).start()
                self._cond.wait(0.25)

    def _run_one(self, b: Broadcast) -> None:
        try:
            self._execute(b)
        except Exception:  # noqa: BLE001
            log.exception("broadcast %s crashed", b.key)
            executions.update(b.execution_id, status="failed", finished_at=int(time.time()),
                              reason="Internal error while playing. See the service log.")
        finally:
            with self._cond:
                self._active = None
                b.done.set()
                self._cond.notify_all()

    def _expire_waiting(self) -> None:
        now = time.monotonic()
        for q in list(self._queue):
            if q.manual:
                continue
            if now - q.enqueued_at > q.max_wait:
                self._queue.remove(q)
                blocker = self._active.title if self._active else "another broadcast"
                self._finish_unstarted(q, "skipped", f"Skipped: the line was busy with '{blocker}' for more "
                                                     f"than {int(q.max_wait)} s.")

    def _finish_unstarted(self, b: Broadcast, status: str, reason: str) -> None:
        executions.update(b.execution_id, status=status, reason=reason, finished_at=int(time.time()))
        b.done.set()
        log.warning("%s %s: %s", b.title, status, reason)
        if status == "skipped":
            audit.record("broadcast.skipped", target=b.title, result="warning", details={"key": b.key,
                                                                                         "reason": reason})

    def _execute(self, b: Broadcast) -> None:
        now = clock.now()
        delay = None
        if b.scheduled_for and not b.manual:
            delay = round(max(0.0, (now - b.scheduled_for).total_seconds()), 1)
        executions.update(b.execution_id, status="running", started_at=int(time.time()), delay_seconds=delay)
        cfg = config.get()
        if b.destinations:
            uris, mode = list(b.destinations), cfg.call.mode
        else:
            uris, mode = config.resolve_targets(b.zone_ids, cfg)
        problem = None
        if not b.sound_path or not audio.duration_of(b.sound_path):
            problem = (f"No playable audio file ('{b.sound_name}' is missing)." if b.sound_name
                       else "No audio file is assigned.")
        elif not uris:
            problem = "No enabled destinations in the selected zones."
        if problem:
            executions.update(b.execution_id, status="failed", reason=problem, finished_at=int(time.time()))
            audit.record("broadcast.failed", target=b.title, result="error", details={"key": b.key,
                                                                                      "reason": problem})
            log.error("%s not played: %s", b.title, problem)
            return
        wav = b.sound_path
        pb = b.playback or {}
        if pb.get("mode") in ("repeat", "duration"):
            try:
                wav = audio.render_playback(b.sound_path, pb["mode"], int(pb.get("repeat_count") or 1),
                                            int(pb.get("duration_seconds") or 10))
            except Exception as e:  # noqa: BLE001
                log.warning("could not render playback (%s); playing once", e)
        executions.update(b.execution_id, targets=uris)
        if delay and delay >= LATE_NOTICE_SECONDS:
            log.info("PLAYING %s -> %s (%.0f s late)", b.title, uris, delay)
        else:
            log.info("PLAYING %s -> %s", b.title, uris)
        result = sip.get_engine().page(wav, uris, mode, label=b.title, kind=b.kind, stop=b.stop)
        status = {"success": "success", "partial": "partial", "failed": "failed"}.get(result.status)
        reason = result.message
        if result.status == "cancelled":
            status = "interrupted" if b.stop.reason in ("preempted", "shutdown") else "cancelled"
            reason = b.stop_note or reason
        if delay and delay >= LATE_NOTICE_SECONDS and status in ("success", "partial"):
            reason = f"{reason} Started {int(delay)} s late."
        executions.update(b.execution_id, status=status, reason=reason, finished_at=int(time.time()),
                          results=result.to_dict()["destinations"], codec=result.codec)
        if status in ("failed", "partial", "interrupted"):
            audit.record(f"broadcast.{status}", actor="system" if not b.manual else b.actor, target=b.title,
                         result="error" if status == "failed" else "warning",
                         details={"key": b.key, "reason": reason})
        log.info("%s finished: %s — %s", b.title, status, reason)


_dispatcher: Dispatcher | None = None


def get() -> Dispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = Dispatcher()
    return _dispatcher


def set_dispatcher(d: Dispatcher | None) -> None:
    global _dispatcher
    _dispatcher = d


def manual_key() -> str:
    return f"manual:{uuid.uuid4().hex}"


def from_occurrence(occ: dict) -> Broadcast:
    pol = config.get().policy
    module = occ["module"]
    if module == "adhan":
        from . import audio as _a
        path = _a.resolve_for_prayer(occ["prayer"]) if occ["kind"] == "adhan" else (
            _a.resolve_iqama_for_prayer(occ["prayer"]) or _a.resolve_for_prayer(occ["prayer"]))
        max_wait = pol.adhan_max_delay_seconds
    else:
        path = audio.path_for(occ["sound"], "bells")
        max_wait = pol.bells_max_delay_seconds
    return Broadcast(
        key=occ["key"], module=module, kind=occ["kind"], title=occ["title"], priority=PRIORITY[module],
        manual=False, sound_path=path, sound_name=occ.get("sound") or "", zone_ids=occ.get("zone_ids") or [],
        playback=occ.get("playback"), scheduled_for=datetime.datetime.fromisoformat(occ["at"]),
        actor="scheduler", max_wait=float(max_wait))
