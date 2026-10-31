import { Page, Section, Grid, Metrics, Table, Callout, Rows, Text, Bars, Columns, ColumnSeries, LineChart, Heatmap, Segmented, BulletList, Properties, Image, Button, TableOfContents, useState } from "grok/canvas";

// D = trading-journey `bin/lsl page --scope <scope> --lang <lang>` output (out/<scope>/page.json), pasted verbatim.
// The page only selects and slices; it does no arithmetic on figures. Sections hide themselves via D.has.
const D = __PAGE_JSON__;
// END D

type Tone = "success" | "warning" | "danger";
const t = (x: string | null | undefined): Tone | undefined => (x === "success" || x === "warning" || x === "danger" ? x : undefined);
const cell = (text: string, tone?: string | null) => (t(tone) ? { text, tone: t(tone) } : text);
const U = D.ui as Record<string, string>;
const fmt = (s: string, v: Record<string, string | number>) => s.replace(/\{(\w+)\}/g, (_m, k) => String(v[k] ?? ""));
const H = D.headline as any[];
const metric = (h: any) => ({ value: h.value, label: h.label, detail: [h.exact, h.detail].filter(Boolean).join(" · ") || undefined, tone: t(h.tone) });
const acctLabel = (tag: string) => ((D.data_quality.rh_accounts as any[]).find((a) => a.tag === tag) || { label: tag }).label;
const flagText = (fs: readonly string[]) => fs.map((f) => (D.flag_labels as Record<string, string>)[f] || f).join("; ");
const G = D.assets.groups as any;
const groupRow = (key: string) => (G.rows as any[]).find((r) => r.group === key)!;
const venueSymbol = (v: any) => (v.multiplier_display ? `${v.raw_symbol} (${v.multiplier_display})` : v.raw_symbol);
const CARD = D.card as any;
const VARIANTS = CARD.variants as Record<string, string>;
const svgUrl = (svg: string) => "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);

function save(href: string, name: string): boolean {
  try {
    const a = document.createElement("a");
    a.href = href;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    return true;
  } catch {
    return false;
  }
}

function savePng(svg: string, name: string, onFail: () => void) {
  try {
    const img = new window.Image();
    img.onload = () => {
      try {
        const c = document.createElement("canvas");
        c.width = CARD.width * 2;
        c.height = CARD.height * 2;
        const g = c.getContext("2d")!;
        g.scale(2, 2);
        g.drawImage(img, 0, 0, CARD.width, CARD.height);
        if (!save(c.toDataURL("image/png"), name + ".png")) onFail();
      } catch {
        onFail();
      }
    };
    img.onerror = onFail;
    img.src = svgUrl(svg);
  } catch {
    onFail();
  }
}

function groupSection(r: any) {
  return (
    <Section
      key={r.group}
      heading={r.title}
      caption={fmt(U.group_caption, { kind: r.kind, trades: r.closed_trades, wr: r.win_rate_display, fills: r.fills, fees: r.fees_display })}
      collapsible
      defaultOpen={false}
    >
      <Table
        columns={[U.col_venue, U.col_symbol_there, U.col_net, U.col_trades, U.col_win_rate, U.col_fills, U.col_fees, U.col_biggest_win, U.col_biggest_loss, U.col_fifo]}
        rows={(r.venues as any[]).map((v) => [v.venue_label, venueSymbol(v), cell(v.net_display, v.tone), String(v.closed_trades), v.win_rate_display, String(v.fills), v.fees_display, v.biggest_win_display, v.biggest_loss_display, String(v.approx_trades)])}
      />
    </Section>
  );
}

