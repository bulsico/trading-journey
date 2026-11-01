"""Share card: a tall (1080 wide) dark SVG (optionally PNG) for one report scope, in one language.

    lsl card [--out DIR | --data-dir DATA --scope S] [--period all|90d|30d|7d] [--lang en|zh]
             [--png] [--per-account] [--copy copy.json] [--percent-only (no-op, kept for old callers)]
        -> DIR/share-card.svg (+ .png); with --per-account (several accounts) also one card per
           account in DIR/cards/<account-slug>.svg (+ .png), from the same compute output.

No dollar amounts, ever: the card shows only percentages, ratios, counts, days and asset names.
Sections (top to bottom): header (brand, scope, date range, period), badge + tagline, return on
volume with an unlabelled equity-shape sparkline, a 6-stat grid, best/worst asset names, the
win/loss strip of the last trades, a "fun facts" grid (lsl/funfacts.py: most traded, long/short,
most loved / hated asset, streaks, hold times, busiest day / weekday, venue or account split, asset
mix, liquidations; up to 10, each hidden when the scope has no data for it); then "trader personality test" (type, roast, trend-discipline
line, five score bars), "trader rank" (S-F ladder + verdict) and "the AI asks" (three rules and
one question in a red quote box); footer "Trading Journey".

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
    """Greedy wrap: CJK breaks between characters, Latin between words; closing punctuation never
    starts a line (it hangs on the previous one)."""
    toks = re.findall(r"[\u2e80-\uffff]|[^\s\u2e80-\uffff]+|\s+", str(text))
    lines, cur = [], ""
    for t in toks:
        if t.isspace():
            if cur:
                cur += " "
            continue
        cand = cur + t
        if cur and text_w(cand.rstrip(), size, mono) > width and t[0] not in NO_START:
            lines.append(cur.rstrip())
            cur = t
        else:
            cur = cand
    if cur.strip():
        lines.append(cur.rstrip())
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
class _Out:
    def __init__(self):
        self.o = []

    def __call__(self, s):
        self.o.append(s)

    def text(self, x, y, s, size, fill, weight=None, anchor=None, mono=False, extra=""):
        self.o.append('<text x="%s" y="%s" font-size="%d"%s%s%s fill="%s"%s>%s</text>' % (
            _n(x), _n(y), size, ' font-weight="%s"' % weight if weight else "",
            ' text-anchor="%s"' % anchor if anchor else "", ' class="m"' if mono else "", fill, extra, esc(s)))


def _n(x):
    return ("%.1f" % x).rstrip("0").rstrip(".")


def _bar_color(v):
    return GREEN if v >= 7 else YELLOW if v >= 4 else RED


def svg(d, lang="en", percent_only=False, copy=None):
    """percent_only is accepted for old callers and ignored: the card never shows dollars."""
    T, Lb = L(lang), persona.labels(lang)
    p = d["p"]
    up = (p.get("net_pnl") or 0) >= 0
    col = GREEN if up else RED
    cp = template_copy(d, lang)
    if copy:
        cp.update({k: v for k, v in copy.items() if k in ("persona", "verdict", "rules", "question")})
        cp["source"] = "copy.json"
    o = _Out()
    y = 0.0

    def divider(yy):
        o('<line x1="%d" y1="%s" x2="%d" y2="%s" stroke="%s" stroke-width="2"/>' % (M, _n(yy), W // 2 - 26, _n(yy), LINE))
        o('<line x1="%d" y1="%s" x2="%d" y2="%s" stroke="%s" stroke-width="2"/>' % (W // 2 + 26, _n(yy), W - M, _n(yy), LINE))
        o('<path d="M%d %sl10 -10l10 10l-10 10z" fill="%s"/>' % (W // 2 - 10, _n(yy), YELLOW))

    def header(yy, key):
        em, title = T[key]
        o('<text x="%d" y="%s" font-size="34" font-family="%s">%s</text>' % (M, _n(yy), esc(EMOJI), em))
        o.text(M + 52, yy, title, 34, YELLOW, 800)

    def para(lines, yy, size, fill, lh, x=M, weight=None):
        for ln in lines:
            o.text(x, yy, ln, size, fill, weight)
            yy += lh
        return yy

    # ---- header
    scope = d["scope_labels"].get(lang) or d["scope_label"] or T["all_accounts"]
    src = " + ".join({"rh": "Robinhood", "hl": "Hyperliquid"}[s] for s in d["sources"])
    sub = "%s \u00b7 %s" % (scope, src) if d["scope_kind"] == "all" and src else scope
    if d.get("n_accounts", 0) > 1:
        sub += " \u00b7 " + T["n_accounts"] % d["n_accounts"]
    per = T["periods"][d["period"]]
    o('<rect x="%d" y="52" width="34" height="34" rx="8" fill="%s"/>' % (M, YELLOW))
    o.text(M + 7, 77, ">_", 20, BG, 800, mono=True)
    o.text(M + 50, 80, T["brand"].upper() if lang == "en" else T["brand"], 28, WHITE, 800, mono=True,
           extra=' letter-spacing="2"')
    pw = text_w(per, 24, True) + 36
    o('<rect x="%s" y="50" width="%s" height="40" rx="20" fill="none" stroke="%s" stroke-width="2"/>'
      % (_n(W - M - pw), _n(pw), YELLOW))
    o.text(W - M - pw / 2, 78, per, 24, YELLOW, 700, "middle", mono=True)
    subl = wrap(sub, 26, CW)[:2]
    y = para(subl, 132, 26, GRAY, 36)
    o.text(M, y + 2, "%s \u00b7 %s" % (T["window"] % (p["start"], p["end"]), T["as_of"] % d["as_of"]), 22, DIM,
           mono=True)
    y += 40
    # ---- badge
    if d["badge"]:
        name, tag = T["badges"][d["badge"]]["name"], T["badges"][d["badge"]]["tagline"]
        bw = text_w(name, 26) + 44
        o('<rect x="%d" y="%s" width="%s" height="48" rx="24" fill="%s" fill-opacity="0.14" stroke="%s" '
          'stroke-width="2"/>' % (M, _n(y), _n(bw), col, col))
        o.text(M + 22, y + 33, name, 26, col, 800)
        tl = wrap(tag, 24, CW - bw - 24)
        o.text(M + bw + 20, y + 32, tl[0] if tl else "", 24, GRAY)
        y += 72
    # ---- big number + sparkline
    rov = p.get("return_on_volume")
    o.text(M, y + 26, "%s \u00b7 %s" % (T["return_on_volume"], per), 24, GRAY)
    big = pct(rov, True, 2) if rov is not None else pct(p.get("win_rate"))
    o.text(M - 4, y + 130, big, 104, col if rov is not None else WHITE, 800, mono=True)
    sx0 = M + min(text_w(big, 104, True), 520) + 40
    sx1, sy0, sy1 = W - M, y + 40, y + 132
    sp = d["spark"]
    lo, hi = min(sp), max(sp)
    rng = (hi - lo) or 1.0
    pts = []
    for i, v in enumerate(sp):
        x = sx0 + (sx1 - sx0) * (i / (len(sp) - 1) if len(sp) > 1 else 0)
        pts.append("%.1f,%.1f" % (x, sy1 - (sy1 - sy0) * ((v - lo) / rng)))
    zy = sy1 - (sy1 - sy0) * ((0 - lo) / rng)
    o('<line x1="%s" y1="%.1f" x2="%s" y2="%.1f" stroke="#2a3644" stroke-width="1" stroke-dasharray="4 6"/>'
      % (_n(sx0), zy, _n(sx1), zy))
    o('<polyline points="%s" fill="none" stroke="%s" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>'
      % (" ".join(pts), col))
    y += 168
    # ---- stats grid
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
    o('<rect x="%d" y="%s" width="%d" height="216" rx="18" fill="%s" stroke="%s" stroke-width="2"/>'
      % (M, _n(y), CW, BOX, LINE))
    for i, (lab, val) in enumerate(stats):
        cx, cy = M + 28 + (i % 3) * cw3, y + 48 + (i // 3) * 100
        o.text(cx, cy, lab, 22, GRAY)
        o.text(cx, cy + 46, val, 40, WHITE, 700, mono=True)
    y += 216 + 50
    # ---- best / worst asset + strip
    bits = []
    if d.get("best_asset"):
        bits.append((T["best_asset"], d["best_asset"], GREEN))
    if d.get("worst_asset"):
        bits.append((T["worst_asset"], d["worst_asset"], RED))
    if bits:
        x = M
        for lab, name, c in bits:
            o.text(x, y, lab, 24, GRAY)
            x += text_w(lab, 24) + 14
            o.text(x, y, name, 28, c, 800, mono=True)
            x += text_w(name, 28, True) + 48
        y += 50
    strip = d["strip"]
    if strip:
        o.text(M, y, T["last_trades"] % len(strip), 22, DIM)
        y += 16
        step = min(19.0, CW / float(len(strip)))
        for i, ch in enumerate(strip):
            o('<rect x="%s" y="%s" width="%s" height="26" rx="3" fill="%s"/>'
              % (_n(M + i * step), _n(y), _n(step - 5), GREEN if ch == "W" else RED))
        y += 26 + 24
    cells = fun_cells(d, lang)
    if cells:
        y += 20
        divider(y)
        y += 66
        header(y, "h_fun")
        y += 34
        gap = 20
        cw2 = (CW - gap) / 2.0
        rows = [cells[i:i + 2] for i in range(0, len(cells), 2)]
        for row in rows:
            full = len(row) == 1
            cwid = CW if full else cw2
            laid = []
            for (k, em, lab, val, sub) in row:
                cont = None                       # a " · " list too long for one line wraps onto a 2nd line
                if not sub and " \u00b7 " in val and text_w(val, 26) * 1.12 > cwid - 44:
                    parts = val.split(" \u00b7 ")
                    n = len(parts) - 1
                    while n > 1 and text_w(" \u00b7 ".join(parts[:n]), 26) * 1.12 > cwid - 44:
                        n -= 1
                    val, cont = " \u00b7 ".join(parts[:n]), " \u00b7 ".join(parts[n:])
                laid.append((k, em, lab, val, sub, cont))
            rh = 150 if any(c[4] or c[5] for c in laid) else 116
            for j, (k, em, lab, val, sub, cont) in enumerate(laid):
                cx = M + j * (cw2 + gap)
                o('<rect x="%s" y="%s" width="%s" height="%d" rx="16" fill="%s" stroke="%s" stroke-width="2"/>'
                  % (_n(cx), _n(y), _n(cwid), rh, BOX, LINE))
                o('<text x="%s" y="%s" font-size="26" font-family="%s">%s</text>' % (_n(cx + 22), _n(y + 42),
                                                                                    esc(EMOJI), em))
                lt, ls = fit(lab, 22, cwid - 86, 18)
                o.text(cx + 64, y + 40, lt, ls, GRAY)
                vt, vs = fit(val, 30, cwid - 44, 22, bold=True)
                if cont:
                    ct, cs = fit(cont, 30, cwid - 44, 22, bold=True)
                    vs = cs = min(vs, cs)
                    o.text(cx + 22, y + 84, vt, vs, WHITE, 800)
                    o.text(cx + 22, y + 124, ct, cs, WHITE, 800)
                else:
                    o.text(cx + 22, y + 88, vt, vs, WHITE, 800)
                if sub:
                    st_, ss = fit(sub, 21, cwid - 44, 17)
                    o.text(cx + 22, y + 126, st_, ss, DIM)
            y += rh + 16
        y += 14
    y += 20
    divider(y)
    y += 66
    # ---- persona
    pz = d["persona"]
    header(y, "h_persona")
    y += 62
    if pz:
        tl = Lb["archetypes"][pz["archetype"]] + "".join(("" if lang == "zh" else " ") + Lb["modifiers"][m]
                                                         for m in pz["modifiers"])
        if text_w(T["type_prefix"] + tl, 34) <= CW:
            o('<text x="%d" y="%s" font-size="30" fill="%s">%s<tspan font-size="36" font-weight="800" fill="%s">%s'
              '</tspan></text>' % (M, _n(y), WHITE, esc(T["type_prefix"]), YELLOW, esc(tl)))
            y += 56
        else:
            o.text(M, y, T["type_prefix"], 30, WHITE)
            y += 48
            y = para(wrap(tl, 36, CW), y, 36, YELLOW, 48, weight=800) + 8
    y = para(wrap(cp["persona"], BODY, CW), y, BODY, WHITE, 46)
    if d["period"] != "all":
        o.text(M, y, T["persona_all_time"], 21, DIM)
        y += 36
    tr = d["trend"]
    if tr:
        s = T["trend_line"].format(pct=pct(tr["counter_share"]))
        if tr.get("counter_worse") is not None:
            s += ("" if lang == "zh" else " ") + T["trend_worse" if tr["counter_worse"] else "trend_better"]
        y += 8
        for ln in wrap(s, SMALL, CW - 30):
            o('<rect x="%d" y="%s" width="6" height="30" fill="%s"/>' % (M, _n(y - 24), YELLOW))
            o.text(M + 22, y, ln, SMALL, GRAY)
            y += 38
    if pz:
        y += 14
        rk = [k for k in RATING_KEYS if k in pz["ratings"]]
        bh = 40 + len(rk) * 58
        o('<rect x="%d" y="%s" width="%d" height="%d" rx="18" fill="%s" stroke="%s" stroke-width="2"/>'
          % (M, _n(y), CW, bh, BOX, LINE))
        lw = max(text_w(Lb["ratings"][k], 26) for k in rk) if rk else 0
        bx0 = M + 30 + lw + 24
        bx1 = W - M - 30 - text_w("10/10", 28, True) - 20
        for i, k in enumerate(rk):
            v = int(pz["ratings"][k])
            cy = y + 20 + i * 58 + 38
            c = _bar_color(v)
            o.text(M + 30, cy, Lb["ratings"][k], 26, WHITE)
            o('<rect x="%s" y="%s" width="%s" height="20" rx="10" fill="#1d2632"/>' % (_n(bx0), _n(cy - 18), _n(bx1 - bx0)))
            if v > 0:
                o('<rect x="%s" y="%s" width="%s" height="20" rx="10" fill="%s"/>'
                  % (_n(bx0), _n(cy - 18), _n((bx1 - bx0) * v / 10.0), c))
            o.text(W - M - 30, cy, "%d/10" % v, 28, c, 700, "end", mono=True)
        y += bh + 30
    y += 20
    divider(y)
    y += 66
    # ---- rank
    header(y, "h_grade")
    y += 34
    g = (pz or {}).get("grade") or "?"
    lh = 46
    o('<rect x="%d" y="%s" width="%d" height="%d" rx="18" fill="%s" stroke="%s" stroke-width="2"/>'
      % (M, _n(y), CW, 24 + lh * len(GRADE_LADDER), BOX, LINE))
    for i, gg in enumerate(GRADE_LADDER):
        ry = y + 12 + i * lh
        me = gg == g
        if me:
            o('<rect x="%d" y="%s" width="%d" height="%d" rx="10" fill="%s" fill-opacity="0.16" stroke="%s" '
              'stroke-width="2"/>' % (M + 12, _n(ry), CW - 24, lh, YELLOW, YELLOW))
        o.text(M + 40, ry + 34, gg, 30, YELLOW if me else DIM, 800, mono=True)
        o.text(M + 96, ry + 33, Lb["grades"][gg], 26, WHITE if me else GRAY, 700 if me else None)
        if me:
            o.text(W - M - 32, ry + 31, T["you_are_here"], 22, YELLOW, 700, "end")
    y += 24 + lh * len(GRADE_LADDER) + 52
    o.text(M, y, T["grade_line"].format(grade=g, label=Lb["grades"][g]), 32, YELLOW, 800)
    y += 50
    y = para(wrap(cp["verdict"], BODY, CW), y, BODY, WHITE, 46)
    y += 20
    divider(y)
    y += 66
    # ---- the AI asks
    header(y, "h_soul")
    y += 58
    y = para(wrap(T["soul_intro"], SMALL, CW), y, SMALL, GRAY, 38) + 14
    for i, r in enumerate(cp["rules"][:3]):
        lead, rest = split_lead(r)
        o.text(M, y, "%d." % (i + 1), BODY, YELLOW, 800, mono=True)
        y = para(wrap(lead, BODY - 2, CW - 56), y, BODY - 2, WHITE, 42, x=M + 56, weight=800)
        if rest:
            y = para(wrap(rest, BODY - 4, CW - 56), y, BODY - 4, GRAY, 40, x=M + 56)
        y += 18
    y += 6
    y = para(wrap(T["question_intro"], SMALL, CW), y, SMALL, GRAY, 38) + 8
    ql = wrap(cp["question"], BODY + 2, CW - 64)
    qh = 40 + len(ql) * 46
    o('<rect x="%d" y="%s" width="%d" height="%d" rx="12" fill="%s" fill-opacity="0.08"/>' % (M, _n(y), CW, qh, RED))
    o('<rect x="%d" y="%s" width="8" height="%d" rx="2" fill="%s"/>' % (M, _n(y), qh, RED))
    para(ql, y + 52, BODY + 2, WHITE, 46, x=M + 36, weight=800)
    y += qh + 64
    # ---- footer
    o('<line x1="%d" y1="%s" x2="%d" y2="%s" stroke="%s" stroke-width="2"/>' % (M, _n(y - 30), W - M, _n(y - 30), LINE))
    o.text(W / 2, y + 12, T["footer"], 24, DIM, 700, "middle", mono=True, extra=' letter-spacing="1"')
    H = int(y + 56)
    head = ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" font-family="%s">'
            '<style>.m{font-family:%s}</style>' % (W, H, W, H, esc(FONTS.get(lang, FONTS["en"])),
                                                      MONO.get(lang, MONO["en"]).replace("'", "")))
    bg = ('<rect width="%d" height="%d" rx="28" fill="%s"/>'
          '<rect x="1" y="1" width="%d" height="%d" rx="27" fill="none" stroke="#1d2733" stroke-width="2"/>'
          % (W, H, BG, W - 2, H - 2))
    return "\n".join([head, bg] + o.o + ["</svg>"]) + "\n"


def size_of(s):
    m = re.search(r'width="(\d+)" height="(\d+)"', s)
    return int(m.group(1)), int(m.group(2))


# ----------------------------------------------------------------- page / cli
def _variants(sm, se, lang, key, copy=None):
    T = L(lang)
    periods = available(sm, key)
    variants, badges, sizes = {}, {}, {}
    for per in periods:
        d = data(sm, se, per, key)
        variants[per] = svg(d, lang, copy=copy)
        sizes[per] = list(size_of(variants[per]))
        badges[per] = {"code": d["badge"], "name": T["badges"][d["badge"]]["name"] if d["badge"] else None,
                       "tagline": T["badges"][d["badge"]]["tagline"] if d["badge"] else None}
    return {"periods": [{"id": x, "label": T["periods"][x]} for x in periods],
            "default": {"period": periods[0] if periods else None},
            "variants": variants, "sizes": sizes, "badges": badges}


def page_block(sm, se, lang="en", copy=None):
    """Every available period as an inline SVG string for the Page, in the Page's language; with
    several accounts also the same per account ("accounts", in By-account row order).  copy (a
    checked copy.json) applies to the scope card only; account cards use the templates."""
    slug = (sm.get("scope") or {}).get("slug") or "all"
    out = dict(_variants(sm, se, lang, "all", copy), filename="trading-journey-%s" % slug, width=W,
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


PNG_SCALE = 2  # retina: a 1080-wide card rasterizes to 2160 px so text stays crisp when zoomed or shared


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


INSTALL_HINT = ("no SVG rasterizer found; the PNG attached in chat is the user's only download, so install one "
                "and rerun with --png: `sudo apt-get install -y librsvg2-bin` (rsvg-convert), `brew install librsvg`, "
                "or `python3 -m venv ~/.lsl-venv && ~/.lsl-venv/bin/pip install cairosvg` with ~/.lsl-venv/bin on PATH; "
                "attach the SVG only as a last resort")


def _write(path, s, png, res):
    with open(path, "w", encoding="utf-8") as f:
        f.write(s)
    res["svg"] = os.path.abspath(path)
    res["size"] = list(size_of(s))
    if png:
        pp = path[:-4] + ".png"
        tool = to_png(path, pp)
        if tool:
            res.update({"png": os.path.abspath(pp), "rasterizer": tool})
        else:
            res.update({"png": None, "hint": INSTALL_HINT})
    return res


def per_account(sm, se, out_dir, period="all", png=False, lang="en"):
    """One card per account into out_dir/cards/<slug>.svg (+ .png), in By-account row order.
    An account without enough history for `period` is listed with its available periods."""
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
            _write(os.path.join(out_dir, "cards", r["slug"] + ".svg"),
                   svg(data(sm, se, period, r["key"]), lang), png, res)
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
    _write(os.path.join(out_dir, "share-card.svg"), svg(data(sm, se, period), lang, copy=copy), png, res)
    if per_account_cards:
        res["cards"] = per_account(sm, se, out_dir, period, png, lang)
    return res
