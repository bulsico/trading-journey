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

Requirements: `python3` 3.8+ (stdlib only for the engine), `git`, and an SVG
rasterizer for the PNG share card, which is the user's only download path (step 5
installs one when missing).

## 0. Setup (every run)

```bash
REPO=~/trading-journey
[ -d $REPO/.git ] || git clone --branch v0.2.6 --depth 1 https://github.com/bulsico/trading-journey $REPO
cd $REPO && git fetch -q --depth 1 origin tag v0.2.6 2>/dev/null; git checkout -q v0.2.6
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

Daily candles for the trend tags (after both syncs, every run):

```bash
python3 bin/lsl candles fetch --data-dir $DATA
```

It needs no connector: Hyperliquid `candleSnapshot` for every crypto / HL market
(HIP-3 perps use their own coin; Robinhood crypto uses the HL coin) and the keyless
Yahoo chart endpoint for stocks and option underlyings, one daily series per
underlying, cached in `$DATA/raw/candles/` and fetched incrementally (only the
last 2 days are re-fetched). It throttles, backs off on 429 and is resumable:
if it stops, run it again. Progress goes to stderr; the result lists
`unavailable` series (delisted tickers, coins HL never listed); their trades stay
untagged, they are not guessed. A first run on a long history takes a few
minutes; later runs make almost no calls.
If the box cannot reach Yahoo, use the Robinhood connector instead: run the
Robinhood loop above again with `--candles on` until `done` (about one call per
50 trading days per symbol, so hundreds of calls for a long history), then
`python3 bin/lsl candles fetch --data-dir $DATA --stocks rh`.
`python3 bin/lsl candles status --data-dir $DATA` shows which series are complete.
Without candles, compute still runs; the trend section is just hidden.

## 4. Scope, then compute

After syncing, do not ask about scope: build ONE combined report (`all`). With
several accounts (Robinhood accounts and/or Hyperliquid addresses) it already
holds a per-account breakdown ("By account": each account's net, share of the
total, return on volume, trades, win rate, drawdown, best/worst asset, style,
plus cross-account callouts such as opposite bets on the same asset or losing
accounts giving back the winners' gains). Rows add up to the combined totals to
the cent. Mention once that a report for a single account is available too.
Other scopes, only when the user asks (listed as `scopes` by `lsl sources`):

- `all`: every source combined (default)
- `rh`: Robinhood only; `rh:<account>`: one Robinhood account (`rh:a0`, or its last 4 digits)
- `hl`: all Hyperliquid addresses; `hl:<address>`: one address (full or short form)

Labels are masked (`Robinhood Roth IRA …1234`, `0xabcd…1234`); never show a full
account number or put one in the Page.
Generate other scopes on request; each scope writes its own folder
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
| `summary.json` | every number in the chat report: `overall`, `scopes`, `accounts` (per-account rows, totals, reconciliation, insights), `persona`, `profile`, `trend`, best/worst trades, assets, months, `approximate`, `scope` |
| `series.json` | chart data, per-asset totals, `asset_groups` (cross-venue), `trend_entries` (one SMA tag per closed trade) |
| `open_positions.json` | `hl_perp`, `hl_spot`, `rh` positions and flags |
| `data_quality.json` | `pass`, `checks[]` (`required` flag), RH coverage, gain sources, reconciliation, grouping |
| `page.json` | the Page payload, already in `$LANG_`: `tldr`, `accounts`, `profile`, `card`, `has`, pre-formatted `*_display` strings, chart series |

Read `data_quality.json` first. If `pass` is false, tell the user which required
check failed and that numbers may be incomplete. Optional failures are worth one
line in the data-quality notes, not an alarm.

Trend discipline (`summary.trend`): every closed trade's entry is tagged against
the daily SMAs of its asset (stocks 15/50/200, crypto 7/50/200, options use the
underlying), from the last closed daily candle before the entry day. `alignment`
is `with` (long above the fast SMA in a 50 > 200 golden cross, or short below it
in a death cross), `counter` (the reverse) or `neutral`; too little history is
`insufficient_history`, never a guess. Aggregates are by alignment, regime,
direction × alignment, venue and account, plus alt longs taken while BTC was in
a death cross; `notable` lists what is worth saying. With 20+ tagged trades,
trend awareness in the persona is the with-trend share (`persona.trend_basis`
= `sma`), otherwise the old formula (`formula`). Quote these fields; never
re-derive trend calls from `context/market-context.md` or memory.

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

The chat attachment is the only real download path. The Page runs in a sandboxed
frame with no download, blob or clipboard support, so it shows the card as an
image with no download buttons and its note says the PNG is in the chat. So:

- Always attach the combined `share-card.png` to the chat right after the
  report, without being asked.
- If the result says `"png": null` (no rasterizer), install one first, then
  rerun the same `lsl card ... --png` command:
  ```bash
  command -v rsvg-convert || sudo apt-get install -y librsvg2-bin \
    || { python3 -m venv ~/.lsl-venv && ~/.lsl-venv/bin/pip install -q cairosvg \
         && export PATH=~/.lsl-venv/bin:$PATH; }      # macOS: brew install librsvg
  ```
  `lsl card` finds `rsvg-convert`, `cairosvg` (CLI on `PATH`) or `inkscape`.
  Attach `share-card.svg` only as a last resort, when no rasterizer can be
  installed, and say once why it is an SVG.

`--percent-only` hides dollar amounts (return on volume and win rate only);
offer it when the user wants to share without amounts. If a period has too
little data the result is an error listing `available_periods`; pick one of
those. The card never shows an account number.

Any other card (another period, the % version, or one account) is attached as a
PNG when the user asks for it, made with `lsl card ... --png` (`--period`,
`--percent-only`, `--per-account`). The Page's period and $/% toggles only
preview cards; they cannot save them.

With several accounts the combined card says "N accounts" on its scope line.
After the report, attach the combined card and offer per-account cards ("want a
card for just one wallet or account?"). On request:

```bash
python3 bin/lsl card --data-dir $DATA --scope all --lang $LANG_ --per-account [--period ...] [--percent-only] --png
```

This also writes `$OUT/cards/<account-slug>.svg` (+ `.png`) for every account,
in By-account order; the result's `cards[]` lists each one (`label`, `svg`,
`png`), or `skipped` with that account's `available_periods` when it has too
little data for the period. Attach only the PNG(s) the user asked for. The
Page previews the same cards behind its account picker (view only).

## 6. Build the Page

Read your Pages (canvas) skill before the first `WriteCanvas`.

1. Rebuild the payload in compact form (the whole Page source goes inline in
   one `WriteCanvas` call, so it must stay small), then fill the template with
   the exact compact dump:
   ```bash
   python3 bin/lsl page --data-dir $DATA --scope $SCOPE --lang $LANG_ --compact
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
with two or more accounts a "By account" section shows the account table,
cross-account callouts and one expandable detail per account (equity
sparkline, profile stats, a pointer to its card); it is hidden for a single account.
With candles, a "Trend discipline" section shows tag coverage, callouts, the
with / counter / neutral table, regime and direction tables, and the split by
venue and account.
It opens with the TL;DR, the share card (account picker when there are several
accounts, period and $/% toggles, the card as an image plus a line saying the
PNG is attached in the chat; no download buttons), and the trader
profile ($/% toggle, venue selector, 7D/30D/90D/All table). The Page is
private to the user, so real tickers and amounts are fine there.

`--compact` keeps every number the Page shows and only shortens lists: the
share card is the default period in $ (the % and other-period cards, and the
per-account cards, stay available via `lsl card`; account rows say to ask you),
asset groups are the top 5 best / 5 worst / 8 multi-venue, account sparklines
use 24 points, and Robinhood positions show the first 12 rows with a "N more"
line. The Page cannot save files; every card download is a PNG you attach in chat
(step 5).

## 7. Chat report

Open with the TL;DR: the lines of `page.json` → `tldr`, verbatim (3 to 6
lines, already in `$LANG_`; with several accounts one line names the account
that carried the result and the one that bled; a trend line appears when trend
discipline is notable). The "By account" section
follows when there is more than one account. Then follow the template for the language exactly:

- Chinese: `bot/report-template.zh.md`
- English: `bot/report-template.en.md`

Read `context/market-context.md` as background first. After the report, always
attach the combined share card PNG (step 5; SVG only as a last resort). Keep it
under ~1500 words (中文 1500 字).

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
