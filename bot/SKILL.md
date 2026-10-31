---
name: trading-journey
description: When the user wants their trading journey (交易来时路) reviewed or roasted from any mix of their Robinhood accounts (official Robinhood connector, optional) and zero or more Hyperliquid addresses, wants a share card of their P&L, wants it refreshed with new trades, or asks how their trading history looks.
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

Requirements: `python3` 3.8+ (stdlib only, no installs), `git`. Optional: an SVG
rasterizer for the PNG share card (see step 5).

## 0. Setup (every run)

```bash
REPO=~/trading-journey
[ -d $REPO/.git ] || git clone --branch v0.2.0 --depth 1 https://github.com/bulsico/trading-journey $REPO
cd $REPO && git fetch -q --depth 1 origin tag v0.2.0 2>/dev/null; git checkout -q v0.2.0
python3 bin/lsl selftest                     # must end with "OK" (exit 0) before you continue
DATA=~/trading-journey-data                  # persistent; reuse the same dir every run
```

`DATA` (`--data-dir`, alias `--run`; default `$LSL_DATA_DIR` or
`~/trading-journey-data`) holds `state/` (sync cursors and parsed records),
`raw/` (saved connector results) and `out/<scope>/` (computed files). It makes
later runs incremental, and an incremental run computes byte-identical output to
a fresh full sync (tested). Never put it inside the repo or anywhere shared.

## 1. Language

Write the report, the Page and the card in the user's language: Chinese (`zh`,
the 交易来时路 roast) or English (`en`). Pick it from the language the user
writes in; if that is unclear (mixed, or neither), ask once: "中文还是 English?".
Remember the choice (memory) and reuse it on later runs; switch whenever the user
asks ("in English please", "用中文"). Pass it as `--lang en|zh` to `compute`,
`page` and `card`. Below, `$LANG_` is that value.

## 2. Sources

A user can have **0..N Hyperliquid addresses** and, optionally, **Robinhood**.
Any combination works: RH only, HL only (one or several addresses), or both.

Check what is already synced: `python3 bin/lsl sources --data-dir $DATA`
(prints `rh`, `hl_addresses`, `any` and the available report `scopes`).

- **Zero sources** (`"any": false` and the user gave nothing): do not compute.
  Ask for at least one: "Send me one or more Hyperliquid addresses (0x…), and/or
  connect the official Robinhood connector so I can read your accounts (read-only)."
- **Hyperliquid addresses**: add each one the user gives, then fetch.
  ```bash
  python3 bin/lsl hl add    --data-dir $DATA 0xADDRESS   # validates (0x + 40 hex) and dedupes, case-insensitive
  python3 bin/lsl hl list   --data-dir $DATA             # short forms only (0xabcd…1234)
  python3 bin/lsl hl remove --data-dir $DATA 0xADDRESS   # also deletes that address's stored data
  python3 bin/lsl hl fetch  --data-dir $DATA [--now $NOW] # every tracked address, incremental
  ```
  An invalid address returns `{"error": ...}`: tell the user and ask again. An
  address already tracked returns `"status": "already_added"`: say so. The address
  is public chain data, but never write it into shared files.
- **Robinhood (optional)**: find the user's Robinhood connector with
  `GetDynamicTools` using `pattern: "Robinhood"`. Never assume a namespace
  name; use the one it returns (`$RH_NS`). If none is installed or it needs auth,
  offer to connect the **official Robinhood connector** (add-connector skill)
  and continue with Hyperliquid alone if the user declines. Never ask for
  Robinhood credentials.

## 3. Fetch

Pick one as-of time for the round: `NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)` and pass
`--now $NOW` to `hl fetch` and to the first `rh plan` / `rh refresh` of the round.

Hyperliquid: `python3 bin/lsl hl fetch --data-dir $DATA --now $NOW`. The first
run per address downloads its full history; later runs fetch only what is new.

