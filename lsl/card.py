"""Share card: a 1200x675 dark SVG (optionally PNG) summing up one report scope.

    lsl card [--out DIR | --data-dir DATA --scope S] [--period all|90d|30d|7d]
             [--percent-only] [--lang en|zh] [--png]   -> DIR/share-card.svg (+ .png)

Pure stdlib, fixed layout and font stack, so the same inputs give byte-identical SVG.
Every number comes from summary.json / series.json (profile periods, daily cumulative).
Privacy: the card names only the scope label (e.g. "All accounts", "Robinhood Roth IRA ...1234",
"0xabcd...1234"); never a full address or account number.  --percent-only drops every dollar
figure: the big number becomes return on volume and drawdown is shown as a share of peak.

Badge tiers (first match wins; computed for the card's period P from the scope's profile):
  LIQUIDATION_SPEEDRUN  all-time card, net < 0 and >= 3 trades ended in liquidation
  PENNY_PICKER          net < -$100 and win rate >= 50%   (wins often, loses big)
  FEE_FARMER            net < -$100 and trading style is Scalper
  EXIT_LIQUIDITY        net < -$100
  TREADMILL             |net| <= $100
  SNIPER                net > $100 and win rate >= 70%
  DIAMOND_HANDS         net > $100 and style Swing or Position (median hold >= 1 day)
  GRINDER               any other net > $100
"""
import os
import shutil
import subprocess

from . import persona
from .util import r2, read_json

W, H = 1200, 675
GREEN, RED, GRAY, DIM, WHITE, BG = "#2ee6a6", "#ff5c7a", "#8b98a9", "#5d6b7c", "#e9eef5", "#0b0f14"
# Some rasterizers (cairosvg) only honour the first family, so zh leads with a CJK face
# (which also carries Latin glyphs); browsers walk the whole list either way.
FONTS = {"en": "Inter, 'Noto Sans', 'DejaVu Sans', Arial, 'Noto Sans CJK SC', sans-serif",
         "zh": "'Noto Sans CJK SC', 'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei', 'Source Han Sans SC', "
               "Inter, Arial, sans-serif"}
PERIOD_ORDER = ("all", "90d", "30d", "7d")
SPARK_POINTS = 120
STRIP_MAX = 50


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
def money_k(x):
    a = abs(x)
    if a >= 1e6:
        s = "{:,.2f}M".format(a / 1e6)
    elif a >= 1e3:
        s = "{:,.1f}K".format(a / 1e3)
    else:
        s = "{:,.0f}".format(a)
    return ("-$" if x < 0 and s.strip("0.,KM") else "+$" if x > 0 else "$") + s


def pct(x, plus=False, dp=1):
    if x is None:
        return "-"
    return ("+" if plus and x > 0 else "") + ("{:.%df}%%" % dp).format(x * 100)


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def text_w(s, size):
    """Deterministic width estimate (no font metrics in stdlib)."""
    w = 0.0
    for ch in str(s):
        o = ord(ch)
        w += 1.0 if o > 0x2e80 else 0.36 if ch in " .,:;|!'il1" else 0.62 if ch.isdigit() or ch in "$+-%" else 0.6
    return w * size


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


def data(sm, se, period="all", key="all"):
    prof = (sm.get("profile") or {}).get(key) or {}
    p = (prof.get("periods") or {}).get(period) or {}
    if not p.get("enough"):
        return None
    scope = sm.get("scope") or {}
    o = sm.get("overall") or {}
    strip = prof.get("strip") or ""
    if period != "all":
        strip = strip[-min(len(strip), p["closed_trades"]):] if p["closed_trades"] else ""
    return {"period": period, "p": p, "style": prof.get("style"),
            "badge": badge(p, prof, o.get("liquidated_trades") or 0, period),
            "scope_label": scope.get("label"), "scope_labels": scope.get("labels") or {}, "scope_kind": scope.get("kind", "all"),
            "sources": [s for s in ("rh", "hl") if (sm.get("sources") or {}).get(s)],
            "as_of": (sm.get("as_of") or "")[:10], "strip": strip[-STRIP_MAX:],
            "spark": spark(se, key, p["start"], p["end"])}


def available(sm):
    prof = (sm.get("profile") or {}).get("all") or {}
    return [x for x in PERIOD_ORDER if ((prof.get("periods") or {}).get(x) or {}).get("enough")]


