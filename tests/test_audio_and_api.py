"""Audio rendering, uploads, API workflows, audit trail."""
import io
import wave

from conftest import admin_login

from app import audio, config


def test_render_repeat_and_duration(env):
    src = audio.path_for("Three beeps.wav", "bells")
    base = audio.duration_of(src)
    rep = audio.render_playback(src, "repeat", 3)
    assert abs(audio.duration_of(rep) - (base * 3 + 0.8)) < 0.05
    dur = audio.render_playback(src, "duration", duration_seconds=7)
    assert abs(audio.duration_of(dur) - 7.0) < 0.01
    assert audio.render_playback(src, "once") == src
    assert audio.render_playback(src, "repeat", 3) == rep                  # cached


def _wav_bytes(seconds=0.5, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x10" * int(rate * seconds))
    return buf.getvalue()


def test_upload_rename_conflict_and_delete_guard(client):
    h = admin_login(client)
    files = {"file": ("../../evil name.wav", _wav_bytes(), "audio/wav")}
    r = client.post("/api/adhan/sounds", files=files, headers=h)
    assert r.status_code == 200, r.text
    name = r.json()["name"]
    assert "/" not in name and ".." not in name
    r = client.post("/api/adhan/sounds", files={"file": ("../../evil name.wav", _wav_bytes(), "audio/wav")}, headers=h)
    assert r.status_code == 409
    r = client.put("/api/adhan/sounds/assignments", json={"default_file": name}, headers=h)
    assert r.status_code == 200
    r = client.delete(f"/api/adhan/sounds/{name}", headers=h)
    assert r.status_code == 400 and "assigned" in r.json()["detail"]["message"]
    r = client.post("/api/adhan/sounds", files={"file": ("x.exe", b"MZ", "application/octet-stream")}, headers=h)
    assert r.status_code == 400


def test_bell_workflow_end_to_end(client, env):
    h = admin_login(client)
    r = client.put("/api/system/destinations", headers=h, json={
        "destinations": [{"id": "d1", "name": "Hall", "uri": "4090", "enabled": True},
                         {"id": "d2", "name": "Yard", "uri": "4091", "enabled": True}],
        "zones": [{"id": "z1", "name": "Classrooms", "destination_ids": ["d1"]}]})
    assert r.status_code == 200, r.text
    r = client.post("/api/bells/timetables", headers=h, json={"name": "Regular", "events": [
        {"label": "Period 1", "kind": "period", "start_time": "08:00", "end_time": "08:45", "ring_end": True}]})
    tt = r.json()
    r = client.post("/api/bells/schedules", headers=h, json={
        "name": "2026-27", "effective_from": "2026-09-01", "days": {"sun": tt["id"], "mon": tt["id"]},
        "zone_ids": ["z1"], "sound": "Classic school bell.wav", "playback_mode": "repeat", "repeat_count": 2})
    assert r.status_code == 200, r.text
    day = client.get("/api/bells/day?date=2026-09-14").json()
    assert [o["time"] for o in day["occurrences"]] == ["08:00", "08:45"]
    assert day["occurrences"][0]["zone_ids"] == ["z1"]
    cal = client.get("/api/bells/calendar?start=2026-09-13&days=3").json()["days"]
    assert [c["status"] for c in cal] == ["school_day", "school_day", "not_school_day"]
    # zone in use cannot be removed
    r = client.put("/api/system/destinations", headers=h, json={
        "destinations": [{"id": "d1", "name": "Hall", "uri": "4090", "enabled": True}], "zones": []})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "zone_in_use"
    # ring now to the zone
    r = client.post("/api/bells/ring", headers=h, json={"sound": "Three beeps.wav", "zone_ids": ["z1"]})
    assert r.status_code == 200
    assert env["dispatcher"].wait_idle(10)
    hist = client.get("/api/bells/history").json()["items"]
    assert hist[0]["status"] == "success" and hist[0]["targets"] == ["4090"]
    audit = client.get("/api/audit").json()["items"]
    actions = {a["action"] for a in audit}
    assert {"destinations.update", "bells.timetable_create", "bells.schedule_create", "bells.ring_now"} <= actions
    assert all(a["actor"] == "admin" for a in audit if a["action"].startswith("bells."))


def test_manual_conflict_api(client, env):
    h = admin_login(client)
    env["backend"].time_scale = 1.0
    env["backend"].default["answer_after"] = 0.01
    r1 = client.post("/api/bells/ring", headers=h, json={"sound": "Classic school bell.wav"})
    assert r1.status_code == 200
    import time
    time.sleep(0.3)
    r2 = client.post("/api/bells/ring", headers=h, json={"sound": "Three beeps.wav"})
    assert r2.status_code == 409 and r2.json()["detail"]["can_interrupt"] is True
    r3 = client.post("/api/broadcast/stop", headers=h)
    assert r3.json()["stopped"] is True
    assert env["dispatcher"].wait_idle(10)
    items = client.get("/api/bells/history").json()["items"]
    assert items[0]["status"] == "cancelled" and "admin" in items[0]["reason"]


def test_sip_settings_validation(client):
    h = admin_login(client)
    r = client.put("/api/system/sip", json={"port": 99999}, headers=h)
    assert r.status_code == 400
    r = client.put("/api/system/sip", json={"registrar": "bad host"}, headers=h)
    assert r.status_code == 400
    r = client.put("/api/system/codecs", json={"priorities": {"PCMA/8000/1": 0}}, headers=h)
    assert r.status_code == 400
    assert config.get().sip.port == 5060


def test_health_is_public_and_minimal(client, env):
    env["scheduler"].start()
    r = client.get("/api/health")
    assert r.status_code == 200 and set(r.json()) == {"ok", "db", "scheduler", "version"}
    assert client.get("/api/dashboard").status_code == 401
    r = client.get("/")
    assert "Content-Security-Policy" in r.headers
