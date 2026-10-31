"""Per-account breakdown inside a combined report (summary.json "accounts").

One row per account that has data: each Robinhood account (masked label, e.g.
"Robinhood Roth IRA ...1234") and each Hyperliquid address (its nickname or 0xabcd...1234).

Row fields (all from that account's own trades/fills; see README "By account"):
  net_pnl               realized - fees (Hyperliquid funding excluded), dollars
  funding               Hyperliquid funding (0 for Robinhood)
  net_pnl_incl_funding  net_pnl + funding
  return_on_volume      net_pnl_incl_funding / volume
  volume, fills, fees, closed_trades, wins, win_rate
  max_drawdown          all-time max drawdown of the account's daily net (profile, excl. funding)
  best_asset/worst_asset  asset group with the highest positive / lowest negative net (or null)
  style                 profile trading style code (median hold)
  share_of_total        net_pnl_incl_funding / combined net_pnl_incl_funding (signed; rows add
                        up to 100%, so with mixed signs a row can be negative or above 100%)
  period                first and last day with a fill or close

Reconciliation: dollar fields are rounded to cents by largest remainder so the rows add up
to the combined figure to the cent; counts add up exactly.  Deliberate differences: max
drawdown, win rate and return on volume are not additive (combined values are recomputed
on the combined history), and funding per row is net_pnl_incl_funding - net_pnl.

Ordering: |net_pnl_incl_funding| descending, then label, then key.

Cross-account insights (rules):
  opposing   an asset group traded in two or more accounts where one account's closed trades
             are >= 55% long and another's are <= 45% long, each with >= MIN_DIR_TRADES closed
             trades in that group.  Options are excluded (their side is not the underlying's
             direction).  Sorted by total |net| on the group, then symbol; at most TOP_N.
  open_opposing  an asset group currently held long in one account and short in another
             (open positions; options excluded).
  offsets    when some accounts made money and others lost (by net_pnl_incl_funding):
             gross gain = sum of winners, gross loss = sum of losers,
             given_back = |gross loss| / gross gain, and for each loser its own share of the
             winners' gains (|loss| / gross gain).
"""
import math
from collections import defaultdict

from . import assets as asset_mod
from .util import r2, r4

PNL_EPS = 0.001
MIN_DIR_TRADES = 3
LONG_MIN, SHORT_MAX = 0.55, 0.45
TOP_N = 5


def allocate(raws, total):
    """Round each raw dollar value to cents so the results add up exactly to `total`
    (largest remainder; ties go to the earlier row)."""
    if not raws:
        return []
    target = int(round(total * 100))
    cents = [v * 100.0 for v in raws]
    out = [math.floor(c) for c in cents]
    rem = target - sum(out)
    if 0 <= rem <= len(raws):
        for i in sorted(range(len(raws)), key=lambda i: (-(cents[i] - out[i]), i))[:rem]:
            out[i] += 1
    else:  # raws do not add up to total (should not happen); keep the residue visible on the largest row
        out = [int(round(c)) for c in cents]
        j = min(range(len(raws)), key=lambda i: (-abs(raws[i]), i))
        out[j] += target - sum(out)
    return [r2(c / 100.0) for c in out]


def _group_of(x, cache, amap):
    v = asset_mod.venue_of(x)
    k = (v, str(x["sym"]))
    if k not in cache:
        cache[k] = asset_mod.canonical(v, x["sym"], x.get("canon"), amap)
    return cache[k]


def _asset_nets(trades, acts, cache, amap):
    net = defaultdict(float)
    sym = {}
    for t in trades:
        c = _group_of(t, cache, amap)
        net[c["group"]] += t["pnl"]
        sym[c["group"]] = c["symbol"]
    for f in acts:
        c = _group_of(f, cache, amap)
        net[c["group"]] -= f["fee"]
        sym[c["group"]] = c["symbol"]
    return {g: (r2(v), sym[g]) for g, v in net.items()}


