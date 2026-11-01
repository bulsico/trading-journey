# Trading Journey

Deterministic trading-history reviewer. Engine: `bin/lsl` (Python 3.8+,
standard library only). Scripts compute every number; you only interpret
the files they write.

When the user wants a trading journey reviewed (P&L, share cards, a roast,
交易来时路, English or Chinese), follow `docs/workflow.md`, then the Claude
Code extras in `.claude/skills/trading-journey/SKILL.md`. That skill is also
`/trading-journey`.

When changing the engine, tests, or labels, follow the developer section of
`AGENTS.md`. Run `python3 bin/lsl selftest` after every change. Do not invent
a figure the scripts do not already print.
