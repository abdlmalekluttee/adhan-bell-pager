"""Scheduling safety: exactly-once, restarts, downtime, grace windows, conflict policy."""
import datetime
import time

from app import bells, config, db, dispatcher, executions, scheduler


def setup_bells(times=("08:00",), sound="Three beeps.wav"):
    t = bells.save_timetable({"name": "Day", "events": [
        {"label": f"Bell {x}", "kind": "custom", "start_time": x} for x in times]})
    bells.save_schedule({"name": "Main", "effective_from": "2026-09-01", "days": {d: t["id"] for d in bells.WEEKDAYS},
                         "sound": sound})


def no_adhan():
    config.update_section("prayer", {"enabled_prayers": {p: False for p in config.PRAYERS}})


def rows(module="bells"):
    return executions.search(modules=[module], limit=50)["items"]


def settle(env):
    assert env["dispatcher"].wait_idle(10)


def test_plays_once_at_time(env, fake_clock):
    no_adhan()
    setup_bells()
    s = env["scheduler"]
    s.recover()
    s.tick()                                     # 07:00 – nothing due, 08:00 becomes known
    fake_clock.advance(hours=1, seconds=1)       # 08:00:01
    s.tick()
    s.tick()
    settle(env)
    items = rows()
    assert len(items) == 1 and items[0]["status"] == "success"
    fake_clock.advance(seconds=30)
    s.tick()
    settle(env)
    assert len(rows()) == 1
    assert len([x for x in env["backend"].log if x[0] == "call"]) == 1


def test_restart_does_not_replay(env, fake_clock):
    no_adhan()
    setup_bells()
    s = env["scheduler"]
    s.recover()
    s.tick()
    fake_clock.advance(hours=1, seconds=2)
    s.tick()
    settle(env)
    db.set_setting(scheduler.HEARTBEAT_KEY, fake_clock.now.timestamp())
    # "restart": a brand-new scheduler 20 s later, still inside the grace window
    fake_clock.advance(seconds=20)
    s2 = scheduler.Scheduler()
    s2.recover()
    s2.tick()
    settle(env)
    assert len(rows()) == 1
    assert len([x for x in env["backend"].log if x[0] == "call"]) == 1


def test_missed_during_downtime_is_recorded_not_played(env, fake_clock):
    no_adhan()
    setup_bells(times=("08:00", "08:30"))
    db.set_setting(scheduler.HEARTBEAT_KEY, fake_clock.now.timestamp())   # last alive 07:00
    fake_clock.advance(hours=1, minutes=10)                               # back at 08:10
    s = scheduler.Scheduler()
    s.recover()
    s.tick()
    settle(env)
    items = rows()
    assert [(i["title"], i["status"]) for i in items] == [("Bell 08:00", "missed")]
    assert "never played late" in items[0]["reason"]
    assert not [x for x in env["backend"].log if x[0] == "call"]
    fake_clock.advance(minutes=20, seconds=1)                            # 08:30:01
    s.tick()
    settle(env)
    assert {i["title"]: i["status"] for i in rows()}["Bell 08:30"] == "success"


def test_restart_within_grace_plays_late(env, fake_clock):
    no_adhan()
    setup_bells()
    fake_clock.advance(minutes=59, seconds=50)                            # 07:59:50
    db.set_setting(scheduler.HEARTBEAT_KEY, fake_clock.now.timestamp())
    fake_clock.advance(seconds=40)                                        # 08:00:30, 30 s late
    s = scheduler.Scheduler()
    s.recover()
    s.tick()
    settle(env)
    item = rows()[0]
    assert item["status"] == "success" and item["delay_seconds"] >= 29


def test_stalled_beyond_grace_is_missed(env, fake_clock):
    no_adhan()
    setup_bells()
    s = env["scheduler"]
    s.recover()
    s.tick()
    fake_clock.advance(hours=1, minutes=5)                                # process paused for 5 minutes
    s.tick()
    settle(env)
    assert rows()[0]["status"] == "missed"


def test_bell_added_after_its_time_is_ignored(env, fake_clock):
    no_adhan()
    s = env["scheduler"]
    s.recover()
    fake_clock.advance(hours=2)                                           # 09:00
    s.tick()
    setup_bells(times=("08:30",))
    s.mark_dirty()
    s.tick()
    settle(env)
    assert rows() == []


def test_interrupted_on_crash_not_replayed(env, fake_clock):
    no_adhan()
    setup_bells()
    at = "2026-09-14T08:00:00+02:00"
    key = bells.plan_for(datetime.date(2026, 9, 14))["occurrences"][0]["key"]
    eid = executions.create(key, module="bells", kind="bell", title="Bell 08:00", priority=10, scheduled_for=at)
    executions.update(eid, status="running")
    db.set_setting(scheduler.HEARTBEAT_KEY, fake_clock.advance(hours=1).timestamp())
    fake_clock.advance(seconds=10)
    s = scheduler.Scheduler()
    s.recover()
    s.tick()
    settle(env)
    items = rows()
    assert len(items) == 1 and items[0]["status"] == "interrupted"
    assert not [x for x in env["backend"].log if x[0] == "call"]


def test_duplicate_submit_is_ignored(env, fake_clock):
    occ = {"key": "bells:2026-09-14:s1:08:00:1", "module": "bells", "kind": "bell", "title": "X",
           "at": "2026-09-14T07:00:00+02:00", "sound": "Three beeps.wav", "zone_ids": [], "playback": {"mode": "once"}}
    d = env["dispatcher"]
    assert d.submit(dispatcher.from_occurrence(occ))["status"] == "queued"
    assert d.submit(dispatcher.from_occurrence(occ))["status"] == "duplicate"
    settle(env)
    assert len(rows()) == 1


