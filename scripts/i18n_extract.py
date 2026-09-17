"""Collect translatable English strings and compare them with the Arabic dictionary.

    python scripts/i18n_extract.py            # summary of missing translations
    python scripts/i18n_extract.py --missing  # print missing keys as JSON (for translating)

Strict keys (must be translated; tests/test_i18n.py enforces this):
  * literal arguments of t("...") and tn(n, "...", "...") in app/static/js
Additional keys (translated when present, not enforced):
  * UI label maps (title:/label: values) that are passed to t() indirectly
  * user-facing backend messages and permission/role labels
"""
from __future__ import annotations

import ast
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
JS_DIR = ROOT / "app" / "static" / "js"
AR_FILE = JS_DIR / "locales" / "ar.js"

_STR = r'"((?:[^"\\\n]|\\.)*)"|\'((?:[^\'\\\n]|\\.)*)\''
T_CALL = re.compile(r"(?<![\w.$])t\(\s*(?:" + _STR + r")")
TN_CALL = re.compile(r"(?<![\w.$])tn\(\s*[^,]+,\s*(?:" + _STR + r")\s*,\s*(?:" + _STR + r")")
LABEL_VALUES = re.compile(r"\b(?:title|label)\s*:\s*(?:" + _STR + r")")
MAP_VALUES = re.compile(r"\b[a-z_]+\s*:\s*\"([A-Z][^\"\n]{2,80})\"")


def _unescape(s: str) -> str:
    try:
        return json.loads(f'"{s}"')
    except ValueError:
        return s.replace("\\'", "'")


def js_strict() -> dict[str, set[str]]:
    keys: dict[str, set[str]] = {}
    for f in sorted(JS_DIR.rglob("*.js")):
        if "locales" in f.parts:
            continue
        text = f.read_text(encoding="utf-8")
        for m in T_CALL.finditer(text):
            s = m.group(1) if m.group(1) is not None else m.group(2)
            keys.setdefault(_unescape(s), set()).add(f.name)
        for m in TN_CALL.finditer(text):
            for s in (m.group(1) or m.group(2), m.group(3) or m.group(4)):
                if s is not None:
                    keys.setdefault(_unescape(s), set()).add(f.name)
    return keys


def js_indirect() -> set[str]:
    out: set[str] = set()
    for f in sorted(JS_DIR.rglob("*.js")):
        if "locales" in f.parts:
            continue
        text = f.read_text(encoding="utf-8")
        for m in LABEL_VALUES.finditer(text):
            s = m.group(1) if m.group(1) is not None else m.group(2)
            if s and re.search(r"[A-Za-z]", s):
                out.add(_unescape(s))
        for m in MAP_VALUES.finditer(text):
            out.add(_unescape(m.group(1)))
    return out


def backend_messages() -> set[str]:
    """Plain string constants from user-facing backend modules (f-strings are skipped)."""
    files = ["app/routers/*.py", "app/sip/base.py", "app/sip/engine.py", "app/rbac.py", "app/bells.py",
             "app/dispatcher.py", "app/scheduler.py", "app/executions.py", "app/accounts.py", "app/security.py",
             "app/directory.py", "app/config.py", "app/audio.py", "app/timetable.py", "app/certificates.py",
             "app/dns_providers.py", "app/https_server.py"]
    out: set[str] = set()
    for pattern in files:
        for f in ROOT.glob(pattern):
            tree = ast.parse(f.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.JoinedStr):
                    for v in node.values:
                        v._skip = True  # noqa: SLF001
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and not getattr(node, "_skip", False):
                    s = node.value.strip()
                    if 3 <= len(s) <= 220 and re.match(r"^[A-Z“']", s) and " " in s and "\n" not in s \
                            and not re.search(r"(SELECT|INSERT|UPDATE|DELETE|CREATE|PRAGMA|\{|%s|\\\\|://)", s):
                        out.add(s)
    return out


def load_ar() -> dict[str, str]:
    if not AR_FILE.exists():
        return {}
    text = AR_FILE.read_text(encoding="utf-8")
    body = text[text.index("{"): text.rindex("}") + 1]
    return json.loads(body)


def write_ar(data: dict[str, str]) -> None:
    ordered = dict(sorted(data.items(), key=lambda kv: kv[0].lower()))
    AR_FILE.write_text(
        "// Arabic translations, keyed by the exact English text used in the code.\n"
        "// Generated and checked by scripts/i18n_extract.py; keep this file valid JSON inside the braces.\n"
        "export const AR = " + json.dumps(ordered, ensure_ascii=False, indent=2) + ";\n", encoding="utf-8", newline="\n")


def main() -> int:
    strict = js_strict()
    extra = (js_indirect() | backend_messages()) - set(strict)
    ar = load_ar()
    missing_strict = sorted(k for k in strict if k not in ar)
    missing_extra = sorted(k for k in extra if k not in ar)
    if "--missing" in sys.argv:
        print(json.dumps({"strict": missing_strict, "extra": missing_extra}, ensure_ascii=False, indent=1))
        return 0
    if "--merge" in sys.argv:
        idx = sys.argv.index("--merge")
        new = json.loads(pathlib.Path(sys.argv[idx + 1]).read_text(encoding="utf-8"))
        ar.update({k: v for k, v in new.items() if v})
        write_ar(ar)
        print(f"merged {len(new)} entries; dictionary now has {len(ar)}")
        return 0
    unused = sorted(k for k in ar if k not in strict and k not in extra)
    print(f"strict keys: {len(strict)}  missing: {len(missing_strict)}")
    print(f"extra keys:  {len(extra)}  missing: {len(missing_extra)}")
    print(f"dictionary:  {len(ar)}  unused: {len(unused)}")
    return 1 if missing_strict else 0


if __name__ == "__main__":
    sys.exit(main())
