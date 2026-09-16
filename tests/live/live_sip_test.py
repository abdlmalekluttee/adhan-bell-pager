"""Live end-to-end test against a deployed instance and a real PBX.

NOT part of CI. It places short real calls. Run it only against your own test
extension, with permission. All credentials come from environment variables:

    ABP_URL=http://10.0.0.5:8090 ABP_ADMIN_PASSWORD=... SIP_SERVER=... SIP_USER=... SIP_PASSWORD=... \
    SIP_DEST=4090 SSH_HOST=myvm python tests/live/live_sip_test.py [--only name,name] [--report out.json]

Steps that need the host (restart, network interruption) use ``ssh $SSH_HOST``;
the network interruption needs passwordless or ``SUDO_PASSWORD`` sudo on that host.
The report distinguishes what was *measured* (SIP/RTP) from what a human must
confirm (audible audio at the destination).
"""
from __future__ import annotations

import argparse
import datetime
import io
import json
import math
import os
import shlex
import struct
import subprocess
import sys
import time
import wave

import httpx

URL = os.environ.get("ABP_URL", "http://127.0.0.1:8090").rstrip("/")
ADMIN_USER = os.environ.get("ABP_ADMIN_USER", "admin")
ADMIN_PW = os.environ.get("ABP_ADMIN_PASSWORD", "")
SIP_SERVER = os.environ.get("SIP_SERVER", "")
SIP_USER = os.environ.get("SIP_USER", "")
SIP_PASSWORD = os.environ.get("SIP_PASSWORD", "")
SIP_DEST = os.environ.get("SIP_DEST", "")
SSH_HOST = os.environ.get("SSH_HOST", "")
SUDO_PASSWORD = os.environ.get("SUDO_PASSWORD", "")

REPORT: list[dict] = []


def log(msg: str) -> None:
    print(f"{datetime.datetime.now():%H:%M:%S} {msg}", flush=True)


class Client:
    def __init__(self):
        self.c = httpx.Client(base_url=URL, timeout=30)

    def csrf(self) -> dict:
        return {"X-CSRF-Token": self.c.cookies.get("abp_csrf", "")}

    def login(self, user: str, pw: str) -> httpx.Response:
        self.c.cookies.clear()
        return self.c.post("/api/auth/login", json={"username": user, "password": pw})

    def get(self, path, **kw):
        r = self.c.get(path, **kw)
        return r

    def post(self, path, json_body=None, **kw):
        return self.c.post(path, json=json_body if json_body is not None else {}, headers=self.csrf(), **kw)

    def put(self, path, json_body, **kw):
        return self.c.put(path, json=json_body, headers=self.csrf(), **kw)

    def delete(self, path, **kw):
        return self.c.delete(path, headers=self.csrf(), **kw)


def ssh(cmd: str, sudo: bool = False, timeout: int = 120) -> str:
    """Run a command on SSH_HOST. With sudo=True the password goes to sudo on stdin, never on a command line."""
    if sudo:
        out = subprocess.run(["ssh", SSH_HOST, "sudo -S -p '' sh -c " + shlex.quote(cmd)],
                             input=SUDO_PASSWORD + "\n", capture_output=True, text=True, timeout=timeout)
    else:
        out = subprocess.run(["ssh", SSH_HOST, cmd], capture_output=True, text=True, timeout=timeout)
    return (out.stdout + out.stderr).strip()


def record(name: str, ok: bool, kind: str, details: dict) -> None:
    REPORT.append({"test": name, "result": "pass" if ok else "fail", "evidence": kind, "details": details,
                   "time": datetime.datetime.now().isoformat(timespec="seconds")})
    log(f"{'PASS' if ok else 'FAIL'} {name} — {json.dumps(details)[:300]}")


def tone_wav(seconds: float, freq: float = 660.0, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = b"".join(
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / rate) * (0.6 + 0.4 * math.sin(i / rate * 3))))
            for i in range(int(rate * seconds)))
        w.writeframes(frames)
    return buf.getvalue()


def wait_idle(api: Client, timeout: float = 120) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        b = api.get("/api/broadcast").json()
        if not b["line_busy"] and not b["queue"]:
            return True
        time.sleep(0.5)
    return False


def latest(api: Client, module: str, n: int = 1) -> list[dict]:
    return api.get(f"/api/{module}/history", params={"limit": n}).json()["items"]


