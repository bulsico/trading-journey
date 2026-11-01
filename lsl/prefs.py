"""Language preference stored in the data dir, not in a host's memory.

    prefs.json    {"lang": "en" | "zh" | "system"}

`en` and `zh` are used as stored. `system` is resolved on every read from
LC_ALL, LC_MESSAGES, then LANG (a Chinese locale of any variant maps to zh,
an English one to en). Any other locale falls back to en and the result
carries a note. An empty, C, or POSIX locale is not a choice: the caller
asks which language the user is writing in.

The file lives at the data-dir root, so `lsl purge` (which removes state,
raw and out) does not forget the language.
"""
import json
import os

from . import persona
from .util import write_json

CHOICES = ("en", "zh", "system")
FILENAME = "prefs.json"


def path(data_dir):
    return os.path.join(os.path.abspath(data_dir), FILENAME)


def _both(key, locale=""):
    """Same string in both label files once; otherwise English / Chinese."""
    en = persona.labels("en")["prefs"][key]
    zh = persona.labels("zh")["prefs"][key]
    if "{locale}" in en or "{locale}" in zh:
        en = en.format(locale=locale)
        zh = zh.format(locale=locale)
    return en if en == zh else "%s / %s" % (en, zh)


def question():
    return _both("question")


def options():
    return [{"id": "en", "label": _both("opt_en")},
            {"id": "zh", "label": _both("opt_zh")},
            {"id": "system", "label": _both("opt_system")}]


def locale_name(environ=None):
    """Locale tag without encoding or modifier: 'zh_CN', 'en_US', ''."""
    env = os.environ if environ is None else environ
    raw = (env.get("LC_ALL") or env.get("LC_MESSAGES") or env.get("LANG") or "").strip()
    if not raw:
        return ""
    return raw.split(".", 1)[0].split("@", 1)[0]


def resolve_system(environ=None):
    """-> (lang or None, reason or None). reason is no_locale or unsupported."""
    name = locale_name(environ)
    low = name.lower().replace("_", "-")
    if low.startswith("zh"):
        return "zh", None
    if low.startswith("en"):
        return "en", None
    if low in ("", "c", "posix"):
        return None, "no_locale"
    return "en", "unsupported"


def _load(data_dir):
    p = path(data_dir)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (ValueError, OSError):
        return {"_error": "prefs.json is not valid JSON"}
    if not isinstance(data, dict):
        return {"_error": "prefs.json must be a JSON object"}
    return data


def show(data_dir, environ=None):
    """Current choice. status ask: the caller asks the user, in text, then set_choice.
    status ready: `lang` is en or zh and is what `--lang` should be."""
    data = _load(data_dir)
    base = {"question": question(), "options": options(), "path": path(data_dir),
            "data_dir": os.path.abspath(data_dir)}
    if data.get("_error"):
        return dict(base, error=data["_error"])
    choice = data.get("lang")
    if choice not in CHOICES:
        return dict(base, status="ask", choice=None, lang=None, note=None, reason="unset")
    if choice == "system":
        lang, reason = resolve_system(environ)
        if lang is None:
            return dict(base, status="ask", choice="system", lang=None, reason=reason,
                        note=_both(reason))
        note = _both(reason, locale_name(environ)) if reason else None
        return dict(base, status="ready", choice="system", lang=lang, reason=reason, note=note)
    return dict(base, status="ready", choice=choice, lang=choice, note=None, reason=None)


def set_choice(data_dir, choice, environ=None):
    if choice not in CHOICES:
        return {"error": "lang must be en, zh or system", "path": path(data_dir)}
    write_json(path(data_dir), {"lang": choice})
    return show(data_dir, environ)


def saved_choice(data_dir):
    """The stored choice, or None. Does not resolve `system`."""
    data = _load(data_dir)
    if data.get("_error"):
        return None
    choice = data.get("lang")
    return choice if choice in CHOICES else None
