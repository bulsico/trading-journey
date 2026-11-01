"""Self-contained HTML report. The local stand-in for the canvas Page.

    lsl html [--out DIR | --data-dir DATA --scope S] [--lang en|zh] [--copy FILE]
        -> DIR/report.html

Every figure is taken from the compute outputs (via page.build, so display
strings are the ones the Page would show). This module does not invent,
round or re-derive a statistic. Share cards are inlined from the SVG (or
PNG) files `lsl card` wrote, and the PNG paths are listed for the user to
open. Nothing is fetched. The same inputs produce the same bytes.

`page.json` is not rewritten, so a later `lsl page --compact` (the canvas
payload) cannot shrink this report, and rendering the report cannot shrink
the canvas payload.
"""
import base64
import html
import os

from . import card as card_mod
from . import page as page_mod
from . import persona
from .util import read_json


def _version():
    import lsl
    return lsl.VERSION

REPORT_NAME = "report.html"
_CARD_STEMS = ("1-stats", "2-facts", "3-roast")
_COLORS = ("#e8c547", "#7eb6ff", "#e07a5f", "#81b29a")

_CSS = """
body { background: #12141a; color: #e7e5e1; margin: 0;
  font: 16px/1.45 "PingFang SC", "Hiragino Sans GB", "Noto Sans CJK SC", "Noto Sans SC",
  "Source Han Sans SC", "Microsoft YaHei", sans-serif; }
main { max-width: 960px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 1.6rem; font-weight: 650; margin: 0 0 0.25rem; }
h2 { font-size: 1.2rem; font-weight: 650; margin: 2rem 0 0.5rem; }
h3 { font-size: 1rem; font-weight: 650; margin: 1.2rem 0 0.4rem; }
p, li { margin: 0.35rem 0; }
.muted { color: #9a978f; }
.banner { background: #1c2028; padding: 10px 12px; margin: 12px 0; }
.banner.danger { border-left: 3px solid #ff6b6b; }
.banner.success { border-left: 3px solid #3dd68c; }
.cards, .metrics { display: flex; flex-wrap: wrap; gap: 12px; }
.card svg, .card img { width: 270px; height: auto; background: #000; }
.metric { background: #1c2028; padding: 12px 14px; min-width: 140px; }
.metric b { display: block; font-size: 1.2rem; margin-top: 2px; }
table { border-collapse: collapse; width: 100%; margin: 0.4rem 0 0.8rem; }
td, th { border-bottom: 1px solid #2a2e38; padding: 4px 8px; text-align: left; vertical-align: top; }
th { color: #9a978f; font-weight: 550; }
.success { color: #3dd68c; }
.danger { color: #ff6b6b; }
.warning { color: #e8c547; }
.path { font-family: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
  font-size: 13px; word-break: break-all; }
.chart { width: 100%; height: auto; background: #1c2028; }
footer { margin-top: 3rem; color: #9a978f; font-size: 13px; }
@media print {
  body { background: #fff; color: #111; }
  .banner, .metric { background: #f4f4f4; }
  .muted, footer, th { color: #444; }
  td, th { border-bottom-color: #ccc; }
  .card svg, .card img { width: 180px; }
}
"""


def _esc(s):
    return html.escape("" if s is None else str(s), quote=True)


def _tone(tone):
    return tone if tone in ("success", "danger", "warning", "info") else None


class _Doc:
    def __init__(self, page, labels):
        self.p = page
        self.L = labels
        self.U = labels["ui"]
        self.H = labels["html"]
        self.out = []

    def add(self, s):
        self.out.append(s)

    def section(self, title):
        self.add("<h2>%s</h2>" % _esc(title))

    def ptext(self, s, cls=None):
        if s:
            extra = ' class="%s"' % cls if cls else ""
            self.add("<p%s>%s</p>" % (extra, _esc(s)))

    def bullets(self, items):
        rows = [x for x in items if x]
        if not rows:
            return
        self.add("<ul>")
        for x in rows:
            self.add("<li>%s</li>" % _esc(x))
        self.add("</ul>")

    def table(self, headers, rows):
        """rows: list of list of (text, tone or None)."""
        if not rows:
            return
        self.add("<table><thead><tr>")
        for h in headers:
            self.add("<th>%s</th>" % _esc(h))
        self.add("</tr></thead><tbody>")
        for row in rows:
            self.add("<tr>")
            for text, tone in row:
                cls = _tone(tone)
                extra = ' class="%s"' % cls if cls else ""
                self.add("<td%s>%s</td>" % (extra, _esc(text)))
            self.add("</tr>")
        self.add("</tbody></table>")

    def cell(self, text, tone=None):
        return (text, tone)


