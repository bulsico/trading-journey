# Trading Journey

Deterministic trading-history reviewer (`bin/lsl`). Scripts compute every
number. The agent only interprets the files.

## Run a review (Codex)

When the user wants their trading history reviewed, roasted, or turned into
share cards (交易来时路, English or Chinese), follow `docs/workflow.md`, then
only these Codex extras (also in `.agents/skills/trading-journey/SKILL.md`):

- Language: when `python3 bin/lsl prefs` prints `"status": "ask"`, ask in
  plain text with the printed question and options. Save with
  `python3 bin/lsl prefs set --lang en|zh|system`. The prefs file is the
  source of truth. There is no memory tool.
- Report: there is no canvas Page. `python3 bin/lsl html` writes a
  self-contained HTML file. Tell the user that path and the PNG paths.
- Robinhood: only if a Robinhood MCP server is connected
  (`codex mcp add robinhood-trading --url https://agent.robinhood.com/mcp/trading`,
  then `/mcp`). Call only the `get_*` tools `lsl rh plan` prints. Never ask
  for or store credentials. Otherwise review Hyperliquid only.
- Do not compute, round, or judge a trend yourself.

## Develop the engine

Guidance for coding agents changing this repo.

- Engine: `lsl/` (Python 3.8+, standard library only, no dependencies). Entry point `bin/lsl`.
- Gate: `python3 bin/lsl selftest` (stdlib unittest over synthetic fixtures in `tests/`). Run it after every change.
- Determinism: the same input must give byte-identical output. Sort keys, avoid wall-clock time
  unless passed via `--now`, and keep incremental sync output equal to a fresh full sync (tested).
- Rule: connectors and APIs fetch raw data only; scripts compute every number; the agent only
  interprets. If the HTML report or the chat review needs a new figure, add it to `lsl/` with a test.
- Robinhood: the planner may only emit read-only `get_*` tools. Never add order, transfer or
  settings calls. The namespace is passed with `--namespace`; never hard-code it.
- Never commit real account data, account numbers, addresses, data dirs or connector results.
  Test fixtures must be synthetic.
- The procedure every host shares is `docs/workflow.md`. Host extras:
  `bot/SKILL.md` (Grok Bot; the wrapper skill points here), `CLAUDE.md` and
  `.claude/skills/trading-journey/SKILL.md` (Claude Code), this file and
  `.agents/skills/trading-journey/SKILL.md` (Codex). The canvas template is
  `bot/page-template.tsx`. Chat reviews use `bot/report-template.{en,zh}.md`.
- Sources: 0..N Hyperliquid addresses and optional Robinhood, in any combination. Every
  output must work for RH only, HL only (one or many addresses) and both: no crashes, NaN
  or empty sections.
- Languages: every user-facing string lives in `lsl/labels/{en,zh}.json` with `{named}`
  placeholders; add a key to both files.
- Privacy: outputs show masked labels only (`…1234`, short addresses). Examples in docs and
  tests use synthetic addresses such as `0xabcd…1234`.

Layout:

| path | what |
|---|---|
| `docs/workflow.md` | harness-neutral procedure (shell, files, plain-text question) |
| `bot/SKILL.md` | Grok Bot adapter (question widget, canvas Page, chat PNGs, official connector) |
| `CLAUDE.md`, `.claude/skills/trading-journey/` | Claude Code adapter |
| `.agents/skills/trading-journey/` | Codex skill |
| `lsl/cli.py` | CLI, data-dir resolution |
| `lsl/prefs.py` | report language in `$DATA/prefs.json` |
| `lsl/htmlreport.py` | self-contained `report.html` |
| `lsl/rhsync.py`, `lsl/rhmodel.py`, `lsl/rhpage.py` | Robinhood plan/ingest walker and parsing |
| `lsl/hl.py` | Hyperliquid address book (add/remove/list) and per-address fetch |
| `lsl/scope.py` | `--scope` parsing, labels, out-dir slugs |
| `lsl/profile.py` | trader profile (formulas and thresholds documented in the docstring and README) |
| `lsl/accounts.py` | per-account breakdown of a combined report: rows, cent-exact reconciliation, cross-account insights (rules in the docstring) |
| `lsl/card.py` | three 1080x1920 story cards (stats, fun facts, roast) as SVG, badges, layout bounds check, optional 2x PNG, per-account cards |
| `lsl/funfacts.py` | share-card fun facts per scope and period (counts, shares, durations, dates, names; missing facts omitted) |
| `lsl/candles.py` | daily candle cache (`DATA/raw/candles/`): HL candleSnapshot, Yahoo keyless stocks, optional RH `get_equity_historicals` windows; closed candles only, incremental = full (tested) |
| `lsl/trend.py` | SMA trend tags per entry and their aggregates (rules in the docstring); feeds persona trend awareness |
| `lsl/report.py` | TL;DR lines |
| `lsl/fifo.py`, `lsl/compute.py`, `lsl/persona.py` | P&L, stats, persona |
| `lsl/assets.py`, `lsl/data/` | cross-venue grouping, HL spot whitelist |
| `lsl/page.py`, `lsl/labels/` | page.json and en/zh labels |
| `tests/` | synthetic fixtures; `test_sources.py` covers source combinations, scopes, card, profile; `test_accounts.py` the By-account breakdown and per-account cards; `test_story_cards.py` story-card bounds (long Chinese/English copy), fact dropping, stale-file cleanup; `test_trend.py` SMA math, tagging, candle cache, determinism; `test_page_template.py` keeps download code out of the sandboxed Page and checks template label keys; `test_hosts.py` prefs, data-dir resolution, HTML report |