def sip_status(api: Client) -> dict:
    return api.get("/api/system/sip").json()["status"]


def wait_registered(api: Client, timeout: float = 60) -> dict:
    deadline = time.time() + timeout
    st = {}
    while time.time() < deadline:
        st = sip_status(api)
        if st["registered"]:
            return st
        time.sleep(1)
    return st


def exec_summary(e: dict) -> dict:
    d = (e.get("results") or [{}])[0]
    return {"status": e["status"], "reason": e["reason"], "codec": e.get("codec"), "outcome": d.get("outcome"),
            "answered": d.get("answered"), "played_s": d.get("played_seconds"), "rtp": d.get("rtp"),
            "ring_s": d.get("ring_seconds"), "delay_s": e.get("delay_seconds")}


# --------------------------------------------------------------------------- #
# Steps
# --------------------------------------------------------------------------- #
def step_setup(api: Client) -> None:
    r = api.login(ADMIN_USER, ADMIN_PW)
    if r.status_code != 200:
        raise SystemExit(f"admin sign-in failed: {r.status_code} {r.text}")
    body = {"destinations": [{"id": "dest4090", "name": "Test speaker", "uri": SIP_DEST, "enabled": True}],
            "zones": [{"id": "testzone", "name": "Test zone", "destination_ids": ["dest4090"]}]}
    assert api.put("/api/system/destinations", body).status_code == 200
    r = api.put("/api/system/call", {"mode": "sequential", "ring_timeout": 20, "answer_delay_ms": 1200,
                                     "hangup_after_eof_ms": 600, "max_call_seconds": 600})
    assert r.status_code == 200, r.text
    r = api.put("/api/system/codecs", {"priorities": {"G722/16000/1": 250, "PCMA/8000/1": 240, "PCMU/8000/1": 230,
                                                      "opus/48000/2": 0, "speex/16000/1": 0, "iLBC/8000/1": 0,
                                                      "GSM/8000/1": 0}})
    assert r.status_code == 200, r.text
    r = api.put("/api/system/sip", {"enabled": True, "registrar": SIP_SERVER, "port": 5060, "transport": "udp",
                                    "username": SIP_USER, "auth_user": SIP_USER, "password": SIP_PASSWORD,
                                    "display_name": "Bell Pager", "register_expires": 300, "local_port": 5060})
    assert r.status_code == 200, r.text
    assert SIP_PASSWORD not in r.text
    for name, secs in (("live-adhan-40s.wav", 40), ("live-adhan-8s.wav", 8)):
        files = {"file": (name, tone_wav(secs, 520), "audio/wav")}
        r = api.c.post("/api/adhan/sounds", files=files, params={"replace": "true"}, headers=api.csrf())
        assert r.status_code == 200, r.text
    r = api.put("/api/adhan/sounds/assignments", {"default_file": "live-adhan-8s.wav", "use_global_for_all": True,
                                                   "iqama_file": ""})
    assert r.status_code == 200, r.text
    r = api.put("/api/adhan/settings", {"zone_ids": ["testzone"]})
    assert r.status_code == 200, r.text


def test_registration(api: Client) -> None:
    t0 = time.time()
    st = wait_registered(api, 30)
    record("SIP registration (UDP)", st.get("registered", False), "measured",
           {"seconds": round(time.time() - t0, 1), "code": st.get("reg_code"), "expires": st.get("expires"),
            "identity": st.get("identity"), "transport": st.get("transport")})


def test_renewal(api: Client) -> None:
    st = wait_registered(api, 30)
    first = st.get("last_registered_at")
    wait = max(60, (st.get("expires") or 120) + 20)
    log(f"waiting {wait}s to observe a registration refresh")
    time.sleep(wait)
    st2 = sip_status(api)
    ok = st2["registered"] and st2.get("last_registered_at") and st2["last_registered_at"] > (first or 0)
    record("Periodic registration renewal", bool(ok), "measured",
           {"first": first, "after": st2.get("last_registered_at"), "renewed_after_s":
            round((st2.get("last_registered_at") or 0) - (first or 0), 1), "expires": st2.get("expires")})