Robinhood loop (same on the first and on later runs):

1. `python3 bin/lsl rh plan --data-dir $DATA --namespace $RH_NS [--max 8] [--now $NOW]` prints
   `{"status": "calls"|"pending"|"done", "calls": [...], "progress": {...}}`.
2. For every entry in `calls`: `CallDynamicTool` with `namespace` =
   `call.namespace`, `toolName` = `call.tool`, `arguments` = `call.args`
   **verbatim**, and `write_to_file: true`. Then `cp <result file> <call.save_as>`
   and delete the result file. Do not read, trim or summarise results.
   Independent calls in one batch can run in parallel.
3. `python3 bin/lsl rh ingest --data-dir $DATA` reports what was parsed and any `missing` ids.
4. Repeat from 1. `pending` means some `save_as` files are still missing: make
   those calls again, then ingest. Stop at `"status": "done"`.

On a later run the first `rh plan` already says `done`. Then run
`python3 bin/lsl rh refresh --data-dir $DATA --namespace $RH_NS [--now $NOW]`,
which starts an incremental round and prints its first calls, and continue from
step 2. Only refresh a finished round. `--namespace` is remembered in the data
dir; pass it again whenever `GetDynamicTools` returns a different one.
`python3 bin/lsl rh status --data-dir $DATA` shows per-stream completeness.

## 4. Scope, then compute

After syncing, ask which scope to report on, listing the `scopes` from
`lsl sources` by label (default: everything combined):

- `all`: every source combined (default)
- `rh`: Robinhood only; `rh:<account>`: one Robinhood account (`rh:a0`, or its last 4 digits)
- `hl`: all Hyperliquid addresses; `hl:<address>`: one address (full or short form)

Labels are masked (`Robinhood Roth IRA …1234`, `0xabcd…1234`); never show a full
account number or put one in the Page. If the user does not care, use `all`.
Generate other scopes later on request; each scope writes its own folder
`$DATA/out/<scope-slug>/` (e.g. `out/all`, `out/rh-a1`, `out/hl-0xabcd-1234`), so
scopes coexist. Raw data is shared and fetched once; the scope only filters what
is computed.

```bash
SCOPE=all
python3 bin/lsl compute --data-dir $DATA --scope $SCOPE --lang $LANG_
OUT=$(python3 bin/lsl compute --data-dir $DATA --scope $SCOPE --lang $LANG_ | python3 -c 'import json,sys; print(json.load(sys.stdin)["out"])')
```

(`compute` prints `{"status": "done", "out": ...}`; running it twice is
harmless and byte-identical.) It writes, byte-identical for the same input:

| file | use |
|---|---|
| `summary.json` | every number in the chat report: `overall`, `scopes`, `persona`, `profile`, best/worst trades, assets, months, `approximate`, `scope` |
| `series.json` | chart data, per-asset totals, `asset_groups` (cross-venue) |
| `open_positions.json` | `hl_perp`, `hl_spot`, `rh` positions and flags |
| `data_quality.json` | `pass`, `checks[]` (`required` flag), RH coverage, gain sources, reconciliation, grouping |
| `page.json` | the Page payload, already in `$LANG_`: `tldr`, `profile`, `card`, `has`, pre-formatted `*_display` strings, chart series |

Read `data_quality.json` first. If `pass` is false, tell the user which required
check failed and that numbers may be incomplete. Optional failures are worth one
line in the data-quality notes, not an alarm.

Gains policy: inside Robinhood's realized-P&L coverage window, Robinhood's own
`realized_gain` is the truth; earlier closes are FIFO estimates (`approx: true`).
Hyperliquid uses `closedPnl` per order; funding is reported separately. HL spot
counts only HYPE and Unit-bridged tokens. The same asset across venues is
grouped via `lsl/data/asset_map.json`; ambiguous ones stay ungrouped.

## 5. Share card

The card always follows the report scope and language.

