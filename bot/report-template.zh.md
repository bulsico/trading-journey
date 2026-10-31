# 交易来时路 · 报告模板（中文）

Chat report template used by `bot/SKILL.md` step 7 (Chinese; the English one is
`report-template.en.md`, same structure). All data comes from the files
`bin/lsl compute --scope <scope> --lang zh` writes to `$OUT` (= `$DATA/out/<scope-slug>/`);
the bot never computes a number itself.
所有数字都由 `bin/lsl`（Python 标准库，确定性输出）计算。你只负责解读，不要自己算任何数字。

---

## 第一步：字段对照（summary.json，以及 page.json 里已格式化好的展示字段）

- TL;DR：`page.json` → `tldr`（3-6 行，原样照抄，不改数字；只有趋势纪律值得一提时才会出现「趋势：」开头的一行）
- 分析范围：`summary.scope.label`（例如「全部账户」「Robinhood Roth IRA …1234」「0xabcd…1234」）；`summary.sources`（rh / hl）；Hyperliquid 地址只用 `summary.hl_addresses[].label`（短地址或用户起的名字）
- 交易者档案：`page.json` → `profile.by[profile.default]`：`facts[]`（最长连胜、持仓时长、交易风格、盈亏层级、仓位体量、多空偏好，`value` 已含 emoji）、`strip_display`（最近 50 笔 🟩/🟥）、`periods`（7D/30D/90D/ALL 的净盈亏、最大回撤、胜率、Sharpe、Sortino、成交额、笔数；`-` 表示数据不足）。原始数值在 `summary.profile.{all,rh,hl}`
- 分享卡：`bin/lsl card --scope <scope> --lang zh --png` 生成的 `share-card.png`（或 `.svg`），附在报告里。分账户卡片（`--per-account`）只在用户要的时候生成
- 分账户（仅当 `summary.accounts.count` > 1）：`page.json` → `accounts.rows[]`，按原顺序（`label`、`net_display`、`share_display`、`rov_display`、`volume_display`、`trades`、`win_rate_display`、`max_dd_display`、`best_display`、`worst_display`、`style_display`、`period_display`），`accounts.total`，`accounts.insights[]`（`lead`、`body`），`accounts.none`，`accounts.shared_display`。原始数值和规则见 `summary.accounts`（`rows`、`totals`、`reconciliation`、`insights`）

- 基本档案：`overall.period.{from,to,days}`、`overall.fills`、`overall.unique_assets`、`overall.liquidations`
- 交易统计：`overall.win_rate`、`overall.avg_win`、`overall.avg_loss`、`overall.profit_factor_ratio`、`overall.net_pnl`
- 人格：`persona.archetype_label`、`persona.modifier_labels`、`persona.ratings.{patience,fomo_resistance,stop_loss_discipline,trend_awareness,resilience}`、`persona.scores`
- 亏损名人堂 / 闪光时刻：`worst_trades` / `best_trades`（`sym`、`pnl`、`date`、`approx`）
- 弱点：`persona.scores`、`by_asset_worst`、`by_month_worst`
- 月度成绩单：`recent_months`、`by_month_best`、`by_month_worst`
- 持仓：`open_positions.json`（`hl_perp`、`hl_spot`、`rh`；`liq_distance_pct`、`asset_history`）
- 等级：`persona.grade`、`persona.grade_label`
- 趋势纪律（仅当 `page.json` → `has.trend` 为真）：`page.json` → `trend`：`coverage_display`、`callouts[]`（`lead`、`body`）、`none`、`alignment[]` / `regime[]` / `direction[]`（`label`、`trades`、`share_display`、`win_rate_display`、`net_display`、`avg_display`）、`venues[]` / `accounts[]`（`label`、`tagged`、`with_display`、`counter_display`、`with_net_display`、`counter_net_display`）、`basis_display`。原始值：`summary.trend`（`by_alignment`、`by_regime`、`by_direction`、`by_venue`、`by_account`、`alt_longs_in_btc_death_cross`、`notable`）；逐笔：`series.trend_entries[]`（`price`、`sma`、`dist_pct`、`regime`、`alignment`、`btc_regime`）。`persona.trend_basis` 为 `sma` 时趋势感知来自这些标签，为 `formula` 时沿用旧公式
- 数据质量：`data_quality.json` 的 `pass` 和 `checks`；`summary.approximate.share` 是 FIFO 估算的比例，>0 时在报告里注明「部分盈亏为估算」

---

## 第二步：读取市场背景参考

在生成报告前，先读取仓库里的 `context/market-context.md`。这份文档总结了2023-2026年的市场规律、HL平台特性和常见行为陷阱，只作为评价的背景。每笔开仓是顺势还是逆势已经由脚本算好：用 `summary.trend` 和 `series.trend_entries`（均线位置、50/200 格局、做多山寨时的 BTC 格局），不要根据这份文档或记忆自行推断。若 `summary.trend.status` 为 `no_candles`，就说明趋势标签未计算，不要猜。

