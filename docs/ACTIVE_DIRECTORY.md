# Active Directory sign-in (optional)

Staff can sign in with their Windows domain account. Access is decided by **AD group membership**, mapped to roles
in the app. Local accounts, including the recovery administrator, keep working at all times.

## What you need

* A domain controller reachable from the server on **LDAPS (636)** or **LDAP with StartTLS (389)**. Plain LDAP is
  not offered.
* The DC's certificate must be trusted: either issued by a public CA, or paste your internal **root CA certificate
  (PEM)** in the settings. Keep *Validate certificate* on.
* A **read-only bind account** (a normal domain user is enough), for example
  `CN=svc-pager,OU=Service Accounts,DC=school,DC=local`.
* Security groups for access, for example:

| AD group | Mapped role |
|---|---|
| Pager School Bells | School Bells Administrator |
| Pager Adhan | Adhan Administrator |
| Pager Operators | Operator |
| Pager Admins | Full Administrator |

## Setup

Administration › **Active Directory**:

1. **Domain controllers:** one per line, e.g. `ldaps://dc1.school.local` and `ldaps://dc2.school.local`
   (the second is used if the first is down).
2. **Connection security:** LDAPS or StartTLS. **CA certificate:** paste the PEM if needed.
3. **Bind account DN** and **password**. The password is encrypted on disk and never shown again.
4. **Base DN:** e.g. `DC=school,DC=local`. Advanced: separate user/group search bases, user filter, nested groups.
5. **Test connection.** Then use **Check a user** with a real username to see their groups, which roles they would
   get, and whether they could sign in (optionally with their password).
6. Add **group mappings** (search for the group by name, pick a role).
7. Turn on **Enable directory sign-in** and save.

Users sign in with `name`, `SCHOOL\name` or `name@school.local`.

## Rules

| Situation | Result |
|---|---|
| Valid AD user in one or more mapped groups | Signs in; permissions = union of all mapped roles + roles assigned directly in the app |
| Valid AD user in no mapped group | Sign-in refused: *not a member of any group that has access* |
| Wrong password | *Invalid username or password* (same message as an unknown user) |
| Account disabled, locked out, expired, password expired | Sign-in refused with the reason |
| A local account with the same username exists | The local account is used; the directory is not consulted |
| Nested groups (group inside a mapped group) | Counted when *Include nested groups* is on (default) |

**Passwords:** domain passwords are only used to bind to the directory at sign-in and are never stored. AD users
cannot change their password in this app.

## Refresh policy

* Group membership and account state are read **at every sign-in**.
* For signed-in users they are re-read at least every **15 minutes** (*Refresh interval*, 5–240) in the background
  while the user is active.
* If a refresh finds the account disabled, deleted, or no longer in any mapped group, **all their sessions end
  immediately**.
* **Removing a group mapping** takes effect on the next request of every affected user (mappings are applied live).

## Directory outages

* **New AD sign-ins fail** with *The directory is unavailable* until a DC answers. Local accounts work normally.
* **Signed-in AD users keep their last known permissions** for the *Outage grace period* (default **60 minutes**
  after the last successful check). After that, their requests are refused until the directory can confirm them
  again. Set the grace period to 0 to refuse immediately when a refresh is due and the directory is unreachable.
* Scheduled Adhan and bells are **not affected** by directory outages.

If the directory configuration locks everyone out, sign in with the recovery administrator, or on the server:

```bash
docker compose exec adhan-bell-pager python -m app.cli disable-directory
```

## What was tested

* Automated tests (`tests/test_directory.py`) run the real search, bind, nested-group, mapping, refresh, disabled
  account, revoked membership, outage/grace and escalation logic against an in-memory LDAP directory (ldap3 mock).
* **Not verified against a live Microsoft domain controller** in this release: certificate validation against your
  PKI, AD-specific bind error codes (disabled/locked/expired return codes), and large-directory performance. Use
  *Test connection* and *Check a user* during setup, and report any difference.