```bash
python3 bin/lsl card --data-dir $DATA --scope $SCOPE --lang $LANG_ [--period all|90d|30d|7d] [--percent-only] --png
```

Writes `$OUT/share-card.svg` (1200x675, byte-identical) and, when a rasterizer
is installed (`rsvg-convert`, `cairosvg` or `inkscape`), `$OUT/share-card.png`.
Attach the PNG in chat. If the result says `"png": null` with an install hint,
attach the SVG instead and tell the user once how to get PNGs
(`sudo apt-get install librsvg2-bin`, `brew install librsvg` or
`pip install cairosvg`). `--percent-only` hides dollar amounts (return on volume
and win rate only); offer it when the user wants to share without amounts. If a period has too little data the result is an error listing
`available_periods`; pick one of those. The card never shows an account number.

## 6. Build the Page

Read your Pages (canvas) skill before the first `WriteCanvas`.

1. Fill the template with the exact compact dump:
   ```bash
   python3 - "$OUT/page.json" bot/page-template.tsx "$OUT/page.tsx" <<'PY'
   import json, sys
   d = json.dumps(json.load(open(sys.argv[1])), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
   t = open(sys.argv[2]).read()
   assert t.count("__PAGE_JSON__") == 1
   open(sys.argv[3], "w").write(t.replace("__PAGE_JSON__", d))
   open(sys.argv[3] + ".D", "w").write(d)
   PY
   ```
2. In `page.tsx`, replace only the placeholders in the Verdict section
   (`VERDICT_LEAD`, `VERDICT_ARC`, `PRO_n`, `CON_n`, `QUESTION_n`) with your
   interpretation, written in `$LANG_`. Prose interpolates `${D...}` fields; no
   typed numerals, no arithmetic. Do not edit the `const D = ...;` line.
3. `WriteCanvas` with the whole file. Update the same Page for the same scope on
   later runs; a different scope gets its own Page.
4. Verify: `ReadCanvas` with `write_to_file`, extract the text between
   `const D = ` and `;\n// END D`, and compare it byte for byte with
   `page.tsx.D`. Fix with `EditCanvas` if it differs. Delete the ReadCanvas
   result file afterwards (it holds account data).

The template adapts to the sources by itself (`D.has`): sections for a missing
source are hidden, Robinhood-only pages show no funding or HL sections, and
multi-address pages get one profile tab per address. It opens with the TL;DR,
the share card (period and $/% toggles, Download PNG / Download SVG), and the
trader profile ($/% toggle, venue selector, 7D/30D/90D/All table). The Page is
private to the user, so real tickers and amounts are fine there.

## 7. Chat report

Open with the TL;DR: the lines of `page.json` → `tldr`, verbatim (3 to 5
lines, already in `$LANG_`). Then follow the template for the language exactly:

- Chinese: `bot/report-template.zh.md`
- English: `bot/report-template.en.md`

Read `context/market-context.md` as background first. Attach the share card
PNG (or SVG). Keep it under ~1500 words (中文 1500 字).

Privacy: the report, card and Page are for the user only. Never post them, and
never put account numbers, addresses, or data files anywhere shared or committed.

## 8. Clean up

Keep `$DATA` (`state/`, `raw/`, `out/`): it makes the next sync incremental.
Always delete connector result files after copying them to `save_as`, and
ReadCanvas dumps, since they hold account data. If the user asks to wipe their
data: `python3 bin/lsl purge --data-dir $DATA [--source rh|hl|all] --yes`; to stop
tracking one address: `lsl hl remove`.

## Refreshing reference data (maintainers)

When Unit adds assets: `python3 bin/lsl hl tokens --unit` (public spotMeta, no
address), edit `lsl/data/hl_spot_whitelist.json` (`tokens`, bump `retrieved`),
then `python3 bin/lsl selftest`. New cross-venue rules go in
`lsl/data/asset_map.json` (public reference tickers only).