# ----------------------------------------------------------------- svg
def svg(d, lang="en", percent_only=False):
    T = L(lang)
    p = d["p"]
    up = (p["net_pnl"] or 0) >= 0
    col = GREEN if up else RED
    if percent_only:
        if p.get("return_on_volume") is not None:
            big, big_label = pct(p["return_on_volume"], True, 2), T["return_on_volume"]
        else:
            big, big_label, col = pct(p["win_rate"]), T["win_rate"], WHITE
    else:
        big, big_label = money_k(p["net_pnl"]), T["net_pnl_ex_funding" if "hl" in d["sources"] else "net_pnl"]
    scope = d["scope_labels"].get(lang) or d["scope_label"] or T["all_accounts"]
    src = " + ".join({"rh": "Robinhood", "hl": "Hyperliquid"}[s] for s in d["sources"])
    sub = "%s \u00b7 %s" % (scope, src) if d["scope_kind"] == "all" and src else scope
    per = T["periods"][d["period"]]
    o = []
    a = o.append
    a('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" font-family="%s">'
      % (W, H, W, H, esc(FONTS.get(lang, FONTS['en']))))
    a('<defs><pattern id="g" width="40" height="40" patternUnits="userSpaceOnUse">'
      '<path d="M40 0H0V40" fill="none" stroke="#141b24" stroke-width="1"/></pattern>'
      '<linearGradient id="f" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="%s" stop-opacity="0.28"/>'
      '<stop offset="1" stop-color="%s" stop-opacity="0"/></linearGradient></defs>' % (col, col))
    a('<rect width="%d" height="%d" rx="28" fill="%s"/><rect width="%d" height="%d" rx="28" fill="url(#g)"/>'
      % (W, H, BG, W, H))
    a('<rect x="1" y="1" width="%d" height="%d" rx="27" fill="none" stroke="#1d2733" stroke-width="2"/>' % (W - 2, H - 2))
    # header
    a('<rect x="60" y="52" width="26" height="26" rx="7" fill="%s"/>' % col)
    a('<path d="M66 71 L72 64 L76 68 L81 59" fill="none" stroke="%s" stroke-width="3" stroke-linecap="round" '
      'stroke-linejoin="round"/>' % BG)
    a('<text x="100" y="73" font-size="24" font-weight="700" fill="%s" letter-spacing="1">%s</text>'
      % (WHITE, esc(T["brand"])))
    a('<text x="100" y="106" font-size="20" fill="%s">%s</text>' % (GRAY, esc(sub)))
    a('<text x="1140" y="73" font-size="20" fill="%s" text-anchor="end">%s</text>'
      % (GRAY, esc(T["as_of"] % d["as_of"])))
    a('<text x="1140" y="106" font-size="20" fill="%s" text-anchor="end">%s</text>'
      % (DIM, esc(T["window"] % (p["start"], p["end"]))))
    # big number
    a('<text x="60" y="170" font-size="24" fill="%s">%s</text>' % (GRAY, esc(big_label)))
    a('<text x="56" y="262" font-weight="800" fill="%s"><tspan font-size="104">%s</tspan>'
      '<tspan font-size="40" fill="%s" dx="14">/%s</tspan></text>' % (col, esc(big), DIM, esc(per)))
    # sparkline
    sp = d["spark"]
    x0, x1, y0, y1 = 60.0, 1140.0, 290.0, 420.0
    lo, hi = min(sp), max(sp)
    rng = (hi - lo) or 1.0
    pts = []
    for i, v in enumerate(sp):
        x = x0 + (x1 - x0) * (i / (len(sp) - 1) if len(sp) > 1 else 0)
        y = y1 - (y1 - y0) * ((v - lo) / rng)
        pts.append("%.1f,%.1f" % (x, y))
    zy = y1 - (y1 - y0) * ((0 - lo) / rng)
    a('<line x1="60" y1="%.1f" x2="1140" y2="%.1f" stroke="#2a3644" stroke-width="1" stroke-dasharray="4 6"/>' % (zy, zy))
    a('<polygon points="%.1f,%.1f %s %.1f,%.1f" fill="url(#f)"/>' % (x0, y1, " ".join(pts), x1, y1))
    a('<polyline points="%s" fill="none" stroke="%s" stroke-width="3" stroke-linejoin="round" '
      'stroke-linecap="round"/>' % (" ".join(pts), col))
    # win/loss strip
    strip = d["strip"]
    if strip:
        a('<text x="60" y="458" font-size="16" fill="%s">%s</text>' % (DIM, esc(T["last_trades"] % len(strip))))
        sx = 1140 - len(strip) * 14 + 4
        for i, ch in enumerate(strip):
            a('<rect x="%d" y="444" width="10" height="18" rx="2" fill="%s"/>' % (sx + i * 14, GREEN if ch == "W" else RED))
    # stats
    if percent_only:
        dd = pct(-p["drawdown_of_peak"]) if p.get("drawdown_of_peak") is not None else "-"
        stats = [(T["win_rate"], pct(p["win_rate"])), (T["sharpe"], fnum(p["sharpe"])), (T["max_dd_pct"], dd),
                 (T["trades"], "{:,}".format(p["closed_trades"]))]
    else:
        stats = [(T["win_rate"], pct(p["win_rate"])), (T["sharpe"], fnum(p["sharpe"])),
                 (T["max_dd"], money_k(p["max_drawdown"]) if p["max_drawdown"] else "$0"),
                 (T["trades"], "{:,}".format(p["closed_trades"]))]
    for i, (lab, val) in enumerate(stats):
        x = 60 + i * 270
        a('<text x="%d" y="512" font-size="18" fill="%s" letter-spacing="0.5">%s</text>' % (x, GRAY, esc(lab)))
        a('<text x="%d" y="556" font-size="38" font-weight="700" fill="%s">%s</text>' % (x, WHITE, esc(val)))
    # footer
    a('<line x1="60" y1="586" x2="1140" y2="586" stroke="#1d2733" stroke-width="2"/>')
    if d["badge"]:
        name = T["badges"][d["badge"]]["name"]
        tag = T["badges"][d["badge"]]["tagline"]
        bw = int(text_w(name, 22) + 40)
        a('<rect x="60" y="604" width="%d" height="40" rx="20" fill="%s" fill-opacity="0.14" stroke="%s" '
          'stroke-width="2"/>' % (bw, col, col))
        a('<text x="%d" y="631" font-size="22" font-weight="700" fill="%s">%s</text>' % (80, col, esc(name)))
        a('<text x="%d" y="631" font-size="20" fill="%s">%s</text>' % (60 + bw + 18, GRAY, esc(tag)))
    a('<text x="1140" y="631" font-size="20" font-weight="700" fill="%s" text-anchor="end">%s</text>'
      % (DIM, esc(T["footer"])))
    a('</svg>')
    return "\n".join(o) + "\n"


