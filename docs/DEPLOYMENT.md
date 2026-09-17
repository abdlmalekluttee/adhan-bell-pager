# Deployment, upgrades, backups and rollback

## Requirements

* Linux server or VM (tested on Ubuntu 24.04), 2 CPU / 2 GB RAM minimum, 4 GB free disk.
* Docker Engine with Docker Compose v2, `curl`.
* Network access from the server to the PBX (SIP + RTP) and from staff PCs to the web port.
* The first image build downloads and compiles PJSIP (about 5–10 minutes). Later builds use the cache.

## Install

```bash
sudo mkdir -p /opt/adhan-bell-pager && sudo chown "$USER" /opt/adhan-bell-pager
cd /opt/adhan-bell-pager
tar xzf ~/adhan-bell-pager-2.1.0.tar.gz --strip-components=1      # or: git clone … .
cp .env.example .env && nano .env                                  # WEB_PORT, TZ
./scripts/install.sh
```

`install.sh` runs the preflight check, builds the image `adhan-bell-pager:<version>`, starts the container with
`restart: unless-stopped`, and waits for `/api/health`.

Open `http://<server>:<WEB_PORT>`, sign in as **admin / admin**, and choose a new password when asked. Then:

1. Administration › **Phone system**: PBX address, extension, password, transport. Wait for *Connected*.
2. Administration › **Speakers & zones**: add extensions / paging groups and zones.
3. Administration › **Time & priorities**: timezone and time sync.
4. **Adhan**: location, prayer schedule, sounds, where it plays. Use *Test* on Adhan › Today.
5. **School Bells**: timetables, weekly schedule, holidays ([SCHOOL_BELLS.md](SCHOOL_BELLS.md)).
6. Administration › **Users** / **Active Directory**.
7. Optional: Administration › **HTTPS** for a Let's Encrypt certificate ([HTTPS.md](HTTPS.md)).

### `.env` settings

| Variable | Default | Purpose |
|---|---|---|
| `WEB_PORT` | 8080 | Web UI port, plain HTTP (host networking) |
| `HTTPS_PORT` | 8443 | HTTPS port, opened once a certificate is set up in Administration › HTTPS (443 works) |
| `TZ` | Africa/Tripoli | Container OS timezone (logs). Schedules use the timezone set in the app. |
| `AUTH_COOKIE_SECURE` | false | `true` only behind a separate HTTPS reverse proxy (built-in HTTPS handles cookies itself) |
| `SIP_LOG_LEVEL` | 3 | 4 logs SIP messages for troubleshooting |
| `APP_VERSION` | set by scripts | Image tag used by compose |

Never put SIP or directory passwords in `.env`; enter them in the web UI.

### Persistent data

| Path | Contents |
|---|---|
| `config/config.yaml` | Phone system, speakers, zones, Adhan and time settings (includes the SIP password; mode 600) |
| `config/app.db` | Users, roles, sessions, bells, history, audit log (SQLite) |
| `config/secret.key` | Key that encrypts the directory bind password, DNS API tokens and the TLS private key — back it up with the database |
| `config/tls/` | Current HTTPS certificate and key for the listener (rewritten from the database at start) |
| `config/adhan-pager.log*` | Rotating service log |
| `audio/` | Adhan recordings; `audio/bells/` bell sounds |

The container runs as an unprivileged user (uid 10001) and fixes volume ownership at start. Container logs rotate
(10 MB × 5). The service starts automatically with Docker after a reboot (`systemctl is-enabled docker`).

## Backups

```bash
./scripts/backup.sh                     # safe while running; writes backups/adhan-bell-pager-<version>-<time>.tar.gz
```

The archive contains settings, a consistent copy of the database (SQLite online backup), the secret key and all
audio. It **contains secrets** (SIP password, password hashes): the `backups/` directory is mode 700 — copy archives
to secure storage. Example nightly cron:

```cron
30 2 * * * cd /opt/adhan-bell-pager && ./scripts/backup.sh >/dev/null && find backups -name '*.tar.gz' -mtime +30 -delete
```

Restore (current data is moved aside to `config.before-restore-<time>`, never deleted):

```bash
./scripts/restore.sh backups/adhan-bell-pager-2.0.0-20260917-020000.tar.gz
```

## Upgrading

```bash
cd /opt/adhan-bell-pager
./scripts/upgrade.sh ~/adhan-bell-pager-2.1.0.tar.gz
```

The script: takes a backup → keeps the current source in `.previous-source` → unpacks the new release (keeping
`config/`, `audio/`, `backups/`, `.env`) → builds and starts → waits for health. **If the new version is not healthy
it rolls back automatically**: restores the previous source, image tag and the backup.

### Manual rollback

```bash
cd /opt/adhan-bell-pager
(cd .previous-source && tar cf - .) | tar xf - -C .
sed -i 's/^APP_VERSION=.*/APP_VERSION=<previous version>/' .env
./scripts/restore.sh backups/<backup taken before the upgrade>.tar.gz
```

Previous images stay available locally (`docker images adhan-bell-pager`) until you prune them.

## Upgrading from Adhan Pager v1.x

v2 reads the v1 volumes directly; nothing needs converting by hand.

1. Back up the v1 folders first: `tar czf adhan-pager-v1-backup.tgz config audio`.
2. Stop v1: `docker compose down` in the v1 directory (container `adhan-pager`).
3. Put the v2 release in a new directory and **move or copy** `config/` and `audio/` into it (or unpack v2 over the
   v1 checkout, keeping those folders). Create `.env` (set `WEB_PORT=8080` to keep the old address).
4. `./scripts/install.sh`.

On first start v2:

* keeps `config.yaml` values (SIP, destinations, codecs, location, prayer settings, audio assignments, NTP), adds ids
  to destinations and new sections with defaults, after saving `config/backups/config-pre-upgrade-<time>.yaml`;
* migrates `users.json` into the database — **the v1 admin keeps its username and password** and becomes the
  recovery Full Administrator; the file is renamed `users.json.migrated`;
* keeps Adhan recordings in `audio/` and adds built-in bell sounds under `audio/bells/`;
* keeps an imported manual prayer timetable.

Changed in v2: the v1 "accent colour" preference is replaced by module colours; renaming the admin account from its
own page is replaced by user management; the web port now comes from `WEB_PORT` (v1 ignored `web.port`).

To go back to v1: stop v2, restore `adhan-pager-v1-backup.tgz` into the v1 directory, start v1.

## Test VM deployment (this project)

The development VM runs the service from `/opt/adhan-bell-pager` on port **8090** (port 8080 belongs to another
application there). UFW allows 8090/tcp from the management network and SIP/RTP only from the PBX.
