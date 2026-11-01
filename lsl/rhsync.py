"""Robinhood history sync as a resumable, callback-free state machine.

The bot never decides what to call. The loop is:

    lsl rh plan   --run RUN     -> JSON list of exact MCP calls (tool + args)
    (bot executes each call verbatim, with write_to_file, and moves the file
     to RUN/raw/<call id>.txt)
    lsl rh ingest --run RUN     -> parses, dedupes, validates, advances state
    ... repeat until plan says {"status": "done"}

Everything under RUN/state and RUN/raw holds private account data.

Streams (one in-flight call each, so a plan batch can run in parallel):
  accounts                       get_accounts
  <acct>.orders.{equity,option,crypto}
                                 windowed backward walk; every window is
                                 verified (cursor at its oldest record must
                                 return EMPTY); truncation keeps the complete
                                 records and continues at oldest+1us; MANGLED /
                                 ERROR retry then split the window; records
                                 outside the window split it; full-page ties
                                 are recorded as TIE_OVERFLOW
  <acct>.pnl.{class}             get_realized_pnl span=all (monthly buckets,
                                 the completeness oracle)
  <acct>.pnlday.{class}.<month>  daily buckets for months where RH counts more
                                 closes than our orders explain
  <acct>.repair.{class}.<day>    re-walk of a day RH says we under-counted
  <acct>.pos.{equity,option,crypto}
                                 current open positions
  <acct>.optuniverse             get_option_positions(nonzero=false) by
                                 expiration window, split when truncated
  <acct>.hist.<n>                get_pnl_trade_history(symbol, span=all),
                                 following next_cursor until ""
"""
import datetime as dt
import hashlib
import json
import os

from . import rhmodel
from .rhpage import (COMPLETE, EMPTY, ERROR, MANGLED, TRUNCATED, classify, mangled_head,
                     make_cursor)
from .util import (UTC, add_months, et_wall_to_utc, iso, read_json, to_utc,
                   write_json)

# The connector namespace is whatever the host named its Robinhood MCP server.
# It is passed once with --namespace and kept in rh_state.json. The engine never
# stores credentials and never plans a tool that is not a read-only get_*.
NO_NAMESPACE = ("pass --namespace <the Robinhood MCP namespace your host assigned "
                "(for example the name you gave https://agent.robinhood.com/mcp/trading). "
                "Do not put credentials in the namespace or the data dir.")
READ_ONLY_TOOLS = {
    "get_accounts", "get_equity_orders", "get_option_orders", "get_crypto_orders",
    "get_realized_pnl", "get_pnl_trade_history", "get_equity_positions",
    "get_option_positions", "get_crypto_positions",
    "get_equity_historicals",     # daily candles for the SMA trend tags (lsl/candles.py)
}
ORDER_TOOLS = {"equity": "get_equity_orders", "option": "get_option_orders",
               "crypto": "get_crypto_orders"}
ORDER_KEYS = {"equity": ("orders", "results"), "option": ("orders", "results"),
              "crypto": ("results", "orders")}
POS_TOOLS = {"equity": "get_equity_positions", "option": "get_option_positions",
             "crypto": "get_crypto_positions"}
POS_KEYS = ("positions", "results", "holdings")
CLASSES = ("equity", "option", "crypto")
# inline errors that mean "this stream does not exist for this account"
NOT_APPLICABLE = ("no crypto account linked",)
# once a walker has spent LANE_AFTER_CALLS calls and is still busy, the older full
# calendar years are handed to parallel "lane" walkers (one per year), each bounded
# by its own floor, so long histories finish in fewer bot rounds.
LANE_AFTER_CALLS = 6
LANE_START_YEAR = 2013
OPTUNI_SPLIT = 6
RETRIES = 2                       # re-issue a failed call this many times
MIN_WINDOW = dt.timedelta(minutes=10)
INCREMENTAL_OVERLAP = dt.timedelta(days=7)
MAX_CALLS = 6000
VERSION = 1


# ----------------------------------------------------------------- state io
class Run:
    def __init__(self, run_dir):
        self.dir = os.path.abspath(run_dir)
        self.state_dir = os.path.join(self.dir, "state")
        self.raw_dir = os.path.join(self.dir, "raw")
        self.path = os.path.join(self.state_dir, "rh_state.json")
        self.st = read_json(self.path)

    def save(self):
        write_json(self.path, self.st)

    def data_path(self, sid):
        safe = sid.replace("/", "_")
        return os.path.join(self.state_dir, "rh", safe + ".json")

    def load_data(self, sid, default):
        return read_json(self.data_path(sid), default)

    def save_data(self, sid, obj):
        write_json(self.data_path(sid), obj)