def fnum(x):
    return "-" if x is None else "{:.2f}".format(x)


# ----------------------------------------------------------------- page / cli
def page_block(sm, se, lang="en"):
    """Every available period x {usd, pct} as inline SVG strings for the Page."""
    T = L(lang)
    periods = available(sm)
    variants, badges = {}, {}
    for per in periods:
        d = data(sm, se, per)
        variants[per + ".usd"] = svg(d, lang, False)
        variants[per + ".pct"] = svg(d, lang, True)
        badges[per] = {"code": d["badge"], "name": T["badges"][d["badge"]]["name"] if d["badge"] else None,
                       "tagline": T["badges"][d["badge"]]["tagline"] if d["badge"] else None}
    slug = (sm.get("scope") or {}).get("slug") or "all"
    return {"periods": [{"id": x, "label": T["periods"][x]} for x in periods],
            "default": {"period": periods[0] if periods else None, "mode": "usd"},
            "variants": variants, "badges": badges, "filename": "trading-journey-%s" % slug,
            "width": W, "height": H}


def rasterizer():
    for name in ("rsvg-convert", "cairosvg", "inkscape"):
        if shutil.which(name):
            return name
    try:
        import cairosvg  # noqa: F401
        return "cairosvg-module"
    except Exception:
        return None


def to_png(svg_path, png_path):
    tool = rasterizer()
    if tool == "rsvg-convert":
        cmd = ["rsvg-convert", "-w", str(W), "-h", str(H), "-o", png_path, svg_path]
    elif tool == "cairosvg":
        cmd = ["cairosvg", svg_path, "-o", png_path]
    elif tool == "inkscape":
        cmd = ["inkscape", svg_path, "--export-type=png", "--export-filename=" + png_path]
    elif tool == "cairosvg-module":
        import cairosvg
        cairosvg.svg2png(url=svg_path, write_to=png_path)
        return tool
    else:
        return None
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return tool


INSTALL_HINT = ("no SVG rasterizer found; install one for PNG output: `sudo apt-get install librsvg2-bin` "
                "(rsvg-convert), `brew install librsvg`, or `pip install cairosvg`")


def run(out_dir, period="all", percent_only=False, png=False, lang="en"):
    sm = read_json(os.path.join(out_dir, "summary.json"))
    se = read_json(os.path.join(out_dir, "series.json"))
    if sm is None or se is None:
        return {"error": "missing summary.json / series.json in %s (run `lsl compute` first)" % out_dir}
    avail = available(sm)
    if period not in avail:
        return {"error": "period %r has too little data for a card" % period, "available_periods": avail}
    s = svg(data(sm, se, period), lang, percent_only)
    path = os.path.join(out_dir, "share-card.svg")
    with open(path, "w", encoding="utf-8") as f:
        f.write(s)
    res = {"status": "done", "svg": os.path.abspath(path), "period": period, "lang": lang,
           "percent_only": percent_only, "scope": (sm.get("scope") or {}).get("id", "all"),
           "available_periods": avail}
    if png:
        pp = os.path.join(out_dir, "share-card.png")
        tool = to_png(path, pp)
        if tool:
            res.update({"png": os.path.abspath(pp), "rasterizer": tool})
        else:
            res.update({"png": None, "hint": INSTALL_HINT})
    return res
