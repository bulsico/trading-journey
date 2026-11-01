"""Share cards: a set of three 9:16 story cards (each a 1080x1920 dark SVG, optionally a 2x PNG of
2160x3840) for one report scope and period, in one language.

    lsl card [--out DIR | --data-dir DATA --scope S] [--period all|90d|30d|7d] [--lang en|zh]
             [--png] [--per-account] [--copy copy.json] [--percent-only (no-op, kept for old callers)]
        -> DIR/share-card-1-stats.svg, share-card-2-facts.svg, share-card-3-roast.svg (+ .png and a
           one-PNG stored .zip each) and DIR/share-cards.zip (all PNGs, stored, fixed timestamps);
           with --per-account (several accounts) the same per account in DIR/cards/<slug>-1-stats.svg
           ... and DIR/cards/<slug>-share-cards.zip, from the same compute output.

No dollar amounts, ever: the cards show only percentages, ratios, counts, days and asset names.
  1/3 stats:  header (brand, scope, date range, period), badge + tagline, return on volume (big)
              with an unlabelled equity-shape sparkline, a 6-stat grid, best/worst asset names and
              the win/loss strip of the last trades.
  2/3 facts:  the fun facts (lsl/funfacts.py: most traded, long/short, most loved / hated asset,
              streaks, hold times, busiest day, venue or account split, asset mix, liquidations;
              each hidden when the scope has no data for it).  Text never shrinks to squeeze more
              in: facts that do not fit are dropped (FUN_DROP first, then from the end).  With no
              facts at all the card is left out and the set is 1/2, 2/2.
  3/3 roast:  "trader personality test" (type, roast, trend-discipline line, five score bars),
              "trader rank" (S-F ladder + verdict) and "the AI asks" (three rules and one question
              in a red quote box).  ROAST_TIERS step the body size and spacing down until it fits.
Each card has a footer: brand on the left, "i/n" on the right.  Every text line is wrapped or
fitted with the deterministic width estimate (text_w); layout_problems() checks that nothing leaves
the frame or overlaps (tests run it on every card).

Every number comes from summary.json / series.json.  The persona, scores and rank are all-time
for the card's scope (summary.persona for the combined card, summary.scopes[key].persona for an
account); the stats, badge, strip and sparkline follow the card's period.
"Drawdown / peak" is profile drawdown_of_peak: the largest drop of cumulative P&L, as a share of
the peak cumulative P&L before it (shown only when that peak is positive).

Copy: the roast, verdict, rules and question default to deterministic templates (labels
<lang>.json card.roast / verdict / rules / questions) keyed on archetype, grade and data flags.
`--copy copy.json` (the report language) can replace any of them:
    {"persona": str, "verdict": str, "rules": [str, str, str], "question": str}
Each value is validated (see check_copy): length limits, no "$", and every number in it must be
one the script printed (counts, percentages, ratios, dates; dollar fields excluded).  Text is
wrapped to fit; a value that would not fit is rejected.  A rule's first sentence is its bold lead.

Badge tiers (first match wins; computed for the card's period from the scope's profile; the
P&L sign and thresholds pick the badge but no amount is ever drawn):
  LIQUIDATION_SPEEDRUN  all-time card, net < 0 and >= 3 trades ended in liquidation
  PENNY_PICKER          net < -$100 and win rate >= 50%   (wins often, loses big)
  FEE_FARMER            net < -$100 and trading style is Scalper
  EXIT_LIQUIDITY        net < -$100
  TREADMILL             |net| <= $100
  SNIPER                net > $100 and win rate >= 70%
  DIAMOND_HANDS         net > $100 and style Swing or Position (median hold >= 1 day)
  GRINDER               any other net > $100
Privacy: the card names only the scope label (e.g. "All accounts", "Robinhood Roth IRA ...1234",
"0xabcd...1234"); never a full address or account number.
"""
import json
import os
import re
import shutil
import subprocess

from . import persona
from .util import r2, read_json

W = 1080
M = 64                      # side margin
CW = W - 2 * M              # content width
GREEN, RED, YELLOW, GRAY, DIM, WHITE, BG = "#2ee6a6", "#ff5c7a", "#f5c542", "#8b98a9", "#5d6b7c", "#e9eef5", "#0b0f14"
LINE, BOX = "#1f2a36", "#111821"
# Some rasterizers (cairosvg) only honour the first family, so zh leads with a CJK face
# (which also carries Latin glyphs); browsers walk the whole list either way.
FONTS = {"en": "Inter, 'Noto Sans', 'DejaVu Sans', Arial, 'Noto Sans CJK SC', sans-serif",
         "zh": "'Noto Sans CJK SC', 'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei', 'Source Han Sans SC', "
               "Inter, Arial, sans-serif"}
MONO = {"en": "'IBM Plex Mono', 'JetBrains Mono', Menlo, Consolas, 'DejaVu Sans Mono', monospace",
        "zh": "'Noto Sans Mono CJK SC', 'Noto Sans CJK SC', 'PingFang SC', Menlo, Consolas, monospace"}
EMOJI = "'Noto Color Emoji', 'Apple Color Emoji', 'Segoe UI Emoji', sans-serif"
PERIOD_ORDER = ("all", "90d", "30d", "7d")
SPARK_POINTS = 120
STRIP_MAX = 50
GRADE_LADDER = ("S", "A", "B", "C", "D", "F")
RATING_KEYS = ("patience", "fomo_resistance", "stop_loss_discipline", "trend_awareness", "resilience")
# copy.json limits: characters per field, and the most wrapped lines a field may take
COPY_LIMITS = {"zh": {"persona": 150, "verdict": 70, "rule": 60, "question": 50},
               "en": {"persona": 330, "verdict": 170, "rule": 140, "question": 120}}
COPY_LINES = {"persona": 6, "verdict": 3, "rule": 3, "question": 3}
SMA_PERIODS = (7, 15, 50, 200)


def L(lang):
    return persona.labels(lang)["card"]


def badge(p, prof, liquidated_trades=0, period="all"):
    net, wr = p.get("net_pnl"), p.get("win_rate")
    if net is None:
        return None
    if period == "all" and net < 0 and liquidated_trades >= 3:
        return "LIQUIDATION_SPEEDRUN"
    if net < -100:
        if (wr or 0) >= 0.5:
            return "PENNY_PICKER"
        if prof.get("style") == "SCALPER":
            return "FEE_FARMER"
        return "EXIT_LIQUIDITY"
    if net <= 100:
        return "TREADMILL"
    if (wr or 0) >= 0.7:
        return "SNIPER"
    if prof.get("style") in ("SWING", "POSITION"):
        return "DIAMOND_HANDS"
    return "GRINDER"


# ----------------------------------------------------------------- formatting
def pct(x, plus=False, dp=1):
    if x is None:
        return "-"
    return ("+" if plus and x > 0 else "") + ("{:.%df}%%" % dp).format(x * 100)


def fnum(x):
    return "-" if x is None else "{:.2f}".format(x)


def cnt(x):
    return "-" if x is None else "{:,}".format(int(x))


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _cjk(ch):
    return ord(ch) > 0x2e80


def text_w(s, size, mono=False):
    """Deterministic width estimate (no font metrics in stdlib)."""
    w = 0.0
    for ch in str(s):
        if ch in "\ufe0f\u200d":           # emoji variation selector / joiner: zero width
            continue
        if _cjk(ch):
            w += 1.0
        elif mono:
            w += 0.6
        else:
            w += (0.3 if ch in " .,:;|!'il1()[]" else 0.62 if ch.isdigit() or ch in "$+-%/" else
                  0.68 if ch.isupper() or ch in "mwMW" else 0.56)
    return w * size


NO_START = set("，。、；：！？）」』》%,.;:!?)'\"…·")


