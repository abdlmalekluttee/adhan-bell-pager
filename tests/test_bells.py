"""School bell timetable rules: weekdays, effective dates, holidays, overrides, validation, timezone."""
import datetime

import pytest

from app import bells, config

MON = datetime.date(2026, 9, 14)


def tt(name="Regular day", rows=None):
    rows = rows or [
        {"label": "Start of day", "kind": "start_of_day", "start_time": "07:45"},
        {"label": "Period 1", "kind": "period", "start_time": "08:00", "end_time": "08:45", "ring_end": True},
        {"label": "Period 2", "kind": "period", "start_time": "08:45", "end_time": "09:30", "ring_end": True},
        {"label": "Break", "kind": "break", "start_time": "09:30", "end_time": "09:50", "ring_start": False,
         "ring_end": True},
        {"label": "End of day", "kind": "end_of_day", "start_time": "13:00"},
    ]
    return bells.save_timetable({"name": name, "events": rows})


def schedule(name="Main", days=None, eff_from="2026-09-01", eff_to=None, **kw):
    data = {"name": name, "effective_from": eff_from, "effective_to": eff_to, "days": days or {},
            "sound": "Classic school bell.wav", **kw}
    return bells.save_schedule(data)


def test_weekday_mapping_and_merge(env):
    regular = tt()
    short = tt("Short day", [{"label": "Only", "kind": "period", "start_time": "09:00", "end_time": "10:00"}])
    schedule(days={"sun": regular["id"], "mon": regular["id"], "thu": short["id"]})
    plan = bells.plan_for(MON)
    assert plan["status"] == "school_day"
    times = [o["time"] for o in plan["occurrences"]]
    # 08:45 end of period 1 and start of period 2 merge into one ring
    assert times == ["07:45", "08:00", "08:45", "09:30", "09:50", "13:00"]
    merged = next(o for o in plan["occurrences"] if o["time"] == "08:45")
    assert merged["labels"] == ["End of Period 1", "Period 2"]
    thu = bells.plan_for(MON + datetime.timedelta(days=3))
    assert [o["time"] for o in thu["occurrences"]] == ["09:00"]
    tue = bells.plan_for(MON + datetime.timedelta(days=1))
    assert tue["status"] == "not_school_day" and tue["occurrences"] == []


def test_effective_dates_latest_start_wins(env):
    regular = tt()
    ramadan = tt("Ramadan", [{"label": "P1", "kind": "period", "start_time": "09:00", "end_time": "09:30"}])
    all_days = {d: regular["id"] for d in bells.WEEKDAYS}
    schedule("School year", days=all_days, eff_from="2026-09-01", eff_to="2027-06-30")
    schedule("Ramadan", days={d: ramadan["id"] for d in bells.WEEKDAYS}, eff_from="2026-09-10", eff_to="2026-09-20")
    assert bells.plan_for(datetime.date(2026, 9, 9))["schedule"]["name"] == "School year"
    p = bells.plan_for(MON)
    assert p["schedule"]["name"] == "Ramadan"
    assert [s["name"] for s in p["overlapping"]] == ["School year"]
    assert bells.plan_for(datetime.date(2026, 9, 21))["schedule"]["name"] == "School year"
    assert bells.plan_for(datetime.date(2026, 8, 31))["status"] == "no_schedule"
    warnings = [w for s in bells.list_schedules() for w in s["warnings"]]
    assert any(w["code"] == "schedule_overlap" and "Ramadan" in w["message"] for w in warnings)


def test_disabled_schedule_is_ignored(env):
    regular = tt()
    schedule(days={"mon": regular["id"]}, enabled=False)
    assert bells.plan_for(MON)["status"] == "no_schedule"