def test_bell_call(api: Client) -> None:
    r = api.post("/api/bells/ring", {"sound": "Three beeps.wav", "zone_ids": ["testzone"], "test": True})
    ok = r.status_code == 200 and wait_idle(api, 60)
    e = latest(api, "bells")[0]
    s = exec_summary(e)
    ok = ok and e["status"] == "success" and (s["rtp"] or {}).get("tx_packets", 0) > 0
    record("School bell test call to destination (auto-answer, playback, hang-up)", ok, "measured (SIP/RTP)", s)


def test_bell_repeat(api: Client) -> None:
    r = api.post("/api/bells/ring", {"sound": "Three beeps.wav", "zone_ids": ["testzone"], "playback_mode": "repeat",
                                     "repeat_count": 2, "test": True})
    ok = r.status_code == 200 and wait_idle(api, 60)
    e = latest(api, "bells")[0]
    s = exec_summary(e)
    record("Bell repeat count (x2) playback length", ok and e["status"] == "success" and (s["played_s"] or 0) >= 3.0,
           "measured", s)


def test_adhan_call(api: Client) -> None:
    r = api.post("/api/adhan/test", {"prayer": "dhuhr"})
    ok = r.status_code == 200 and wait_idle(api, 90)
    e = latest(api, "adhan")[0]
    s = exec_summary(e)
    record("Adhan test call (8 s recording)", ok and e["status"] == "success" and (s["played_s"] or 0) >= 7.5,
           "measured (SIP/RTP)", s)


def test_manual_stop(api: Client) -> None:
    r = api.post("/api/bells/ring", {"sound": "Classic school bell.wav", "zone_ids": ["testzone"],
                                     "playback_mode": "duration", "duration_seconds": 60, "test": True})
    assert r.status_code == 200, r.text
    deadline = time.time() + 30
    playing = False
    while time.time() < deadline:
        b = api.get("/api/broadcast").json()
        line = (b.get("active") or {}).get("line") or {}
        if any(d.get("state") == "playing" for d in line.get("destinations", [])):
            playing = True
            break
        time.sleep(0.3)
    time.sleep(3)
    t_stop = time.time()
    r = api.post("/api/broadcast/stop")
    released = wait_idle(api, 15)
    release_s = round(time.time() - t_stop, 2)
    e = latest(api, "bells")[0]
    st = sip_status(api)
    ok = playing and r.json().get("stopped") and released and e["status"] == "cancelled" and release_s < 5 \
        and st["active_calls"] == 0 and st["live_objects"] == {"calls": 0, "players": 0}
    record("Manual stop releases the call immediately", ok, "measured",
           {"was_playing": playing, "release_seconds": release_s, **exec_summary(e),
            "live_objects": st["live_objects"]})


def test_repeated_calls(api: Client, n: int = 5) -> None:
    results = []
    for i in range(n):
        r = api.post("/api/bells/ring", {"sound": "Three beeps.wav", "zone_ids": ["testzone"], "test": True})
        if r.status_code != 200:
            results.append({"i": i, "http": r.status_code})
            continue
        wait_idle(api, 60)
        e = latest(api, "bells")[0]
        results.append({"i": i, "status": e["status"], "codec": e["codec"],
                        "rtp": ((e.get("results") or [{}])[0]).get("rtp")})
        time.sleep(0.5)
    st = sip_status(api)
    ok = all(x.get("status") == "success" for x in results) and st["live_objects"] == {"calls": 0, "players": 0}
    record(f"{n} back-to-back calls, no leaks", ok, "measured",
           {"results": results, "live_objects": st["live_objects"]})


def test_manual_conflict(api: Client) -> None:
    api.post("/api/bells/ring", {"sound": "Classic school bell.wav", "zone_ids": ["testzone"],
                                 "playback_mode": "duration", "duration_seconds": 25, "test": True})
    time.sleep(4)
    r1 = api.post("/api/bells/ring", {"sound": "Three beeps.wav", "zone_ids": ["testzone"], "test": True})
    r2 = api.post("/api/bells/ring", {"sound": "Three beeps.wav", "zone_ids": ["testzone"], "test": True,
                                      "force": True})
    wait_idle(api, 60)
    items = latest(api, "bells", 2)
    ok = r1.status_code == 409 and r1.json()["detail"]["code"] == "line_busy" and r2.status_code == 200 \
        and items[0]["status"] == "success" and items[1]["status"] == "cancelled"
    record("Manual broadcast on a busy line: refused, then interrupt on request", ok, "measured",
           {"first_http": r1.status_code, "forced_http": r2.status_code,
            "statuses": [(i["title"], i["status"], i["reason"]) for i in items]})


