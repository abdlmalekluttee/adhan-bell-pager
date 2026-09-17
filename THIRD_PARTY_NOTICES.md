# Third-party notices

Adhan & Bell Pager is derived from **Adhan Pager** (Copyright 2026 Adhan Pager Contributors) and is distributed under
the PolyForm Noncommercial License 1.0.0 (see `LICENSE`).

It bundles or depends on the following third-party software. Their licenses apply to those components.

## Bundled in the web interface (`app/static/vendor`, `app/static/fi`)

| Component | Version | License | Source |
|---|---|---|---|
| Tabler (CSS and JS, includes Bootstrap and Popper) | 1.5.1 | MIT | https://github.com/tabler/tabler |
| Tabler Icons (webfont) | 3.46.0 | MIT | https://github.com/tabler/tabler-icons |
| Noto Sans (variable, Latin subsets, via Fontsource) | 5.3.0 | SIL Open Font License 1.1 | https://github.com/notofonts/latin-greek-cyrillic |
| Cairo (variable, Arabic and Latin subsets, via Fontsource) | 5.3.0 | SIL Open Font License 1.1 | https://github.com/Gue3bara/Cairo |
| flag-icons | as shipped in Adhan Pager 1.7 | MIT | https://github.com/lipis/flag-icons |

License texts: `app/static/vendor/tabler/LICENSE`, `app/static/vendor/tabler-icons/LICENSE`,
`app/static/vendor/fonts/OFL-NotoSans.txt`, `app/static/vendor/fonts/OFL-Cairo.txt`.

## Built into the Docker image

| Component | License |
|---|---|
| PJSIP / pjproject 2.14.1 (downloaded and compiled during the build) | GPL-2.0-or-later (commercial licensing available from Teluu) |
| FFmpeg (Debian package) | LGPL-2.1-or-later / GPL depending on build |
| Python 3.11 | PSF License |

## Python packages (`requirements.txt`)

| Package | License |
|---|---|
| acme, josepy (Certbot project) | Apache-2.0 |
| adhanpy | MIT |
| bcrypt | Apache-2.0 |
| cryptography | Apache-2.0 or BSD-3-Clause |
| dnspython | ISC |
| FastAPI, Starlette | MIT, BSD-3-Clause |
| geonamescache (GeoNames data, CC BY 4.0) | MIT |
| ldap3 | LGPL-3.0 |
| ntplib | MIT |
| pydantic | MIT |
| python-multipart | Apache-2.0 |
| pyOpenSSL | Apache-2.0 |
| pyRFC3339 | MIT |
| PyYAML | MIT |
| requests | Apache-2.0 |
| tzdata | Apache-2.0 |
| uvicorn | BSD-3-Clause |

## Development tooling (not distributed)

| Component | License |
|---|---|
| Impeccable design skill (`pbakaus/impeccable`) — used during the redesign; not included in the repository or release | Apache-2.0 |
| pytest, httpx, ruff | MIT / BSD-3-Clause |

The built-in bell sounds (`Classic school bell`, `Two-tone chime`, `Three beeps`) are synthesized by the application
at first start and contain no third-party recordings.