def build(accts, overall, profiles, open_pos=None):
    """accts: [{"key", "id", "slug", "venue", "label", "labels", "trades", "acts", "funding"}].
    overall: the combined stats(); profiles: summary["profile"] (must hold every account key)."""
    amap = asset_mod.load_map()
    cache = {}
    raw = []
    for a in accts:
        t, f = a["trades"], a["acts"]
        realized = math.fsum(x["pnl"] for x in t)
        fees = math.fsum(x["fee"] for x in f)
        days = sorted({x["day"] for x in t} | {x["day"] for x in f})
        raw.append({"net": realized - fees, "incl": realized - fees + a["funding"], "fees": fees,
                    "volume": math.fsum(x.get("notional", 0.0) for x in f), "days": days})
    vol_total = (profiles.get("all") or {}).get("volume")
    if vol_total is None:
        vol_total = r2(math.fsum(r["volume"] for r in raw))
    alloc = {k: allocate([r[k] for r in raw], tot) for k, tot in (
        ("net", overall["net_pnl"]), ("incl", overall["net_pnl_incl_funding"]),
        ("fees", overall["fees"]), ("volume", vol_total))}
    total_incl = overall["net_pnl_incl_funding"]
    rows = []
    for i, a in enumerate(accts):
        t, f = a["trades"], a["acts"]
        ct = [x for x in t if abs(x["pnl"]) > PNL_EPS]
        wins = sum(1 for x in ct if x["pnl"] > 0)
        pr = profiles.get(a["key"]) or {}
        pall = (pr.get("periods") or {}).get("all") or {}
        nets = _asset_nets(t, f, cache, amap)
        pos = sorted(((v, g) for g, v in nets.items() if v[0] > 0), key=lambda z: (-z[0][0], z[1]))
        neg = sorted(((v, g) for g, v in nets.items() if v[0] < 0), key=lambda z: (z[0][0], z[1]))
        net, incl, vol = alloc["net"][i], alloc["incl"][i], alloc["volume"][i]
        rows.append({
            "key": a["key"], "id": a["id"], "slug": a["slug"], "venue": a["venue"],
            "label": a["label"], "labels": a["labels"],
            "net_pnl": net, "funding": r2(incl - net), "net_pnl_incl_funding": incl,
            "fees": alloc["fees"][i], "volume": vol,
            "return_on_volume": r4(incl / vol) if vol else None,
            "fills": len(f), "closed_trades": len(ct), "wins": wins,
            "win_rate": r4(wins / len(ct)) if ct else None,
            "max_drawdown": pall.get("max_drawdown"),
            "best_asset": {"group": pos[0][1], "symbol": pos[0][0][1], "net_pnl": pos[0][0][0]} if pos else None,
            "worst_asset": {"group": neg[0][1], "symbol": neg[0][0][1], "net_pnl": neg[0][0][0]} if neg else None,
            "style": pr.get("style"),
            "share_of_total": r4(incl / total_incl) if abs(total_incl) >= 0.005 else None,
            "period": {"from": raw[i]["days"][0] if raw[i]["days"] else None,
                       "to": raw[i]["days"][-1] if raw[i]["days"] else None},
        })
    rows.sort(key=lambda r: (-abs(r["net_pnl_incl_funding"]), r["label"], r["key"]))
    totals = {"net_pnl": overall["net_pnl"], "net_pnl_incl_funding": overall["net_pnl_incl_funding"],
              "funding": overall["funding"], "fees": overall["fees"], "volume": vol_total,
              "fills": overall["fills"], "closed_trades": overall["closed_trades"], "wins": overall["wins"],
              "win_rate": overall["win_rate"]}

    def cents(xs):
        return int(round(math.fsum(x * 100 for x in xs)))

    rec = {}
    for k in ("net_pnl", "net_pnl_incl_funding", "fees", "volume"):
        rec[k] = cents(r[k] for r in rows) == int(round(totals[k] * 100))
    for k in ("fills", "closed_trades", "wins"):
        rec[k] = sum(r[k] for r in rows) == totals[k]
    rec["funding_cents_diff"] = cents(r["funding"] for r in rows) - int(round(totals["funding"] * 100))
    return {"count": len(rows), "rows": rows, "totals": totals,
            "reconciliation": dict(rec, all_match=all(v for k, v in rec.items() if k != "funding_cents_diff")),
            "insights": insights(accts, rows, cache, amap, open_pos)}