def _clock(api: Client) -> datetime.datetime:
    return datetime.datetime.fromisoformat(api.get("/api/auth/me").json()["server"]["now"])


def test_scheduled_and_policy(api: Client) -> None:
    """Scheduled bell, scheduled Adhan (manual timetable), and the conflict policy, live."""
    now = _clock(api)
    base = (now + datetime.timedelta(minutes=3)).replace(second=0, microsecond=0)
    t_bell_before = base.strftime("%H:%M")                            # bell interrupted by the Adhan
    t_adhan = (base + datetime.timedelta(minutes=1)).strftime("%H:%M")
    t_bell_during = (base + datetime.timedelta(minutes=1)).strftime("%H:%M")   # same minute: waits
    t_bell_alone = (base + datetime.timedelta(minutes=4)).strftime("%H:%M")
    # Adhan at t_adhan via a one-day manual timetable, 40 s recording
    api.put("/api/adhan/sounds/assignments", {"default_file": "live-adhan-40s.wav", "use_global_for_all": True})
    settings = api.get("/api/adhan/settings").json()
    old_prayer = settings["prayer"]
    day = base.date().isoformat()
    # An Adhan plays at most once per prayer per day, so use a prayer that has not played yet today.
    done = {i["event_key"].split(":")[2] for i in api.get("/api/adhan/history", params={"limit": 200}).json()["items"]
            if i["event_key"].startswith(f"adhan:{day}:")}
    prayer = next(p for p in ("dhuhr", "asr", "maghrib", "isha", "fajr") if p not in done)
    label = prayer.capitalize()
    lines = [f"Date : {day}"] + [f"{p.capitalize()} : {t_adhan + ':00' if p == prayer else ''}"
                                 for p in ("fajr", "dhuhr", "asr", "maghrib", "isha")]
    tt = "\n".join(lines) + "\n"
    r = api.c.post("/api/adhan/manual-timetable", files={"file": ("live.txt", tt.encode(), "text/plain")},
                   headers=api.csrf())
    assert r.status_code == 200, r.text
    api.put("/api/adhan/settings", {"prayer": {"enabled_prayers": {p: p == prayer for p in
                                                                     ("fajr", "dhuhr", "asr", "maghrib", "isha")},
                                               "iqama": {"enabled": False, "minutes": 15}}})
    api.put("/api/system/time", {"policy": {"adhan_max_delay_seconds": 300, "bells_max_delay_seconds": 60,
                                            "adhan_late_grace_seconds": 120, "bells_late_grace_seconds": 60}})
    tt_body = {"name": "Live test day", "events": [
        {"label": "Bell before Adhan", "kind": "custom", "start_time": t_bell_before},
        {"label": "Bell during Adhan", "kind": "custom", "start_time": t_bell_during, "sound_start": "Three beeps.wav"},
        {"label": "Bell alone", "kind": "custom", "start_time": t_bell_alone, "sound_start": "Three beeps.wav"},
    ]}
    existing = [t for t in api.get("/api/bells/timetables").json()["timetables"] if t["name"] == "Live test day"]
    for t in existing:
        for s in api.get("/api/bells/schedules").json()["schedules"]:
            if s["name"] == "Live test schedule":
                api.delete(f"/api/bells/schedules/{s['id']}")
        api.delete(f"/api/bells/timetables/{t['id']}")
    tt_id = api.post("/api/bells/timetables", tt_body).json()["id"]
    wkey = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][base.weekday()]
    r = api.post("/api/bells/schedules", {"name": "Live test schedule", "effective_from": day, "effective_to": day,
                                          "days": {wkey: tt_id}, "zone_ids": ["testzone"],
                                          "sound": "Classic school bell.wav", "playback_mode": "duration",
                                          "duration_seconds": 75})   # long enough to overlap the Adhan a minute later
    assert r.status_code == 200, r.text
    plan = api.get("/api/bells/day", params={"date": day}).json()
    conflicts = {o["title"]: o["conflicts"] for o in plan["occurrences"]}
    log(f"scheduled: bell {t_bell_before}, adhan {t_adhan}, bell {t_bell_during}, bell {t_bell_alone}; waiting")
    end = datetime.datetime.combine(base.date(), datetime.time.fromisoformat(t_bell_alone + ":00"),
                                    tzinfo=now.tzinfo) + datetime.timedelta(seconds=40)
    while _clock(api) < end:
        time.sleep(5)
    wait_idle(api, 120)
    # newest first: keep the first (latest) execution per title so earlier runs do not mask this one
    bells_hist, adhan_hist = {}, {}
    for i in api.get("/api/bells/history", params={"limit": 20}).json()["items"]:
        bells_hist.setdefault(i["title"], i)
    for i in api.get("/api/adhan/history", params={"limit": 10}).json()["items"]:
        adhan_hist.setdefault(i["title"], i)
    before = bells_hist.get("Bell before Adhan", {})
    during = bells_hist.get("Bell during Adhan", {})
    alone = bells_hist.get("Bell alone", {})
    adhan = adhan_hist.get(f"{label} Adhan", {})
    ok_sched = alone.get("status") == "success" and adhan.get("status") == "success"
    record("Scheduled bell and scheduled Adhan play on time", ok_sched, "measured",
           {"bell_alone": exec_summary(alone) if alone else None, "adhan": exec_summary(adhan) if adhan else None})
    ok_policy = before.get("status") == "interrupted" and during.get("status") in ("success", "skipped")
    record("Conflict policy: Adhan interrupts a bell; a bell due during the Adhan waits (≤60 s) or is skipped",
           ok_policy, "measured",
           {"bell_before": exec_summary(before) if before else None,
            "bell_during": exec_summary(during) if during else None, "planner_conflicts": conflicts})
    # restore Adhan settings
    api.put("/api/adhan/settings", {"prayer": {"source": old_prayer["source"],
                                               "enabled_prayers": old_prayer["enabled_prayers"],
                                               "iqama": old_prayer["iqama"]}})
    api.put("/api/adhan/sounds/assignments", {"default_file": "live-adhan-8s.wav", "use_global_for_all": True})


