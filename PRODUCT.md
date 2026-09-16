# Product

<!-- impeccable:product-schema 1 -->

> Recorded without an interview: the owner asked for autonomous delivery. Facts below come from the
> owner's written brief and the v1.7 README; items marked *(inferred)* should be confirmed.

## Platform

web

## Users

- **School office and facilities staff** who set up class timetables, holidays and exam days, and ring a bell by hand
  when needed. Non-technical; use a desktop PC in an office, sometimes a phone in a corridor. *(inferred from brief)*
- **Imams and mosque/prayer-room caretakers** who keep prayer times, the Adhan recording and iqama right.
- **IT administrators** who connect the PBX, manage users and roles, and optionally connect Active Directory.
- **Viewers** (e.g. a head teacher or board member) who only check what is scheduled and what happened.

Arabic and English speakers, often in the same organisation; Arabic users expect a native right-to-left interface.

## Product Purpose

Broadcast the Adhan and school bells automatically through an existing SIP phone system (PBX paging groups, SIP
speakers, auto-answer phones), on time, without cloud services. Success means every scheduled broadcast plays once, at
the right minute, on the right speakers — and when something does not, staff can see why and fix it.

## Positioning

One self-hosted service that owns the paging line for both the call to prayer and the school day, with an explicit,
visible priority policy between them, instead of two separate systems competing for the same speakers.

## Operating Context

- Runs on a small Linux VM or server on the school/mosque LAN, next to an Asterisk-style PBX. Often offline from the
  internet, so every asset is bundled.
- Daily rhythm: bells every 40–45 minutes on school days, five prayers a day, iqama optional; holidays, Ramadan and
  exam timetables change the pattern.
- Several administrators with different scopes: bells staff must not touch the Adhan or security settings and vice
  versa.

## Capabilities and Constraints

- Adhan: offline prayer-time calculation (adhanpy) or an imported timetable; per-prayer enable/adjust; iqama; per-prayer
  recordings; location by city or coordinates.
- School bells: reusable day timetables, weekly schedules with effective dates, holidays/no-bell days/overrides, zones,
  repeat or duration playback, ring now/test/stop, history.
- SIP via PJSIP: UDP/TCP/TLS registration, G.722/G.711/Opus; G.729 is not available for sending audio.
- Unified role-based permissions; optional Active Directory sign-in; local recovery administrator.
- Terminology used in the UI: *speakers* (destinations), *zones*, *timetable*, *weekly schedule*, *holidays & changes*,
  *ring now*, *phone system* (SIP).

## Brand Commitments

- Name: **Adhan & Bell Pager** (Arabic: منادي الأذان والجرس). *(chosen during this rebuild)*
- Visual framework pinned by the owner: **Tabler**. Arabic typeface pinned by the owner: **Cairo**.
- Identity carried from v1: deep prayer-hall green with a brass accent; School Bells uses an amber accent to separate
  the modules.
- License: PolyForm Noncommercial 1.0.0 (inherited from Adhan Pager).

## Evidence on Hand

- v1.7 screenshots in `assets/screenshots/` (previous UI, for reference only).
- No customer quotes, logos or metrics exist; do not invent them.

## Product Principles

1. **Never surprise the building.** A broadcast plays once, on time, or is clearly reported as skipped/missed — never
   late and unannounced.
2. **Everyday actions first.** Today's schedule, ring now and stop are one click away; setup detail is secondary.
3. **Scoped trust.** People only see and change what their role allows, and the server enforces it.
4. **Explain failures in the language of the room.** "The speaker did not answer" beats a SIP code; the code is still
   there for IT.
5. **Works offline, in Arabic and English, on a phone or a desktop.**

## Accessibility & Inclusion

WCAG 2.1 AA contrast in light and dark themes, keyboard operable, visible focus, reduced-motion support, full RTL
layout for Arabic.
