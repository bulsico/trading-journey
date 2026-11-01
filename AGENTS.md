# AGENTS.md

Guidance for coding agents working in this repo.

- Engine: `lsl/` (Python 3.8+, standard library only, no dependencies). Entry point `bin/lsl`.
- Gate: `python3 bin/lsl selftest` (stdlib unittest over synthetic fixtures in `tests/`). Run it after every change.
- Determinism: the same input must give byte-identical output. Sort keys, avoid wall-clock time
  unless passed via `--now`, and keep incremental sync output equal to a fresh full sync (tested).
- Rule: connectors and APIs fetch raw data only; scripts compute every number; the bot only
  interprets. If the Page or report needs a new figure, add it to `lsl/` with a test.
- Robinhood: the planner may only emit read-only `get_*` tools. Never add order, transfer or
  settings calls.
- Never commit real account data, account numbers, addresses, data dirs or connector results.
  Test fixtures must be synthetic.
- Bot-facing docs: `bot/SKILL.md` (protocol), `bot/page-template.tsx` (Page, adapts to
  `D.has`), `bot/report-template.{en,zh}.md` (report layouts, same structure).
- Sources: 0..N Hyperliquid addresses and optional Robinhood, in any combination. Every
  output must work for RH only, HL only (one or many addresses) and both: no crashes, NaN
  or empty sections. Robinhood's namespace is passed with `--namespace`; never hard-code it.
- Languages: every user-facing string lives in `lsl/labels/{en,zh}.json` with `{named}`
  placeholders; add a key to both files.
- Privacy: outputs show masked labels only (`…1234`, short addresses). Examples in docs and
  tests use synthetic addresses such as `0xabcd…1234`.

Layout:

| path | what |
|---|---|
| `lsl/cli.py` | CLI |
| `lsl/rhsync.py`, `lsl/rhmodel.py`, `lsl/rhpage.py` | Robinhood plan/ingest walker and parsing |
| `lsl/hl.py` | Hyperliquid address book (add/remove/list) and per-address fetch |
| `lsl/scope.py` | `--scope` parsing, labels, out-dir slugs |
| `lsl/profile.py` | trader profile (formulas and thresholds documented in the docstring and README) |
| `lsl/accounts.py` | per-account breakdown of a combined report: rows, cent-exact reconciliation, cross-account insights (rules in the docstring) |
| `lsl/card.py` | three 1080x1920 story cards (stats, fun facts, roast) as SVG, badges, layout bounds check, optional 2x PNG, stored zips, per-account cards |
| `lsl/funfacts.py` | share-card fun facts per scope and period (counts, shares, durations, dates, names; missing facts omitted) |
| `lsl/candles.py` | daily candle cache (`DATA/raw/candles/`): HL candleSnapshot, Yahoo keyless stocks, optional RH `get_equity_historicals` windows; closed candles only, incremental = full (tested) |
| `lsl/trend.py` | SMA trend tags per entry and their aggregates (rules in the docstring); feeds persona trend awareness |
| `lsl/report.py` | TL;DR lines |
| `lsl/fifo.py`, `lsl/compute.py`, `lsl/persona.py` | P&L, stats, persona |
| `lsl/assets.py`, `lsl/data/` | cross-venue grouping, HL spot whitelist |
| `lsl/page.py`, `lsl/labels/` | page.json and en/zh labels |
| `tests/` | synthetic fixtures; `test_sources.py` covers source combinations, scopes, card, profile; `test_accounts.py` the By-account breakdown and per-account cards; `test_story_cards.py` story-card bounds (long Chinese/English copy), fact dropping, zips; `test_trend.py` SMA math, tagging, candle cache, determinism; `test_page_template.py` keeps download code out of the sandboxed Page and checks template label keys |
