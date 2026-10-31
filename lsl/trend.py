"""Deterministic SMA trend tags per trade entry, and their aggregates.

Rules (the bot quotes these tags; it never infers trend on its own):
* Periods: stocks (asset kind "equity": RH stocks, RH options via their
  underlying, HIP-3 stock perps) use SMA 15/50/200; everything else (crypto,
  commodities, indices, pre-IPO perps on Hyperliquid) uses SMA 7/50/200.
* Reference price: the close of the last *closed* daily candle dated before the
  entry's UTC day (entry = close time - quantity-weighted hold).  The SMAs end on
  that same candle, so there is no look-ahead and no partial candle.
* status: ok | insufficient_history (fewer than 200 closes before the entry)
  | stale (the last close is more than STALE_DAYS old at entry) | unavailable
  (no candle series for the asset) | no_entry_time (hold unknown).
* regime: golden (SMA50 > SMA200) | death (SMA50 < SMA200) | flat.
* stack: bullish (fast > 50 > 200) | bearish (fast < 50 < 200) | mixed.
* direction on the underlying: long/short as traded; options: long call and
  short put = long, long put and short call = short.
* trend_alignment: with-trend when long with price > fast SMA and golden regime,
  or short with price < fast SMA and death regime; counter-trend when long with
  price < fast SMA and death regime, or short with price > fast SMA and golden
  regime; neutral otherwise (one signal each way, flat regime or unknown direction).
* btc_regime: for crypto entries, the BTC 50/200 regime on the same day (the
  "no new alt longs in a BTC death cross" rule in context/market-context.md).
Aggregates count closed trades (|P&L| > PNL_EPS) with their realized trade P&L.
"""
import datetime as dt
import math

from . import assets as asset_mod
from .util import r2, r4

STOCK_PERIODS = (15, 50, 200)
CRYPTO_PERIODS = (7, 50, 200)
STALE_DAYS = {"stock": 7, "crypto": 3}
MIN_TAGGED = 20             # tagged closed trades needed before the persona uses trend data
PNL_EPS = 0.001
NOTABLE_WIN_RATE_GAP = 0.10
NOTABLE_MIN_TRADES = 10
ALIGN = ("with", "counter", "neutral")
REGIMES = ("golden", "death", "flat")
BTC = "hl:BTC"


def entry_day(t):
    if t.get("hold_s") is None:
        return None
    return (t["ts"] - dt.timedelta(seconds=t["hold_s"])).astimezone(dt.timezone.utc).date().isoformat()


def direction(t):
    side = t.get("side")
    if side not in ("long", "short"):
        return None
    if t["cls"] != "option":
        return side
    typ = str(t.get("label") or "")[-1:].upper()
    if typ not in ("C", "P"):
        return None
    return side if typ == "C" else ("short" if side == "long" else "long")


def resolve(t, amap=None):
    """-> {"kind", "periods", "group", "candidates": [[sid, alt...], ...]} or None (unmapped)."""
    v = asset_mod.venue_of(t)
    c = asset_mod.canonical(v, t["sym"], t.get("canon"), amap)
    if c["ambiguous"]:
        return None
    gk, base = c["kind"], c["symbol"]
    kind = "stock" if gk == "equity" else "crypto"
    group = []
    if gk == "equity":
        group = [["rh:" + base]]
    elif gk == "crypto":
        group = [["hl:" + base, "hl:k" + base]]
    own = []
    if v == "hl_perp" or v.startswith("hl_dex:"):
        own = [["hl:" + str(t["sym"])]]
    cands = []
    for x in own + group:
        if x not in cands:
            cands.append(x)
    if not cands:
        return None
    return {"kind": kind, "periods": STOCK_PERIODS if kind == "stock" else CRYPTO_PERIODS,
            "group": c["group"], "candidates": cands}


def sma(closes, n):
    return math.fsum(closes[-n:]) / n if len(closes) >= n else None


def _sig(x):
    return float("%.10g" % x)


def _before(bars, day):
    """Index just past the last bar dated before `day` (bars sorted by date)."""
    lo, hi = 0, len(bars)
    while lo < hi:
        m = (lo + hi) // 2
        if bars[m][0] < day:
            lo = m + 1
        else:
            hi = m
    return lo


