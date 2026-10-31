"""Page data: every figure, display string, chart series and risk flag a
dashboard Page shows, derived deterministically from the four compute outputs.

    lsl page --out DIR [--lang en|zh]     -> DIR/page.json

`lsl compute` also writes page.json. The bot pastes page.json into the Page
verbatim and writes only prose; it never formats or derives a number itself.
Input is only summary/series/open_positions/data_quality.json, so this step
can be re-run after the raw fetch state has been deleted.
"""
import datetime as dt
import math
import os
from collections import defaultdict

from . import persona
from .util import r2, r4, read_json, write_json

SCHEMA = 1
TOP_N = 10
RECENT_MONTHS = 24
CONCENTRATION_FLAG = 0.2      # one RH equity >= 20% of RH equity cost basis
EXPIRY_SOON_DAYS = 30
LIQ_NEAR_PCT = 15

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
HOLD_LABELS = {"lt_1h": "< 1 hour", "1h_1d": "1 hour - 1 day", "1d_7d": "1 - 7 days",
               "7d_30d": "1 - 4 weeks", "30d_180d": "1 - 6 months", "gt_180d": "> 6 months"}
CLS_LABELS = {"equity": "Stocks", "option": "Options", "crypto": "Crypto", "perp": "Perps", "spot": "Spot"}
SRC_LABELS = {"rh": "Robinhood", "hl": "Hyperliquid"}
KIND_LABELS = {"individual": "Individual", "ira_traditional": "Traditional IRA", "ira_roth": "Roth IRA"}
GAIN_LABELS = {"fifo": "FIFO estimate (before RH P&L coverage)", "rh": "Robinhood realized gain",
               "hl": "Hyperliquid closedPnl"}
SCORE_KEYS = {"trend_master": "TREND_MASTER", "overtrade": "OVERTRADE", "knife_catcher": "KNIFE_CATCHER",
              "scatter_gun": "SCATTER_GUN", "panic_trader": "PANIC_TRADER", "diamond_hands": "DIAMOND_HANDS"}
CHECK_LABELS = {
    "fetch_complete": "Every order window fetched",
    "no_missing_closes_vs_rh": "No closes missing vs Robinhood monthly P&L",
    "realized_gain_match_rh": "Monthly realized gain matches Robinhood",
    "history_rows_matched": "Robinhood trade-history rows matched to fills",
    "fifo_matches_positions": "FIFO open lots match current positions",
    "fills_complete": "All fills fetched (not capped)",
    "spot_names_resolved": "All spot token names resolved",
    "spot_whitelist_ids_match": "HL spot whitelist token ids match spotMeta",
    "all_grouped": "Every traded symbol mapped to a cross-venue asset",
}
KIND_GROUP_LABELS = {"crypto": "Crypto", "equity": "Stock", "commodity": "Commodity", "index": "Index",
                     "private": "Pre-IPO", None: "Unmapped"}
VENUE_LABELS = {"rh_equity": "Robinhood Stocks", "rh_option": "Robinhood Options", "rh_crypto": "Robinhood Crypto",
                "hl_spot": "Hyperliquid Spot", "hl_perp": "Hyperliquid Perps"}


def venue_label(v):
    if v.startswith("hl_dex:"):
        return "Hyperliquid %s perps (HIP-3)" % v.split(":", 1)[1]
    return VENUE_LABELS.get(v, v)
FLAG_LABELS = {
    "losing_track_record": "losing track record on this asset",
    "no_track_record": "no closed trades on this asset yet",
    "zero_cost_basis": "zero cost basis (corporate action / transfer?)",
    "concentrated": "large share of RH stock cost basis",
    "expiring_soon": "option expires within 30 days",
    "short_premium": "short / credit option",
    "near_zero_cost": "near-zero entry cost (airdrop or dust)",
    "no_mark_price": "no mark price fetched, value unknown",
    "near_liquidation": "within 15% of liquidation",
}


# ---------------------------------------------------------------- formatting
def money(x, plus=False):
    if x is None:
        return "n/a"
    x = r2(x)
    s = "{:,.2f}".format(abs(x))
    return ("-$" if x < 0 else ("+$" if plus and x > 0 else "$")) + s


