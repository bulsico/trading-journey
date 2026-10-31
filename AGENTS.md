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
- Bot-facing docs: `bot/SKILL.md` (protocol), `bot/page-template.tsx` (Page),
  `bot/report-template.zh.md` (Chinese report layout).

Layout:

| path | what |
|---|---|
| `lsl/cli.py` | CLI |
| `lsl/rhsync.py`, `lsl/rhmodel.py`, `lsl/rhpage.py` | Robinhood plan/ingest walker and parsing |
| `lsl/hl.py` | Hyperliquid fetch |
| `lsl/fifo.py`, `lsl/compute.py`, `lsl/persona.py` | P&L, stats, persona |
| `lsl/assets.py`, `lsl/data/` | cross-venue grouping, HL spot whitelist |
| `lsl/page.py`, `lsl/labels/` | page.json and labels |