def context(bars, day, periods, kind):
    """SMA context for an entry on UTC `day` from closed daily bars [(date, close)]."""
    i = _before(bars, day)
    if i == 0:
        return {"status": "insufficient_history", "bars": 0}
    last = bars[i - 1][0]
    if (dt.date.fromisoformat(day) - dt.date.fromisoformat(last)).days > STALE_DAYS[kind]:
        return {"status": "stale", "last_close_date": last}
    if i < max(periods):
        return {"status": "insufficient_history", "bars": i}
    closes = [b[1] for b in bars[i - max(periods):i]]
    p = closes[-1]
    f, m, s = (sma(closes, n) for n in periods)
    regime = "golden" if m > s else ("death" if m < s else "flat")
    stack = "bullish" if f > m > s else ("bearish" if f < m < s else "mixed")
    keys = [str(n) for n in periods]
    return {"status": "ok", "ref_date": last, "price": _sig(p),
            "sma": {k: _sig(v) for k, v in zip(keys, (f, m, s))},
            "above": {k: p > v for k, v in zip(keys, (f, m, s))},
            "dist_pct": {k: r4(p / v - 1) if v else None for k, v in zip(keys, (f, m, s))},
            "regime": regime, "stack": stack, "_fast_above": p > f, "_fast_below": p < f}


def alignment(dirn, ctx):
    if ctx.get("status") != "ok" or dirn is None:
        return "neutral" if ctx.get("status") == "ok" else None
    up, down = ctx["_fast_above"], ctx["_fast_below"]
    g, dc = ctx["regime"] == "golden", ctx["regime"] == "death"
    if dirn == "long":
        return "with" if up and g else ("counter" if down and dc else "neutral")
    return "with" if down and dc else ("counter" if up and g else "neutral")


def tag(t, series):
    """Per-entry tag dict for one trade. series: {sid: [(date, close)]}."""
    day = entry_day(t)
    dirn = direction(t)
    v = asset_mod.venue_of(t)
    base = {"entry_date": day, "venue": v, "direction": dirn, "status": None, "alignment": None,
            "series": None, "periods": None, "group": None}
    if day is None:
        return dict(base, status="no_entry_time")
    info = resolve(t)
    if info is None:
        return dict(base, status="unavailable")
    base["periods"] = list(info["periods"])
    base["group"] = info["group"]
    best = None
    for alts in info["candidates"]:
        sid = next((a for a in alts if series.get(a)), None) or next((a for a in alts if a in series), None)
        if sid is None:
            continue
        ctx = context(series[sid], day, info["periods"], info["kind"])
        if ctx["status"] == "ok":
            best = (sid, ctx)
            break
        if best is None:
            best = (sid, ctx)
    if best is None:
        return dict(base, status="unavailable")
    sid, ctx = best
    out = dict(base, series=sid, status=ctx["status"])
    if ctx["status"] == "ok":
        out.update({k: v for k, v in ctx.items() if not k.startswith("_") and k != "status"})
        out["alignment"] = alignment(dirn, ctx)
    if info["kind"] == "crypto" and series.get(BTC):
        b = context(series[BTC], day, CRYPTO_PERIODS, "crypto")
        out["btc_regime"] = b["regime"] if b["status"] == "ok" else None
    return out


def _agg(rows):
    ct = [r for r in rows if abs(r["pnl"]) > PNL_EPS]
    w = sum(1 for r in ct if r["pnl"] > 0)
    pnl = math.fsum(r["pnl"] for r in ct)
    return {"trades": len(ct), "wins": w, "win_rate": r4(w / len(ct)) if ct else None,
            "pnl": r2(pnl), "avg_pnl": r2(pnl / len(ct)) if ct else None}


def _block(rows):
    ok = [r for r in rows if r["status"] == "ok"]
    closed_ok = [r for r in ok if abs(r["pnl"]) > PNL_EPS]
    by = {a: _agg([r for r in ok if r["alignment"] == a]) for a in ALIGN}
    n = len(closed_ok)
    return {"tagged": n,
            "with_share": r4(by["with"]["trades"] / n) if n else None,
            "counter_share": r4(by["counter"]["trades"] / n) if n else None,
            "by_alignment": by}


