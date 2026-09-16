"""SIP backend interface shared by the real PJSIP backend and the simulator."""
from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, field

_ids = itertools.count(1)


class SipError(Exception):
    """A SIP operation failed; ``code`` is a SIP status code when one is known."""

    def __init__(self, message: str, code: int = 0):
        super().__init__(message)
        self.code = code


@dataclass
class CallHandle:
    uri: str
    id: int = field(default_factory=lambda: next(_ids))
    created: float = field(default_factory=time.monotonic)
    confirmed: threading.Event = field(default_factory=threading.Event)
    media_ready: threading.Event = field(default_factory=threading.Event)
    eof: threading.Event = field(default_factory=threading.Event)
    disconnected: threading.Event = field(default_factory=threading.Event)
    state: str = "calling"
    last_code: int = 0
    last_reason: str = ""
    codec: str = ""
    native: object = None          # backend-private object (pj.Call)


@dataclass
class RegistrationState:
    registered: bool = False
    code: int = 0
    reason: str = ""
    last_success: float | None = None     # wall clock
    last_attempt: float | None = None
    expires: int = 0
    consecutive_failures: int = 0
    next_retry_at: float | None = None
    in_progress: bool = False


REGISTRATION_MESSAGES = {
    0: "Not registered yet.",
    200: "Registered.",
    401: "The PBX rejected the credentials. Check the SIP username, authentication ID and password.",
    403: "The PBX refused this account. Check the password, whether the extension is enabled, and "
         "whether this server's IP address is allowed.",
    404: "The extension was not found on the PBX. Check the SIP username and domain.",
    407: "The proxy rejected the credentials. Check the authentication ID and password.",
    408: "The PBX did not respond. Check the server address, port, transport and firewall.",
    423: "The PBX rejected the registration interval. Try a longer registration expiry.",
    480: "The PBX reported the account as temporarily unavailable.",
    500: "The PBX reported an internal error.",
    502: "Bad gateway between this server and the PBX.",
    503: "The PBX could not be reached (network or transport error).",
    504: "The PBX timed out.",
}


def registration_message(code: int, reason: str = "", sip_host: str = "") -> str:
    low = (reason or "").lower()
    # PJSIP reports local transport problems as 502/503/408 with a descriptive reason.
    if "eresolve" in low or "gethostbyname" in low:
        return "The PBX address could not be found (DNS). Check the server address."
    if "connection refused" in low or "econnrefused" in low:
        return "The PBX refused the connection. Check the port and transport (UDP, TCP or TLS)."
    if ("ssl" in low or "tls" in low) and code >= 500:
        return "The secure (TLS) connection to the PBX failed. Check the port and certificate settings."
    if "unreachable" in low:
        return "The PBX network is unreachable from this server. Check routing and firewalls."
    if code in REGISTRATION_MESSAGES:
        return REGISTRATION_MESSAGES[code]
    if 200 <= code < 300:
        return "Registered."
    return f"Registration failed ({code} {reason})".strip()


def call_outcome(code: int) -> str:
    """Classify a final SIP response for an unanswered call."""
    if code in (486, 600, 603):
        return "busy"
    if code in (408, 480, 487, 0):
        return "no_answer"
    if code in (403, 404, 484, 485, 488, 606):
        return "rejected"
    return "failed"


CALL_OUTCOME_MESSAGES = {
    "played": "Answered and the audio played to the end.",
    "stopped": "Stopped by an operator.",
    "interrupted": "Interrupted by a higher-priority broadcast.",
    "remote_hangup": "The destination hung up before the audio finished.",
    "no_answer": "The destination did not answer.",
    "busy": "The destination was busy.",
    "rejected": "The PBX rejected the call.",
    "failed": "The call failed.",
    "no_media": "The call connected but no audio path was negotiated (codec mismatch?).",
    "timeout": "The call reached the maximum call length and was ended.",
}


class Backend:
    """Interface. All methods may be called from any thread."""
    name = "base"
    available = False
    simulated = False
    detail = ""

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def restart(self) -> None:
        self.stop()
        self.start()

    def running(self) -> bool:
        return False

    def registration(self) -> RegistrationState:
        return RegistrationState()

    def make_call(self, uri: str) -> CallHandle:
        raise SipError("SIP is not available")

    def start_playback(self, handle: CallHandle, wav_path: str) -> None:
        raise SipError("SIP is not available")

    def hangup(self, handle: CallHandle) -> None: ...

    def stream_stats(self, handle: CallHandle) -> dict:
        return {}

    def release(self, handle: CallHandle) -> None: ...

    def live_objects(self) -> dict:
        return {}

    def codecs(self) -> list[dict]:
        return []