def wrap(text, size, width, mono=False):
    """Greedy wrap: CJK breaks between characters, Latin between words.  Closing punctuation never
    starts a line: it pulls the word before it onto the next line (so nothing hangs past `width`);
    only a line that is a single word plus its punctuation may hang."""
    toks = re.findall(r"[\u2e80-\uffff]|[^\s\u2e80-\uffff]+|\s+", str(text))
    lines, cur = [], []

    def s(ts):
        return "".join(ts).strip()
    for t in toks:
        if t.isspace():
            if cur and cur[-1] != " ":
                cur.append(" ")
            continue
        if cur and text_w(s(cur + [t]), size, mono) > width:
            if t[0] not in NO_START:
                lines.append(s(cur))
                cur = [t]
                continue
            words = [i for i, x in enumerate(cur) if x != " "]
            k = len(words) - 1
            while k > 0 and cur[words[k]][0] in NO_START:   # keep a run of closers with their word
                k -= 1
            if k > 0:
                lines.append(s(cur[:words[k]]))
                cur = cur[words[k]:] + [t]
                continue
        cur.append(t)
    if s(cur):
        lines.append(s(cur))
    return lines


def split_lead(s):
    """A rule's bold lead is its first sentence."""
    m = re.match(r"^(.+?[。！？!?]|.+?\.)(\s*)(\S.*)$", s.strip())
    return (m.group(1), m.group(3)) if m else (s.strip(), "")


# ----------------------------------------------------------------- data
def spark(se, scope_key, start, end):
    rows = [r for r in (se.get("cumulative") or {}).get(scope_key) or [] if r["day"] <= end]
    base = 0.0
    for r in rows:
        if r["day"] < start:
            base = r["cumulative"]
    pts = [r2(r["cumulative"] - base) for r in rows if r["day"] >= start]
    if len(pts) > SPARK_POINTS:
        n = len(pts)
        pts = [pts[int(round(i * (n - 1) / (SPARK_POINTS - 1)))] for i in range(SPARK_POINTS)]
    return [0.0] + pts


def account_rows(sm):
    """The per-account rows of a combined report ([] with fewer than two accounts)."""
    a = sm.get("accounts") or {}
    return a.get("rows") or [] if (a.get("count") or 0) > 1 else []


def _persona_codes(sm, key):
    if key == "all":
        p = sm.get("persona") or {}
    else:
        p = ((sm.get("scopes") or {}).get(key) or {}).get("persona") or {}
    if not p.get("archetype"):
        return None
    return {"archetype": p["archetype"], "modifiers": list(p.get("modifiers") or []),
            "ratings": dict(p.get("ratings") or {}), "grade": p.get("grade") or "?"}


def _trend_for(sm, key):
    tr = sm.get("trend") or {}
    if tr.get("status") != "ok":
        return None
    if key == "all":
        t = tr
    else:
        t = (tr.get("by_account") or {}).get(key)
    if not t or not t.get("tagged") or t.get("counter_share") is None:
        return None
    ba = t.get("by_alignment") or {}
    w, c = (ba.get("with") or {}).get("avg_pnl"), (ba.get("counter") or {}).get("avg_pnl")
    out = {"counter_share": t["counter_share"], "counter_worse": None if w is None or c is None else c < w}
    if key == "all" and "alt_longs_in_btc_death_cross_lost" in (tr.get("notable") or []):
        dc = tr.get("alt_longs_in_btc_death_cross") or {}
        out["alt_dc"] = {"trades": dc.get("trades"), "win_rate": dc.get("win_rate")}
    return out


def _assets_bw(sm, key, acct):
    if acct:
        b, w = acct.get("best_asset") or {}, acct.get("worst_asset") or {}
        return (b.get("symbol") if (b.get("net_pnl") or 0) > 0 else None,
                w.get("symbol") if (w.get("net_pnl") or 0) < 0 else None)
    g = sm.get("asset_groups") or {}
    b, w = (g.get("best") or [{}])[0], (g.get("worst") or [{}])[0]
    return (b.get("symbol") if (b.get("net_pnl") or 0) > 0 else None,
            w.get("symbol") if (w.get("net_pnl") or 0) < 0 else None)


def data(sm, se, period="all", key="all"):
    prof = (sm.get("profile") or {}).get(key) or {}
    p = (prof.get("periods") or {}).get(period) or {}
    if not p.get("enough"):
        return None
    scope = sm.get("scope") or {}
    strip = prof.get("strip") or ""
    if period != "all":
        strip = strip[-min(len(strip), p["closed_trades"]):] if p["closed_trades"] else ""
    rows = account_rows(sm)
    acct = next((r for r in rows if r["key"] == key), None) if key != "all" else None
    st = (sm.get("scopes") or {}).get(key) or {} if key != "all" else sm.get("overall") or {}
    if acct:
        label, labels, kind, srcs, n = acct["label"], acct.get("labels") or {}, "account", [acct["venue"]], 0
    else:
        label, labels, kind = scope.get("label"), scope.get("labels") or {}, scope.get("kind", "all")
        srcs = [s for s in ("rh", "hl") if (sm.get("sources") or {}).get(s)]
        n = len(rows) if kind == "all" else 0
    best, worst = _assets_bw(sm, key, acct)
    liq = st.get("liquidated_trades") or 0
    return {"period": period, "p": p, "style": prof.get("style"),
            "badge": badge(p, prof, liq, period), "key": key, "n_accounts": n,
            "scope_label": label, "scope_labels": labels, "scope_kind": kind, "sources": srcs,
            "as_of": (sm.get("as_of") or "")[:10], "strip": strip[-STRIP_MAX:],
            "spark": spark(se, key, p["start"], p["end"]),
            "all_time": {"fills": st.get("fills"), "assets": st.get("unique_assets"),
                         "days": (st.get("period") or {}).get("days"), "fpd": st.get("avg_fills_per_day"),
                         "peak": st.get("peak_fills_in_one_day"), "peak_date": st.get("peak_fills_date"),
                         "closed": st.get("closed_trades"), "win_rate": st.get("win_rate"),
                         "pf": st.get("profit_factor_ratio"), "liq_trades": liq},
            "persona": _persona_codes(sm, key), "trend": _trend_for(sm, key),
            "best_asset": best, "worst_asset": worst,
            "fun": ((sm.get("fun") or {}).get(key) or {}).get(period) or {},
            "account_labels": {r["key"]: dict(r.get("labels") or {}, _=r.get("label")) for r in rows}}


def available(sm, key="all"):
    prof = (sm.get("profile") or {}).get(key) or {}
    return [x for x in PERIOD_ORDER if ((prof.get("periods") or {}).get(x) or {}).get("enough")]


# ----------------------------------------------------------------- template copy
def _vals(d):
    a, tr = d["all_time"], d["trend"] or {}
    fpd = a.get("fpd")
    return {"assets": cnt(a.get("assets")), "fills": cnt(a.get("fills")), "days": cnt(a.get("days")),
            "fpd": "-" if fpd is None else "{:.1f}".format(fpd), "peak": cnt(a.get("peak")),
            "peak_date": a.get("peak_date") or "", "trades": cnt(a.get("closed")),
            "win_rate": pct(a.get("win_rate")), "pf": fnum(a.get("pf")), "liq": cnt(a.get("liq_trades")),
            "pct": pct(tr.get("counter_share"))}


def rule_codes(d):
    """Data flags behind the default rules, in priority order (first three are used)."""
    a, tr = d["all_time"], d["trend"] or {}
    arche = (d["persona"] or {}).get("archetype")
    codes = []
    if tr.get("alt_dc") and tr["alt_dc"].get("trades"):
        codes.append("ALT_DC")
    if (a.get("liq_trades") or 0) > 0:
        codes.append("LIQ")
    if (tr.get("counter_share") or 0) >= 0.25:
        codes.append("COUNTER")
    if a.get("pf") is not None and a["pf"] < 1:
        codes.append("LOW_PF")
    if (a.get("fpd") or 0) >= 5 or arche == "OVERTRADE":
        codes.append("OVERTRADE")
    if (a.get("assets") or 0) >= 30:
        codes.append("SCATTER")
    if d.get("worst_asset"):
        codes.append("WORST")
    return (codes + ["JOURNAL", "SIZE", "REVIEW"])[:3]