def test_restart_recovery(api: Client) -> None:
    """Restart the container: re-registers, no duplicate executions, a bell due during the restart is handled."""
    now = _clock(api)
    due = (now + datetime.timedelta(minutes=2)).replace(second=0, microsecond=0)
    day = due.date().isoformat()
    for s in api.get("/api/bells/schedules").json()["schedules"]:
        if s["name"] in ("Restart test schedule", "Live test schedule"):
            api.delete(f"/api/bells/schedules/{s['id']}")
    for t in api.get("/api/bells/timetables").json()["timetables"]:
        if t["name"] in ("Restart test day", "Live test day"):
            api.delete(f"/api/bells/timetables/{t['id']}")
    tt_id = api.post("/api/bells/timetables", {"name": "Restart test day", "events": [
        {"label": "Bell during restart", "kind": "custom", "start_time": due.strftime("%H:%M")}]}).json()["id"]
    wkey = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][due.weekday()]
    api.post("/api/bells/schedules", {"name": "Restart test schedule", "effective_from": day, "effective_to": day,
                                      "days": {wkey: tt_id}, "zone_ids": ["testzone"], "sound": "Three beeps.wav"})
    before_count = api.get("/api/bells/history", params={"limit": 1}).json()["total"]
    # stop ~10 s before the bell, start ~20 s after it (inside the 60 s grace)
    while (due - _clock(api)).total_seconds() > 10:
        time.sleep(1)
    anchor_srv, anchor_local = _clock(api), time.time()
    ssh("cd /opt/adhan-bell-pager && docker compose stop")
    while (anchor_srv + datetime.timedelta(seconds=time.time() - anchor_local) - due).total_seconds() < 20:
        time.sleep(1)
    ssh("cd /opt/adhan-bell-pager && docker compose start")
    api2 = Client()
    deadline = time.time() + 90
    while time.time() < deadline:
        try:
            if api2.login(ADMIN_USER, ADMIN_PW).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(2)
    st = wait_registered(api2, 60)
    wait_idle(api2, 60)
    items = [i for i in api2.get("/api/bells/history", params={"limit": 10}).json()["items"]
             if i["title"] == "Bell during restart"]
    total_after = api2.get("/api/bells/history", params={"limit": 1}).json()["total"]
    ok = st.get("registered") and len(items) == 1 and items[0]["status"] == "success" and total_after == before_count + 1
    record("Restart: re-registers; bell due during a short restart plays once (inside grace), no duplicates",
           ok, "measured", {"registered": st.get("registered"), "executions": [exec_summary(i) for i in items],
                            "history_total_before": before_count, "after": total_after})
    api.c = api2.c


