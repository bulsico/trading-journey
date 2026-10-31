"""Daily candles for the SMA trend tags (read-only, public or connector data).

Sources
* Crypto and every Hyperliquid market (perps, HIP-3 dex coins such as dex:SYM):
  the public HL info API `candleSnapshot`, interval 1d (UTC days, up to 5000
  candles per call), fetched from the box by `lsl candles fetch`.
* Stocks (RH equities, the underlying of RH options, and the stock fallback of
  HIP-3 stock perps): the Robinhood connector's `get_equity_historicals`
  (interval day, regular session, split-adjusted).  These are MCP calls, so they
  ride the `lsl rh plan` / `lsl rh ingest` loop after the account sync is done.
  The connector gateway keeps 20,000 bytes of each result's text and a daily bar
  costs ~350 B of it, so a call asks for at most RH_WEEKDAYS_PER_CALL weekdays of
  one symbol; a truncated result halves that symbol's window size and the window
  is planned again.  A long stock history therefore costs many bot calls
  (~1 call per 2.5 months of history per symbol).
* Stocks, keyless alternative: `lsl candles fetch --stocks yahoo` fetches the same
  split-adjusted daily closes from the public Yahoo Finance chart endpoint, one
  box-side HTTP call per symbol (no key, no bot calls).  It fills only what is
  not cached yet; series fetched this way carry "source": "yahoo".  Use one stock
  source per data dir for byte-identical reruns.

Cache: DATA/raw/candles/<series>.json, one file per series id ("hl:<coin>" or
"rh:<SYMBOL>").  Only closed candles are stored: a day's bar is kept only when
its date is before the as_of day of the account data, so today's partial candle
never enters a file.  Each fetched range is recorded with the as_of it was
fetched under; once the data's as_of moves on, the last REFETCH_DAYS days of a
range are fetched again (incremental), so an incremental cache ends up equal to
a full re-fetch.  Everything is resumable: each series file is written as soon
as its call lands, and planning only asks for what is missing.

Windows: for every series the needed range per trade entry is
[entry day - LOOKBACK_DAYS[kind], entry day - 1] (200 bars + buffer); ranges are
merged when they are within MERGE_GAP_DAYS of each other.
"""
import datetime as dt
import json
import math
import os
import random
import sys
import time
import urllib.error
import urllib.request

from .util import read_json, write_json

HL_API = "https://api.hyperliquid.xyz/info"
LOOKBACK_DAYS = {"stock": 320, "crypto": 220}     # calendar days: >= 200 closes + buffer
MERGE_GAP_DAYS = {"stock": 30, "crypto": 400}      # HL: one call covers the whole span anyway
REFETCH_DAYS = 2
RH_WEEKDAYS_PER_CALL = 50
RH_MIN_WEEKDAYS = 10
RH_TOOL = "get_equity_historicals"
RH_ATTEMPTS = 3
HL_MAX_CANDLES = 5000
HL_WEIGHT_PER_MIN = 400        # self-imposed budget; HL allows 1200 weight / min / IP
HL_BASE_WEIGHT = 20            # info request weight; candleSnapshot adds 1 per 60 items returned
HL_RETRIES = 6
DAY = dt.timedelta(days=1)


# ----------------------------------------------------------------- dates / ranges
def d(s):
    return dt.date.fromisoformat(s)


def ds(x):
    return x.isoformat()


def merge(ranges, gap=0):
    """[[start, end], ...] (ISO dates, inclusive) -> sorted, merged within `gap` days."""
    out = []
    for s, e in sorted((d(a), d(b)) for a, b in ranges if a <= b):
        if out and (s - out[-1][1]).days <= gap + 1:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [[ds(a), ds(b)] for a, b in out]


def subtract(ranges, covered):
    out = []
    cov = merge(covered)
    for s, e in merge(ranges):
        s, e = d(s), d(e)
        for cs, ce in cov:
            cs, ce = d(cs), d(ce)
            if ce < s or cs > e:
                continue
            if cs > s:
                out.append([ds(s), ds(cs - DAY)])
            s = max(s, ce + DAY)
            if s > e:
                break
        if s <= e:
            out.append([ds(s), ds(e)])
    return out


def weekdays(s, e):
    s, e = d(s), d(e)
    n = (e - s).days + 1
    if n <= 0:
        return 0
    full, rem = divmod(n, 7)
    return full * 5 + sum(1 for i in range(rem) if (s + dt.timedelta(days=full * 7 + i)).weekday() < 5)