def template_copy(d, lang):
    T, v = L(lang), _vals(d)
    pz = d["persona"] or {"archetype": "UNKNOWN", "grade": "?"}
    roast = T["roast"][pz["archetype"]].format(**v)
    if pz["archetype"] != "UNKNOWN" and d["all_time"].get("peak") and d["all_time"].get("peak_date"):
        roast += ("" if lang == "zh" else " ") + T["roast_peak"].format(**v)
    codes = rule_codes(d)
    tr = d["trend"] or {}
    dc = tr.get("alt_dc") or {}
    rv = dict(v, n=v["liq"], asset=d.get("worst_asset") or "")
    rules = []
    for c in codes:
        if c == "ALT_DC":
            rules.append(T["rules"][c].format(**dict(rv, n=cnt(dc.get("trades")), wr=pct(dc.get("win_rate")))))
        else:
            rules.append(T["rules"][c].format(**rv))
    q = T["questions"].get(codes[0], T["questions"]["DEFAULT"])
    return {"persona": roast, "verdict": T["verdict"][pz["grade"]].format(**v), "rules": rules, "question": q,
            "source": "template", "rule_codes": codes}


# ----------------------------------------------------------------- copy.json validation
MONEY_KEY = re.compile(r"pnl|fee|funding|volume$|drawdown$|notional|price|cumulative|cost|avg_win|avg_loss|"
                       r"biggest|^net|equity|^values$|ceiling|floor|amount|balance|value$|_usd|deposit|withdraw|"
                       r"cash|size|^avg$|^loss|^gain|_s$|^ts$|^hold")
DATE_RE = re.compile(r"\d{4}-\d{2}(?:-\d{2})?")
NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _num_forms(v):
    out = set()
    if isinstance(v, bool):
        return out
    if isinstance(v, int):
        out.add(str(v))
    elif isinstance(v, float):
        for dp in (0, 1, 2):
            out.add(("{:.%df}" % dp).format(abs(v)))
        out.add(repr(abs(v)))
        for dp in (0, 1, 2):
            out.add(("{:.%df}" % dp).format(abs(v) * 100))
        if v == int(v):
            out.add(str(abs(int(v))))
    return {x.lstrip("-") for x in out}


def allowed_numbers(sm):
    """Every count, ratio, percentage and date the script printed (dollar-valued fields excluded)."""
    nums, dates = set(str(i) for i in range(11)) | {str(x) for x in SMA_PERIODS}, set()

    def walk(o, money=False):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, money or bool(MONEY_KEY.search(str(k))))
        elif isinstance(o, list):
            for v in o:
                walk(v, money)
        elif isinstance(o, str):
            for m in DATE_RE.findall(o):
                dates.add(m)
                dates.add(m[:7])
        elif not money and isinstance(o, (int, float)):
            nums.update(_num_forms(o))
    walk(sm)
    return nums, dates


def check_text(s, allowed, lang, field, width, size):
    nums, dates = allowed
    probs = []
    if not isinstance(s, str) or not s.strip():
        return ["%s: must be a non-empty string" % field]
    if "$" in s or "\uff04" in s:
        probs.append("%s: contains '$' (no dollar amounts on the card)" % field)
    lim = COPY_LIMITS[lang]["rule" if field.startswith("rules") else field]
    if len(s) > lim:
        probs.append("%s: %d characters (limit %d)" % (field, len(s), lim))
    rest = s
    for m in DATE_RE.findall(s):
        if m not in dates:
            probs.append("%s: date %s is not in the script output" % (field, m))
        rest = rest.replace(m, " ")
    for m in NUM_RE.findall(rest):
        n = m.replace(",", "")
        if n not in nums:
            probs.append("%s: number %s is not in the script output" % (field, m))
    nlines = len(wrap(s, size, width))
    if nlines > COPY_LINES["rule" if field.startswith("rules") else field]:
        probs.append("%s: wraps to %d lines (limit %d)" % (field, nlines, COPY_LINES["rule" if field.startswith("rules") else field]))
    return probs


def check_copy(copy, sm, lang):
    """-> (clean copy dict, problems).  Accepts keys persona, verdict, rules (exactly 3), question;
    any may be omitted (the template fills it).  A {"zh": {...}, "en": {...}} file uses its `lang` part."""
    if not isinstance(copy, dict):
        return None, ["copy must be a JSON object"]
    if lang in copy and isinstance(copy[lang], dict):
        copy = copy[lang]
    probs, out = [], {}
    allowed = allowed_numbers(sm)
    for k in copy:
        if k not in ("persona", "verdict", "rules", "question"):
            probs.append("unknown key %r (allowed: persona, verdict, rules, question)" % k)
    for k in ("persona", "verdict", "question"):
        if k in copy:
            probs += check_text(copy[k], allowed, lang, k, *_SLOT[k])
            out[k] = str(copy[k]).strip()
    if "rules" in copy:
        r = copy["rules"]
        if not isinstance(r, list) or len(r) != 3:
            probs.append("rules: must be a list of exactly 3 strings")
        else:
            for i, s in enumerate(r):
                probs += check_text(s, allowed, lang, "rules[%d]" % i, *_SLOT["rule"])
            out["rules"] = [str(s).strip() for s in r]
    return (None if probs else out), probs


def load_copy(path, sm, lang):
    try:
        with open(path, encoding="utf-8") as f:
            c = json.load(f)
    except Exception as e:  # noqa: BLE001
        return None, ["cannot read %s: %s" % (path, e)]
    return check_copy(c, sm, lang)


# text slots: (width, font size) used both to render and to validate wrapping
BODY, SMALL = 30, 25
_SLOT = {"persona": (CW, BODY), "verdict": (CW, BODY), "rule": (CW - 56, BODY - 2), "question": (CW - 64, BODY + 2)}


# ----------------------------------------------------------------- fun facts
FUN_ORDER = ("most_traded", "long_short", "loved", "hated", "streaks", "time_held", "busiest_day", "weekday",
             "venues", "top_account", "classes", "liquidated")
FUN_MAX = 10
FUN_DROP = ("weekday", "classes", "busiest_day")     # dropped first when more than FUN_MAX are available


def _dur(x, T):
    if x["unit"] == "m" and not x["num"]:
        return T["units"]["lt1"]
    return T["units"][x["unit"]].format(n=x["num"])