def test_network_interruption(api: Client) -> None:
    """Block traffic to the PBX for longer than the registration expiry, then restore it."""
    rule = f"OUTPUT -d {SIP_SERVER} -j DROP"
    ssh(f"iptables -I {rule}; (sleep 420; iptables -D {rule} 2>/dev/null) >/dev/null 2>&1 &", sudo=True)
    log("PBX blocked (failsafe removes the rule after 7 minutes)")
    t0 = time.time()
    lost = None
    while time.time() - t0 < 360:
        st = sip_status(api)
        if not st["registered"]:
            lost = st
            break
        time.sleep(5)
    r = api.post("/api/bells/ring", {"sound": "Three beeps.wav", "zone_ids": ["testzone"], "test": True})
    wait_idle(api, 90)
    during = latest(api, "bells")[0]
    ssh(f"iptables -D {rule}", sudo=True)
    t1 = time.time()
    st = wait_registered(api, 300)
    recovered_s = round(time.time() - t1, 1)
    ok = lost is not None and st.get("registered") and during["status"] == "failed"
    record("Network interruption: registration loss is detected and shown, recovers after restore", ok, "measured",
           {"detected_after_s": round(time.time() - t0, 1) if lost else None,
            "status_while_down": {k: lost.get(k) for k in ("reg_code", "message", "next_retry_in",
                                                            "consecutive_failures")} if lost else None,
            "call_while_down": exec_summary(during), "recovered_after_s": recovered_s})


def test_wrong_password(api: Client) -> None:
    """Two failed registrations at most (PBX intrusion detection may ban the IP otherwise)."""
    r = api.put("/api/system/sip", {"password": "definitely-wrong-password"})
    assert r.status_code == 200
    time.sleep(8)
    st = sip_status(api)
    api.put("/api/system/sip", {"password": SIP_PASSWORD})
    st2 = wait_registered(api, 60)
    ok = not st["registered"] and st["reg_code"] in (401, 403, 407) and bool(st.get("next_retry_in")) \
        and st2.get("registered")
    record("Wrong SIP password: clear error, bounded retry, recovers when fixed", ok, "measured",
           {"code": st["reg_code"], "message": st["message"], "next_retry_in": st.get("next_retry_in"),
            "failures": st["consecutive_failures"], "recovered": st2.get("registered")})


def test_tcp(api: Client) -> None:
    r = api.put("/api/system/sip", {"transport": "tcp"})
    st = wait_registered(api, 45)
    call = None
    if st.get("registered"):
        api.post("/api/bells/ring", {"sound": "Three beeps.wav", "zone_ids": ["testzone"], "test": True})
        wait_idle(api, 60)
        call = exec_summary(latest(api, "bells")[0])
    api.put("/api/system/sip", {"transport": "udp"})
    back = wait_registered(api, 45)
    ok = r.status_code == 200 and st.get("registered") and call and call["status"] == "success" and back.get("registered")
    record("SIP over TCP: registration and call", bool(ok), "measured",
           {"tcp_registered": st.get("registered"), "tcp_message": st.get("message"), "call": call,
            "udp_restored": back.get("registered")})


def test_codecs(api: Client) -> None:
    variants = [
        ("G.711 u-law only", {"PCMU/8000/1": 250, "PCMA/8000/1": 0, "G722/16000/1": 0, "opus/48000/2": 0}),
        ("G.722 only", {"G722/16000/1": 250, "PCMA/8000/1": 0, "PCMU/8000/1": 0, "opus/48000/2": 0}),
        ("Opus first", {"opus/48000/2": 250, "G722/16000/1": 240, "PCMA/8000/1": 230, "PCMU/8000/1": 220}),
        ("Default (G.722, A-law, u-law)", {"G722/16000/1": 250, "PCMA/8000/1": 240, "PCMU/8000/1": 230,
                                           "opus/48000/2": 0}),
    ]
    out = []
    for label, pri in variants:
        api.put("/api/system/codecs", {"priorities": pri})
        wait_registered(api, 45)
        api.post("/api/bells/ring", {"sound": "Three beeps.wav", "zone_ids": ["testzone"], "test": True})
        wait_idle(api, 60)
        e = latest(api, "bells")[0]
        out.append({"offer": label, **exec_summary(e)})
    ok = all(o["status"] in ("success", "failed") for o in out) and out[-1]["status"] == "success"
    record("Codec negotiation: codec actually used per offer", ok, "measured", {"results": out})


