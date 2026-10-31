"""Report text blocks built from script-computed numbers, in en or zh:

- ui(lang)               section headings / column names the Page template shows
- profile_block(sm,lang) the trader profile (Hyperdash-style) per venue, $ and % variants
- tldr(sm, page, lang)   3-5 line TLDR that opens the report and the Page (with several
                         accounts one line names the account that carried and the one that bled,
                         in place of the best/worst single trade line)

The bot never recomputes any of this; it pastes it and adds prose around it.
"""
from . import persona
from .page import money, money_k, num, pct
from .profile import ICONS, STRIP_N

DASH = "-"


def labels(lang):
    return persona.labels(lang)


def ui(lang):
    return labels(lang)["ui"]


def duration(sec, lang):
    P = labels(lang)["profile"]
    if sec is None:
        return DASH
    if sec < 3600:
        return P["minutes"] % num(sec / 60.0)
    if sec < 86400:
        return P["hours"] % num(sec / 3600.0, 1)
    return P["days"] % num(sec / 86400.0, 1)


def _f2(x):
    return DASH if x is None else "{:.2f}".format(x)


def _venue_label(key, lang):
    P = labels(lang)["profile"]
    if key == "all":
        return P["combined"]
    if key == "rh":
        return "Robinhood"
    if key == "hl":
        return "Hyperliquid"
    return "HL " + key[3:]


def _one(pr, lang):
    P = labels(lang)["profile"]
    C = labels(lang)["card"]

    def coh(code, table):
        icon = ICONS.get(code, "") if code else ""
        name = P[table].get(code, DASH) if code else DASH
        return {"code": code, "icon": icon, "value": (icon + " " + name) if icon else name}

    d = pr["direction"]
    pe = pr.get("periods") or {}
    ids = [x for x in ("7d", "30d", "90d", "all") if x in pe]

    def cells(fn):
        return [fn(pe[x]) if pe[x]["enough"] else DASH for x in ids]

    rows = [
        {"id": "net", "label": P["net_pnl"], "usd": cells(lambda q: money(q["net_pnl"], True)),
         "pct": cells(lambda q: pct(q["return_on_volume"], 2) if q["return_on_volume"] is not None else DASH),
         "pct_label": P["return_on_volume"]},
        {"id": "dd", "label": P["max_drawdown"], "usd": cells(lambda q: money(q["max_drawdown"], True)),
         "pct": cells(lambda q: pct(-q["drawdown_of_peak"]) if q["drawdown_of_peak"] is not None else DASH),
         "pct_label": P["drawdown_of_peak"]},
        {"id": "win_rate", "label": P["win_rate"], "usd": cells(lambda q: pct(q["win_rate"])),
         "pct": cells(lambda q: pct(q["win_rate"]))},
        {"id": "sharpe", "label": P["sharpe"], "usd": cells(lambda q: _f2(q["sharpe"])),
         "pct": cells(lambda q: _f2(q["sharpe"]))},
        {"id": "sortino", "label": P["sortino"], "usd": cells(lambda q: _f2(q["sortino"])),
         "pct": cells(lambda q: _f2(q["sortino"]))},
        {"id": "volume", "label": P["volume"], "usd": cells(lambda q: money_k(q["volume"])),
         "pct": cells(lambda q: money_k(q["volume"]))},
        {"id": "trades", "label": P["closed_trades"], "usd": cells(lambda q: num(q["closed_trades"])),
         "pct": cells(lambda q: num(q["closed_trades"]))},
    ]
    vol = pr["volume"] or 0
    top = [
        {"id": "pnl", "label": P["all_time_pnl"], "usd": money_k(pr["all_time_pnl"], True),
         "pct": pct(pr["all_time_pnl"] / vol, 2) if vol else DASH, "pct_label": P["return_on_volume"],
         "exact": money(pr["all_time_pnl"], True),
         "tone": None if not pr["all_time_pnl"] else "success" if pr["all_time_pnl"] > 0 else "danger"},
        {"id": "volume", "label": P["volume"], "usd": money_k(pr["volume"]), "pct": money_k(pr["volume"])},
        {"id": "trades", "label": P["closed_trades"], "usd": num(pr["closed_trades"]), "pct": num(pr["closed_trades"])},
        {"id": "win_rate", "label": P["win_rate"], "usd": pct(pr["win_rate"]), "pct": pct(pr["win_rate"])},
    ]
    facts = [
        {"id": "streak", "label": P["win_streak"],
         "value": P["streak_fmt"] % (num(pr["win_streak_trades"]), num(pr["win_streak_days"]))},
        {"id": "hold", "label": P["duration"],
         "value": P["duration_fmt"] % (duration(pr["hold_avg_s"], lang), duration(pr["hold_median_s"], lang))},
        dict(coh(pr["style"], "styles"), id="style", label=P["style"]),
        dict(coh(pr["pnl_cohort"], "pnl_cohorts"), id="pnl_cohort", label=P["pnl_cohort"]),
        dict(coh(pr["size_cohort"], "size_cohorts"), id="size_cohort", label=P["size_cohort"]),
        dict(coh(d["bias"], "bias"), id="bias", label=P["direction"],
             detail=(P["bias_fmt"] % (pct(d["long_share"]), pct(1 - d["long_share"]))) if d["long_share"] is not None else DASH),
    ]
    return {"top": top, "facts": facts,
            "strip": pr["strip"], "strip_display": "".join("\U0001f7e9" if c == "W" else "\U0001f7e5" for c in pr["strip"]),
            "strip_label": P["strip_label"] % len(pr["strip"]) if pr["strip"] else P["no_trades"],
            "periods": {"ids": ids, "labels": [C["periods"][x] for x in ids], "rows": rows}}


