"""Fun facts for the share card: counts, shares, durations, dates and names only (never dollars).

build(scopes, profiles) -> {scope_key: {period_id: facts}} for every scope with a
profile, over the same [start, end] day window as that scope's profile period.  A fact whose data
is missing for the scope/window is left out (never written as 0).  All keys are chosen so the
copy.json validator (card.allowed_numbers) treats their numbers as printable (no money-like keys).

facts (each optional):
  most_traded   {"ticker", "trades", "share"}            closed trades on the most-traded name
  long_short    {"long_share", "short_share", "trades"}  closed trades by direction
  loved / hated {"ticker", "trades", "win_rate"}         best / worst win rate, >= MIN_ASSET_TRADES
  streaks       {"streak_w", "streak_l"}                 longest run of winning / losing closes
  time_held     {"median_hold", "shortest_hold", "longest_hold", "trades"} each {"num", "unit"}
  busiest_day   {"date", "fills"}
  weekday       {"weekday" (0=Mon), "fills", "share"}
  venues        {"rows": [{"venue", "share"}...]}         both Robinhood and Hyperliquid present
  top_account   {"account", "share", "accounts"}         several accounts on one venue
  classes       {"rows": [{"cls": stocks|crypto|options, "share"}...]}  two or more classes
  liquidated    {"trades"}                               scope has Hyperliquid perp closes
"""
import datetime as dt
from collections import defaultdict

from .util import r4

PNL_EPS = 0.001
MIN_ASSET_TRADES = 3
CLASS_OF = {"equity": "stocks", "option": "options", "crypto": "crypto", "spot": "crypto", "perp": "crypto"}
CLASS_ORDER = ("stocks", "crypto", "options")


def ticker(t):
    s = str(t.get("sym") or "?")
    s = s.split("/")[0] if t.get("cls") == "spot" else s
    return s.split(":")[-1].replace("$", "") or "?"     # HIP-3 markets: "dex:ENA" -> "ENA"


def duration(sec):
    """Seconds -> {"num", "unit"} in display precision (m: whole minutes, h / d: one decimal)."""
    if sec < 3600:
        return {"num": int(round(sec / 60.0)), "unit": "m"}
    if sec < 48 * 3600:
        return {"num": _whole(round(sec / 3600.0, 1)), "unit": "h"}
    d = sec / 86400.0
    return {"num": int(round(d)) if d >= 10 else _whole(round(d, 1)), "unit": "d"}


def _whole(x):
    return int(x) if x == int(x) else x


def _streaks(flags):
    best_w = best_l = cur = 0
    prev = None
    for f in flags:
        cur = cur + 1 if f == prev else 1
        prev = f
        if f:
            best_w = max(best_w, cur)
        else:
            best_l = max(best_l, cur)
    return best_w, best_l


def _account_key(t, multi_hl):
    if t["src"] == "rh":
        return "rh." + str(t["acct"])
    return "hl." + str(t["acct"]) if multi_hl else "hl"


def facts(trades, acts, start, end, multi_hl=False):
    ct = [t for t in trades if abs(t["pnl"]) > PNL_EPS and start <= t["day"] <= end]
    fl = [f for f in acts if start <= f["day"] <= end]
    ct.sort(key=lambda t: (t["ts"], t["day"], t["src"], str(t["acct"]), t["cls"], str(t["sym"]), t["pnl"]))
    out = {}
    n = len(ct)
    if n:
        by = defaultdict(list)
        for t in ct:
            by[ticker(t)].append(t["pnl"] > 0)
        top = sorted(by.items(), key=lambda kv: (-len(kv[1]), kv[0]))[0]
        out["most_traded"] = {"ticker": top[0], "trades": len(top[1]), "share": r4(len(top[1]) / n)}
        longs = sum(1 for t in ct if t.get("side") == "long")
        out["long_short"] = {"long_share": r4(longs / n), "short_share": r4((n - longs) / n), "trades": n}
        q = [(k, len(v), r4(sum(v) / len(v))) for k, v in by.items() if len(v) >= MIN_ASSET_TRADES]
        if len(q) >= 2:
            lo = sorted(q, key=lambda x: (-x[2], -x[1], x[0]))[0]
            ha = sorted(q, key=lambda x: (x[2], -x[1], x[0]))[0]
            if lo[2] > ha[2]:
                out["loved"] = {"ticker": lo[0], "trades": lo[1], "win_rate": lo[2]}
                out["hated"] = {"ticker": ha[0], "trades": ha[1], "win_rate": ha[2]}
        w, l = _streaks([t["pnl"] > 0 for t in ct])
        out["streaks"] = {"streak_w": w, "streak_l": l}
        hs = sorted(t["hold_s"] for t in ct if t.get("hold_s") is not None)
        if hs:
            m = len(hs)
            med = hs[m // 2] if m % 2 else (hs[m // 2 - 1] + hs[m // 2]) / 2.0
            out["time_held"] = {"median_hold": duration(med), "shortest_hold": duration(hs[0]),
                            "longest_hold": duration(hs[-1]), "trades": m}
        srcs = {t["src"] for t in ct}
        if srcs == {"rh", "hl"}:
            rows = [{"venue": v, "share": r4(sum(1 for t in ct if t["src"] == v) / n)} for v in ("hl", "rh")]
            out["venues"] = {"rows": sorted(rows, key=lambda r: (-r["share"], r["venue"]))}
        else:
            ac = defaultdict(int)
            for t in ct:
                ac[_account_key(t, multi_hl)] += 1
            if len(ac) >= 2:
                k, c = sorted(ac.items(), key=lambda kv: (-kv[1], kv[0]))[0]
                out["top_account"] = {"account": k, "share": r4(c / n), "accounts": len(ac)}
        cc = defaultdict(int)
        for t in ct:
            cc[CLASS_OF.get(t["cls"], "crypto")] += 1
        if len(cc) >= 2:
            rows = [{"cls": c, "share": r4(cc[c] / n)} for c in CLASS_ORDER if cc.get(c)]
            out["classes"] = {"rows": sorted(rows, key=lambda r: (-r["share"], CLASS_ORDER.index(r["cls"])))}
        if any(t["src"] == "hl" and t["cls"] == "perp" for t in ct):
            out["liquidated"] = {"trades": sum(1 for t in ct if t.get("liquidation"))}
    if fl:
        per = defaultdict(int)
        for f in fl:
            per[f["day"]] += 1
        d, c = sorted(per.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        out["busiest_day"] = {"date": d, "fills": c}
        wd = [0] * 7
        for f in fl:
            wd[dt.date.fromisoformat(f["day"]).weekday()] += 1
        i = sorted(range(7), key=lambda j: (-wd[j], j))[0]
        if len(per) >= 3:
            out["weekday"] = {"weekday": i, "fills": wd[i], "share": r4(wd[i] / len(fl))}
    return out


def build(scopes, profiles):
    """scopes: {key: (trades, acts, funding)} including "all"; profiles: compute.profiles output."""
    out = {}
    for key in sorted(scopes):
        tr, ac = scopes[key][0], scopes[key][1]
        multi_hl = any(str(x["acct"]) != "hl" for x in list(tr) + list(ac) if x["src"] == "hl")
        per = {}
        for pid, p in sorted(((profiles.get(key) or {}).get("periods") or {}).items()):
            if p.get("enough") and p.get("start") and p.get("end"):
                per[pid] = facts(tr, ac, p["start"], p["end"], multi_hl)
        out[key] = per
    return out