def money_k(x, plus=False):
    if x is None:
        return "n/a"
    a = abs(x)
    if a >= 1e6:
        s = "{:,.2f}M".format(a / 1e6)
    elif a >= 1e3:
        s = "{:,.1f}K".format(a / 1e3)
    else:
        s = "{:,.0f}".format(a)
    return ("-$" if x < 0 and s.strip("0.,KM") else ("+$" if plus and x > 0 else "$")) + s


def pct(x, dp=1):
    return "n/a" if x is None else ("{:.%df}%%" % dp).format(x * 100)


def num(x, dp=0):
    return "n/a" if x is None else ("{:,.%df}" % dp).format(x)


def qty(x):
    return "{:,.4f}".format(x).rstrip("0").rstrip(".")


def plural(n, word):
    return "%s %s%s" % (num(n), word, "" if n == 1 else "s")


def tone(x):
    return None if not x else ("success" if x > 0 else "danger")


def k(x):
    return r2((x or 0.0) / 1000.0)


def nice_up(v):
    """Smallest round step x 10^n >= v (v > 0)."""
    if v <= 0:
        return 0.0
    e = 10 ** math.floor(math.log10(v))
    for m in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if m * e >= v - 1e-9:
            return r2(m * e)
    return r2(10 * e)


def axis(values):
    hi = max(values + [0.0])
    lo = min(values + [0.0])
    return {"ceiling": nice_up(hi) if hi > 0 else 0.0,
            "floor": -nice_up(-lo) if lo < 0 else 0.0}