def chunks(s, e, size):
    """Split [s, e] into consecutive windows of at most `size` weekdays (deterministic)."""
    out, cur, n, x, e_ = [], d(s), 0, d(s), d(e)
    while x <= e_:
        if x.weekday() < 5:
            n += 1
        if n == size or x == e_:
            out.append([ds(cur), ds(x)])
            cur, n = x + DAY, 0
        x += DAY
    return [c for c in out if weekdays(*c) > 0]


# ----------------------------------------------------------------- cache
def cache_dir(run_dir):
    return os.path.join(os.path.abspath(run_dir), "raw", "candles")


def series_file(run_dir, sid):
    safe = sid.replace("@", "at").replace(":", "_").replace("/", "_")
    return os.path.join(cache_dir(run_dir), safe + ".json")


def load_series(run_dir, sid):
    c = read_json(series_file(run_dir, sid))
    return c if c else {"id": sid, "bars": [], "covered": [], "missing_as_of": None}


def save_series(run_dir, c):
    c["bars"] = sorted(c["bars"])
    c["covered"] = sorted(c["covered"])
    write_json(series_file(run_dir, c["id"]), c)


def effective_covered(c, as_of_day):
    """Covered ranges; ranges fetched under an older as_of lose their last REFETCH_DAYS days."""
    out = []
    for s, e, at in c["covered"]:
        if at != as_of_day:
            e = min(e, ds(d(at) - dt.timedelta(days=1 + REFETCH_DAYS)))
        if s <= e:
            out.append([s, e])
    return merge(out)


def is_missing(c, as_of_day):
    """The source has no such series (checked under this as_of)."""
    return c.get("missing_as_of") == as_of_day


def put_bars(c, s, e, bars, as_of_day):
    """Replace bars dated within [s, e] (and before as_of_day) and record the range."""
    e = min(e, ds(d(as_of_day) - DAY))
    if s > e:
        return
    keep = [b for b in c["bars"] if not (s <= b[0] <= e)]
    new = {}
    for day, close in bars:
        if s <= day <= e:
            new[day] = close
    c["bars"] = sorted(keep + [[k, v] for k, v in new.items()])
    c["covered"] = [x for x in c["covered"] if not (s <= x[0] and x[1] <= e)] + [[s, e, as_of_day]]
    c["missing_as_of"] = None


def load_all(run_dir):
    """{series id: [(date, close), ...]} for every fetched series (an empty list = the source has
    no closes in the fetched ranges, e.g. before a listing -> insufficient_history, not unavailable)."""
    out = {}
    root = cache_dir(run_dir)
    if not os.path.isdir(root):
        return out
    for n in sorted(os.listdir(root)):
        if n.endswith(".json"):
            c = read_json(os.path.join(root, n)) or {}
            if c.get("id") and (c.get("bars") or c.get("covered")):
                out[c["id"]] = [(b[0], float(b[1])) for b in c["bars"]]
    return out


# ----------------------------------------------------------------- what is needed
def needs(trades, as_of):
    """-> (as_of_day, {sid: {"kind", "ranges", "alt"}}) for every series a trade may use."""
    from . import trend
    as_of_day = (as_of or "")[:10]
    if not as_of_day:
        return None, {}
    out = {}
    for t in trades:
        ent = trend.entry_day(t)
        if ent is None or ent > as_of_day:
            continue
        info = trend.resolve(t)
        if info is None:
            continue
        kind = info["kind"]
        s = ds(d(ent) - dt.timedelta(days=LOOKBACK_DAYS[kind]))
        e = min(ds(d(ent) - DAY), ds(d(as_of_day) - DAY))
        for alts in info["candidates"]:
            n = out.setdefault(alts[0], {"kind": kind, "ranges": [], "alt": list(alts[1:])})
            n["ranges"].append([s, e])
    for sid, n in out.items():
        n["ranges"] = merge(n["ranges"], MERGE_GAP_DAYS[n["kind"]])
    return as_of_day, out


def data_trades(run_dir):
    """Trades + as_of from the stored account data (the same records compute uses)."""
    from . import compute
    rh, hl = compute.load_rh(run_dir), compute.load_hl(run_dir)
    return compute.trades_and_as_of(rh, hl)


# ----------------------------------------------------------------- Hyperliquid
class Missing(Exception):
    pass