def insights(accts, rows, cache=None, amap=None, open_pos=None):
    amap = amap or asset_mod.load_map()
    cache = {} if cache is None else cache
    lab = {r["key"]: r["label"] for r in rows}
    # ---- opposing direction on closed trades
    per = defaultdict(lambda: defaultdict(lambda: [0, 0, 0.0]))  # group -> key -> [n, longs, net]
    sym = {}
    traded = defaultdict(set)
    for a in accts:
        for t in a["trades"]:
            c = _group_of(t, cache, amap)
            if c["ambiguous"]:
                continue
            traded[c["group"]].add(a["key"])
            if t["cls"] == "option" or abs(t["pnl"]) <= PNL_EPS:
                continue
            e = per[c["group"]][a["key"]]
            e[0] += 1
            e[1] += 1 if t.get("side") == "long" else 0
            e[2] += t["pnl"]
            sym[c["group"]] = c["symbol"]
    opp = []
    for g, by in per.items():
        side = {"long": [], "short": []}
        for key, (n, lo, net) in by.items():
            if n < MIN_DIR_TRADES:
                continue
            ls = lo / n
            d = "long" if ls >= LONG_MIN else "short" if ls <= SHORT_MAX else None
            if d:
                side[d].append({"key": key, "label": lab.get(key, key), "closed_trades": n,
                                "long_share": r4(ls), "net_pnl": r2(net)})
        if side["long"] and side["short"]:
            for s in side.values():
                s.sort(key=lambda x: (-x["closed_trades"], x["label"]))
            tot = math.fsum(abs(x["net_pnl"]) for s in side.values() for x in s)
            opp.append({"group": g, "symbol": sym[g], "long": side["long"], "short": side["short"],
                        "abs_net": r2(tot)})
    opp.sort(key=lambda o: (-o["abs_net"], o["symbol"], o["group"]))
    # ---- opposing open positions
    held = defaultdict(lambda: {"long": set(), "short": set()})
    hsym = {}
    multi_hl = sum(1 for a in accts if a["venue"] == "hl") > 1
    for p in (open_pos or {}).get("rh", []):
        if p.get("cls") == "option" or p.get("side") not in ("long", "short"):
            continue
        c = _group_of({"src": "rh", "cls": p["cls"], "sym": p["sym"]}, cache, amap)
        if not c["ambiguous"]:
            held[c["group"]][p["side"]].add("rh." + p["acct"])
            hsym[c["group"]] = c["symbol"]
    for p in (open_pos or {}).get("hl_perp", []):
        c = _group_of({"src": "hl", "cls": "perp", "sym": p["coin"]}, cache, amap)
        if not c["ambiguous"] and p.get("side") in ("long", "short"):
            held[c["group"]][p["side"]].add(("hl." + p["acct"]) if multi_hl else "hl")
            hsym[c["group"]] = c["symbol"]
    open_opp = [{"group": g, "symbol": hsym[g],
                 "long": sorted(({"key": k, "label": lab.get(k, k)} for k in v["long"]), key=lambda x: (x["label"], x["key"])),
                 "short": sorted(({"key": k, "label": lab.get(k, k)} for k in v["short"]), key=lambda x: (x["label"], x["key"]))}
                for g, v in sorted(held.items()) if v["long"] and v["short"]]
    # ---- offsets
    win = [r for r in rows if r["net_pnl_incl_funding"] > 0]
    lose = [r for r in rows if r["net_pnl_incl_funding"] < 0]
    offsets = None
    if win and lose:
        gain = math.fsum(r["net_pnl_incl_funding"] for r in win)
        loss = math.fsum(r["net_pnl_incl_funding"] for r in lose)
        offsets = {"gross_gain": r2(gain), "gross_loss": r2(loss), "given_back": r4(-loss / gain),
                   "winners": [{"key": r["key"], "label": r["label"], "net_pnl": r["net_pnl_incl_funding"]}
                               for r in win],
                   "losers": [{"key": r["key"], "label": r["label"], "net_pnl": r["net_pnl_incl_funding"],
                               "share_of_gains": r4(-r["net_pnl_incl_funding"] / gain)} for r in lose]}
    return {"opposing": opp[:TOP_N], "opposing_count": len(opp), "open_opposing": open_opp[:TOP_N],
            "shared_assets": sum(1 for v in traded.values() if len(v) > 1), "offsets": offsets}
