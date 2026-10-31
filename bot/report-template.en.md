# Trading Journey · report template (English)

Chat report template used by `bot/SKILL.md` step 7 (English; the Chinese one is
`report-template.zh.md`, same structure). All data comes from the files
`bin/lsl compute --scope <scope> --lang en` writes to `$OUT` (= `$DATA/out/<scope-slug>/`).
Every number is computed by `bin/lsl` (Python stdlib, deterministic). You only
interpret: never compute, convert or re-round a number yourself.

---

## Step 1: field map (summary.json, plus the pre-formatted display fields in page.json)

- TL;DR: `page.json` → `tldr` (3 to 5 lines, copy verbatim, numbers untouched)
- Scope: `summary.scope.label` (e.g. "All accounts", "Robinhood Roth IRA …1234", "0xabcd…1234"); `summary.sources` (rh / hl); Hyperliquid addresses only as `summary.hl_addresses[].label` (short form or the user's nickname)
- Basics: `overall.period.{from,to,days}`, `overall.fills`, `overall.unique_assets`, `overall.liquidations`
- Stats: `overall.win_rate`, `overall.avg_win`, `overall.avg_loss`, `overall.profit_factor_ratio`, `overall.net_pnl`
- Trader profile: `page.json` → `profile.by[profile.default]`: `facts[]` (longest win streak, trade duration, trading style, P&L cohort, size cohort, direction bias; `value` already carries the emoji), `strip_display` (last 50 trades as 🟩/🟥), `periods` (7D/30D/90D/ALL net, max drawdown, win rate, Sharpe, Sortino, volume, trades; `-` means not enough data). Raw values: `summary.profile.{all,rh,hl}`
- Persona: `persona.archetype_label`, `persona.modifier_labels`, `persona.ratings.{patience,fomo_resistance,stop_loss_discipline,trend_awareness,resilience}`, `persona.scores`
- Hall of shame / highlights: `worst_trades` / `best_trades` (`sym`, `pnl`, `date`, `approx`)
- Weak spots: `persona.scores`, `by_asset_worst`, `by_month_worst`
- Monthly report card: `recent_months`, `by_month_best`, `by_month_worst`
- Positions: `open_positions.json` (`hl_perp`, `hl_spot`, `rh`; `liq_distance_pct`, `asset_history`)
- Grade: `persona.grade`, `persona.grade_label`
- Data quality: `data_quality.json` `pass` and `checks`; `summary.approximate.share` is the FIFO-estimated share. If > 0, say "some P&L is estimated"
- Share card: `share-card.png` (or `.svg`) from `bin/lsl card --scope <scope> --lang en --png`; attach it

---

## Step 2: read the market context

Before writing, read `context/market-context.md` in the repo. It summarizes the
2023-2026 market regimes, Hyperliquid specifics and common behavioural traps, so
you can judge whether a trade went with or against the market of its day.

Use it as background, never quote it. Fold it into the judgement (e.g. a loss
taken long during the BTC death cross → "going long here took guts, and the
market sent you the bill").

---

## Step 3: write the report

Follow the structure below exactly. Asset names and dollar amounts as the
scripts print them. The voice is "the friend who loves you but won't let you
off the hook": honest, funny, sometimes savage, and every jab backed by a
number from the files.

```
# 🪞 Trading Journey · your trading file

> Your trading history, distilled. Every step you took to get here.

## ⚡ TL;DR

{page.json tldr, one line each, verbatim}

{attach the share card}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 📋 The basics

| | |
|------|------|
| Scope | {scope.label} |
| Sources | {Robinhood accounts (masked …1234 labels only) and/or short HL addresses; omit a missing source} |
| Period | {from} → {to} ({days} days) |
| Fills | {fills} |
| Assets traded | {unique_assets} |
| Liquidations | {liquidations} (only when Hyperliquid is in scope) |

## 📊 The stats

| | |
|------|------|
| Win rate | {win_rate%} |
| Avg win | +{avg_win} |
| Avg loss | {avg_loss} |
| Profit factor | {overall.profit_factor_ratio} |

{One sentence on how win rate and profit factor combine, e.g. high win rate with a weak profit factor means small wins and big losses}

## 📇 Trader profile

{each fact on its own line: "{label}: {value}", direction bias followed by its detail}

Last 50 trades: {strip_display}

| | 7D | 30D | 90D | All |
|------|----|-----|-----|-----|
{one row per periods.rows entry, from the usd column (pct column if the user wants percentages only); keep "-" as is: not enough data}

{One sentence: style vs cohort, or recent vs all-time contrast}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🧬 Trader personality

**Your type: {archetype_label}{modifiers}**

{2-3 sentences on what this type is like, in second person ("you"), so it lands}

Five ratings:

```
Patience            {█ x patience}  {patience}/10  ({one-line jab})
FOMO resistance     {█ x fomo_resistance}  {fomo_resistance}/10  ({jab})
Stop-loss discipline {█ x stop_loss_discipline}  {stop_loss_discipline}/10  ({jab})
Trend awareness     {█ x trend_awareness}  {trend_awareness}/10  ({jab})
Resilience          {█ x resilience}  {resilience}/10  ({jab})
```

> █ filled, ░ empty, e.g. ████░░░░░░ 4/10

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🏆 Hall of shame (top three)

> Your historic blunders, kept on file. Next time you read this list, may it buy you one second of clarity.

### 🥇 Gold · {sym} · {pnl}

> "{1-2 savage sentences on how this one went wrong, citing real numbers}"

### 🥈 Silver · {sym} · {pnl}

> "{jab}"

### 🥉 Bronze · {sym} · {pnl}

> "{jab}"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## ✨ Highlight reel

> Not all bad news. You got some right. Remember how that felt.

{best_trades top 2-3, one sentence of praise each; honest, not gushing}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🔍 Fatal flaws, decoded

> The data doesn't lie. Patterns pulled from your own records:

{2-3 most visible patterns from persona.scores and by_asset / by_month; each needs a concrete number}

### ❌ Flaw one: {name}
{2-3 sentences + the number that proves it}

### ❌ Flaw two: {name}
{2-3 sentences + the number}

{only if there is a clear third:}
### ❌ Flaw three: {name}
{2-3 sentences + the number}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 📊 Monthly report card

| Month | Net | Fills | Cumulative |
|------|--------|------|------|
{the five worst and five best months}

{1-2 sentences on the overall arc, e.g. "a textbook pump-and-fade curve"}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 📌 Open position check

{only if at least one of hl_perp / hl_spot / rh in open_positions.json is non-empty; otherwise skip the section entirely, no "no open positions" line}

| Asset | Side | Size | Entry | uPnL | Leverage | Liq. price |
|------|------|------|--------|--------|------|--------|
{one row per Hyperliquid perp; uPnL as +$X / -$X; "—" when liq price is null}

{Robinhood (rh) and HL spot (hl_spot) holdings in a short table: asset, account label, quantity, cost; never a full account number}

{1-3 sentences per position against the history: win rate on that asset, how you usually act when underwater, leverage and liquidation distance; start with "⚠️" when liq_distance_pct < 15}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 📊 Trader grade

```
S  Legend             win rate >55%, consistently profitable, rarely liquidated
A  Seasoned pro       win rate >50%, roughly breakeven, disciplined
B  Promising rookie   around 50%, the occasional flash of brilliance
C  Promising contrarian indicator
D  Pure exit liquidity win rate <40%
F  Time to rethink some life choices
```

**Grade: {grade} · "{grade_label}"**

{1-2 sentences of reasoning from win rate, profit factor, liquidations; no dollar amounts}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🤖 Hard questions

**Three things that would change your report card:**

1. {specific, data-backed advice, not generic}
2. {advice}
3. {advice}

**Before your next trade, ask yourself:**
> "{one question tailored to your biggest flaw}"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

> ⚠️ **For entertainment only**: AI-generated review, not financial, tax or investment advice. Numbers can differ from your broker's because of funding, unrealized P&L and estimates; your broker's statements are the source of truth. The AI can be wrong. Your wallet can't.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*Generated by [Trading Journey](https://github.com/bulsico/trading-journey)*
*Data as of: {to}*
```

---

## Voice and style

**Tone:** dry, quick, a little savage, like a group chat roast from a friend
who actually reads charts. Trading slang is fine when it fits naturally:
"got rekt", "bag holding", "exit liquidity", "buying the top", "diamond hands",
"paper hands", "revenge trading", "touching grass", "ngmi / wagmi" (sparingly),
"textbook" (good or bad). Never stack slang for its own sake.

**Never:**
- only cheerlead; name the real problems
- invent a number; every figure comes from summary.json / page.json / open_positions.json, no arithmetic of your own
- write a full account number or full address; masked …1234 labels and short addresses only
- go stiff or corporate
- run past ~1500 words

**Number format:**
- P&L as +$X,XXX / -$X,XXX
- win rate as XX.X%
- amounts exactly as the scripts print them; no rounding to "k" unless the script already did