def _discover(directory, prefix):
    """Card files for one set, in story order. Missing cards are omitted."""
    out = []
    if not directory or not os.path.isdir(directory):
        return out
    for stem in _CARD_STEMS:
        name = "%s-%s" % (prefix, stem)
        svg = os.path.join(directory, name + ".svg")
        png = os.path.join(directory, name + ".png")
        has_svg, has_png = os.path.isfile(svg), os.path.isfile(png)
        if not has_svg and not has_png:
            continue
        out.append({"id": "share-card-" + stem,
                    "svg": os.path.abspath(svg) if has_svg else None,
                    "png": os.path.abspath(png) if has_png else None})
    return out


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _fallback_cards(page):
    """SVGs already embedded in page.json, used only when `lsl card` has not been run."""
    card = page.get("card") or {}
    per = (card.get("default") or {}).get("period")
    svgs = (card.get("variants") or {}).get(per) or []
    ids = (card.get("card_ids") or {}).get(per) or []
    out = []
    for i, svg in enumerate(svgs):
        out.append({"id": ids[i] if i < len(ids) else "share-card-%d" % (i + 1),
                    "svg": None, "png": None, "svg_text": svg})
    return out


def _load_set(found):
    """Attach svg text (or png bytes) so rendering does not touch the disk again."""
    out = []
    for c in found:
        item = {"id": c["id"], "svg": c.get("svg"), "png": c.get("png"), "svg_text": None, "png_b64": None}
        if c.get("svg_text"):
            item["svg_text"] = c["svg_text"]
        elif c.get("svg"):
            item["svg_text"] = _read(c["svg"])
        elif c.get("png"):
            with open(c["png"], "rb") as f:
                item["png_b64"] = base64.b64encode(f.read()).decode("ascii")
        out.append(item)
    return out


def _chart(journey):
    lines = journey.get("lines") or []
    cum = journey.get("cumulative_k") or {}
    width, height = 720, 200
    series = []
    for i, line in enumerate(lines):
        vals = cum.get(line.get("key")) or []
        if vals:
            series.append((line.get("label") or line.get("key") or "", vals, _COLORS[i % len(_COLORS)]))
    if not series:
        return ""
    lo = min([0.0] + [v for _l, vals, _c in series for v in vals])
    hi = max([0.0] + [v for _l, vals, _c in series for v in vals])
    span = (hi - lo) or 1.0

    def xy(i, v, n):
        x = 0.0 if n == 1 else i * (width - 1) / (n - 1)
        y = 1.0 + (hi - v) / span * (height - 2)
        return "%.2f,%.2f" % (x, y)

    parts = ['<svg class="chart" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" '
             'width="%d" height="%d" role="img">' % (width, height, width, height)]
    zy = 1.0 + (hi - 0.0) / span * (height - 2)
    parts.append('<line x1="0" y1="%.2f" x2="%d" y2="%.2f" stroke="#2a2e38" stroke-width="1"/>' % (zy, width, zy))
    for _label, vals, color in series:
        n = len(vals)
        pts = " ".join(xy(i, v, n) for i, v in enumerate(vals))
        parts.append('<polyline fill="none" stroke="%s" stroke-width="2" points="%s"/>' % (color, pts))
    parts.append("</svg>")
    legend = " · ".join("%s" % label for label, _v, _c in series)
    return "".join(parts) + ('<p class="muted">%s</p>' % _esc(legend) if legend else "")


def _card_html(cards):
    parts = ['<div class="cards">']
    for c in cards:
        if c.get("svg_text"):
            parts.append('<div class="card">%s</div>' % c["svg_text"])
        elif c.get("png_b64"):
            parts.append('<div class="card"><img alt="%s" src="data:image/png;base64,%s"></div>'
                         % (_esc(c["id"]), c["png_b64"]))
    parts.append("</div>")
    return "".join(parts)


