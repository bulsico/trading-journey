"""Robinhood MCP tool-result parsing: DRF cursors and page classification.

Findings (verified live, 2026-10):
* get_{equity,option,crypto}_orders use DRF position cursors:
  base64("p=" + quote_plus("YYYY-MM-DD HH:MM:SS.ffffff+00:00")), '=' stripped.
  Microseconds are required. cursor p=T returns created_at < T, newest first;
  created_at_gte=G narrows to [G, T). No page_size.
* The connector gateway truncates results at ~20 KB, and the server cursor is
  lost when that happens. In "mangling" mode only a sliver is kept, so a
  truncated result is NEVER treated as empty.
* The JSON body starts at '{"data"'; the file written by write_to_file wraps it
  in a few lines of framing.
"""
import base64
import json
import re
import urllib.parse

from .util import to_utc

TRUNC_MARK = "truncated by the connectors gateway"
EMPTY, COMPLETE, TRUNCATED, MANGLED, ERROR = "EMPTY", "COMPLETE", "TRUNCATED", "MANGLED", "ERROR"
CURSOR_ERRORS = ("Not found", "Invalid cursor")


def make_cursor(ts):
    """Cursor for records strictly older than ts."""
    p = to_utc(ts).isoformat(sep=" ", timespec="microseconds")
    return base64.b64encode(("p=" + urllib.parse.quote_plus(p)).encode()).decode().rstrip("=")


def decode_cursor(c):
    return urllib.parse.unquote_plus(base64.b64decode(c + "=" * (-len(c) % 4)).decode())


def _find_key(d, keys):
    for k in keys:
        if isinstance(d, dict) and k in d:
            return k
    return None


def parse_page(text, keys):
    """-> (records, truncated, data_dict_or_None). keys: candidate list keys."""
    if isinstance(keys, str):
        keys = (keys,)
    truncated = TRUNC_MARK in text
    start = text.find('{"data"')
    if start < 0:
        raise ValueError("no data object in tool result")
    dec = json.JSONDecoder()
    try:
        obj, _ = dec.raw_decode(text, start)
        d = obj["data"]
        if isinstance(d, list):
            return d, truncated, {}
        if isinstance(d, dict):
            k = _find_key(d, keys)
            return (d.get(k) or []) if k else [], truncated, d
    except (json.JSONDecodeError, KeyError, TypeError):
        pass
    i = -1
    for k in keys:
        i = text.find('"%s":[' % k, start)
        if i >= 0:
            i += len(k) + 4
            break
    if i < 0:
        return [], True, None
    recs = []
    while True:
        while i < len(text) and text[i] in " ,\n\r\t":
            i += 1
        if i >= len(text) or text[i] != "{":
            break
        try:
            rec, i = dec.raw_decode(text, i)
        except json.JSONDecodeError:
            break                               # incomplete record at the cut
        recs.append(rec)
    return recs, True, None


_HEAD = re.compile(r'\{"id":"([0-9a-fA-F-]{36})"')


def mangled_head(text, keys):
    """Id of the first record of a mangled page (its prefix survives the cut)."""
    if isinstance(keys, str):
        keys = (keys,)
    for k in keys:
        i = text.find('"%s":[' % k)
        if i >= 0:
            m = _HEAD.match(text, i + len(k) + 4)
            return m.group(1) if m else None
    return None


def classify(text, keys):
    """-> (status, records, data). Only an exact untruncated empty list is EMPTY."""
    if text is None:
        return ERROR, [], None
    if '"data"' not in text:
        return ERROR, [], None
    try:
        recs, trunc, d = parse_page(text, keys)
    except ValueError:
        return ERROR, [], None
    if trunc:
        return (MANGLED if not recs else TRUNCATED), recs, None
    return (EMPTY if not recs else COMPLETE), recs, d
