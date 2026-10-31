"""Asset identity: the Hyperliquid spot whitelist and cross-venue grouping.

Both are data-driven (lsl/data/*.json) plus the fixed rules documented in
lsl/data/asset_map.json.  Pure functions; same input -> same output.
"""
import json
import math
import os
import re
from collections import defaultdict

from .util import r2, r4

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
PNL_EPS = 0.001
KINDS = ("crypto", "equity", "commodity", "index", "private")
MULT_RULES = (re.compile(r"^k([A-Z0-9]+)$"), re.compile(r"^1000([A-Z0-9]+)$"))
ADJUSTED_CHAIN = re.compile(r"^[A-Z]+[0-9]+$")
VENUE_ORDER = ("rh_equity", "rh_option", "rh_crypto", "hl_spot", "hl_perp")


def _load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


def load_whitelist():
    return _load("hl_spot_whitelist.json")


def load_map():
    return _load("asset_map.json")


# ------------------------------------------------------------------ HL spot whitelist
class SpotWhitelist:
    """Resolve an HL spot fill coin ('@N' or a named pair) to its base token via
    spotMeta indices, and decide whether it counts."""

    def __init__(self, spot_meta, wl):
        meta = spot_meta or {}
        self.tok_id = {t.get("index"): t.get("tokenId") for t in meta.get("tokens") or []}
        self.pairs = {}
        for u in meta.get("universe") or []:
            tk = u.get("tokens") or []
            if u.get("index") is None or len(tk) < 2:
                continue
            self.pairs["@%d" % u["index"]] = (tk[0], tk[1])
            if u.get("name"):
                self.pairs[u["name"]] = (tk[0], tk[1])
        self.base = {t["index"]: t for t in wl.get("tokens") or []}
        self.quote = {t["index"]: t for t in wl.get("quote_tokens") or []}
        self.have_meta = bool(self.pairs)
        mism = set()
        for idx, t in list(self.base.items()) + list(self.quote.items()):
            live = self.tok_id.get(idx)
            if live is not None and live != t.get("tokenId"):
                mism.add(idx)
        self.id_mismatch = sorted(mism)

    def resolve(self, coin):
        """-> (underlying or None, reason)."""
        pr = self.pairs.get(coin)
        if pr is None:
            return None, "unresolved_pair"
        b, q = pr
        if b not in self.base:
            return None, "not_whitelisted"
        if q not in self.quote:
            return None, "quote_not_whitelisted"
        if b in self.id_mismatch or q in self.id_mismatch:
            return None, "token_id_mismatch"
        return self.base[b]["underlying"], "ok"

    def token_ok(self, idx):
        return idx in self.base and idx not in self.id_mismatch

    def underlying_of_token(self, idx):
        return self.base[idx]["underlying"] if self.token_ok(idx) else None


# ------------------------------------------------------------------ canonical symbols
def venue_of(t):
    if t["src"] == "rh":
        return "rh_" + t["cls"]
    if t["cls"] == "spot":
        return "hl_spot"
    s = str(t["sym"])
    return "hl_dex:" + s.split(":", 1)[0] if ":" in s else "hl_perp"


def _strip_mult(sym):
    for rx in MULT_RULES:
        m = rx.match(sym)
        if m:
            return m.group(1), 1000
    return sym, 1


