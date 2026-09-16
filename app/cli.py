"""Administrator command line, run inside the container.

    docker compose exec adhan-bell-pager python -m app.cli reset-password
    docker compose exec adhan-bell-pager python -m app.cli reset-password --username admin
    docker compose exec adhan-bell-pager python -m app.cli users
    docker compose exec adhan-bell-pager python -m app.cli disable-directory
"""
from __future__ import annotations

import argparse
import getpass
import sys


def _open():
    from . import accounts, config, db
    config.load()
    db.open_db()
    accounts.bootstrap()


def reset_password(args) -> int:
    from . import accounts, audit, db
    _open()
    if args.username:
        user = accounts.get_by_username(args.username)
    else:
        user = db.one("SELECT * FROM users WHERE is_recovery = 1")
    if not user:
        print("User not found.", file=sys.stderr)
        return 1
    if user["source"] != "local":
        print("That is a directory account; reset it in Active Directory.", file=sys.stderr)
        return 1
    if args.password_stdin:
        pw = sys.stdin.readline().rstrip("\n")
    else:
        pw = getpass.getpass(f"New password for {user['username']}: ")
        if pw != getpass.getpass("Repeat the new password: "):
            print("Passwords do not match.", file=sys.stderr)
            return 1
    try:
        accounts.set_password(user["id"], pw, must_change=not args.no_force_change)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    accounts.update_user(user["id"], disabled=False)
    n = accounts.revoke_sessions(user["id"])
    audit.record("user.reset_password", actor="cli", target=user["username"], details={"sessions_ended": n})
    print(f"Password for '{user['username']}' was reset. Existing sessions ended: {n}.")
    return 0


def list_users(args) -> int:
    from . import accounts
    _open()
    for u in accounts.list_users():
        flags = []
        if u["is_recovery"]:
            flags.append("recovery")
        if u["disabled"]:
            flags.append("disabled")
        roles = ", ".join(r["name"] for r in u["roles"]) or "no roles"
        print(f"{u['username']:<24} {u['source']:<6} {roles}{'  [' + ', '.join(flags) + ']' if flags else ''}")
    return 0


def disable_directory(args) -> int:
    from . import audit, db, directory
    _open()
    cfg = directory.get_config()
    cfg["enabled"] = False
    db.set_setting(directory.SETTINGS_KEY, cfg)
    audit.record("directory.update", actor="cli", details={"enabled": False})
    print("Directory sign-in disabled. Local accounts continue to work.")
    return 0


def backup(args) -> int:
    """Consistent snapshot of settings, database, secret key and audio as a .tar.gz."""
    import datetime
    import io
    import os
    import sqlite3
    import tarfile
    import tempfile

    from . import __version__, config, db
    config.load()
    cfg_dir = os.path.dirname(config.CONFIG_PATH)
    out = args.out or os.path.join(cfg_dir, "backups",
                                   f"adhan-bell-pager-{__version__}-{datetime.datetime.now():%Y%m%d-%H%M%S}.tar.gz")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    db_path = db.default_path()
    with tempfile.TemporaryDirectory() as tmp:
        snap = os.path.join(tmp, "app.db")
        if os.path.exists(db_path):
            src = sqlite3.connect(db_path)
            dst = sqlite3.connect(snap)
            with dst:
                src.backup(dst)          # online, consistent copy even while the service runs
            dst.close()
            src.close()
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh, tarfile.open(fileobj=fh, mode="w:gz") as tar:
            info = tarfile.TarInfo("BACKUP-INFO.txt")
            text = (f"Adhan & Bell Pager backup\nversion: {__version__}\n"
                    f"created: {datetime.datetime.now().isoformat()}\n"
                    "Contains secrets (SIP password, secret key, password hashes). Store it securely.\n").encode()
            info.size = len(text)
            tar.addfile(info, io.BytesIO(text))
            for name in os.listdir(cfg_dir):
                full = os.path.join(cfg_dir, name)
                skip = (name == "backups" or name.startswith("app.db") or name.endswith((".log", ".tmp"))
                        or ".log." in name or not os.path.isfile(full))
                if skip:
                    continue
                tar.add(full, arcname=f"config/{name}")
            if os.path.exists(snap):
                tar.add(snap, arcname="config/app.db")
            for root_dir, dirs, files in os.walk(config.AUDIO_DIR):
                dirs[:] = [d for d in dirs if d != ".cache"]
                for f in files:
                    if f.startswith(".upload-") or f.startswith(".convert-"):
                        continue
                    full = os.path.join(root_dir, f)
                    tar.add(full, arcname="audio/" + os.path.relpath(full, config.AUDIO_DIR).replace(os.sep, "/"))
    print(out)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("reset-password", help="reset a local user's password (default: recovery administrator)")
    rp.add_argument("--username")
    rp.add_argument("--password-stdin", action="store_true", help="read the new password from standard input")
    rp.add_argument("--no-force-change", action="store_true", help="do not require a change at next sign-in")
    rp.set_defaults(fn=reset_password)
    sub.add_parser("users", help="list users and roles").set_defaults(fn=list_users)
    sub.add_parser("disable-directory", help="turn off Active Directory sign-in").set_defaults(fn=disable_directory)
    bp = sub.add_parser("backup", help="write a consistent backup archive (settings, database, audio)")
    bp.add_argument("--out", help="archive path (default: <config>/backups/adhan-bell-pager-<version>-<time>.tar.gz)")
    bp.set_defaults(fn=backup)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