def urllib_post(body, timeout=30):
    req = urllib.request.Request(HL_API, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        txt = e.read().decode(errors="replace").strip()
        if e.code == 500 and txt == "null":
            raise Missing()        # HL answers 500 "null" for an unknown coin
        raise


def _ms(day):
    return int(dt.datetime.combine(d(day), dt.time(), dt.timezone.utc).timestamp() * 1000)


def hl_candles(post, coin, s, e, sleep=time.sleep, rng=random.random, log=None):
    """Daily candles of `coin` for [s, e] -> ([(date, close)], weight). Retries 429 with backoff."""
    body = {"type": "candleSnapshot",
            "req": {"coin": coin, "interval": "1d", "startTime": _ms(s), "endTime": _ms(e) + 86399999}}
    for k in range(HL_RETRIES):
        try:
            rows = post(body)
            break
        except Missing:
            raise
        except urllib.error.HTTPError as ex:
            if ex.code != 429 and ex.code < 500 or k == HL_RETRIES - 1:
                raise
            wait = min(60.0, 2.0 ** (k + 1)) * (0.5 + rng())
            if log:
                log("  HTTP %d for %s, backing off %.1fs" % (ex.code, coin, wait))
            sleep(wait)
        except (urllib.error.URLError, TimeoutError, OSError):
            if k == HL_RETRIES - 1:
                raise
            sleep(min(60.0, 2.0 ** (k + 1)) * (0.5 + rng()))
    rows = rows or []
    out = []
    for r in rows:
        day = dt.datetime.fromtimestamp(int(r["t"]) / 1000.0, dt.timezone.utc).date().isoformat()
        out.append((day, float(r["c"])))
    return out, HL_BASE_WEIGHT + int(math.ceil(len(rows) / 60.0))


def _hl_series(nd):
    return {sid: n for sid, n in nd.items() if sid.startswith("hl:")}


def fetch_hl(run_dir, post=None, sleep=time.sleep, log=None, trades_as_of=None):
    """Fetch every missing HL candle range (resumable; throttled). Returns counts."""
    post = post or urllib_post
    log = log or (lambda m: sys.stderr.write(m + "\n"))
    trades, as_of = trades_as_of or data_trades(run_dir)
    as_of_day, nd = needs(trades, as_of)
    if not as_of_day:
        return {"error": "no account data yet: sync Robinhood and/or Hyperliquid first"}
    todo = sorted(_hl_series(nd))
    calls, weight, t0, done = 0, 0, time.time(), 0
    missing = []
    for i, sid in enumerate(todo):
        n = nd[sid]
        chain = [sid] + n["alt"]
        for cid in chain:
            c = load_series(run_dir, cid)
            if is_missing(c, as_of_day):
                continue
            gaps = subtract(n["ranges"], effective_covered(c, as_of_day))
            if not gaps:
                break
            span = [gaps[0][0], gaps[-1][1]]       # one call covers it (<= HL_MAX_CANDLES days)
            coin = cid.split(":", 1)[1]
            try:
                bars, w = hl_candles(post, coin, span[0], span[1], sleep=sleep, log=log)
            except Missing:
                c["missing_as_of"] = as_of_day
                save_series(run_dir, c)
                calls += 1
                weight += HL_BASE_WEIGHT
                log("candles %d/%d %s: not listed on Hyperliquid" % (i + 1, len(todo), coin))
                sleep(HL_BASE_WEIGHT * 60.0 / HL_WEIGHT_PER_MIN)
                continue
            put_bars(c, span[0], span[1], bars, as_of_day)
            save_series(run_dir, c)
            calls += 1
            weight += w
            done += 1
            log("candles %d/%d %s: %d bars %s..%s" % (i + 1, len(todo), coin, len(bars), span[0], span[1]))
            sleep(w * 60.0 / HL_WEIGHT_PER_MIN)
            break
        else:
            missing.append(sid)
    return {"status": "done", "source": "hyperliquid candleSnapshot 1d", "as_of_day": as_of_day,
            "series": len(todo), "calls": calls, "weight": weight, "fetched": done,
            "unavailable": missing, "seconds": round(time.time() - t0, 1)}


# ----------------------------------------------------------------- Robinhood (MCP plan loop)
def _cstate_path(run_dir):
    return os.path.join(os.path.abspath(run_dir), "state", "candles.json")


def _cstate(run_dir):
    return read_json(_cstate_path(run_dir), {"rh_window": {}, "attempts": {}, "failed": [], "calls": 0})


def rh_plan(run_dir, max_calls, trades_as_of=None):
    """Next get_equity_historicals calls ([{"sid", "symbol", "start", "end", "tool", "args"}])."""
    st = _cstate(run_dir)
    trades, as_of = trades_as_of or data_trades(run_dir)
    as_of_day, nd = needs(trades, as_of)
    if not as_of_day:
        return [], 0
    out, remaining = [], 0
    for sid in sorted(x for x in nd if x.startswith("rh:")):
        c = load_series(run_dir, sid)
        if is_missing(c, as_of_day):
            continue
        sym = sid.split(":", 1)[1]
        size = st["rh_window"].get(sym, RH_WEEKDAYS_PER_CALL)
        failed = [f[1:] for f in st["failed"] if f[0] == sid]
        for gs, ge in subtract(nd[sid]["ranges"], effective_covered(c, as_of_day) + failed):
            for s, e in chunks(gs, ge, size):
                remaining += 1
                if len(out) < max_calls:
                    out.append({"sid": sid, "symbol": sym, "start": s, "end": e, "as_of_day": as_of_day,
                                "tool": RH_TOOL,
                                "args": {"symbols": [sym], "start_time": s + "T00:00:00Z",
                                         "end_time": ds(d(e) + DAY) + "T00:00:00Z", "interval": "day",
                                         "bounds": "regular", "adjustment_type": "split"}})
    return out, remaining


def _parse_rh(text, sym):
    """-> ("ok", [(date, close)]) | ("truncated", None) | ("error", message)."""
    from .rhpage import TRUNC_MARK
    start = text.find('{"data"')
    if start < 0:
        low = text.lower()
        if "not found" in low or "invalid" in low or "unknown" in low or "no instrument" in low:
            return "unknown", None
        return "error", None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text, start)
        res = obj["data"]["results"]
    except (ValueError, KeyError, TypeError):
        return ("truncated", None) if TRUNC_MARK in text else ("error", None)
    if TRUNC_MARK in text:
        return "truncated", None
    bars = []
    found = False
    for r in res or []:
        if str(r.get("symbol", "")).upper() != sym.upper():
            continue
        found = True
        for b in r.get("bars") or []:
            if b.get("interpolated") or b.get("close_price") in (None, ""):
                continue
            bars.append((b["begins_at"][:10], float(b["close_price"])))
    return ("ok", bars) if found or res == [] else ("unknown", None)


