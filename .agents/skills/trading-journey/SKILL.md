---
name: trading-journey
description: Review the user's trading journey (交易来时路) from Hyperliquid addresses and, when a Robinhood MCP server is already connected, Robinhood. Use for P&L, share cards, a roast of their trades, or a refresh of a previous review, in English or Chinese.
---

# Trading Journey · Codex

Follow `docs/workflow.md` exactly. It is a shell, files, and a plain-text
question. Do not compute, round, or judge a trend yourself. The extras below
are the only Codex differences. Where they disagree with the workflow, these
win.

## Language

When `python3 bin/lsl prefs` prints `"status": "ask"`, ask in the chat with
the printed `question` and `options`. Save the answer:

```bash
python3 bin/lsl prefs set --lang en|zh|system
```

There is no memory tool. The prefs file in the data directory is the source
of truth. If `note` is set, tell the user that sentence.

## Report

There is no canvas Page. `python3 bin/lsl html` writes the report. Tell the
user the `out` path and every path in `pngs` (or the SVG paths when there is
no PNG). Do not invent a download button.

## Robinhood

Only when this session already has a Robinhood MCP server. Connect, if the
user wants it, with the host's own login (never a password in the chat):

```bash
codex mcp add robinhood-trading --url https://agent.robinhood.com/mcp/trading
```

Then `/mcp` and select it. The namespace is whatever name that command
registered; pass it to `lsl rh plan --namespace`. Call only the `get_*` tools
the plan prints, and write the raw result to `save_as`. If the server is not
connected, review Hyperliquid only.