def init_state(run, now):
    run.st = {
        "version": VERSION, "round": 1, "sync_end": iso(now), "prev_sync_end": None,
        "seq": 0, "calls_issued": 0, "pending": {}, "accounts": [],
        "streams": {"accounts": {"type": "accounts", "status": "active", "attempts": 0}},
        "flags": {}, "opts": {"option_universe": False},
    }
    run.save()


def start_incremental(run, now):
    """New sync round over a finished state: re-walk recent windows, refresh the rest."""
    st = run.st
    prev = st["sync_end"]
    st["round"] += 1
    st["prev_sync_end"], st["sync_end"] = prev, iso(now)
    st["pending"] = {}
    floor = iso(to_utc(prev) - INCREMENTAL_OVERLAP)
    for sid, s in st["streams"].items():
        t = s["type"]
        if t == "orders" and s.get("kind") in ("repair", "lane"):
            s["status"] = "done"
        elif t == "orders":
            _orders_reset(s, st["sync_end"], floor)
        elif t in ("pnl", "pos", "accounts"):
            s.update(status="active", attempts=0, cursor=None)
            if t == "pos":
                run.save_data(sid, [])
        elif t in ("pnlday", "repair"):
            s["status"] = "done"
    for k in list(st["flags"]):
        if k.startswith(("repair_planned:", "history_planned:")):
            del st["flags"][k]
    run.save()


# ----------------------------------------------------------------- orders walker
def _orders_stream(acct_tag, cls, base, sync_end, floor=None):
    s = {"type": "orders", "status": "active", "cls": cls, "tool": ORDER_TOOLS[cls],
         "base": base, "attempts": 0, "stats": {}, "windows_done": 0, "empty_ranges": 0,
         "failed": [], "synced_through": None}
    _orders_reset(s, sync_end, floor)
    return s


def _orders_reset(s, sync_end, floor):
    lt = to_utc(sync_end)
    gte = add_months(lt.replace(hour=0, minute=0, second=0, microsecond=0), 0)
    if floor:
        gte = max(gte, to_utc(floor))
    s.update(status="active", phase="window", lt=iso(lt), gte=iso(gte), cur=iso(lt),
             oldest=None, seg_lt=iso(lt), seg_gte=iso(gte), stack=[], win_months=1,
             floor=floor, attempts=0, round_end=sync_end)


def _bump(s, key):
    s["stats"][key] = s["stats"].get(key, 0) + 1


def _orders_call(s):
    a = dict(s["base"])
    if s["phase"] == "probe":
        a["cursor"] = make_cursor(s["lt"])
    else:
        a["created_at_gte"] = iso(s["gte"], s["cls"] == "crypto")
        a["cursor"] = make_cursor(s["cur"])
    return s["tool"], a


def _ts(r):
    return to_utc(r["created_at"])


def _wait_for_single(run, sid, s, head):
    """A mangled page whose first record is readable by id: fetch that one order
    alone, then resume the walk strictly below it (see _one_ingest)."""
    S = run.st["streams"]
    key = "%s.one.%s" % (sid, head)
    if not head or key in S:
        return False
    a = dict(s["base"])
    a["order_id"] = head
    S[key] = {"type": "one", "status": "active", "owner": sid, "cls": s["cls"],
              "tool": s["tool"], "args": a, "attempts": 0}
    s["status"] = "wait"
    _bump(s, "single_fetch")
    return True


def _one_ingest(run, sid, s, status, recs, d):
    if status in (ERROR, MANGLED) and s["attempts"] < RETRIES:
        s["attempts"] += 1
        return
    owner = run.st["streams"][s["owner"]]
    owner["status"] = "active"
    if not recs and isinstance(d, dict) and d.get("id"):
        recs = [d]
    recs = [r for r in recs if isinstance(r, dict) and r.get("id") and r.get("created_at")]
    s["status"] = "done"
    if recs:
        _orders_ingest(run, s["owner"], owner, COMPLETE, recs[:1])
    else:
        s["why"] = status
        _orders_ingest(run, s["owner"], owner, MANGLED, [], head=None, final=True)


