"""Page data: every figure, display string, chart series and risk flag a
dashboard Page shows, derived deterministically from the four compute outputs.

    lsl page --out DIR [--lang en|zh]     -> DIR/page.json

`lsl compute` also writes page.json. The bot pastes page.json into the Page
verbatim and writes only prose; it never formats or derives a number itself.
Input is only summary/series/open_positions/data_quality.json, so this step
can be re-run after the raw fetch state has been deleted.
"""
import datetime as dt
import json
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
        return _("Hyperliquid %s perps (HIP-3)") % v.split(":", 1)[1]
    return _(VENUE_LABELS.get(v, v))
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


# ---------------------------------------------------------------- language
# Every user-facing string below is written in English and passed through _();
# labels/<lang>.json "page" maps it to the target language (en maps to itself).
_L = {}


def set_lang(lang):
    global _L
    _L = persona.labels(lang).get("page", {})


def _(s):
    return _L.get(s, s)


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
    return _("%s " + word + ("" if n == 1 else "s")) % num(n)


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
    return _("{mon} {yy}").format(mon=_(MONTHS[int(m[5:7]) - 1]), yy=m[2:4])


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
            "fees_display": money(s["fees"]), "period_display": _("%s to %s") % (s["period"]["from"], s["period"]["to"]),
            "fifo_trades": fifo, "fifo_share_display": pct(r4(fifo / tot) if tot else 0.0)}


def _headline(sm):
    o, p = sm["overall"], sm["persona"]
    has_hl = bool(sm["sources"].get("hl"))
    items = [
        {"id": "net", "label": _("Net realized P&L"), "value": money_k(o["net_pnl"], True),
         "exact": money(o["net_pnl"], True),
         "detail": _("after fees, before HL funding") if has_hl else _("after fees"), "tone": tone(o["net_pnl"])},
        {"id": "net_funding", "label": _("Incl. HL funding"), "value": money_k(o["net_pnl_incl_funding"], True),
         "exact": money(o["net_pnl_incl_funding"], True),
         "detail": _("funding") + " " + money(o["funding"], True), "tone": tone(o["net_pnl_incl_funding"])},
        {"id": "win_rate", "label": _("Win rate"), "value": pct(o["win_rate"]),
         "detail": _("%s wins / %s losses") % (num(o["wins"]), num(o["losses"]))},
        {"id": "payoff", "label": _("Avg win / avg loss"), "value": num(o["profit_factor_ratio"], 2),
         "detail": _("%s vs %s") % (money(o["avg_win"]), money(o["avg_loss"])),
         "tone": "danger" if (o["profit_factor_ratio"] or 0) < 1 else "success"},
        {"id": "trades", "label": _("Closed trades"), "value": num(o["closed_trades"]),
         "detail": _("%s fills") % num(o["fills"])},
        {"id": "assets", "label": _("Assets traded"), "value": num(o["unique_assets"]),
         "detail": _("%s days of history") % num(o["period"]["days"])},
        {"id": "fees", "label": _("Fees paid"), "value": money_k(o["fees"]), "exact": money(o["fees"])},
        {"id": "liquidations", "label": _("Liquidation fills"), "value": num(o["liquidations"]),
         "detail": _("%s trades ended in liquidation") % num(o["liquidated_trades"]),
         "tone": "danger" if o["liquidations"] else None},
        {"id": "hold", "label": _("Median hold"), "value": _("%s days") % num(o["holding"]["median_days"], 1),
         "detail": _("average %s days") % num(o["holding"]["avg_days"], 1)},
        {"id": "busiest", "label": _("Busiest day"), "value": _("%s fills") % num(o["peak_fills_in_one_day"]),
         "detail": o["peak_fills_date"]},
        {"id": "grade", "label": _("Grade"), "value": p["grade"], "detail": None},
    ]
    # funding and liquidations exist only on Hyperliquid
    return [h for h in items if has_hl or h["id"] not in ("net_funding", "liquidations")]


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


def _lines(se):
    """Which series the charts draw: rh + hl when both exist, one per HL address when HL is
    split by address and alone, otherwise just "all"."""
    mon = se["monthly"]
    has = [x for x in ("rh", "hl") if mon.get(x)]
    if len(has) == 2:
        return ["rh", "hl"]
    sub = sorted(x for x in mon if x.startswith("hl.") and mon[x])
    if has == ["hl"] and len(sub) > 1:
        return sub
    return []


def line_label(key):
    if key == "all":
        return _("All sources")
    if key == "rh":
        return "Robinhood"
    if key == "hl":
        return "Hyperliquid"
    if key.startswith("hl."):
        return "HL " + key[3:]
    return key


