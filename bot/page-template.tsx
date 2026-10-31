import { Page, Section, Grid, Metrics, Table, Callout, Rows, Text, Bars, Columns, ColumnSeries, LineChart, Heatmap, Segmented, BulletList, useState } from "grok/canvas";

// D = trading-journey `bin/lsl page` output (out/page.json), pasted verbatim. The page only selects and slices; it does no arithmetic on figures.
const D = __PAGE_JSON__;
// END D

type Tone = "success" | "warning" | "danger";
const t = (x: string | null | undefined): Tone | undefined => (x === "success" || x === "warning" || x === "danger" ? x : undefined);
const cell = (text: string, tone?: string | null) => (t(tone) ? { text, tone: t(tone) } : text);
const H = D.headline;
const metric = (h: any) => ({ value: h.value, label: h.label, detail: [h.exact, h.detail].filter(Boolean).join(" · ") || undefined, tone: t(h.tone) });
const scope = (id: string) => D.scopes.find((s) => s.scope === id)!;
const cls = (label: string) => D.assets.classes.find((c) => c.label === label)!;
const hold = (label: string) => D.activity.holding.find((h) => h.label === label)!;
const acctKind = (tag: string) => (D.data_quality.rh_accounts.find((a) => a.tag === tag) || { kind: tag }).kind;
const flagText = (fs: readonly string[]) => fs.map((f) => (D.flag_labels as Record<string, string>)[f] || f).join("; ");
const G = D.assets.groups;
const groupRow = (key: string) => G.rows.find((r) => r.group === key)!;
const venueSymbol = (v: any) => (v.multiplier_display ? `${v.raw_symbol} (${v.multiplier_display})` : v.raw_symbol);

function GroupSection({ r }: { r: (typeof G.rows)[number] }) {
  return (
    <Section
      heading={r.title}
      caption={`${r.kind} · ${r.closed_trades} closed trades · ${r.win_rate_display} win rate · ${r.fills} fills · fees ${r.fees_display} · biggest win ${r.biggest_win_display} · biggest loss ${r.biggest_loss_display}`}
      collapsible
      defaultOpen={false}
    >
      <Table
        columns={["Venue", "Symbol there", "Net", "Closed trades", "Win rate", "Fills", "Fees", "Biggest win", "Biggest loss", "FIFO-est."]}
        rows={r.venues.map((v) => [v.venue_label, venueSymbol(v), cell(v.net_display, v.tone), String(v.closed_trades), v.win_rate_display, String(v.fills), v.fees_display, v.biggest_win_display, v.biggest_loss_display, String(v.approx_trades)])}
      />
    </Section>
  );
}