def fun_cells(d, lang):
    """[(key, emoji, label, value, sub or None)] for the facts this scope/period has; missing ones are
    simply absent.  Only counts, shares, durations, dates and names."""
    T, f = L(lang), d.get("fun") or {}
    Fm = T["fun_fmt"]

    def ntr(n):
        return Fm["trades_1"] if n == 1 else Fm["trades_n"].format(n=cnt(n))

    cells = {}
    if "most_traded" in f:
        x = f["most_traded"]
        cells["most_traded"] = (Fm["most_traded"].format(ticker=x["ticker"], trades=ntr(x["trades"]),
                                                         share=pct(x["share"], dp=0)), None)
    if "long_short" in f:
        x = f["long_short"]
        cells["long_short"] = (Fm["long_short"].format(long=pct(x["long_share"], dp=0),
                                                       short=pct(x["short_share"], dp=0)), None)
    for k in ("loved", "hated"):
        if k in f:
            x = f[k]
            cells[k] = (Fm["asset_wr"].format(ticker=x["ticker"], wr=pct(x["win_rate"], dp=0),
                                              trades=ntr(x["trades"])), None)
    if "streaks" in f:
        x = f["streaks"]
        cells["streaks"] = (Fm["streaks"].format(w=x["streak_w"], l=x["streak_l"]), None)
    if "time_held" in f:
        x = f["time_held"]
        cells["time_held"] = (_dur(x["median_hold"], T),
                          Fm["hold_sub"].format(lo=_dur(x["shortest_hold"], T), hi=_dur(x["longest_hold"], T)))
    if "busiest_day" in f:
        x = f["busiest_day"]
        cells["busiest_day"] = (Fm["busiest_day"].format(date=x["date"], fills=cnt(x["fills"])), None)
    if "weekday" in f:
        x = f["weekday"]
        cells["weekday"] = (Fm["weekday"].format(day=T["weekdays"][x["weekday"]], share=pct(x["share"], dp=0)), None)
    if "venues" in f:
        cells["venues"] = (" \u00b7 ".join("%s %s" % (T["venues"][r["venue"]], pct(r["share"], dp=0))
                                           for r in f["venues"]["rows"]), None)
    if "top_account" in f:
        x = f["top_account"]
        lab = (d.get("account_labels") or {}).get(x["account"]) or {}
        name = lab.get(lang) or lab.get("_")
        if name:
            cells["top_account"] = (name, Fm["top_account_sub"].format(share=pct(x["share"], dp=0),
                                                                       n=x["accounts"]))
    if "classes" in f:
        cells["classes"] = (" \u00b7 ".join("%s %s" % (T["classes"][r["cls"]], pct(r["share"], dp=0))
                                            for r in f["classes"]["rows"]), None)
    if "liquidated" in f:
        n = f["liquidated"]["trades"]
        cells["liquidated"] = (Fm["liq_0"] if n == 0 else Fm["liq_1"] if n == 1 else
                               Fm["liq_n"].format(n=cnt(n)), None)
    keys = [k for k in FUN_ORDER if k in cells]
    for k in FUN_DROP:
        if len(keys) > FUN_MAX and k in keys:
            keys.remove(k)
    out = []
    for k in keys[:FUN_MAX]:
        em, lab = T["fun"][k]
        v, sub = cells[k]
        out.append((k, em, lab, v.replace("$", ""), sub.replace("$", "") if sub else None))
    return out


def fit(s, size, width, min_size, mono=False, bold=False):
    """Largest font size >= min_size that fits; past that, trim with an ellipsis.  Bold text runs
    ~12% wider than text_w's regular-weight estimate."""
    k = 1.12 if bold else 1.0

    def w(t, z):
        return text_w(t, z, mono) * k
    sz = size
    while sz > min_size and w(s, sz) > width:
        sz -= 1
    if w(s, sz) <= width:
        return s, sz
    while s and w(s + "\u2026", sz) > width:
        s = s[:-1]
    return s.rstrip() + "\u2026", sz


# ----------------------------------------------------------------- svg
H = 1920                    # every card is a 9:16 story frame: 1080 x 1920 (PNG at 2x: 2160 x 3840)
FOOT_LINE = H - 112         # footer rule; content must end above Y_MAX
Y_MAX = FOOT_LINE - 36
CARD_IDS = ("share-card-1-stats", "share-card-2-facts", "share-card-3-roast")
BOLD_K = 1.12               # bold text runs ~12% wider than text_w's regular-weight estimate


class _Out:
    def __init__(self):
        self.o = []

    def __call__(self, s):
        self.o.append(s)

    def text(self, x, y, s, size, fill, weight=None, anchor=None, mono=False, extra=""):
        self.o.append('<text x="%s" y="%s" font-size="%d"%s%s%s fill="%s"%s>%s</text>' % (
            _n(x), _n(y), size, ' font-weight="%s"' % weight if weight else "",
            ' text-anchor="%s"' % anchor if anchor else "", ' class="m"' if mono else "", fill, extra, esc(s)))

    def emoji(self, x, y, em, size):
        self.o.append('<text x="%s" y="%s" font-size="%d" font-family="%s">%s</text>' % (
            _n(x), _n(y), size, esc(EMOJI), em))

    def para(self, lines, y, size, fill, lh, x=M, weight=None):
        for ln in lines:
            self.text(x, y, ln, size, fill, weight)
            y += lh
        return y


def _n(x):
    return ("%.1f" % x).rstrip("0").rstrip(".")


def _bar_color(v):
    return GREEN if v >= 7 else YELLOW if v >= 4 else RED


def _bw(s, size, mono=False, bold=False):
    return text_w(s, size, mono) * (BOLD_K if bold else 1.0)


def _clamp_lines(lines, n, size, width, bold=False):
    """At most n lines; a cut last line ends with an ellipsis (deterministic)."""
    if len(lines) <= n:
        return lines
    last = lines[n - 1]
    while last and _bw(last + "\u2026", size, bold=bold) > width:
        last = last[:-1]
    return lines[:n - 1] + [last.rstrip() + "\u2026"]


def _frame(body, lang, idx, total, T):
    """Wrap a card body: background, footer (brand left, "i/n" right) and the svg element."""
    o = _Out()
    o('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s" stroke-width="2"/>' % (M, FOOT_LINE, W - M, FOOT_LINE, LINE))
    ft, fs = fit(T["footer"], 28, CW - 140, 26, mono=True, bold=True)
    o.text(M, FOOT_LINE + 62, ft, fs, DIM, 700, mono=True, extra=' letter-spacing="1"')
    o.text(W - M, FOOT_LINE + 62, "%d/%d" % (idx, total), 30, YELLOW, 800, "end", mono=True)
    head = ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" font-family="%s">'
            '<style>.m{font-family:%s}</style>' % (W, H, W, H, esc(FONTS.get(lang, FONTS["en"])),
                                                      MONO.get(lang, MONO["en"]).replace("'", "")))
    bg = ('<rect width="%d" height="%d" rx="28" fill="%s"/>'
          '<rect x="1" y="1" width="%d" height="%d" rx="27" fill="none" stroke="#1d2733" stroke-width="2"/>'
          % (W, H, BG, W - 2, H - 2))
    return "\n".join([head, bg] + body + o.o + ["</svg>"]) + "\n"


def _top(o, d, lang, T):
    """Brand mark + name on the left, period pill on the right (every card)."""
    per = T["periods"][d["period"]]
    o('<rect x="%d" y="56" width="44" height="44" rx="10" fill="%s"/>' % (M, YELLOW))
    o.text(M + 8, 87, ">_", 26, BG, 800, mono=True)
    pw = text_w(per, 28, True) * BOLD_K + 44
    bt, bs = fit(T["brand"].upper() if lang == "en" else T["brand"], 34, CW - pw - 90, 28, mono=True, bold=True)
    o.text(M + 64, 92, bt, bs, WHITE, 800, mono=True, extra=' letter-spacing="2"')
    o('<rect x="%s" y="54" width="%s" height="50" rx="25" fill="none" stroke="%s" stroke-width="2"/>'
      % (_n(W - M - pw), _n(pw), YELLOW))
    o.text(W - M - pw / 2, 89, per, 28, YELLOW, 700, "middle", mono=True)
    return 104


def _section(o, y, T, key, size=44):
    em, title = T[key]
    o.emoji(M, y, em, size)
    tt, ts = fit(title, size, CW - size - 20, 30, bold=True)
    o.text(M + size + 18, y, tt, ts, YELLOW, 800)


