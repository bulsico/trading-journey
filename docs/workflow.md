# Trading Journey

Review a trading history. The engine is `bin/lsl` (Python 3.8+, standard library
only). It fetches raw data, computes every number, and writes files. You
interpret those files. You do not add, average, convert, re-round, or decide
a trend yourself. If a figure you want is not in the files, leave it out.

This document assumes a shell, a filesystem, and a way to ask a question in
plain text and wait for the answer. It does not assume a canvas, a memory
store, a question widget, chat attachments, or any particular model host.
Optional extras for Grok Bot, Claude Code, and Codex are listed at the end.

Read-only: Robinhood is fetched only through the `get_*` calls `lsl rh plan`
prints. Never place, cancel, or modify orders, transfers, or settings. Never
ask for or store a password, API key, private key, or OAuth secret. Account
data stays in the data directory.

## 0. Get the program

From a checkout of this repo:

```bash
python3 bin/lsl selftest
```

The last line must be `OK` and the exit code 0. Do not continue past a
failure.

If you are not already in a checkout, clone the release and use that tree:

```bash
git clone --branch v0.3.0 --depth 1 https://github.com/bulsico/trading-journey
cd trading-journey && python3 bin/lsl selftest
```

An SVG rasterizer (`rsvg-convert`, `cairosvg`, or `inkscape`) is needed for
the PNG share cards. Install one when `lsl card --png` reports none:

```bash
command -v rsvg-convert || sudo apt-get install -y librsvg2-bin \
  || { python3 -m venv ~/.lsl-venv && ~/.lsl-venv/bin/pip install -q cairosvg \
       && export PATH=~/.lsl-venv/bin:$PATH; }      # macOS: brew install librsvg
```

## 1. Data directory

Persistent, outside the repo, reused every run. It holds `state/` (sync
cursors and parsed records), `raw/` (saved tool results), `out/<scope>/`
(computed files), and `prefs.json` (report language). Later syncs are
incremental, and an incremental sync matches a fresh full sync byte for byte.

When you omit `--data-dir`, resolution is:

1. `$LSL_DATA_DIR`, if set
2. `~/trading-journey-data`, if that directory already exists (an existing
   store is reused)
3. `~/.trading-journey`

```bash
python3 bin/lsl data-dir
```

Below, `$DATA` is that path. Pass `--data-dir "$DATA"` on every command, or
export `LSL_DATA_DIR` and omit the flag. A host adapter may pass its own
directory instead. Never put the directory inside the repo or anywhere it
would be committed.

## 2. Language

One language for the report, the HTML file, and the cards: Chinese (`zh`) or
English (`en`). Do not guess it silently.

```bash
python3 bin/lsl prefs --data-dir "$DATA"
```

- `"status": "ask"`: ask the person, in plain text, the printed `question`,
  and wait. Offer the three printed `options` (`en`, `zh`, `system`) by their
  `label`. Then save the id they chose:

  ```bash
  python3 bin/lsl prefs set --lang en|zh|system --data-dir "$DATA"
  ```

  Map a free-text answer onto an id (`English` → `en`, `中文` → `zh`,
  "system" / "系统语言" → `system`). Do not generate a report until this
  succeeds.
- `"status": "ready"`: use `lang` (`en` or `zh`). Do not ask again.
- `choice` `system` is resolved on every read from `LC_ALL`, then
  `LC_MESSAGES`, then `LANG`. A Chinese locale (`zh`, `zh-CN`, `zh-TW`,
  `zh-Hant`, …) is `zh`; an English one is `en`. Any other language falls
  back to `en`. If `note` is set, tell the user that sentence once.
- `reason` `no_locale` (empty, `C`, or `POSIX`): there is no system language.
  Ask which language they are writing in and save `en` or `zh` (not
  `system`).

Switch whenever they ask ("in English", "用中文") by setting the new choice.
`$LANG_` below is the resolved `lang`.

## 3. Sources

Zero or more Hyperliquid addresses, and optionally Robinhood. Any mix works:
Hyperliquid only, Robinhood only, or both.

```bash
python3 bin/lsl sources --data-dir "$DATA"
```

Prints `rh`, `hl_addresses`, `any`, and the available report `scopes`.

**Nothing synced and nothing offered.** Do not compute. Ask for at least one
Hyperliquid address (`0x` plus 40 hex digits). Robinhood is optional and only
possible when a host can perform the file loop in the next section.

**Hyperliquid.** Public API, no key:

```bash
python3 bin/lsl hl add    --data-dir "$DATA" 0xADDRESS   # validates and dedupes
python3 bin/lsl hl list   --data-dir "$DATA"             # short forms only (0xabcd…1234)
python3 bin/lsl hl remove --data-dir "$DATA" 0xADDRESS   # also deletes that address's stored data
```

