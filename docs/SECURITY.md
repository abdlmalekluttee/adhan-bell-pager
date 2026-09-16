# Security

## Summary of controls

| Area | Control |
|---|---|
| Authorization | Deny by default. Every API route declares its permission; a test fails the build if one does not. Module isolation and escalation guards are covered by automated tests. |
| Passwords | bcrypt (cost 12). Minimum 8 characters, common passwords refused. Default `admin/admin` must be changed before anything else is reachable. |
| Sign-in throttling | 5 failures per username+address → 5-minute pause; 20 per address. Unknown users take the same time and message as wrong passwords. |
| Sessions | Random 256-bit tokens, only their SHA-256 stored; idle and absolute expiry; revoked on logout, password change/reset, disable, AD revocation. Cookies `HttpOnly`, `SameSite=Strict`, optional `Secure`. |
| CSRF | Per-session token required on every state-changing request; sign-in requires JSON. |
| Browser | Strict Content-Security-Policy (no inline scripts), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`. No third-party network requests: all assets are bundled. |
| Secrets | SIP password never returned by the API (write-only, `has_password`). Directory bind password encrypted with Fernet (`config/secret.key`). Secrets redacted from audit details and log lines. `config/` is mode 700, `config.yaml`, `app.db`, `secret.key` mode 600. |
| Uploads | 50 MB limit streamed to disk; extension allow-list; names sanitised (no paths); decoded and re-encoded by ffmpeg; files in use cannot be deleted. |
| Input validation | Pydantic models with ranges for all settings; timetable/schedule validation; LDAP filter escaping; SIP URIs validated. |
| Container | Runs as uid 10001; no privileged mode; host networking only for SIP/RTP. |
| Audit | Administrative and operational actions with actor, time, address, target, result. |

## Reporting

Please report vulnerabilities privately to the repository owner rather than in public issues.

## Hardening checklist for deployments

* Restrict the web port to staff networks (firewall), or put the app behind an HTTPS reverse proxy and set
  `AUTH_COOKIE_SECURE=true`.
* Allow SIP/RTP only from the PBX.
* Use a dedicated PBX extension with a strong password and, if supported, an IP ACL for this server.
* Keep *Validate certificate* on for Active Directory.
* Store backups securely: they contain the SIP password and password hashes.
* Review the audit log and dashboard problems regularly.
