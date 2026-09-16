"""Paging engine: dials destinations, plays audio, hangs up, reports per-destination results.

The engine owns call *policy* (ring timeout, answer delay, end-of-file hang-up,
manual stop, safety limits) and delegates SIP mechanics to a backend. Every
call is released in a ``finally`` block so stuck sessions and leaked media
players cannot accumulate.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass, field

from .. import audio, config
from .base import CALL_OUTCOME_MESSAGES, Backend, CallHandle, SipError, call_outcome, registration_message

log = logging.getLogger("sip")

MEDIA_WAIT_SECONDS = 6.0
DISCONNECT_WAIT_SECONDS = 6.0


@dataclass
class DestResult:
    uri: str
    outcome: str = "failed"
    message: str = ""
    sip_code: int = 0
    answered: bool = False
    codec: str = ""
    ring_seconds: float = 0.0
    connected_seconds: float = 0.0
    played_seconds: float = 0.0
    played_to_end: bool = False
    rtp: dict = field(default_factory=dict)


@dataclass
class PageResult:
    status: str                      # success | partial | failed | cancelled
    destinations: list[DestResult]
    codec: str = ""
    message: str = ""

    def to_dict(self) -> dict:
        return {"status": self.status, "codec": self.codec, "message": self.message,
                "destinations": [asdict(d) for d in self.destinations]}


class StopToken:
    def __init__(self):
        self._evt = threading.Event()
        self.reason = ""

    def set(self, reason: str = "stopped") -> None:
        if not self._evt.is_set():
            self.reason = reason
        self._evt.set()

    def is_set(self) -> bool:
        return self._evt.is_set()

    def wait(self, timeout: float) -> bool:
        return self._evt.wait(timeout)


class PagingEngine:
    def __init__(self, backend: Backend):
        self.backend = backend
        self._lock = threading.RLock()
        self._active: dict | None = None
        self._stop: StopToken | None = None
        self._handles: dict[int, CallHandle] = {}
        self._line_lock = threading.Lock()     # one page at a time on the line

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        b = self.backend
        reg = b.registration()
        sip = config.get().sip
        now = time.time()
        return {
            "available": b.available,
            "simulated": b.simulated,
            "backend": b.name,
            "detail": b.detail,
            "enabled": sip.enabled,
            "running": b.running(),
            "registered": reg.registered,
            "reg_code": reg.code,
            "reg_reason": reg.reason,
            "message": (registration_message(reg.code, reg.reason, sip.registrar) if sip.enabled
                        else "SIP is turned off in settings."),
            "last_registered_at": reg.last_success,
            "last_attempt_at": reg.last_attempt,
            "expires": reg.expires,
            "consecutive_failures": reg.consecutive_failures,
            "next_retry_in": (max(0, int(reg.next_retry_at - now)) if reg.next_retry_at else None),
            "identity": f"{sip.username}@{sip.registrar}",
            "transport": sip.transport,
            "port": sip.port,
            "active_calls": len(self._handles),
            "live_objects": b.live_objects(),
        }

    def active(self) -> dict | None:
        with self._lock:
            if not self._active:
                return None
            out = dict(self._active)
            out["destinations"] = [dict(d) for d in self._active["destinations"]]
        started = out.get("playing_since")
        if started and out.get("duration"):
            elapsed = time.monotonic() - started
            out["elapsed_s"] = round(elapsed, 1)
            out["remaining_s"] = round(max(0.0, out["duration"] - elapsed), 1)
        out.pop("playing_since", None)
        return out

    # ----------------------------------------------------------------- control
    def start(self) -> None:
        if not config.get().sip.enabled:
            log.info("SIP is disabled in settings; engine not started")
            return
        try:
            self.backend.start()
        except Exception as e:  # noqa: BLE001
            log.exception("SIP backend failed to start: %s", e)

    def shutdown(self) -> None:
        self.stop_active("shutdown")
        self._wait_idle(8)
        try:
            self.backend.stop()
        except Exception:  # noqa: BLE001
            log.exception("SIP backend stop failed")

    def restart(self) -> None:
        """Apply new SIP settings. Any page in progress is ended cleanly first."""
        self.stop_active("settings changed")
        self._wait_idle(10)
        try:
            self.backend.stop()
        except Exception:  # noqa: BLE001
            log.exception("SIP backend stop failed")
        self.start()

    def _wait_idle(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while self._handles and time.monotonic() < deadline:
            time.sleep(0.05)

    def stop_active(self, reason: str = "stopped") -> bool:
        with self._lock:
            tok = self._stop
            busy = self._active is not None
        if tok and busy:
            tok.set(reason)
            log.info("page stop requested (%s)", reason)
            return True
        return False

    def busy(self) -> bool:
        return self._line_lock.locked()

    # ------------------------------------------------------------------ paging
    def page(self, wav_path: str, destinations: list[str], mode: str = "sequential", *,
             label: str = "", kind: str = "", stop: StopToken | None = None) -> PageResult:
        """Blocking: call ``destinations`` and play ``wav_path`` on each. Never raises for call failures."""
        stop = stop or StopToken()
        if not destinations:
            return PageResult("failed", [], message="No destinations to call.")
        if not self.backend.available:
            return PageResult("failed", [DestResult(d, "failed", self.backend.detail or "SIP not available")
                                         for d in destinations], message="SIP engine is not available.")
        if not self.backend.running():
            return PageResult("failed", [DestResult(d, "failed", "SIP engine is not running")
                                         for d in destinations], message="SIP engine is not running.")
        if not self._line_lock.acquire(timeout=1.0):
            return PageResult("failed", [], message="Another page is already on the line.")
        try:
            duration = audio.duration_of(wav_path) or 0.0
            with self._lock:
                self._stop = stop
                self._active = {"label": label, "kind": kind, "mode": mode, "audio": wav_path,
                                "duration": duration, "started_at": time.time(), "playing_since": None,
                                "destinations": [{"uri": d, "state": "waiting", "codec": ""}
                                                 for d in destinations]}
            if not self.backend.registration().registered:
                log.warning("paging while not registered; the PBX may reject the call")
            results: list[DestResult] = []
            if mode == "parallel" and len(destinations) > 1:
                slots: list[DestResult | None] = [None] * len(destinations)
                threads = []
                for i, uri in enumerate(destinations):
                    def run(i=i, uri=uri):
                        slots[i] = self._dial_one(i, uri, wav_path, duration, stop)
                    t = threading.Thread(target=run, daemon=True, name=f"page-{i}")
                    t.start()
                    threads.append(t)
                for t in threads:
                    t.join()
                results = [s or DestResult(destinations[i], "failed", "internal error")
                           for i, s in enumerate(slots)]
            else:
                for i, uri in enumerate(destinations):
                    if stop.is_set():
                        results.append(DestResult(uri, "stopped" if stop.reason != "preempted" else "interrupted",
                                                  "Not called: the page was stopped."))
                        continue
                    results.append(self._dial_one(i, uri, wav_path, duration, stop))
            return self._summarise(results, stop)
        finally:
            with self._lock:
                self._active = None
                self._stop = None
            self._line_lock.release()

    def _summarise(self, results: list[DestResult], stop: StopToken) -> PageResult:
        played = [r for r in results if r.outcome == "played"]
        codec = next((r.codec for r in results if r.codec), "")
        if stop.is_set() and not all(r.outcome == "played" for r in results):
            status = "cancelled"
            msg = "Stopped before it finished." if stop.reason != "preempted" else \
                "Interrupted by a higher-priority broadcast."
        elif played and len(played) == len(results):
            status, msg = "success", "Played to every destination."
        elif played:
            status, msg = "partial", f"Played to {len(played)} of {len(results)} destinations."
        else:
            status = "failed"
            msg = results[0].message if len(results) == 1 else "No destination received the audio."
        return PageResult(status, results, codec=codec, message=msg)

    def _set_dest(self, index: int, **fields) -> None:
        with self._lock:
            if self._active and index < len(self._active["destinations"]):
                self._active["destinations"][index].update(fields)

    def _dial_one(self, index: int, uri_in: str, wav_path: str, duration: float, stop: StopToken) -> DestResult:
        cfg = config.get().call
        uri = self.dest_uri(uri_in)
        res = DestResult(uri_in)
        t0 = time.monotonic()
        self._set_dest(index, state="calling")
        try:
            h = self.backend.make_call(uri)
        except SipError as e:
            res.outcome, res.sip_code, res.message = "failed", e.code, f"Could not place the call: {e}"
            self._set_dest(index, state="failed")
            log.error("call to %s could not be placed: %s", uri, e)
            return res
        except Exception as e:  # noqa: BLE001
            res.outcome, res.message = "failed", f"Could not place the call: {e}"
            self._set_dest(index, state="failed")
            log.exception("call to %s could not be placed", uri)
            return res
        self._handles[h.id] = h
        log.info("calling %s", uri)
        try:
            # -- ringing --------------------------------------------------------
            deadline = t0 + cfg.ring_timeout
            while True:
                if h.confirmed.is_set():
                    break
                if h.disconnected.is_set():
                    res.sip_code = h.last_code
                    res.outcome = call_outcome(h.last_code)
                    res.message = f"{CALL_OUTCOME_MESSAGES[res.outcome]} ({h.last_code} {h.last_reason})".strip()
                    self._set_dest(index, state=res.outcome)
                    log.warning("call to %s ended before answer: %s %s", uri, h.last_code, h.last_reason)
                    return res
                if stop.is_set():
                    return self._abort(h, res, index, stop, answered=False)
                if time.monotonic() > deadline:
                    self._hangup_and_wait(h)
                    res.outcome, res.sip_code = "no_answer", h.last_code or 408
                    res.message = f"{CALL_OUTCOME_MESSAGES['no_answer']} (after {cfg.ring_timeout} s)"
                    self._set_dest(index, state="no_answer")
                    log.warning("call to %s not answered within %ss", uri, cfg.ring_timeout)
                    return res
                stop.wait(0.05)
            res.answered = True
            res.sip_code = 200
            res.ring_seconds = round(time.monotonic() - t0, 2)
            t_conn = time.monotonic()
            self._set_dest(index, state="connected")
            log.info("call to %s answered after %.1fs", uri, res.ring_seconds)

            # -- media ----------------------------------------------------------
            media_deadline = time.monotonic() + MEDIA_WAIT_SECONDS
            while not h.media_ready.is_set():
                if h.disconnected.is_set():
                    return self._remote_hangup(h, res, index, t_conn)
                if stop.is_set():
                    return self._abort(h, res, index, stop, answered=True, t_conn=t_conn)
                if time.monotonic() > media_deadline:
                    self._hangup_and_wait(h)
                    res.outcome, res.message = "no_media", CALL_OUTCOME_MESSAGES["no_media"]
                    self._set_dest(index, state="no_media")
                    log.error("call to %s: no active audio media", uri)
                    return res
                stop.wait(0.05)
            res.codec = h.codec
            self._set_dest(index, codec=h.codec)
            log.info("call to %s: audio codec %s", uri, h.codec or "unknown")

            # -- answer delay (lets auto-answer speakers open the audio path) --
            if cfg.answer_delay_ms and stop.wait(cfg.answer_delay_ms / 1000.0):
                return self._abort(h, res, index, stop, answered=True, t_conn=t_conn)
            if h.disconnected.is_set():
                return self._remote_hangup(h, res, index, t_conn)

            # -- playback -------------------------------------------------------
            try:
                self.backend.start_playback(h, wav_path)
            except SipError as e:
                self._hangup_and_wait(h)
                res.outcome, res.message = "failed", f"Audio playback could not start: {e}"
                self._set_dest(index, state="failed")
                log.error("call to %s: playback failed: %s", uri, e)
                return res
            t_play = time.monotonic()
            with self._lock:
                if self._active is not None:
                    self._active["playing_since"] = t_play
            self._set_dest(index, state="playing")
            limit = min(cfg.max_call_seconds, duration + 20) if duration else cfg.max_call_seconds
            while not h.eof.is_set():
                if h.disconnected.is_set():
                    res.played_seconds = round(time.monotonic() - t_play, 1)
                    return self._remote_hangup(h, res, index, t_conn)
                if stop.is_set():
                    res.played_seconds = round(time.monotonic() - t_play, 1)
                    res.rtp = self._stats(h)
                    return self._abort(h, res, index, stop, answered=True, t_conn=t_conn)
                if time.monotonic() - t_play > limit:
                    res.rtp = self._stats(h)
                    self._hangup_and_wait(h)
                    res.outcome, res.message = "timeout", CALL_OUTCOME_MESSAGES["timeout"]
                    res.played_seconds = round(time.monotonic() - t_play, 1)
                    self._set_dest(index, state="timeout")
                    log.error("call to %s exceeded %.0fs of playback; ended", uri, limit)
                    return res
                stop.wait(0.05)
            res.played_seconds = round(time.monotonic() - t_play, 1)
            res.played_to_end = True
            log.info("call to %s: audio finished after %.1fs", uri, res.played_seconds)
            if cfg.hangup_after_eof_ms:
                stop.wait(cfg.hangup_after_eof_ms / 1000.0)
            res.rtp = self._stats(h)
            self._hangup_and_wait(h)
            res.connected_seconds = round(time.monotonic() - t_conn, 1)
            res.outcome, res.message = "played", CALL_OUTCOME_MESSAGES["played"]
            self._set_dest(index, state="played")
            return res
        finally:
            self._handles.pop(h.id, None)
            try:
                self.backend.release(h)
            except Exception:  # noqa: BLE001
                log.exception("releasing call to %s failed", uri)

    def _stats(self, h: CallHandle) -> dict:
        try:
            return self.backend.stream_stats(h)
        except Exception:  # noqa: BLE001
            return {}

    def _hangup_and_wait(self, h: CallHandle) -> None:
        if h.disconnected.is_set():
            return
        try:
            self.backend.hangup(h)
        except Exception as e:  # noqa: BLE001
            log.warning("hangup of %s failed: %s", h.uri, e)
        if not h.disconnected.wait(DISCONNECT_WAIT_SECONDS):
            log.error("call to %s did not confirm disconnect within %ss; releasing anyway",
                      h.uri, DISCONNECT_WAIT_SECONDS)

    def _abort(self, h, res: DestResult, index: int, stop: StopToken, *, answered: bool,
               t_conn: float | None = None) -> DestResult:
        self._hangup_and_wait(h)
        res.outcome = "interrupted" if stop.reason == "preempted" else "stopped"
        res.message = CALL_OUTCOME_MESSAGES[res.outcome]
        if t_conn:
            res.connected_seconds = round(time.monotonic() - t_conn, 1)
        self._set_dest(index, state=res.outcome)
        log.info("call to %s ended: %s (%s)", h.uri, res.outcome, stop.reason)
        return res

    def _remote_hangup(self, h, res: DestResult, index: int, t_conn: float) -> DestResult:
        res.outcome, res.sip_code = "remote_hangup", h.last_code
        res.message = CALL_OUTCOME_MESSAGES["remote_hangup"]
        res.connected_seconds = round(time.monotonic() - t_conn, 1)
        self._set_dest(index, state="remote_hangup")
        log.warning("call to %s: remote side hung up early", h.uri)
        return res

    @staticmethod
    def dest_uri(dst: str) -> str:
        sip = config.get().sip
        dst = dst.strip()
        if dst.startswith(("sip:", "sips:")):
            uri = dst
        elif "@" in dst:
            uri = f"sip:{dst}"
        else:
            host = sip.registrar if sip.port == 5060 or ":" in sip.registrar else f"{sip.registrar}:{sip.port}"
            uri = f"sip:{dst}@{host}"
        if sip.transport in ("tcp", "tls") and "transport=" not in uri:
            uri += f";transport={sip.transport}"
        return uri
