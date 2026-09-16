# School bells: setting up timetables

This guide is written for school office staff. Everything here is done in the web interface under **School Bells**.

## The three building blocks

| Block | What it is | Example |
|---|---|---|
| **Timetable** | A reusable plan for one kind of day: start of day, periods, breaks, end of day. | "Regular day", "Short Thursday", "Exam day" |
| **Weekly schedule** | Which timetable runs on each weekday, between a start date and an optional end date, where the bells ring and which sound plays. | "School year 2026–2027": Sun–Wed Regular day, Thu Short Thursday |
| **Holidays & changes** | Dates that differ from the weekly schedule. | "National day" (holiday), "Exam week" (use Exam day timetable) |

## 1. Prepare speakers and zones (administrator)

Administration › **Speakers & zones**:

1. Add each speaker, phone or PBX paging group with its extension (for example `4090`).
2. Create zones such as *Classrooms*, *Playground*, *Prayer hall* and tick the speakers in each.

If you do not create zones, bells ring on every enabled speaker.

## 2. Create a timetable

School Bells › **Timetables** › *New timetable*.

* Choose **Generate periods** to fill the day quickly: first period start, period length, number of periods, gaps,
  and one or more breaks.
* Each row has a type:
  * **Start of day** / **End of day** / **Single bell** — one bell at one time.
  * **Period** / **Break** — a start and end time. Tick *Ring at start* and/or *Ring at end*.
* When the end of one period and the start of the next are the same minute with the same sound and zones, **one**
  bell rings.
* Under *More* on a row you can use a different sound or different zones for that bell only (for example, the break
  bell only in the playground).

The editor warns you about overlapping periods and several different bells in the same minute.

## 3. Create the weekly schedule

School Bells › **Weekly schedules** › *New schedule*:

* **Dates:** from the first school day; leave the end date empty to run until changed.
* **Days:** pick a timetable for each school day. *Sunday–Thursday* and *Monday–Friday* buttons fill the week.
  Days without a timetable are not school days.
* **Where:** choose zones (none = all enabled speakers).
* **Sound and how it plays:** once, several times (1–20), or for a fixed number of seconds (1–600).

**Several schedules at once:** you can keep the normal year schedule and add a *Ramadan* schedule for its dates. When
two enabled schedules cover the same day, **the one that starts latest wins**. The pages show a warning so this is
never a surprise.

## 4. Holidays and special days

School Bells › **Holidays & changes** › *Add*:

| Type | Use it for | Effect |
|---|---|---|
| Holiday | Public holidays, school breaks | No bells |
| No bells | Exams without bells, maintenance days | No bells |
| Different timetable | Half days, exam days, events | Uses the chosen timetable instead of the normal one |

A change can apply to all schedules or to one schedule. **Holidays and no-bell days always win** over a different
timetable on the same date. Two *different timetable* changes cannot overlap.

## 5. Check today

School Bells › **Today** shows every bell of the day with its time, name, sound and zones, what already played and
how it went, the next bell with a countdown, and the next two weeks at a glance. Use the arrows to look at other days.

Warnings you may see:

* *Overlaps Dhuhr Adhan, which has priority.* The Adhan and a bell compete for the speakers; see the priority policy
  in [SCHEDULING.md](SCHEDULING.md).
* *A bell sound used today is missing.* Upload the sound again or pick another one.

## Ring now, test and stop

* **Ring a bell now** (School Bells › Today): choose a sound, zones and how it plays. Tick *This is a test* to label it
  as a test in the history.
* **Stop** appears in the bar at the top of every page while something is playing, for users allowed to stop it.
* If something else is already playing you are asked before it is stopped.

## History

School Bells › **History** lists every bell with its result (played, failed, skipped, missed, stopped, interrupted),
how late it started, and per-speaker details. Use *Problems only* to find what needs attention.

## Behaviour after a power cut or restart

Bells are never played late by surprise. A bell that was due while the server was off is recorded as *missed*. If
the service comes back within 60 seconds of a bell, that bell still rings. Details: [SCHEDULING.md](SCHEDULING.md).
