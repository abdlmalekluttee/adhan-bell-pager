import datetime
import os
import sys
import tempfile

import pytest

_BOOT = tempfile.mkdtemp(prefix="abp-boot-")
os.environ.setdefault("CONFIG_PATH", os.path.join(_BOOT, "config", "config.yaml"))
os.environ.setdefault("AUDIO_DIR", os.path.join(_BOOT, "audio"))
os.environ.setdefault("LOG_PATH", os.path.join(_BOOT, "test.log"))
os.environ["SIP_SIMULATE"] = "1"
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import accounts, audio, clock, config, db, directory, dispatcher, rbac, scheduler, security, sip  # noqa: E402
from app.sip.engine import PagingEngine  # noqa: E402
from app.sip.sim import SimBackend  # noqa: E402

TZ = "Africa/Tripoli"
collect_ignore = ["live"]      # live tests place real calls; run them by hand


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Fresh config, database, audio library and singletons for each test."""
    cfg_dir = tmp_path / "config"
    audio_dir = tmp_path / "audio"
    cfg_dir.mkdir()
    audio_dir.mkdir()
    monkeypatch.setattr(config, "CONFIG_PATH", str(cfg_dir / "config.yaml"))
    monkeypatch.setattr(config, "AUDIO_DIR", str(audio_dir))
    monkeypatch.setattr(config, "MANUAL_TIMETABLE_PATH", str(cfg_dir / "manual_timetable.json"))
    monkeypatch.setattr(config, "_config", None)
    monkeypatch.setattr(config, "_listeners", [])
    monkeypatch.setenv("SECRET_KEY_PATH", str(cfg_dir / "secret.key"))
    monkeypatch.delenv("USERS_PATH", raising=False)
    security.reset_key_cache()
    accounts.reset_throttle()
    cfg = config.load()
    cfg.ntp.enabled = False
    cfg.call.answer_delay_ms = 0
    cfg.call.hangup_after_eof_ms = 0
    cfg.call.ring_timeout = 5
    config.save(cfg)
    db.open_db(str(cfg_dir / "app.db"))
    accounts.bootstrap()
    audio.ensure_builtin_bells()
    backend = SimBackend(time_scale=0.01)
    engine = PagingEngine(backend)
    sip.set_engine(engine)
    engine.start()
    d = dispatcher.Dispatcher()
    dispatcher.set_dispatcher(d)
    d.start()
    s = scheduler.Scheduler()
    scheduler.set_scheduler(s)
    directory.set_connection_factory(None)
    clock.set_source(None)
    yield {"tmp": tmp_path, "backend": backend, "engine": engine, "dispatcher": d, "scheduler": s}
    s.shutdown()
    d.shutdown()
    clock.set_source(None)
    directory.set_connection_factory(None)


class FakeClock:
    def __init__(self, start: datetime.datetime):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now = self.now + datetime.timedelta(**kw)
        return self.now


@pytest.fixture
def fake_clock(env):
    from zoneinfo import ZoneInfo
    fc = FakeClock(datetime.datetime(2026, 9, 14, 7, 0, tzinfo=ZoneInfo(TZ)))   # a Monday
    clock.set_source(fc)
    return fc


@pytest.fixture
def client(env):
    from fastapi.testclient import TestClient

    from app import main
    main.app.router.lifespan_context = _noop_lifespan
    with TestClient(main.app) as c:
        yield c


from contextlib import asynccontextmanager  # noqa: E402


@asynccontextmanager
async def _noop_lifespan(app):
    yield


def login(client, username, password):
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"x-csrf-token": client.cookies.get(accounts.CSRF_COOKIE)}


def make_user(username, *, role_keys=(), perms=None, password="Correct-Horse-9"):
    role_ids = [rbac.role_id_by_key(k) for k in role_keys]
    if perms is not None:
        now = db.now()
        rid = db.execute("INSERT INTO roles (name, description, builtin, permissions, created_at, updated_at) "
                         "VALUES (?, '', 0, ?, ?, ?)", (f"custom-{username}", db.dumps(list(perms)), now, now)).lastrowid
        role_ids.append(rid)
    return accounts.create_user(username, password=password, must_change_password=False, role_ids=role_ids)


def admin_login(client):
    u = accounts.get_by_username("admin")
    accounts.set_password(u["id"], "Admin-Pass-123")
    return login(client, "admin", "Admin-Pass-123")
