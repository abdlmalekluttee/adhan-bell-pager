# HTTPS with Let's Encrypt

Adhan & Bell Pager can serve its web interface on a secure `https://` address with a free certificate from
[Let's Encrypt](https://letsencrypt.org/). The certificate is obtained and renewed automatically through **DNS
validation**, using the API of your DNS provider:

| DNS provider | Supported |
|---|---|
| Cloudflare | Yes |
| cPanel (the domain's DNS zone hosted in a cPanel account) | Yes |
| Others (Google Cloud DNS, Squarespace, registrar DNS, …) | Not built in — use a reverse proxy, or move the zone's DNS to Cloudflare |

Everything is set up in **Administration › HTTPS** by a user with the *Manage HTTPS certificates* permission.

## How it works

1. You choose a host name in a public domain you own, for example `pager.yourschool.com`.
2. The app asks Let's Encrypt for a certificate. Let's Encrypt replies with a random value.
3. The app creates the TXT record `_acme-challenge.pager.yourschool.com` with that value through the DNS provider's API,
   and waits until the record is visible on the domain's authoritative name servers.
4. Let's Encrypt looks the record up in public DNS, confirms you control the domain, and issues the certificate.
5. The app removes the temporary record, stores the certificate and serves HTTPS on `HTTPS_PORT` (default **8443**).

Let's Encrypt never connects to your server, so **the server can stay on your private network**. Nothing needs to be
opened to the internet. The server does need **outbound HTTPS** to Let's Encrypt and to the DNS provider's API.

The plain `http://` address on `WEB_PORT` keeps working, so a certificate problem can never lock you out — unless
you turn on the redirect (see below).

## Before you start

* **A public domain you control**, with its DNS hosted at Cloudflare or in cPanel. Let's Encrypt cannot issue
  certificates for IP addresses (such as `10.44.172.99`) or internal-only names (`.local`, `.lan`, `.internal`).
* **An internal DNS record** that points the chosen name to the server, for example
  `pager.yourschool.com → 10.44.172.99` on your school's DNS server. The name does not need to exist in public DNS.
  People must open the app by this name; opening it by IP address shows a certificate warning.
* **Firewall:** allow `HTTPS_PORT` (default 8443/tcp) from the staff networks. On Ubuntu with UFW:

  ```bash
  sudo ufw allow proto tcp from 10.44.0.0/16 to any port 8443 comment "Adhan & Bell Pager HTTPS"
  ```

* **Outbound access** from the server: `acme-v02.api.letsencrypt.org:443`, `api.cloudflare.com:443` (Cloudflare) or
  your cPanel server on port 2083 (cPanel), and DNS (UDP/TCP 53) to public resolvers for the propagation check. If
  outbound DNS is blocked, the app waits a fixed time instead (Cloudflare 30 s, cPanel 90 s).

## Creating the API token

### Cloudflare

1. Cloudflare dashboard › **My Profile › API Tokens › Create Token**.
2. Use the **Edit zone DNS** template.
3. *Zone Resources*: **Include › Specific zone › yourschool.com** (only this domain).
4. The template gives `Zone › DNS › Edit`; also add `Zone › Zone › Read`.
5. Optional: *Client IP Address Filtering* with your school's public IP.
6. Create the token and paste it into **Cloudflare API token**. It is shown only once by Cloudflare.

### cPanel

1. cPanel › **Security › Manage API Tokens › Create**. Give it a name such as `adhan-bell-pager`, optionally an expiry.
2. Enter the **cPanel address** (usually `https://your-cpanel-server:2083`), your **cPanel username** and the token.
3. The domain must be the account's main, addon or parked domain, and its public name servers must be the ones of
   this cPanel server. If the domain uses Cloudflare or another DNS host, choose that provider instead.

Keep **Check the cPanel server's certificate** on. Turn it off only if the cPanel address has no valid certificate;
the token could then be intercepted.

## Step by step

1. Administration › **HTTPS**.
2. **Host names**: one per line. Wildcards such as `*.yourschool.com` are allowed.
3. **DNS provider**: Cloudflare or cPanel, and its credentials.
4. Click **Test DNS provider**. Tick *Also add and remove a test record* to prove the token can write DNS records.
5. **Certificate authority**: start with **Let's Encrypt staging** while testing (certificates are not trusted by
   browsers, but the limits are much higher), then switch to **Let's Encrypt**.
6. Tick **I accept the Let's Encrypt Subscriber Agreement**. The email address is optional.
7. Turn on **Serve the app over HTTPS** and **Save changes**.
8. Click **Request certificate** and watch the steps. It usually takes one to three minutes.
9. Open `https://pager.yourschool.com:8443/` from a staff computer.
10. Only after that works, optionally turn on **Send people who open the http:// address to https://**.

If you used staging, switch the certificate authority to *Let's Encrypt*, save, and click **Renew now**.

## Renewal

* The renewal check runs every 30 minutes. A certificate is renewed after **two thirds of its lifetime** (day 60 of a
  90-day certificate), so it works with shorter-lived certificates too.
* Renewed certificates are loaded into the running server; no restart and no dropped connections.
* A failed attempt is retried after 1 h, 2 h, 4 h, 8 h, then every 12 h. Each failure is written to the audit log
  (`https.certificate_failed`); the first success afterwards is logged as `https.renewal_recovered`.
* The dashboard shows an **HTTPS** status cell: *Renewal failing* once a renewal is overdue and failing, *Expiring
  soon* when less than a sixth of the lifetime is left, *Expired*, or *Not serving* if the port could not be opened.
* Renewal runs only while **Serve the app over HTTPS** is on.

## Ports

| Variable | Default | Notes |
|---|---|---|
| `WEB_PORT` | 8080 | Plain HTTP. Always on. Docker's health check uses it. |
| `HTTPS_PORT` | 8443 | Opened only while HTTPS is on and a valid certificate exists. |

To use the standard port, set `HTTPS_PORT=443` in `.env` and run `docker compose up -d`. The container keeps only the
`NET_BIND_SERVICE` capability for this; the service still runs unprivileged. Make sure nothing else on the host uses
port 443.

## The redirect

With **Send people who open the http:// address to https://** on, every request to `WEB_PORT` is answered with a
permanent redirect (308) to `https://<name>:<HTTPS_PORT>/…`. Requests by IP address are sent to the first host name.
The health check (`/api/health`) and requests from the server itself are never redirected.

The app does **not** send `Strict-Transport-Security`: HSTS applies to every port of a host name, and would break the
plain HTTP port that stays available as a fallback.

## Locked out?

The redirect sends people to a name that does not resolve, or the HTTPS port is blocked:

```bash
cd /opt/adhan-bell-pager
docker compose exec adhan-bell-pager python -m app.cli disable-https
```

This turns off HTTPS and the redirect (the certificate is kept). The redirect stops within seconds; restart the
container to close the HTTPS port. Then open `http://<server>:<WEB_PORT>/` again.

## Security notes

* API tokens and the certificate's private key are **encrypted** with `config/secret.key` and stored in the database,
  so backups include them. Keep backups private. They are never returned by the API or written to logs; the audit log
  records only that a token changed.
* Give tokens the **least access** possible: one zone, DNS edit only.
* The certificate files are written to `config/tls/` (mode 600) for the listener.
* TLS 1.2 and 1.3 only.
* Session cookies set over HTTPS are marked `Secure`. Use one address consistently: a browser that signed in on
  `https://name` cannot then sign in on `http://name` until its cookies expire or are cleared.

## Troubleshooting

| Message | What to check |
|---|---|
| *Cloudflare refused the API token* | Token permissions (Zone › DNS › Edit and Zone › Zone › Read) and zone scope. Recreate the token if in doubt. |
| *No Cloudflare zone that contains … is visible to this token* | The host name must be inside a zone the token includes. |
| *cPanel refused the username or API token* | Username, token, token expiry, and that the address is the cPanel port (2083), not WHM (2087). |
| *The secure connection to cPanel was not trusted* | Use the cPanel server's real host name in the address. |
| *The DNS record did not appear on the domain's name servers* | The domain's public name servers are not those of the selected provider (for example the zone exists in cPanel but the domain uses Cloudflare). Check with `dig NS yourschool.com`. Increase *Wait for DNS up to* for slow providers. |
| *Let's Encrypt could not verify the DNS record* | Same as above; also check CAA records (`dig CAA yourschool.com`) allow `letsencrypt.org`. |
| *Let's Encrypt rate limit reached* | Wait (limits are per week for the same names). Use staging for tests. |
| *Could not reach the certificate authority* | Outbound HTTPS from the server, proxy or firewall. |
| *Could not listen on port 443* | Another service uses the port, or the container was started without the default capabilities. |
| Browser warns about the certificate | Open the app by the host name, not the IP address; and not with a staging certificate. |

The service log (Administration › Service logs) shows every step of a request.

## Testing

Automated tests cover the settings, both DNS provider APIs (with recorded API behaviour), the issuance job, renewal
timing and back-off, the TLS listener with live certificate reload, the redirect and cookies. CI also runs the full
ACME DNS-01 flow against **Pebble**, Let's Encrypt's official test server. See [TESTING.md](TESTING.md) for what has and
has not been verified against the real Let's Encrypt, Cloudflare and cPanel services.
