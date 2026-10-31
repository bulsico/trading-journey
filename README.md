# Trading Journey

A Grok Bot skill plus a small deterministic engine that turns your trading
history into a private "trading journey" Page and a candid chat review
(交易来时路). Sources:

- **Robinhood**, read through the Robinhood MCP connector (read-only `get_*` tools only)
- **Hyperliquid**, read from the public API for an address you give it

## The deterministic rule

- Connectors and APIs **fetch raw data only**.
- `bin/lsl` (Python 3.8+, standard library only) **computes every number**.
  The same input gives byte-identical output.
- The bot **only interprets**: it copies figures verbatim from the output files
  and writes the commentary. It never adds, averages, converts or re-rounds.

## Use it from Grok Bot

Install `bot/SKILL.md` as a skill. It tells the bot to clone this repo at tag
`v0.1.0`, run the self-test, sync, compute, build the Page from
`bot/page-template.tsx`, and write the review (Chinese layout in
`bot/report-template.zh.md`, background in `context/market-context.md`).

## CLI

```bash
python3 bin/lsl selftest                                   # 36 synthetic-data tests

python3 bin/lsl hl fetch  --data-dir DATA --address 0x...  # full first time, then incremental
python3 bin/lsl rh plan   --data-dir DATA [--max 8]        # next exact read-only MCP calls
python3 bin/lsl rh ingest --data-dir DATA                  # parse saved results, advance
python3 bin/lsl rh status --data-dir DATA                  # per-stream completeness (counts only)
python3 bin/lsl rh refresh --data-dir DATA                 # start an incremental round on a finished sync

python3 bin/lsl compute --data-dir DATA --out DATA/out --lang en|zh
python3 bin/lsl page    --out DATA/out [--lang en|zh]      # rebuild page.json only
python3 bin/lsl purge   --data-dir DATA [--source rh|hl|all] --yes
python3 bin/lsl hl tokens --unit                           # public spotMeta lookup for the whitelist
```

`compute` writes `summary.json`, `series.json`, `open_positions.json`,
`data_quality.json` and `page.json`.

### Robinhood loop protocol

MCP tools can only be called by the bot, so the script plans and the bot executes:

1. `rh plan` prints `{"status": "calls"|"pending"|"done", "calls": [...]}`.
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
- `out/`: computed files

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

## Known gaps

- Realized P&L only. Dividends, transfers, splits and option assignments are
  not modelled; no mark prices are fetched for open positions.
- Robinhood P&L before its realized-gain coverage start is a FIFO estimate.
- The Hyperliquid API returns at most about 10,000 fills per address.
- The Page template assumes both sources; with one source the bot trims the
  sections for the missing one.
- The Robinhood connector namespace is assumed to be `user-Robinhood-xai`.

## Privacy

Everything runs on your own machine or bot box. Account data lives only in the
data dir, which is outside the repo and git-ignored. Never commit it, your
account numbers or your address. Delete raw connector result files after they
are saved, and use `lsl purge` to wipe stored data.

Not financial, tax or investment advice. For entertainment and self-review.

## License

MIT