def _paths_html(doc, cards):
    pngs = [c["png"] for c in cards if c.get("png")]
    svgs = [c["svg"] for c in cards if c.get("svg")]
    if pngs:
        doc.add("<h3>%s</h3>" % _esc(doc.H["png_heading"]))
        doc.ptext(doc.H["png_hint"], "muted")
        doc.add("<ul>")
        for p in pngs:
            doc.add('<li class="path">%s</li>' % _esc(p))
        doc.add("</ul>")
    elif svgs:
        doc.ptext(doc.H["no_png"], "muted")
        doc.add("<ul>")
        for p in svgs:
            doc.add('<li class="path">%s</li>' % _esc(p))
        doc.add("</ul>")


def _profile(doc, one, heading):
    doc.add("<h3>%s</h3>" % _esc(heading))
    doc.add('<div class="metrics">')
    for item in one.get("top") or []:
        shown = item.get("exact") or item.get("usd") or ""
        cls = _tone(item.get("tone"))
        extra = ' class="%s"' % cls if cls else ""
        doc.add("<div class=\"metric\"><span>%s</span><b%s>%s</b></div>"
                % (_esc(item.get("label")), extra, _esc(shown)))
    doc.add("</div>")
    facts = one.get("facts") or []
    if facts:
        def fact_value(f):
            shown = f.get("value") or ""
            if f.get("detail"):
                shown = "%s · %s" % (shown, f["detail"])
            return shown
        doc.table([doc.U["col_metric"], doc.U["col_detail"]],
                  [[doc.cell(f.get("label")), doc.cell(fact_value(f))] for f in facts])
    if one.get("strip_display"):
        doc.ptext(one.get("strip_label"))
        doc.ptext(one.get("strip_display"))
    periods = one.get("periods") or {}
    ids = periods.get("labels") or []
    rows = []
    for r in periods.get("rows") or []:
        cells = [doc.cell(r.get("label"))]
        for val in r.get("usd") or []:
            cells.append(doc.cell(val))
        rows.append(cells)
    if rows:
        doc.table([doc.U["performance"]] + list(ids), rows)


def _accounts(doc):
    acc = doc.p.get("accounts") or {}
    if not acc.get("show"):
        return
    labels = acc.get("labels") or {}
    doc.section(labels.get("heading") or "By account")
    doc.ptext(acc.get("caption"), "muted")
    headers = [labels.get("col_account"), labels.get("col_net"), labels.get("col_share"),
               labels.get("col_rov"), labels.get("col_volume"), labels.get("col_trades"),
               labels.get("col_win_rate"), labels.get("col_max_dd"), labels.get("col_best"),
               labels.get("col_worst"), labels.get("col_style")]
    rows = []
    for r in acc.get("rows") or []:
        rows.append([
            doc.cell(r.get("label")), doc.cell(r.get("net_display"), r.get("tone")),
            doc.cell(r.get("share_display")), doc.cell(r.get("rov_display")),
            doc.cell(r.get("volume_display")), doc.cell(r.get("trades")),
            doc.cell(r.get("win_rate_display")), doc.cell(r.get("max_dd_display")),
            doc.cell(r.get("best_display")), doc.cell(r.get("worst_display")),
            doc.cell(r.get("style_display")),
        ])
    total = acc.get("total") or {}
    if total:
        rows.append([
            doc.cell(total.get("label")), doc.cell(total.get("net_display"), total.get("tone")),
            doc.cell(total.get("share_display")), doc.cell(total.get("rov_display")),
            doc.cell(total.get("volume_display")), doc.cell(total.get("trades")),
            doc.cell(total.get("win_rate_display")), doc.cell(""), doc.cell(""), doc.cell(""),
            doc.cell(""),
        ])
    doc.table(headers, rows)
    for ins in acc.get("insights") or []:
        doc.ptext(ins.get("lead"))
        doc.ptext(ins.get("body"), "muted")
    doc.ptext(acc.get("none"), "muted")
    doc.ptext(acc.get("shared_display"), "muted")
    for r in acc.get("rows") or []:
        doc.ptext(r.get("caption"), "muted")


