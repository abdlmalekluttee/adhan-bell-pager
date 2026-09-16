"""Configuration: typed models, YAML persistence, sane defaults.

The running configuration for SIP, destinations, the Adhan module and system
settings lives in one YAML file (CONFIG_PATH), exactly as in v1.x so existing
installations keep working. School-bell timetables, users, roles, execution
history and the audit log live in SQLite (see ``db.py``).

Upgrade notes
-------------
* v1.x files load unchanged; new sections take their defaults.
* Destinations gain a stable ``id``. Missing ids are assigned on load and the
  file is rewritten once, after a timestamped backup is taken.
* Secrets (the SIP password) are never returned by the API: use
  ``public_dump()`` for anything that leaves the process.
"""
from __future__ import annotations

import datetime
import logging
import os
import shutil
import threading
import uuid
from typing import Dict, List, Literal, Optional
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

log = logging.getLogger("config")

CONFIG_PATH = os.environ.get("CONFIG_PATH", "/config/config.yaml")
AUDIO_DIR = os.environ.get("AUDIO_DIR", "/audio")
# Parsed manual prayer timetable (keyed by MM-DD), written by the importer.
MANUAL_TIMETABLE_PATH = os.environ.get(
    "MANUAL_TIMETABLE_PATH",
    os.path.join(os.path.dirname(CONFIG_PATH), "manual_timetable.json"),
)

PRAYERS = ["fajr", "dhuhr", "asr", "maghrib", "isha"]


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


