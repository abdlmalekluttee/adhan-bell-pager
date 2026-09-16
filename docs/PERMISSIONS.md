# Users, roles and permissions

## Model

* Every action in the app requires a **permission**. The server checks it on every API request; hiding a button is
  never the only protection. Anything not explicitly allowed is denied.
* A **role** is a named set of permissions. Users get permissions from their roles.
* A user's **effective permissions** are the **union** of all their roles — roles assigned directly in the app plus
  roles granted by Active Directory group mappings. There are no "deny" rules.
* Each action permission includes the module's *view* permission.
* Administration › Users › *edit* shows each user's effective permissions and **where each one comes from**
  (for example *"School Bells Administrator (AD group: Pager Bells)"*). Users see the same list under *My account*.

## Permissions

| Area | Permission | Allows |
|---|---|---|
| Adhan | View Adhan | Prayer times, Adhan schedule and status |
| | Edit Adhan schedule | Calculation, prayers, iqama, location, timetable import, where the Adhan plays |
| | Manage Adhan sounds | Upload, assign, delete Adhan recordings |
| | Start and test Adhan | Test an Adhan or play a file now |
| | Stop Adhan | Stop an Adhan that is playing |
| | View Adhan history | Past Adhan broadcasts and failures |
| School Bells | View School Bells | Today's bells, timetables, schedules |
| | Edit bell schedules | Timetables, weekly schedules, holidays and changes |
| | Manage bell sounds | Upload and delete bell sounds |
| | Ring and test bells | Ring now, tests |
| | Stop bells | Stop a bell that is ringing |
| | View bell history | Past bells and failures |
| Administration | Manage destinations and zones | Speakers, paging groups and zones used by both modules |
| | Configure SIP | SIP account, codecs, call behaviour, reconnect |
| | System settings | Timezone, time sync, broadcast priority settings |
| | Configure Active Directory | Directory connection and group mappings |
| | Manage users | Create, edit, disable users; assign roles |
| | Manage roles | Create and edit custom roles |
| | View audit log | Who changed what |
| | View service logs | Technical logs |

## Built-in roles

| Role | Gets |
|---|---|
| **Full Administrator** | Everything, including permissions added in future versions |
| **Adhan Administrator** | All Adhan permissions; nothing in School Bells or Administration |
| **School Bells Administrator** | All School Bells permissions; nothing in Adhan or Administration |
| **Operator** | View, ring/test, stop and history in both modules; cannot change schedules or sounds |
| **Viewer** | View and history in both modules |

Built-in roles cannot be edited or deleted. Create **custom roles** for anything else (Administration › Roles), for
example "Bells operator" = View School Bells + Ring and test bells + Stop bells.

## Common setups

| The person needs… | Give them |
|---|---|
| Only School Bells | School Bells Administrator |
| Only the Adhan | Adhan Administrator |
| Both modules, no security settings | Adhan Administrator + School Bells Administrator |
| Everything | Full Administrator |
| To ring bells but not change timetables | Operator, or a custom role |

## Safety rules (privilege escalation)

* You can only grant, map or put into a custom role **permissions you have yourself**.
* You cannot edit, disable, reset or delete a user who has permissions you do not have (for example a Full
  Administrator, unless you are one).
* A manual broadcast may only interrupt something that you are allowed to stop.
* Changing the system timezone from the Adhan location page also needs *System settings*.
* You cannot disable or delete yourself. The system refuses any change that would leave **no enabled Full
  Administrator**.

## Recovery administrator

The first local administrator (`admin` on new installs, or the existing v1 account after an upgrade) is the
**recovery account**: it is always a Full Administrator, cannot be disabled or deleted, and always signs in locally,
even when Active Directory is enabled or unavailable.

Forgot its password? On the server:

```bash
cd /opt/adhan-bell-pager
docker compose exec adhan-bell-pager python -m app.cli reset-password
```

The new password must be changed at the next sign-in. Other commands: `python -m app.cli users` (list users and
roles), `python -m app.cli disable-directory` (turn off AD sign-in).

## Sessions

* Sessions are stored server-side and can be ended: *My account › Sign out other devices*; disabling a user or
  resetting their password ends their sessions immediately.
* Browser sessions expire after 8 hours (2 hours idle); *Keep me signed in* lasts 7 days.
* After 5 failed sign-ins for a username from one address, sign-in is paused for 5 minutes.
* Cookies are `HttpOnly`, `SameSite=Strict`; set `AUTH_COOKIE_SECURE=true` when serving over HTTPS. Every change
  request carries a CSRF token.

## Audit log

Administration › Audit log records sign-ins (successful and denied), user and role changes, AD settings and group
mappings, SIP and codec changes, destinations and zones, schedule changes in both modules, sound uploads and
deletions, manual broadcasts, stops, and failed/skipped/missed/interrupted broadcasts, service restarts and outages.
Each entry has the actor, time, address, action, target and result. Secrets are never recorded.