def test_adhan_interrupts_bell(env, fake_clock):
    be = env["backend"]
    be.time_scale = 1.0
    be.default["answer_after"] = 0.01
    d = env["dispatcher"]
    bell = dispatcher.from_occurrence({"key": "bells:k1", "module": "bells", "kind": "bell", "title": "Bell",
                                       "at": fake_clock.now.isoformat(), "sound": "Classic school bell.wav",
                                       "zone_ids": [], "playback": {"mode": "once"}})
    d.submit(bell)
    time.sleep(0.4)
    config.update_section("audio", {"default_file": ""})
    import shutil
    shutil.copy(bell.sound_path, config.AUDIO_DIR + "/adhan.wav")
    config.update_section("audio", {"default_file": "adhan.wav"})
    adhan = dispatcher.from_occurrence({"key": "adhan:2026-09-14:dhuhr:adhan", "module": "adhan", "kind": "adhan",
                                        "prayer": "dhuhr", "title": "Dhuhr Adhan", "at": fake_clock.now.isoformat()})
    d.submit(adhan)
    settle(env)
    status = {i["title"]: (i["status"], i["reason"]) for i in executions.search(modules=["bells", "adhan"])["items"]}
    assert status["Bell"][0] == "interrupted" and "Dhuhr Adhan" in status["Bell"][1]
    assert status["Dhuhr Adhan"][0] == "success"


def test_bell_waiting_behind_adhan_is_skipped(env, fake_clock):
    be = env["backend"]
    be.time_scale = 1.0
    be.default["answer_after"] = 0.01
    config.update_section("policy", {"bells_max_delay_seconds": 0})
    d = env["dispatcher"]
    import shutil
    shutil.copy(config.AUDIO_DIR + "/bells/Classic school bell.wav", config.AUDIO_DIR + "/adhan.wav")
    config.update_section("audio", {"default_file": "adhan.wav"})
    d.submit(dispatcher.from_occurrence({"key": "adhan:x:dhuhr:adhan", "module": "adhan", "kind": "adhan",
                                         "prayer": "dhuhr", "title": "Dhuhr Adhan", "at": fake_clock.now.isoformat()}))
    time.sleep(0.3)
    bell = dispatcher.from_occurrence({"key": "bells:k2", "module": "bells", "kind": "bell", "title": "Bell",
                                       "at": fake_clock.now.isoformat(), "sound": "Three beeps.wav", "zone_ids": [],
                                       "playback": {"mode": "once"}})
    bell.max_wait = 0.5
    d.submit(bell)
    settle(env)
    status = {i["title"]: (i["status"], i["reason"]) for i in executions.search(modules=["bells", "adhan"])["items"]}
    assert status["Bell"][0] == "skipped" and "busy" in status["Bell"][1], status
    assert status["Dhuhr Adhan"][0] == "success"


def test_manual_conflict_requires_force(env, fake_clock):
    be = env["backend"]
    be.time_scale = 1.0
    be.default["answer_after"] = 0.01
    d = env["dispatcher"]
    from app import audio, clock, planner

    def manual(title):
        return dispatcher.Broadcast(key=dispatcher.manual_key(), module="bells", kind="manual", title=title,
                                    priority=planner.PRIORITY["manual"], manual=True,
                                    sound_path=audio.path_for("Classic school bell.wav", "bells"),
                                    sound_name="Classic school bell.wav", scheduled_for=clock.now(), actor="op")
    d.submit(manual("First"))
    time.sleep(0.3)
    try:
        d.submit(manual("Second"))
        raise AssertionError("expected conflict")
    except dispatcher.Conflict as c:
        assert c.active["title"] == "First"
    d.submit(manual("Second"), force=True)
    settle(env)
    status = {i["title"]: i["status"] for i in rows()}
    assert status == {"First": "cancelled", "Second": "success"}


def test_missing_audio_fails_with_reason(env, fake_clock):
    d = env["dispatcher"]
    d.submit(dispatcher.from_occurrence({"key": "adhan:y:fajr:adhan", "module": "adhan", "kind": "adhan",
                                         "prayer": "fajr", "title": "Fajr Adhan", "at": fake_clock.now.isoformat()}))
    settle(env)
    item = rows("adhan")[0]
    assert item["status"] == "failed" and "No audio file" in item["reason"]


def test_no_destinations_fails_with_reason(env, fake_clock):
    config.update_section("destinations", [{"name": "Off", "uri": "600", "enabled": False}])
    d = env["dispatcher"]
    d.submit(dispatcher.from_occurrence({"key": "bells:z", "module": "bells", "kind": "bell", "title": "B",
                                         "at": fake_clock.now.isoformat(), "sound": "Three beeps.wav",
                                         "zone_ids": [], "playback": {"mode": "once"}}))
    settle(env)
    assert "No enabled destinations" in rows()[0]["reason"]


def test_conflict_annotation(env, fake_clock):
    from app import planner
    occ = [
        {"key": "a", "module": "adhan", "title": "Dhuhr Adhan", "at": "2026-09-14T12:00:00+02:00", "est_seconds": 180},
        {"key": "b", "module": "bells", "title": "Bell 12:01", "at": "2026-09-14T12:01:00+02:00", "est_seconds": 5},
        {"key": "c", "module": "bells", "title": "Bell 11:59", "at": "2026-09-14T11:59:50+02:00", "est_seconds": 30},
    ]
    out = {o["key"]: o["conflicts"] for o in planner.annotate_conflicts(occ)}
    assert out["b"][0]["outcome"] == "skipped"
    assert out["c"][0]["outcome"] == "interrupts"
