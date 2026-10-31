"""Hyperliquid raw-data fetcher (public info API, no auth, read-only).

Fetches, for one address passed on the command line (never stored in the repo):
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
import urllib.request

from .util import ms, write_json, read_json

HL_API = "https://api.hyperliquid.xyz/info"
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


def fetch(run_dir, address, now, post=None):
    post = post or urllib_transport
    address = address.strip().lower()
    if not (address.startswith("0x") and len(address) == 42):
        return {"error": "address must be a 0x-prefixed 40-hex-digit address"}
    end = ms(now)
    d = os.path.join(run_dir, "state", "hl")
    os.makedirs(d, exist_ok=True)

    prev = load(run_dir) or {}
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


def load(run_dir):
    d = os.path.join(run_dir, "state", "hl")
    if not os.path.exists(os.path.join(d, "info.json")):
        return None
    return {k: read_json(os.path.join(d, k + ".json")) for k in
            ("fills", "funding", "ledger", "spot_names", "spot_meta", "clearinghouse", "spot_clearinghouse",
             "info") if os.path.exists(os.path.join(d, k + ".json"))}
