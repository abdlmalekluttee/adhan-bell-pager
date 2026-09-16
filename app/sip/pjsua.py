"""PJSIP (pjsua2) backend.

Threading model
---------------
PJSIP is not safe to call from arbitrary Python threads. This backend owns one
dedicated thread that creates the endpoint, polls it with ``libHandleEvents``
(``threadCnt = 0`` and ``mainThreadOnly = True``), and executes every command
submitted by other threads. pjsua2 callbacks therefore run on that same thread
and only update plain Python state and ``threading.Event`` objects.

Registration
------------
PJSIP refreshes the registration before it expires and retries on timeouts and
5xx responses. Responses PJSIP does not retry by itself (401/403/404) are
retried by the supervisor with exponential back-off (30 s doubling to 15 min),
so a wrong password does not hammer the PBX and a fixed one recovers on its own.
"""
from __future__ import annotations

import concurrent.futures
import logging
import os
import queue
import threading
import time

from .. import config
from .base import Backend, CallHandle, RegistrationState, SipError, registration_message

log = logging.getLogger("sip")
pjlog = logging.getLogger("pjsip")

try:
    import pjsua2 as pj
    PJ_AVAILABLE = True
    PJ_IMPORT_ERROR = ""
except Exception as _e:  # noqa: BLE001
    pj = None
    PJ_AVAILABLE = False
    PJ_IMPORT_ERROR = str(_e)

BACKOFF_START = 30
BACKOFF_MAX = 900


def _backoff(failures: int) -> int:
    return min(BACKOFF_MAX, BACKOFF_START * (2 ** max(0, failures - 1)))


if PJ_AVAILABLE:
    class _LogWriter(pj.LogWriter):
        def write(self, entry):  # noqa: D401
            msg = (entry.msg or "").rstrip()
            if not msg:
                return
            if entry.level <= 1:
                pjlog.error(msg)
            elif entry.level == 2:
                pjlog.warning(msg)
            else:
                pjlog.debug(msg)

    class _Player(pj.AudioMediaPlayer):
        def __init__(self, handle: CallHandle):
            super().__init__()
            self.handle = handle

        def onEof2(self):  # noqa: N802
            self.handle.eof.set()

    class _Call(pj.Call):
        def __init__(self, acc, handle: CallHandle):
            super().__init__(acc)
            self.handle = handle

        def onCallState(self, prm):  # noqa: N802
            h = self.handle
            try:
                ci = self.getInfo()
            except Exception:  # noqa: BLE001
                return
            h.state = ci.stateText
            h.last_code = int(ci.lastStatusCode or 0)
            h.last_reason = ci.lastReason or ""
            if ci.state == pj.PJSIP_INV_STATE_CONFIRMED:
                h.confirmed.set()
            elif ci.state == pj.PJSIP_INV_STATE_DISCONNECTED:
                h.disconnected.set()

        def onCallMediaState(self, prm):  # noqa: N802
            h = self.handle
            try:
                ci = self.getInfo()
                for i, mi in enumerate(ci.media):
                    if mi.type == pj.PJMEDIA_TYPE_AUDIO and mi.status == pj.PJSUA_CALL_MEDIA_ACTIVE:
                        h.native["media_index"] = i
                        try:
                            si = self.getStreamInfo(i)
                            name = getattr(si, "codecName", "") or ""
                            rate = getattr(si, "codecClockRate", 0) or 0
                            h.codec = f"{name}/{rate}" if rate else name
                        except Exception:  # noqa: BLE001
                            pass
                        h.media_ready.set()
                        return
            except Exception:  # noqa: BLE001
                log.exception("media state callback failed")

    class _Account(pj.Account):
        def __init__(self, backend: "PjsuaBackend"):
            super().__init__()
            self.backend = backend

        def onRegState(self, prm):  # noqa: N802
            self.backend._on_reg_state(self, prm)


