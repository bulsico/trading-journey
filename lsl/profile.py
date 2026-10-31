"""Trader profile: the Hyperdash-style "who is this trader" block, per scope.

Pure functions over the normalized trades/fills that compute.py builds; every
threshold and formula lives here and is documented in README.md
("Trader profile").  Codes are language-neutral; labels/{en,zh}.json name them.

Definitions (a "closed trade" has |P&L| > PNL_EPS; daily net = realized P&L of
trades closed that day minus fees of fills that day, funding excluded):

- volume              sum over fills of |qty x price| (USD; options: contracts x 100 x premium)
- win/loss strip      the last STRIP_N closed trades, oldest first: "W" (P&L > 0) or "L"
- win streak, trades  longest run of consecutive winning closed trades (by close time)
- win streak, days    longest run of consecutive *trading days with a closed trade* whose
                      daily realized P&L is > 0 (days with no close neither extend nor break it)
- trade duration      avg / median of per-trade holding time (FIFO lot-weighted, where known)
- trading style       by median holding time: SCALPER < 1 h <= DAY_TRADER < 1 d <= SWING < 30 d <= POSITION
- P&L cohort          all-time net incl. funding: REKT <= -100k < UNPROFITABLE < -100 <= BREAKEVEN
                      < +100 <= PROFITABLE < +100k <= BIG_WINNER
- size cohort         median fill notional: SHRIMP < $100 <= FISH < $1k <= DOLPHIN < $10k <= WHALE
- direction bias      share of closed trades that closed a long; LONG > 55%, SHORT < 45%, else NEUTRAL
- periods (7d/30d/90d ending on the as-of date, and all-time), over every calendar day in the window
  (zero-filled): net, volume, win rate, max drawdown of the cumulative daily net (starting at 0),
  drawdown as a share of the window's peak cumulative profit (None if that peak <= 0),
  return on volume = net / volume, Sharpe = mean / pstdev x sqrt(365),
  Sortino = mean / sqrt(mean(min(0, x)^2)) x sqrt(365).
  A rolling window counts ("enough": true) when history starts on or before its first day and it
  holds at least MIN_TRADES closed trades; all-time needs one closed trade. Otherwise its metrics are None.
  Sharpe/Sortino also need MIN_ACTIVE_DAYS days with nonzero net and a nonzero deviation.
"""
import datetime as dt
import math

from .util import r2, r4

PNL_EPS = 0.001
STRIP_N = 50
MIN_TRADES = 3
MIN_ACTIVE_DAYS = 5
PERIODS = (("7d", 7), ("30d", 30), ("90d", 90), ("all", None))
STYLE_BANDS = ((3600, "SCALPER"), (86400, "DAY_TRADER"), (30 * 86400, "SWING"), (None, "POSITION"))
PNL_BANDS = ((-100000, "REKT"), (-100, "UNPROFITABLE"), (100, "BREAKEVEN"), (100000, "PROFITABLE"),
             (None, "BIG_WINNER"))
SIZE_BANDS = ((100, "SHRIMP"), (1000, "FISH"), (10000, "DOLPHIN"), (None, "WHALE"))
LONG_BIAS, SHORT_BIAS = 0.55, 0.45
ICONS = {"SCALPER": "\u26a1", "DAY_TRADER": "\u2600\ufe0f", "SWING": "\U0001f3c4", "POSITION": "\U0001f3d4\ufe0f",
         "REKT": "\U0001f480", "UNPROFITABLE": "\U0001fa79", "BREAKEVEN": "\u2696\ufe0f",
         "PROFITABLE": "\U0001f4b0", "BIG_WINNER": "\U0001f3c6",
         "SHRIMP": "\U0001f990", "FISH": "\U0001f41f", "DOLPHIN": "\U0001f42c", "WHALE": "\U0001f40b",
         "LONG": "\U0001f402", "SHORT": "\U0001f43b", "NEUTRAL": "\u2696\ufe0f"}


def band(x, bands, inclusive_low=False):
    """First label whose upper bound is above x (PNL_BANDS: x <= bound for the first band)."""
    if x is None:
        return None
    for i, (hi, lab) in enumerate(bands):
        if hi is None:
            return lab
        if x < hi or (inclusive_low and i == 0 and x <= hi):
            return lab
    return None


def style(median_hold_s):
    return band(median_hold_s, STYLE_BANDS)


def pnl_cohort(net):
    return band(net, PNL_BANDS, inclusive_low=True)


def size_cohort(median_notional):
    return band(median_notional, SIZE_BANDS)


def median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def closed(trades):
    return sorted((t for t in trades if abs(t["pnl"]) > PNL_EPS),
                  key=lambda t: (t["ts"], t["src"], t["acct"], str(t["sym"]), t["pnl"]))


def streak(flags):
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


def daily_net(trades, acts):
    by = {}
    for t in trades:
        by.setdefault(t["day"], [[], []])[0].append(t["pnl"])
    for f in acts:
        by.setdefault(f["day"], [[], []])[1].append(f["fee"])
    return {d: math.fsum(p) - math.fsum(fe) for d, (p, fe) in by.items()}


