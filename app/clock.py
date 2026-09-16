"""Single source of "now" for scheduling: NTP-corrected, in the configured timezone.

Tests replace the time source with ``set_source`` so schedules can be exercised
at any date, across midnight or DST changes, without sleeping.
"""
from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

from . import config

_source = None


def set_source(fn) -> None:
    """``fn() -> aware datetime`` or None to restore the NTP-corrected clock."""
    global _source
    _source = fn


def tz() -> ZoneInfo:
    return ZoneInfo(config.get().location.timezone)


def now() -> datetime.datetime:
    zone = tz()
    if _source is not None:
        return _source().astimezone(zone)
    from . import ntp
    return ntp.corrected_now(config.get().location.timezone)


def today() -> datetime.date:
    return now().date()


def at(day: datetime.date, hhmm: str) -> datetime.datetime:
    """Local wall-clock time on ``day`` in the configured zone.

    A time that does not exist (skipped by a DST jump) resolves forward, and an
    ambiguous time (repeated hour) uses its first occurrence, so every bell
    rings exactly once.
    """
    h, m = (int(x) for x in hhmm.split(":")[:2])
    zone = tz()
    naive = datetime.datetime(day.year, day.month, day.day, h, m)
    aware = naive.replace(tzinfo=zone, fold=0)
    roundtrip = aware.astimezone(datetime.timezone.utc).astimezone(zone)
    if roundtrip.replace(tzinfo=None) != naive:
        # Non-existent local time: move past the gap.
        aware = (naive + datetime.timedelta(hours=1)).replace(tzinfo=zone)
    return aware