def _orders_ingest(run, sid, s, status, recs, head=None, final=False):
    if status == MANGLED and not final and _wait_for_single(run, sid, s, head):
        return
    if final:
        s["attempts"] = RETRIES
    _bump(s, status)
    data = run.load_data(sid, {})
    slack = dt.timedelta(seconds=1) if s["cls"] == "crypto" else dt.timedelta(0)

    if s["phase"] == "probe":
        lt = to_utc(s["lt"])
        if status in (ERROR, MANGLED):
            if s["attempts"] < RETRIES:
                s["attempts"] += 1
                return
            s["attempts"] = 0
            _bump(s, "probe_fallback")
            _new_segment(s, add_months(lt, -12), lt)
            return
        s["attempts"] = 0
        if status == EMPTY:
            s["status"] = "done"
            s["synced_through"] = s["round_end"]
            return
        recs = [r for r in recs if _ts(r) < lt]
        for r in recs:
            data[r["id"]] = r
        run.save_data(sid, data)
        if not recs:
            _bump(s, "probe_fallback")
            _new_segment(s, add_months(lt, -12), lt)
            return
        newest = max(_ts(r) for r in recs)
        if s.get("floor") and newest < to_utc(s["floor"]):
            s["status"] = "done"                 # nothing left inside this lane
            s["synced_through"] = s["round_end"]
            return
        ms = newest.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        hi = min(lt, add_months(ms, 1))
        if hi < lt:
            s["empty_ranges"] += 1           # [hi, lt) proven empty by the probe
        _new_segment(s, add_months(ms, -(s["win_months"] - 1)), hi)
        return

    gte, cur = to_utc(s["gte"]), to_utc(s["cur"])
    if status in (ERROR, MANGLED):
        if s["attempts"] < RETRIES:
            s["attempts"] += 1
            return
        s["attempts"] = 0
        _split(run, sid, s, status, data)
        return
    s["attempts"] = 0
    if any(not (gte - slack <= _ts(r) < cur) for r in recs):
        _split(run, sid, s, "OUT_OF_WINDOW", data)
        return
    new = [r for r in recs if r["id"] not in data]
    for r in recs:
        data[r["id"]] = r                    # newer copy wins (state changes)
    run.save_data(sid, data)
    if recs:
        t = min(_ts(r) for r in recs)
        s["oldest"] = iso(t if s["oldest"] is None else min(to_utc(s["oldest"]), t))
    if status == TRUNCATED:
        oldest = to_utc(s["oldest"])
        if not new:
            s["failed"].append([s["oldest"], s["oldest"], "TIE_OVERFLOW"])
            s["cur"] = iso(oldest)
        else:
            s["cur"] = iso(oldest + dt.timedelta(microseconds=1))
        return
    if status == EMPTY and (s["oldest"] is None or s["cur"] == s["oldest"]):
        _window_done(run, sid, s, data)
        return
    s["cur"] = s["oldest"]                   # verify / continue strictly below oldest


def _split(run, sid, s, why, data):
    gte, lt = to_utc(s["gte"]), to_utc(s["lt"])
    _bump(s, "split")
    if lt - gte <= MIN_WINDOW:
        s["failed"].append([iso(gte), iso(lt), why])
        _window_done(run, sid, s, data, failed=True)
        return
    mid = gte + (lt - gte) / 2
    s["stack"].append([iso(gte), iso(mid)])
    s.update(gte=iso(mid), lt=iso(lt), cur=iso(lt), oldest=None)


def _new_segment(s, gte, lt):
    if s.get("floor") and gte < to_utc(s["floor"]):
        gte = to_utc(s["floor"])
    s.update(phase="window", seg_gte=iso(gte), seg_lt=iso(lt), gte=iso(gte), lt=iso(lt),
             cur=iso(lt), oldest=None, stack=[])


