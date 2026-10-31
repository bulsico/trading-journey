"""Hyperliquid raw-data fetcher (public info API, no auth, read-only).

Zero to many addresses.  The configured list lives in the private data dir
(DATA/state/hl/addresses.json, never in the repo); each address syncs into its
own DATA/state/hl/<fingerprint>/ directory (fingerprint = sha256(address)[:12])
with its own incremental state.  `fetch_all` syncs every configured address
against one shared `now` and one spotMeta snapshot.  A pre-0.2 single-address
layout (files directly in state/hl/) is adopted when the matching address is
added, and read as the only address while no list exists.

Fetches, per address:
  * userFillsByTime           paged forward from t=0, deduped by tid
  * userFunding               paged forward, deduped by (time, coin, hash)
  * userNonFundingLedgerUpdates  paged forward, deduped by (time, hash, type)
  * spotMeta                  "@N" -> "BASE/QUOTE" names (no hardcoded map), plus a
                              slim token/pair index table used for the spot whitelist

Incremental: if RUN/state/hl already holds an earlier sync, each stream restarts
at its last stored timestamp and merges (dedupe by key), so a re-run fetches only
new rows and gives the same files a fresh full fetch would.  Snapshots (spotMeta,
clearinghouse, spot balances) are re-read every time.
  * clearinghouseState        perp open positions, default dex + every builder
                              dex seen in fills (keyed "main" / dex name)
  * spotClearinghouseState    spot balances

Paging: each request asks for [start, now]; the next start is the last
timestamp seen (ties at the boundary are re-fetched and deduped).  If a whole
page is duplicates (more same-millisecond rows than one page holds), the start
advances by +1 ms and the skipped millisecond is recorded as a possible gap.

Only calculation-free normalisation happens here; numbers stay as strings
from the API and are interpreted in compute.py.  The transport is injectable
so tests can run against a fake API.
"""
import hashlib
import json
import os
import re
import shutil
import urllib.request

from .util import ms, write_json, read_json

HL_API = "https://api.hyperliquid.xyz/info"
ADDRESS_RE = re.compile(r"^0x[0-9a-f]{40}$")
LABEL_RE = re.compile(r"^[A-Za-z0-9_-]{1,24}$")
FILES = ("fills", "funding", "ledger", "spot_names", "spot_meta", "clearinghouse", "spot_clearinghouse",
         "info")
FILL_CAP = 10000          # HL serves at most the 10k most recent fills
MAX_PAGES = 200


