---
name: trading-journey
description: When the user wants their trading journey (交易来时路) reviewed or roasted from their Robinhood accounts (Robinhood MCP connector) and/or a Hyperliquid address, wants it refreshed with new trades, or asks how their trading history looks.
---

# Trading Journey · Grok Bot skill

**Rule zero: scripts compute, you interpret.** Connectors and APIs only fetch raw
data. Every number you show comes from the files `bin/lsl` writes, exactly as
written. Never add, average, convert, re-round or take a percentage yourself; if
a number you want is not in the files, leave it out (or add it to the scripts
with a test first).

Read-only: only Robinhood `get_*` tools are ever called (the planner asserts
this). Never call anything that places, cancels or modifies orders, transfers or
settings.

Requirements: `python3` 3.8+ (stdlib only, no installs), `git`, the Robinhood
connector connected (for Robinhood), and the user's Hyperliquid address (for
Hyperliquid; it is public chain data, but never write it into shared files).

## 0. Setup (every run)

```bash
REPO=~/trading-journey
[ -d $REPO/.git ] || git clone --branch v0.1.0 --depth 1 https://github.com/bulsico/trading-journey $REPO
cd $REPO && git checkout -q v0.1.0
python3 bin/lsl selftest                     # must end with "OK" (exit 0) before you continue
DATA=~/trading-journey-data                  # persistent; reuse the same dir every run
```

`DATA` (`--data-dir`, alias `--run`; default `$LSL_DATA_DIR` or
`~/trading-journey-data`) holds `state/` (sync cursors and parsed records),
`raw/` (saved connector results) and `out/` (computed files). It is what makes
later runs incremental, and an incremental run computes byte-identical output
to a fresh full sync (tested). Never put it inside the repo or anywhere shared.

## 1. Fetch Hyperliquid (public API, no auth)

```bash
python3 bin/lsl hl fetch --data-dir $DATA --address 0xUSER_ADDRESS
```

The first run downloads the full history; later runs fetch only what is new.

## 2. Fetch Robinhood (Robinhood MCP, read-only loop)

Loop (the same on the first and on later runs):

1. `python3 bin/lsl rh plan --data-dir $DATA [--max 8]` prints
   `{"status": "calls"|"pending"|"done", "calls": [...], "progress": {...}}`.
2. For every entry in `calls`: `CallDynamicTool` with `namespace` =
   `call.namespace`, `toolName` = `call.tool`, `arguments` = `call.args`
   **verbatim**, and `write_to_file: true`. Then `cp <result file> <call.save_as>`
   and delete the result file. Do not read, trim or summarise results.
   Independent calls in one batch can run in parallel. If your Robinhood
   connector's namespace differs from `call.namespace`, use yours; nothing else
   changes.
3. `python3 bin/lsl rh ingest --data-dir $DATA` reports what was parsed and
   any `missing` ids.
4. Repeat from 1. `pending` means some `save_as` files are still missing: make
   those calls again, then ingest. Stop at `"status": "done"`.

On a later run, the very first `rh plan` already says `done` (the last round
finished). Then run `python3 bin/lsl rh refresh --data-dir $DATA`, which starts
an incremental round and prints its first calls in the same shape, and continue
the loop from step 2. Only refresh a finished round.

`python3 bin/lsl rh status --data-dir $DATA` shows per-stream completeness. The
planner handles gateway truncation, mangled pages, errors (window splitting),
dense years, realized-P&L reconciliation and per-symbol trade history. The
option-contract universe is an optional cross-check (`--option-universe on`),
off by default because it costs many calls. A first full sync of a long history
can take hundreds of calls; later rounds only re-walk recent windows.

When doing both sources, pick one as-of time first (`NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)`)
and pass `--now $NOW` to `hl fetch` and to the first `rh plan` / `rh refresh`
of the round, so both sources share it.

## 3. Compute

```bash
python3 bin/lsl compute --data-dir $DATA --out $DATA/out --lang en   # or zh
```

Writes five files, byte-identical for the same input:

| file | use |
|---|---|
| `summary.json` | every number in the chat report: `overall`, `scopes` (`rh`, `rh.aN`, `hl`), `persona`, best/worst trades, assets, months, `recent_months`, `approximate` |
| `series.json` | chart data, per-asset totals, `asset_groups` (cross-venue) |
| `open_positions.json` | `hl_perp`, `hl_spot`, `rh` positions and flags |
| `data_quality.json` | `pass`, `checks[]` (`required` flag), RH coverage start, gain sources, reconciliation, grouping |
| `page.json` | the Page payload: pre-formatted `*_display` strings, chart series, windows, annotations, flags. Rebuild alone with `python3 bin/lsl page --out $DATA/out [--lang en\|zh]` |