用它作为评价的背景，不要在报告里直接引用它的内容，而是将其融入判断（例如：某笔亏损发生在BTC死亡交叉期间 → 评语里自然提到"这时候做多需要勇气，但市场告诉你了"）。

---

## 第三步：生成报告

读取 summary JSON 后，按照以下格式生成报告。**全程中文**（资产名称和美元金额保持英文）。语气是「爱你但不惯着你的老朋友」——真诚、幽默、有时毒舌，但所有批评都有数据支撑。

**报告格式（严格按照以下结构）：**

---

```
# 🪞 交易来时路 · 你的交易档案

> 用AI蒸馏你的交易历史，回望你走过的每一步

## ⚡ TL;DR

{page.json 的 tldr，每行一条，原样照抄}

{附上分享卡图片}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{仅当 accounts.count > 1；否则整节跳过：}
## 🗂️ 分账户

| 账户 | 净盈亏 | 占总盈亏 | 成交额回报率 | 平仓数 | 胜率 | 风格 |
|------|------|------|------|------|------|------|
{accounts.rows 每个账户一行，按原顺序，展示字段原样照抄；最后一行是 accounts.total}

{accounts.insights 每条写成「**{lead}**：{body}」，原样照抄；没有的话写 accounts.none}

{1-2 句：哪个账户扛起了结果、哪个账户在失血，账户之间有没有互相打架（反向下注）；只做解读，数字全部照抄表格}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 📋 基本档案

| 项目 | 数据 |
|------|------|
| 分析范围 | {scope.label} |
| 数据来源 | {Robinhood 账户（只用 …后四位标签）和/或 Hyperliquid 短地址；没有的来源不写} |
| 分析周期 | {from} → {to}（{days} 天） |
| 总成交笔数 | {totalFills} 笔 |
| 交易资产数 | {uniqueAssets} 个 |
| 爆仓次数 | {liquidations} 次（只有包含 Hyperliquid 时才写这一行） |

## 📊 交易统计

| 项目 | 数据 |
|------|------|
| 胜率 | {winRate%} |
| 平均盈利 | +{avgWin} |
| 平均亏损 | {avgLoss} |
| 盈亏比 | {overall.profit_factor_ratio} |

{用1句话点评胜率和盈亏比的组合，例如胜率高但盈亏比低说明什么，或者反过来}

## 📇 交易者档案

{facts 每条一行：「{label}：{value}」，多空偏好后面加上 detail}

最近 50 笔：{strip_display}

| 指标 | 7D | 30D | 90D | 全部 |
|------|----|-----|-----|------|
{periods.rows 每行一条，用 usd 列（用户要求只看百分比时用 pct 列）；"-" 原样保留，表示数据不足}

{1句话点评：风格和盈亏层级、近期和全部的反差}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🧬 交易员人格测评

**你的交易员类型：{archetypeLabel}{modifiers}**

{用2-3句话描述这个类型的核心特征，要有代入感，要用"你"而不是"他"}

五维评分：

```
持仓耐心     {ratings.patience 个实心方块}  {ratings.patience}/10  （{对应的毒舌评语}）
FOMO抵抗力   {ratings.fomoResistance 个实心方块}  {ratings.fomoResistance}/10  （{评语}）
止损纪律     {ratings.stopLossDiscipline 个实心方块}  {ratings.stopLossDiscipline}/10  （{评语}）
趋势感知     {ratings.trendAwareness 个实心方块}  {ratings.trendAwareness}/10  （{评语}）
越挫越勇     {ratings.resilience 个实心方块}  {ratings.resilience}/10  （{评语}）
```

> 用 █ 代表实心，░ 代表空，例如：████░░░░░░ 4/10

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 📐 趋势纪律

（仅当 `has.trend` 为真；否则写一行：趋势标签需要先运行 `lsl candles fetch`。）

{trend.coverage_display}

{逐条 trend.callouts[]：**{lead}**：{body}；没有则用 trend.none}

| 开仓 | 交易 | 占比 | 胜率 | 净盈亏 | 平均盈亏 |
|---|---|---|---|---|---|
{trend.alignment[] 每项一行}

{一两句话说明格局和方向两张表（trend.regime[]、trend.direction[]）显示了什么；多个平台或账户时，指出逆势在哪里代价最大（trend.venues[] / trend.accounts[]）。数字只取自这些字段。}

{trend.basis_display}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🏆 亏损名人堂（前三名）

> 以下是你的历史性失误，留存备查，以后看到这个清单，希望你能清醒一秒。

{取 worstTrades 前三，每个写一个段落，格式如下：}

### 🥇 金牌亏损 · {coin} · {pnl}

> 「{1-2句毒舌评语，具体描述这笔交易怎么亏的，引用真实数据}」

### 🥈 银牌亏损 · {coin} · {pnl}

> 「{评语}」

### 🥉 铜牌亏损 · {coin} · {pnl}

> 「{评语}」

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## ✨ 闪光时刻（最佳表现）

> 不全是坏消息，你也有做对的时候——记住这种感觉。

{取 bestTrades 前2-3名，每个一句话夸奖，但要实事求是不要过度吹捧}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🔍 致命弱点解密

> 数据不说谎。以下是从你的交易记录里提炼出来的行为模式：

{根据 archetypeScores 和 byAsset/byMonth 数据，识别2-3个最显著的行为模式。每个弱点必须有具体数据支撑。}

### ❌ 弱点一：{名称}
{2-3句描述 + 具体数据例证}

### ❌ 弱点二：{名称}
{2-3句描述 + 具体数据例证}

{如果有第三个显著弱点：}
### ❌ 弱点三：{名称}
{2-3句描述 + 具体数据例证}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 📊 月度成绩单

{取 byMonth 最近12个月（或全部数据如果少于12个月），找出最好和最差的月份，评论关键转折点}

| 月份 | 净盈亏 | 成交 | 累计 |
|------|--------|------|------|
{数据表格，前五差月份和前五好月份}

{用1-2句话点评整体趋势，比如"你的成绩单呈现出一个经典的'冲高回落'曲线"}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 📌 当前持仓风险评估

{只有当 open_positions.json 的 hl_perp / hl_spot / rh 至少一个非空时才输出这一节，否则完全跳过，不要提"暂无持仓"之类的话}

{如果有持仓，先输出表格：}

| 资产 | 方向 | 规模 | 入场价 | 浮盈亏 | 杠杆 | 清算价 |
|------|------|------|--------|--------|------|--------|
{Hyperliquid 永续每个持仓一行，浮盈亏正数写 +$X，负数写 -$X；清算价为 null 时填 "—"}

{Robinhood 持仓（rh）和 HL 现货（hl_spot）另列简表：资产、账户标签、数量、成本；不要写账户完整号码}

{然后对每个持仓结合历史数据评论（1-3句/每个），评论角度：}
{- 在该资产上的历史胜率（从 byAsset 查）——胜率低就点出来}
{- 当前浮盈亏状态 + 交易员历史习惯的关联（比如：你历来在亏损时越套越加，现在浮亏X%，你会怎么做？）}
{- 杠杆倍数 + 清算距离（入场价到清算价的百分比）——如果清算距离 <15%，用"⚠️"开头特别警示}
{语气与全篇一致：爱你但不惯着你，数据说话}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 📊 交易员等级认定

```
S级  传奇交易员     胜率>55% & 持续盈利 & 少爆仓
A级  稳健老手       胜率>50% & 基本打平 & 有纪律
B级  有潜力新人     接近50%，偶有亮点
C级  有潜力的反向指标
D级  纯韭菜         胜率<40%
F级  建议认真考虑人生方向
```

**本次评级：{grade} 级 · 「{gradeLabel}」**

{1-2句评级理由，引用胜率、盈亏比、爆仓次数等数据——不要提具体盈亏金额}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🤖 来自AI的灵魂拷问

**三件事，做到了你的成绩单会大不一样：**

1. {基于数据的具体建议1，不要泛泛而谈}
2. {具体建议2}
3. {具体建议3}

**下次你想开新仓，先问自己：**
> 「{一句话，基于你最大的弱点定制的灵魂问题}」

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

> ⚠️ **娱乐声明**：本报告由 AI 生成，仅供娱乐参考。盈亏数据可能因资金费率、持仓未实现盈亏等因素与实际情况存在出入，请以交易平台官方数据为准。AI 会犯错，你的钱包不会骗你。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*报告由 [交易来时路](https://github.com/bulsico/trading-journey) 生成*
*数据截至：{to}*
```