def accounts_line(sm, lang):
    """The TLDR accounts line (None with fewer than two accounts or no winner/loser)."""
    a = sm.get("accounts") or {}
    rows = a.get("rows") or []
    if (a.get("count") or 0) < 2:
        return None
    T = labels(lang)["tldr"]
    win = sorted((r for r in rows if r["net_pnl_incl_funding"] > 0), key=lambda r: (-r["net_pnl_incl_funding"], r["label"]))
    lose = sorted((r for r in rows if r["net_pnl_incl_funding"] < 0), key=lambda r: (r["net_pnl_incl_funding"], r["label"]))

    def lab(r):
        return (r.get("labels") or {}).get(lang) or r["label"]
    v = {"n": num(len(rows))}
    if win:
        v.update(carry=lab(win[0]), carry_pnl=money(win[0]["net_pnl_incl_funding"], True))
    if lose:
        v.update(bleed=lab(lose[0]), bleed_pnl=money(lose[0]["net_pnl_incl_funding"], True))
    if win and lose:
        return T["accounts"].format(**v)
    if win:
        return T["accounts_carry"].format(**v)
    if lose:
        return T["accounts_bleed"].format(**v)
    return None


def profile_block(sm, lang):
    """Venue tabs: all, plus rh and hl when both exist (accounts live in the By-account section)."""
    prof = sm.get("profile") or {}
    keys = ["all"]
    if "rh" in prof and "hl" in prof:
        keys += ["rh", "hl"]
    return {"default": "all", "venues": [{"key": k, "label": _venue_label(k, lang)} for k in keys],
            "by": {k: _one(prof[k], lang) for k in keys if k in prof}, "strip_n": STRIP_N}


def tldr(sm, page, lang):
    T = labels(lang)["tldr"]
    o = sm["overall"]
    lines = []
    if not o["closed_trades"]:
        return [T["no_trades"].format(fills=num(o["fills"]))]
    lines.append(T["headline_1" if o["period"]["days"] == 1 else "headline"].format(net=money(o["net_pnl_incl_funding"], True), days=num(o["period"]["days"]),
                                      start=o["period"]["from"], end=o["period"]["to"]))
    acc = accounts_line(sm, lang)
    if acc:
        lines.append(acc)
    g = page["assets"]["groups"]
    rows = {r["group"]: r for r in g["rows"]}
    b = rows.get(g["best"][0]) if g["best"] else None
    w = rows.get(g["worst"][0]) if g["worst"] else None
    b = b if b and b["tone"] == "success" else None          # a "best" that lost money is not a best
    w = w if w and w["tone"] == "danger" else None
    if b and w:
        lines.append(T["assets"].format(best=b["symbol"], best_pnl=b["net_display"],
                                        worst=w["symbol"], worst_pnl=w["net_display"]))
    elif b:
        lines.append(T["assets_best"].format(best=b["symbol"], best_pnl=b["net_display"]))
    elif w:
        lines.append(T["assets_worst"].format(worst=w["symbol"], worst_pnl=w["net_display"]))
    bt = [t for t in (sm.get("best_trades") or [])[:1] if t["pnl"] > 0]
    wt = [t for t in (sm.get("worst_trades") or [])[:1] if t["pnl"] < 0]
    if acc:
        pass
    elif bt and wt:
        lines.append(T["trades"].format(best=bt[0]["sym"], best_pnl=money(bt[0]["pnl"], True),
                                        worst=wt[0]["sym"], worst_pnl=money(wt[0]["pnl"], True)))
    elif bt:
        lines.append(T["trades_best"].format(best=bt[0]["sym"], best_pnl=money(bt[0]["pnl"], True)))
    elif wt:
        lines.append(T["trades_worst"].format(worst=wt[0]["sym"], worst_pnl=money(wt[0]["pnl"], True)))
    lines.append(T["win_rate_1" if o["closed_trades"] == 1 else "win_rate"].format(win_rate=pct(o["win_rate"]), trades=num(o["closed_trades"]),
                                      payoff=num(o["profit_factor_ratio"], 2)))
    tl = trend_line(sm, lang)
    if tl:
        lines.append(tl)
    bd = ((page.get("card") or {}).get("badges") or {}).get("all") or {}
    per = page["persona"]
    verdict = T["verdict"].format(archetype=per["archetype_label"], grade=per["grade"], grade_label=per["grade_label"])
    if bd.get("name"):
        verdict += T["verdict_sep"] + T["verdict_badge"].format(badge=bd["name"], tagline=bd["tagline"])
    lines.append(verdict)
    return lines[:6]


def trend_line(sm, lang):
    """One TLDR line when trend discipline is notable (trend.notable codes), else None."""
    from .page import trend_vars
    tr = sm.get("trend") or {}
    codes = tr.get("notable") or []
    if tr.get("status") != "ok" or not codes:
        return None
    T = labels(lang)["tldr"]
    V = trend_vars(tr)
    first = {"alt_longs_in_btc_death_cross_lost": "alt_longs"}.get(codes[0], codes[0])
    line = T["trend_" + first].format(**V)
    if first != "alt_longs" and "alt_longs_in_btc_death_cross_lost" in codes:
        line += (" " if lang == "en" else "") + T["trend_alt_longs_tail"].format(**V)
    return line
