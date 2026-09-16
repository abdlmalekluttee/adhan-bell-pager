"""Capture documentation screenshots from a running demo instance (see docs/dev/UI_CONVENTIONS.md).

    python scripts/screenshots.py --url http://127.0.0.1:8931 --user admin --password Demo-Admin-2026 \
        --out assets/screenshots/v2 [--review .impeccable/review]

Uses Playwright with the locally installed Microsoft Edge or Google Chrome (no browser download).
Only run against demo data: screenshots are committed to the repository.
"""
from __future__ import annotations

import argparse
import pathlib
import time

from playwright.sync_api import sync_playwright

DESKTOP = {"width": 1440, "height": 900}
MOBILE = {"width": 390, "height": 844}


def login(page, url, user, password, lang, theme):
    page.goto(url + "/")
    page.evaluate(f"localStorage.setItem('abp.lang', '{lang}'); localStorage.setItem('abp.theme', '{theme}')")
    page.goto(url + "/")
    page.wait_for_selector("#login-form, .page-wrapper", timeout=15000)
    if page.query_selector("#login-form"):
        page.fill("#login-user", user)
        page.fill("#login-pass", password)
        page.click("#login-form button[type=submit]")
        page.wait_for_selector(".page-wrapper", timeout=15000)
    page.evaluate("""async ([lang, theme]) => {
        const csrf = document.cookie.match(/abp_csrf=([^;]+)/)[1];
        await fetch('/api/auth/preferences', {method: 'POST', headers: {'Content-Type': 'application/json',
          'X-CSRF-Token': decodeURIComponent(csrf)}, body: JSON.stringify({language: lang, theme})});
    }""", [lang, theme])
    page.goto(url + "/")
    page.wait_for_selector(".page-wrapper", timeout=15000)


def shot(page, url, route, path, *, wait="#page-root > *", full=False, before=None):
    page.goto(url + "/#" + route)
    page.wait_for_selector(wait, timeout=15000)
    page.wait_for_timeout(1200)
    if before:
        before(page)
        page.wait_for_timeout(700)
    path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(path), full_page=full)
    print("saved", path)


def open_edit_user(page):
    page.locator("button[data-action=edit]").nth(1).click()
    page.wait_for_selector("dialog[open]")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8931")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", required=True)
    ap.add_argument("--out", default="assets/screenshots/v2")
    ap.add_argument("--review", default="")
    ap.add_argument("--channel", default="msedge")
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=args.channel)

        ctx = browser.new_context(viewport=DESKTOP, device_scale_factor=1, color_scheme="light")
        page = ctx.new_page()
        login(page, args.url, args.user, args.password, "en", "light")
        shot(page, args.url, "/", out / "dashboard-light.png")
        shot(page, args.url, "/bells", out / "bells-today.png")
        shot(page, args.url, "/bells/timetables/1", out / "timetable-editor.png")
        shot(page, args.url, "/bells/schedules", out / "weekly-schedules.png")
        shot(page, args.url, "/bells/calendar", out / "holidays-calendar.png")
        shot(page, args.url, "/adhan", out / "adhan-today.png")
        shot(page, args.url, "/admin/users", out / "users.png", before=open_edit_user)
        shot(page, args.url, "/admin/sip", out / "phone-system.png")
        if args.review:
            rv = pathlib.Path(args.review)
            shot(page, args.url, "/", rv / "desktop.png", full=True)
            shot(page, args.url, "/bells/timetables/1", rv / "desktop-timetable-editor.png", full=True)
            shot(page, args.url, "/admin/roles", rv / "desktop-roles.png", full=True)
        ctx.close()

        ctx = browser.new_context(viewport=DESKTOP, device_scale_factor=1, color_scheme="dark")
        page = ctx.new_page()
        login(page, args.url, args.user, args.password, "ar", "dark")
        shot(page, args.url, "/", out / "dashboard-ar-dark.png")
        shot(page, args.url, "/bells", out / "bells-today-ar-dark.png")
        if args.review:
            shot(page, args.url, "/bells", pathlib.Path(args.review) / "desktop-ar-dark.png", full=True)
        ctx.close()

        ctx = browser.new_context(viewport=MOBILE, device_scale_factor=2, color_scheme="light", is_mobile=True,
                                  has_touch=True)
        page = ctx.new_page()
        login(page, args.url, args.user, args.password, "en", "light")
        shot(page, args.url, "/bells", out / "mobile-bells.png")
        if args.review:
            shot(page, args.url, "/", pathlib.Path(args.review) / "mobile.png", full=True)
            shot(page, args.url, "/bells/timetables/1", pathlib.Path(args.review) / "mobile-timetable-editor.png",
                 full=True)
        # restore defaults for the demo account
        login(page, args.url, args.user, args.password, "en", "auto")
        ctx.close()

        ctx = browser.new_context(viewport=DESKTOP, device_scale_factor=1, color_scheme="light")
        page = ctx.new_page()
        page.goto(args.url + "/")
        page.evaluate("localStorage.setItem('abp.lang', 'en'); localStorage.setItem('abp.theme', 'light')")
        page.goto(args.url + "/")
        page.wait_for_selector("#login-form")
        time.sleep(1)
        page.screenshot(path=str(out / "sign-in.png"))
        print("saved", out / "sign-in.png")
        browser.close()


if __name__ == "__main__":
    main()