---

## 语气与风格指南

**必须使用的网络流行语（自然融入，不要堆砌）：**
- 夯 = 被套牢/亏大了 ("被某个币夯了一大笔")
- 寄了 = 完蛋了 ("这仓位寄了")
- 麻了 = 麻木无语 ("看到这个数字我麻了")
- 绷不住了 = 忍不住了
- 破防了 = 被戳痛点了
- 遥遥领先（讽刺用法）= "在亏损榜上遥遥领先"
- 抽象 = 操作令人困惑 ("这操作太抽象了")
- 上头了 = 冲动操作 ("那天你上头了")
- 赢麻了/亏麻了 = 达到麻木程度
- 教科书级别 = 经典案例（正反皆可）
- 草台班子 = 很混乱的操作

**禁止：**
- 不要只给正面鼓励，必须指出真实问题
- 不要捏造数据，所有数字来自 summary.json / page.json / open_positions.json，不要自己做加减乘除
- 不要写完整的账户号码或完整地址，只用 …后四位标签和短地址
- 不要用过于书面或正式的语言，要有生命力
- 不要超过1500字，要精炼

**数字格式：**
- 正数 P&L 用 +$X,XXX 格式
- 负数 P&L 用 -$X,XXX 格式
- 胜率用百分比 XX.X%
- 金额原样使用脚本输出的数字，不要换算成「万」或四舍五入