def rh_ingest(run_dir, call, text):
    """Store one saved get_equity_historicals result. Returns a short status string."""
    st = _cstate(run_dir)
    m = call["candles"]
    sid, sym, s, e, as_of_day = m["sid"], m["symbol"], m["start"], m["end"], m["as_of_day"]
    st["calls"] = st.get("calls", 0) + 1
    status, bars = _parse_rh(text, sym)
    c = load_series(run_dir, sid)
    key = "%s|%s|%s" % (sid, s, e)
    if status == "ok":
        put_bars(c, s, e, bars, as_of_day)
        save_series(run_dir, c)
        st["attempts"].pop(key, None)
    elif status == "truncated":
        cur = st["rh_window"].get(sym, RH_WEEKDAYS_PER_CALL)
        # halve relative to this call's window, so a batch of truncated calls halves once
        st["rh_window"][sym] = max(RH_MIN_WEEKDAYS, min(cur, weekdays(s, e) // 2))
    elif status == "unknown":
        c["missing_as_of"] = as_of_day
        save_series(run_dir, c)
    else:
        n = st["attempts"].get(key, 0) + 1
        st["attempts"][key] = n
        if n >= RH_ATTEMPTS:
            st["failed"].append([sid, s, e])
            st["attempts"].pop(key, None)
    write_json(_cstate_path(run_dir), st)
    return status


YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/%s?period1=%d&period2=%d&interval=1d&events=split"
YAHOO_PAUSE_S = 1.0


def urllib_get_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (trading-journey lsl)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise Missing()
        if e.code == 400 and "doesn't exist" in e.read().decode(errors="replace"):
            return {"chart": {"result": [{"timestamp": [], "indicators": {"quote": [{"close": []}]}}]}}
        raise


def _yahoo_bars(obj):
    res = ((obj or {}).get("chart") or {}).get("result") or []
    if not res:
        raise Missing()
    r = res[0]
    off = (r.get("meta") or {}).get("gmtoffset") or 0
    ts = r.get("timestamp") or []
    q = (((r.get("indicators") or {}).get("quote") or [{}])[0].get("close")) or []
    out = {}
    for t, c in zip(ts, q):
        if c is None:
            continue
        out[dt.datetime.fromtimestamp(t + off, dt.timezone.utc).date().isoformat()] = round(float(c), 6)
    return sorted(out.items())


def fetch_stocks_yahoo(run_dir, get=None, sleep=time.sleep, log=None, trades_as_of=None):
    """Keyless stock candles: one Yahoo chart call per symbol over its uncovered span."""
    get = get or urllib_get_json
    log = log or (lambda m: sys.stderr.write(m + "\n"))
    t0 = time.time()
    trades, as_of = trades_as_of or data_trades(run_dir)
    as_of_day, nd = needs(trades, as_of)
    out = {"source": "yahoo", "series": 0, "calls": 0, "fetched": 0, "unavailable": [], "errors": []}
    if not as_of_day:
        return dict(out, seconds=0.0)
    todo = []
    for sid in sorted(x for x in nd if x.startswith("rh:")):
        out["series"] += 1
        c = load_series(run_dir, sid)
        if is_missing(c, as_of_day):
            out["unavailable"].append(sid)
            continue
        gaps = subtract(nd[sid]["ranges"], effective_covered(c, as_of_day))
        if gaps:
            todo.append((sid, gaps))
    for i, (sid, gaps) in enumerate(todo):
        sym = sid.split(":", 1)[1]
        s, e = gaps[0][0], gaps[-1][1]
        url = YAHOO_CHART % (sym.replace(".", "-"), _ms(s) // 1000, _ms(ds(d(e) + DAY)) // 1000)
        c = load_series(run_dir, sid)
        bars = None
        for k in range(HL_RETRIES):
            try:
                out["calls"] += 1
                bars = _yahoo_bars(get(url))
                break
            except Missing:
                break
            except Exception as ex:   # 429 / 5xx / network: back off with jitter
                code = getattr(ex, "code", None)
                if k == HL_RETRIES - 1 or (code is not None and 400 <= code < 500 and code != 429):
                    out["errors"].append([sid, type(ex).__name__])
                    break
                sleep(min(60.0, 2 ** k) * (1 + random.random()))
        if bars is None:
            if not any(x[0] == sid for x in out["errors"]):
                c["missing_as_of"] = as_of_day
                save_series(run_dir, c)
                out["unavailable"].append(sid)
                log("stock candles %d/%d %s: unavailable" % (i + 1, len(todo), sym))
        else:
            for gs, ge in gaps:
                put_bars(c, gs, ge, bars, as_of_day)
            c["source"] = "yahoo"
            save_series(run_dir, c)
            out["fetched"] += 1
            log("stock candles %d/%d %s: %d bars %s..%s" % (i + 1, len(todo), sym, len(bars), s, e))
        sleep(YAHOO_PAUSE_S)
    out["seconds"] = round(time.time() - t0, 1)
    return out


def status(run_dir, trades_as_of=None):
    trades, as_of = trades_as_of or data_trades(run_dir)
    as_of_day, nd = needs(trades, as_of)
    st = _cstate(run_dir)
    out = {"as_of_day": as_of_day, "hl": {"series": 0, "complete": 0, "missing": 0},
           "rh": {"series": 0, "complete": 0, "missing": 0, "windows_left": 0},
           "rh_calls_ingested": st.get("calls", 0), "rh_failed_windows": len(st["failed"])}
    for sid, n in sorted(nd.items()):
        src = "hl" if sid.startswith("hl:") else "rh"
        o = out[src]
        o["series"] += 1
        c = load_series(run_dir, sid)
        if is_missing(c, as_of_day):
            alts = [load_series(run_dir, a) for a in n["alt"]]
            if not any(not subtract(n["ranges"], effective_covered(a, as_of_day)) for a in alts):
                o["missing"] += 1
                continue
            o["complete"] += 1
            continue
        if not subtract(n["ranges"], effective_covered(c, as_of_day)):
            o["complete"] += 1
    out["rh"]["windows_left"] = rh_plan(run_dir, 0, (trades, as_of))[1]
    return out
