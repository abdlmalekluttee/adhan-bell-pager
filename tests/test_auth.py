"""Local authentication, sessions, CSRF, lockout, and v1 upgrade."""
import json
import os

import bcrypt
import yaml
from conftest import admin_login, login, make_user

from app import accounts, config, db, rbac, security


def test_first_run_forces_password_change(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert r.status_code == 200 and r.json()["user"]["must_change_password"]
    assert client.get("/api/dashboard").status_code == 403
    h = {"x-csrf-token": client.cookies.get(accounts.CSRF_COOKIE)}
    r = client.post("/api/auth/change-password", json={"current_password": "admin", "new_password": "admin"},
                    headers=h)
    assert r.status_code == 400
    r = client.post("/api/auth/change-password", json={"current_password": "admin", "new_password": "N3w-Password"},
                    headers=h)
    assert r.status_code == 200
    assert client.get("/api/dashboard").status_code == 200


def test_wrong_password_and_lockout(client):
    make_user("user1")
    for _ in range(5):
        assert client.post("/api/auth/login", json={"username": "user1", "password": "nope"}).status_code == 401
    r = client.post("/api/auth/login", json={"username": "user1", "password": "Correct-Horse-9"})
    assert r.status_code == 429


def test_unknown_user_same_error(client):
    r = client.post("/api/auth/login", json={"username": "ghost", "password": "whatever1"})
    assert r.status_code == 401 and r.json()["detail"]["message"] == "Invalid username or password."


def test_csrf_and_logout_revokes_session(client):
    h = admin_login(client)
    assert client.post("/api/auth/preferences", json={"theme": "dark"}).status_code == 403
    assert client.post("/api/auth/preferences", json={"theme": "dark"}, headers={"x-csrf-token": "x"}).status_code == 403
    assert client.post("/api/auth/preferences", json={"theme": "dark"}, headers=h).json()["user"]["theme"] == "dark"
    token = client.cookies.get(accounts.SESSION_COOKIE)
    assert client.post("/api/auth/logout", headers=h).status_code == 200
    client.cookies.set(accounts.SESSION_COOKIE, token)
    assert client.get("/api/dashboard").status_code == 401


def test_login_requires_json(client):
    r = client.post("/api/auth/login", data={"username": "admin", "password": "admin"})
    assert r.status_code == 415


def test_disabled_user_cannot_sign_in_and_sessions_end(client):
    u = make_user("temp", role_keys=["viewer"])
    login(client, "temp", "Correct-Horse-9")
    assert client.get("/api/bells/day").status_code == 200
    accounts.update_user(u["id"], disabled=True)
    assert client.get("/api/bells/day").status_code == 401
    r = client.post("/api/auth/login", json={"username": "temp", "password": "Correct-Horse-9"})
    assert r.status_code == 403


def test_user_without_roles_has_no_access(client):
    accounts.create_user("noroles", password="Correct-Horse-9", must_change_password=False)
    r = client.post("/api/auth/login", json={"username": "noroles", "password": "Correct-Horse-9"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "no_access"


def test_session_expiry(client):
    make_user("exp", role_keys=["viewer"])
    login(client, "exp", "Correct-Horse-9")
    db.execute("UPDATE sessions SET expires_at = ?", (db.now() - 1,))
    assert client.get("/api/bells/day").status_code == 401


def test_password_hash_and_long_passwords():
    h = security.hash_password("x" * 100)
    assert security.verify_password("x" * 100, h)
    assert not security.verify_password("x" * 99 + "y", h)


def test_upgrade_from_v1(tmp_path, monkeypatch):
    """A v1.7 config directory upgrades in place, keeping settings, login and files."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    (audio_dir / "makkah.wav").write_bytes(b"RIFF0000WAVE")
    v1 = {
        "sip": {"enabled": True, "registrar": "192.0.2.10", "proxy": "", "port": 5060, "transport": "udp",
                "username": "3001", "auth_user": "3001", "password": "pbx-secret", "display_name": "Adhan System",
                "register_expires": 300, "local_port": 5060},
        "destinations": [{"name": "Hall", "uri": "4090", "enabled": True}],
        "call": {"mode": "sequential", "ring_timeout": 30, "max_call_seconds": 600, "answer_delay_ms": 1500,
                 "hangup_after_eof_ms": 800},
        "codecs": {"priorities": {"PCMA/8000/1": 240, "PCMU/8000/1": 230, "G722/16000/1": 250}},
        "location": {"latitude": 32.8872, "longitude": 13.1913, "timezone": "Africa/Tripoli", "country_code": "LY",
                     "city": "Tripoli", "use_manual": False},
        "prayer": {"source": "calculated", "method": "EGYPTIAN", "madhab": "SHAFI",
                   "high_latitude_rule": "MIDDLE_OF_THE_NIGHT", "adjustments": {"fajr": 2},
                   "enabled_prayers": {"fajr": True, "dhuhr": True, "asr": False, "maghrib": True, "isha": True},
                   "iqama": {"enabled": True, "minutes": 10}},
        "audio": {"default_file": "makkah.wav", "per_prayer": {"fajr": ""}, "target_rate": 16000, "gain_db": 0},
        "ntp": {"enabled": False, "server": "pool.ntp.org", "sync_interval_min": 60, "set_system_clock": False},
        "web": {"bind": "0.0.0.0", "port": 8080},
    }
    (cfg_dir / "config.yaml").write_text(yaml.safe_dump(v1), encoding="utf-8")
    old_hash = bcrypt.hashpw(b"imam-password", bcrypt.gensalt()).decode()
    (cfg_dir / "users.json").write_text(json.dumps({"secret": "abc", "users": {"imam": {
        "username": "imam", "password_hash": old_hash, "must_change_password": False, "language": "ar",
        "theme": "dark", "token_version": 3, "last_login": 1700000000}}}), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", str(cfg_dir / "config.yaml"))
    monkeypatch.setattr(config, "AUDIO_DIR", str(audio_dir))
    monkeypatch.setattr(config, "_config", None)
    monkeypatch.setenv("SECRET_KEY_PATH", str(cfg_dir / "secret.key"))
    security.reset_key_cache()

    cfg = config.load()
    assert cfg.sip.password == "pbx-secret" and cfg.sip.registrar == "192.0.2.10"
    assert cfg.destinations[0].uri == "4090" and cfg.destinations[0].id
    assert cfg.prayer.method == "EGYPTIAN" and cfg.prayer.adjustments["fajr"] == 2
    assert cfg.prayer.enabled_prayers["asr"] is False and cfg.prayer.iqama.minutes == 10
    assert cfg.audio.default_file == "makkah.wav"
    backups = os.listdir(cfg_dir / "backups")
    assert len(backups) == 1 and backups[0].startswith("config-pre-upgrade-")
    assert yaml.safe_load((cfg_dir / "backups" / backups[0]).read_text())["destinations"][0].get("id") is None
    again = config.load()
    assert again.destinations[0].id == cfg.destinations[0].id
    assert len(os.listdir(cfg_dir / "backups")) == 1       # upgraded once

    db.open_db(str(cfg_dir / "app.db"))
    accounts.bootstrap()
    u = accounts.verify_local("imam", "imam-password")
    assert u and u["is_recovery"] and u["language"] == "ar" and u["theme"] == "dark"
    assert rbac.effective_for_user(u).is_full_admin
    assert (cfg_dir / "users.json.migrated").exists() and not (cfg_dir / "users.json").exists()
    assert accounts.get_by_username("admin") is None       # no default account injected
    accounts.bootstrap()                                   # idempotent
    assert len(db.query("SELECT * FROM users")) == 1


def test_corrupt_v1_user_store_refuses_default_credentials(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "users.json").write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", str(cfg_dir / "config.yaml"))
    monkeypatch.setattr(config, "_config", None)
    db.open_db(str(cfg_dir / "app.db"))
    import pytest
    with pytest.raises(RuntimeError, match="could not be migrated"):
        accounts.bootstrap()
    assert accounts.get_by_username("admin") is None