def build(trades, series, accts=()):
    """-> (summary block, per-entry rows). accts: [{"key", "trades"}] for the per-account split."""
    if not series:
        return {"status": "no_candles", "note": "run `lsl candles fetch` (and the `lsl rh plan` loop for stocks)"}, []
    rows, ids = [], {}
    for t in trades:
        tg = tag(t, series)
        r = dict(tg, src=t["src"], acct=t["acct"], cls=t["cls"], sym=t["sym"], label=t.get("label"),
                 side=t.get("side"), close_date=t["day"], pnl=r2(t["pnl"]))
        rows.append(r)
        ids[id(t)] = r
    ok = [r for r in rows if r["status"] == "ok"]
    closed = [r for r in rows if abs(r["pnl"]) > PNL_EPS]
    statuses = {}
    for r in closed:
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1
    out = {"status": "ok",
           "method": {"stock_periods": list(STOCK_PERIODS), "crypto_periods": list(CRYPTO_PERIODS),
                      "regime": "SMA50 vs SMA200", "reference": "last closed daily close before the entry UTC day",
                      "with_trend": "long: price > fast SMA and SMA50 > SMA200; short: price < fast SMA and SMA50 < SMA200",
                      "counter_trend": "long: price < fast SMA and SMA50 < SMA200; short: price > fast SMA and SMA50 > SMA200",
                      "min_tagged_for_persona": MIN_TAGGED},
           "coverage": {"closed_trades": len(closed), "tagged": sum(1 for r in closed if r["status"] == "ok"),
                        "share": r4(sum(1 for r in closed if r["status"] == "ok") / len(closed)) if closed else None,
                        "by_status": dict(sorted(statuses.items()))}}
    out.update(_block(rows))
    out["by_regime"] = {g: _agg([r for r in ok if r["regime"] == g]) for g in REGIMES}
    out["by_direction"] = {dn: {a: _agg([r for r in ok if r["direction"] == dn and r["alignment"] == a])
                                for a in ALIGN} for dn in ("long", "short")}
    out["by_venue"] = {v: _block([r for r in rows if r["venue"] == v]) for v in sorted({r["venue"] for r in rows})}
    out["by_account"] = {a["key"]: _block([ids[id(t)] for t in a["trades"] if id(t) in ids]) for a in accts}
    alt = [r for r in ok if r.get("btc_regime") == "death" and r["direction"] == "long"
           and str(r["group"]).startswith("crypto:") and r["group"] != "crypto:BTC"]
    out["alt_longs_in_btc_death_cross"] = _agg(alt)
    out["notable"] = notable(out)
    rows.sort(key=lambda r: (r["entry_date"] or "", r["close_date"], r["src"], r["acct"], r["cls"],
                             str(r["sym"]), str(r["label"]), r["pnl"]))
    return out, rows


def notable(tr):
    """Codes for the TLDR / report when trend discipline clearly matters."""
    w, c = tr["by_alignment"]["with"], tr["by_alignment"]["counter"]
    out = []
    if w["trades"] >= NOTABLE_MIN_TRADES and c["trades"] >= NOTABLE_MIN_TRADES:
        if (w["win_rate"] - c["win_rate"]) >= NOTABLE_WIN_RATE_GAP or (w["pnl"] > 0 > c["pnl"]):
            out.append("with_trend_better")
        elif (c["win_rate"] - w["win_rate"]) >= NOTABLE_WIN_RATE_GAP or (c["pnl"] > 0 > w["pnl"]):
            out.append("counter_trend_better")
    if tr["with_share"] is not None and tr["tagged"] >= MIN_TAGGED and tr["counter_share"] >= 0.3:
        out.append("fights_trend_often")
    a = tr.get("alt_longs_in_btc_death_cross") or {}
    if a.get("trades", 0) >= NOTABLE_MIN_TRADES and a["pnl"] < 0:
        out.append("alt_longs_in_btc_death_cross_lost")
    return out