def _trend_table(doc, title, headers, rows):
    if not rows:
        return
    doc.add("<h3>%s</h3>" % _esc(title))
    doc.table(headers, rows)


def _trend(doc):
    tr = doc.p.get("trend") or {}
    if not tr.get("show"):
        return
    T = doc.L["trend"]
    labels = tr.get("labels") or {}
    doc.section(T.get("heading") or "Trend")
    doc.ptext(T.get("caption"), "muted")
    doc.ptext(tr.get("coverage_display"))
    for c in tr.get("callouts") or []:
        doc.ptext(c.get("lead"))
        doc.ptext(c.get("body"), "muted")
    doc.ptext(tr.get("none"), "muted")
    h = [labels.get("col_alignment") or T.get("col_alignment"),
         labels.get("col_trades") or T.get("col_trades"),
         labels.get("col_share") or T.get("col_share"),
         labels.get("col_win_rate") or T.get("col_win_rate"),
         labels.get("col_net") or T.get("col_net"),
         labels.get("col_avg") or T.get("col_avg")]

    def rows_of(seq):
        return [[doc.cell(r.get("label")), doc.cell(r.get("trades")), doc.cell(r.get("share_display")),
                 doc.cell(r.get("win_rate_display")), doc.cell(r.get("net_display"), r.get("tone")),
                 doc.cell(r.get("avg_display"))] for r in seq]

    _trend_table(doc, T.get("col_alignment") or "", h, rows_of(tr.get("alignment") or []))
    _trend_table(doc, T.get("by_regime") or "", h, rows_of(tr.get("regime") or []))
    _trend_table(doc, T.get("by_direction") or "", h, rows_of(tr.get("direction") or []))
    split_h = [T.get("col_venue"), T.get("col_tagged"), T.get("col_with"), T.get("col_counter"),
               T.get("col_with_net"), T.get("col_counter_net")]
    venues = [[doc.cell(r.get("label")), doc.cell(r.get("tagged")), doc.cell(r.get("with_display")),
               doc.cell(r.get("counter_display")), doc.cell(r.get("with_net_display"), r.get("with_tone")),
               doc.cell(r.get("counter_net_display"), r.get("counter_tone"))] for r in tr.get("venues") or []]
    _trend_table(doc, T.get("by_venue") or "", split_h, venues)
    acct_h = [T.get("col_account")] + split_h[1:]
    accts = [[doc.cell(r.get("label")), doc.cell(r.get("tagged")), doc.cell(r.get("with_display")),
              doc.cell(r.get("counter_display")), doc.cell(r.get("with_net_display"), r.get("with_tone")),
              doc.cell(r.get("counter_net_display"), r.get("counter_tone"))] for r in tr.get("accounts") or []]
    _trend_table(doc, T.get("by_account") or "", acct_h, accts)
    doc.ptext(tr.get("basis_display"), "muted")
    doc.ptext(tr.get("rules"), "muted")


def _assets(doc):
    assets = doc.p.get("assets") or {}
    classes = assets.get("classes") or []
    groups = assets.get("groups") or {}
    if not classes and not (groups.get("rows") or []):
        return
    doc.section(doc.U["assets"])
    if groups:
        doc.ptext(doc.U["assets_caption"].format(
            count=groups.get("count"), with_closes=groups.get("with_closes"),
            multi=groups.get("multi_venue_count")), "muted")
    if classes:
        doc.table(
            [doc.U["col_class"], doc.U["col_net"], doc.U["col_detail"]],
            [[doc.cell(c.get("label")), doc.cell(c.get("net_display"), c.get("tone")),
              doc.cell(doc.U["class_meta"].format(trades=c.get("closed_trades"), wr=c.get("win_rate_display"),
                                                  assets=c.get("assets")))] for c in classes])
    by = {r.get("group"): r for r in groups.get("rows") or []}

    def titles(keys, heading_key, n_key):
        rows = [by[k] for k in keys if k in by]
        if not rows:
            return
        doc.add("<h3>%s</h3>" % _esc(doc.U[heading_key].format(n=len(rows)) if n_key else doc.U[heading_key]))
        doc.bullets([r.get("title") for r in rows])

    titles(groups.get("best") or [], "best_n", True)
    titles(groups.get("worst") or [], "worst_n", True)
    multi = [by[k] for k in (groups.get("multi_venue") or []) if k in by]
    if multi:
        doc.add("<h3>%s</h3>" % _esc(doc.U["multi_venue_fmt"].format(
            n=len(multi), total=groups.get("multi_venue_count"))))
        doc.bullets([r.get("title") for r in multi])