class PjsuaBackend(Backend):
    name = "pjsip"

    def __init__(self):
        self.available = PJ_AVAILABLE
        self.detail = "" if PJ_AVAILABLE else f"PJSIP Python bindings are not installed ({PJ_IMPORT_ERROR})"
        self._thread: threading.Thread | None = None
        self._cmds: queue.Queue = queue.Queue()
        self._stopping = threading.Event()
        self._ready = threading.Event()
        self._start_error: Exception | None = None
        self._reg = RegistrationState()
        self._ep = None
        self._acc = None
        self._writer = None
        self._calls: dict[int, CallHandle] = {}
        self._players = 0
        self._codecs: list[dict] = []
        self._reg_logged: tuple | None = None
        self._life_lock = threading.Lock()      # serialises start/stop
        self.starting = False

    # ------------------------------------------------------------ lifecycle
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self._ready.is_set()
                    and self._start_error is None)

    def start(self) -> None:
        if not self.available:
            log.warning("SIP backend unavailable: %s", self.detail)
            return
        with self._life_lock:
            if self._thread and self._thread.is_alive():
                return
            self.starting = True
            try:
                self._start_locked()
            finally:
                self.starting = False

    def _start_locked(self) -> None:
        self._stopping.clear()
        self._ready.clear()
        self._start_error = None
        self._reg = RegistrationState()
        self._thread = threading.Thread(target=self._run, name="pjsua", daemon=True)
        self._thread.start()
        if not self._ready.wait(20):
            log.error("SIP endpoint did not start within 20 s")
        if self._start_error:
            self.detail = f"SIP endpoint failed to start: {self._start_error}"
            raise SipError(self.detail)
        self.detail = ""

    def stop(self) -> None:
        with self._life_lock:
            t = self._thread
            if not t:
                return
            self._stopping.set()
            t.join(15)
            if t.is_alive():
                log.error("SIP thread did not stop within 15 s")
            self._thread = None
            self._reg = RegistrationState()

    def _run(self) -> None:
        try:
            self._create()
        except Exception as e:  # noqa: BLE001
            self._start_error = e
            log.exception("SIP endpoint creation failed")
            self._destroy()
            self._ready.set()
            return
        self._ready.set()
        last_tick = 0.0
        while not self._stopping.is_set():
            try:
                self._ep.libHandleEvents(20)
            except Exception:  # noqa: BLE001
                log.exception("libHandleEvents failed")
                time.sleep(0.05)
            self._drain()
            now = time.monotonic()
            if now - last_tick >= 1.0:
                last_tick = now
                self._supervise()
        self._drain(reject=True)
        self._destroy()
        log.info("SIP endpoint stopped")

    def _drain(self, reject: bool = False) -> None:
        while True:
            try:
                fn, fut = self._cmds.get_nowait()
            except queue.Empty:
                return
            if reject:
                fut.set_exception(SipError("SIP engine is stopping"))
                continue
            try:
                fut.set_result(fn())
            except BaseException as e:  # noqa: BLE001
                fut.set_exception(e)

    def _submit(self, fn, timeout: float = 10.0):
        if threading.current_thread() is self._thread:
            return fn()
        if not self.running():
            raise SipError("SIP engine is not running", 503)
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self._cmds.put((fn, fut))
        try:
            return fut.result(timeout)
        except concurrent.futures.TimeoutError as e:
            raise SipError("SIP engine did not respond in time") from e
        except SipError:
            raise
        except Exception as e:  # noqa: BLE001
            raise SipError(_pj_message(e)) from e

    # --------------------------------------------------------------- create
    def _create(self) -> None:
        from .. import __version__
        cfg = config.get()
        sip = cfg.sip
        ep = pj.Endpoint()
        ep.libCreate()
        epc = pj.EpConfig()
        epc.uaConfig.threadCnt = 0
        epc.uaConfig.mainThreadOnly = True
        epc.uaConfig.maxCalls = 16
        epc.uaConfig.userAgent = f"AdhanBellPager/{__version__}"
        level = int(os.environ.get("SIP_LOG_LEVEL", "3"))
        self._writer = _LogWriter()
        epc.logConfig.writer = self._writer
        epc.logConfig.level = level
        epc.logConfig.consoleLevel = level
        epc.logConfig.msgLogging = level >= 4
        epc.medConfig.noVad = True
        epc.medConfig.ecTailLen = 0
        ep.libInit(epc)
        self._ep = ep

        ttype = {"udp": pj.PJSIP_TRANSPORT_UDP, "tcp": pj.PJSIP_TRANSPORT_TCP,
                 "tls": pj.PJSIP_TRANSPORT_TLS}[sip.transport]
        tcfg = pj.TransportConfig()
        tcfg.port = sip.local_port
        if sip.transport == "tls":
            tcfg.tlsConfig.verifyServer = bool(sip.tls_verify_server)
        try:
            ep.transportCreate(ttype, tcfg)
        except Exception as e:  # noqa: BLE001
            raise SipError(f"Could not open {sip.transport.upper()} port {sip.local_port}: {_pj_message(e)}") from e
        ep.libStart()
        try:
            ep.audDevManager().setNullDev()
        except Exception as e:  # noqa: BLE001
            log.warning("could not select the null audio device: %s", _pj_message(e))
        self._apply_codecs(cfg.codecs.priorities)
        if sip.enabled:
            self._create_account()
        log.info("SIP endpoint started (%s, local port %s)", sip.transport.upper(), sip.local_port)

    def _apply_codecs(self, priorities: dict[str, int]) -> None:
        wanted = {k.lower(): v for k, v in priorities.items()}
        out = []
        for ci in self._ep.codecEnum2():
            cid = ci.codecId
            prio = wanted.get(cid.lower())
            if prio is None:
                base = cid.split("/")[0].lower()
                prio = next((v for k, v in wanted.items() if k.split("/")[0] == base
                             and k.split("/")[1:2] == cid.lower().split("/")[1:2]), 0)
            try:
                self._ep.codecSetPriority(cid, int(prio))
            except Exception as e:  # noqa: BLE001
                log.debug("codec %s priority not set: %s", cid, e)
            out.append({"id": cid, "priority": int(prio)})
        self._codecs = sorted(out, key=lambda c: -c["priority"])
        enabled = [c["id"] for c in self._codecs if c["priority"] > 0]
        log.info("codecs offered (in order): %s", ", ".join(enabled) or "none")
        if not enabled:
            log.error("no codecs are enabled; calls cannot carry audio")

    def _create_account(self) -> None:
        sip = config.get().sip
        tparam = f";transport={sip.transport}" if sip.transport in ("tcp", "tls") else ""
        default_port = 5061 if sip.transport == "tls" else 5060
        host = sip.registrar if (sip.port == default_port or ":" in sip.registrar) else f"{sip.registrar}:{sip.port}"
        display = "".join(ch for ch in sip.display_name if ch not in '"\\\r\n<>').strip()
        acfg = pj.AccountConfig()
        acfg.idUri = f'"{display}" <sip:{sip.username}@{sip.registrar}>' if display else \
            f"sip:{sip.username}@{sip.registrar}"
        acfg.regConfig.registrarUri = f"sip:{host}{tparam}"
        acfg.regConfig.timeoutSec = sip.register_expires
        acfg.regConfig.retryIntervalSec = 60
        acfg.regConfig.firstRetryIntervalSec = 15
        acfg.regConfig.randomRetryIntervalSec = 10
        acfg.regConfig.dropCallsOnFail = False
        if sip.proxy:
            proxy = sip.proxy if sip.proxy.startswith("sip:") else f"sip:{sip.proxy}"
            acfg.sipConfig.proxies.append(f"{proxy};lr{tparam}")
        acfg.sipConfig.authCreds.append(
            pj.AuthCredInfo("digest", "*", sip.auth_user or sip.username, 0, sip.password))
        acfg.mediaConfig.transportConfig.port = sip.rtp_port_start
        acfg.mediaConfig.transportConfig.portRange = sip.rtp_port_range
        acc = _Account(self)
        self._reg.last_attempt = time.time()
        self._reg.in_progress = True
        acc.create(acfg)
        self._acc = acc
        log.info("registering %s via %s", acfg.idUri, acfg.regConfig.registrarUri)

    def _destroy(self) -> None:
        ep = self._ep
        if ep is None:
            return
        try:
            if self._calls:
                ep.hangupAllCalls()
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline and any(not h.disconnected.is_set()
                                                          for h in self._calls.values()):
                    ep.libHandleEvents(20)
            for h in list(self._calls.values()):
                h.disconnected.set()
                h.native = None
            self._calls.clear()
            if self._acc is not None:
                try:
                    self._acc.shutdown()
                except Exception:  # noqa: BLE001
                    pass
                self._acc = None
            ep.libDestroy()
        except Exception:  # noqa: BLE001
            log.exception("SIP endpoint destroy failed")
        finally:
            self._ep = None
            self._reg = RegistrationState()

    # ---------------------------------------------------------- registration
    def _on_reg_state(self, acc, prm) -> None:
        r = self._reg
        try:
            info = acc.getInfo()
            active = bool(info.regIsActive)
            expires = int(info.regExpiresSec)
        except Exception:  # noqa: BLE001
            active, expires = False, 0
        code = int(prm.code or 0)
        r.in_progress = False
        r.code, r.reason, r.expires = code, prm.reason or "", expires
        was = r.registered
        r.registered = active and 200 <= code < 300
        if r.registered:
            r.last_success = time.time()
            r.consecutive_failures = 0
            r.next_retry_at = None
            if not was:
                log.info("SIP registered (expires in %ss)", expires)
            else:
                log.debug("SIP registration renewed (expires in %ss)", expires)
        elif 200 <= code < 300:
            log.info("SIP unregistered")
        else:
            r.consecutive_failures += 1
            wait = _backoff(r.consecutive_failures)
            r.next_retry_at = time.time() + wait
            key = (code, r.consecutive_failures if r.consecutive_failures <= 3 else "many")
            msg = registration_message(code, r.reason, sip_host=config.get().sip.registrar)
            if was or key != self._reg_logged or r.consecutive_failures % 10 == 0:
                log.warning("SIP registration failed: %s %s — %s Next attempt in %ss (failure #%d)",
                            code, r.reason, msg, wait, r.consecutive_failures)
            self._reg_logged = key

    def _supervise(self) -> None:
        r = self._reg
        acc = self._acc
        if acc is None or r.registered or r.in_progress:
            return
        if r.next_retry_at and time.time() >= r.next_retry_at:
            r.last_attempt = time.time()
            r.in_progress = True
            r.next_retry_at = time.time() + _backoff(r.consecutive_failures + 1)
            try:
                acc.setRegistration(True)
                log.info("retrying SIP registration")
            except Exception as e:  # noqa: BLE001
                r.in_progress = False
                log.debug("registration retry deferred: %s", _pj_message(e))

    def registration(self) -> RegistrationState:
        return self._reg

    # ----------------------------------------------------------------- calls
    def make_call(self, uri: str) -> CallHandle:
        def do():
            if self._acc is None:
                raise SipError("No SIP account is configured", 503)
            h = CallHandle(uri=uri)
            call = _Call(self._acc, h)
            h.native = {"call": call, "player": None, "media_index": None}
            prm = pj.CallOpParam(True)
            prm.opt.audioCount = 1
            prm.opt.videoCount = 0
            try:
                call.makeCall(uri, prm)
            except Exception as e:  # noqa: BLE001
                h.native = None
                raise SipError(_pj_message(e)) from e
            self._calls[h.id] = h
            return h
        return self._submit(do)

    def start_playback(self, handle: CallHandle, wav_path: str) -> None:
        def do():
            n = handle.native
            if not n or handle.disconnected.is_set():
                raise SipError("The call has already ended")
            idx = n.get("media_index")
            if idx is None:
                raise SipError("The call has no active audio")
            player = _Player(handle)
            try:
                player.createPlayer(wav_path, pj.PJMEDIA_FILE_NO_LOOP)
                player.startTransmit(n["call"].getAudioMedia(idx))
            except Exception as e:  # noqa: BLE001
                raise SipError(_pj_message(e)) from e
            n["player"] = player
            self._players += 1
        self._submit(do)

    def hangup(self, handle: CallHandle) -> None:
        def do():
            n = handle.native
            if not n or handle.disconnected.is_set():
                return
            try:
                n["call"].hangup(pj.CallOpParam())
            except Exception as e:  # noqa: BLE001
                log.debug("hangup: %s", _pj_message(e))
        self._submit(do)

    def stream_stats(self, handle: CallHandle) -> dict:
        def do():
            n = handle.native
            if not n or n.get("media_index") is None or handle.disconnected.is_set():
                return {}
            ss = n["call"].getStreamStat(n["media_index"])
            tx, rx = ss.rtcp.txStat, ss.rtcp.rxStat
            return {"tx_packets": int(tx.pkt), "tx_bytes": int(tx.bytes), "rx_packets": int(rx.pkt),
                    "rx_lost": int(rx.loss)}
        try:
            return self._submit(do)
        except SipError:
            return {}

    def release(self, handle: CallHandle) -> None:
        def do():
            n = handle.native
            self._calls.pop(handle.id, None)
            if not n:
                return
            player = n.get("player")
            if player is not None:
                if not handle.disconnected.is_set() and n.get("media_index") is not None:
                    try:
                        player.stopTransmit(n["call"].getAudioMedia(n["media_index"]))
                    except Exception:  # noqa: BLE001
                        pass
                n["player"] = None
                self._players -= 1
                del player
            if not handle.disconnected.is_set():
                try:
                    n["call"].hangup(pj.CallOpParam())
                except Exception:  # noqa: BLE001
                    pass
            handle.native = None
        try:
            self._submit(do)
        except SipError:
            handle.native = None
            self._calls.pop(handle.id, None)

    def live_objects(self) -> dict:
        return {"calls": len(self._calls), "players": self._players}

    def codecs(self) -> list[dict]:
        return list(self._codecs)


def _pj_message(e: Exception) -> str:
    for attr in ("reason", "title"):
        v = getattr(e, attr, None)
        if v:
            return str(v)
    info = getattr(e, "info", None)
    if callable(info):
        try:
            return str(info())
        except Exception:  # noqa: BLE001
            pass
    return str(e) or type(e).__name__
