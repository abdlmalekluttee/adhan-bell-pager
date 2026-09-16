"""SIP paging lifecycle with the simulated backend."""
import threading
import time

from app import audio, config, sip


def wav():
    return audio.path_for("Three beeps.wav", "bells")


def test_answered_call_plays_and_hangs_up(env):
    eng, be = env["engine"], env["backend"]
    res = eng.page(wav(), ["4090"], label="t")
    assert res.status == "success"
    d = res.destinations[0]
    assert d.outcome == "played" and d.played_to_end and d.answered
    assert d.codec == "PCMA/8000"
    assert d.rtp["tx_packets"] > 0
    assert ("hangup", "sip:4090@pbx.example.local") in be.log
    assert be.live_objects() == {"calls": 0, "players": 0}          # nothing leaked


def test_ring_timeout_is_enforced(env):
    eng, be = env["engine"], env["backend"]
    cfg = config.get()
    cfg.call.ring_timeout = 1
    be.behaviours["4090"] = {"no_answer": True}
    t0 = time.monotonic()
    res = eng.page(wav(), ["4090"])
    assert time.monotonic() - t0 < 3
    assert res.status == "failed" and res.destinations[0].outcome == "no_answer"
    assert be.live_objects()["calls"] == 0


def test_busy_rejected_and_partial(env):
    eng, be = env["engine"], env["backend"]
    be.behaviours["4091"] = {"busy": True}
    be.behaviours["4092"] = {"fail_code": 404}
    res = eng.page(wav(), ["4090", "4091", "4092"], "sequential")
    outcomes = [d.outcome for d in res.destinations]
    assert outcomes == ["played", "busy", "rejected"]
    assert res.status == "partial"


def test_parallel_paging(env):
    eng = env["engine"]
    res = eng.page(wav(), ["4090", "4091", "4092"], "parallel")
    assert res.status == "success" and len(res.destinations) == 3


def test_manual_stop_releases_immediately(env):
    eng, be = env["engine"], env["backend"]
    be.time_scale = 1.0          # real time so the audio is still playing when we stop
    stop = sip.StopToken()
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", eng.page(wav(), ["4090"], stop=stop)))
    t.start()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not (eng.active() and eng.active()["destinations"][0]["state"] == "playing"):
        time.sleep(0.02)
    t_stop = time.monotonic()
    stop.set("stopped")
    t.join(5)
    assert time.monotonic() - t_stop < 1.0
    r = out["r"]
    assert r.status == "cancelled" and r.destinations[0].outcome == "stopped"
    assert be.live_objects() == {"calls": 0, "players": 0}
    assert eng.active() is None


def test_remote_hangup_is_reported(env):
    eng, be = env["engine"], env["backend"]
    be.time_scale = 1.0
    be.default["answer_after"] = 0.01
    be.behaviours["4090"] = {"remote_hangup_after": 0.3}
    res = eng.page(wav(), ["4090"])
    assert res.destinations[0].outcome == "remote_hangup" and res.status == "failed"


def test_no_media_is_reported(env):
    eng, be = env["engine"], env["backend"]
    be.behaviours["4090"] = {"media": False}
    from app.sip import engine as engmod
    old = engmod.MEDIA_WAIT_SECONDS
    engmod.MEDIA_WAIT_SECONDS = 0.3
    try:
        res = eng.page(wav(), ["4090"])
    finally:
        engmod.MEDIA_WAIT_SECONDS = old
    assert res.destinations[0].outcome == "no_media"


def test_not_running_fails_cleanly(env):
    eng, be = env["engine"], env["backend"]
    be.stop()
    res = eng.page(wav(), ["4090"])
    assert res.status == "failed" and "not running" in res.message


def test_repeated_calls_short_interval_no_leak(env):
    eng, be = env["engine"], env["backend"]
    for _ in range(15):
        assert eng.page(wav(), ["4090"]).status == "success"
    assert be.live_objects() == {"calls": 0, "players": 0}
    assert len([x for x in be.log if x[0] == "call"]) == 15


def test_only_one_page_on_the_line(env):
    eng, be = env["engine"], env["backend"]
    be.time_scale = 1.0
    t = threading.Thread(target=lambda: eng.page(wav(), ["4090"]))
    t.start()
    time.sleep(0.1)
    res = eng.page(wav(), ["4091"])
    assert res.status == "failed" and "already on the line" in res.message
    eng.stop_active("stopped")
    t.join(5)


def test_restart_during_call_ends_it_cleanly(env):
    eng, be = env["engine"], env["backend"]
    be.time_scale = 1.0
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", eng.page(wav(), ["4090"])))
    t.start()
    time.sleep(0.2)
    eng.restart()
    t.join(5)
    assert out["r"].status in ("cancelled", "failed")
    assert be.running()
    assert be.live_objects()["calls"] == 0


def test_dest_uri_transport_and_port(env):
    cfg = config.get()
    assert sip.PagingEngine.dest_uri("4090") == "sip:4090@pbx.example.local"
    cfg.sip.transport = "tcp"
    assert sip.PagingEngine.dest_uri("4090") == "sip:4090@pbx.example.local;transport=tcp"
    cfg.sip.port = 5070
    assert sip.PagingEngine.dest_uri("4090") == "sip:4090@pbx.example.local:5070;transport=tcp"
    assert sip.PagingEngine.dest_uri("sip:600@other") == "sip:600@other;transport=tcp"


def test_registration_backoff_is_bounded():
    from app.sip.pjsua import BACKOFF_MAX, _backoff
    seq = [_backoff(n) for n in range(1, 12)]
    assert seq[:4] == [30, 60, 120, 240]
    assert max(seq) == BACKOFF_MAX and all(b <= BACKOFF_MAX for b in seq)


def test_registration_messages():
    from app.sip.base import registration_message
    assert "credentials" in registration_message(401)
    assert "did not respond" in registration_message(408)
    assert "Registered" in registration_message(200)