An invalid address returns `{"error": ...}`: say so and ask again. An address
already tracked returns `"status": "already_added"`. The address is public
chain data; do not write the full address into a shared file. Examples use
synthetic addresses such as `0xabcd…1234`.

**Robinhood (optional).** The engine never logs into Robinhood. It prints the
exact read-only calls; something else runs them and saves the raw JSON.

1. `python3 bin/lsl rh plan --data-dir "$DATA" --namespace "$RH_NS" [--max 8] [--now "$NOW"]`
   prints `{"status": "calls"|"pending"|"done", "calls": [...]}`.
2. For every entry in `calls`, call `call.tool` in `call.namespace` with
   `call.args` verbatim. Save the untouched result at `call.save_as`. Do not
   read, trim, or summarise it. Independent calls in one batch can run together.
3. `python3 bin/lsl rh ingest --data-dir "$DATA"`.
4. Repeat from 1. `pending` means a `save_as` file is still missing. Stop at
   `"status": "done"`.

On a later run the first `rh plan` already says `done`. Then
`python3 bin/lsl rh refresh --data-dir "$DATA" --namespace "$RH_NS" [--now "$NOW"]`
starts an incremental round. Only refresh a finished round. `--namespace` is
remembered; pass it again when the host's name for the server changes.
`python3 bin/lsl rh status --data-dir "$DATA"` shows completeness, counts only.

`$RH_NS` is the name **your host** gave the server. The engine does not
hard-code one. The official Trading MCP is
`https://agent.robinhood.com/mcp/trading` (streamable HTTP; the host does the
OAuth login):

- Claude Code: `claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading`, then `/mcp` to authenticate
- Codex: `codex mcp add robinhood-trading --url https://agent.robinhood.com/mcp/trading`, then `/mcp`
- Grok Bot: the official Robinhood connector (`bot/SKILL.md`)

Use whatever name that command registered. Call only the tools named in
`calls`. If the server also exposes order, transfer, or settings tools, do
not call them. If you cannot run the calls, skip Robinhood and review
Hyperliquid. A person with only a shell and no MCP client is in that case:
do not type credentials into the terminal.

## 4. Fetch

Pick one as-of time and pass it as `--now` so the run does not depend on the
clock a second time:

```bash
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
python3 bin/lsl hl fetch --data-dir "$DATA" --now "$NOW"
```

The first run per address downloads its history; later runs fetch what is new.
Do the Robinhood loop from section 3 in the same round, with the same `$NOW`
on the first `rh plan` or `rh refresh`.

## 5. Candles

Trend tags need daily candles. After both syncs:

```bash
python3 bin/lsl candles fetch --data-dir "$DATA"
python3 bin/lsl candles status --data-dir "$DATA"
```

No connector: Hyperliquid `candleSnapshot` for crypto and HL markets, and the
keyless Yahoo chart endpoint for stocks and option underlyings. Closed candles
only, cached under `$DATA/raw/candles/`, incremental. Progress goes to stderr.
`unavailable` series stay untagged; they are not guessed. If Yahoo cannot be
reached and the Robinhood loop works, run that loop again with `--candles on`
until `done`, then `python3 bin/lsl candles fetch --data-dir "$DATA" --stocks rh`.
Without candles, compute still runs and the trend section is omitted.

## 6. Compute

Do not ask which scope to use. Build one combined report (`all`). With several
accounts it already contains a per-account breakdown (rows add up to the cent).
Mention once that a single-account report exists. Other scopes, only when
asked: `all`, `rh`, `rh:<account>`, `hl`, `hl:<address>`. Labels are masked
(`Robinhood Roth IRA …1234`, `0xabcd…1234`). Each scope writes
`$DATA/out/<scope-slug>/`. Raw data is fetched once.

```bash
python3 bin/lsl compute --data-dir "$DATA" --scope all --lang "$LANG_"
```

The JSON result's `out` is `$OUT`. Running compute twice is harmless and
byte-identical. It writes `summary.json` (every number in the review, including
`accounts`, `persona`, `profile`, `trend`), `series.json`, `open_positions.json`,
`data_quality.json`, and `page.json`.

Read `data_quality.json` first. If `pass` is false, say which required check
failed and that numbers may be incomplete. An optional failure is one line,
not an alarm.