# ---- card 1: stats
def _card_stats(d, lang, gap):
    T = L(lang)
    p = d["p"]
    col = GREEN if (p.get("net_pnl") or 0) >= 0 else RED
    o = _Out()
    _top(o, d, lang, T)
    scope = d["scope_labels"].get(lang) or d["scope_label"] or T["all_accounts"]
    src = " + ".join({"rh": "Robinhood", "hl": "Hyperliquid"}[s] for s in d["sources"])
    sub = "%s \u00b7 %s" % (scope, src) if d["scope_kind"] == "all" and src else scope
    if d.get("n_accounts", 0) > 1:
        sub += " \u00b7 " + T["n_accounts"] % d["n_accounts"]
    y = o.para(_clamp_lines(wrap(sub, 32, CW), 2, 32, CW), 166, 32, GRAY, 44)
    when = "%s \u00b7 %s" % (T["window"] % (p["start"], p["end"]), T["as_of"] % d["as_of"])
    y = o.para(_clamp_lines(wrap(when, 28, CW, mono=True), 2, 28, CW), y + 2, 28, DIM, 40)
    y += gap - 30
    # badge + tagline
    if d["badge"]:
        name, tag = T["badges"][d["badge"]]["name"], T["badges"][d["badge"]]["tagline"]
        nt, ns = fit(name, 38, CW - 56, 28, bold=True)
        bw = _bw(nt, ns, bold=True) + 56
        o('<rect x="%d" y="%s" width="%s" height="68" rx="34" fill="%s" fill-opacity="0.14" stroke="%s" '
          'stroke-width="2"/>' % (M, _n(y), _n(bw), col, col))
        o.text(M + 28, y + 47, nt, ns, col, 800)
        y += 68 + 54
        y = o.para(_clamp_lines(wrap(tag, 34, CW), 2, 34, CW), y, 34, GRAY, 46) - 46 + gap
    # headline number + sparkline
    per = T["periods"][d["period"]]
    rov = p.get("return_on_volume")
    lt, ls = fit("%s \u00b7 %s" % (T["return_on_volume"], per), 32, CW, 26)
    o.text(M, y + 10, lt, ls, GRAY)
    big = pct(rov, True, 2) if rov is not None else pct(p.get("win_rate"))
    bt, bs = fit(big, 156, CW, 96, mono=True, bold=True)
    o.text(M - 4, y + 10 + 158, bt, bs, col if rov is not None else WHITE, 800, mono=True)
    y += 10 + 158 + 40
    sx0, sx1, sy0, sy1 = M, W - M, y, y + 190
    sp = d["spark"]
    lo, hi = min(sp), max(sp)
    rng = (hi - lo) or 1.0
    pts = []
    for i, v in enumerate(sp):
        x = sx0 + (sx1 - sx0) * (i / (len(sp) - 1) if len(sp) > 1 else 0)
        pts.append("%.1f,%.1f" % (x, sy1 - (sy1 - sy0) * ((v - lo) / rng)))
    zy = sy1 - (sy1 - sy0) * ((0 - lo) / rng)
    o('<line x1="%s" y1="%.1f" x2="%s" y2="%.1f" stroke="#2a3644" stroke-width="2" stroke-dasharray="6 8"/>'
      % (_n(sx0), zy, _n(sx1), zy))
    o('<polyline points="%s" fill="none" stroke="%s" stroke-width="5" stroke-linejoin="round" stroke-linecap="round"/>'
      % (" ".join(pts), col))
    y = sy1 + gap
    # stats grid
    a = d["all_time"]
    dd = p.get("drawdown_of_peak")
    wr_lab = T["win_rate"].capitalize() if lang == "en" else T["win_rate"]
    if d["period"] == "all":
        stats = [(wr_lab, pct(p.get("win_rate"))), (T["profit_factor"], fnum(a.get("pf"))),
                 (T["closed"], cnt(p.get("closed_trades"))), (T["assets"], cnt(a.get("assets"))),
                 (T["active_days"], cnt(p.get("active_days"))), (T["dd_of_peak"], pct(-dd) if dd is not None else "-")]
    else:
        stats = [(wr_lab, pct(p.get("win_rate"))), (T["sharpe"], fnum(p.get("sharpe"))),
                 (T["closed"], cnt(p.get("closed_trades"))), (T["active_days"], cnt(p.get("active_days"))),
                 (T["sortino"], fnum(p.get("sortino"))), (T["dd_of_peak"], pct(-dd) if dd is not None else "-")]
    cw3 = CW / 3.0
    o('<rect x="%d" y="%s" width="%d" height="332" rx="22" fill="%s" stroke="%s" stroke-width="2"/>'
      % (M, _n(y), CW, BOX, LINE))
    for i, (lab, val) in enumerate(stats):
        cx, cy = M + 30 + (i % 3) * cw3, y + 64 + (i // 3) * 154
        lt, ls = fit(lab, 30, cw3 - 44, 26)
        o.text(cx, cy, lt, ls, GRAY)
        vt, vs = fit(val, 56, cw3 - 44, 34, mono=True, bold=True)
        o.text(cx, cy + 68, vt, vs, WHITE, 700, mono=True)
    y += 332 + gap
    # best / worst asset
    bits = []
    if d.get("best_asset"):
        bits.append((T["best_asset"], d["best_asset"], GREEN))
    if d.get("worst_asset"):
        bits.append((T["worst_asset"], d["worst_asset"], RED))
    if bits:
        half = CW / 2.0
        for j, (lab, name, c) in enumerate(bits):
            x = M + j * half
            lt, ls = fit(lab, 34, half * 0.4, 26)
            o.text(x, y + 36, lt, ls, GRAY)
            x += _bw(lt, ls) + 16
            nt, ns = fit(name, 48, M + (j + 1) * half - 24 - x, 28, mono=True, bold=True)
            o.text(x, y + 38, nt, ns, c, 800, mono=True)
        y += 48 + gap
    # recent win/loss strip: up to 2 rows of 25
    strip = d["strip"]
    if strip:
        o.text(M, y + 22, T["last_trades"] % len(strip), 30, DIM)
        y += 22 + 26
        per_row = 25
        step = CW / float(per_row)
        for i, ch in enumerate(strip):
            r, c = divmod(i, per_row)
            o('<rect x="%s" y="%s" width="%s" height="40" rx="6" fill="%s"/>'
              % (_n(M + c * step), _n(y + r * 54), _n(step - 8), GREEN if ch == "W" else RED))
        y += 54 * ((len(strip) - 1) // per_row) + 40
    return o.o, y


# ---- card 2: fun facts
FACT_ROW, FACT_GAP, FACT_LINE, FACT_SUB = 128, 16, 52, 44
FACT_TOP = 256


def _fact_layout(cells, lang):
    """Each fact as a full-width row: emoji, label, the value (bold; a long " · " list continues on a
    second line), optional sub line.  -> [(cell, value, cont, value_size, row_height)]"""
    vw = CW - 120
    out = []
    for (k, em, lab, val, sub) in cells:
        cont = None
        if _bw(val, 36, bold=True) > vw and " \u00b7 " in val:   # down to 36 on one line, else a second line
            parts = val.split(" \u00b7 ")
            n = len(parts) - 1
            while n > 1 and _bw(" \u00b7 ".join(parts[:n]), 42, bold=True) > vw:
                n -= 1
            val, cont = " \u00b7 ".join(parts[:n]), " \u00b7 ".join(parts[n:])
        vt, vs = fit(val, 42, vw, 32, bold=True)
        if cont:
            ct, cs = fit(cont, 42, vw, 32, bold=True)
            vs = min(vs, cs)
        rh = FACT_ROW + (FACT_LINE if cont else 0) + (FACT_SUB if sub else 0)
        out.append(((k, em, lab, val, sub), val, cont, vs, rh))
    return out


def fact_keys(d, lang):
    """The fun facts that fit on the facts card at full size, in FUN_ORDER (the rest are dropped,
    FUN_DROP first, then from the end: text is never shrunk to squeeze more in)."""
    cells = fun_cells(d, lang)
    lay = _fact_layout(cells, lang)

    def height(rows):
        return sum(r[4] for r in rows) + FACT_GAP * max(0, len(rows) - 1)
    keys = [r[0][0] for r in lay]
    drop = [k for k in FUN_DROP if k in keys] + list(reversed(keys))
    while lay and FACT_TOP + height(lay) > Y_MAX:
        k = next(k for k in drop if k in [r[0][0] for r in lay])
        lay = [r for r in lay if r[0][0] != k]
    return lay


def _card_facts(d, lang):
    T = L(lang)
    o = _Out()
    _top(o, d, lang, T)
    _section(o, 196, T, "h_fun", 46)
    lay = fact_keys(d, lang)
    used = sum(r[4] for r in lay) + FACT_GAP * max(0, len(lay) - 1)
    gap = FACT_GAP + min(24, (Y_MAX - FACT_TOP - used) // max(1, len(lay) - 1)) if len(lay) > 1 else FACT_GAP
    y = FACT_TOP
    for (k, em, lab, _v, sub), val, cont, vs, rh in lay:
        o('<rect x="%d" y="%s" width="%d" height="%d" rx="20" fill="%s" stroke="%s" stroke-width="2"/>'
          % (M, _n(y), CW, rh, BOX, LINE))
        o.emoji(M + 26, y + 64, em, 46)
        lt, ls = fit(lab, 30, CW - 140, 26)
        o.text(M + 100, y + 48, lt, ls, GRAY)
        vt, _s = fit(val, vs, CW - 120, vs, bold=True)
        o.text(M + 100, y + 101, vt, vs, WHITE, 800)
        yy = y + 101
        if cont:
            ct, _s = fit(cont, vs, CW - 120, vs, bold=True)
            yy += FACT_LINE
            o.text(M + 100, yy, ct, vs, WHITE, 800)
        if sub:
            st_, ss = fit(sub, 30, CW - 120, 26)
            o.text(M + 100, yy + FACT_SUB, st_, ss, DIM)
        y += rh + gap
    return o.o, y - gap


# ---- card 3: persona, rank, the AI asks
# Fitting tiers, tried in order until the card ends above the footer: (body size, vertical ladder,
# keep trend line, keep intros, max persona lines, max lines per rule explanation).  Validation (_SLOT) guarantees the last fits.
# A bigger body beats the vertical ladder: at each size the full ladder first, then the compact chips.
# The two one-line intros above the rules and the question go before the body shrinks below 32.
# Last resorts for copy at its validated limits: clamp the persona, then each rule's explanation
# (after its bold lead) to fewer lines with an ellipsis; finally body 28 with 2-line verdict/question.
ROAST_TIERS = ((36, True, True, True, 9, 9), (36, False, True, True, 9, 9), (34, True, True, True, 9, 9),
               (34, False, True, True, 9, 9), (34, False, True, False, 9, 9), (32, True, True, True, 9, 9),
               (32, False, True, True, 9, 9), (32, False, True, False, 9, 9),
               (30, True, True, True, 9, 9), (30, False, True, True, 9, 9), (30, False, False, True, 9, 9),
               (30, False, False, False, 9, 9), (30, False, False, False, 5, 9), (30, False, False, False, 5, 1),
               (28, False, False, False, 4, 1), (28, False, False, False, 3, 1),
               (28, False, False, False, 4, 0))


def _card_roast(d, lang, cp, tier, spare=0):
    T, Lb = L(lang), persona.labels(lang)
    body, ladder, keep_trend, intros, max_pl, max_rest = tier
    last = max_rest == 0
    small = max(26, body - 6)
    lhb = int(round(body * 1.4))
    o = _Out()
    _top(o, d, lang, T)
    pz = d["persona"]
    y = 190
    _section(o, y, T, "h_persona", 42)
    y += 70
    if pz:
        tl = Lb["archetypes"][pz["archetype"]] + "".join(("" if lang == "zh" else " ") + Lb["modifiers"][m]
                                                         for m in pz["modifiers"])
        big = body + 8
        if _bw(T["type_prefix"], body) + _bw(tl, big, bold=True) <= CW:
            o('<text x="%d" y="%s" font-size="%d" fill="%s">%s<tspan font-size="%d" font-weight="800" fill="%s">%s'
              '</tspan></text>' % (M, _n(y), body, WHITE, esc(T["type_prefix"]), big, YELLOW, esc(tl)))
            y += big + 26
        else:
            o.text(M, y, T["type_prefix"], body, WHITE)
            y += lhb
            y = o.para(_clamp_lines(wrap(tl, big, CW / BOLD_K), 2, big, CW, True), y, big, YELLOW, big + 12,
                       weight=800) + 14
    y = o.para(_clamp_lines(wrap(cp["persona"], body, CW), max_pl, body, CW), y, body, WHITE, lhb)
    if d["period"] != "all":
        y = o.para(_clamp_lines(wrap(T["persona_all_time"], 26, CW), 2, 26, CW), y, 26, DIM, 36)
    tr = d["trend"]
    if tr and keep_trend:
        s = T["trend_line"].format(pct=pct(tr["counter_share"]))
        if tr.get("counter_worse") is not None:
            s += ("" if lang == "zh" else " ") + T["trend_worse" if tr["counter_worse"] else "trend_better"]
        y += 6
        for ln in _clamp_lines(wrap(s, small, CW - 30), 2, small, CW - 30):
            o('<rect x="%d" y="%s" width="6" height="%d" fill="%s"/>' % (M, _n(y - small + 2), small + 4, YELLOW))
            o.text(M + 22, y, ln, small, GRAY)
            y += small + 12
    if pz:
        y += 4 + spare // 2
        rk = [k for k in RATING_KEYS if k in pz["ratings"]]
        rs = body - 2
        rh = rs + 24
        bh = 28 + len(rk) * rh
        o('<rect x="%d" y="%s" width="%d" height="%d" rx="20" fill="%s" stroke="%s" stroke-width="2"/>'
          % (M, _n(y), CW, bh, BOX, LINE))
        lw = min(max(_bw(Lb["ratings"][k], rs) for k in rk), CW * 0.45) if rk else 0
        bx0 = M + 28 + lw + 24
        bx1 = W - M - 28 - text_w("10/10", rs, True) * BOLD_K - 20
        for i, k in enumerate(rk):
            v = int(pz["ratings"][k])
            cy = y + 14 + i * rh + rh / 2.0 + rs * 0.36
            c = _bar_color(v)
            lt, ls = fit(Lb["ratings"][k], rs, lw, 26)
            o.text(M + 28, cy, lt, ls, WHITE)
            o('<rect x="%s" y="%s" width="%s" height="22" rx="11" fill="#1d2632"/>'
              % (_n(bx0), _n(cy - rs * 0.36 - 11), _n(bx1 - bx0)))
            if v > 0:
                o('<rect x="%s" y="%s" width="%s" height="22" rx="11" fill="%s"/>'
                  % (_n(bx0), _n(cy - rs * 0.36 - 11), _n((bx1 - bx0) * v / 10.0), c))
            o.text(W - M - 28, cy, "%d/10" % v, rs, c, 700, "end", mono=True)
        y += bh
    # ---- rank
    y += 62 + spare
    _section(o, y, T, "h_grade", 40)
    y += 22
    g = (pz or {}).get("grade") or "?"
    if ladder:
        lh = body + 14
        o('<rect x="%d" y="%s" width="%d" height="%d" rx="20" fill="%s" stroke="%s" stroke-width="2"/>'
          % (M, _n(y), CW, 16 + lh * len(GRADE_LADDER), BOX, LINE))
        here_w = _bw(T["you_are_here"], 26, bold=True)
        for i, gg in enumerate(GRADE_LADDER):
            ry = y + 8 + i * lh
            me = gg == g
            if me:
                o('<rect x="%d" y="%s" width="%d" height="%d" rx="12" fill="%s" fill-opacity="0.16" stroke="%s" '
                  'stroke-width="2"/>' % (M + 10, _n(ry), CW - 20, lh, YELLOW, YELLOW))
            base = ry + lh / 2.0 + body * 0.36
            o.text(M + 36, base, gg, body, YELLOW if me else DIM, 800, mono=True)
            avail = CW - 96 - 28 - (here_w + 24 if me else 0)
            gt, gs = fit(Lb["grades"][gg], max(26, body - 4), avail, 26, bold=me)
            o.text(M + 96, base, gt, gs, WHITE if me else GRAY, 700 if me else None)
            if me:
                o.text(W - M - 28, base, T["you_are_here"], 26, YELLOW, 700, "end")
        y += 16 + lh * len(GRADE_LADDER) + 50
    else:
        gap = 12
        cw = (CW - gap * (len(GRADE_LADDER) - 1)) / float(len(GRADE_LADDER))
        for i, gg in enumerate(GRADE_LADDER):
            me = gg == g
            cx = M + i * (cw + gap)
            o('<rect x="%s" y="%s" width="%s" height="76" rx="14" fill="%s"%s stroke="%s" stroke-width="2"/>'
              % (_n(cx), _n(y), _n(cw), YELLOW if me else BOX, ' fill-opacity="0.16"' if me else "",
                 YELLOW if me else LINE))
            o.text(cx + cw / 2.0, y + 52, gg, 40, YELLOW if me else DIM, 800, "middle", mono=True)
        y += 76 + 52
    gl, gs = fit(T["grade_line"].format(grade=g, label=Lb["grades"][g]), body + 2, CW, 26, bold=True)
    o.text(M, y, gl, gs, YELLOW, 800)
    y += lhb + 4
    vl = wrap(cp["verdict"], body, CW)
    y = o.para(_clamp_lines(vl, 2, body, CW) if last else vl, y, body, WHITE, lhb) - lhb
    # ---- the AI asks
    y += 86 + spare
    _section(o, y, T, "h_soul", 40)
    y += 64
    if intros:
        y = o.para(wrap(T["soul_intro"], small, CW), y, small, GRAY, small + 12) + 4
    rb, rr = body - 2, max(26, body - 4)
    for i, r in enumerate(cp["rules"][:3]):
        lead, rest = split_lead(r)
        o.text(M, y, "%d." % (i + 1), rb, YELLOW, 800, mono=True)
        ll = wrap(lead, rb, (CW - 56) / BOLD_K)
        if max_rest < 9:
            ll = _clamp_lines(ll, 2, rb, (CW - 56) / BOLD_K)
        y = o.para(ll, y, rb, WHITE, rb + 12, x=M + 56, weight=800)
        if rest and max_rest:
            y = o.para(_clamp_lines(wrap(rest, rr, CW - 56), max_rest, rr, CW - 56), y, rr, GRAY, rr + 12, x=M + 56)
        y += 10
    if intros:
        y += 4
        y = o.para(wrap(T["question_intro"], small, CW), y, small, GRAY, small + 12)
    y -= small - 4
    qs = body + 2
    ql = wrap(cp["question"], qs, (CW - 72) / BOLD_K)
    if last:
        ql = _clamp_lines(ql, 2, qs, (CW - 72) / BOLD_K)
    qh = 36 + len(ql) * (qs + 14)
    y += 10
    o('<rect x="%d" y="%s" width="%d" height="%d" rx="14" fill="%s" fill-opacity="0.08"/>' % (M, _n(y), CW, qh, RED))
    o('<rect x="%d" y="%s" width="8" height="%d" rx="2" fill="%s"/>' % (M, _n(y), qh, RED))
    o.para(ql, y + 18 + qs, qs, WHITE, qs + 14, x=M + 36, weight=800)
    y += qh
    return o.o, y


def _copy_for(d, lang, copy):
    cp = template_copy(d, lang)
    if copy:
        cp.update({k: v for k, v in copy.items() if k in ("persona", "verdict", "rules", "question")})
        cp["source"] = "copy.json"
    return cp


def svgs(d, lang="en", copy=None, percent_only=False):
    """The story set for one scope/period: [(card id, svg)], each 1080x1920, in order (stats, fun
    facts, roast; the facts card is left out when the scope has no fun facts).  percent_only is
    accepted for old callers and ignored: no card ever shows dollars."""
    T = L(lang)
    cp = _copy_for(d, lang, copy)
    bodies = []
    g = 74
    body, end = _card_stats(d, lang, g)
    if end < Y_MAX - 60:                  # spread spare room over the gaps (stays above the footer)
        body, end2 = _card_stats(d, lang, g + min(46, int((Y_MAX - 40 - end) / 6)))
        if end2 > Y_MAX:
            body, _e = _card_stats(d, lang, g)
    bodies.append((CARD_IDS[0], body))
    if fun_cells(d, lang):
        bodies.append((CARD_IDS[1], _card_facts(d, lang)[0]))
    for tier in ROAST_TIERS:
        body, end = _card_roast(d, lang, cp, tier)
        if end <= Y_MAX:
            break
    if end < Y_MAX - 30:                  # spread spare room over the section gaps
        sp = min(48, int((Y_MAX - 10 - end) / 2.5))
        b2, e2 = _card_roast(d, lang, cp, tier, sp)
        if e2 <= Y_MAX:
            body = b2
    bodies.append((CARD_IDS[2], body))
    n = len(bodies)
    return [(cid, _frame(b, lang, i + 1, n, T)) for i, (cid, b) in enumerate(bodies)]


def svg(d, lang="en", percent_only=False, copy=None):
    """All cards of the set joined (text checks only; not one valid SVG)."""
    return "".join(s for _i, s in svgs(d, lang, copy))


# ---- layout check (used by tests): every element inside the frame, text inside the margins,
# no two text lines overlapping.  Widths use the same estimate as the layout (text_w).
def layout_problems(s, margin=M - 8):
    import xml.etree.ElementTree as ET
    root = ET.fromstring(s)
    ns = "{http://www.w3.org/2000/svg}"
    w, h = int(root.get("width")), int(root.get("height"))
    probs, boxes = [], []
    if (w, h) != (W, H):
        probs.append("size %dx%d" % (w, h))
    for el in root.iter():
        tag = el.tag.replace(ns, "")
        if tag == "rect":
            x, y = float(el.get("x", 0)), float(el.get("y", 0))
            x2, y2 = x + float(el.get("width")), y + float(el.get("height"))
            if x < 0 or y < 0 or x2 > W or y2 > H:
                probs.append("rect out of frame %s" % ((x, y, x2, y2),))
            elif (x, y) != (0, 0) and (x, y) != (1, 1) and y2 > FOOT_LINE:
                probs.append("rect over the footer %s" % ((x, y, x2, y2),))
        elif tag == "polyline":
            for pt in el.get("points").split():
                px, py = map(float, pt.split(","))
                if not (0 <= px <= W and 0 <= py <= FOOT_LINE):
                    probs.append("polyline point out of bounds %s" % pt)
        elif tag == "text":
            size = float(el.get("font-size"))
            mono = el.get("class") == "m"
            bold = (el.get("font-weight") or "") in ("700", "800")
            wd = _bw(el.text or "", size, mono, bold)
            top_size = size
            for t in el:
                ts = float(t.get("font-size", size))
                wd += _bw(t.text or "", ts, mono, (t.get("font-weight") or "") in ("700", "800"))
                top_size = max(top_size, ts)
            x, y = float(el.get("x")), float(el.get("y"))
            a = el.get("text-anchor")
            x0 = x - wd if a == "end" else x - wd / 2 if a == "middle" else x
            box = (x0, y - top_size * 0.88, x0 + wd, y + top_size * 0.22)
            txt = "".join(el.itertext())
            if box[0] < margin - 6 or box[2] > W - margin or box[1] < 0 or box[3] > H:
                probs.append("text out of bounds %r %s" % (txt, tuple(round(v) for v in box)))
            if y < FOOT_LINE and box[3] > FOOT_LINE:
                probs.append("text over the footer %r" % txt)
            boxes.append((box, txt))
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            (a0, a1, a2, a3), ta = boxes[i]
            (b0, b1, b2, b3), tb = boxes[j]
            if min(a2, b2) - max(a0, b0) > 2 and min(a3, b3) - max(a1, b1) > 2:
                probs.append("text overlap %r / %r" % (ta, tb))
    return probs


def size_of(s):
    m = re.search(r'width="(\d+)" height="(\d+)"', s)
    return int(m.group(1)), int(m.group(2))


# ----------------------------------------------------------------- page / cli
def _variants(sm, se, lang, key, copy=None):
    """variants[period] = the card set as a list of SVG strings (in story order)."""
    T = L(lang)
    periods = available(sm, key)
    variants, badges, sizes, ids = {}, {}, {}, {}
    for per in periods:
        d = data(sm, se, per, key)
        cs = svgs(d, lang, copy=copy)
        variants[per] = [s for _i, s in cs]
        ids[per] = [i for i, _s in cs]
        sizes[per] = [W, H]
        badges[per] = {"code": d["badge"], "name": T["badges"][d["badge"]]["name"] if d["badge"] else None,
                       "tagline": T["badges"][d["badge"]]["tagline"] if d["badge"] else None}
    return {"periods": [{"id": x, "label": T["periods"][x]} for x in periods],
            "default": {"period": periods[0] if periods else None},
            "variants": variants, "card_ids": ids, "sizes": sizes, "badges": badges}


def page_block(sm, se, lang="en", copy=None):
    """Every available period's card set (a list of inline SVG strings, story order) for the Page,
    in the Page's language; with several accounts also the same per account ("accounts", in
    By-account row order).  copy (a checked copy.json) applies to the scope cards only; account
    cards use the templates."""
    slug = (sm.get("scope") or {}).get("slug") or "all"
    out = dict(_variants(sm, se, lang, "all", copy), filename="trading-journey-%s" % slug, width=W, height=H,
               copy_source="copy.json" if copy else "template")
    out["accounts"] = [dict(_variants(sm, se, lang, r["key"]), key=r["key"], slug=r["slug"],
                            label=(r.get("labels") or {}).get(lang) or r["label"],
                            filename="trading-journey-%s" % r["slug"]) for r in account_rows(sm)]
    return out


def rasterizer():
    for name in ("rsvg-convert", "cairosvg", "inkscape"):
        if shutil.which(name):
            return name
    try:
        import cairosvg  # noqa: F401
        return "cairosvg-module"
    except Exception:
        return None


PNG_SCALE = 2  # retina: a 1080x1920 card rasterizes to 2160x3840 px so text stays crisp when zoomed or shared


def to_png(svg_path, png_path, scale=PNG_SCALE):
    tool = rasterizer()
    with open(svg_path, encoding="utf-8") as f:
        w, h = size_of(f.read())
    pw, ph = w * scale, h * scale
    if tool == "rsvg-convert":
        cmd = ["rsvg-convert", "-w", str(pw), "-h", str(ph), "-o", png_path, svg_path]
    elif tool == "cairosvg":
        cmd = ["cairosvg", svg_path, "-s", str(scale), "-o", png_path]
    elif tool == "inkscape":
        cmd = ["inkscape", svg_path, "--export-type=png", "--export-width=%d" % pw, "--export-filename=" + png_path]
    elif tool == "cairosvg-module":
        import cairosvg
        cairosvg.svg2png(url=svg_path, write_to=png_path, scale=scale)
        return tool
    else:
        return None
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return tool


INSTALL_HINT = ("no SVG rasterizer found; the PNGs attached in chat are the user's only download, so install one "
                "and rerun with --png: `sudo apt-get install -y librsvg2-bin` (rsvg-convert), `brew install librsvg`, "
                "or `python3 -m venv ~/.lsl-venv && ~/.lsl-venv/bin/pip install cairosvg` with ~/.lsl-venv/bin on PATH; "
                "attach the SVGs only as a last resort")
ZIP_TIME = (2020, 1, 1, 0, 0, 0)  # fixed member time: zips are byte-stable


def _zip_files(zp, paths):
    """A stored (uncompressed) zip of `paths` (by basename, in order); chat clients recompress
    inline images, so the zip is the full-resolution download."""
    import zipfile
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_STORED) as z:
        for pp in paths:
            zi = zipfile.ZipInfo(os.path.basename(pp), date_time=ZIP_TIME)
            zi.external_attr = 0o644 << 16
            with open(pp, "rb") as f:
                z.writestr(zi, f.read())
    return os.path.abspath(zp)


def _zip_png(pp):
    """One PNG in a stored zip next to it (<name>.zip)."""
    return _zip_files(pp[:-4] + ".zip", [pp])


def _write(path, s, png, res):
    with open(path, "w", encoding="utf-8") as f:
        f.write(s)
    res["svg"] = os.path.abspath(path)
    res["size"] = list(size_of(s))
    if png:
        pp = path[:-4] + ".png"
        tool = to_png(path, pp)
        if tool:
            res.update({"png": os.path.abspath(pp), "rasterizer": tool, "zip": _zip_png(pp)})
        else:
            res.update({"png": None, "hint": INSTALL_HINT})
    return res


OLD_SINGLE = (".svg", ".png", ".zip")   # the pre-v0.2.13 single tall card, removed so it is never attached by mistake


def _write_set(dir_, prefix, cards, png, res, zip_name):
    """Write one card set: <dir>/<prefix><card id minus 'share-card'>.svg (+ .png, .zip) and a
    combined stored zip <dir>/<zip_name> of all PNGs in story order.  Fills res["cards_set"],
    res["zip"] (the combined zip) and, for old callers, res["svg"] / res["png"] (first card)."""
    out = []
    for cid, s in cards:
        r = {"id": cid}
        _write(os.path.join(dir_, prefix + cid[len("share-card"):] + ".svg"), s, png, r)
        out.append(r)
    res["cards_set"] = out
    res["count"] = len(out)
    res["svg"], res["size"] = out[0]["svg"], out[0]["size"]
    if png:
        pngs = [r.get("png") for r in out]
        if all(pngs):
            res.update({"png": pngs[0], "rasterizer": out[0]["rasterizer"],
                        "zip": _zip_files(os.path.join(dir_, zip_name), pngs)})
        else:
            res.update({"png": None, "hint": INSTALL_HINT})
    return res


def per_account(sm, se, out_dir, period="all", png=False, lang="en"):
    """One card set per account into out_dir/cards/<slug>-1-stats.svg ... (+ .png, .zip) and
    out_dir/cards/<slug>-share-cards.zip, in By-account row order.  An account without enough
    history for `period` is listed with its available periods."""
    out = []
    rows = account_rows(sm)
    if rows:
        os.makedirs(os.path.join(out_dir, "cards"), exist_ok=True)
    for r in rows:
        avail = available(sm, r["key"])
        res = {"account": r["key"], "label": (r.get("labels") or {}).get(lang) or r["label"], "slug": r["slug"],
               "available_periods": avail}
        if period not in avail:
            res["skipped"] = "period %r has too little data for this account" % period
        else:
            cdir = os.path.join(out_dir, "cards")
            for ext in OLD_SINGLE:
                old = os.path.join(cdir, r["slug"] + ext)
                if os.path.exists(old):
                    os.remove(old)
            _write_set(cdir, r["slug"], svgs(data(sm, se, period, r["key"]), lang), png, res,
                       r["slug"] + "-share-cards.zip")
        out.append(res)
    return out


def run(out_dir, period="all", percent_only=False, png=False, lang="en", per_account_cards=False, copy_path=None):
    sm = read_json(os.path.join(out_dir, "summary.json"))
    se = read_json(os.path.join(out_dir, "series.json"))
    if sm is None or se is None:
        return {"error": "missing summary.json / series.json in %s (run `lsl compute` first)" % out_dir}
    avail = available(sm)
    if period not in avail:
        return {"error": "period %r has too little data for a card" % period, "available_periods": avail}
    copy = None
    if copy_path:
        copy, probs = load_copy(copy_path, sm, lang)
        if probs:
            return {"error": "copy.json rejected; fix it or rerun without --copy (templates)", "problems": probs}
    res = {"status": "done", "period": period, "lang": lang, "scope": (sm.get("scope") or {}).get("id", "all"),
           "available_periods": avail, "accounts": len(account_rows(sm)),
           "copy": "copy.json" if copy else "template"}
    if percent_only:
        res["note"] = "--percent-only is a no-op: the card never shows dollar amounts"
    for ext in OLD_SINGLE:
        old = os.path.join(out_dir, "share-card" + ext)
        if os.path.exists(old):
            os.remove(old)
    _write_set(out_dir, "share-card", svgs(data(sm, se, period), lang, copy=copy), png, res, "share-cards.zip")
    if per_account_cards:
        res["cards"] = per_account(sm, se, out_dir, period, png, lang)
    return res