def test_permissions(api: Client) -> None:
    uname = "live-bells-only"
    users = {u["username"]: u for u in api.get("/api/users").json()["users"]}
    bells_role = next(r["id"] for r in api.get("/api/roles").json()["roles"] if r["key"] == "bells_admin")
    pw = "Live-Test-" + os.urandom(6).hex()
    if uname in users:
        api.delete(f"/api/users/{users[uname]['id']}")
    r = api.post("/api/users", {"username": uname, "password": pw, "must_change_password": False,
                                "role_ids": [bells_role]})
    assert r.status_code == 200, r.text
    uid = r.json()["id"]
    u = Client()
    u.login(uname, pw)
    checks = {
        "adhan test call": u.post("/api/adhan/test", {"prayer": "dhuhr"}).status_code,
        "SIP settings read": u.get("/api/system/sip").status_code,
        "SIP settings write": u.put("/api/system/sip", {"registrar": "10.0.0.1"}).status_code,
        "SIP reconnect": u.post("/api/system/sip/reconnect").status_code,
        "codecs write": u.put("/api/system/codecs", {"priorities": {"PCMA/8000/1": 250}}).status_code,
        "users list": u.get("/api/users").status_code,
    }
    allowed = u.post("/api/bells/ring", {"sound": "Three beeps.wav", "zone_ids": ["testzone"], "test": True})
    wait_idle(api, 60)
    api.delete(f"/api/users/{uid}")
    ok = all(v == 403 for v in checks.values()) and allowed.status_code == 200
    record("Permissions on the live API: bells-only user cannot call Adhan or change SIP; can ring bells", ok,
           "measured", {"denied": checks, "bell_ring_http": allowed.status_code})


def cleanup(api: Client) -> None:
    for s in api.get("/api/bells/schedules").json()["schedules"]:
        if s["name"] in ("Restart test schedule", "Live test schedule"):
            api.delete(f"/api/bells/schedules/{s['id']}")
    for t in api.get("/api/bells/timetables").json()["timetables"]:
        if t["name"] in ("Restart test day", "Live test day"):
            api.delete(f"/api/bells/timetables/{t['id']}")


TESTS = {
    "registration": test_registration, "bell": test_bell_call, "repeat": test_bell_repeat, "adhan": test_adhan_call,
    "stop": test_manual_stop, "repeated": test_repeated_calls, "conflict": test_manual_conflict,
    "permissions": test_permissions, "codecs": test_codecs, "tcp": test_tcp, "wrongpw": test_wrong_password,
    "scheduled": test_scheduled_and_policy, "restart": test_restart_recovery, "renewal": test_renewal,
    "network": test_network_interruption,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--report", default="live-report.json")
    ap.add_argument("--skip-setup", action="store_true")
    args = ap.parse_args()
    missing = [k for k, v in {"ABP_ADMIN_PASSWORD": ADMIN_PW, "SIP_SERVER": SIP_SERVER, "SIP_USER": SIP_USER,
                              "SIP_PASSWORD": SIP_PASSWORD, "SIP_DEST": SIP_DEST}.items() if not v]
    if missing:
        print("missing environment variables: " + ", ".join(missing))
        return 2
    api = Client()
    if args.skip_setup:
        api.login(ADMIN_USER, ADMIN_PW)
    else:
        step_setup(api)
    names = [n for n in args.only.split(",") if n] or list(TESTS)
    for n in names:
        try:
            TESTS[n](api)
        except Exception as e:  # noqa: BLE001
            record(n, False, "error", {"exception": f"{type(e).__name__}: {e}"})
        wait_idle(api, 60)
    try:
        cleanup(api)
    except Exception:  # noqa: BLE001
        pass
    with open(args.report, "w", encoding="utf-8") as fh:
        json.dump(REPORT, fh, indent=2)
    failed = [r for r in REPORT if r["result"] != "pass"]
    print(f"\n{len(REPORT) - len(failed)} passed, {len(failed)} failed. Report: {args.report}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
