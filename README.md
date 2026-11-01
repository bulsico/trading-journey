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
`v0.2.14`, run the self-test, ask which language to use, collect sources (Hyperliquid
addresses; Robinhood via the official connector, whose namespace it discovers),
sync, fetch daily candles for the SMA trend tags (`lsl candles fetch`), compute one combined report (with a per-account breakdown when there are
several accounts; single-account reports on request), render the share cards
(per-account cards on request), build the
Page from `bot/page-template.tsx`, and write the review
(`bot/report-template.en.md` or `bot/report-template.zh.md`; background in
`context/market-context.md`).

Before the first report and Page, the bot asks which language to use with a
question widget: English, 中文 (Chinese), or System language. System language is
resolved from the user's locale, or from the language they write in, and maps to
`zh` or `en`. Other languages fall back to English, and the bot says so. The
choice is saved to memory and reused on later runs without asking again. A saved
"System language" is resolved again on every run. Ask the bot at any time to
switch.

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
python3 bin/lsl page    --data-dir DATA [--scope SCOPE] [--lang en|zh] [--compact] [--copy FILE]   # rebuild page.json only
python3 bin/lsl card    --data-dir DATA [--scope SCOPE] [--lang en|zh] [--period all|90d|30d|7d] [--copy FILE] [--png] [--per-account]
python3 bin/lsl purge   --data-dir DATA [--source rh|hl|all] --yes
python3 bin/lsl hl tokens --unit                           # public spotMeta lookup for the whitelist
```

`compute` writes `summary.json`, `series.json`, `open_positions.json`,
`data_quality.json` and `page.json` to `DATA/out/<scope-slug>/` (`--out DIR`
overrides). `card` writes the three story cards `share-card-{1-stats,2-facts,3-roast}.svg`
(and with `--png` a 2x `.png` each) next to them. With no source at all, `compute` returns an error asking for one.
`page --compact` rewrites `page.json` small enough to paste into one Page write:
same numbers, shorter lists (share cards for the default period only, top asset groups, 24-point
sparklines, first 12 Robinhood positions); see `lsl/page.py` `compact()`.

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

`lsl card` draws a set of three 9:16 story cards, each a 1080×1920 SVG
(byte-identical for the same input) with a 2160×3840 PNG on `--png`, in the
style of a casual trader personality test, with no dollar amounts anywhere:
only percentages, ratios, counts, days and asset names (drawdown is
`drawdown_of_peak`, % of the peak cumulative P&L). Dark background, mono
numbers, yellow emoji section headers; body text is about 34px and up, key
numbers bigger, nothing below about 26px, and a layout check
(`card.layout_problems`) keeps every element inside the card:

- `1-stats`: brand, scope (sources, "N accounts"), window and as-of date;
  badge + tagline, return on volume with a sparkline, a stats box (win rate,
  profit factor, closed trades, assets, active days, drawdown of peak; Sharpe
  and Sortino on shorter periods), best / worst asset, the W/L strip;
- `2-facts`: 🎲 fun facts (`lsl/funfacts.py`, up to 10 rows over the card's window): most
  traded ticker, long vs short split, most loved / arch-nemesis asset by win
  rate, longest win / loss streaks, median / shortest / longest hold, busiest
  day, favorite weekday, venue split or go-to account, stocks / crypto / options
  mix, liquidations. Each is hidden when the scope has no data for it (never
  shown as 0). Facts that do not fit are dropped (lowest priority first)
  rather than shrunk; with no facts at all the card is left out;
- `3-roast`: 🧬 trader type and modifiers, a roast built from counts (fills, days, fills per
  day, busiest day), the trend-discipline line (% of entries against the SMA
  trend) and five 0-10 score bars;
  📊 the S-F grade ladder with the user's grade highlighted, plus a verdict;
  🤖 three rules (bold lead + detail) and one question in a red-bordered box;
- every card: a footer with the brand ("Trading Journey", plus "交易旅程" in
  Chinese) and "1/3", "2/3", "3/3".

Files: `share-card-1-stats.svg`, `share-card-2-facts.svg`,
`share-card-3-roast.svg`; with `--png` each also gets a 2160x3840 `.png`. The
JSON result lists them in order in `cards_set`. Stale files from older versions
(the single `share-card.*` and any leftover `.zip`) are removed on each run.

Persona, scores and grade are all-time for the card's scope; the stats, badge,
strip and sparkline follow `--period`. The persona paragraph, verdict, rules and
question come from deterministic templates keyed on archetype, grade and flags
(BTC death-cross alt longs, liquidations, counter-trend share, profit factor,
overtrading, asset scatter). `--copy FILE` swaps in the bot's own wording from a
`copy.json` (`persona`, `verdict`, `rules` [3], `question`; any may be omitted),
which is checked first: no `$`, every number must appear in the script output
(dollar fields excluded), per-language length and wrapped-line limits. A
rejected file is an error listing the problems; nothing is drawn.
`--percent-only` is accepted as a no-op. `--png` rasterizes with the first of
`rsvg-convert`, `cairosvg` or `inkscape` it finds; without one it returns an
install hint and the SVGs are still written. The Page shows the three cards side
by side as images with a period toggle, but has no download buttons: it
runs in a sandboxed frame with no download, blob or clipboard support. The chat
attachments are the download path; the bot always attaches the combined set
after the report (only the three PNGs in order, 1/3, 2/3, 3/3, as chat images;
they are full resolution and can be downloaded from the chat preview;
installing a rasterizer first if needed) and any other set on request.

With several accounts the combined card adds "N accounts" to its scope line.
`--per-account` (scope `all`) also writes `cards/<account-slug>-{1-stats,2-facts,3-roast}.svg`
(+ `.png`) for every account, from that account's own profile, persona, liquidations and
equity curve (template copy); it honors `--period`, and an account with too
little data for the period is listed as `skipped` with its `available_periods`.
The Page previews the same cards through its account picker; the PNGs come in
chat.

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
