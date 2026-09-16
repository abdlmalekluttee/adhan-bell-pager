# Phone system (SIP) setup and troubleshooting

## Recommended setup

* Create a dedicated SIP extension on the PBX for this service (for example 3001).
* Use **host networking** (the default `docker-compose.yml`) so SIP and RTP are not rewritten by Docker NAT.
* Transport **UDP** unless your PBX requires TCP or TLS. Port 5060 (5061 for TLS).
* Target an **auto-answer** extension, a SIP speaker, or a **PBX paging group** (best for many speakers).
* Firewall: allow SIP (5060/udp and tcp) and RTP (16000–16100/udp by default, *Advanced* in Phone system settings)
  **from the PBX** to the server. Example with UFW:

  ```bash
  sudo ufw allow proto udp from <pbx-ip> to any port 5060 comment "Adhan & Bell Pager SIP"
  sudo ufw allow proto tcp from <pbx-ip> to any port 5060 comment "Adhan & Bell Pager SIP/TCP"
  sudo ufw allow proto udp from <pbx-ip> to any port 16000:16100 comment "Adhan & Bell Pager RTP"
  sudo ufw allow proto tcp from <admin-network> to any port <WEB_PORT> comment "Adhan & Bell Pager web UI"
  ```

## Codecs

Administration › Phone system › Codecs lists what this server can send, in order of preference. The PBX chooses one
of the enabled codecs; the codec actually used is shown for every call in the history.

| Codec | Notes |
|---|---|
| G.722 | Wideband (16 kHz), best quality when the PBX and speakers support it |
| G.711 A-law (PCMA) / µ-law (PCMU) | Universal |
| Opus | Supported by this server; only used if the PBX negotiates it |
| **G.729** | **Not available for sending audio.** A PBX set to "pass-through" for G.729 cannot use it with this server; keep G.711 or G.722 enabled on the extension. |

Recordings are converted to 16 kHz mono WAV on upload (8 kHz is enough for G.711-only systems).

## Registration status messages

The dashboard, the status dot in the top bar and Administration › Phone system show the current state.

| Message | Likely cause / fix |
|---|---|
| Registered | Working. *Last registered* updates at every refresh (the PBX sets the interval). |
| The PBX rejected the credentials (401/407) | Wrong authentication ID or password. |
| The PBX refused this account (403) | Wrong password, extension disabled, or the server IP is not allowed on the extension (e.g. "permit" ACL). |
| The extension was not found (404) | Wrong username or SIP domain. |
| The PBX did not respond (408) | Wrong address/port/transport, or a firewall drops SIP. |
| The PBX address could not be found (DNS) | Hostname does not resolve from the server. Use the IP or fix DNS. |
| The PBX refused the connection | TCP/TLS selected but the PBX does not listen on that port. |

**Retries are bounded.** After a failure the service retries after 30 s, then 60 s, 120 s … up to every 15 minutes, so a
wrong password never floods the PBX (and does not trigger intrusion-detection bans). Fixing the settings and saving
reconnects immediately; *Reconnect now* forces a new attempt.

## Call results

Every broadcast records, for each destination:

| Result | Meaning / fix |
|---|---|
| Played to the end | Answered, audio played completely, then hung up |
| No answer | Rang for the ring timeout (Phone system › Call behaviour) without answer. Check auto-answer. |
| Busy / Rejected | The PBX returned busy (486) or refused (403/404/488): check the extension and codec settings. |
| No audio path | Answered but no media was negotiated: usually no common codec. |
| Hung up early | The far end hung up before the audio finished. |
| Stopped / Interrupted | An operator stopped it, or a higher-priority broadcast took the line. |

**Audio sent (RTP packets)** proves audio left this server and **audio received** proves the far end had an active
media stream. It does not prove the speaker was audible (volume, power, amplifier). Confirm by listening during a test.

## Tuning for auto-answer speakers

* *Wait after answer before audio* (default 1.5 s): increase if the first syllable is cut off.
* *Pause after audio before hanging up* (default 0.8 s): increase if the end is cut off.
* *Ring timeout* (default 30 s) and *Maximum call length* (safety limit) prevent stuck calls.
* *All at once* calls every destination in parallel; *One after another* calls them in order.

## Logs

Administration › Service logs (filter *SIP*). For deeper traces set `SIP_LOG_LEVEL=4` in `.env` and restart:
SIP messages are then logged (authorization digests appear, passwords never do). Set it back to 3 afterwards.

```bash
docker compose logs -f --tail 200
```
