"""Password hashing, secret encryption at rest, and log/API redaction helpers."""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import secrets
import threading

import bcrypt
from cryptography.fernet import Fernet, InvalidToken

from . import config

log = logging.getLogger("security")

MIN_PASSWORD_LEN = 8
_BCRYPT_MAX = 72
_key_lock = threading.Lock()
_fernet: Fernet | None = None


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #
def _prehash(password: str) -> bytes:
    raw = password.encode("utf-8")
    if len(raw) <= _BCRYPT_MAX:
        return raw
    # bcrypt ignores bytes after 72; pre-hash long passphrases so every byte counts.
    return base64.b64encode(hashlib.sha256(raw).digest())


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash or password is None:
        return False
    try:
        if bcrypt.checkpw(_prehash(password), password_hash.encode("ascii")):
            return True
        raw = password.encode("utf-8")
        if len(raw) > _BCRYPT_MAX:
            # v1.x hashed the raw bytes, which bcrypt truncated at 72.
            return bcrypt.checkpw(raw[:_BCRYPT_MAX], password_hash.encode("ascii"))
        return False
    except Exception:  # noqa: BLE001
        return False


_DUMMY_HASH = None


def burn_password_check(password: str) -> None:
    """Spend the same time as a real check so unknown usernames are not revealed."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_hex(8))
    verify_password(password or "", _DUMMY_HASH)


def validate_password(password: str, username: str = "") -> str | None:
    if not password:
        return "Password is required."
    if len(password) < MIN_PASSWORD_LEN:
        return f"Password must be at least {MIN_PASSWORD_LEN} characters."
    if len(password) > 256:
        return "Password is too long."
    if username and password.lower() == username.lower():
        return "Password must not be the same as the username."
    if password.lower() in {"admin", "password", "11111111", "qwerty123", "adminadmin",
                            "password1", "qwertyui"}:
        return "That password is too common."
    return None


USERNAME_RE = re.compile(r"^[A-Za-z0-9._@-]{3,64}$")


def validate_username(username: str) -> str | None:
    if not username or username != username.strip():
        return "Username is required and cannot start or end with spaces."
    if not USERNAME_RE.match(username):
        return "Username must be 3-64 characters: letters, numbers, dot, dash, underscore or @."
    return None


# --------------------------------------------------------------------------- #
# Secrets at rest (directory bind password)
# --------------------------------------------------------------------------- #
def _key_path() -> str:
    return os.environ.get("SECRET_KEY_PATH") or os.path.join(
        os.path.dirname(config.CONFIG_PATH), "secret.key")


def _box() -> Fernet:
    global _fernet
    with _key_lock:
        if _fernet is not None:
            return _fernet
        env_key = os.environ.get("APP_SECRET_KEY", "").strip()
        if env_key:
            key = env_key.encode()
        else:
            path = _key_path()
            if os.path.exists(path):
                with open(path, "rb") as fh:
                    key = fh.read().strip()
            else:
                key = Fernet.generate_key()
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(key)
                log.info("security: generated a new secret key at %s", path)
        _fernet = Fernet(key)
        return _fernet


def reset_key_cache() -> None:
    global _fernet
    with _key_lock:
        _fernet = None


def encrypt(plain: str) -> str:
    if not plain:
        return ""
    return _box().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _box().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        log.error("security: stored secret could not be decrypted (secret.key changed?)")
        return ""


# --------------------------------------------------------------------------- #
# Tokens
# --------------------------------------------------------------------------- #
def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_id(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #
_SECRET_KEYS = re.compile(r"(pass(word)?|secret|token|credential|bind_password|api_key|csrf)",
                          re.I)


def redact(value):
    """Return a deep copy with secret-looking fields replaced by '***'."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if isinstance(k, str) and _SECRET_KEYS.search(k) and not k.startswith("has_"):
                out[k] = "***" if v not in (None, "", False) else v
            else:
                out[k] = redact(v)
        return out
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value
