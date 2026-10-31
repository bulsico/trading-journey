# Trading Journey

A Grok Bot skill plus a small deterministic engine that turns your trading
history into a private "trading journey" Page, a shareable P&L card and a
candid chat review (交易来时路), in English or Chinese. Sources, in any
combination (at least one):

- **Robinhood** (optional), read through the official Robinhood connector
  (read-only `get_*` tools only), every account it exposes
- **Hyperliquid**, zero or more addresses, read from the public API

## The deterministic rule

- Connectors and APIs **fetch raw data only**.
- `bin/lsl` (Python 3.8+, standard library only) **computes every number**.
  The same input gives byte-identical output.
- The bot **only interprets**: it copies figures verbatim from the output files
  and writes the commentary. It never adds, averages, converts or re-rounds.

## Use it from Grok Bot

Install `bot/SKILL.md` as a skill. It tells the bot to clone this repo at tag
`v0.2.1`, run the self-test, pick the language, collect sources (Hyperliquid
addresses; Robinhood via the official connector, whose namespace it discovers),
sync, compute one combined report (with a per-account breakdown when there are
several accounts; single-account reports on request), render the share card
(per-account cards on request), build the
Page from `bot/page-template.tsx`, and write the review
(`bot/report-template.en.md` or `bot/report-template.zh.md`; background in
`context/market-context.md`).

## CLI

```bash
python3 bin/lsl selftest                                   # 65 synthetic-data tests

python3 bin/lsl hl add    --data-dir DATA 0x... [--label NAME]  # validate (0x + 40 hex), dedupe
python3 bin/lsl hl list   --data-dir DATA                  # tracked addresses (short form)
python3 bin/lsl hl remove --data-dir DATA 0x...|NAME       # stop tracking, delete its data
python3 bin/lsl hl fetch  --data-dir DATA                  # every address: full first time, then incremental
python3 bin/lsl rh plan   --data-dir DATA --namespace NS [--max 8]  # next exact read-only MCP calls
python3 bin/lsl rh ingest --data-dir DATA                  # parse saved results, advance
python3 bin/lsl rh status --data-dir DATA                  # per-stream completeness (counts only)
python3 bin/lsl rh refresh --data-dir DATA                 # start an incremental round on a finished sync
python3 bin/lsl sources   --data-dir DATA                  # which sources have data, available scopes

python3 bin/lsl compute --data-dir DATA [--scope SCOPE] [--lang en|zh]   # -> DATA/out/<scope-slug>/
python3 bin/lsl page    --data-dir DATA [--scope SCOPE] [--lang en|zh]   # rebuild page.json only
python3 bin/lsl card    --data-dir DATA [--scope SCOPE] [--lang en|zh] [--period all|90d|30d|7d] [--percent-only] [--png] [--per-account]
python3 bin/lsl purge   --data-dir DATA [--source rh|hl|all] --yes
python3 bin/lsl hl tokens --unit                           # public spotMeta lookup for the whitelist
```

`compute` writes `summary.json`, `series.json`, `open_positions.json`,
`data_quality.json` and `page.json` to `DATA/out/<scope-slug>/` (`--out DIR`
overrides). `card` writes `share-card.svg` (and `share-card.png` with `--png`)
next to them. With no source at all, `compute` returns an error asking for one.

### Scopes

`--scope` picks what a report covers; raw data is shared and fetched once.

| scope | covers | out dir |
|---|---|---|
| `all` (default) | every source | `out/all` |
| `rh` | every Robinhood account | `out/rh` |
| `rh:a0` or `rh:1234` | one Robinhood account (tag or last 4 digits) | `out/rh-a0` |
| `hl` | every Hyperliquid address | `out/hl` |
| `hl:0x…` or `hl:NAME` | one address (full, short form or label) | `out/hl-0xabcd-1234` |

Labels are masked: `Robinhood Roth IRA …1234`, `0xabcd…1234`. Full account
numbers never appear in any output. `lsl sources` lists the scopes available.
A combined scope equals the merge of its parts (tested).

### By account

With two or more accounts (each Robinhood account and each Hyperliquid address
is one account), the combined report carries a per-account breakdown in
`summary.json` → `accounts` (rules in `lsl/accounts.py`), shown as the "By
account" section of the Page and the chat report (hidden for one account):

- Row per account: net P&L incl. funding (and before funding), funding, fees,
  volume, return on volume (net incl. funding / volume), fills, closed trades,
  win rate, max drawdown (all-time, from its own profile), best/worst asset
  group (net after fees; only a positive best and a negative worst), style,
  share of the combined net P&L (signed, so rows sum to 100%; with mixed signs a
  row can be negative or above 100%) and active range.
- Order: |net P&L incl. funding| descending, then label.
- Reconciliation: dollar fields are rounded to cents by largest remainder so
  the rows add up to the combined figures to the cent; counts add up exactly
  (`accounts.reconciliation`, tested). Max drawdown, win rate and return on
  volume are not additive; funding per row is net incl. funding − net.
- Insights: *opposing*: an asset group where one account's closed trades are
  ≥ 55% long and another's ≤ 45% long, each with ≥ 3 closed trades there
  (options excluded), top 5 by total |net|; *open opposing*: currently held
  long in one account and short in another; *offsets*: when some accounts made
  money and others lost: gross gain, gross loss, the share of gains given back,
  and each loser's own share.
- TL;DR: one line names the account that carried the result and the one that
  bled (replacing the best/worst single-trade line).

### Share card