def _journey(se):
    mon = se["monthly"]
    lines = _lines(se)
    scopes = ["all"] + lines
    rows = [r["month"] for s_ in scopes for r in mon.get(s_) or []]
    if not rows:
        return {"labels": [], "unit": "K", "lines": [], "cumulative_k": {"all": []}, "monthly_net_k": {"all": []},
                "windows": [], "peak": None, "low": None, "drawdown_from_peak_display": "-",
                "recent": {"start": 0, "labels": [], "ceiling": 0.0, "floor": 0.0}}
    months = month_seq(min(rows), max(rows))
    cum, net = {}, {}
    for s_ in scopes:
        by = {r["month"]: r for r in mon.get(s_) or []}
        c, cl, nl = 0.0, [], []
        for m in months:
            if m in by:
                c = by[m]["cumulative"]
            cl.append(k(c))
            nl.append(k(by[m]["net_pnl"]) if m in by else 0.0)
        cum[s_], net[s_] = cl, nl
    allc = cum["all"]
    peak_i = max(range(len(months)), key=lambda i: (allc[i], -i))
    low_i = min(range(len(months)), key=lambda i: (allc[i], i))
    by_all = {r["month"]: r for r in mon["all"]}

    def cum_exact(i):
        m = [x for x in months[:i + 1] if x in by_all]
        return by_all[m[-1]]["cumulative"] if m else 0.0

    windows = [("all", _("Full history"), months[0])]
    # a "recent" zoom only when there are 3+ years: the last two calendar years
    if len(months) > 36:
        start = "%04d-01" % (int(months[-1][:4]) - 1)
        windows.append(("recent", _("Since %s") % start[:4], start))
    out_w = []
    for wid, label, start in windows:
        si = months.index(start) if start in months else 0
        vals = [v for s_ in scopes for v in cum[s_][si:]]
        notes = [{"at": i - si, "label": None} for i in (peak_i, low_i) if i >= si]
        out_w.append(dict({"id": wid, "label": label, "start": si, "annotations": notes}, **axis(vals)))
    peak_label = _("Peak %s (%s)") % (money_k(cum_exact(peak_i), True), mlabel(months[peak_i]))
    low_label = _("Low %s (%s)") % (money_k(cum_exact(low_i), True), mlabel(months[low_i]))
    for w in out_w:
        for n in w["annotations"]:
            n["label"] = peak_label if n["at"] == peak_i - w["start"] else low_label
    recent = months[-RECENT_MONTHS:]
    ri = len(months) - len(recent)
    mvals = [v for s_ in (lines or ["all"]) for v in net[s_][ri:]]
    return {
        "labels": [mlabel(m) for m in months], "unit": "K",
        "lines": [{"key": x, "label": line_label(x)} for x in (lines or ["all"])],
        "cumulative_k": cum, "monthly_net_k": net, "windows": out_w,
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
    cols = ["all"] + _lines(se)
    for y in years:
        row = {"year": y, "cells": []}
        for s in cols:
            rs = [r for r in mon.get(s, []) if r["month"][:4] == y]
            v = r2(math.fsum(r["net_pnl"] for r in rs))
            row[s + "_net"] = v
            row[s + "_net_display"] = money(v, True) if rs else "-"
            if s != "all":
                row["cells"].append({"key": s, "display": money(v, True) if rs else "-", "tone": tone(v)})
        rs = [r for r in mon["all"] if r["month"][:4] == y]
        ct = sum(r["closed_trades"] for r in rs)
        w = sum(r["wins"] for r in rs)
        row.update({"closed_trades": ct, "fills": sum(r["fills"] for r in rs),
                    "win_rate_display": pct(r4(w / ct)) if ct else "-", "tone": tone(row["all_net"]),
                    "net_k": k(row["all_net"])})
        out.append(row)
    return out


def _years_cols(se):
    return [{"key": x, "label": line_label(x)} for x in _lines(se)]


def _asset_row(a):
    return {"asset": a["asset"], "src": SRC_LABELS[a["src"]], "cls": _(CLS_LABELS.get(a["cls"], a["cls"])),
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
                kind=_(KIND_GROUP_LABELS.get(g["kind"], g["kind"]) or "-"), venue_count=g["venue_count"],
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
        classes.append({"label": "%s %s" % (SRC_LABELS[src], _(CLS_LABELS.get(cls, cls))), "net_pnl": net,
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
    weekday = [{"label": _(WEEKDAYS[r["weekday"]]), "value": r["fills"], "display": num(r["fills"]),
                "share_display": pct(r4(r["fills"] / tot) if tot else 0.0)} for r in wd]
    mon = {r["month"]: r for r in se["monthly"]["all"]}
    years = sorted({m[:4] for m in mon})
    heat = [[mon.get("%s-%02d" % (y, i), {}).get("fills", 0) for i in range(1, 13)] for y in years]
    busiest = (sorted(mon.values(), key=lambda r: (-r["fills"], r["month"])) or [None])[0]
    hold = []
    for h in se["holding_times"]:
        hold.append({"label": _(HOLD_LABELS.get(h["bucket"], h["bucket"])), "trades": h["trades"],
                     "realized_pnl": h["realized_pnl"], "pnl_display": money(h["realized_pnl"], True),
                     "pnl_k": k(h["realized_pnl"]), "tone": tone(h["realized_pnl"]),
                     "win_rate_display": pct(h["win_rate"]), "trades_display": num(h["trades"])})
    o = sm["overall"]
    return {"weekday": weekday, "heat_years": years, "heat_months": [_(m) for m in MONTHS], "heat_fills": heat,
            "heat_max": max([v for row in heat for v in row] + [0]),
            "busiest_month": {"month": busiest["month"], "label": mlabel(busiest["month"]),
                              "fills_display": num(busiest["fills"])} if busiest else None,
            "avg_fills_per_day_display": num(o["avg_fills_per_day"], 2),
            "concentration_display": pct(o["concentration"]),
            "holding": hold}


def _trade(t):
    flags = [_(f) for f, on in (("liquidated", t["liquidation"]), ("FIFO estimate", t["approx"]),
                                ("expired", t["expiry"])) if on]
    return {"sym": t["sym"], "src": SRC_LABELS[t["src"]], "cls": _(CLS_LABELS.get(t["cls"], t["cls"])),
            "acct": t.get("acct"), "side": _(t["side"]), "date": t["date"], "pnl_display": money(t["pnl"], True),
            "tone": tone(t["pnl"]),
            "hold_display": (_("%s days") % num(t["hold_days"], 1)) if t.get("hold_days") is not None else "-",
            "flags": flags}


def _positions(op, sm):
    as_of = dt.date.fromisoformat((op.get("as_of") or sm.get("as_of") or "1970-01-01")[:10])
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
        rows.append({"acct": p["acct"], "sym": p["sym"], "cls": _(CLS_LABELS.get(p["cls"], p["cls"])),
                     "side": _(p["side"]), "qty_display": qty(p["qty"]),
                     "avg_price_display": money(p["avg_price"]) if p["avg_price"] is not None else "n/a",
                     "cost_basis_display": money(eq_cost[i]) if i in eq_cost else "-",
                     "share_display": pct(share) if share is not None else "-",
                     "expiration": p.get("expiration"), "days_to_expiry": dte,
                     "history_display": (_("%s, %s") % (plural(h["closed_trades"], "trade"), money(h["net_pnl"], True))
                                         if h and h["closed_trades"] else _("none")),
                     "history_tone": tone(h["net_pnl"]) if h else None,
                     "flags": flags})
    spot = []
    for s in op.get("hl_spot") or []:
        flags = ["no_mark_price"] + (["near_zero_cost"] if s["entry_notional"] < 1 else [])
        spot.append({"acct": s.get("acct"), "coin": s["coin"], "qty_display": qty(s["total"]), "entry_display": money(s["entry_notional"]),
                     "flags": flags})
    perp = []
    for s in op.get("hl_perp") or []:
        flags = ["near_liquidation"] if s.get("liq_distance_pct") is not None and s["liq_distance_pct"] < LIQ_NEAR_PCT else []
        perp.append({"acct": s.get("acct"), "coin": s["coin"], "side": _(s["side"]), "size_display": qty(s["size"]),
                     "entry_display": money(s.get("entry_px")), "mark_display": money(s.get("mark_px")),
                     "leverage_display": "%sx" % num(s.get("leverage") or 0),
                     "upnl_display": money(s.get("unrealized_pnl"), True), "upnl_tone": tone(s.get("unrealized_pnl")),
                     "liq_distance_display": pct((s["liq_distance_pct"] or 0) / 100.0) if s.get("liq_distance_pct") is not None else _("n/a"),
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
            "flag_counts": [{"flag": f, "label": _(FLAG_LABELS[f]), "count": fc[f]} for f in sorted(fc)]}


def _dq(dq, sm):
    checks = []
    for c in dq["checks"]:
        scope, _sep, name = c["name"].rpartition(".")
        d = c["detail"]
        checks.append({"name": c["name"], "scope": scope, "label": _(CHECK_LABELS.get(name, name)),
                       "pass": c["pass"], "required": c["required"],
                       "detail": ", ".join("%s %s" % (key.replace("_", " "), d[key]) for key in sorted(d))})
    accts = []
    for a in (dq.get("rh") or {}).get("accounts", []):
        n = sum(a["fills"].values())
        g = a.get("gains") or {}
        accts.append({"tag": a["tag"], "kind": _(KIND_LABELS.get(a["kind"], a["kind"])), "fills": n,
                      "label": ((sm.get("scope") or {}).get("rh_account_labels") or {}).get(a["tag"]) or a["tag"],
                      "coverage_start": a["coverage_start"] or "-",
                      "fifo_before": g.get("fifo_closes_before_coverage", 0),
                      "rh_sourced": g.get("fifo_closes_rh_sourced", 0),
                      "no_cost_basis": g.get("closes_without_cost_basis", 0),
                      "fifo_dropped": g.get("fifo_closes_dropped_in_coverage", 0),
                      "expiry_closes": a["expiry_closes"], "possible_tie_gaps": a["possible_tie_gaps"],
                      "positions_mismatch": a["positions_reconciliation"]["mismatch"],
                      "fifo_open_without_position": a["positions_reconciliation"]["fifo_open_without_position"]})
    gs = sm["overall"]["gain_sources"]
    gains = [{"source": key, "label": _(GAIN_LABELS.get(key, key)), "trades": v["trades"],
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
    has_rh, has_hl = bool(sm["sources"].get("rh")), bool(sm["sources"].get("hl"))
    gaps = [{"title": _("Dividends and interest"), "detail": _("Not fetched; realized P&L excludes them.")}]
    if has_rh:
        gaps += [
            {"title": _("Transfers in/out"), "detail": _("%d RH closes had no cost basis (shares likely transferred in).") % nb},
            {"title": _("Splits and corporate actions"),
             "detail": _("Not modelled in FIFO; %d RH position lots disagree with FIFO.") % mism},
            {"title": _("Option assignment / exercise"),
             "detail": _("Expiries are modelled (%d expiry closes); assignment and exercise are not.")
             % sum(a["expiry_closes"] for a in accts)}]
    if has_hl:
        gaps += [
            {"title": _("HL transfers"), "detail": _("%d HL deposit/withdraw/transfer ledger events are not P&L.") % transfers},
            {"title": _("HL vaults and airdrops"),
             "detail": _("%d vault ledger events excluded from P&L; %d airdrop (genesis) events; "
                         "%d airdrop sell fills excluded.") % (vault, hc.get("airdrop_genesis_events", 0),
                                                              hc.get("airdrop_sell_fills_excluded", 0))},
            {"title": _("HL spot junk filter"),
             "detail": _("Only HYPE and Unit-bridged spot tokens count (matched by spotMeta token index + tokenId); "
                         "%d other spot fills (%d closing trades) and %d spot balances excluded.")
             % (hc.get("spot_fills_excluded_not_whitelisted", 0), hc.get("spot_trades_excluded_not_whitelisted", 0),
                (h.get("spot_whitelist") or {}).get("excluded_spot_balances", 0))}]
    gaps.append({"title": _("Unrealized P&L"),
                 "detail": _("No mark prices fetched for RH or HL spot holdings; open positions show cost only.")})
    ag = dq.get("asset_grouping") or {}
    first = sorted(a["coverage_start"] for a in accts if a["coverage_start"] != "-")
    return {"pass": dq["pass"], "checks": checks,
            "required_failed": sum(1 for c in checks if c["required"] and not c["pass"]),
            "optional_failed": sum(1 for c in checks if not c["required"] and not c["pass"]),
            "checks_total": len(checks), "rh_accounts": accts, "gain_sources": gains,
            "rh_coverage_start": first[0] if first else None,
            "approx_trades": sm["approximate"]["trades"], "approx_share_display": pct(sm["approximate"]["share"]),
            "hl": None if not has_hl else {"fills_raw": h.get("fills_raw", 0), "fills_used": h.get("fills_used", 0),
                   "funding_events": h.get("funding_events", 0), "dust_fills_excluded": hc.get("dust_fills_excluded", 0),
                   "liquidation_fills_self": h.get("liquidation_fills_self", 0),
                   "spot_fills_excluded": hc.get("spot_fills_excluded_not_whitelisted", 0),
                   "spot_trades_excluded": hc.get("spot_trades_excluded_not_whitelisted", 0)},
            "grouping": {"groups": ag.get("groups", 0), "multi_venue": ag.get("multi_venue_groups", 0),
                         "ambiguous_count": ag.get("ambiguous_count", 0),
                         "ambiguous": [dict(a, venue_label=venue_label(a["venue"]),
                                            reason_label=_(a["reason"].replace("_", " "))) for a in ag.get("ambiguous", [])],
                         "collisions": [" / ".join(c) for c in ag.get("symbol_kind_collisions", [])]},
            "gaps": gaps}


def _scopes(sm, dq):
    o = sm["overall"]
    rows = [_scope_row("all", _("All sources"), o)]
    kinds = {a["tag"]: a["kind"] for a in (dq.get("rh") or {}).get("accounts", [])}
    keys = sorted(sm["scopes"])
    for key in keys:
        if key == "hl":
            name = "Hyperliquid"
        elif key == "rh":
            name = _("Robinhood (all accounts)")
        elif key.startswith("hl."):
            name = "Hyperliquid " + key[3:]
        else:
            name = "Robinhood " + _(KIND_LABELS.get(kinds.get(key.split(".")[1]), key))
        st = sm["scopes"][key]
        # a scope identical to the total (single source / single account) adds nothing
        if st["fills"] == o["fills"] and st["closed_trades"] == o["closed_trades"] and r2(st["net_pnl"]) == r2(o["net_pnl"]):
            continue
        rows.append(_scope_row(key, name, st))
    return rows


ACCT_SPARK_POINTS = 60


def _acct_spark(se, key):
    rows = (se.get("cumulative") or {}).get(key) or []
    if len(rows) > ACCT_SPARK_POINTS:
        n = len(rows)
        rows = [rows[int(round(i * (n - 1) / (ACCT_SPARK_POINTS - 1)))] for i in range(ACCT_SPARK_POINTS)]
    vals = [k(r["cumulative"]) for r in rows]
    return dict(axis(vals), labels=[r["day"] for r in rows], values=vals, unit="K")


def _accounts(sm, se, lang):
    """By-account section (only with two or more accounts): display rows, a totals row,
    per-account detail (equity sparkline in $K, profile facts) and insight callouts."""
    from . import report
    A = sm.get("accounts") or {}
    T = persona.labels(lang)["accounts"]
    PL = persona.labels(lang)["profile"]
    if (A.get("count") or 0) < 2:
        return {"show": False, "count": A.get("count") or 0, "rows": [], "total": None, "insights": [],
                "shared_display": None, "labels": T}

    def lab(r):
        return (r.get("labels") or {}).get(lang) or r["label"]

    def asset(x):
        return "%s %s" % (x["symbol"], money(x["net_pnl"], True)) if x else "-"

    rows = []
    for r in A["rows"]:
        prof = (sm.get("profile") or {}).get(r["key"])
        one = report._one(prof, lang) if prof else None
        style = next((f["value"] for f in (one or {}).get("facts", []) if f["id"] == "style"), "-")
        period = _("%s to %s") % (r["period"]["from"], r["period"]["to"]) if r["period"]["from"] else "-"
        rows.append({
            "key": r["key"], "slug": r["slug"], "label": lab(r), "venue": SRC_LABELS[r["venue"]],
            "net_display": money(r["net_pnl_incl_funding"], True), "net_k_display": money_k(r["net_pnl_incl_funding"], True),
            "tone": tone(r["net_pnl_incl_funding"]),
            "net_ex_funding_display": money(r["net_pnl"], True), "funding_display": money(r["funding"], True),
            "rov_display": pct(r["return_on_volume"], 2) if r["return_on_volume"] is not None else "-",
            "volume_display": money_k(r["volume"]), "trades": num(r["closed_trades"]),
            "win_rate_display": pct(r["win_rate"]) if r["win_rate"] is not None else "-",
            "max_dd_display": money(r["max_drawdown"]) if r["max_drawdown"] else "$0.00",
            "best_display": asset(r["best_asset"]), "worst_display": asset(r["worst_asset"]),
            "style_display": style, "share_display": pct(r["share_of_total"]) if r["share_of_total"] is not None else "-",
            "period_display": period,
            "caption": T["detail_caption"].format(venue=SRC_LABELS[r["venue"]], period=period,
                                                  net_ex=money(r["net_pnl"], True), funding=money(r["funding"], True)),
            "spark": _acct_spark(se, r["key"]),
            "facts": [{"label": f["label"], "value": f["value"]} for f in (one or {}).get("facts", [])],
            "strip_display": (one or {}).get("strip_display") or "",
            "strip_label": (one or {}).get("strip_label") or PL["no_trades"],
        })
    t = A["totals"]
    total = {"label": T["total"], "net_display": money(t["net_pnl_incl_funding"], True),
             "tone": tone(t["net_pnl_incl_funding"]), "volume_display": money_k(t["volume"]),
             "rov_display": pct(t["net_pnl_incl_funding"] / t["volume"], 2) if t["volume"] else "-",
             "trades": num(t["closed_trades"]), "win_rate_display": pct(t["win_rate"]) if t["closed_trades"] else "-",
             "share_display": pct(1.0) if abs(t["net_pnl_incl_funding"]) >= 0.005 else "-"}
    ins = []
    I = A.get("insights") or {}
    lm = {r["key"]: lab(r) for r in A["rows"]}

    def L(x):
        return lm.get(x["key"], x["label"])

    LS, CS = T["list_sep"], T["clause_sep"]

    def side(xs):
        return LS.join(T["side_item"].format(label=L(x), trades=num(x["closed_trades"]),
                                               net=money(x["net_pnl"], True)) for x in xs)
    for o in I.get("opposing") or []:
        ins.append({"kind": "opposing", "tone": "warning", "lead": T["opposing_lead"].format(symbol=o["symbol"]),
                    "body": T["opposing_body"].format(long=side(o["long"]), short=side(o["short"]))})
    for o in I.get("open_opposing") or []:
        ins.append({"kind": "open_opposing", "tone": "info", "lead": T["open_lead"].format(symbol=o["symbol"]),
                    "body": T["open_body"].format(long=LS.join(L(x) for x in o["long"]),
                                                 short=LS.join(L(x) for x in o["short"]))})
    off = I.get("offsets")
    if off:
        body = T["offset_body"].format(
            winners=LS.join(L(x) for x in off["winners"]),
            gain=money(off["gross_gain"], True),
            losers=LS.join(L(x) for x in off["losers"]),
            loss=money(off["gross_loss"], True))
        shares = CS.join(T["loser_share"].format(label=L(x), share=pct(x["share_of_gains"])) for x in off["losers"])
        ins.append({"kind": "offsets", "tone": "danger" if off["given_back"] >= 1 else "warning",
                    "lead": T["offset_lead"].format(given_back=pct(off["given_back"])),
                    "body": T["with_shares"].format(body=body, shares=shares)})
    return {"show": True, "count": A["count"], "rows": rows, "total": total, "insights": ins,
            "none": T["none"] if not ins else None,
            "shared_display": T["shared"].format(n=num(I["shared_assets"])) if I.get("shared_assets") else None,
            "caption": T["caption"].format(n=num(A["count"])), "labels": T}


def trend_vars(tr):
    """Display values shared by the Page callouts and the TLDR trend line."""
    w, c = tr["by_alignment"]["with"], tr["by_alignment"]["counter"]
    a = tr.get("alt_longs_in_btc_death_cross") or {}
    wr = lambda b: pct(b["win_rate"]) if b.get("win_rate") is not None else "-"
    av = lambda b: money(b["avg_pnl"], True) if b.get("avg_pnl") is not None else "-"
    return {"w_wr": wr(w), "w_net": money(w["pnl"], True), "w_n": num(w["trades"]), "w_avg": av(w),
            "c_wr": wr(c), "c_net": money(c["pnl"], True), "c_n": num(c["trades"]), "c_avg": av(c),
            "counter_share": pct(tr["counter_share"]) if tr.get("counter_share") is not None else "-",
            "n": num(a.get("trades") or 0), "wr": wr(a) if a else "-", "net": money(a.get("pnl") or 0, True)}


def _trend(sm, lang):
    """Trend-discipline section: SMA alignment of every entry, all figures from summary["trend"]."""
    tr = sm.get("trend") or {}
    T = persona.labels(lang)["trend"]
    if tr.get("status") != "ok" or not tr.get("tagged"):
        return {"show": False, "labels": {}, "message": T["no_candles"] if tr.get("status") == "no_candles" else None}
    cov = tr["coverage"]
    untagged = [T["status_item"].format(label=T["status_" + k], n=num(v)) for k, v in sorted(cov["by_status"].items())
                if k != "ok" and v and ("status_" + k) in T]
    tagged = tr["tagged"]

    def row(label, b, share=True):
        return {"label": label, "trades": num(b["trades"]),
                "share_display": pct(b["trades"] / tagged) if share and tagged else "-",
                "win_rate_display": pct(b["win_rate"]) if b.get("win_rate") is not None else "-",
                "net_display": money(b["pnl"], True), "avg_display": money(b["avg_pnl"], True) if b.get("avg_pnl") is not None else "-",
                "tone": tone(b["pnl"])}

    def split(key, label, b):
        al = b["by_alignment"]
        return {"key": key, "label": label, "tagged": num(b["tagged"]),
                "with_display": pct(b["with_share"]) if b.get("with_share") is not None else "-",
                "counter_display": pct(b["counter_share"]) if b.get("counter_share") is not None else "-",
                "with_net_display": money(al["with"]["pnl"], True), "with_tone": tone(al["with"]["pnl"]),
                "counter_net_display": money(al["counter"]["pnl"], True), "counter_tone": tone(al["counter"]["pnl"])}
    acct_lab = {r["key"]: (r.get("labels") or {}).get(lang) or r["label"] for r in (sm.get("accounts") or {}).get("rows", [])}
    V = trend_vars(tr)
    callouts = []
    for code in tr.get("notable") or []:
        k = {"alt_longs_in_btc_death_cross_lost": "alt_longs"}.get(code, code)
        tone_ = "success" if code == "with_trend_better" else "warning" if code == "counter_trend_better" else "danger"
        callouts.append({"kind": code, "tone": tone_, "lead": T["n_%s_lead" % k].format(**V), "body": T["n_" + k].format(**V)})
    per = sm.get("persona") or {}
    basis = per.get("trend_basis")
    from .trend import MIN_TAGGED
    return {
        "show": True, "labels": {k: v for k, v in T.items() if k.startswith(("col_", "by_"))}, "caption": T["caption"],
        "coverage_display": T["coverage"].format(tagged=num(tagged), closed=num(cov["closed_trades"]), share=pct(cov["share"]),
                                                 untagged=T.get("list_sep", ", ").join(untagged) or "-"),
        "alignment": [row(T[a], tr["by_alignment"][a]) for a in ("with", "counter", "neutral")],
        "regime": [row(T[r], tr["by_regime"][r]) for r in ("golden", "death", "flat") if tr["by_regime"][r]["trades"]],
        "direction": [row("%s · %s" % (T[d], T[a]), tr["by_direction"][d][a])
                      for d in ("long", "short") for a in ("with", "counter", "neutral") if tr["by_direction"][d][a]["trades"]],
        "venues": [split(v, venue_label(v), b) for v, b in sorted(tr["by_venue"].items()) if b["tagged"]],
        "accounts": [split(a, acct_lab.get(a, a), b) for a, b in sorted(tr["by_account"].items()) if b["tagged"]]
                    if len(acct_lab) >= 2 else [],
        "callouts": callouts, "none": T["none"] if not callouts else None, "rules": T["rules"],
        "basis_display": (T["basis_sma"].format(score=num((per.get("ratings") or {}).get("trend_awareness") or 0))
                          if basis == "sma" else T["basis_formula"].format(min=num(MIN_TAGGED))),
    }


def build(sm, se, op, dq, lang="en", copy=None):
    from . import card as card_mod, report
    set_lang(lang)
    o = sm["overall"]
    src = sm.get("sources") or {}
    scope = sm.get("scope") or {"id": "all", "kind": "all", "label": None}
    lines = _lines(se)
    page = {
        "schema": SCHEMA, "lang": lang, "html_lang": "zh-Hans" if lang == "zh" else "en", "as_of": sm["as_of"],
        "as_of_display": (sm["as_of"] or "")[:16].replace("T", " ") + " UTC",
        "scope": {"id": scope["id"], "kind": scope.get("kind", "all"),
                  "label": (scope.get("labels") or {}).get(lang) or scope.get("label") or _("All accounts")},
        "period": dict(o["period"], display=_("%s to %s") % (o["period"]["from"], o["period"]["to"])),
        "sources": [SRC_LABELS[s_] for s_ in sorted(src) if src[s_]],
        "has": {"rh": bool(src.get("rh")), "hl": bool(src.get("hl")),
                "hl_multi": bool(sm.get("hl_addresses")), "both": bool(src.get("rh")) and bool(src.get("hl")),
                "trades": bool(o["closed_trades"]), "journey": bool(se["monthly"].get("all")),
                "rh_coverage": False},
        "ui": report.ui(lang),
        "headline": _headline(sm), "persona": _persona(sm, lang), "scopes": _scopes(sm, dq),
        "journey": _journey(se), "years": _years(se), "year_columns": _years_cols(se), "recent_months": [
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
        "flag_labels": {f: _(v) for f, v in FLAG_LABELS.items()},
    }
    page["has"]["rh_coverage"] = bool(page["data_quality"]["rh_coverage_start"])
    page["has"]["multi_line"] = bool(lines)
    page["accounts"] = _accounts(sm, se, lang)
    page["has"]["accounts"] = page["accounts"]["show"]
    page["trend"] = _trend(sm, lang)
    page["has"]["trend"] = page["trend"]["show"]
    page["profile"] = report.profile_block(sm, lang)
    page["card"] = card_mod.page_block(sm, se, lang, copy)
    page["tldr"] = report.tldr(sm, page, lang)
    page["compact"] = False
    page["positions"]["rh_more"] = 0
    page["card"]["compact"] = False
    return page


# --compact: the Page source is pasted inline in one WriteCanvas call, so the payload must stay
# small. Fixed, deterministic trims; everything dropped stays in the chat report and the CLI.
COMPACT = {"spark_points": 24, "groups_best": 5, "groups_worst": 5, "groups_multi": 8, "rh_positions": 12}


def _pick(seq, n):
    """n evenly spaced items of seq, first and last included (seq itself when short enough)."""
    m = len(seq)
    if m <= n:
        return list(seq)
    return [seq[int(round(i * (m - 1) / (n - 1)))] for i in range(n)]


def compact(page):
    """Shrink a built page in place (and return it):
    - share cards: only the default period's set (no other periods); per-account cards are
      dropped from the Page (they stay available via `lsl card --per-account`);
    - asset groups: top 5 best / 5 worst / 8 multi-venue;
    - per-account sparklines: 24 points; Robinhood positions: first 12 rows (`rh_more` counts the rest);
    - fields the Page template never reads (`scopes`, `assets.best`, `assets.worst`)."""
    C = page["card"]
    per = (C.get("default") or {}).get("period")
    C["variants"] = {per: C["variants"][per]} if per and per in C["variants"] else {}
    C["sizes"] = {k: v for k, v in C["sizes"].items() if k == per}
    C["periods"] = [x for x in C["periods"] if x["id"] == per]
    C["badges"] = {k: v for k, v in C["badges"].items() if k == per}
    C["accounts"] = []
    C["compact"] = True
    G = page["assets"]["groups"]
    G["best"] = G["best"][:COMPACT["groups_best"]]
    G["worst"] = G["worst"][:COMPACT["groups_worst"]]
    G["multi_venue"] = G["multi_venue"][:COMPACT["groups_multi"]]
    shown = set(G["best"] + G["worst"] + G["multi_venue"])
    G["rows"] = [r for r in G["rows"] if r["group"] in shown]
    page["assets"].pop("best", None)
    page["assets"].pop("worst", None)
    page.pop("scopes", None)
    for r in page["accounts"]["rows"]:
        sp = r["spark"]
        vals = _pick(sp["values"], COMPACT["spark_points"])
        r["spark"] = dict(axis(vals), labels=_pick(sp["labels"], COMPACT["spark_points"]), values=vals, unit=sp["unit"])
    P = page["positions"]
    n = COMPACT["rh_positions"]
    P["rh_more"] = max(0, len(P["rh"]) - n)
    P["rh"] = P["rh"][:n]
    page["compact"] = True
    return page


def run(out_dir, lang="en", compact_=False, copy_path=None):
    """Rebuild page.json in out_dir from the compute outputs there, in `lang` (`compact_`: see compact();
    `copy_path`: the bot's copy.json for the share card, checked by card.load_copy)."""
    docs = {n: read_json(os.path.join(out_dir, n + ".json"))
            for n in ("summary", "series", "open_positions", "data_quality")}
    missing = sorted(n for n, d in docs.items() if d is None)
    if missing:
        return {"error": "missing %s in %s" % (", ".join(missing), out_dir)}
    copy = None
    if copy_path:
        from . import card as card_mod
        copy, probs = card_mod.load_copy(copy_path, docs["summary"], lang)
        if probs:
            return {"error": "copy.json rejected; fix it or rerun without --copy (templates)", "problems": probs}
    page = build(docs["summary"], docs["series"], docs["open_positions"], docs["data_quality"], lang, copy)
    if compact_:
        compact(page)
    path = os.path.join(out_dir, "page.json")
    write_json(path, page)
    return {"status": "done", "out": os.path.abspath(path), "compact": bool(compact_),
            "card_copy": page["card"]["copy_source"],
            "inline_bytes": len(json.dumps(page, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode())}
