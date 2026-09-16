"""Every t("...") string in the web UI has an Arabic translation, and placeholders are preserved."""
import importlib.util
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("i18n_extract", ROOT / "scripts" / "i18n_extract.py")
i18n = importlib.util.module_from_spec(spec)
spec.loader.exec_module(i18n)


def test_all_ui_strings_translated():
    strict = i18n.js_strict()
    ar = i18n.load_ar()
    missing = sorted(k for k in strict if k not in ar)
    assert not missing, f"{len(missing)} UI strings lack Arabic translations, e.g. {missing[:10]}"


def test_placeholders_match():
    ar = i18n.load_ar()
    bad = []
    for en, tr in ar.items():
        if set(re.findall(r"\{(\w+)\}", en)) != set(re.findall(r"\{(\w+)\}", tr)):
            bad.append(en)
    assert not bad, f"placeholder mismatch in: {bad[:10]}"


def test_translations_are_arabic():
    ar = i18n.load_ar()
    latin_only = [en for en, tr in ar.items()
                  if re.search(r"[A-Za-z]{4,}", en) and not re.search(r"[؀-ۿ]", tr)]
    allowed = {"English", "SIP", "PBX", "LDAPS", "StartTLS", "Opus", "Speex", "iLBC", "G.722", "RTP", "NTP", "UDP",
               "TCP", "TLS"}

    def identifier_like(s: str) -> bool:
        # DNs, HTTP verbs, single tokens and placeholder-only strings stay as they are
        return "=" in s or " " not in s.strip() or not re.sub(r"\{\w+\}|[()\s,.:/-]", "", s)

    latin_only = [k for k in latin_only if k not in allowed and not re.fullmatch(r"[A-Z0-9 .:/()\-]+", k)
                  and not (ar[k] == k and identifier_like(k))]
    assert not latin_only, f"untranslated entries: {latin_only[:10]}"