`lsl card` draws a 1200x675 SVG (byte-identical for the same input): dark
background, scope label and short address top left, as-of date top right, big
net P&L with its period (return on volume with `--percent-only`), a W/L strip,
a stats row (win rate, Sharpe, max drawdown, trades), a cumulative sparkline, and a "Trading Journey" footer with a badge.
`--percent-only` hides every dollar amount. `--png` rasterizes with the first of
`rsvg-convert`, `cairosvg` or `inkscape` it finds; without one it returns an
install hint and the SVG is still written. The Page embeds the card with period
and $/% toggles and Download PNG / SVG buttons.

With several accounts the combined card adds "N accounts" to its scope line.
`--per-account` (scope `all`) also writes `cards/<account-slug>.svg` (+ `.png`)
for every account, from that account's own profile, liquidations and equity
curve; it honors `--period` and `--percent-only`, and an account with too little
data for the period is listed as `skipped` with its `available_periods`. The
Page offers the same cards through an account picker and a download button on
each By-account row.

Badge (first rule that matches, on the card's period net P&L and win rate):
all-time net < 0 with >= 3 liquidated trades → Liquidation Speedrun;
net < -$100 → Penny Picker (win rate >= 50%), Fee Farmer (scalper) or Exit
Liquidity; net within ±$100 → Treadmill; net > $100 → Sniper (win rate >= 70%),
Diamond Hands (swing or position style) or Grinder.

### Robinhood loop protocol

MCP tools can only be called by the bot, so the script plans and the bot executes:

1. `rh plan --namespace NS` prints `{"status": "calls"|"pending"|"done", "calls": [...]}`.
2. The bot calls each `call.tool` in `call.namespace` with `call.args` verbatim
   and saves the raw result to `call.save_as`.
3. `rh ingest` parses what was saved. Repeat until `status` is `done`.

The planner handles truncated or mangled pages, errors (window splitting), dense
years, realized-P&L reconciliation and per-symbol history, and asserts that only
`get_*` tools are planned.

## Data dir, incremental sync, purge

`--data-dir` (alias `--run`) defaults to `$LSL_DATA_DIR` or `~/trading-journey-data`:

- `state/`: sync cursors and parsed records
- `raw/`: saved connector results (`raw/done/` once ingested)
- `out/<scope-slug>/`: computed files per scope

Keep it between runs. Later syncs fetch only what is new, and an incremental
sync computes byte-identical output to a fresh full sync (covered by tests).
`lsl purge` deletes the stored account data for one source or all.

## How numbers are defined

- Robinhood: inside Robinhood's realized-P&L coverage window, its own
  `realized_gain` is used. Earlier closes are FIFO estimates, flagged `approx`.
- Hyperliquid: `closedPnl` per order, funding reported separately. Spot counts
  only HYPE and Unit-bridged tokens, matched by spotMeta index and tokenId
  (`lsl/data/hl_spot_whitelist.json`). Other spot tokens are excluded and counted.
- The same asset across venues (RH stock, option, crypto; HL spot, perp, HIP-3
  dex) is grouped by `lsl/data/asset_map.json`. Ambiguous symbols stay ungrouped
  and are listed in `data_quality.json`.

## Trader profile

Per scope and per venue (combined, Robinhood, Hyperliquid), in `summary.profile`
and on the Page, card and TL;DR. A closed trade has |P&L| > $0.001; daily net =
realized P&L of trades closed that day minus that day's fees (funding excluded).

- volume: sum of |qty x price| over fills (options: contracts x 100 x premium)
- W/L strip: last 50 closed trades, oldest first
- longest win streak: consecutive winning trades, and consecutive trading days
  with a closed trade and positive daily net
- trade duration: avg and median FIFO holding time
- style by median holding time: Scalper < 1 h ≤ Day trader < 1 d ≤ Swing < 30 d ≤ Position
- P&L cohort (all-time net incl. funding): Rekt ≤ -$100k < Unprofitable < -$100 ≤
  Breakeven < +$100 ≤ Profitable < +$100k ≤ Big winner
- size cohort by median fill notional: Shrimp < $100 ≤ Fish < $1k ≤ Dolphin < $10k ≤ Whale
- direction bias: share of closed trades that closed a long; Long > 55%, Short < 45%, else Neutral
- 7D / 30D / 90D / All (windows end on the as-of date; every calendar day,
  zero-filled): net, volume, win rate, max drawdown of cumulative daily net
  (from 0), drawdown as % of the window's peak profit, return on volume = net /
  volume, Sharpe = mean / pstdev x √365, Sortino = mean / √mean(min(0, x)²) x √365.
  A rolling window needs history covering its first day and ≥ 3 closed trades
  (all-time: one closed trade);
  Sharpe and Sortino also need ≥ 5 days with nonzero net. Otherwise the value is
  shown as "-". With few losing days Sortino can be very large.

Days are US/Eastern for Robinhood and UTC for Hyperliquid. Period net excludes
HL funding; all-time P&L and the P&L cohort include it.

## TL;DR

`page.json` → `tldr` holds 3 to 5 script-written lines (headline net, best and
worst asset or trade, win rate, verdict and badge) that open the Page
and the chat report.

## Known gaps

- Realized P&L only. Dividends, transfers, splits and option assignments are
  not modelled; no mark prices are fetched for open positions.
- Robinhood P&L before its realized-gain coverage start is a FIFO estimate.
- The Hyperliquid API returns at most about 10,000 fills per address.
- "%" figures are return on traded volume, not return on capital (deposits
  are not modelled).
- Addresses are compared case-insensitively; EIP-55 checksums are not verified.

## Privacy

Everything runs on your own machine or bot box. Account data lives only in the
data dir, which is outside the repo and git-ignored. Never commit it, your
account numbers or your addresses. Delete raw connector result files after they
are saved, and use `lsl purge` to wipe stored data.

Not financial, tax or investment advice. For entertainment and self-review.

## License

MIT