export default function TradingJourney() {
  const [win, setWin] = useState<string>("all");
  const [view, setView] = useState<string>("multi");
  const w = D.journey.windows.find((x) => x.id === win)!;
  const s = w.start;
  const J = D.journey;
  const points = J.labels.slice(s).map((label, i) => ({ label, value: J.cumulative_k.all[s + i] }));
  const rs = J.recent.start;
  const P = D.persona;
  const keys = view === "best" ? G.best : view === "worst" ? G.worst : G.multi_venue;
  const GQ = D.data_quality.grouping;

  return (
    <Page
      title="Trading Journey"
      width="wide"
      summary={`Robinhood + Hyperliquid realized P&L, ${D.period.display} (${D.period.days} days). Data as of ${D.as_of_display}. Every figure comes verbatim from trading-journey's deterministic outputs.`}
    >
      <Section heading="Scoreboard">
        <Metrics metrics={H.slice(0, 4).map(metric)} />
        <Metrics metrics={H.slice(4, 8).map(metric)} />
        <Metrics metrics={H.slice(8, 10).map(metric).concat([{ value: P.grade, label: "Grade", detail: P.grade_label, tone: undefined }])} />
      </Section>

      <Section heading="The verdict">
        {/* BOT: write the interpretation here. Prose only; every figure is a ${D...} field, never a typed numeral or your own arithmetic. */}
        <Callout tone="warning" lead={`${P.archetype_label} ${P.modifier_labels.join(" ")} · Grade ${P.grade} (${P.grade_label})`}>
          {`VERDICT_LEAD: one or two sentences on the headline, e.g. win rate ${H[2].value} vs payoff ${H[3].value}.`}
        </Callout>
        <Text>{`VERDICT_ARC: the journey, e.g. peak ${J.peak.label}, low ${J.low.label}, change since peak ${J.drawdown_from_peak_display}.`}</Text>
        <Grid columns={2}>
          <Section heading="What actually works">
            <BulletList marker="pro" items={[
              `PRO_1: e.g. best asset class ${D.assets.classes[0].label} ${D.assets.classes[0].net_display}.`,
            ]} />
          </Section>
          <Section heading="What keeps blowing up">
            <BulletList marker="con" items={[
              `CON_1: e.g. worst single trade ${D.worst_trades[0].sym} ${D.worst_trades[0].pnl_display}.`,
            ]} />
          </Section>
        </Grid>
        <Section heading="Questions to sit with">
          <BulletList items={["QUESTION_1", "QUESTION_2", "QUESTION_3"]} />
        </Section>
      </Section>

      <Section heading="Cumulative realized P&L" caption="Thousands of USD, month-end, forward-filled. Robinhood vs Hyperliquid vs combined.">
        <Segmented options={D.journey.windows.map((x) => ({ value: x.id, label: x.label }))} value={win} onChange={setWin} />
        <LineChart
          points={points}
          ceiling={w.ceiling}
          floor={w.floor}
          unit={J.unit}
          labels={["All sources"]}
          lines={[{ label: "Robinhood", values: J.cumulative_k.rh.slice(s) }, { label: "Hyperliquid", values: J.cumulative_k.hl.slice(s) }]}
          annotations={w.annotations.map((a) => ({ at: a.at, label: a.label }))}
        />
        <Table
          columns={["Scope", "Net realized", "Incl. HL funding", "Win rate", "Avg win / avg loss", "Closed trades", "Fills", "Fees", "FIFO-estimated", "Period"]}
          rows={D.scopes.map((x) => [x.name, cell(x.net_pnl_display, x.tone), x.net_incl_funding_display, x.win_rate_display, x.payoff_display, String(x.closed_trades), String(x.fills), x.fees_display, x.fifo_share_display, x.period_display])}
        />
      </Section>

      <Section heading="Monthly P&L, recent" caption={`Thousands of USD per month, Robinhood vs Hyperliquid, ${J.recent.labels[0]} to ${J.recent.labels[J.recent.labels.length - 1]}.`}>
        <ColumnSeries
          labels={J.recent.labels}
          series={[{ name: "Robinhood", values: J.monthly_net_k.rh.slice(rs) }, { name: "Hyperliquid", values: J.monthly_net_k.hl.slice(rs) }]}
          unit={J.unit}
          ceiling={J.recent.ceiling}
        />
        <Grid columns={3}>
          <Section heading={`Since ${D.recent_months[0].label}`}>
            <Table columns={["Month", "Net", "Win rate", "Cumulative"]} rows={D.recent_months.map((m) => [m.label, cell(m.net_display, m.tone), m.win_rate_display, m.cumulative_display])} />
          </Section>
          <Section heading="Best months ever">
            <Table columns={["Month", "Net", "Win rate"]} rows={D.best_months.map((m) => [m.label, cell(m.net_display, "success"), m.win_rate_display])} />
          </Section>
          <Section heading="Worst months ever">
            <Table columns={["Month", "Net", "Win rate"]} rows={D.worst_months.map((m) => [m.label, cell(m.net_display, "danger"), m.win_rate_display])} />
          </Section>
        </Grid>
      </Section>

      <Section heading="Year by year" caption="Net realized P&L per calendar year, thousands of USD.">
        <Columns points={D.years.map((y) => ({ label: y.year, value: y.net_k, display: y.all_net_display }))} />
        <Table
          columns={["Year", "All", "Robinhood", "Hyperliquid", "Closed trades", "Win rate", "Fills"]}
          rows={D.years.map((y) => [y.year, cell(y.all_net_display, y.tone), y.rh_net_display, y.hl_net_display, String(y.closed_trades), y.win_rate_display, String(y.fills)])}
        />
      </Section>

      <Section heading="Where the money came from (and went)" caption="Net realized P&L by venue and asset class, thousands of USD.">
        <Bars bars={D.assets.classes.map((c) => ({ label: c.label, value: c.net_k, display: c.net_display, meta: `${c.closed_trades} trades · ${c.win_rate_display} wins · ${c.assets} assets` }))} />
        <Grid columns={2}>
          <Section heading="Best single trades">
            <Table columns={["Asset", "Venue", "Closed", "Side", "Held", "P&L"]} rows={D.best_trades.map((x) => [x.sym, `${x.src} ${x.cls}`, x.date, x.side, x.hold_display, cell(x.pnl_display, x.tone)])} />
          </Section>
          <Section heading="Worst single trades">
            <Table columns={["Asset", "Venue", "Closed", "Side", "Held", "P&L", "Flags"]} rows={D.worst_trades.map((x) => [x.sym, `${x.src} ${x.cls}`, x.date, x.side, x.hold_display, cell(x.pnl_display, x.tone), x.flags.join(", ") || "-"])} />
          </Section>
        </Grid>
      </Section>

      <Section
        heading="Assets across venues"
        caption={`The same asset traded on Robinhood stocks, options or crypto and on Hyperliquid spot, perps or a HIP-3 dex is grouped into one row. ${G.count} assets after grouping, ${G.with_closes} with closed trades, ${G.multi_venue_count} traded on more than one venue, ${GQ.ambiguous_count} left ungrouped as ambiguous. Each row shows the asset's totals; open it to see each venue.`}
      >
        <Segmented
          options={[
            { value: "multi", label: `Biggest multi-venue (${G.multi_venue.length} of ${G.multi_venue_count})` },
            { value: "best", label: `Best ${G.best.length}` },
            { value: "worst", label: `Worst ${G.worst.length}` },
          ]}
          value={view}
          onChange={setView}
        />
        {keys.map((k) => <GroupSection key={k} r={groupRow(k)} />)}
        <Text>{GQ.ambiguous_count ? `Ungrouped (ambiguous): ${GQ.ambiguous.map((a: any) => a.symbol ?? String(a)).join(", ")}` : "No symbols were left ungrouped. Multi-venue list ranks by the size of the net result, win or loss; the full list is in series.json."}</Text>
      </Section>

      <Section heading="Activity and holding times" caption={`Average ${D.activity.avg_fills_per_day_display} fills per day · busiest month ${D.activity.busiest_month.label} (${D.activity.busiest_month.fills_display} fills) · busiest day ${H[9].detail} (${H[9].value}) · median hold ${H[8].value}, ${H[8].detail}`}>
        <Grid columns={2}>
          <Section heading="Fills by weekday">
            <Columns points={D.activity.weekday.map((d) => ({ label: d.label, value: d.value, display: `${d.display} (${d.share_display})` }))} />
          </Section>
          <Section heading="P&L by holding time" caption="Thousands of USD">
            <Bars bars={D.activity.holding.map((h) => ({ label: h.label, value: h.pnl_k, display: h.pnl_display, meta: `${h.trades_display} trades · ${h.win_rate_display} wins` }))} />
          </Section>
        </Grid>
        <Section heading="Fills per month">
          <Heatmap rows={D.activity.heat_years} columns={D.activity.heat_months} values={D.activity.heat_fills} max={D.activity.heat_max} />
        </Section>
      </Section>

      <Section heading="Trader profile" caption="Archetype scores and ratings from the trading-journey persona model.">
        <Grid columns={2}>
          <Section heading="Archetype scores">
            <Bars bars={P.scores.map((x) => ({ label: x.label, value: x.value, display: x.display }))} />
          </Section>
          <Section heading="Ratings">
            <Table columns={["Rating", "Score", ""]} rows={P.ratings.map((r) => [r.label, r.display, r.bar])} />
          </Section>
        </Grid>
      </Section>

      <Section heading="Open positions and risk flags" caption={`Robinhood: ${D.positions.counts.rh} positions (${D.positions.counts.rh_equity} stocks, ${D.positions.counts.rh_option} options), stock cost basis ${D.positions.rh_equity_cost_basis_display}. Hyperliquid: ${D.positions.counts.hl_spot} spot balances, ${D.positions.counts.hl_perp} perps. Cost only; no mark prices were fetched.`}>
        <Rows rows={D.positions.flag_counts.map((f) => ({ title: f.label, meta: String(f.count), status: { label: f.flag.replace(/_/g, " "), tone: f.flag === "no_mark_price" || f.flag === "near_zero_cost" ? undefined : ("warning" as Tone) } }))} />
        <Section heading="Robinhood">
          <Table
            columns={["Symbol", "Account", "Class", "Side", "Qty", "Avg price", "Cost basis", "Share of RH stock cost", "Expires", "Your track record here", "Flags"]}
            rows={D.positions.rh.map((r) => [r.sym, acctKind(r.acct), r.cls, r.side, r.qty_display, r.avg_price_display, r.cost_basis_display, r.share_display, r.expiration ? `${r.expiration} (${r.days_to_expiry}d)` : "-", cell(r.history_display, r.history_tone), r.flags.length ? cell(flagText(r.flags), "warning") : "-"])}
          />
        </Section>
        <Section heading="Hyperliquid spot">
          <Table columns={["Token", "Qty", "Avg entry", "Flags"]} rows={D.positions.hl_spot.map((r) => [r.coin, r.qty_display, r.entry_display, flagText(r.flags)])} />
          <Text>{D.positions.hl_perp.length ? "" : "No open Hyperliquid perps. The leverage is in the rear-view mirror for now."}</Text>
        </Section>
      </Section>

      <Section heading="Data quality" caption={`${D.data_quality.checks_total} checks · ${D.data_quality.required_failed} required failed · ${D.data_quality.optional_failed} optional failed`}>
        <Callout tone="info" lead={`Robinhood gains before ${D.data_quality.rh_coverage_start} are FIFO estimates`}>
          {`Robinhood's realized-gain data starts at ${D.data_quality.rh_coverage_start}. Earlier closes are rebuilt by FIFO from order fills: ${D.data_quality.approx_trades} trade records, ${D.data_quality.approx_share_display} of all records. Treat every Robinhood figure from before then as an estimate.`}
        </Callout>
        <Grid columns={2}>
          <Section heading="Where each P&L figure comes from">
            <Table columns={["Source", "Trade records", "Share", "P&L"]} rows={D.data_quality.gain_sources.map((g) => [g.label, String(g.trades), g.share_display, cell(g.pnl_display, g.tone)])} />
          </Section>
          <Section heading="Known gaps">
            <Rows rows={D.data_quality.gaps.map((g) => ({ title: g.title, detail: g.detail }))} />
          </Section>
        </Grid>
        <Section heading="Robinhood accounts">
          <Table
            columns={["Account", "Fills", "RH P&L from", "FIFO closes before", "RH-sourced closes", "No cost basis", "FIFO dropped", "Expiry closes", "Position mismatches", "Possible tie gaps"]}
            rows={D.data_quality.rh_accounts.map((a) => [`${a.kind} (${a.tag})`, String(a.fills), a.coverage_start, String(a.fifo_before), String(a.rh_sourced), String(a.no_cost_basis), String(a.fifo_dropped), String(a.expiry_closes), String(a.positions_mismatch), String(a.possible_tie_gaps)])}
          />
          <Text>{`Hyperliquid: ${D.data_quality.hl.fills_used} of ${D.data_quality.hl.fills_raw} fills used (${D.data_quality.hl.dust_fills_excluded} dust fills excluded; ${D.data_quality.hl.spot_fills_excluded} spot fills in non-whitelisted tokens excluded, ${D.data_quality.hl.spot_trades_excluded} of them closing trades), ${D.data_quality.hl.funding_events} funding events, ${D.data_quality.hl.liquidation_fills_self} liquidation fills. Spot counts only HYPE and Unit-bridged tokens.`}</Text>
        </Section>
        <Section heading="Checks">
          <Table
            columns={["Check", "Scope", "Type", "Result", "Detail"]}
            rows={D.data_quality.checks.map((c) => [c.label, c.scope, c.required ? "required" : "optional", c.pass ? cell("pass", "success") : cell("fail", c.required ? "danger" : "warning"), c.detail])}
          />
        </Section>
        <Text>Realized P&L only. Not tax or investment advice. Rebuilt by trading-journey from Robinhood MCP and Hyperliquid API data; this page shows the script's numbers and adds commentary only.</Text>
      </Section>
    </Page>
  );
}
