"""Fill an empty installation with example data (for demos, screenshots and UI testing).

    CONFIG_PATH=./demo/config/config.yaml AUDIO_DIR=./demo/audio python scripts/seed_demo.py

Never run this against a production installation: it adds users, zones and schedules.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import accounts, audio, bells, config, db  # noqa: E402


def main():
    cfg = config.load()
    db.open_db()
    accounts.bootstrap()
    audio.ensure_builtin_bells()
    cfg.ntp.enabled = False
    cfg.destinations = [
        config.Destination(id="hall", name="Prayer hall", uri="4090"),
        config.Destination(id="blockA", name="Block A classrooms", uri="4101"),
        config.Destination(id="blockB", name="Block B classrooms", uri="4102"),
        config.Destination(id="yard", name="Playground", uri="4103"),
    ]
    cfg.zones = [
        config.Zone(id="classes", name="Classrooms", destination_ids=["blockA", "blockB"]),
        config.Zone(id="outdoor", name="Playground", destination_ids=["yard"]),
        config.Zone(id="prayer", name="Prayer hall", destination_ids=["hall"]),
    ]
    cfg.adhan.zone_ids = ["prayer"]
    cfg.location.country_code, cfg.location.city, cfg.location.city_local_name = "LY", "Tripoli", "طرابلس"
    config.save(cfg)
    admin = accounts.get_by_username("admin")
    if admin["must_change_password"]:
        accounts.set_password(admin["id"], os.environ.get("DEMO_ADMIN_PASSWORD", "Demo-Admin-2026"))
    for username, name, role in (("bells.office", "School office", "bells_admin"), ("imam", "Imam Yusuf", "adhan_admin"),
                                 ("caretaker", "Caretaker", "operator"), ("governor", "Board member", "viewer")):
        if not accounts.get_by_username(username):
            accounts.create_user(username, password="Demo-User-2026", display_name=name, must_change_password=False,
                                 role_keys=[role])
    if not bells.list_timetables():
        rows = [{"label": "Assembly", "kind": "start_of_day", "start_time": "07:45"}]
        start = datetime.datetime(2000, 1, 1, 8, 0)
        for i in range(1, 7):
            end = start + datetime.timedelta(minutes=45)
            rows.append({"label": f"Period {i}", "kind": "period", "start_time": start.strftime("%H:%M"),
                         "end_time": end.strftime("%H:%M"), "ring_end": True})
            start = end
            if i == 3:
                brk_end = start + datetime.timedelta(minutes=20)
                rows.append({"label": "Break", "kind": "break", "start_time": start.strftime("%H:%M"),
                             "end_time": brk_end.strftime("%H:%M"), "ring_start": False, "ring_end": True,
                             "zone_ids": ["classes", "outdoor"]})
                start = brk_end
        rows.append({"label": "End of day", "kind": "end_of_day", "start_time": "13:15",
                     "sound_start": "Two-tone chime.wav"})
        regular = bells.save_timetable({"name": "Regular day", "description": "Sunday to Wednesday", "events": rows})
        short = bells.save_timetable({"name": "Short Thursday", "description": "Five 40-minute periods", "events": [
            {"label": "Period 1", "kind": "period", "start_time": "08:00", "end_time": "08:40", "ring_end": True},
            {"label": "Period 2", "kind": "period", "start_time": "08:40", "end_time": "09:20", "ring_end": True},
            {"label": "Break", "kind": "break", "start_time": "09:20", "end_time": "09:40", "ring_start": False,
             "ring_end": True},
            {"label": "Period 3", "kind": "period", "start_time": "09:40", "end_time": "10:20", "ring_end": True},
            {"label": "Period 4", "kind": "period", "start_time": "10:20", "end_time": "11:00", "ring_end": True},
            {"label": "Period 5", "kind": "period", "start_time": "11:00", "end_time": "11:40", "ring_end": True},
        ]})
        exam = bells.save_timetable({"name": "Exam day", "events": [
            {"label": "Exam", "kind": "period", "start_time": "09:00", "end_time": "11:00", "ring_end": True}]})
        today = datetime.date.today()
        year_start = datetime.date(today.year if today.month >= 9 else today.year - 1, 9, 1)
        bells.save_schedule({"name": f"School year {year_start.year}–{year_start.year + 1}",
                             "effective_from": year_start.isoformat(),
                             "effective_to": datetime.date(year_start.year + 1, 6, 30).isoformat(),
                             "days": {"sun": regular["id"], "mon": regular["id"], "tue": regular["id"],
                                      "wed": regular["id"], "thu": short["id"]},
                             "zone_ids": ["classes", "outdoor"], "sound": "Classic school bell.wav"})
        bells.save_exception({"name": "Exam week", "kind": "override",
                              "date_from": (today + datetime.timedelta(days=9)).isoformat(),
                              "date_to": (today + datetime.timedelta(days=11)).isoformat(), "timetable_id": exam["id"]})
        bells.save_exception({"name": "National holiday", "kind": "holiday",
                              "date_from": (today + datetime.timedelta(days=5)).isoformat()})
    print("Demo data ready. Sign in as admin (password from DEMO_ADMIN_PASSWORD or Demo-Admin-2026).")


if __name__ == "__main__":
    main()