def urllib_transport(body, timeout=30):
    req = urllib.request.Request(HL_API, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _page_forward(post, body, key, t_of, end, start=0, existing=()):
    """Generic forward pager over [start, end] ms.  Returns (rows, info).

    Next start = last timestamp seen, so boundary ties are re-read and deduped.
    If a page brings nothing new: when every row sits on the start millisecond
    and the page is as long as the longest page seen, more same-ms rows may be
    hidden (counted as a possible tie gap); either way start moves +1 ms.  An
    empty page ends the walk.  `existing` rows (an earlier sync) seed the dedupe
    set and the result, so an incremental walk from their last timestamp gives
    the same rows as a full walk."""
    rows = list(existing)
    seen = {key(r) for r in rows}
    pages, gaps, max_len = 0, 0, 0
    while pages < MAX_PAGES and start <= end:
        page = post(dict(body, startTime=start, endTime=end)) or []
        pages += 1
        if not page:
            break
        max_len = max(max_len, len(page))
        times = [t_of(r) for r in page]
        new = 0
        for r in page:
            k = key(r)
            if k not in seen:
                seen.add(k)
                rows.append(r)
                new += 1
        last = max(times)
        if new:
            start = last
            continue
        if min(times) == last == start and len(page) == max_len and max_len > 1:
            gaps += 1
        start = last + 1
    rows.sort(key=lambda r: (t_of(r), json.dumps(key(r), sort_keys=True, default=str)))
    return rows, {"pages": pages, "possible_tie_gaps": gaps, "hit_page_limit": pages >= MAX_PAGES,
                  "new": len(rows) - len(existing)}


def _start_after(rows, t_of):
    """Incremental start: the last stored timestamp (ties there are re-read and deduped)."""
    return max((t_of(r) for r in rows), default=0)


def spot_meta_slim(meta):
    """Public spotMeta subset kept for compute: token index/name/tokenId and pair -> token indices."""
    return {"tokens": sorted(({"index": t.get("index"), "name": t.get("name"), "tokenId": t.get("tokenId")}
                              for t in (meta or {}).get("tokens", [])), key=lambda t: t["index"]),
            "universe": sorted(({"index": u.get("index"), "name": u.get("name"), "tokens": u.get("tokens")}
                                for u in (meta or {}).get("universe", [])), key=lambda u: u["index"])}


def _fill_key(f):
    return ("tid", f.get("tid"), f.get("oid"), f.get("coin")) if f.get("tid") is not None else \
        ("h", f.get("hash"), f.get("time"), f.get("coin"), f.get("px"), f.get("sz"))


def _ledger_key(u):
    d = u.get("delta") or {}
    return (u.get("time"), u.get("hash"), d.get("type"), d.get("coin"), str(d.get("usdc")))


def spot_names(meta):
    """spotMeta -> {"@N": "BASE/QUOTE"} using universe[N].tokens -> tokens[i].name."""
    toks = {t.get("index"): t.get("name") for t in (meta or {}).get("tokens", [])}
    out = {}
    for u in (meta or {}).get("universe", []):
        idx = u.get("index")
        tk = u.get("tokens") or []
        if idx is None or len(tk) < 2:
            continue
        base, quote = toks.get(tk[0]), toks.get(tk[1])
        if base and quote:
            out["@%d" % idx] = "%s/%s" % (base, quote)
        elif u.get("name"):
            out["@%d" % idx] = u["name"]
        # named pairs (e.g. "BASE/USDC") are already readable
        if u.get("name") and "/" in u["name"]:
            out[u["name"]] = u["name"]
    return out


def tokens(unit=False, names=(), post=None):
    """Public spotMeta lookup for refreshing lsl/data/hl_spot_whitelist.json."""
    post = post or urllib_transport
    meta = post({"type": "spotMeta"}) or {}
    want = {n.upper() for n in names}
    out = []
    for t in meta.get("tokens", []):
        nm, full = str(t.get("name")), str(t.get("fullName") or "")
        if (unit and (full.startswith("Unit ") or nm == "HYPE")) or nm.upper() in want:
            out.append({"index": t.get("index"), "name": nm, "fullName": t.get("fullName"),
                        "tokenId": t.get("tokenId")})
    pairs = {}
    idx = {t["index"] for t in out}
    for u in meta.get("universe", []):
        tk = u.get("tokens") or []
        if tk and tk[0] in idx:
            pairs.setdefault(tk[0], []).append({"pair": "@%s" % u.get("index"), "quote_token": tk[1]})
    for t in out:
        t["pairs"] = pairs.get(t["index"], [])
    return {"status": "done", "tokens": sorted(out, key=lambda t: t["index"])}


# ----------------------------------------------------------------- address list
def normalize(address):
    """Lower-cased 0x + 40 hex digits, or None."""
    a = str(address or "").strip().lower()
    return a if ADDRESS_RE.match(a) else None


def fingerprint(address):
    return hashlib.sha256(address.encode()).hexdigest()[:12]


def short(address, n=4):
    return "%s\u2026%s" % (address[:2 + n], address[-n:])


def _root(run_dir):
    return os.path.join(run_dir, "state", "hl")


def _cfg_path(run_dir):
    return os.path.join(_root(run_dir), "addresses.json")


def addr_dir(run_dir, address):
    return os.path.join(_root(run_dir), fingerprint(address))


def load_config(run_dir):
    """Sorted, deduped [{"address", "label"}] or None when no list was ever saved."""
    cfg = read_json(_cfg_path(run_dir))
    if cfg is None:
        return None
    seen, out = set(), []
    for e in cfg.get("addresses") or []:
        a = normalize(e.get("address"))
        if a and a not in seen:
            seen.add(a)
            out.append({"address": a, "label": e.get("label")})
    return sorted(out, key=lambda e: e["address"])


def _save_config(run_dir, entries):
    write_json(_cfg_path(run_dir), {"addresses": sorted(entries, key=lambda e: e["address"])})


def _legacy_fp(run_dir):
    info = read_json(os.path.join(_root(run_dir), "info.json"))
    return (info or {}).get("address_fingerprint") if info else None


def _adopt_legacy(run_dir, address):
    """Move a pre-0.2 flat state/hl layout into the address's own dir when it is that address."""
    if _legacy_fp(run_dir) != fingerprint(address):
        return False
    d = addr_dir(run_dir, address)
    os.makedirs(d, exist_ok=True)
    for k in FILES:
        src = os.path.join(_root(run_dir), k + ".json")
        if os.path.exists(src):
            os.replace(src, os.path.join(d, k + ".json"))
    return True


def tags(entries):
    """address -> display tag.  One address: "hl" (scope/account ids stay as in 0.1);
    several: the custom label or a short 0x1234\u20265678 form, widened until unique."""
    if len(entries) == 1:
        return {entries[0]["address"]: "hl"}
    out = {}
    for n in (4, 6, 8, 20):
        out = {e["address"]: e.get("label") or short(e["address"], n) for e in entries}
        if len(set(out.values())) == len(out):
            break
    return out


def labels(entries):
    """address -> human label (custom label or short form), for display only."""
    t = tags(entries) if len(entries) > 1 else {}
    return {e["address"]: e.get("label") or t.get(e["address"]) or short(e["address"]) for e in entries}


def add(run_dir, address, label=None):
    a = normalize(address)
    if not a:
        return {"error": "not a Hyperliquid address: expected 0x followed by 40 hex digits"}
    if label is not None and not LABEL_RE.match(label):
        return {"error": "label must be 1-24 letters, digits, '-' or '_'"}
    entries = load_config(run_dir) or []
    if any(e["address"] == a for e in entries):
        return {"status": "already_added", "address": short(a), "count": len(entries)}
    if label and any(e.get("label") == label for e in entries):
        return {"error": "label %r is already used" % label}
    entries.append({"address": a, "label": label})
    os.makedirs(_root(run_dir), exist_ok=True)
    adopted = _adopt_legacy(run_dir, a)
    _save_config(run_dir, entries)
    return {"status": "added", "address": short(a), "fingerprint": fingerprint(a),
            "adopted_existing_data": adopted, "count": len(entries)}


def _find(entries, ident):
    ident = str(ident or "").strip()
    a = normalize(ident)
    for e in entries:
        if a and e["address"] == a:
            return e
    for e in entries:
        if ident and ident in (fingerprint(e["address"]), e.get("label"), short(e["address"])):
            return e
    return None


def remove(run_dir, ident):
    """Drop an address (full address, label, fingerprint or short form) and delete its stored data."""
    entries = load_config(run_dir) or []
    e = _find(entries, ident)
    if not e:
        return {"error": "no configured address matches %r" % ident, "count": len(entries)}
    entries = [x for x in entries if x is not e]
    _save_config(run_dir, entries)
    d = addr_dir(run_dir, e["address"])
    if os.path.isdir(d):
        shutil.rmtree(d)
    return {"status": "removed", "address": short(e["address"]), "count": len(entries)}


def listing(run_dir):
    entries = load_config(run_dir)
    out = []
    lab = labels(entries or [])
    for e in entries or []:
        info = read_json(os.path.join(addr_dir(run_dir, e["address"]), "info.json"))
        out.append({"address": e["address"], "short": short(e["address"]), "label": lab[e["address"]],
                    "fingerprint": fingerprint(e["address"]), "synced": info is not None,
                    "as_of_ms": (info or {}).get("as_of_ms"),
                    "fills": ((info or {}).get("fills") or {}).get("count")})
    res = {"status": "done", "count": len(out), "addresses": out}
    lf = _legacy_fp(run_dir)
    if lf:
        res["legacy_data"] = {"fingerprint": lf, "used": entries is None,
                              "hint": "data from an earlier single-address version; `lsl hl add <that address>` "
                                      "adopts it, `lsl purge --source hl` deletes it"}
    return res


# ----------------------------------------------------------------- fetch
def fetch(run_dir, address, now, post=None):
    """Add `address` (if new) and sync just that address."""
    res = add(run_dir, address)
    if "error" in res:
        return res
    return _fetch_one(run_dir, normalize(address), ms(now), post or urllib_transport)


def fetch_all(run_dir, now, post=None):
    """Sync every configured address against one `now` and one spotMeta snapshot."""
    post = post or urllib_transport
    entries = load_config(run_dir) or []
    if not entries:
        res = {"error": "no Hyperliquid addresses configured; `lsl hl add 0x...` first"}
        if _legacy_fp(run_dir):
            res["legacy_data"] = listing(run_dir)["legacy_data"]
        return res
    end = ms(now)
    meta = post({"type": "spotMeta"}) or {}
    lab = labels(entries)
    out = []
    for e in entries:
        r = _fetch_one(run_dir, e["address"], end, post, meta=meta)
        out.append({"address": short(e["address"]), "label": lab[e["address"]], "info": r["info"]})
    return {"status": "done", "count": len(out), "addresses": out}


def _fetch_one(run_dir, address, end, post, meta=None):
    d = addr_dir(run_dir, address)
    os.makedirs(d, exist_ok=True)
    prev = _load_dir(d) or {}
    pinfo = prev.get("info") or {}
    t_of = lambda r: int(r.get("time", 0))
    streams = {}
    for name, body, key in (
            ("fills", {"type": "userFillsByTime", "user": address, "aggregateByTime": False}, _fill_key),
            ("funding", {"type": "userFunding", "user": address}, _ledger_key),
            ("ledger", {"type": "userNonFundingLedgerUpdates", "user": address}, _ledger_key)):
        old = prev.get(name) or []
        rows, inf = _page_forward(post, body, key, t_of, end, start=_start_after(old, t_of), existing=old)
        p = pinfo.get(name) or {}
        # sticky completeness flags across incremental syncs
        inf["possible_tie_gaps"] += p.get("possible_tie_gaps", 0)
        inf["hit_page_limit"] = inf["hit_page_limit"] or bool(p.get("hit_page_limit"))
        streams[name] = (rows, inf)
    fills, fi = streams["fills"]
    funding, fu = streams["funding"]
    ledger, le = streams["ledger"]
    fi["possibly_capped"] = bool((pinfo.get("fills") or {}).get("possibly_capped")) or fi["new"] >= FILL_CAP
    # never keep counterparty/own addresses: liquidatedUser -> is_self flag
    for f in fills:
        liq = f.get("liquidation")
        if isinstance(liq, dict) and "liquidatedUser" in liq:
            liq["is_self"] = str(liq.pop("liquidatedUser")).lower() == address
    if meta is None:
        meta = post({"type": "spotMeta"}) or {}
    # perp state for the default dex plus every builder dex (HIP-3) seen in fills
    dexes = [""] + sorted({str(f.get("coin")).split(":")[0] for f in fills if ":" in str(f.get("coin"))})
    ch = {}
    for dex in dexes:
        body = {"type": "clearinghouseState", "user": address}
        if dex:
            body["dex"] = dex
        ch[dex or "main"] = post(body) or {}
    sch = post({"type": "spotClearinghouseState", "user": address}) or {}

    names = spot_names(meta)
    unresolved = sorted({f.get("coin") for f in fills
                         if str(f.get("coin", "")).startswith("@") and f.get("coin") not in names})
    info = {
        "as_of_ms": end,
        "address_fingerprint": hashlib.sha256(address.encode()).hexdigest()[:12],
        "syncs": int(pinfo.get("syncs", 0)) + 1,
        "fills": dict(fi, count=len(fills)),
        "funding": dict(fu, count=len(funding)),
        "ledger": dict(le, count=len(ledger)),
        "spot_names": len(names), "unresolved_spot_coins": len(unresolved),
        "perp_dexes": len(dexes),
        "perp_positions": sum(1 for c in ch.values() for p in c.get("assetPositions", [])
                              if float((p.get("position") or {}).get("szi") or 0) != 0),
        "spot_balances": sum(1 for b in sch.get("balances", []) if float(b.get("total") or 0) != 0),
    }
    write_json(os.path.join(d, "fills.json"), fills)
    write_json(os.path.join(d, "funding.json"), funding)
    write_json(os.path.join(d, "ledger.json"), ledger)
    write_json(os.path.join(d, "spot_names.json"), names)
    write_json(os.path.join(d, "spot_meta.json"), spot_meta_slim(meta))
    write_json(os.path.join(d, "clearinghouse.json"), ch)
    write_json(os.path.join(d, "spot_clearinghouse.json"), sch)
    write_json(os.path.join(d, "info.json"), info)
    return {"status": "done", "info": info}


def _load_dir(d):
    if not os.path.exists(os.path.join(d, "info.json")):
        return None
    return {k: read_json(os.path.join(d, k + ".json")) for k in FILES
            if os.path.exists(os.path.join(d, k + ".json"))}


def load(run_dir):
    """Every synced configured address, sorted by address:
    [{"tag", "label", "fingerprint", <data files>...}].  [] when none is synced.
    With no address list at all, a pre-0.2 flat layout is read as the only address."""
    entries = load_config(run_dir)
    if entries is None:
        h = _load_dir(_root(run_dir))
        if not h:
            return []
        fp = (h.get("info") or {}).get("address_fingerprint") or "legacy"
        return [dict(h, tag="hl", label="hl:" + fp[:6], fingerprint=fp, short=None)]
    tg, lab, out = tags(entries), labels(entries), []
    for e in entries:
        h = _load_dir(addr_dir(run_dir, e["address"]))
        if h:
            out.append(dict(h, tag=tg[e["address"]], label=lab[e["address"]],
                            fingerprint=fingerprint(e["address"]), short=short(e["address"])))
    return out


def load_one(run_dir, address):
    return _load_dir(addr_dir(run_dir, normalize(address)))