def test_holiday_beats_override_and_override_replaces_timetable(env):
    regular = tt()
    exam = tt("Exam day", [{"label": "Exam", "kind": "period", "start_time": "10:00", "end_time": "12:00",
                            "ring_end": True}])
    schedule(days={d: regular["id"] for d in bells.WEEKDAYS})
    bells.save_exception({"name": "Exams", "kind": "override", "date_from": "2026-09-14", "date_to": "2026-09-15",
                          "timetable_id": exam["id"]})
    p = bells.plan_for(MON)
    assert p["status"] == "override" and [o["time"] for o in p["occurrences"]] == ["10:00", "12:00"]
    bells.save_exception({"name": "National day", "kind": "holiday", "date_from": "2026-09-14"})
    p = bells.plan_for(MON)
    assert p["status"] == "holiday" and p["occurrences"] == []
    assert bells.plan_for(MON + datetime.timedelta(days=1))["status"] == "override"
    bells.save_exception({"name": "Maintenance", "kind": "no_bells", "date_from": "2026-09-16", "date_to": "2026-09-16"})
    assert bells.plan_for(MON + datetime.timedelta(days=2))["status"] == "no_bells"


def test_exception_scoped_to_other_schedule_does_not_apply(env):
    regular = tt()
    s1 = schedule("A", days={"mon": regular["id"]})
    other = schedule("B", days={}, eff_from="2020-01-01", eff_to="2020-12-31")
    bells.save_exception({"name": "B only", "kind": "holiday", "date_from": "2026-09-14", "schedule_id": other["id"]})
    assert bells.plan_for(MON)["status"] == "school_day"
    bells.save_exception({"name": "A only", "kind": "holiday", "date_from": "2026-09-14", "schedule_id": s1["id"]})
    assert bells.plan_for(MON)["status"] == "holiday"


def test_overlapping_overrides_rejected(env):
    a = tt()
    bells.save_exception({"name": "X", "kind": "override", "date_from": "2026-10-01", "date_to": "2026-10-05",
                          "timetable_id": a["id"]})
    with pytest.raises(ValueError, match="overlap"):
        bells.save_exception({"name": "Y", "kind": "override", "date_from": "2026-10-05", "date_to": "2026-10-06",
                              "timetable_id": a["id"]})


@pytest.mark.parametrize("rows,msg", [
    ([{"label": "", "start_time": "08:00"}], "enter a name"),
    ([{"label": "P", "start_time": "8:0"}], "time like"),
    ([{"label": "P", "start_time": "09:00", "end_time": "08:00"}], "after the start"),
    ([{"label": "P", "kind": "bogus", "start_time": "09:00"}], "unknown row type"),
    ([{"label": "P", "start_time": "09:00", "sound_start": "nope.wav"}], "not in the bell sound library"),
])
def test_timetable_validation_errors(env, rows, msg):
    with pytest.raises(ValueError, match=msg):
        bells.save_timetable({"name": "Bad", "events": rows})


def test_timetable_warnings_overlap_and_same_minute(env):
    t = tt("Messy", [
        {"label": "A", "kind": "period", "start_time": "08:00", "end_time": "09:00"},
        {"label": "B", "kind": "period", "start_time": "08:30", "end_time": "09:30",
         "sound_start": "Three beeps.wav"},
        {"label": "C", "kind": "custom", "start_time": "08:30", "sound_start": "Two-tone chime.wav"},
    ])
    codes = [w["code"] for w in t["warnings"]]
    assert "overlap" in codes and "same_minute" in codes


def test_same_minute_warning_uses_schedule_context(env):
    # Explicitly choosing the schedule's own default sound does not create a second bell.
    t = tt("Explicit default", [
        {"label": "A", "kind": "custom", "start_time": "10:00"},
        {"label": "B", "kind": "custom", "start_time": "10:00", "sound_start": "Classic school bell.wav"},
    ])
    assert not [w for w in t["warnings"] if w["code"] == "same_minute"]
    schedule(days={"mon": t["id"]})
    plan = bells.plan_for(MON)
    assert len(plan["occurrences"]) == 1 and not plan["warnings"]
    t2 = tt("Really different", [
        {"label": "A", "kind": "custom", "start_time": "11:00"},
        {"label": "B", "kind": "custom", "start_time": "11:00", "sound_start": "Three beeps.wav"},
    ])
    bells.save_schedule({"name": "Main", "effective_from": "2026-09-01", "days": {"mon": t2["id"]},
                         "sound": "Classic school bell.wav"}, bells.list_schedules()[0]["id"])
    plan = bells.plan_for(MON)
    assert [w["code"] for w in plan["warnings"]] == ["same_minute"]


