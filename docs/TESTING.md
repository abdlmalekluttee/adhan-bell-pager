# Testing

Three kinds of evidence are kept apart in this project:

| Kind | What it proves | Where |
|---|---|---|
| **Automated** | Logic and API behaviour, run on every push in CI | `tests/` (pytest), JS syntax, shell syntax, Docker build + container smoke test |
| **Simulated** | End-to-end flows with a fake SIP backend (`SIP_SIMULATE=1`) | automated tests and UI review |
| **Live** | Real registration, calls, RTP and scheduling against a real PBX and auto-answer extension | `tests/live/live_sip_test.py` (manual, credentials from environment) |

## Automated tests (123 tests)

```bash
pip install -r requirements-dev.txt
pytest -q
```

| Area | File | Covers |
|---|---|---|
| School bell rules | `test_bells.py` | weekday mapping, merged same-minute bells, effective dates (latest start wins), disabled schedules, holiday > override, scoped exceptions, overlapping overrides rejected, validation errors and warnings, same-minute warnings in schedule context, timezone use, DST gap and repeated hour, holidays without a schedule |
| Scheduling safety | `test_scheduler.py` | plays exactly once, restart does not replay, missed during downtime (recorded, not played), restart inside grace plays late, stalled process → missed, bells added after their time ignored, crash mid-broadcast → interrupted and not replayed, duplicate submissions ignored, Adhan interrupts a bell, bell waiting behind the Adhan is skipped, manual conflict requires force, missing audio / no destinations fail with reasons, conflict prediction |
| SIP lifecycle | `test_engine.py` | answered call plays and hangs up, ring timeout enforced, busy/rejected/partial results, parallel paging, manual stop releases immediately, remote hang-up, no media, engine not running, 15 back-to-back calls without leaks, one page on the line at a time, restart during a call, URI transport/port, bounded registration back-off, registration messages |
| Permissions | `test_permissions.py` | every API route declares authorization (deny by default), built-in roles, bells-only and Adhan-only users blocked from other modules and administration on 25 endpoints, viewer read-only, operator can ring but not edit, a bells operator cannot interrupt the Adhan, escalation guards for users/roles, built-in roles immutable, recovery account and last-admin protection (no partial writes), permission sources, timezone change needs system permission, secrets never returned or audited, zones used by the Adhan protected |
| Authentication | `test_auth.py` | forced first password change, lockout, uniform unknown-user error, CSRF and logout revocation, JSON-only sign-in, disabled users, users without roles, session expiry, long passwords, **v1.7 upgrade** (config, destinations, prayer settings, users.json migration, backups, idempotence), corrupt v1 user store never falls back to default credentials |
| Active Directory | `test_directory.py` | nested-group sign-in, DOMAIN\\user and UPN forms, union of groups, full-admin mapping, wrong password / unmapped / disabled accounts, empty-password bind refused, directory outage (within refresh, within grace, beyond grace, recovery, new sign-ins fail, local accounts work), revoked membership ends sessions, mapping removal applies immediately, local account precedence, bind password hidden and encrypted, connection/user/group lookup tools, config validation, mapping escalation guard — against an in-memory LDAP directory |
| Audio and API workflows | `test_audio_and_api.py` | repeat/duration rendering, upload sanitising, conflicts, delete guard, full bell workflow with zones and audit trail, manual conflict via API, SIP validation, public health endpoint, security headers |
| Translations | `test_i18n.py` | every UI string has an Arabic translation, placeholders match, translations are Arabic |

## Live tests on the development VM

Environment: Ubuntu 24.04 VM, Docker deployment on port 8090, Asterisk PBX (chan_sip), dedicated test extension,
auto-answer destination. Run 2026-09-17. Only short calls to the test destination were placed; no PBX configuration
was changed.