def _window_done(run, sid, s, data, failed=False):
    if not failed:
        s["windows_done"] += 1
    if s["stack"]:
        g, l = s["stack"].pop()
        s.update(gte=g, lt=l, cur=l, oldest=None)
        return
    seg_gte, seg_lt = to_utc(s["seg_gte"]), to_utc(s["seg_lt"])
    n = sum(1 for r in data.values() if seg_gte <= _ts(r) < seg_lt)
    if s.get("floor") and seg_gte <= to_utc(s["floor"]):
        s["status"] = "done"
        s["synced_through"] = s["round_end"]
        return
    if n == 0:
        s.update(phase="probe", lt=iso(seg_gte), gte=None, cur=None, oldest=None)
        return
    wm = s["win_months"]
    wm = min(wm * 2, 12) if n < 15 else (max(1, wm // 2) if n > 60 else wm)
    s["win_months"] = wm
    _new_segment(s, add_months(seg_gte, -wm), seg_gte)


# ----------------------------------------------------------------- simple streams
def _simple_ingest(run, sid, s, status, recs, d):
    """pnl / pnlday / pos / hist: one call, or a cursor chain."""
    _bump(s, status) if "stats" in s else None
    if status in (ERROR, MANGLED) and s["attempts"] < RETRIES:
        s["attempts"] += 1
        return
    s["attempts"] = 0
    t = s["type"]
    if t in ("pnl", "pnlday"):
        if status in (COMPLETE, EMPTY):
            run.save_data(sid, {"data_points": recs, "total": _totals(d)})
            s["status"] = "done"
        else:
            s["status"] = "failed"
            s["why"] = status
        return
    # cursor-chained lists (positions, trade history)
    rows = run.load_data(sid, [])
    if t == "pos":
        seen = {_row_key(r) for r in rows}
        for r in recs:
            k = _row_key(r)
            if k not in seen:
                seen.add(k)
                rows.append(r)
    else:
        rows.extend(recs)                    # trade-history pages never overlap
    run.save_data(sid, rows)
    if status == TRUNCATED:
        s["status"] = "partial"
        s["why"] = "TRUNCATED"
        return
    if status in (ERROR, MANGLED):
        s["status"] = "failed"
        s["why"] = status
        return
    nxt = (d or {}).get("next_cursor", (d or {}).get("next"))
    if nxt and status == COMPLETE and nxt != s.get("cursor"):
        s["cursor"] = nxt
        s["pages"] = s.get("pages", 1) + 1
        if s["pages"] > 200:
            s["status"] = "failed"
            s["why"] = "TOO_MANY_PAGES"
        return
    s["status"] = "done"


def _totals(d):
    if not isinstance(d, dict):
        return None
    return {k: v for k, v in d.items() if not isinstance(v, (list, dict))}


def _row_key(r):
    for k in ("id", "option_id"):
        if k in r:
            return "%s:%s:%s" % (k, r[k], r.get("type", ""))
    return hashlib.sha1(json.dumps(r, sort_keys=True).encode()).hexdigest()


def _optuni_ingest(run, sid, s, status, recs):
    """Option-contract universe by expiration window.  Each window is its own
    stream; a truncated window is split into OPTUNI_SPLIT child streams that
    are planned in parallel.  Parents end as "split"."""
    if status in (ERROR, MANGLED) and s["attempts"] < RETRIES:
        s["attempts"] += 1
        return
    s["attempts"] = 0
    data = run.load_data(sid, {})
    for r in recs:
        data["%s:%s" % (r.get("option_id") or r.get("id"), r.get("type", ""))] = r
    run.save_data(sid, data)
    S = run.st["streams"]
    wins = list(s.get("stack") or [])
    s["stack"] = []
    d0, d1 = dt.date.fromisoformat(s["win"][0]), dt.date.fromisoformat(s["win"][1])
    if status in (TRUNCATED, MANGLED, ERROR):
        if (d1 - d0).days < 1:
            s["failed"].append([s["win"][0], s["win"][1], status])
        else:
            n = min(OPTUNI_SPLIT, (d1 - d0).days + 1)
            step = ((d1 - d0).days + 1) / n
            edges = [d0 + dt.timedelta(days=int(round(i * step))) for i in range(n + 1)]
            for i in range(n):
                a, b = edges[i], edges[i + 1] - dt.timedelta(days=1)
                if b >= a:
                    wins.append([a.isoformat(), b.isoformat()])
    for i, w in enumerate(sorted(wins)):
        csid = "%s.%d" % (sid, i)
        S[csid] = {"type": "optuni", "status": "active", "attempts": 0, "tool": s["tool"],
                   "args": dict(s["args"]), "win": w, "stack": [], "failed": []}
    if wins:
        s["status"] = "split"
    else:
        s["status"] = "done" if not s["failed"] else "partial"


# ----------------------------------------------------------------- call building
def _call_for(st, sid, s):
    t = s["type"]
    if t == "accounts":
        return "get_accounts", {}
    if t == "orders":
        return _orders_call(s)
    if t == "one":
        return s["tool"], dict(s["args"])
    if t in ("pnl", "pnlday", "pos", "hist", "optuni"):
        a = dict(s["args"])
        if t == "optuni":
            a["expiration_date_gte"], a["expiration_date_lte"] = s["win"]
        if s.get("cursor"):
            a["cursor"] = s["cursor"]
        return s["tool"], a
    raise ValueError(t)


def _list_keys(s):
    t = s["type"]
    if t == "accounts":
        return ("accounts",)
    if t in ("orders", "repair", "one"):
        return ORDER_KEYS[s["cls"]]
    if t in ("pnl", "pnlday"):
        return ("data_points",)
    if t == "hist":
        return ("trades", "results")
    return POS_KEYS


def _cid(st):
    st["seq"] += 1
    return "r%d_c%05d" % (st["round"], st["seq"])


# ----------------------------------------------------------------- coordinator
def _ensure_streams(run):
    """Create follow-up streams once their inputs are complete (deterministic)."""
    st = run.st
    S = st["streams"]
    _spawn_lanes(run)
    for a in st["accounts"]:
        tag = a["tag"]
        orders = [S[k] for k in sorted(S) if k.startswith(tag + ".orders.")]
        pnls = [S.get("%s.pnl.%s" % (tag, c)) for c in CLASSES]
        pnls = [p for p in pnls if p]
        base_done = all(o["status"] in ("done", "na") for o in orders
                        if o["type"] == "orders") and \
            all(p["status"] in ("done", "failed", "na") for p in pnls)
        if not base_done:
            continue
        fk = "repair_planned:%s" % tag
        if fk not in st["flags"]:
            st["flags"][fk] = 1
            _plan_daily_checks(run, a)
            continue
        if any(S[k]["status"] == "active" for k in S
               if k.startswith(tag + ".pnlday.") or k.startswith(tag + ".repair.")):
            continue
        fk2 = "repair_walk:%s" % tag
        if fk2 not in st["flags"]:
            st["flags"][fk2] = 1
            if _plan_repairs(run, a):
                continue
        if any(S[k]["status"] == "active" for k in S if k.startswith(tag + ".repair.")):
            continue
        fk3 = "history_planned:%s" % tag
        if fk3 not in st["flags"]:
            st["flags"][fk3] = 1
            _plan_history(run, a)
        _plan_optuni(run, a)


def _spawn_lanes(run):
    st = run.st
    S = st["streams"]
    for sid in sorted(S):
        s = S[sid]
        if s["type"] != "orders" or s.get("kind") or s.get("laned") or s["status"] != "active":
            continue
        if s["phase"] != "window" or sum(s["stats"].values()) < LANE_AFTER_CALLS:
            continue
        year = to_utc(s["seg_gte"]).year
        s["laned"] = True
        if year <= LANE_START_YEAR:
            continue
        s["floor"] = iso(dt.datetime(year, 1, 1, tzinfo=UTC))
        for y in range(LANE_START_YEAR, year):
            lo = dt.datetime(y, 1, 1, tzinfo=UTC)
            hi = dt.datetime(y + 1, 1, 1, tzinfo=UTC)
            # the oldest lane is open-ended so nothing older can be skipped
            ln = _orders_stream(None, s["cls"], s["base"], iso(hi),
                                None if y == LANE_START_YEAR else iso(lo))
            ln.update(kind="lane", phase="probe", lt=iso(hi), gte=None, cur=None,
                      round_end=s["round_end"])
            S["%s.y%d" % (sid, y)] = ln
    # a year lane that is still busy after LANE_AFTER_CALLS calls hands its older
    # whole months to month lanes the same way (dense years finish in fewer rounds)
    for sid in sorted(S):
        s = S[sid]
        if s["type"] != "orders" or s.get("kind") != "lane" or s.get("laned"):
            continue
        if s["status"] != "active" or s["phase"] != "window" or not s.get("floor"):
            continue
        if sum(s["stats"].values()) < LANE_AFTER_CALLS:
            continue
        s["laned"] = True
        top = to_utc(s["seg_gte"]).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        lo = to_utc(s["floor"])
        if top <= lo:
            continue
        s["floor"] = iso(top)
        m = lo
        while m < top:
            hi = add_months(m, 1)
            ln = _orders_stream(None, s["cls"], s["base"], iso(hi), iso(m))
            ln.update(kind="lane", phase="probe", lt=iso(hi), gte=None, cur=None,
                      round_end=s["round_end"], laned=True)
            S["%s.m%02d" % (sid, m.month)] = ln
            m = hi


def _orders_of(run, tag, cls):
    main = "%s.orders.%s" % (tag, cls)
    recs = {}
    for sid, s in sorted(run.st["streams"].items()):
        if s["type"] == "orders" and (sid == main or sid.startswith(main + ".y")
                                      or sid.startswith(tag + ".repair.%s." % cls)):
            recs.update(run.load_data(sid, {}))
    return list(recs.values())


def _rh_buckets(run, sid, monthly=True):
    d = run.load_data(sid, None)
    if not d:
        return {}
    return rhmodel.pnl_buckets(d["data_points"], monthly)


def _plan_daily_checks(run, a):
    """Months where RH counts more closing trades than our orders -> daily buckets."""
    st, tag = run.st, a["tag"]
    for cls in CLASSES:
        psid = "%s.pnl.%s" % (tag, cls)
        if psid not in st["streams"] or st["streams"][psid]["status"] != "done":
            continue
        rh = _rh_buckets(run, psid)
        ours = rhmodel.closing_counts(cls, _orders_of(run, tag, cls), st["sync_end"], daily=False)
        for m in sorted(rh):
            if st.get("prev_sync_end") and m < rhmodel.et_month_of(st["prev_sync_end"]):
                continue
            if rh[m][0] > ours.get(m, 0):
                y, mo = int(m[:4]), int(m[5:7])
                end = add_months(dt.date(y, mo, 1), 1) - dt.timedelta(days=1)
                sid = "%s.pnlday.%s.%s" % (tag, cls, m)
                st["streams"][sid] = {"type": "pnlday", "status": "active", "attempts": 0,
                                      "cls": cls, "month": m, "tool": "get_realized_pnl",
                                      "args": {"account_number": a["rhs_account_number"],
                                               "asset_classes": [cls],
                                               "start_date": "%s-01" % m,
                                               "end_date": end.isoformat()}}


def _plan_repairs(run, a):
    """Days where daily RH closes exceed ours -> re-walk that ET day's orders."""
    st, tag = run.st, a["tag"]
    made = False
    for sid in sorted(k for k in st["streams"] if k.startswith(tag + ".pnlday.")):
        s = st["streams"][sid]
        if s["status"] != "done":
            continue
        cls = s["cls"]
        rh = _rh_buckets(run, sid, monthly=False)
        ours = rhmodel.closing_counts(cls, _orders_of(run, tag, cls), st["sync_end"], daily=True)
        for day in sorted(rh):
            if rh[day][0] > ours.get(day, 0):
                # walk the ET day plus one day before (options: orders placed earlier)
                lo = et_wall_to_utc((dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat(), 0)
                hi = et_wall_to_utc((dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat(), 0)
                rsid = "%s.repair.%s.%s" % (tag, cls, day)
                if rsid in st["streams"]:
                    continue
                base = st["streams"]["%s.orders.%s" % (tag, cls)]["base"]
                r = _orders_stream(tag, cls, base, iso(hi), floor=iso(lo))
                r["kind"] = "repair"
                st["streams"][rsid] = r
                made = True
    return made


def _plan_history(run, a):
    st, tag = run.st, a["tag"]
    S = st["streams"]
    # coverage start: first month any class has RH-counted closes
    cov = None
    for cls in CLASSES:
        psid = "%s.pnl.%s" % (tag, cls)
        if psid in S and S[psid]["status"] == "done":
            months = [m for m, v in _rh_buckets(run, psid).items() if v[0] or v[1]]
            if months:
                cov = min(months) if cov is None else min(cov, min(months))
    st["flags"]["coverage:%s" % tag] = cov
    since = st.get("prev_sync_end")
    if cov:
        syms = set()
        for cls in CLASSES:
            for o in _orders_of(run, tag, cls):
                t = o.get("last_transaction_at") or o.get("updated_at") or o["created_at"]
                if rhmodel.et_month_of(t) < cov:
                    continue
                if since and to_utc(t) < to_utc(since) - INCREMENTAL_OVERLAP:
                    continue
                s = rhmodel.history_symbol(cls, o)
                if s and rhmodel.is_filled(cls, o):
                    syms.add(s)
        index = st["flags"].setdefault("hist_index:%s" % tag, {})
        for sym in sorted(syms):
            if sym not in index:
                index[sym] = len(index)
            sid = "%s.hist.%d" % (tag, index[sym])
            S[sid] = {"type": "hist", "status": "active", "attempts": 0, "cursor": None,
                      "tool": "get_pnl_trade_history",
                      "args": {"account_number": a["rhs_account_number"], "span": "all",
                               "symbol": sym}}
            run.save_data(sid, [])


def _plan_optuni(run, a):
    """Option contract universe (expiration windows by year), opt-in, only for
    accounts with options.  Re-checked every plan so it can be enabled later."""
    st, tag = run.st, a["tag"]
    S = st["streams"]
    usid = "%s.optuniverse" % tag
    if usid in S or not st.get("opts", {}).get("option_universe"):
        return
    opts = _orders_of(run, tag, "option")
    if opts:
        y0 = min(to_utc(o["created_at"]).year for o in opts)
        y1 = to_utc(st["sync_end"]).year + 3
        wins = [["%d-01-01" % y, "%d-12-31" % y] for y in range(y1, y0 - 1, -1)]
        first = wins.pop()
        S[usid] = {"type": "optuni", "status": "active", "attempts": 0, "tool": "get_option_positions",
                   "args": {"account_number": a["account_number"], "nonzero": False},
                   "win": first, "stack": wins, "failed": []}


def _on_accounts(run, recs):
    st = run.st
    known = {a["account_number"]: a for a in st["accounts"]}
    for r in recs:
        num = r.get("account_number")
        if not num or r.get("deactivated") or r.get("permanently_deactivated"):
            continue
        if num not in known:
            known[num] = {"tag": "a%d" % len(known), "account_number": num,
                          "rhs_account_number": r.get("rhs_account_number"),
                          "kind": r.get("brokerage_account_type") or r.get("type") or "unknown"}
            st["accounts"].append(known[num])
    S = st["streams"]
    for a in st["accounts"]:
        tag = a["tag"]
        for cls in CLASSES:
            if cls == "crypto":
                if not a.get("rhs_account_number"):
                    continue
                base = {"rhs_account_number": a["rhs_account_number"]}
            else:
                base = {"account_number": a["account_number"]}
            sid = "%s.orders.%s" % (tag, cls)
            if sid not in S:
                S[sid] = _orders_stream(tag, cls, base, st["sync_end"])
            if a.get("rhs_account_number"):
                psid = "%s.pnl.%s" % (tag, cls)
                S.setdefault(psid, {"type": "pnl", "status": "active", "attempts": 0, "cls": cls,
                                    "tool": "get_realized_pnl",
                                    "args": {"account_number": a["rhs_account_number"],
                                             "span": "all", "asset_classes": [cls]}})
            pos_args = ({"rhs_account_number": a["rhs_account_number"]} if cls == "crypto"
                        else {"account_number": a["account_number"]})
            if cls == "option":
                pos_args["nonzero"] = True
            if cls == "crypto" and not a.get("rhs_account_number"):
                continue
            S.setdefault("%s.pos.%s" % (tag, cls), {"type": "pos", "status": "active", "attempts": 0,
                                                    "cls": cls, "tool": POS_TOOLS[cls],
                                                    "args": pos_args, "cursor": None})


# ----------------------------------------------------------------- public API
def set_option_universe(run, enabled):
    """The option-contract universe (get_option_positions nonzero=false by
    expiration window) is an optional cross-check: option order legs already
    carry expiration/strike/option_id.  Turning it off marks unfinished windows
    "skipped" and drops their unexecuted pending calls; finished windows are
    kept and used as a partial cross-check."""
    st = run.st
    st.setdefault("opts", {})["option_universe"] = bool(enabled)
    if not enabled:
        for cid in sorted(st["pending"]):
            if st["streams"][st["pending"][cid]["stream"]]["type"] == "optuni":
                del st["pending"][cid]
        for s in st["streams"].values():
            if s["type"] == "optuni" and s["status"] == "active":
                s["status"] = "skipped"
    run.save()


def set_namespace(run, namespace):
    """Remember the connector namespace; already planned calls follow it."""
    run.st["namespace"] = namespace
    for c in run.st["pending"].values():
        c["namespace"] = namespace
    run.save()


def _candle_calls(run, max_calls):
    """Stock candle windows, planned once the account streams are done (lsl/candles.py)."""
    from . import candles
    st = run.st
    calls = []
    try:
        planned, _ = candles.rh_plan(run.dir, max_calls)
    except Exception as e:          # never let candles break the account sync
        return [], "candle planning failed: %s" % type(e).__name__
    for p in planned:
        assert p["tool"] in READ_ONLY_TOOLS, p["tool"]
        cid = _cid(st)
        c = {"id": cid, "stream": "candles", "namespace": st["namespace"], "tool": p["tool"],
             "args": p["args"], "save_as": os.path.join(run.raw_dir, cid + ".txt"),
             "candles": {k: p[k] for k in ("sid", "symbol", "start", "end", "as_of_day")}}
        st["pending"][cid] = c
        calls.append(c)
    return calls, None


def plan(run, max_calls=8, now=None, option_universe=None, namespace=None, candles=True):
    if run.st is None:
        if not namespace:
            return {"error": NO_NAMESPACE}
        init_state(run, now or dt.datetime.now(UTC))
    st = run.st
    if namespace and namespace != st.get("namespace"):
        set_namespace(run, namespace)
    if not st.get("namespace"):
        return {"error": NO_NAMESPACE}
    if option_universe is not None and bool(option_universe) != st.get("opts", {}).get("option_universe"):
        set_option_universe(run, option_universe)
    if st["pending"]:
        calls = list(st["pending"].values())
        return {"status": "pending", "note": "save these results, then run ingest", "calls": calls,
                "progress": progress(run)}
    # follow-up stages may finish instantly (e.g. no daily checks needed), so keep
    # advancing until some stream is active or nothing changes any more
    while True:
        before = (len(st["flags"]), len(st["streams"]))
        _ensure_streams(run)
        if any(s["status"] == "active" for s in st["streams"].values()):
            break
        if (len(st["flags"]), len(st["streams"])) == before:
            break
    calls = []
    for sid in sorted(st["streams"]):
        s = st["streams"][sid]
        if s["status"] != "active":
            continue
        if len(calls) >= max_calls:
            break
        if st["calls_issued"] >= MAX_CALLS:
            break
        tool, args = _call_for(st, sid, s)
        assert tool in READ_ONLY_TOOLS, tool
        cid = _cid(st)
        c = {"id": cid, "stream": sid, "namespace": st["namespace"], "tool": tool, "args": args,
             "save_as": os.path.join(run.raw_dir, cid + ".txt")}
        st["pending"][cid] = c
        st["calls_issued"] += 1
        calls.append(c)
    note = None
    if (not calls and candles and st["calls_issued"] < MAX_CALLS
            and not any(s["status"] == "active" for s in st["streams"].values())):
        calls, note = _candle_calls(run, max_calls)
    run.save()
    if not calls:
        return dict({"status": "done", "calls": [], "progress": progress(run)}, **({"note": note} if note else {}))
    return {"status": "calls", "calls": calls, "progress": progress(run),
            **({"stage": "stock candles (daily SMA history)"} if calls[0]["stream"] == "candles" else {})}


def ingest(run):
    st = run.st
    done, missing = [], []
    for cid in sorted(st["pending"]):
        c = st["pending"][cid]
        path = os.path.join(run.raw_dir, cid + ".txt")
        if not os.path.exists(path):
            missing.append(cid)
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        sid = c["stream"]
        if sid == "candles":
            from . import candles as candles_mod
            cst = candles_mod.rh_ingest(run.dir, c, text)
            del st["pending"][cid]
            os.makedirs(os.path.join(run.raw_dir, "done"), exist_ok=True)
            os.replace(path, os.path.join(run.raw_dir, "done", cid + ".txt"))
            done.append({"id": cid, "status": "candles:" + cst, "records": 0})
            continue
        s = st["streams"][sid]
        status, recs, d = classify(text, _list_keys(s))
        if status == ERROR and any(m in text for m in NOT_APPLICABLE):
            s["status"] = "na"
        elif s["type"] == "accounts":
            if status == COMPLETE:
                _on_accounts(run, recs)
                s["status"] = "done"
            elif s["attempts"] < RETRIES:
                s["attempts"] += 1
            else:
                s["status"] = "failed"
        elif s["type"] == "orders":
            head = mangled_head(text, _list_keys(s)) if status == MANGLED else None
            _orders_ingest(run, sid, s, status, recs, head=head)
        elif s["type"] == "one":
            _one_ingest(run, sid, s, status, recs, d)
        elif s["type"] == "optuni":
            _optuni_ingest(run, sid, s, status, recs)
        else:
            _simple_ingest(run, sid, s, status, recs, d)
        del st["pending"][cid]
        os.makedirs(os.path.join(run.raw_dir, "done"), exist_ok=True)
        os.replace(path, os.path.join(run.raw_dir, "done", cid + ".txt"))
        done.append({"id": cid, "status": status, "records": len(recs)})
    run.save()
    return {"ingested": done, "missing": missing, "progress": progress(run)}


def progress(run):
    st = run.st
    by = {}
    for s in st["streams"].values():
        k = s["type"]
        by.setdefault(k, {})
        by[k][s["status"]] = by[k].get(s["status"], 0) + 1
    return {"round": st["round"], "calls_issued": st["calls_issued"],
            "pending": len(st["pending"]), "streams": by}


def status(run):
    """Completeness summary (counts only, no account data)."""
    st = run.st
    out = {"progress": progress(run), "streams": {}}
    for sid in sorted(st["streams"]):
        s = st["streams"][sid]
        row = {"type": s["type"], "status": s["status"]}
        if s["type"] == "orders":
            row.update(windows_done=s["windows_done"], failed=len(s["failed"]),
                       records=len(run.load_data(sid, {})), stats=s["stats"])
        elif s["type"] in ("hist", "pos"):
            row["rows"] = len(run.load_data(sid, []))
        if s.get("why"):
            row["why"] = s["why"]
        out["streams"][sid] = row
    return out