Trend (`summary.trend`): each closed trade's entry is tagged against the daily
SMAs of its asset (stocks 15/50/200, crypto 7/50/200, options use the
underlying), from the last closed daily candle before the entry day.
`alignment` is `with`, `counter`, or `neutral`. Too little history is
`insufficient_history`, never a guess. Quote `summary.trend` and
`persona.trend_basis` (`sma` or `formula`). Do not re-derive a trend from
`context/market-context.md` or from memory.

Formulas, cohorts, and the gains policy are in the README. Inside Robinhood's
realized-P&L window, Robinhood's own `realized_gain` is the truth; earlier
closes are FIFO estimates (`approx: true`). Hyperliquid uses `closedPnl` per
order. Funding is separate.

## 7. Share cards

Three 9:16 story cards, 1080×1920 SVG and, with `--png`, a 2160×3840 PNG.
No dollar amounts: percentages, ratios, counts, days, and names only.
`1-stats`, `2-facts` (omitted when the window has no facts), `3-roast`.
Persona and grade are all-time; stats and facts follow `--period`.

The persona paragraph, verdict, three rules, and question come from templates
unless you write `$OUT/card-copy.json`:

```json
{"persona": "...", "verdict": "...", "rules": ["Lead sentence. Detail.", "...", "..."], "question": "..."}
```

Every key is optional. No `$` and no dollar amount. Every number you type must
already appear in `summary.json` as a count, percentage, ratio, day count, or
date, copied as printed. Character / wrapped-line limits: zh persona 150/6,
verdict 70/3, rule 60/3, question 50/3; en 330/6, 170/3, 140/3, 120/3. A
rejected file is an error with `problems[]` and nothing is drawn. Skip the
file to keep the templates. Draft any chat review (section 9) before you write
this file, so the card quotes the review.

```bash
python3 bin/lsl card --data-dir "$DATA" --scope all --lang "$LANG_" --png
```

Add `--copy "$OUT/card-copy.json"` only after that file has been written. The
result's `cards_set` lists `id`, `svg`, and `png` in order. Print every PNG
path (or the SVG path when `png` is null). With several accounts the combined card says "N accounts". Offer
per-account cards; on request:

```bash
python3 bin/lsl card --data-dir "$DATA" --scope all --lang "$LANG_" --per-account --png
```

Files land in `$OUT/cards/`. Same rule: print the paths, do not invent a
download. Other periods (`--period 90d|30d|7d`) only when asked. If a period
has too little data the error lists `available_periods`.

## 8. HTML report

This is the local page. One self-contained file, cards inlined, PNG paths
listed, figures copied from the compute outputs. It does not rewrite
`page.json`.

```bash
python3 bin/lsl html --data-dir "$DATA" --scope all --lang "$LANG_"
```

If `$OUT/card-copy.json` exists it is quoted when it passes the same checks;
a rejected file is an error and the previous report is left in place. The
result's `out` is the HTML path and `pngs` lists the PNGs. Tell the user both,
so they can open them. There is no generated PDF: printing the HTML from a
browser is the way to get one. A PDF writer is not part of the engine because
a deterministic file would need bundled CJK fonts.

`lsl page` still rebuilds `page.json` for the Grok canvas. You do not need it
for the HTML report.

## 9. Chat review

Skip this if the person only wants the files.

Open with `page.json` → `tldr`, verbatim (3 to 6 lines, already in `$LANG_`).
Then follow the template for the language:

- English: `bot/report-template.en.md`
- Chinese: `bot/report-template.zh.md`

Read `context/market-context.md` as background first. Every figure comes from
the files. Keep it under about 1500 words (中文 1500 字). The report is for
the user only. Do not post it, and do not put account numbers, addresses, or
data files anywhere shared.

## 10. Clean up

Keep `$DATA`. Delete raw tool-result files after they have been copied to
`save_as`. To wipe stored account data:
`python3 bin/lsl purge --data-dir "$DATA" [--source rh|hl|all] --yes`.
`prefs.json` is not account data and is kept. Deleting the data directory
forgets the language too. To stop tracking one address: `lsl hl remove`.

## Host adapters

The steps above are the whole tool. These files only add optional host
features. A new Grok Bot wrapper should point at `bot/SKILL.md`.

| Host | Entry | What it adds |
|---|---|---|
| Grok Bot | `bot/SKILL.md` | pinned checkout, `~/trading-journey-data`, question widget, official Robinhood connector, chat PNG attachments, canvas Page |
| Claude Code | `CLAUDE.md`, `.claude/skills/trading-journey/SKILL.md` (`/trading-journey`) | plain-text question, HTML report, Robinhood only if that MCP is connected |
| Codex | `AGENTS.md`, `.agents/skills/trading-journey/SKILL.md` | same as Claude Code, Codex MCP command |