def _trades(doc):
    if not (doc.p.get("has") or {}).get("trades"):
        return
    for key, title in (("best_trades", "best_trades"), ("worst_trades", "worst_trades")):
        rows = doc.p.get(key) or []
        if not rows:
            continue
        doc.section(doc.U[title])
        doc.table(
            [doc.U["col_asset"], doc.U["col_venue"], doc.U["col_date"], doc.U["col_side"],
             doc.U["col_hold"], doc.U["col_net"], doc.U["col_flags"]],
            [[doc.cell(t.get("sym")), doc.cell("%s %s" % (t.get("src") or "", t.get("cls") or "")),
              doc.cell(t.get("date")), doc.cell(t.get("side")), doc.cell(t.get("hold_display")),
              doc.cell(t.get("pnl_display"), t.get("tone")),
              doc.cell("; ".join(t.get("flags") or []) or "-")] for t in rows])


def _months(doc):
    journey = doc.p.get("journey") or {}
    if (doc.p.get("has") or {}).get("journey"):
        doc.section(doc.U["journey"])
        doc.add(_chart(journey))
        peak, low = journey.get("peak") or {}, journey.get("low") or {}
        doc.ptext(peak.get("label"))
        doc.ptext(low.get("label"))
        doc.section(doc.U["monthly"])
    recent = doc.p.get("recent_months") or []
    if recent:
        doc.add("<h3>%s</h3>" % _esc(doc.U["since_fmt"].format(label=recent[0].get("label"))))
        doc.table(
            [doc.U["col_month"], doc.U["col_net"], doc.U["col_win_rate"], doc.U["col_cumulative"]],
            [[doc.cell(m.get("label")), doc.cell(m.get("net_display"), m.get("tone")),
              doc.cell(m.get("win_rate_display")), doc.cell(m.get("cumulative_display"))] for m in recent])
    for key, title in (("best_months", "best_months"), ("worst_months", "worst_months")):
        rows = doc.p.get(key) or []
        if not rows:
            continue
        doc.add("<h3>%s</h3>" % _esc(doc.U[title]))
        doc.table(
            [doc.U["col_month"], doc.U["col_net"], doc.U["col_win_rate"]],
            [[doc.cell(m.get("label")), doc.cell(m.get("net_display"), "success" if key == "best_months" else "danger"),
              doc.cell(m.get("win_rate_display"))] for m in rows])


def _years(doc):
    years = doc.p.get("years") or []
    if not years:
        return
    doc.section(doc.U["by_year"])
    cols = doc.p.get("year_columns") or []
    headers = [doc.U["col_year"], doc.U["col_total"]] + [c.get("label") for c in cols] + [
        doc.U["col_trades"], doc.U["col_win_rate"], doc.U["col_fills"]]
    rows = []
    for y in years:
        cells = [doc.cell(y.get("year")), doc.cell(y.get("all_net_display"), y.get("tone"))]
        for c in y.get("cells") or []:
            cells.append(doc.cell(c.get("display"), c.get("tone")))
        cells += [doc.cell(y.get("closed_trades")), doc.cell(y.get("win_rate_display")), doc.cell(y.get("fills"))]
        rows.append(cells)
    doc.table(headers, rows)


def _activity(doc):
    if not (doc.p.get("has") or {}).get("trades"):
        return
    act = doc.p.get("activity") or {}
    if not act:
        return
    doc.section(doc.U["activity"])
    busy = act.get("busiest_month") or {}
    if busy:
        doc.ptext(doc.U["activity_caption"].format(
            avg=act.get("avg_fills_per_day_display"), month=busy.get("label"),
            fills=busy.get("fills_display")), "muted")
    wd = act.get("weekday") or []
    if wd:
        doc.add("<h3>%s</h3>" % _esc(doc.U["weekday"]))
        doc.table([doc.U["col_period"], doc.U["col_fills"], doc.U["col_share"]],
                  [[doc.cell(r.get("label")), doc.cell(r.get("display")), doc.cell(r.get("share_display"))]
                   for r in wd])
    hold = act.get("holding") or []
    if hold:
        doc.add("<h3>%s</h3>" % _esc(doc.U["holding"]))
        doc.table([doc.U["holding_pnl"], doc.U["col_trades"], doc.U["col_win_rate"], doc.U["col_net"]],
                  [[doc.cell(r.get("label")), doc.cell(r.get("trades_display")),
                    doc.cell(r.get("win_rate_display")), doc.cell(r.get("pnl_display"), r.get("tone"))]
                   for r in hold])