def month_seq(a, b):
    y, m = int(a[:4]), int(a[5:7])
    out = []
    while "%04d-%02d" % (y, m) <= b:
        out.append("%04d-%02d" % (y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def mlabel(m):
    return "%s %s" % (MONTHS[int(m[5:7]) - 1], m[2:4])


# ---------------------------------------------------------------- sections
def _scope_row(key, name, s, approx_trades=None):
    gs = s.get("gain_sources") or {}
    tot = sum(v["trades"] for v in gs.values())
    fifo = (gs.get("fifo") or {}).get("trades", 0)
    return {"scope": key, "name": name,
            "net_pnl_display": money(s["net_pnl"], True), "tone": tone(s["net_pnl"]),
            "net_incl_funding_display": money(s["net_pnl_incl_funding"], True),
            "closed_trades": s["closed_trades"], "fills": s["fills"],
            "win_rate_display": pct(s["win_rate"]), "payoff_display": num(s["profit_factor_ratio"], 2),
            "fees_display": money(s["fees"]), "period_display": "%s to %s" % (s["period"]["from"], s["period"]["to"]),
            "fifo_trades": fifo, "fifo_share_display": pct(r4(fifo / tot) if tot else 0.0)}


def _headline(sm):
    o, p = sm["overall"], sm["persona"]
    return [
        {"label": "Net realized P&L", "value": money_k(o["net_pnl"], True), "exact": money(o["net_pnl"], True),
         "detail": "after fees, before HL funding", "tone": tone(o["net_pnl"])},
        {"label": "Incl. HL funding", "value": money_k(o["net_pnl_incl_funding"], True),
         "exact": money(o["net_pnl_incl_funding"], True),
         "detail": "funding " + money(o["funding"], True), "tone": tone(o["net_pnl_incl_funding"])},
        {"label": "Win rate", "value": pct(o["win_rate"]), "detail": "%s wins / %s losses" % (num(o["wins"]), num(o["losses"]))},
        {"label": "Avg win / avg loss", "value": num(o["profit_factor_ratio"], 2),
         "detail": "%s vs %s" % (money(o["avg_win"]), money(o["avg_loss"])),
         "tone": "danger" if (o["profit_factor_ratio"] or 0) < 1 else "success"},
        {"label": "Closed trades", "value": num(o["closed_trades"]), "detail": "%s fills" % num(o["fills"])},
        {"label": "Assets traded", "value": num(o["unique_assets"]), "detail": "%s days of history" % num(o["period"]["days"])},
        {"label": "Fees paid", "value": money_k(o["fees"]), "exact": money(o["fees"])},
        {"label": "Liquidation fills", "value": num(o["liquidations"]),
         "detail": "%s trades ended in liquidation" % num(o["liquidated_trades"]),
         "tone": "danger" if o["liquidations"] else None},
        {"label": "Median hold", "value": "%s days" % num(o["holding"]["median_days"], 1),
         "detail": "average %s days" % num(o["holding"]["avg_days"], 1)},
        {"label": "Busiest day", "value": "%s fills" % num(o["peak_fills_in_one_day"]), "detail": o["peak_fills_date"]},
        {"label": "Grade", "value": p["grade"], "detail": None},
    ]


def _persona(sm, lang):
    p = sm["persona"]
    L = persona.labels(lang)
    ratings = [{"key": key, "label": L["ratings"][key], "value": p["ratings"][key],
                "bar": "\u2588" * p["ratings"][key] + "\u2591" * (10 - p["ratings"][key]),
                "display": "%d/10" % p["ratings"][key]}
               for key in ("patience", "fomo_resistance", "stop_loss_discipline", "trend_awareness", "resilience")]
    scores = sorted(({"key": key, "label": L["archetypes"][SCORE_KEYS[key]], "value": v, "display": "%d" % v}
                     for key, v in p["scores"].items()), key=lambda r: (-r["value"], r["key"]))
    return {"archetype": p["archetype"], "archetype_label": L["archetypes"][p["archetype"]],
            "modifier_labels": [L["modifiers"][m] for m in p["modifiers"]],
            "grade": p["grade"], "grade_label": L["grades"][p["grade"]],
            "ratings": ratings, "scores": scores}


def _journey(se):
    mon = se["monthly"]
    scopes = [s for s in ("all", "rh", "hl") if mon.get(s)]
    first = min(r["month"] for s in scopes for r in mon[s])
    last = max(r["month"] for s in scopes for r in mon[s])
    months = month_seq(first, last)
    cum, net = {}, {}
    for s in scopes:
        by = {r["month"]: r for r in mon[s]}
        c, cl, nl = 0.0, [], []
        for m in months:
            if m in by:
                c = by[m]["cumulative"]
            cl.append(k(c))
            nl.append(k(by[m]["net_pnl"]) if m in by else 0.0)
        cum[s], net[s] = cl, nl
    allc = cum["all"]
    peak_i = max(range(len(months)), key=lambda i: (allc[i], -i))
    low_i = min(range(len(months)), key=lambda i: (allc[i], i))
    by_all = {r["month"]: r for r in mon["all"]}

    def cum_exact(i):
        m = [x for x in months[:i + 1] if x in by_all]
        return by_all[m[-1]]["cumulative"] if m else 0.0

    windows = []
    for wid, label, start in (("all", "Full history", months[0]), ("recent", "Since 2024", "2024-01")):
        si = months.index(start) if start in months else 0
        vals = [v for s in scopes for v in cum[s][si:]]
        notes = [{"at": i - si, "label": lab} for i, lab in ((peak_i, None), (low_i, None)) if i >= si]
        windows.append(dict({"id": wid, "label": label, "start": si, "annotations": notes}, **axis(vals)))
    peak_label = "Peak %s (%s)" % (money_k(cum_exact(peak_i), True), mlabel(months[peak_i]))
    low_label = "Low %s (%s)" % (money_k(cum_exact(low_i), True), mlabel(months[low_i]))
    for w in windows:
        for n in w["annotations"]:
            n["label"] = peak_label if n["at"] == peak_i - w["start"] else low_label
    recent = months[-RECENT_MONTHS:]
    ri = len(months) - len(recent)
    mvals = [v for s in ("rh", "hl") if s in net for v in net[s][ri:]]
    return {
        "labels": [mlabel(m) for m in months], "unit": "K",
        "cumulative_k": cum, "monthly_net_k": net, "windows": windows,
        "peak": {"index": peak_i, "month": months[peak_i], "display": money(cum_exact(peak_i), True),
                 "label": peak_label},
        "low": {"index": low_i, "month": months[low_i], "display": money(cum_exact(low_i), True),
                "label": low_label},
        "drawdown_from_peak_display": money(r2(cum_exact(len(months) - 1) - cum_exact(peak_i)), True),
        "recent": {"start": ri, "labels": [mlabel(m) for m in recent], **axis(mvals)},
    }


def _years(se):
    mon = se["monthly"]
    out = []
    years = sorted({r["month"][:4] for r in mon["all"]})
    for y in years:
        row = {"year": y}
        for s in ("all", "rh", "hl"):
            rs = [r for r in mon.get(s, []) if r["month"][:4] == y]
            row[s + "_net"] = r2(math.fsum(r["net_pnl"] for r in rs))
            row[s + "_net_display"] = money(row[s + "_net"], True) if rs else "-"
        rs = [r for r in mon["all"] if r["month"][:4] == y]
        ct = sum(r["closed_trades"] for r in rs)
        w = sum(r["wins"] for r in rs)
        row.update({"closed_trades": ct, "fills": sum(r["fills"] for r in rs),
                    "win_rate_display": pct(r4(w / ct)) if ct else "-", "tone": tone(row["all_net"]),
                    "net_k": k(row["all_net"])})
        out.append(row)
    return out


def _asset_row(a):
    return {"asset": a["asset"], "src": SRC_LABELS[a["src"]], "cls": CLS_LABELS.get(a["cls"], a["cls"]),
            "net_display": money(a["net_pnl"], True), "net_k": k(a["net_pnl"]),
            "tone": tone(a["net_pnl"]), "closed_trades": a["closed_trades"], "win_rate_display": pct(a["win_rate"]),
            "biggest_win_display": money(a["biggest_win"], True) if a["wins"] else "-",
            "biggest_loss_display": money(a["biggest_loss"], True) if a["losses"] else "-",
            "approx_trades": a["approx_trades"]}


def _group_cells(a):
    return {"closed_trades": a["closed_trades"], "fills": a["fills"],
            "win_rate_display": pct(a["win_rate"]) if a["closed_trades"] else "-",
            "fees_display": money(a["fees"]),
            "net_display": money(a["net_pnl"], True), "tone": tone(a["net_pnl"]),
            "biggest_win_display": money(a["biggest_win"], True) if a["wins"] else "-",
            "biggest_loss_display": money(a["biggest_loss"], True) if a["losses"] else "-",
            "approx_trades": a["approx_trades"]}


def _group_row(g):
    # page rows keep only what the Page shows (size matters: page.json is embedded verbatim)
    venues = []
    for v in g["venues"]:
        vr = dict(_group_cells(v), venue_label=venue_label(v["venue"]), raw_symbol=v["raw_symbol"])
        if v["multiplier"] != 1:
            vr["multiplier_display"] = "x%d" % v["multiplier"]
        venues.append(vr)
    return dict(_group_cells(g), group=g["group"], symbol=g["symbol"],
                kind=KIND_GROUP_LABELS.get(g["kind"], g["kind"]), venue_count=g["venue_count"],
                title="%s · %s · %s" % (g["symbol"], money(g["net_pnl"], True),
                                        plural(g["venue_count"], "venue")),
                venues=venues)


MULTI_TOP_N = 24


def _groups(se):
    gs = se.get("asset_groups") or []
    closed = [g for g in gs if g["closed_trades"]]
    best = sorted(closed, key=lambda g: (-g["net_pnl"], g["group"]))[:TOP_N]
    worst = sorted(closed, key=lambda g: (g["net_pnl"], g["group"]))[:TOP_N]
    multi_all = sorted((g for g in gs if g["venue_count"] > 1), key=lambda g: (-abs(g["net_pnl"]), g["group"]))
    multi = multi_all[:MULTI_TOP_N]  # biggest by |net|; the full list is in series.json asset_groups
    # each group row is stored once; the best / worst / multi-venue lists name rows by group key
    shown = {g["group"]: g for g in best + worst + multi}
    return {"count": len(gs), "with_closes": len(closed), "multi_venue_count": len(multi_all),
            "rows": [_group_row(shown[key]) for key in sorted(shown)],
            "best": [g["group"] for g in best], "worst": [g["group"] for g in worst],
            "multi_venue": [g["group"] for g in multi]}


def _assets(se):
    pa = [a for a in se["per_asset"] if a["closed_trades"]]
    best = sorted(pa, key=lambda a: (-a["net_pnl"], a["asset"], a["src"], a["cls"]))[:TOP_N]
    worst = sorted(pa, key=lambda a: (a["net_pnl"], a["asset"], a["src"], a["cls"]))[:TOP_N]
    grp = defaultdict(list)
    for a in se["per_asset"]:
        grp[(a["src"], a["cls"])].append(a)
    classes = []
    for (src, cls), rows in grp.items():
        net = r2(math.fsum(a["net_pnl"] for a in rows))
        ct = sum(a["closed_trades"] for a in rows)
        w = sum(a["wins"] for a in rows)
        classes.append({"label": "%s %s" % (SRC_LABELS[src], CLS_LABELS.get(cls, cls)), "net_pnl": net,
                        "net_display": money(net, True), "net_k": k(net), "tone": tone(net),
                        "assets": len(rows), "closed_trades": ct, "fills": sum(a["fills"] for a in rows),
                        "win_rate_display": pct(r4(w / ct)) if ct else "-"})
    classes.sort(key=lambda c: (-c["net_pnl"], c["label"]))
    return {"best": [_asset_row(a) for a in best], "worst": [_asset_row(a) for a in worst],
            "classes": classes, "assets_with_closes": len(pa), "assets_total": len(se["per_asset"]),
            "assets_profitable": sum(1 for a in pa if a["net_pnl"] > 0), "groups": _groups(se)}


def _activity(se, sm):
    wd = sorted(se["activity"]["weekday"], key=lambda r: r["weekday"])
    tot = sum(r["fills"] for r in wd)
    weekday = [{"label": WEEKDAYS[r["weekday"]], "value": r["fills"], "display": num(r["fills"]),
                "share_display": pct(r4(r["fills"] / tot) if tot else 0.0)} for r in wd]
    mon = {r["month"]: r for r in se["monthly"]["all"]}
    years = sorted({m[:4] for m in mon})
    heat = [[mon.get("%s-%02d" % (y, i), {}).get("fills", 0) for i in range(1, 13)] for y in years]
    busiest = sorted(mon.values(), key=lambda r: (-r["fills"], r["month"]))[0]
    hold = []
    for h in se["holding_times"]:
        hold.append({"label": HOLD_LABELS.get(h["bucket"], h["bucket"]), "trades": h["trades"],
                     "realized_pnl": h["realized_pnl"], "pnl_display": money(h["realized_pnl"], True),
                     "pnl_k": k(h["realized_pnl"]), "tone": tone(h["realized_pnl"]),
                     "win_rate_display": pct(h["win_rate"]), "trades_display": num(h["trades"])})
    o = sm["overall"]
    return {"weekday": weekday, "heat_years": years, "heat_months": MONTHS, "heat_fills": heat,
            "heat_max": max([v for row in heat for v in row] + [0]),
            "busiest_month": {"month": busiest["month"], "label": mlabel(busiest["month"]),
                              "fills_display": num(busiest["fills"])},
            "avg_fills_per_day_display": num(o["avg_fills_per_day"], 2),
            "concentration_display": pct(o["concentration"]),
            "holding": hold}


def _trade(t):
    flags = [f for f, on in (("liquidated", t["liquidation"]), ("FIFO estimate", t["approx"]),
                             ("expired", t["expiry"])) if on]
    return {"sym": t["sym"], "src": SRC_LABELS[t["src"]], "cls": CLS_LABELS.get(t["cls"], t["cls"]),
            "side": t["side"], "date": t["date"], "pnl_display": money(t["pnl"], True), "tone": tone(t["pnl"]),
            "hold_display": ("%s days" % num(t["hold_days"], 1)) if t.get("hold_days") is not None else "-",
            "flags": flags}


def _positions(op, sm):
    as_of = dt.date.fromisoformat(op["as_of"][:10])
    rh = op.get("rh") or []
    eq_cost = {i: r2(p["qty"] * p["avg_price"]) for i, p in enumerate(rh)
               if p["cls"] == "equity" and p["avg_price"] is not None}
    eq_total = math.fsum(eq_cost.values())
    rows = []
    for i, p in enumerate(rh):
        h = p.get("asset_history")
        flags = []
        if not h or not h["closed_trades"]:
            flags.append("no_track_record")
        elif h["net_pnl"] < 0:
            flags.append("losing_track_record")
        if p["avg_price"] == 0:
            flags.append("zero_cost_basis")
        share = None
        if i in eq_cost and eq_total:
            share = r4(eq_cost[i] / eq_total)
            if share >= CONCENTRATION_FLAG:
                flags.append("concentrated")
        dte = None
        if p["cls"] == "option" and p.get("expiration"):
            dte = (dt.date.fromisoformat(p["expiration"]) - as_of).days
            if dte <= EXPIRY_SOON_DAYS:
                flags.append("expiring_soon")
            if p["side"] == "short" or (p["avg_price"] or 0) < 0:
                flags.append("short_premium")
        rows.append({"acct": p["acct"], "sym": p["sym"], "cls": CLS_LABELS.get(p["cls"], p["cls"]),
                     "side": p["side"], "qty_display": qty(p["qty"]),
                     "avg_price_display": money(p["avg_price"]) if p["avg_price"] is not None else "n/a",
                     "cost_basis_display": money(eq_cost[i]) if i in eq_cost else "-",
                     "share_display": pct(share) if share is not None else "-",
                     "expiration": p.get("expiration"), "days_to_expiry": dte,
                     "history_display": ("%s, %s" % (plural(h["closed_trades"], "trade"), money(h["net_pnl"], True))
                                         if h and h["closed_trades"] else "none"),
                     "history_tone": tone(h["net_pnl"]) if h else None,
                     "flags": flags})
    spot = []
    for s in op.get("hl_spot") or []:
        flags = ["no_mark_price"] + (["near_zero_cost"] if s["entry_notional"] < 1 else [])
        spot.append({"coin": s["coin"], "qty_display": qty(s["total"]), "entry_display": money(s["entry_notional"]),
                     "flags": flags})
    perp = []
    for s in op.get("hl_perp") or []:
        flags = ["near_liquidation"] if s.get("liq_distance_pct") is not None and s["liq_distance_pct"] < LIQ_NEAR_PCT else []
        perp.append({"coin": s["coin"], "side": s["side"], "size_display": qty(s["size"]),
                     "entry_display": money(s.get("entry_px")), "mark_display": money(s.get("mark_px")),
                     "leverage_display": "%sx" % num(s.get("leverage") or 0),
                     "upnl_display": money(s.get("unrealized_pnl"), True), "upnl_tone": tone(s.get("unrealized_pnl")),
                     "liq_distance_display": pct((s["liq_distance_pct"] or 0) / 100.0) if s.get("liq_distance_pct") is not None else "n/a",
                     "flags": flags})
    fc = defaultdict(int)
    for r in rows + spot + perp:
        for f in r["flags"]:
            fc[f] += 1
    return {"rh": rows, "hl_spot": spot, "hl_perp": perp,
            "counts": {"rh": len(rows), "hl_spot": len(spot), "hl_perp": len(perp),
                       "rh_equity": sum(1 for p in rh if p["cls"] == "equity"),
                       "rh_option": sum(1 for p in rh if p["cls"] == "option")},
            "rh_equity_cost_basis_display": money(eq_total),
            "flag_counts": [{"flag": f, "label": FLAG_LABELS[f], "count": fc[f]} for f in sorted(fc)]}


def _dq(dq, sm):
    checks = []
    for c in dq["checks"]:
        scope, _, name = c["name"].rpartition(".")
        d = c["detail"]
        checks.append({"name": c["name"], "scope": scope, "label": CHECK_LABELS.get(name, name),
                       "pass": c["pass"], "required": c["required"],
                       "detail": ", ".join("%s %s" % (key.replace("_", " "), d[key]) for key in sorted(d))})
    accts = []
    for a in (dq.get("rh") or {}).get("accounts", []):
        n = sum(a["fills"].values())
        g = a.get("gains") or {}
        accts.append({"tag": a["tag"], "kind": KIND_LABELS.get(a["kind"], a["kind"]), "fills": n,
                      "coverage_start": a["coverage_start"] or "-",
                      "fifo_before": g.get("fifo_closes_before_coverage", 0),
                      "rh_sourced": g.get("fifo_closes_rh_sourced", 0),
                      "no_cost_basis": g.get("closes_without_cost_basis", 0),
                      "fifo_dropped": g.get("fifo_closes_dropped_in_coverage", 0),
                      "expiry_closes": a["expiry_closes"], "possible_tie_gaps": a["possible_tie_gaps"],
                      "positions_mismatch": a["positions_reconciliation"]["mismatch"],
                      "fifo_open_without_position": a["positions_reconciliation"]["fifo_open_without_position"]})
    gs = sm["overall"]["gain_sources"]
    gains = [{"source": key, "label": GAIN_LABELS.get(key, key), "trades": v["trades"],
              "pnl_display": money(v["realized_pnl"], True), "tone": tone(v["realized_pnl"]),
              "share_display": pct(v["share"])} for key, v in sorted(gs.items())]
    h = dq.get("hl") or {}
    led = h.get("ledger_events") or {}
    vault = sum(v for key, v in led.items() if key.startswith("vault"))
    transfers = sum(led.get(key, 0) for key in ("deposit", "withdraw", "send", "internalTransfer",
                                                  "spotTransfer", "accountClassTransfer"))
    hc = h.get("counts") or {}
    nb = sum(a["no_cost_basis"] for a in accts)
    mism = sum(a["positions_mismatch"] + a["fifo_open_without_position"] for a in accts)
    gaps = [
        {"title": "Dividends and interest", "detail": "Not fetched; realized P&L excludes them."},
        {"title": "Transfers in/out", "detail": "%d RH closes had no cost basis (shares likely transferred in); "
                                                 "%d HL deposit/withdraw/transfer ledger events are not P&L."
                                                 % (nb, transfers)},
        {"title": "Splits and corporate actions", "detail": "Not modelled in FIFO; %d RH position lots disagree with FIFO." % mism},
        {"title": "Option assignment / exercise", "detail": "Expiries are modelled (%d expiry closes); "
                                                            "assignment and exercise are not." % sum(a["expiry_closes"] for a in accts)},
        {"title": "HL vaults and airdrops", "detail": "%d vault ledger events excluded from P&L; %d airdrop (genesis) events; "
                                                      "%d airdrop sell fills excluded." % (vault, hc.get("airdrop_genesis_events", 0),
                                                                                        hc.get("airdrop_sell_fills_excluded", 0))},
        {"title": "HL spot junk filter", "detail": "Only HYPE and Unit-bridged spot tokens count (matched by spotMeta "
                                                   "token index + tokenId); %d other spot fills (%d closing trades) "
                                                   "and %d spot balances excluded."
                                                   % (hc.get("spot_fills_excluded_not_whitelisted", 0),
                                                      hc.get("spot_trades_excluded_not_whitelisted", 0),
                                                      (h.get("spot_whitelist") or {}).get("excluded_spot_balances", 0))},
        {"title": "Unrealized P&L", "detail": "No mark prices fetched for RH or HL spot holdings; open positions show cost only."},
    ]
    ag = dq.get("asset_grouping") or {}
    first = sorted(a["coverage_start"] for a in accts if a["coverage_start"] != "-")
    return {"pass": dq["pass"], "checks": checks,
            "required_failed": sum(1 for c in checks if c["required"] and not c["pass"]),
            "optional_failed": sum(1 for c in checks if not c["required"] and not c["pass"]),
            "checks_total": len(checks), "rh_accounts": accts, "gain_sources": gains,
            "rh_coverage_start": first[0] if first else None,
            "approx_trades": sm["approximate"]["trades"], "approx_share_display": pct(sm["approximate"]["share"]),
            "hl": {"fills_raw": h.get("fills_raw", 0), "fills_used": h.get("fills_used", 0),
                   "funding_events": h.get("funding_events", 0), "dust_fills_excluded": hc.get("dust_fills_excluded", 0),
                   "liquidation_fills_self": h.get("liquidation_fills_self", 0),
                   "spot_fills_excluded": hc.get("spot_fills_excluded_not_whitelisted", 0),
                   "spot_trades_excluded": hc.get("spot_trades_excluded_not_whitelisted", 0)},
            "grouping": {"groups": ag.get("groups", 0), "multi_venue": ag.get("multi_venue_groups", 0),
                         "ambiguous_count": ag.get("ambiguous_count", 0),
                         "ambiguous": [dict(a, venue_label=venue_label(a["venue"]),
                                            reason_label=a["reason"].replace("_", " ")) for a in ag.get("ambiguous", [])],
                         "collisions": [" / ".join(c) for c in ag.get("symbol_kind_collisions", [])]},
            "gaps": gaps}


def build(sm, se, op, dq, lang="en"):
    o = sm["overall"]
    scopes = [_scope_row("all", "All sources", o)]
    kinds = {a["tag"]: a["kind"] for a in (dq.get("rh") or {}).get("accounts", [])}
    for key in sorted(sm["scopes"]):
        if key == "hl":
            name = "Hyperliquid"
        elif key == "rh":
            name = "Robinhood (all accounts)"
        else:
            name = "Robinhood " + KIND_LABELS.get(kinds.get(key.split(".")[1]), key)
        scopes.append(_scope_row(key, name, sm["scopes"][key]))
    return {
        "schema": SCHEMA, "as_of": sm["as_of"], "as_of_display": sm["as_of"][:16].replace("T", " ") + " UTC",
        "period": dict(o["period"], display="%s to %s" % (o["period"]["from"], o["period"]["to"])),
        "sources": [SRC_LABELS[s] for s in sorted(sm["sources"]) if sm["sources"][s]],
        "headline": _headline(sm), "persona": _persona(sm, lang), "scopes": scopes,
        "journey": _journey(se), "years": _years(se), "recent_months": [
            {"label": mlabel(r["month"]), "net_display": money(r["net_pnl"], True), "tone": tone(r["net_pnl"]),
             "closed_trades": r["closed_trades"], "win_rate_display": pct(r["win_rate"]),
             "cumulative_display": money(r["cumulative"], True)} for r in sm["recent_months"]],
        "best_months": [{"label": mlabel(r["month"]), "net_display": money(r["net_pnl"], True),
                         "win_rate_display": pct(r["win_rate"])} for r in sm["by_month_best"]],
        "worst_months": [{"label": mlabel(r["month"]), "net_display": money(r["net_pnl"], True),
                          "win_rate_display": pct(r["win_rate"])} for r in sm["by_month_worst"]],
        "assets": _assets(se), "activity": _activity(se, sm),
        "best_trades": [_trade(t) for t in sm["best_trades"]],
        "worst_trades": [_trade(t) for t in sm["worst_trades"]],
        "positions": _positions(op, sm), "data_quality": _dq(dq, sm),
        "flag_labels": FLAG_LABELS,
    }


def run(out_dir, lang="en"):
    docs = {n: read_json(os.path.join(out_dir, n + ".json"))
            for n in ("summary", "series", "open_positions", "data_quality")}
    missing = sorted(n for n, d in docs.items() if d is None)
    if missing:
        return {"error": "missing %s in %s" % (", ".join(missing), out_dir)}
    page = build(docs["summary"], docs["series"], docs["open_positions"], docs["data_quality"], lang)
    write_json(os.path.join(out_dir, "page.json"), page)
    return {"status": "done", "out": os.path.abspath(os.path.join(out_dir, "page.json"))}
