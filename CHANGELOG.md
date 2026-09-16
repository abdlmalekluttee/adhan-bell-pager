# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [2.0.0] - 2026-09-17

First release of **Adhan & Bell Pager**, the successor of Adhan Pager 1.7. Existing v1 installations upgrade in
place (see `docs/DEPLOYMENT.md`).

### Added

- **School Bells module**: reusable day timetables (start/end of day, periods, breaks, single bells, per-bell sound and
  zone overrides), weekly schedules with effective dates, holidays, no-bell days and date-range timetable overrides,
  zones, play once / repeat N times / for N seconds, today view with live status and countdown, two-week outlook,
  conflict warnings, ring now, test, stop, and execution history. Three synthesized bell sounds are included.
- **Broadcast dispatcher** with a documented priority policy (manual > Adhan > bell), maximum delays, interruption
  and explicit "stop and ring" confirmation for manual broadcasts.
- **Exactly-once execution**: every broadcast is recorded under a unique key before it plays; restarts, clock
  corrections and DST repeats cannot replay it. Missed broadcasts during downtime are recorded, never played late;
  a restart within the grace window still plays the due broadcast.
- **Unified role-based access control** for both modules and administration: 20 permissions, five built-in roles,
  custom roles, effective permissions with their sources, escalation guards, recovery administrator, server-side
  enforcement on every API route (deny by default, verified by a test).
- **Optional Active Directory sign-in**: LDAPS/StartTLS with certificate validation, encrypted bind password,
  connection and user/group lookup tests, nested groups, group-to-role mappings, refresh every 15 minutes, outage
  grace period, immediate revocation for disabled accounts or removed memberships.
- **Audit log** of sign-ins, user/role/AD changes, SIP and destination changes, schedule changes, manual broadcasts,
  stops, failures, skipped/missed broadcasts, restarts and outages, with secrets redacted.
- **Redesigned web interface** on Tabler: dashboard with system health, live broadcast bar with progress and stop,
  separate Adhan / School Bells / Administration areas, full Arabic (Cairo typeface) and English with RTL, light/dark/
  system themes saved per user, responsive layouts, accessible dialogs, loading/empty/error states.
- Per-destination call results: answered, busy, no answer, rejected, codec used, seconds played, RTP packets sent and
  received.
- Speakers & zones shared by both modules; the Adhan can target zones.
- Deployment tooling: `install.sh`, online `backup.sh`, `restore.sh`, `upgrade.sh` with automatic rollback,
  `python -m app.cli` (reset password, list users, disable directory, backup).
- CI (lint, 110+ tests, JavaScript checks, Docker build and smoke test), release workflow, Dependabot.

### Fixed (from Adhan Pager 1.7)

- Unanswered calls rang for up to the 10-minute safety limit: the ring timeout is now enforced.
- The answer delay slept on PJSIP's only worker thread, stalling all SIP processing during every page.
- PJSIP was called from several Python threads (risk of "unknown thread" aborts and thread-descriptor leaks): all
  PJSIP work now runs on one dedicated thread.
- TCP and TLS transports did not add `;transport=` to registrar and destination URIs, so registration and calls over
  TCP/TLS could not work.
- Failed, busy or unanswered pages were recorded as successful.
- Repeated clicks on "test" queued duplicate pages; a new page could start while another was waiting.
- Changing SIP settings during a call destroyed the endpoint under the active call.
- Registration failures other than timeouts (401/403/404) were never retried; retries now back off up to 15 minutes.
- Changing the timezone did not move the daily refresh; the NTP offset was ignored when firing jobs.
- `GET /api/config` returned the SIP password to the browser.
- The `web.port` setting was ignored (the container always listened on 8080).
- Session tokens stayed valid for 7 days even without "remember me"; logout ended every session of the user.
- Deleting an audio file that was assigned to a prayer silently broke that prayer's Adhan.
- Uploads were read fully into memory before the size check; conversion errors were unreadable.
- Unrelated `sunrise` time was returned in UTC instead of the configured timezone.

### Changed

- Runtime data now also includes `config/app.db` (SQLite) and `config/secret.key`; back them up with `config.yaml`.
- The container runs as an unprivileged user; the image is multi-stage and smaller.
- The v1 "accent colour" preference is replaced by module colours; the single admin account is replaced by users and
  roles (the v1 admin becomes the recovery administrator).

## [1.7.0] - 2026

Adhan Pager 1.7.0 by the Adhan Pager contributors (original project: `abdlmalekluttee/adhan-pager`).