def _persona(doc):
    per = doc.p.get("persona") or {}
    if not per:
        return
    doc.section(doc.U["persona"])
    bits = [per.get("archetype_label") or "", per.get("grade") or "", per.get("grade_label") or ""]
    doc.ptext(" · ".join(b for b in bits if b))
    for m in per.get("modifier_labels") or []:
        doc.ptext(m, "muted")
    ratings = per.get("ratings") or []
    if ratings:
        doc.add("<h3>%s</h3>" % _esc(doc.U["ratings"]))
        doc.table([doc.U["col_rating"], doc.U["col_score"], ""],
                  [[doc.cell(r.get("label")), doc.cell(r.get("display")), doc.cell(r.get("bar"))] for r in ratings])
    scores = per.get("scores") or []
    if scores:
        doc.add("<h3>%s</h3>" % _esc(doc.U["archetypes"]))
        doc.table([doc.U["col_type"], doc.U["col_score"]],
                  [[doc.cell(r.get("label")), doc.cell(r.get("display"))] for r in scores])


def _positions(doc):
    pos = doc.p.get("positions") or {}
    counts = pos.get("counts") or {}
    if not any(counts.get(k) for k in ("rh", "hl_spot", "hl_perp")):
        return
    flags = doc.p.get("flag_labels") or {}
    doc.section(doc.U["positions"])
    rh = pos.get("rh") or []
    if rh:
        doc.add("<h3>%s</h3>" % _esc(doc.U["rh_positions"]))
        doc.table(
            [doc.U["col_symbol"], doc.U["col_class"], doc.U["col_side"], doc.U["col_qty"],
             doc.U["col_avg"], doc.U["col_cost"], doc.U["col_history"], doc.U["col_flags"]],
            [[doc.cell(r.get("sym")), doc.cell(r.get("cls")), doc.cell(r.get("side")),
              doc.cell(r.get("qty_display")), doc.cell(r.get("avg_price_display")),
              doc.cell(r.get("cost_basis_display")), doc.cell(r.get("history_display"), r.get("history_tone")),
              doc.cell("; ".join(flags.get(f, f) for f in r.get("flags") or []) or "-")] for r in rh])
    perp = pos.get("hl_perp") or []
    if perp:
        doc.add("<h3>%s</h3>" % _esc(doc.U["hl_perps"]))
        doc.table(
            [doc.U["col_coin"], doc.U["col_side"], doc.U["col_size"], doc.U["col_entry"],
             doc.U["col_mark"], doc.U["col_lev"], doc.U["col_upnl"], doc.U["col_liq"], doc.U["col_flags"]],
            [[doc.cell(r.get("coin")), doc.cell(r.get("side")), doc.cell(r.get("size_display")),
              doc.cell(r.get("entry_display")), doc.cell(r.get("mark_display")),
              doc.cell(r.get("leverage_display")), doc.cell(r.get("upnl_display"), r.get("upnl_tone")),
              doc.cell(r.get("liq_distance_display")),
              doc.cell("; ".join(flags.get(f, f) for f in r.get("flags") or []) or "-")] for r in perp])
    spot = pos.get("hl_spot") or []
    if spot:
        doc.add("<h3>%s</h3>" % _esc(doc.U["hl_spot"]))
        doc.table(
            [doc.U["col_coin"], doc.U["col_qty"], doc.U["col_entry"], doc.U["col_flags"]],
            [[doc.cell(r.get("coin")), doc.cell(r.get("qty_display")), doc.cell(r.get("entry_display")),
              doc.cell("; ".join(flags.get(f, f) for f in r.get("flags") or []) or "-")] for r in spot])