def days_between(a, b):
    a, b = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
    return [(a + dt.timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


def drawdown(values):
    """(max drawdown <= 0, peak) of the running sum of `values`, starting from 0."""
    cum = peak = 0.0
    dd = 0.0
    for v in values:
        cum += v
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return -dd, peak


def sharpe(values):
    act = sum(1 for v in values if abs(v) > PNL_EPS)
    if len(values) < 2 or act < MIN_ACTIVE_DAYS:
        return None
    m = math.fsum(values) / len(values)
    sd = math.sqrt(math.fsum((v - m) ** 2 for v in values) / len(values))
    return r2(m / sd * math.sqrt(365)) if sd > 1e-12 else None


def sortino(values):
    act = sum(1 for v in values if abs(v) > PNL_EPS)
    if len(values) < 2 or act < MIN_ACTIVE_DAYS:
        return None
    m = math.fsum(values) / len(values)
    dd = math.sqrt(math.fsum(min(0.0, v) ** 2 for v in values) / len(values))
    return r2(m / dd * math.sqrt(365)) if dd > 1e-12 else None


def period(trades, acts, first_day, end_day, n_days, dn):
    """Metrics for the last `n_days` (None = all) ending on end_day."""
    start = first_day if n_days is None else (dt.date.fromisoformat(end_day)
                                             - dt.timedelta(days=n_days - 1)).isoformat()
    ct = [t for t in closed(trades) if start <= t["day"] <= end_day]
    out = {"start": start, "end": end_day, "days": n_days or (len(days_between(start, end_day)) if start else 0),
           "closed_trades": len(ct)}
    need = MIN_TRADES if n_days else 1           # all-time: any closed trade; rolling windows: MIN_TRADES
    enough = bool(first_day) and len(ct) >= need and (n_days is None or first_day <= start)
    out["enough"] = enough
    if not enough:
        out.update({k: None for k in ("net_pnl", "volume", "wins", "win_rate", "max_drawdown",
                                      "drawdown_of_peak", "return_on_volume", "sharpe", "sortino",
                                      "active_days")})
        return out
    days = days_between(start, end_day)
    vals = [dn.get(d, 0.0) for d in days]
    vol = math.fsum(f.get("notional", 0.0) for f in acts if start <= f["day"] <= end_day)
    net = math.fsum(vals)
    mdd, peak = drawdown(vals)
    wins = sum(1 for t in ct if t["pnl"] > 0)
    out.update({"net_pnl": r2(net), "volume": r2(vol), "wins": wins, "win_rate": r4(wins / len(ct)),
                "max_drawdown": r2(mdd), "drawdown_of_peak": r4(-mdd / peak) if peak > PNL_EPS else None,
                "return_on_volume": r4(net / vol) if vol > PNL_EPS else None,
                "sharpe": sharpe(vals), "sortino": sortino(vals),
                "active_days": sum(1 for v in vals if abs(v) > PNL_EPS)})
    return out


def profile(trades, acts, funding, as_of_day):
    ct = closed(trades)
    wins = [t for t in ct if t["pnl"] > 0]
    dn = daily_net(trades, acts)
    days = sorted(set(dn))
    first = days[0] if days else None
    end = as_of_day or (days[-1] if days else None)
    close_days = sorted({t["day"] for t in ct})
    cday = {}
    for t in ct:
        cday.setdefault(t["day"], []).append(t["pnl"])
    holds = [t["hold_s"] for t in ct if t.get("hold_s") is not None]
    notionals = [f["notional"] for f in acts if f.get("notional")]
    longs = sum(1 for t in ct if t["side"] == "long")
    net = math.fsum(t["pnl"] for t in trades) - math.fsum(f["fee"] for f in acts)
    net_f = net + funding
    ls = r4(longs / len(ct)) if ct else None
    med_hold = median(holds)
    med_not = median(notionals)
    return {
        "net_pnl": r2(net), "funding": r2(funding), "all_time_pnl": r2(net_f),
        "volume": r2(math.fsum(f.get("notional", 0.0) for f in acts)),
        "fills": len(acts), "closed_trades": len(ct), "wins": len(wins), "losses": len(ct) - len(wins),
        "win_rate": r4(len(wins) / len(ct)) if ct else None,
        "strip": "".join("W" if t["pnl"] > 0 else "L" for t in ct[-STRIP_N:]),
        "win_streak_trades": streak([t["pnl"] > 0 for t in ct]),
        "win_streak_days": streak([math.fsum(cday[d]) > 0 for d in close_days]),
        "hold_avg_s": r2(math.fsum(holds) / len(holds)) if holds else None,
        "hold_median_s": r2(med_hold) if med_hold is not None else None,
        "style": style(med_hold),
        "pnl_cohort": pnl_cohort(r2(net_f)) if ct or acts else None,
        "median_fill_notional": r2(med_not) if med_not is not None else None,
        "size_cohort": size_cohort(med_not),
        "direction": {"long_trades": longs, "short_trades": len(ct) - longs, "long_share": ls,
                      "bias": None if ls is None else "LONG" if ls > LONG_BIAS else "SHORT" if ls < SHORT_BIAS
                      else "NEUTRAL"},
        "periods": {pid: period(trades, acts, first, end, n, dn) for pid, n in PERIODS} if end else {},
    }