def canonical(venue, sym, canon=None, amap=None):
    """-> {"group", "symbol", "kind", "multiplier", "ambiguous", "reason"}."""
    amap = amap or load_map()
    sym = str(sym)
    mult, kind, base, why = 1, None, sym, None
    if sym in ("?", "") or sym.startswith("pair:"):
        why = "unresolved_symbol"
    elif venue in ("rh_equity", "rh_option"):
        kind = "equity"
        if venue == "rh_option" and ADJUSTED_CHAIN.match(sym):
            why = "adjusted_option_chain"
    elif venue == "rh_crypto":
        kind = "crypto"
    elif venue == "hl_spot":
        kind, base = "crypto", canon
        if not canon:
            why = "spot_not_whitelisted"
    elif venue == "hl_perp":
        kind = "crypto"
        base, mult = _strip_mult(sym)
    elif venue.startswith("hl_dex:"):
        dex = venue.split(":", 1)[1]
        base, mult = _strip_mult(sym.split(":", 1)[1] if ":" in sym else sym)
        dk = (amap.get("dex_kinds") or {}).get(dex)
        if dk is None:
            why = "unknown_perp_dex"
        elif dk == "tradfi":
            kind = (amap.get("tradfi_kinds") or {}).get(base, "equity")
        else:
            kind = dk
    else:
        why = "unknown_venue"
    if why:
        return {"group": "unmapped:%s:%s" % (venue, sym), "symbol": sym, "kind": None,
                "multiplier": mult, "ambiguous": True, "reason": why}
    key = "%s:%s" % (kind, base)
    key = (amap.get("aliases") or {}).get(key, key)
    kind, base = key.split(":", 1)
    return {"group": key, "symbol": base, "kind": kind, "multiplier": mult, "ambiguous": False,
            "reason": None}


# ------------------------------------------------------------------ grouped rollups
def _stats(ts, fl):
    closed = [t["pnl"] for t in ts if abs(t["pnl"]) > PNL_EPS]
    w = [p for p in closed if p > 0]
    lo = [p for p in closed if p <= 0]
    realized, fees = math.fsum(t["pnl"] for t in ts), math.fsum(f["fee"] for f in fl)
    return {"fills": len(fl), "closed_trades": len(closed), "wins": len(w), "losses": len(lo),
            "win_rate": r4(len(w) / len(closed)) if closed else 0.0,
            "biggest_win": r2(max(w)) if w else 0.0, "biggest_loss": r2(min(lo)) if lo else 0.0,
            "realized_pnl": r2(realized), "fees": r2(fees), "net_pnl": r2(realized - fees),
            "approx_trades": sum(1 for t in ts if t.get("approx"))}


def _venue_rank(v):
    return (VENUE_ORDER.index(v) if v in VENUE_ORDER else len(VENUE_ORDER), v)


def groups(trades, acts, amap=None):
    """Per canonical asset: totals + per-venue breakdown.  Returns (groups, quality)."""
    amap = amap or load_map()
    T, A, C = defaultdict(list), defaultdict(list), {}
    for kind, rows in (("t", trades), ("a", acts)):
        for x in rows:
            v = venue_of(x)
            k = (v, str(x["sym"]))
            if k not in C:
                C[k] = canonical(v, x["sym"], x.get("canon"), amap)
            (T if kind == "t" else A)[k].append(x)
    G = defaultdict(list)
    for k in C:
        G[C[k]["group"]].append(k)
    out = []
    for g in sorted(G):
        keys = sorted(G[g], key=lambda k: (_venue_rank(k[0]), k[1]))
        c0 = C[keys[0]]
        venues = []
        for k in keys:
            venues.append(dict(_stats(T.get(k, []), A.get(k, [])), venue=k[0], raw_symbol=k[1],
                               multiplier=C[k]["multiplier"]))
        ts = [t for k in keys for t in T.get(k, [])]
        fl = [f for k in keys for f in A.get(k, [])]
        out.append(dict(_stats(ts, fl), group=g, symbol=c0["symbol"], kind=c0["kind"],
                        ambiguous=c0["ambiguous"], venue_count=len({k[0] for k in keys}),
                        venues=venues))
    out.sort(key=lambda r: (r["net_pnl"], r["group"]))
    amb = sorted(({"venue": k[0], "raw_symbol": k[1], "reason": C[k]["reason"]}
                  for k in C if C[k]["ambiguous"]), key=lambda r: (r["venue"], r["raw_symbol"]))
    by_sym = defaultdict(set)
    for r in out:
        if not r["ambiguous"]:
            by_sym[r["symbol"]].add(r["group"])
    q = {"venue_symbols": len(C), "groups": len(out),
         "multi_venue_groups": sum(1 for r in out if r["venue_count"] > 1),
         "ambiguous": amb, "ambiguous_count": len(amb),
         "symbol_kind_collisions": sorted(sorted(v) for v in by_sym.values() if len(v) > 1)}
    return out, q