def _quality(doc):
    dq = doc.p.get("data_quality") or {}
    if not dq:
        return
    doc.section(doc.U["data_quality"])
    if dq.get("required_failed"):
        doc.add('<p class="banner danger">%s</p>' % _esc(doc.H["dq_fail"]))
    else:
        doc.add('<p class="banner success">%s</p>' % _esc(doc.H["dq_ok"]))
    if dq.get("optional_failed"):
        doc.ptext(doc.H["dq_optional"].format(n=dq.get("optional_failed")), "muted")
    if (doc.p.get("has") or {}).get("rh_coverage") and dq.get("rh_coverage_start"):
        doc.ptext(doc.U["rh_coverage"] % dq.get("rh_coverage_start"))
    doc.table(
        [doc.U["col_check"], doc.U["col_result"], doc.U["col_type"], doc.U["col_detail"]],
        [[doc.cell(c.get("label")), doc.cell(doc.U["pass"] if c.get("pass") else doc.U["fail"],
                                             "success" if c.get("pass") else "danger"),
          doc.cell(doc.U["required"] if c.get("required") else doc.U["optional"]),
          doc.cell(c.get("detail"))] for c in dq.get("checks") or []])
    gains = dq.get("gain_sources") or []
    if gains:
        doc.add("<h3>%s</h3>" % _esc(doc.U["gain_sources"]))
        doc.table(
            [doc.U["col_source"], doc.U["col_records"], doc.U["col_net"], doc.U["col_share"]],
            [[doc.cell(g.get("label")), doc.cell(g.get("trades")),
              doc.cell(g.get("pnl_display"), g.get("tone")), doc.cell(g.get("share_display"))] for g in gains])
    gaps = dq.get("gaps") or []
    if gaps:
        doc.add("<h3>%s</h3>" % _esc(doc.U["gaps"]))
        for g in gaps:
            doc.ptext(g.get("title"))
            doc.ptext(g.get("detail"), "muted")


def _commentary(doc, copy):
    if not copy:
        return
    doc.section(doc.H["commentary"])
    doc.ptext(copy.get("persona"))
    doc.ptext(copy.get("verdict"))
    rules = copy.get("rules") or []
    if rules:
        doc.add("<h3>%s</h3>" % _esc(doc.H["rules"]))
        doc.bullets(rules)
    if copy.get("question"):
        doc.add("<h3>%s</h3>" % _esc(doc.H["question"]))
        doc.ptext(copy.get("question"))


def render(page, main_cards, account_sets, copy, version):
    """HTML document. `main_cards` and each account set's cards are dicts with
    id, svg, png, svg_text, png_b64. `copy` is a checked card-copy dict or None."""
    lang = page.get("lang") or "en"
    labels = persona.labels(lang)
    doc = _Doc(page, labels)
    U, H = doc.U, doc.H
    scope = (page.get("scope") or {}).get("label") or ""
    title = "%s · %s" % (U["title"], scope) if scope else U["title"]
    period = page.get("period") or {}
    try:
        summary = U["summary_fmt"].format(scope=scope, period=period.get("display") or "",
                                          days=period.get("days"), as_of=page.get("as_of_display") or "")
    except (KeyError, ValueError):
        summary = scope
    doc.add("<!DOCTYPE html>")
    doc.add('<html lang="%s">' % _esc(page.get("html_lang") or lang))
    doc.add("<head><meta charset=\"utf-8\">")
    doc.add('<meta name="viewport" content="width=device-width, initial-scale=1">')
    doc.add("<title>%s</title><style>%s</style></head><body><main>" % (_esc(title), _CSS))
    doc.add("<h1>%s</h1>" % _esc(title))
    doc.ptext(summary, "muted")
    doc.section(U["tldr"])
    doc.bullets(page.get("tldr") or [])
    doc.section(U["share_card"])
    doc.ptext(U["card_note"], "muted")
    if main_cards:
        doc.add(_card_html(main_cards))
        _paths_html(doc, main_cards)
    else:
        doc.ptext(H["no_cards"], "muted")
    doc.section(U["scoreboard"])
    doc.add('<div class="metrics">')
    for item in page.get("headline") or []:
        shown = item.get("exact") or item.get("value") or ""
        cls = _tone(item.get("tone"))
        extra = ' class="%s"' % cls if cls else ""
        detail = item.get("detail") or ""
        doc.add("<div class=\"metric\"><span>%s</span><b%s>%s</b>%s</div>" % (
            _esc(item.get("label")), extra, _esc(shown),
            ('<span class="muted">%s</span>' % _esc(detail)) if detail else ""))
    doc.add("</div>")
    prof = page.get("profile") or {}
    if prof.get("by"):
        doc.section(U["trader_profile"])
        for v in prof.get("venues") or []:
            one = (prof.get("by") or {}).get(v.get("key"))
            if one:
                _profile(doc, one, v.get("label") or v.get("key") or "")
    _accounts(doc)
    _months(doc)
    _years(doc)
    _assets(doc)
    _trades(doc)
    _activity(doc)
    _trend(doc)
    _persona(doc)
    _positions(doc)
    _quality(doc)
    _commentary(doc, copy)
    if account_sets:
        doc.section(H["per_account"])
        for block in account_sets:
            doc.add("<h3>%s</h3>" % _esc(block.get("label")))
            cards = block.get("cards") or []
            if cards:
                doc.add(_card_html(cards))
                _paths_html(doc, cards)
    doc.add("<footer>%s</footer>" % _esc(H["footer"].format(version=version)))
    doc.add("</main></body></html>\n")
    return "\n".join(doc.out)


