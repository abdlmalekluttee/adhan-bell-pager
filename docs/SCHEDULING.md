# Scheduling, priorities and reliability

This document is the reference for **when** broadcasts play, **what happens when two want the line at once**, and
**how the service behaves after restarts, downtime and clock changes**.

## Time and timezone

* All schedules use one **system timezone** (Administration › Time & priorities; the Adhan location page can set it
  when the user also has the *System settings* permission). Changing it moves every schedule in both modules.
* "Now" is the server clock corrected by NTP (the measured offset is applied in software; stepping the OS clock is
  optional).
* Wall-clock times are resolved in that timezone on each date, so daylight-saving changes are handled:
  * a time that does not exist on a spring-forward day rings once, just after the jump;
  * a time that occurs twice on a fall-back day rings once, the first time.

## What gets scheduled

The scheduler keeps a plan for **today and tomorrow**, rebuilt at midnight, every minute, and immediately after any
settings change.

* **Adhan:** each enabled prayer (calculated or from the imported timetable, plus per-prayer adjustment), and the
  iqama when enabled.
* **School bells:** see [SCHOOL_BELLS.md](SCHOOL_BELLS.md) for how a date resolves to a timetable. Bells at the same
  minute with the same sound and zones ring once.

## Exactly once

Every occurrence has a unique key, and a row is written to the execution history **before** anything plays. A key
can never be played twice — not after a restart, a clock correction, a repeated DST hour or a settings change.

| Module | Key | Consequence |
|---|---|---|
| Adhan | `adhan:<date>:<prayer>:<adhan|iqama>` | Adjusting a prayer by a minute after it played does not play it again that day. |
| Bells | `bells:<date>:s<schedule>:<HH:MM>:<rows>` | Moving a bell to a new time schedules a new bell at the new time. |

## Priority policy

One broadcast uses the paging line at a time.

| Priority | Broadcast |
|---|---|
| 1 (highest) | **Manual**: ring now, test, play a file |
| 2 | **Adhan** and iqama (scheduled) |
| 3 | **School bell** (scheduled) |

Rules:

1. **A scheduled broadcast with higher priority interrupts a lower one.** The Adhan cuts off a school bell that is
   still ringing. The bell is recorded as *interrupted* and is not replayed.
2. **A scheduled broadcast with equal or lower priority waits** for the line. If it cannot start within its
   **maximum delay** it is recorded as *skipped*:
   * Adhan: 300 seconds (default)
   * School bell: 60 seconds (default)

   If it starts late but within the limit, it plays and the history shows how late ("Started 40 s late").
3. **Manual broadcasts never interrupt silently.** If the line is busy, the request is refused and the operator sees
   what is playing. They may choose *Stop it and ring*, which is allowed only if they have permission to stop the
   broadcast that is playing (a bells-only operator cannot cut off the Adhan).
4. **Scheduled broadcasts never interrupt a manual one.** They wait (rule 2).
5. **Stop** ends the current broadcast immediately on every speaker and releases the call; nothing is replayed.

The School Bells › Today and Adhan › Today pages show predicted conflicts in advance, for example *"Overlaps Dhuhr
Adhan, which has priority. This bell will probably be skipped."*

## Restarts, downtime and missed broadcasts

* **Late grace.** An occurrence can still start if it is at most this late when the service reaches it:
  Adhan 120 s, bells 60 s (configurable). Otherwise it is recorded as ***missed*** and is **never played late**.
* **Heartbeat.** The scheduler records a heartbeat every 15 s. After a restart it looks at occurrences between the
  last heartbeat and now:
  * inside the grace window → played now (with its delay recorded);
  * older → recorded as *missed* with the outage window ("offline 07:58–08:10").
* **Interrupted work.** Anything that was waiting or playing when the service stopped is recorded as *interrupted*
  and is not replayed.
* **Fresh installs** do not record anything as missed.
* **Added after its time.** A bell or prayer that is added or moved to a time that has already passed today is
  ignored, not played immediately.
* **Clock jumps / paused VM.** If the process is stalled beyond the grace window, the occurrence is *missed*.

## Execution statuses

| Status | Meaning |
|---|---|
| Waiting | Accepted, waiting for the line |
| Playing | On the line now |
| Played | Played to every destination |
| Partly played | Played to some destinations, not all (see per-destination results) |
| Failed | Nothing played: no answer, busy, SIP error, missing audio, no destinations |
| Stopped | Stopped by an operator |
| Interrupted | Cut off by a higher-priority broadcast, or the service stopped mid-way |
| Skipped | Never started: the line stayed busy longer than the maximum delay |
| Missed | The service was not running (or was stalled) at the scheduled time |

Failed, partly played, interrupted, skipped and missed broadcasts appear on the dashboard under *Recent problems*
and in the audit log.

## Per-destination results and "was it heard?"

For each destination the history records: answered or not (no answer, busy, rejected), negotiated codec, seconds
played, whether the audio reached its end, and **RTP packets sent and received**. RTP counts prove that audio left the
server and that the far end had an active media stream. They cannot prove that a speaker was switched on or audible;
confirm that by listening during a test.