def test_schedule_validation(env):
    regular = tt()
    with pytest.raises(ValueError, match="end date"):
        schedule(days={"mon": regular["id"]}, eff_from="2026-09-10", eff_to="2026-09-01")
    with pytest.raises(ValueError, match="no longer exists"):
        schedule(days={"mon": 9999})
    with pytest.raises(ValueError, match="zones"):
        schedule(days={"mon": regular["id"]}, zone_ids=["missing"])
    with pytest.raises(ValueError, match="Repeat count"):
        schedule(days={"mon": regular["id"]}, playback_mode="repeat", repeat_count=50)


def test_timetable_in_use_cannot_be_deleted(env):
    regular = tt()
    schedule(days={"mon": regular["id"]})
    with pytest.raises(ValueError, match="still used"):
        bells.delete_timetable(regular["id"])


def test_occurrences_use_configured_timezone(env):
    regular = tt()
    schedule(days={"mon": regular["id"]})
    at = datetime.datetime.fromisoformat(bells.plan_for(MON)["occurrences"][0]["at"])
    assert at.utcoffset() == datetime.timedelta(hours=2)          # Africa/Tripoli
    config.update_section("location", {"timezone": "Europe/London"})
    at = datetime.datetime.fromisoformat(bells.plan_for(MON)["occurrences"][0]["at"])
    assert at.utcoffset() == datetime.timedelta(hours=1)          # BST in September
    assert at.strftime("%H:%M") == "07:45"


def test_dst_gap_and_repeat_ring_once(env):
    config.update_section("location", {"timezone": "Europe/London"})
    t = tt("Night", [{"label": "Gap", "kind": "custom", "start_time": "01:30"}])
    schedule(days={d: t["id"] for d in bells.WEEKDAYS}, eff_from="2026-01-01")
    spring = bells.plan_for(datetime.date(2026, 3, 29))      # 01:00 -> 02:00, 01:30 does not exist
    autumn = bells.plan_for(datetime.date(2026, 10, 25))     # 02:00 -> 01:00, 01:30 happens twice
    assert len(spring["occurrences"]) == 1 and len(autumn["occurrences"]) == 1
    s_at = datetime.datetime.fromisoformat(spring["occurrences"][0]["at"])
    assert s_at.utcoffset() == datetime.timedelta(hours=1)


def test_holiday_shown_without_schedule_and_merge_regardless_of_order(env):
    bells.save_exception({"name": "Summer", "kind": "holiday", "date_from": "2026-08-01", "date_to": "2026-08-31"})
    p = bells.plan_for(datetime.date(2026, 8, 10))
    assert p["status"] == "holiday" and p["exception"]["name"] == "Summer"
    t = tt("Mixed", [
        {"label": "A", "kind": "custom", "start_time": "09:00"},
        {"label": "B", "kind": "custom", "start_time": "09:00", "sound_start": "Three beeps.wav"},
        {"label": "C", "kind": "custom", "start_time": "09:00"},
    ])
    schedule(days={"mon": t["id"]})
    occ = bells.plan_for(MON)["occurrences"]
    assert len(occ) == 2
    assert sorted(len(o["labels"]) for o in occ) == [1, 2]
    lst = {x["name"]: x for x in bells.list_timetables()}
    assert lst["Mixed"]["ring_count"] == 1


def test_validation_rejects_bad_types(env):
    with pytest.raises(ValueError):
        bells.save_timetable({"name": "X", "events": ["nope"]})
    regular = tt()
    with pytest.raises(ValueError, match="whole numbers"):
        schedule(days={"mon": regular["id"]}, repeat_count="many")
    with pytest.raises(ValueError, match="timetable"):
        bells.save_exception({"name": "X", "kind": "override", "date_from": "2026-01-01", "timetable_id": "abc"})
    long = bells.save_timetable({"name": "N" * 80, "events": []})
    copy = bells.duplicate_timetable(long["id"])
    assert copy["name"] != long["name"]