def _copy(out_dir, copy_path, summary, lang):
    path = copy_path or os.path.join(out_dir, "card-copy.json")
    if copy_path is None and not os.path.exists(path):
        return None, None, "template"
    if not os.path.exists(path):
        return None, {"error": "copy file not found: %s" % path}, None
    copy, probs = card_mod.load_copy(path, summary, lang)
    if probs:
        return None, {"error": "copy.json rejected; fix it or remove it", "problems": probs}, None
    return copy, None, "card-copy.json"


def _write(path, text):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def run(out_dir, lang=None, copy_path=None):
    """Write report.html. Does not modify page.json or the card files."""
    docs = {n: read_json(os.path.join(out_dir, n + ".json"))
            for n in ("summary", "series", "open_positions", "data_quality")}
    missing = sorted(n for n, d in docs.items() if d is None)
    if missing:
        return {"error": "missing %s in %s (run `lsl compute` first)" % (", ".join(missing), out_dir)}
    if lang is None:
        existing = read_json(os.path.join(out_dir, "page.json")) or {}
        lang = existing.get("lang") or "en"
    if lang not in ("en", "zh"):
        return {"error": "lang must be en or zh"}
    copy, err, copy_source = _copy(out_dir, copy_path, docs["summary"], lang)
    if err:
        return err
    built = page_mod.build(docs["summary"], docs["series"], docs["open_positions"],
                           docs["data_quality"], lang, copy)
    on_disk = _discover(out_dir, "share-card")
    main = _load_set(on_disk or _fallback_cards(built))
    account_sets = []
    rows = (built.get("accounts") or {}).get("rows") or []
    cards_dir = os.path.join(out_dir, "cards")
    for r in rows:
        if not r.get("slug"):
            continue
        found = _discover(cards_dir, r["slug"])
        if found:
            account_sets.append({"label": r.get("label"), "cards": _load_set(found)})
    text = render(built, main, account_sets, copy, _version())
    dest = os.path.join(out_dir, REPORT_NAME)
    _write(dest, text)
    pngs = [c["png"] for c in main if c.get("png")]
    for block in account_sets:
        pngs += [c["png"] for c in block["cards"] if c.get("png")]
    return {"status": "done", "out": os.path.abspath(dest), "lang": lang,
            "scope": (built.get("scope") or {}).get("id"),
            "cards": [{"id": c["id"], "svg": c.get("svg"), "png": c.get("png"),
                       "embedded": bool(c.get("svg_text") or c.get("png_b64"))} for c in main],
            "pngs": pngs, "copy": copy_source,
            "accounts": [{"label": b["label"],
                          "cards": [{"id": c["id"], "svg": c.get("svg"), "png": c.get("png")}
                                    for c in b["cards"]]} for b in account_sets]}