class SipConfig(BaseModel):
    enabled: bool = True
    registrar: str = "pbx.example.local"      # SIP domain / registrar host
    proxy: str = ""                            # optional outbound proxy host[:port]
    port: int = Field(5060, ge=1, le=65535)
    transport: Literal["udp", "tcp", "tls"] = "udp"
    username: str = "9000"
    auth_user: str = "9000"                    # auth/realm username (often == username)
    password: str = ""
    display_name: str = "Adhan System"
    register_expires: int = Field(300, ge=60, le=3600)
    local_port: int = Field(5060, ge=0, le=65535)  # local port the UA binds (0 = any)
    rtp_port_start: int = Field(16000, ge=1024, le=65000)
    rtp_port_range: int = Field(100, ge=10, le=10000)
    tls_verify_server: bool = False

    @field_validator("registrar", "username")
    @classmethod
    def _required(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("This field is required.")
        if any(c.isspace() for c in v):
            raise ValueError("Spaces are not allowed.")
        return v


class Destination(BaseModel):
    id: str = Field(default_factory=_new_id)
    name: str = "Paging"
    uri: str = "601"                           # extension number or full sip: URI
    enabled: bool = True

    @field_validator("uri")
    @classmethod
    def _uri(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("Extension or SIP URI is required.")
        if any(c.isspace() for c in v) or any(c in v for c in "<>\"'"):
            raise ValueError("Extension or SIP URI contains invalid characters.")
        return v

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("Name is required.")
        return v[:80]


class Zone(BaseModel):
    """A named group of destinations (e.g. "Classrooms", "Prayer hall")."""
    id: str = Field(default_factory=_new_id)
    name: str
    destination_ids: List[str] = Field(default_factory=list)
    mode: Optional[Literal["sequential", "parallel"]] = None   # None = use call.mode

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("Zone name is required.")
        return v[:80]


class CallConfig(BaseModel):
    mode: Literal["sequential", "parallel"] = "sequential"
    ring_timeout: int = Field(30, ge=5, le=120)          # seconds to wait for answer
    max_call_seconds: int = Field(600, ge=30, le=3600)   # hard safety cap on a single page
    answer_delay_ms: int = Field(1500, ge=0, le=10000)   # pause after answer before audio
    hangup_after_eof_ms: int = Field(800, ge=0, le=10000)  # pause after audio before hangup


class CodecConfig(BaseModel):
    # priority 1..254 (higher = preferred); 0 disables the codec entirely.
    priorities: Dict[str, int] = Field(
        default_factory=lambda: {
            "G722/16000/1": 250,  # wideband, best for Adhan if PBX supports it
            "PCMA/8000/1": 240,   # G.711 a-law
            "PCMU/8000/1": 230,   # G.711 u-law
            "opus/48000/2": 0,
            "speex/16000/1": 0,
            "iLBC/8000/1": 0,
            "GSM/8000/1": 0,
        }
    )

    @field_validator("priorities")
    @classmethod
    def _prio(cls, v: Dict[str, int]) -> Dict[str, int]:
        out = {}
        for k, p in (v or {}).items():
            out[str(k)] = max(0, min(254, int(p)))
        return out


class LocationConfig(BaseModel):
    latitude: float = Field(32.8872, ge=-90, le=90)   # Tripoli, Libya
    longitude: float = Field(13.1913, ge=-180, le=180)
    timezone: str = "Africa/Tripoli"
    # City-picker selection (purely informational + lets the UI restore the choice)
    country_code: str = ""
    country_name: str = ""
    country_local_name: str = ""
    city: str = ""
    city_id: str = ""
    city_local_name: str = ""
    region: str = ""
    use_manual: bool = False

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str) -> str:
        v = (v or "").strip()
        try:
            ZoneInfo(v)
        except Exception:
            raise ValueError(
                f"Invalid timezone '{v}'. Use a valid IANA timezone such as Africa/Tripoli."
            )
        return v

    @model_validator(mode="after")
    def _normalise_mode(self):
        if not self.use_manual and (not self.country_code or not self.city):
            self.use_manual = True
        return self


class IqamaConfig(BaseModel):
    enabled: bool = False
    minutes: int = Field(15, ge=1, le=90)


class PrayerConfig(BaseModel):
    source: Literal["calculated", "manual"] = "calculated"
    method: str = "UMM_AL_QURA"
    madhab: Literal["SHAFI", "HANAFI"] = "SHAFI"
    high_latitude_rule: Literal[
        "MIDDLE_OF_THE_NIGHT", "SEVENTH_OF_THE_NIGHT", "TWILIGHT_ANGLE"
    ] = "MIDDLE_OF_THE_NIGHT"
    adjustments: Dict[str, int] = Field(default_factory=lambda: {p: 0 for p in PRAYERS})
    enabled_prayers: Dict[str, bool] = Field(default_factory=lambda: {p: True for p in PRAYERS})
    iqama: IqamaConfig = Field(default_factory=IqamaConfig)
    iqama_prayers: Dict[str, bool] = Field(default_factory=lambda: {p: True for p in PRAYERS})

    @field_validator("adjustments")
    @classmethod
    def _adj(cls, v: Dict[str, int]) -> Dict[str, int]:
        out = {p: 0 for p in PRAYERS}
        for k, m in (v or {}).items():
            if k in out:
                m = int(m)
                if not -60 <= m <= 60:
                    raise ValueError("Prayer adjustments must be between -60 and 60 minutes.")
                out[k] = m
        return out


class AudioConfig(BaseModel):
    default_file: str = ""
    use_global_for_all: bool = False
    per_prayer: Dict[str, str] = Field(default_factory=lambda: {p: "" for p in PRAYERS})
    iqama_file: str = ""
    iqama_per_prayer: Dict[str, str] = Field(default_factory=lambda: {p: "" for p in PRAYERS})
    target_rate: int = 16000
    gain_db: float = Field(0.0, ge=-30, le=30)

    @field_validator("target_rate")
    @classmethod
    def _rate(cls, v: int) -> int:
        if int(v) not in (8000, 16000, 32000, 44100, 48000):
            raise ValueError("Sample rate must be 8000, 16000, 32000, 44100 or 48000.")
        return int(v)


class AdhanConfig(BaseModel):
    """Where Adhan pages go. Empty zone list = every enabled destination (v1 behaviour)."""
    zone_ids: List[str] = Field(default_factory=list)


class NtpConfig(BaseModel):
    enabled: bool = True
    server: str = "pool.ntp.org"
    sync_interval_min: int = Field(60, ge=5, le=1440)
    set_system_clock: bool = False


class PolicyConfig(BaseModel):
    """Conflict policy between broadcasts. See docs/SCHEDULING.md."""
    adhan_max_delay_seconds: int = Field(300, ge=0, le=1800)
    bells_max_delay_seconds: int = Field(60, ge=0, le=600)
    adhan_late_grace_seconds: int = Field(120, ge=10, le=900)
    bells_late_grace_seconds: int = Field(60, ge=10, le=600)


class WebConfig(BaseModel):
    bind: str = "0.0.0.0"
    port: int = Field(8080, ge=1, le=65535)


class AppConfig(BaseModel):
    sip: SipConfig = Field(default_factory=SipConfig)
    destinations: List[Destination] = Field(
        default_factory=lambda: [Destination(name="Paging Group", uri="601")]
    )
    zones: List[Zone] = Field(default_factory=list)
    call: CallConfig = Field(default_factory=CallConfig)
    codecs: CodecConfig = Field(default_factory=CodecConfig)
    location: LocationConfig = Field(default_factory=LocationConfig)
    prayer: PrayerConfig = Field(default_factory=PrayerConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    adhan: AdhanConfig = Field(default_factory=AdhanConfig)
    ntp: NtpConfig = Field(default_factory=NtpConfig)
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    web: WebConfig = Field(default_factory=WebConfig)

    @model_validator(mode="after")
    def _integrity(self):
        ids = [d.id for d in self.destinations]
        if len(ids) != len(set(ids)):
            raise ValueError("Destination ids must be unique.")
        known = set(ids)
        zids = [z.id for z in self.zones]
        if len(zids) != len(set(zids)):
            raise ValueError("Zone ids must be unique.")
        names = [z.name.lower() for z in self.zones]
        if len(names) != len(set(names)):
            raise ValueError("Zone names must be unique.")
        for z in self.zones:
            z.destination_ids = [i for i in dict.fromkeys(z.destination_ids) if i in known]
        zone_set = set(zids)
        self.adhan.zone_ids = [z for z in dict.fromkeys(self.adhan.zone_ids) if z in zone_set]
        return self


SECTIONS = set(AppConfig.model_fields.keys())

_lock = threading.RLock()
_config: AppConfig | None = None
_listeners: list = []


def on_change(fn) -> None:
    """Register ``fn(section: str)`` to be called after a section is saved."""
    _listeners.append(fn)


def _backup_file(reason: str) -> str | None:
    if not os.path.exists(CONFIG_PATH):
        return None
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bdir = os.path.join(os.path.dirname(CONFIG_PATH), "backups")
    os.makedirs(bdir, exist_ok=True)
    dst = os.path.join(bdir, f"config-{reason}-{stamp}.yaml")
    shutil.copy2(CONFIG_PATH, dst)
    return dst


def load() -> AppConfig:
    """Load config from disk (creating a default file on first run)."""
    global _config
    with _lock:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            needs_upgrade = any(
                isinstance(d, dict) and not d.get("id") for d in raw.get("destinations") or []
            ) or not all(k in raw for k in ("zones", "adhan", "policy"))
            _config = AppConfig(**raw)
            if needs_upgrade:
                b = _backup_file("pre-upgrade")
                _write(_config)
                log.info("config: upgraded %s to the v2 layout (backup: %s)", CONFIG_PATH, b)
        else:
            _config = AppConfig()
            _write(_config)
        return _config


def get() -> AppConfig:
    return _config if _config is not None else load()


def _write(cfg: AppConfig) -> None:
    os.makedirs(os.path.dirname(CONFIG_PATH) or ".", exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg.model_dump(), f, sort_keys=False, allow_unicode=True)
    try:
        os.chmod(tmp, 0o600)   # contains the SIP password
    except OSError:
        pass
    os.replace(tmp, CONFIG_PATH)


def save(cfg: AppConfig) -> AppConfig:
    global _config
    with _lock:
        _config = cfg
        _write(cfg)
        return cfg


def update_section(section: str, data) -> AppConfig:
    """Merge ``data`` into one top-level section, validate and persist."""
    if section not in SECTIONS:
        raise KeyError(section)
    with _lock:
        cfg = get()
        current = cfg.model_dump()
        if isinstance(current[section], list):
            current[section] = data
        elif isinstance(data, dict):
            current[section].update(data)
        else:
            raise ValueError(f"Section '{section}' expects an object.")
        new_cfg = AppConfig(**current)
        save(new_cfg)
    for fn in list(_listeners):
        try:
            fn(section)
        except Exception:  # noqa: BLE001
            log.exception("config listener failed for %s", section)
    return new_cfg


def public_sip(cfg: AppConfig | None = None) -> dict:
    """SIP settings safe to show in the UI: the password is never included."""
    s = (cfg or get()).sip.model_dump()
    s["has_password"] = bool(s.pop("password", ""))
    return s


def timezone() -> str:
    return get().location.timezone


def destination_map(cfg: AppConfig | None = None) -> dict[str, Destination]:
    return {d.id: d for d in (cfg or get()).destinations}


def resolve_targets(zone_ids: list[str] | None, cfg: AppConfig | None = None) -> tuple[list[str], str]:
    """Turn zone ids into (enabled destination URIs, call mode).

    Empty/None zone_ids means "all enabled destinations". Disabled destinations are
    always excluded. When several zones are selected the union is used and the
    call mode is parallel if any selected zone asks for parallel.
    """
    cfg = cfg or get()
    dmap = destination_map(cfg)
    mode = cfg.call.mode
    if not zone_ids:
        return [d.uri for d in cfg.destinations if d.enabled], mode
    zmap = {z.id: z for z in cfg.zones}
    uris: list[str] = []
    modes = set()
    for zid in zone_ids:
        z = zmap.get(zid)
        if not z:
            continue
        if z.mode:
            modes.add(z.mode)
        for did in z.destination_ids:
            d = dmap.get(did)
            if d and d.enabled and d.uri not in uris:
                uris.append(d.uri)
    if modes:
        mode = "parallel" if "parallel" in modes else "sequential"
    return uris, mode