Read `data_quality.json` first. If `pass` is false, tell the user which required
check failed and that numbers may be incomplete. Optional checks that fail are
worth one line in the data-quality notes, not an alarm.

Gains policy: inside Robinhood's realized-P&L coverage window, Robinhood's own
`realized_gain` is the truth; earlier closes are FIFO estimates marked
`approx: true`. Hyperliquid uses `closedPnl` per order. HL spot counts only HYPE
and Unit-bridged tokens (by spotMeta index + tokenId, `lsl/data/hl_spot_whitelist.json`);
everything else is excluded and counted in `data_quality.json`. The same asset
across venues (RH stock/option/crypto, HL spot/perp/HIP-3 dex) is grouped via
`lsl/data/asset_map.json`; anything ambiguous stays ungrouped and is listed.

## 4. Build the Page

Read your Pages (canvas) skill before the first `WriteCanvas`.

1. Fill the template with the exact compact dump:
   ```bash
   python3 - "$DATA/out/page.json" bot/page-template.tsx "$DATA/out/page.tsx" <<'PY'
   import json, sys
   d = json.dumps(json.load(open(sys.argv[1])), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
   t = open(sys.argv[2]).read()
   assert t.count("__PAGE_JSON__") == 1
   open(sys.argv[3], "w").write(t.replace("__PAGE_JSON__", d))
   open(sys.argv[3] + ".D", "w").write(d)
   PY
   ```
2. In `page.tsx`, replace only the placeholders in "The verdict" section
   (`VERDICT_LEAD`, `VERDICT_ARC`, `PRO_n`, `CON_n`, `QUESTION_n`) with your
   interpretation. Prose interpolates `${D...}` fields; no typed numerals, no
   arithmetic. Do not edit the `const D = ...;` line. Set the Page title, e.g.
   "<Name>'s Trading Journey".
3. `WriteCanvas` with the whole file, `width: "wide"`. Update the same Page on
   later runs instead of creating a new one.
4. Verify: `ReadCanvas` with `write_to_file`, extract the text between
   `const D = ` and `;\n// END D`, and compare it byte for byte with
   `page.tsx.D`. Fix with `EditCanvas` if it differs. Delete the ReadCanvas
   result file afterwards (it holds account data).

The template assumes both sources are present. With only one source, remove
the sections and lines that reference the missing scope (`rh` or `hl`) before
writing. If the Page needs a new figure, add it to `lsl/page.py` with a test.

The Page is private to the user, so real tickers and amounts are fine there.

## 5. Chat report

Write a short analysis in chat from `summary.json`, `open_positions.json` and
`data_quality.json`. For Chinese (交易来时路 roast), follow
`bot/report-template.zh.md` exactly (layout, tone, disclaimer), and read
`context/market-context.md` as background first. For English, use the same
sections:

1. Profile: `overall.period`, `fills`, `unique_assets`, `liquidations`, sources; note FIFO estimates when `summary.approximate.share > 0`
2. Stats: `win_rate`, `avg_win`, `avg_loss`, `profit_factor_ratio`, `net_pnl`
3. Persona: `persona.archetype_label` + `modifier_labels`, the five `persona.ratings` as █/░ bars
4. Hall of shame: `worst_trades[0..2]`
5. Highlights: `best_trades[0..2]`
6. Weak spots: 2-3 patterns backed by `persona.scores`, `by_asset_worst`, `by_month_worst`
7. Months: `recent_months`, `by_month_best`, `by_month_worst`
8. Open positions: only if `open_positions.json` has entries; warn when `liq_distance_pct < 15`
9. Grade: `persona.grade` + `grade_label`
10. Three data-backed suggestions and one question
11. Disclaimer: for entertainment only, realized P&L only, not tax or investment advice

Tone: a friend who loves you but won't coddle you; honest, funny, every jab
backed by a number from the files. Keep it under ~1500 words.

Privacy: the report and Page are for the user only. Never post them, and never
put account numbers, the address, or data files anywhere shared or committed.

## 6. Clean up

Keep `$DATA` (`state/`, `raw/`, `out/`): it makes the next sync incremental.
Always delete connector result files after copying them to `save_as`, and
ReadCanvas dumps, since they hold account data. If the user asks to wipe their
data: `python3 bin/lsl purge --data-dir $DATA [--source rh|hl|all] --yes`.

## Refreshing reference data (maintainers)

When Unit adds assets: `python3 bin/lsl hl tokens --unit` (public spotMeta, no
address), edit `lsl/data/hl_spot_whitelist.json` (`tokens`, bump `retrieved`),
then `python3 bin/lsl selftest`. New cross-venue rules go in
`lsl/data/asset_map.json` (public reference tickers only).