export default function TradingJourney() {
  const J = D.journey as any;
  const [win, setWin] = useState<string>("all");
  const [view, setView] = useState<string>("multi");
  const w = (J.windows as any[]).find((x) => x.id === win) || J.windows[0];
  const s = w ? w.start : 0;
  const rs = J.recent.start;
  const lines = J.lines as { key: string; label: string }[];
  const P = D.persona as any;
  const keys: string[] = view === "best" ? G.best : view === "worst" ? G.worst : G.multi_venue;
  const GQ = D.data_quality.grouping as any;
  const DQ = D.data_quality as any;
  const has = D.has as Record<string, boolean>;
  const yc = D.year_columns as { key: string; label: string }[];
  const A = D.activity as any;
  const POS = D.positions as any;
  const PR = D.profile as any;
  const venues = PR.venues as { key: string; label: string }[];
  const [venue, setVenue] = useState<string>(PR.default);
  const [pmode, setPmode] = useState<string>("usd");
  const pb = PR.by[venue];
  const pc = pb.periods;
  const periods = CARD.periods as { id: string; label: string }[];
  const [period, setPeriod] = useState<string>(CARD.default.period);
  const [cmode, setCmode] = useState<string>(CARD.default.mode);
  const [blocked, setBlocked] = useState<boolean>(false);
  const svg = VARIANTS[`${period}.${cmode}`];
  const fname = `${CARD.filename}-${period}${cmode === "pct" ? "-pct" : ""}`;
  const money = [{ value: "usd", label: U.dollars }, { value: "pct", label: U.percent }];
  const toc = [U.tldr, U.share_card, U.trader_profile, U.scoreboard, U.verdict, ...(has.journey ? [U.cumulative] : []),
    ...((D.scopes as any[]).length > 1 ? [U.by_source] : []), ...(has.journey ? [U.monthly] : []), ...((D.years as any[]).length ? [U.by_year] : []),
    ...(has.trades ? [U.where_money] : []), ...((G.rows as any[]).length ? [U.assets] : []), ...(has.trades ? [U.activity] : []),
    U.persona, U.positions, U.data_quality].map((label) => ({ label }));

  return (
    <Page
      title={`${U.title} · ${D.scope.label}`}
      width="wide"
      summary={fmt(U.summary_fmt, { scope: D.scope.label, period: D.period.display, days: D.period.days, as_of: D.as_of_display })}
    >
      <TableOfContents items={toc} />

      <Section heading={U.tldr}>
        <BulletList items={D.tldr as string[]} />
      </Section>

      {svg ? (
        <Section heading={U.share_card} caption={`${D.scope.label} · ${U.card_note}`}>
          <Segmented options={periods.map((p) => ({ value: p.id, label: p.label }))} value={period} onChange={setPeriod} />
          <Segmented options={money} value={cmode} onChange={setCmode} />
          <Image src={svgUrl(svg)} alt={`${U.share_card} · ${D.scope.label}`} fit="contain" />
          <Grid columns={2} plain>
            <Button variant="primary" label={U.download_png} onClick={() => savePng(svg, fname, () => setBlocked(true))} />
            <Button label={U.download_svg} onClick={() => { if (!save(svgUrl(svg), fname + ".svg")) setBlocked(true); }} />
          </Grid>
          {blocked ? <Callout tone="warning">{U.download_blocked}</Callout> : null}
        </Section>
      ) : null}

      <Section heading={U.trader_profile}>
        {venues.length > 1 ? <Segmented options={venues.map((v) => ({ value: v.key, label: v.label }))} value={venue} onChange={setVenue} /> : null}
        <Segmented options={money} value={pmode} onChange={setPmode} />
        <Text>{`${venues.find((v) => v.key === venue)?.label ?? ""} · ${pb.strip_label}: ${pb.strip_display}`}</Text>
        <Metrics metrics={(pb.top as any[]).map((m) => ({ value: pmode === "pct" ? m.pct : m.usd, label: pmode === "pct" && m.pct_label ? m.pct_label : m.label, detail: pmode === "usd" ? m.exact : undefined, tone: t(m.tone) }))} />
        <Properties items={(pb.facts as any[]).map((f) => ({ label: f.label, value: f.value }))} />
        <Section heading={U.performance} caption={pmode === "pct" ? `${U.stats_caption} ${U.pct_note}` : U.stats_caption}>
          <Table
            columns={[U.col_metric, ...(pc.labels as string[])]}
            rows={(pc.rows as any[]).map((r) => [pmode === "pct" && r.pct_label ? r.pct_label : r.label, ...(pmode === "pct" ? r.pct : r.usd)])}
          />
        </Section>
      </Section>

      <Section heading={U.scoreboard}>
        <Text>{D.tldr[0]}</Text>
        <Metrics metrics={H.map(metric)} />
      </Section>

      <Section heading={U.verdict}>
        {/* BOT: write the interpretation here, in D.lang. Prose only; every figure is a ${D...} field, never a typed numeral or your own arithmetic. */}
        <Callout tone="warning" lead={`${P.archetype_label} ${(P.modifier_labels as string[]).join(" ")} · ${P.grade} (${P.grade_label})`}>
          {`VERDICT_LEAD: one or two sentences on the headline, e.g. ${D.tldr[0]}`}
        </Callout>
        <Text>{has.journey ? `VERDICT_ARC: the journey, e.g. ${J.peak.label}, ${J.low.label}.` : "VERDICT_ARC"}</Text>
        <Grid columns={2}>
          <Section heading={U.whats_working}>
            <BulletList marker="pro" items={["PRO_1"]} />
          </Section>
          <Section heading={U.whats_blowing}>
            <BulletList marker="con" items={["CON_1"]} />
          </Section>
        </Grid>
        <Section heading={U.questions}>
          <BulletList items={["QUESTION_1", "QUESTION_2", "QUESTION_3"]} />
        </Section>
      </Section>

      {has.journey && w ? (
        <Section heading={U.cumulative} caption={U.thousands}>
          <Segmented options={(J.windows as any[]).map((x) => ({ value: x.id, label: x.label }))} value={win} onChange={setWin} />
          <LineChart
            points={(J.labels as string[]).slice(s).map((label, i) => ({ label, value: J.cumulative_k.all[s + i] }))}
            ceiling={w.ceiling}
            floor={w.floor}
            unit={J.unit}
            labels={[D.scope.label]}
            lines={has.multi_line ? lines.map((l) => ({ label: l.label, values: (J.cumulative_k[l.key] as number[]).slice(s) })) : undefined}
            annotations={(w.annotations as any[]).map((a) => ({ at: a.at, label: a.label }))}
          />
        </Section>
      ) : null}

      {(D.scopes as any[]).length > 1 ? (
        <Section heading={U.by_source}>
          <Table
            columns={[U.col_scope, U.col_net, ...(has.hl ? [U.col_incl_funding] : []), U.col_win_rate, U.col_payoff, U.col_trades, U.col_fills, U.col_fees, U.col_fifo, U.col_period]}
            rows={(D.scopes as any[]).map((x) => [x.name, cell(x.net_pnl_display, x.tone), ...(has.hl ? [x.net_incl_funding_display] : []), x.win_rate_display, x.payoff_display, String(x.closed_trades), String(x.fills), x.fees_display, x.fifo_share_display, x.period_display])}
          />
        </Section>
      ) : null}

      {has.journey ? (
        <Section heading={U.monthly} caption={U.thousands}>
          <ColumnSeries
            labels={J.recent.labels}
            series={has.multi_line ? lines.map((l) => ({ name: l.label, values: (J.monthly_net_k[l.key] as number[]).slice(rs) })) : [{ name: D.scope.label, values: (J.monthly_net_k.all as number[]).slice(rs) }]}
            unit={J.unit}
            ceiling={J.recent.ceiling}
          />
          <Grid columns={3}>
            {(D.recent_months as any[]).length ? (
              <Section heading={fmt(U.since_fmt, { label: D.recent_months[0].label })}>
                <Table columns={[U.col_month, U.col_net, U.col_win_rate, U.col_cumulative]} rows={(D.recent_months as any[]).map((m) => [m.label, cell(m.net_display, m.tone), m.win_rate_display, m.cumulative_display])} />
              </Section>
            ) : null}
            <Section heading={U.best_months}>
              <Table columns={[U.col_month, U.col_net, U.col_win_rate]} rows={(D.best_months as any[]).map((m) => [m.label, cell(m.net_display, "success"), m.win_rate_display])} />
            </Section>
            <Section heading={U.worst_months}>
              <Table columns={[U.col_month, U.col_net, U.col_win_rate]} rows={(D.worst_months as any[]).map((m) => [m.label, cell(m.net_display, "danger"), m.win_rate_display])} />
            </Section>
          </Grid>
        </Section>
      ) : null}

      {(D.years as any[]).length ? (
        <Section heading={U.by_year} caption={U.thousands}>
          <Columns points={(D.years as any[]).map((y) => ({ label: y.year, value: y.net_k, display: y.all_net_display }))} />
          <Table
            columns={[U.col_year, U.col_total, ...yc.map((c) => c.label), U.col_trades, U.col_win_rate, U.col_fills]}
            rows={(D.years as any[]).map((y) => [y.year, cell(y.all_net_display, y.tone), ...(y.cells as any[]).map((c) => cell(c.display, c.tone)), String(y.closed_trades), y.win_rate_display, String(y.fills)])}
          />
        </Section>
      ) : null}

      {has.trades ? (
        <Section heading={U.where_money} caption={U.thousands}>
          <Bars bars={(D.assets.classes as any[]).map((c) => ({ label: c.label, value: c.net_k, display: c.net_display, meta: fmt(U.class_meta, { trades: c.closed_trades, wr: c.win_rate_display, assets: c.assets }) }))} />
          <Grid columns={2}>
            <Section heading={U.best_trades}>
              <Table columns={[U.col_asset, U.col_venue, U.col_date, U.col_side, U.col_hold, U.col_net]} rows={(D.best_trades as any[]).map((x) => [x.sym, `${x.src} ${x.cls}`, x.date, x.side, x.hold_display, cell(x.pnl_display, x.tone)])} />
            </Section>
            <Section heading={U.worst_trades}>
              <Table columns={[U.col_asset, U.col_venue, U.col_date, U.col_side, U.col_hold, U.col_net, U.col_flags]} rows={(D.worst_trades as any[]).map((x) => [x.sym, `${x.src} ${x.cls}`, x.date, x.side, x.hold_display, cell(x.pnl_display, x.tone), flagText(x.flags) || "-"])} />
            </Section>
          </Grid>
        </Section>
      ) : null}

      {(G.rows as any[]).length ? (
        <Section heading={U.assets} caption={fmt(U.assets_caption, { count: G.count, with_closes: G.with_closes, multi: G.multi_venue_count })}>
          <Segmented
            options={[
              ...(G.multi_venue_count ? [{ value: "multi", label: fmt(U.multi_venue_fmt, { n: G.multi_venue.length, total: G.multi_venue_count }) }] : []),
              { value: "best", label: fmt(U.best_n, { n: G.best.length }) },
              { value: "worst", label: fmt(U.worst_n, { n: G.worst.length }) },
            ]}
            value={!G.multi_venue_count && view === "multi" ? "best" : view}
            onChange={setView}
          />
          {(!G.multi_venue_count && view === "multi" ? (G.best as string[]) : keys).map((k) => groupSection(groupRow(k)))}
          <Text>{GQ.ambiguous_count ? fmt(U.ungrouped, { list: (GQ.ambiguous as any[]).map((a) => a.symbol ?? String(a)).join(", ") }) : U.no_ungrouped}</Text>
        </Section>
      ) : null}

      {has.trades ? (
        <Section heading={U.activity} caption={fmt(U.activity_caption, { avg: A.avg_fills_per_day_display, month: A.busiest_month.label, fills: A.busiest_month.fills_display })}>
          <Grid columns={2}>
            <Section heading={U.weekday}>
              <Columns points={(A.weekday as any[]).map((d) => ({ label: d.label, value: d.value, display: `${d.display} (${d.share_display})` }))} />
            </Section>
            <Section heading={U.holding_pnl} caption={U.thousands}>
              <Bars bars={(A.holding as any[]).map((h) => ({ label: h.label, value: h.pnl_k, display: h.pnl_display, meta: fmt(U.holding_meta, { trades: h.trades_display, wr: h.win_rate_display }) }))} />
            </Section>
          </Grid>
          <Section heading={U.fills_per_month}>
            <Heatmap rows={A.heat_years} columns={A.heat_months} values={A.heat_fills} max={A.heat_max} />
          </Section>
        </Section>
      ) : null}

      <Section heading={U.persona}>
        <Grid columns={2}>
          <Section heading={U.archetypes}>
            <Bars bars={(P.scores as any[]).map((x) => ({ label: x.label, value: x.value, display: x.display }))} />
          </Section>
          <Section heading={U.ratings}>
            <Table columns={[U.col_rating, U.col_score, ""]} rows={(P.ratings as any[]).map((r) => [r.label, r.display, r.bar])} />
          </Section>
        </Grid>
      </Section>

      <Section heading={U.positions}>
        {(POS.flag_counts as any[]).length ? <Rows rows={(POS.flag_counts as any[]).map((f) => ({ title: f.label, meta: String(f.count), status: { label: f.flag.replace(/_/g, " "), tone: f.flag === "no_mark_price" || f.flag === "near_zero_cost" ? undefined : ("warning" as Tone) } }))} /> : null}
        {(POS.rh as any[]).length ? (
          <Section heading={U.rh_positions} caption={POS.rh_equity_cost_basis_display}>
            <Table
              columns={[U.col_symbol, U.col_account, U.col_class, U.col_side, U.col_qty, U.col_avg, U.col_cost, U.col_share_cost, U.col_expires, U.col_history, U.col_flags]}
              rows={(POS.rh as any[]).map((r) => [r.sym, acctLabel(r.acct), r.cls, r.side, r.qty_display, r.avg_price_display, r.cost_basis_display, r.share_display, r.expiration ? `${r.expiration} (${r.days_to_expiry}d)` : "-", cell(r.history_display, r.history_tone), r.flags.length ? cell(flagText(r.flags), "warning") : "-"])}
            />
          </Section>
        ) : null}
        {(POS.hl_spot as any[]).length ? (
          <Section heading={U.hl_spot}>
            <Table columns={[U.col_coin, U.col_qty, U.col_entry, U.col_flags]} rows={(POS.hl_spot as any[]).map((r) => [r.coin, r.qty_display, r.entry_display, flagText(r.flags) || "-"])} />
          </Section>
        ) : null}
        {(POS.hl_perp as any[]).length ? (
          <Section heading={U.hl_perps}>
            <Table
              columns={[U.col_coin, U.col_side, U.col_size, U.col_entry, U.col_mark, U.col_lev, U.col_upnl, U.col_liq, U.col_flags]}
              rows={(POS.hl_perp as any[]).map((r) => [r.coin, r.side, r.size_display, r.entry_display, r.mark_display, r.leverage_display, cell(r.upnl_display, r.upnl_tone), r.liq_distance_display, r.flags.length ? cell(flagText(r.flags), "warning") : "-"])}
            />
          </Section>
        ) : has.hl ? <Text>{U.no_hl_perps}</Text> : null}
        {!(POS.rh as any[]).length && !(POS.hl_spot as any[]).length && !(POS.hl_perp as any[]).length ? <Text>{U.no_positions}</Text> : null}
      </Section>

      <Section heading={U.data_quality} caption={fmt(U.dq_caption, { total: DQ.checks_total, req: DQ.required_failed, opt: DQ.optional_failed })} collapsible defaultOpen={false}>
        {has.rh_coverage ? (
          <Callout tone="info" lead={fmt(U.fifo_title, { start: DQ.rh_coverage_start })}>
            {fmt(U.fifo_body, { trades: DQ.approx_trades, share: DQ.approx_share_display })}
          </Callout>
        ) : null}
        <Grid columns={2}>
          <Section heading={U.gain_sources}>
            <Table columns={[U.col_source, U.col_records, U.col_share, U.col_net]} rows={(DQ.gain_sources as any[]).map((g) => [g.label, String(g.trades), g.share_display, cell(g.pnl_display, g.tone)])} />
          </Section>
          <Section heading={U.gaps}>
            <Rows rows={(DQ.gaps as any[]).map((g) => ({ title: g.title, detail: g.detail }))} />
          </Section>
        </Grid>
        {(DQ.rh_accounts as any[]).length ? (
          <Section heading={U.rh_accounts}>
            <Table
              columns={[U.col_account, U.col_fills, U.col_rh_from, U.col_fifo_before, U.col_rh_sourced, U.col_no_basis, U.col_mismatch]}
              rows={(DQ.rh_accounts as any[]).map((a) => [a.label, String(a.fills), a.coverage_start, String(a.fifo_before), String(a.rh_sourced), String(a.no_cost_basis), String(a.positions_mismatch)])}
            />
          </Section>
        ) : null}
        {DQ.hl ? <Text>{fmt(U.hl_dq, { used: DQ.hl.fills_used, raw: DQ.hl.fills_raw, dust: DQ.hl.dust_fills_excluded, spot: DQ.hl.spot_fills_excluded, funding: DQ.hl.funding_events, liq: DQ.hl.liquidation_fills_self })}</Text> : null}
        <Section heading={U.checks}>
          <Table
            columns={[U.col_check, U.col_scope, U.col_type, U.col_result, U.col_detail]}
            rows={(DQ.checks as any[]).map((c) => [c.label, c.scope, c.required ? U.required : U.optional, c.pass ? cell(U.pass, "success") : cell(U.fail, c.required ? "danger" : "warning"), c.detail])}
          />
        </Section>
      </Section>

      <Text>{U.disclaimer}</Text>
    </Page>
  );
}