```bash
ABP_URL=http://<vm>:8090 ABP_ADMIN_PASSWORD=… SIP_SERVER=<pbx> SIP_USER=<ext> SIP_PASSWORD=… SIP_DEST=<dest> \
SSH_HOST=<vm-ssh-alias> SUDO_PASSWORD=… python tests/live/live_sip_test.py --report live-report.json
```

| Test | Result | Evidence (measured) |
|---|---|---|
| Registration over UDP 5060 | Pass | 200 OK immediately; PBX grants 115 s expiry |
| Periodic renewal | Pass | Re-registered every 115 s |
| Bell test call (auto-answer, playback, hang-up) | Pass | Answered in 1.2 s, **PCMA/8000**, 1.5 s played to end, ~166 RTP packets sent / ~163 received, clean BYE |
| Repeat count ×2 | Pass | 3.3 s played |
| Adhan test (8 s recording) | Pass | 8.0 s played, 431 RTP packets sent |
| Manual stop | Pass | Call released 0.5–0.7 s after Stop; 0 calls and 0 players left |
| Five back-to-back calls | Pass | All played; 0 leaked objects |
| Manual broadcast on a busy line | Pass | 409 with what is playing; forced request stops the first (recorded *Stopped by admin to start …*) |
| Permission enforcement on the live API | Pass | Bells-only user: 403 for Adhan test, SIP read/write, reconnect, codecs, users; bell ring 200 |
| Codec negotiation | Pass | µ-law only → **PCMU**; G.722 only → **G722/16000**; Opus first → **opus/48000**; default offer → **PCMA** (PBX preference) |
| SIP over TCP | Pass | Registered over TCP and played a call; switched back to UDP |
| Wrong SIP password | Pass | 403 with a clear message, next retry scheduled (bounded back-off), recovered when fixed. Limited to one failed attempt to avoid PBX intrusion bans. |
| Scheduled bell and scheduled Adhan | Pass | Bell and Adhan (from an imported one-day timetable) started within 1 s of their minute |
| Conflict policy (Adhan vs bells) | Pass (run 2) | See below |
| Restart during a due bell | Pass | Container stopped 10 s before the bell and started 20 s after: re-registered; the bell played **once**, 22 s late (inside the 60 s grace); history total +1 |
| Network interruption to the PBX (> expiry) | Pass | Loss detected after ~110 s (*The PBX could not be reached*, retry scheduled); a call during the outage failed cleanly; re-registered 19 s after restoring connectivity |

**Audible playback.** RTP counters prove audio was sent to and media was received from the destination for every
call. Whether the destination speaker was audible was **not** observed by the test harness; confirm by listening
during a test broadcast.

### Conflict policy run

Run 1 used a 45 s bell that finished before the Adhan minute, so no interruption could occur (a test-design error).
Run 2 used a 75 s bell overlapping the Adhan:

RESULTS_RUN2

## UI review

* Every page reviewed in the browser at 1440×900 and 390×844, English and Arabic (RTL), light and dark.
* `impeccable detect` on the static UI: 4 anti-patterns found and fixed; 1 advisory remaining from vendored Tabler CSS.
* Independent finish review (Impeccable finish-reviewer procedure): found a crash on Adhan › Today and polish issues
  (contrast on sign-in, RTL order of a SIP address, status colours, side stripe, spacing, 12-hour time inputs); all
  fixed and re-captured.

## Not verified live

* **Microsoft Active Directory**: tested against an in-memory LDAP directory only. Certificate validation against a
  real PKI, AD-specific bind error codes and group expansion on a real domain controller remain to be checked with
  *Test connection* and *Check a user*.
* **TLS SIP transport (5061)**: the PBX accepts TLS connections, but TLS registration was not tested (no certificate
  configuration was provided or changed).
* **Audible audio** at the destination (see above).
* **Full VM reboot**: not performed, because the VM also runs another production-like service. Boot persistence relies
  on the Docker service being enabled and `restart: unless-stopped` (both verified); a container restart was tested.
* **Paging groups / multiple destinations on the real PBX**: parallel and sequential paging were tested with the
  simulator; the live tests used one destination.
