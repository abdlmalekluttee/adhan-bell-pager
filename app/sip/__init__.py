"""SIP paging. ``get_engine()`` returns the process-wide paging engine."""
from __future__ import annotations

import os
import threading

from .base import Backend, CallHandle, RegistrationState, SipError  # noqa: F401
from .engine import PageResult, PagingEngine, StopToken  # noqa: F401

_engine: PagingEngine | None = None
_lock = threading.Lock()


def _make_backend() -> Backend:
    if os.environ.get("SIP_SIMULATE", "").strip().lower() in ("1", "true", "yes"):
        from .sim import SimBackend
        return SimBackend(time_scale=float(os.environ.get("SIP_SIMULATE_SCALE", "1") or 1))
    from .pjsua import PjsuaBackend
    return PjsuaBackend()


def get_engine() -> PagingEngine:
    global _engine
    with _lock:
        if _engine is None:
            _engine = PagingEngine(_make_backend())
        return _engine


def set_engine(engine: PagingEngine | None) -> None:
    """Testing hook."""
    global _engine
    with _lock:
        _engine = engine
