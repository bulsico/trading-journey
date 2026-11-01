---
name: trading-journey
description: When the user wants their trading journey (交易来时路) reviewed or roasted from any mix of their Robinhood accounts (official Robinhood connector, optional) and zero or more Hyperliquid addresses, wants a share card of their P&L, wants it refreshed with new trades, or asks how their trading history looks.
---

# Trading Journey · Grok Bot

**Rule zero: scripts compute, you interpret.** Read-only: only Robinhood
`get_*` tools are ever called. Never call anything that places, cancels, or
modifies orders, transfers, or settings. Never ask for credentials.

## Setup

Do this before anything else, so the rest of the files exist:

```bash
REPO=~/trading-journey
[ -d $REPO/.git ] || git clone --branch v0.3.0 --depth 1 https://github.com/bulsico/trading-journey $REPO
cd $REPO && git fetch -q --depth 1 origin tag v0.3.0 2>/dev/null; git checkout -q v0.3.0
python3 bin/lsl selftest                     # must end with "OK" (exit 0) before you continue
DATA=~/trading-journey-data                  # persistent; reuse the same dir every run
```

Always pass `--data-dir $DATA`. That directory is the Grok Bot store, including
on a first run. Do not switch an existing user to `~/.trading-journey`.

Then follow `docs/workflow.md` (sources, fetch, candles, compute, cards, HTML
report, chat review). This file only adds what Grok Bot has. Where the two
disagree, this file wins.

## Language

The choice lives in `$DATA/prefs.json` (`lsl prefs`), not in memory, so another
host sees it.

When `python3 bin/lsl prefs --data-dir $DATA` prints `"status": "ask"`, ask
with a question widget (`SendToUser`, a single-choice question) before the
first report or Page. Use the printed `question`. Offer exactly the three
printed options, labeled with their `label`, and map them to `en`, `zh`, and
`system`. Wait. Save with:

```bash
python3 bin/lsl prefs set --lang en|zh|system --data-dir $DATA
```

If `note` is set, tell the user that sentence. A saved `system` is resolved
again on every `lsl prefs` read. Switch when the user asks, and save the new
choice. `$LANG_` is the result's `lang`.

## Robinhood

Find the user's Robinhood connector with `GetDynamicTools` using
`pattern: "Robinhood"`. Never assume a namespace name; use the one it returns
(`$RH_NS`). If none is installed or it needs auth, offer to connect the
**official Robinhood connector** and continue with Hyperliquid alone if the
user declines.

The plan / ingest loop is in `docs/workflow.md`. Run each call with
`CallDynamicTool`: `namespace` = `call.namespace`, `toolName` = `call.tool`,
`arguments` = `call.args` **verbatim**, and `write_to_file: true`. Then
`cp <result file> <call.save_as>` and delete the result file. Do not read,
trim, or summarise results. Do not call a tool the plan did not name.

## Share cards in chat

`docs/workflow.md` prints the PNG paths and writes the HTML report. Also
attach the combined set to the chat right after the review, without being
asked: the three PNGs in order (`share-card-1-stats.png`,
`share-card-2-facts.png`, `share-card-3-roast.png`, i.e. `cards_set[*].png`)
as chat images, and nothing else. They are full resolution. Do the same for
every other set you attach. Install a rasterizer (the command in the
workflow) and rerun `lsl card ... --png` when `"png"` is null. Attach the
SVGs only as a last resort, and say once why.

With several accounts, attach the combined set and offer per-account cards.
On request, run `lsl card --per-account --png` and attach only the set the
user asked for. Each `lsl card` overwrites `$OUT`, so attach one set before
rendering the next.

## Canvas Page

In addition to the HTML report, build the canvas Page. Read the Pages
(canvas) skill before the first `WriteCanvas`.

1. Rebuild a compact payload (the whole Page source goes inline in one
   `WriteCanvas` call), then fill the template:
   ```bash
   python3 bin/lsl page --data-dir $DATA --scope $SCOPE --lang $LANG_ --compact [--copy $OUT/card-copy.json]
   python3 - "$OUT/page.json" bot/page-template.tsx "$OUT/page.tsx" <<'PY'
   import json, sys
   d = json.dumps(json.load(open(sys.argv[1])), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
   t = open(sys.argv[2]).read()
   assert t.count("__PAGE_JSON__") == 1
   open(sys.argv[3], "w").write(t.replace("__PAGE_JSON__", d))
   open(sys.argv[3] + ".D", "w").write(d)
   PY
   ```
   Run this after `lsl html`. `lsl page --compact` rewrites `page.json` for
   the canvas only; the HTML report is already written from the full outputs.
2. In `page.tsx`, replace only the placeholders in the Verdict section
   (`VERDICT_LEAD`, `VERDICT_ARC`, `PRO_n`, `CON_n`, `QUESTION_n`) with your
   interpretation, in `$LANG_`. Prose interpolates `${D...}` fields; no typed
   numerals, no arithmetic. Do not edit the `const D = ...;` line.
3. `WriteCanvas` with the whole file. Update the same Page for the same scope
   on later runs; a different scope gets its own Page.
4. Verify: `ReadCanvas` with `write_to_file`, extract the text between
   `const D = ` and `;\n// END D`, and compare it byte for byte with
   `page.tsx.D`. Fix with `EditCanvas` if it differs. Delete the ReadCanvas
   result file afterwards (it holds account data).

The template adapts itself (`D.has`). It has no download buttons; the PNGs
are the ones attached in chat. `--compact` keeps every number the Page shows
and shortens lists (default-period cards only, top asset groups, 24-point
sparklines, first 12 Robinhood positions).

## Chat review

Follow section 9 of `docs/workflow.md`. Keep it under ~1500 words
(中文 1500 字). Then attach the PNGs (above). The HTML path is still told
to the user; the Page is the extra view.

## Clean up

As in the workflow. Also delete connector result files after copying them to
`save_as`, and delete ReadCanvas dumps. Do not put account data in memory.
