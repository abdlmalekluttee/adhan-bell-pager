"""Simulated SIP backend.

Used by the automated tests and, when ``SIP_SIMULATE=1`` is set, for trying the
interface on a machine without PJSIP. The UI shows a clear "simulation" badge so
it can never be mistaken for real paging.
"""
from __future__ import annotations

import threading
import time

from .. import audio
from .base import Backend, CallHandle, RegistrationState, SipError


class SimBackend(Backend):
    name = "simulator"
    available = True
    simulated = True

    def __init__(self, time_scale: float = 1.0):
        self.time_scale = time_scale
        self.behaviours: dict[str, dict] = {}     # uri substring -> behaviour
        self.default = {"answer_after": 0.2, "media_after": 0.05, "busy": False, "no_answer": False,
                        "fail_code": 0, "remote_hangup_after": None, "media": True, "codec": "PCMA/8000"}
        self.register_code = 200
        self._running = False
        self._reg = RegistrationState()
        self._lock = threading.Lock()
        self.calls: list[CallHandle] = []
        self.live: dict[int, CallHandle] = {}
        self.players = 0
        self.log: list[tuple] = []

    # lifecycle ------------------------------------------------------------ #
    def start(self) -> None:
        with self._lock:
            self._running = True
            self._reg.last_attempt = time.time()
            self._reg.code = self.register_code
            self._reg.registered = 200 <= self.register_code < 300
            if self._reg.registered:
                self._reg.last_success = time.time()
                self._reg.consecutive_failures = 0
                self._reg.expires = 300
            else:
                self._reg.consecutive_failures += 1
            self.log.append(("start", self.register_code))

    def stop(self) -> None:
        with self._lock:
            for h in list(self.live.values()):
                self._disconnect(h, 503, "shutdown")
            self._running = False
            self._reg = RegistrationState()
            self.log.append(("stop",))

    def running(self) -> bool:
        return self._running

    def registration(self) -> RegistrationState:
        return self._reg

    def set_registration(self, code: int) -> None:
        self.register_code = code
        with self._lock:
            self._reg.code = code
            self._reg.registered = 200 <= code < 300
            self._reg.last_attempt = time.time()
            if self._reg.registered:
                self._reg.last_success = time.time()
                self._reg.consecutive_failures = 0
            else:
                self._reg.consecutive_failures += 1

    # calls ---------------------------------------------------------------- #
    def _behaviour(self, uri: str) -> dict:
        b = dict(self.default)
        for key, extra in self.behaviours.items():
            if key in uri:
                b.update(extra)
        return b

    def _after(self, seconds: float, fn) -> None:
        t = threading.Timer(max(0.0, seconds * self.time_scale), fn)
        t.daemon = True
        t.start()

    def make_call(self, uri: str) -> CallHandle:
        if not self._running:
            raise SipError("SIP engine is not running", 503)
        b = self._behaviour(uri)
        h = CallHandle(uri=uri)
        with self._lock:
            self.calls.append(h)
            self.live[h.id] = h
        self.log.append(("call", uri))
        if b["fail_code"]:
            self._after(0.05, lambda: self._disconnect(h, b["fail_code"], "failed"))
        elif b["busy"]:
            self._after(0.1, lambda: self._disconnect(h, 486, "Busy Here"))
        elif not b["no_answer"]:
            def answer():
                if h.disconnected.is_set():
                    return
                h.state = "confirmed"
                h.last_code = 200
                h.confirmed.set()
                if b["media"]:
                    def media():
                        h.codec = b["codec"]
                        h.media_ready.set()
                    self._after(b["media_after"], media)
                if b["remote_hangup_after"] is not None:
                    self._after(b["remote_hangup_after"], lambda: self._disconnect(h, 200, "BYE"))
            self._after(b["answer_after"], answer)
        return h

    def start_playback(self, handle: CallHandle, wav_path: str) -> None:
        if handle.disconnected.is_set():
            raise SipError("call already ended")
        dur = audio.duration_of(wav_path)
        if dur is None:
            raise SipError(f"cannot open audio file {wav_path}")
        with self._lock:
            self.players += 1
        handle.native = {"started": time.monotonic(), "dur": dur}
        self.log.append(("play", handle.uri, wav_path))
        self._after(dur, handle.eof.set)

    def _disconnect(self, h: CallHandle, code: int, reason: str) -> None:
        if h.disconnected.is_set():
            return
        h.last_code = code if not h.confirmed.is_set() or code >= 300 else h.last_code or 200
        h.last_reason = reason
        h.state = "disconnected"
        h.disconnected.set()

    def hangup(self, handle: CallHandle) -> None:
        self.log.append(("hangup", handle.uri))
        code = 487 if not handle.confirmed.is_set() else 200
        self._after(0.02, lambda: self._disconnect(handle, code, "Request Terminated" if code == 487 else "BYE"))

    def stream_stats(self, handle: CallHandle) -> dict:
        n = handle.native or {}
        played = time.monotonic() - n.get("started", time.monotonic())
        pkts = int(played / max(self.time_scale, 1e-6) * 50)
        return {"tx_packets": pkts, "tx_bytes": pkts * 160, "rx_packets": pkts, "simulated": True}

    def release(self, handle: CallHandle) -> None:
        with self._lock:
            if self.live.pop(handle.id, None) is not None and handle.native:
                self.players -= 1

    def live_objects(self) -> dict:
        return {"calls": len(self.live), "players": self.players}

    def codecs(self) -> list[dict]:
        return [{"id": c, "priority": p} for c, p in
                (("G722/16000/1", 250), ("PCMA/8000/1", 240), ("PCMU/8000/1", 230), ("opus/48000/2", 0))]
