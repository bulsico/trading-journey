"""Trader persona: archetype scores, primary archetype, modifiers, five ratings
and grade.  Pure functions of summary statistics (ported 1:1 from the old
TypeScript analyze.ts so historic reports stay comparable).  Text labels live
in labels/{zh,en}.json; this module only returns codes."""
import json
import math
import os

ARCHETYPES = ("TREND_MASTER", "OVERTRADE", "KNIFE_CATCHER", "SCATTER_GUN",
              "PANIC_TRADER", "DIAMOND_HANDS")
MODIFIERS = ("STOP_LOSS_PHOBIA", "AVERAGING_DOWN", "TREND_AWAKENING")
GRADES = ("S", "A", "B", "C", "D", "F", "?")


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def _jsround(x):
    """Math.round semantics (half up), deterministic."""
    return int(math.floor(x + 0.5))


def volatility(counts):
    """population stddev / mean of monthly fill counts (0 when < 2 months)."""
    if len(counts) < 2:
        return 0.0
    m = math.fsum(counts) / len(counts)
    if not m:
        return 0.0
    sd = math.sqrt(math.fsum((c - m) ** 2 for c in counts) / len(counts))
    return sd / m


def scores(fpd, win_rate, avg_win, avg_abs_loss, unique_assets, concentration,
           liq_rate, fill_volatility):
    lwr = avg_abs_loss / avg_win if avg_win > 0 else 5.0
    if win_rate > 0.5 and avg_win > avg_abs_loss:
        tm = min(100.0, (win_rate - 0.4) * 200 + (avg_win / (avg_abs_loss or 1)) * 10)
    else:
        tm = max(0.0, win_rate * 50)
    return {
        "overtrade": _jsround(min(100.0, fpd * 5)),
        "knife_catcher": _jsround(min(100.0, lwr * 25)),
        "scatter_gun": _jsround(min(100.0, unique_assets / 10.0 * 50 * (1 - concentration))),
        "trend_master": _jsround(tm),
        "panic_trader": _jsround(min(100.0, liq_rate * 500 + fill_volatility * 30)),
        "diamond_hands": _jsround(_clamp((1 - fpd / 5.0) * 50 + (1 - unique_assets / 30.0) * 50)),
    }


# Trend data (lsl/trend.py) replaces the win-rate proxies when at least
# trend.MIN_TAGGED closed trades carry an SMA tag ("trend_basis": "sma"):
# * trend_awareness = round(10 * with-trend share of tagged closed trades);
# * TREND_MASTER    = with-trend share >= 0.65, with-trend P&L > 0 and net > 0;
# * TREND_AWAKENING = counter-trend P&L < 0, counter-trend share >= 0.2 and the
#                     with-trend average P&L beats the counter-trend average.
# Otherwise the original formulas below are used ("trend_basis": "formula").
TM_WITH_SHARE = 0.65
AWAKENING_COUNTER_SHARE = 0.2


def trend_usable(tr):
    from .trend import MIN_TAGGED
    return bool(tr) and tr.get("status") == "ok" and (tr.get("tagged") or 0) >= MIN_TAGGED


def archetype(sc, win_rate, net, avg_win, avg_abs_loss, tr=None):
    use = trend_usable(tr)
    if use:
        w, c = tr["by_alignment"]["with"], tr["by_alignment"]["counter"]
        if tr["with_share"] >= TM_WITH_SHARE and w["pnl"] > 0 and net > 0:
            return "TREND_MASTER", []
    elif sc["trend_master"] >= 65 and net > 0:
        return "TREND_MASTER", []
    cands = [("OVERTRADE", sc["overtrade"]), ("KNIFE_CATCHER", sc["knife_catcher"]),
             ("SCATTER_GUN", sc["scatter_gun"]), ("PANIC_TRADER", sc["panic_trader"]),
             ("DIAMOND_HANDS", sc["diamond_hands"])]
    # stable sort: ties keep the listed order (same as the JS sort)
    primary = sorted(cands, key=lambda c: -c[1])[0][0]
    mods = []
    if avg_abs_loss > avg_win * 2.5:
        mods.append("STOP_LOSS_PHOBIA")
    if sc["overtrade"] > 50 and primary != "OVERTRADE":
        mods.append("AVERAGING_DOWN")
    if use:
        if (c["pnl"] < 0 and tr["counter_share"] >= AWAKENING_COUNTER_SHARE
                and (w["avg_pnl"] if w["avg_pnl"] is not None else 0.0) > c["avg_pnl"]):
            mods.append("TREND_AWAKENING")
    elif win_rate > 0.5 and net < 0:
        mods.append("TREND_AWAKENING")
    return primary, mods


def ratings(sc, win_rate, avg_win, avg_abs_loss, tr=None):
    ta = (_jsround(min(10.0, 10 * tr["with_share"])) if trend_usable(tr)
          else _jsround(min(10.0, win_rate * 10 + (2 if avg_win > avg_abs_loss else 0))))
    return {
        "patience": _jsround(max(0.0, 10 - sc["overtrade"] / 12.0)),
        "fomo_resistance": _jsround(max(0.0, 10 - (sc["overtrade"] + sc["panic_trader"]) / 20.0)),
        "stop_loss_discipline": _jsround(max(0.0, 10 - sc["knife_catcher"] / 12.0)),
        "trend_awareness": ta,
        "resilience": _jsround(min(10.0, win_rate * 8 + (2 if sc["trend_master"] > 50 else 0))),
    }


def grade(win_rate, net, liq_rate):
    if win_rate >= 0.55 and net > 0 and liq_rate < 0.01:
        return "S"
    if win_rate >= 0.50 and net > -500 and liq_rate < 0.02:
        return "A"
    if win_rate >= 0.45 or net > 0:
        return "B"
    if win_rate >= 0.40:
        return "C"
    if win_rate >= 0.35 or liq_rate < 0.05:
        return "D"
    return "F"


def labels(lang):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "labels", "%s.json" % lang)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def persona(st, lang="zh", trend=None):
    """st: dict with fills, closed, fpd, win_rate, avg_win, avg_loss (<=0),
    unique_assets, concentration, liq_rate, monthly_fill_counts, net."""
    L = labels(lang)
    if not st.get("fills") or not st.get("closed"):
        code, mods, g = "UNKNOWN", [], "?"
        sc = {k: 0 for k in ("overtrade", "knife_catcher", "scatter_gun", "trend_master",
                             "panic_trader", "diamond_hands")}
        rt = {k: 5 for k in ("patience", "fomo_resistance", "stop_loss_discipline",
                             "trend_awareness", "resilience")}
    else:
        aal = abs(st["avg_loss"])
        sc = scores(st["fpd"], st["win_rate"], st["avg_win"], aal, st["unique_assets"],
                    st["concentration"], st["liq_rate"], volatility(st["monthly_fill_counts"]))
        code, mods = archetype(sc, st["win_rate"], st["net"], st["avg_win"], aal, trend)
        rt = ratings(sc, st["win_rate"], st["avg_win"], aal, trend)
        g = grade(st["win_rate"], st["net"], st["liq_rate"])
    return {
        "archetype": code, "archetype_label": L["archetypes"][code],
        "modifiers": mods, "modifier_labels": [L["modifiers"][m] for m in mods],
        "scores": sc, "ratings": rt,
        "grade": g, "grade_label": L["grades"][g],
        "rating_labels": L["ratings"],
        "trend_basis": "sma" if trend_usable(trend) and st.get("fills") and st.get("closed") else "formula",
    }
