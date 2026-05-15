# Bugfix Requirements Document

## Introduction

MatchPredict 的"预测战绩"（Prediction Record）标签页的**核心目的**是：把 ML 模型对每场比赛的**预测值**与比赛结束后回填的**真实值**做**逐场对比展示**——让用户直观看到自己的预测在哪些比赛上胜平负命中、哪些比分精确命中、总进球数偏差多少。它不是一个一般性的"统计仪表盘"，而是一个"我预测对了哪些"的**对照视图**。

当前的实现存在两类缺陷：

1. **核心视角缺失**：现有 HTML 模板（`record-stats-grid` / `record-league-bars` / `record-trend-chart` / `record-match-list`）以及未实现的渲染逻辑围绕的是"聚合统计 + 平铺列表"，并没有为每场比赛把"预测 vs 真实"两列结构性地并排呈现，命中标识（胜平负 / 比分 / 总进球）也未在卡片维度上明确表达。即便补齐 JS，用户也得不到他实际想要的对比体验。
2. **数据为空时的退化体验**：当数据库中尚未有任何已结束的比赛对比数据时（即 `actual_result IS NOT NULL` 的记录数为 0），当前页面会把 4 张统计卡片显示为 `--` / `--%`，并保留分联赛柱状条、趋势图、筛选器、列表区域 spinner——给用户传达"系统出错了"的错误信号，而不是"今天还没有已结束的比赛"。

后端 `GET /api/accuracy/summary` 与 `GET /api/accuracy/matches` 已就绪，能返回所需的全部字段（`predicted_score` / `actual_score` / `predicted_result` / `actual_result` / `result_correct` / `score_correct` / `goal_diff_error` / `ml_probs` 等），且必须基于 `upcoming_fixtures` 表中 `actual_result IS NOT NULL` 的真实记录，**不允许任何 mock / 假数据 / 占位数据**。

本次 bugfix 的目标是把"预测战绩"标签页重写为以"预测 vs 真实逐场对比"为核心的视图，并在无数据时优雅降级为单一空状态提示。用户已明确允许在该 tab 内**重新设计 DOM 结构、CSS 样式与 JS 函数**，不必保留现有的容器 ID（如 `#record-stats-grid`、`#rsc-total`、`#record-league-bars` 等）。

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN 用户切换到"预测战绩"标签 AND 数据库中存在已结束比赛 THEN 系统在每场比赛的卡片中不并排展示"预测胜平负 vs 实际胜平负"、"预测比分 vs 实际比分"、"预测总进球数 vs 实际总进球数"，因此用户无法在卡片维度直观看到自己的预测与真实结果的对照
1.2 WHEN 用户切换到"预测战绩"标签 AND 数据库中存在已结束比赛 THEN 系统不在每场比赛卡片上显式标记胜平负命中（✅/❌）、比分命中（✅/❌）以及总进球数误差值
1.3 WHEN 用户切换到"预测战绩"标签 AND 数据库中**不存在**任何已结束比赛（`total_predicted = 0`）THEN 系统仍然渲染 4 张统计卡片（值为 `--` / `--%`）、分联赛柱状条容器、趋势图容器、筛选器与列表 spinner，给用户造成"页面加载失败"的错觉，而不是显示一个清晰的空状态提示
1.4 WHEN 用户切换到"预测战绩"标签 THEN 系统未实现该 tab 对应的前端渲染函数（`static/script.js` 中无 record 分支），导致后端 API 不被调用，所有区块永远停留在初始占位/spinner 状态
1.5 WHEN 用户操作"全部联赛"或"全部结果"筛选下拉框（HTML 中 `onchange="loadRecordMatches()"`）THEN 浏览器抛出 `ReferenceError: loadRecordMatches is not defined`，列表不刷新
1.6 WHEN "预测战绩"面板的任何 `.record-*` / `.rsc-*` 元素被渲染 THEN 系统不应用任何 CSS 样式，元素以浏览器默认样式呈现，与其他 tab（深度权重、AI 大模型、智能选场）视觉风格不一致
1.7 WHEN 后端 API 调用失败或返回 `success: false` THEN 系统不在该 tab 显示可读的错误提示，用户只看到永久 spinner

### Expected Behavior (Correct)

修复后，"预测战绩"标签页 SHALL 围绕"预测 vs 真实逐场对比"的核心视角重写。允许在该 tab 内重新设计 DOM 结构、CSS 类名、JS 函数；唯一的硬性结构约束是该 tab 入口（`#tab-record` 按钮 + `#panel-record` 面板根元素）保持不变，以兼容现有 `switchTab` 路由逻辑。

2.1 WHEN 用户切换到"预测战绩"标签 THEN 系统 SHALL 调用 `GET /api/accuracy/summary` 与 `GET /api/accuracy/matches`，并仅基于响应数据渲染（绝不使用任何 mock / 假数据 / 占位数据）
2.2 WHEN 用户切换到"预测战绩"标签 AND `summary.total_predicted = 0`（即数据库中没有任何已结束的比赛对比数据）THEN 系统 SHALL 在 `#panel-record` 内**只展示一个空状态区块**，文案为"暂无已结束的比赛数据，比赛结束后会自动同步并生成对比"，并 SHALL **不渲染**任何统计卡片区块（4 张统计卡片）、分联赛准确率柱状条、近期命中趋势图、筛选器、比赛列表、分页器
2.3 WHEN 用户切换到"预测战绩"标签 AND `summary.total_predicted > 0` THEN 系统 SHALL 渲染统计聚合区块，至少包含以下四项：（a）已对比场次数 = `summary.total_predicted`；（b）胜平负命中率（百分比）= `summary.accuracy`；（c）比分精确命中率（百分比）= `round(summary.score_hit / summary.total_predicted * 100, 1)`；（d）平均进球数偏差（每场预测总进球数与实际总进球数的平均差）= `summary.avg_goal_error`
2.4 WHEN 用户切换到"预测战绩"标签 AND `summary.total_predicted > 0` THEN 系统 SHALL 在比赛列表区为每场已结束比赛渲染一张**对比卡片**，每张卡片 SHALL **同时**呈现以下三个对比维度，且每个维度都要让用户一眼分辨"预测值"与"真实值"（左右、上下、双列、双行任选其一即可，但必须结构性并排，而不是只显示真实值）：

  - **胜平负对比**：预测胜平负（`predicted_result`，主胜/平局/客胜）vs 实际胜平负（`actual_result`），并标记命中（`result_correct = true` 时显示 ✅，否则显示 ❌）
  - **比分对比**：预测比分（`predicted_score`，例如 `2-1`）vs 实际比分（`actual_score`），并标记精确命中（`score_correct = true` 时显示 ✅，否则显示 ❌）
  - **总进球数对比**：预测总进球数（`predicted_home_goals + predicted_away_goals`）vs 实际总进球数（`actual_home_goals + actual_away_goals`），并显示误差值（`goal_diff_error`，例如"差 1 球"）

2.5 WHEN 比赛卡片渲染时 THEN 系统 SHALL 在卡片头部展示比赛元信息（联赛、主队 vs 客队、比赛时间），方便用户定位是哪一场比赛
2.6 WHEN `summary.total_predicted > 0` AND `summary.league_stats` 非空 THEN 系统 SHALL 渲染分联赛准确率展示（每个联赛的命中数 / 总数 / 准确率百分比）
2.7 WHEN `summary.total_predicted > 0` AND `summary.trend` 非空 THEN 系统 SHALL 渲染近 14 天的命中趋势可视化（每日命中数 / 总数 / 准确率）
2.8 WHEN `summary.total_predicted > 0` THEN 系统 SHALL 渲染筛选器（按联赛、按命中/未命中筛选）；WHEN 用户改变筛选条件 THEN 系统 SHALL 通过全局可调用的 `loadRecordMatches()` 函数（必须存在）以新的 `league` / `result` 查询参数重新请求 `/api/accuracy/matches?page=1&...` 并刷新列表与分页，且不抛出 `ReferenceError`
2.9 WHEN `/api/accuracy/matches` 返回 `total > per_page` THEN 系统 SHALL 渲染分页控件（上一页/页码/下一页）；WHEN 用户点击页码 THEN 系统 SHALL 重新加载对应页数据
2.10 WHEN "预测战绩"面板被渲染 THEN 系统 SHALL 应用与其他 tab 风格一致的 CSS（卡片圆角、阴影、间距、配色变量、字体层级），使该 tab 在视觉上与"深度权重"/"AI 大模型"/"智能选场"等 tab 协调；新设计的 DOM 结构对应的 CSS 类名由实现自由命名
2.11 WHEN 加载请求进行中 THEN 系统 SHALL 显示加载指示；WHEN 请求完成 THEN 系统 SHALL 移除加载指示并替换为实际数据或空状态
2.12 WHEN `/api/accuracy/summary` 或 `/api/accuracy/matches` 返回非 2xx 状态码或 `success: false` THEN 系统 SHALL 在该 tab 显示一条可读的错误提示（而非永久 spinner），并在浏览器控制台记录错误详情
2.13 WHEN 比赛列表数据返回 THEN 系统 SHALL 仅展示 `actual_result IS NOT NULL` 的真实已结束比赛（由后端 API 保证），系统 SHALL NOT 在前端补造任何模拟比赛、占位比赛或示例比赛

### Unchanged Behavior (Regression Prevention)

修复仅在"预测战绩"tab 范围内重构前端 HTML 模板片段、CSS 与 JS。不修改后端、不修改其他 tab、不修改数据库与同步脚本。

3.1 WHEN 用户切换到"深度权重"（`tab-classic`）标签 THEN 系统 SHALL CONTINUE TO 正确显示原有的联赛/球队选择、赔率输入与预测结果展示
3.2 WHEN 用户切换到"AI 大模型"（`tab-ai`）标签 THEN 系统 SHALL CONTINUE TO 在首次进入时调用 `loadLotteryMatches()` 加载赛事广场，并支持加入待分析队列
3.3 WHEN 用户切换到"智能选场"（`tab-smart`）标签 THEN 系统 SHALL CONTINUE TO 在首次进入时调用 `loadSmartMatches()` 加载比赛
3.4 WHEN 用户切换到"世界杯预测"（`tab-wc`）标签 THEN 系统 SHALL CONTINUE TO 显示倒计时与既有内容
3.5 WHEN 后端接收到 `GET /api/accuracy/summary` 请求 THEN 系统 SHALL CONTINUE TO 返回当前的响应结构（`success`、`summary`、`league_stats`、`trend`），后端字段、字段名、字段类型、字段含义均不修改
3.6 WHEN 后端接收到 `GET /api/accuracy/matches?league=&result=&page=&per_page=` 请求 THEN 系统 SHALL CONTINUE TO 返回当前的响应结构（`success`、`total`、`page`、`per_page`、`matches`），后端字段、字段名、字段类型、字段含义均不修改
3.7 WHEN 后台脚本 `scripts/sync_results.py` 运行 THEN 系统 SHALL CONTINUE TO 同步比赛真实结果并回填 `actual_result`、`actual_home_goals`、`actual_away_goals`、`result_correct`、`score_correct`、`goal_diff_error`、`finished_at` 等字段，行为保持不变
3.8 WHEN 浏览器加载 `static/css/style.css` THEN 系统 SHALL CONTINUE TO 应用所有现有 tab 的样式（不破坏现有选择器、CSS 变量、布局），重写"预测战绩"tab 引入的新选择器不得覆盖或污染其他 tab 的现有规则
3.9 WHEN 浏览器加载 `static/script.js` THEN 系统 SHALL CONTINUE TO 提供原有全局函数（`switchTab`、`fetchTeams`、`runClassic`、`runAI`、`loadLotteryMatches`、`loadSmartMatches` 等）的签名与行为
3.10 WHEN 用户登录、登出、查看积分、消耗积分进行 AI 预测 THEN 系统 SHALL CONTINUE TO 正常工作，导航栏与积分徽章不受"预测战绩"tab 重写的任何影响

## Bug Condition Derivation

### Bug Condition Function

```pascal
FUNCTION isBugCondition(X)
  INPUT: X of type RecordTabInteraction with fields {
            tabClicked,           // 当前激活的 tab key
            filterChanged,        // 触发筛选变化的下拉框 id（可空）
            hasFinishedMatches    // 数据库中是否存在 actual_result IS NOT NULL 的记录
         }
  OUTPUT: boolean

  // 触发条件：用户进入"预测战绩"tab，或在该 tab 内操作筛选
  RETURN X.tabClicked = "record"
      OR X.filterChanged ∈ {"record-filter-league", "record-filter-result"}
END FUNCTION
```

### Property Specification — Fix Checking

```pascal
// Property 1: 进入 record tab 且数据库有已结束比赛 → 必须呈现"预测 vs 真实"对比
FOR ALL X WHERE X.tabClicked = "record" AND X.hasFinishedMatches = true DO
  observe DOM after switchTab("record") completes
  ASSERT  fetch("/api/accuracy/summary")  was called
      AND fetch("/api/accuracy/matches?page=1&per_page=20...") was called
      AND 统计聚合区块已渲染（已对比场次数 / 胜平负命中率 / 比分精确命中率 / 平均进球数偏差）
      AND 比赛列表区为每场比赛渲染了一张对比卡片
      AND 每张对比卡片同时呈现：
            (a) 预测胜平负 与 实际胜平负，且带 ✅/❌ 命中标识
            (b) 预测比分    与 实际比分，  且带 ✅/❌ 命中标识
            (c) 预测总进球数 与 实际总进球数，且显示误差值
      AND 卡片中绝不只显示真实值或只显示预测值（必须并排）
      AND 比赛列表中所有数据均来自后端响应（无前端虚构数据）
END FOR

// Property 2: 进入 record tab 且数据库无任何已结束比赛 → 仅显示空状态
FOR ALL X WHERE X.tabClicked = "record" AND X.hasFinishedMatches = false DO
  observe DOM after switchTab("record") completes
  ASSERT 唯一可见的内容是空状态提示
      AND 统计聚合区块（4 张统计卡片）       未渲染
      AND 分联赛准确率柱状条                 未渲染
      AND 近期命中趋势图                      未渲染
      AND 筛选器                              未渲染
      AND 比赛列表                            未渲染
      AND 分页器                              未渲染
      AND 空状态文案为"暂无已结束的比赛数据，比赛结束后会自动同步并生成对比"
END FOR

// Property 3: 筛选下拉操作应可调用且重新加载列表
FOR ALL X WHERE X.filterChanged ∈ {"record-filter-league", "record-filter-result"} DO
  ASSERT typeof window.loadRecordMatches = "function"
      AND fetch("/api/accuracy/matches?...") was called with new league/result params
      AND no ReferenceError thrown
END FOR

// Property 4: 视觉风格一致性
FOR ALL X WHERE X.tabClicked = "record" AND X.hasFinishedMatches = true DO
  ASSERT 关键展示元素的 computed style 沿用项目共用的圆角/阴影/字体规范
      AND 没有元素以浏览器默认样式（无圆角、无 padding、无配色）呈现
END FOR
```

### Property Specification — Preservation Checking

```pascal
// Property 5: 其他 tab、后端 API、同步脚本与积分系统的行为完全保持
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT F(X) = F'(X)
  // 即：切换到 classic / ai / smart / wc tab 的行为，
  //    后端 /api/accuracy/summary 与 /api/accuracy/matches 的响应契约，
  //    其他 API 端点与 sync_results.py 等脚本的行为，
  //    登录 / 登出 / 积分系统的行为，
  //    在修复前后完全一致
END FOR
```

### Key Definitions

- **F**：当前未修复的前端代码
  - `static/script.js` 中无 `record` tab 的加载/渲染分支与函数
  - `static/css/style.css` 中无任何与"预测战绩"相关的样式规则
  - `templates/index.html` 中 `#panel-record` 的结构是"4 卡 + 联赛柱状条 + 趋势图 + 筛选 + 列表 + 分页 + 空状态"，但**未设计预测 vs 真实并排对比**

- **F'**：修复后的前端代码
  - `templates/index.html` 中 `#panel-record` 内部结构允许重新设计，DOM 容器 ID（如 `#record-stats-grid`、`#rsc-total`、`#record-league-bars`）不要求保留；唯一保留的硬约束是 `#panel-record` 根元素本身（兼容 `switchTab` 路由）
  - `static/script.js` 新增（或替换）该 tab 的加载与渲染逻辑：进入 tab 时同时调用两个 API；按 `total_predicted = 0` / `> 0` 分支渲染；为每场比赛卡片渲染"预测 vs 真实"三维度对照与命中标识；提供全局可调用的 `loadRecordMatches()` 函数响应筛选/分页变化
  - `static/css/style.css` 新增对应样式，与项目其他 tab 视觉一致，且不污染其他选择器

- **Counterexample**：
  1. 数据库中已有 5 场已结束比赛 → 用户切换到"预测战绩"tab → 列表中每场比赛卡片只显示真实比分而没有同位置的预测比分对照、或没有 ✅/❌ 命中标识 → 这是 `isBugCondition` 为真且 Property 1 失败的具体反例。
  2. 数据库中无任何已结束比赛 → 用户切换到"预测战绩"tab → 4 张统计卡片仍显示 `--` / `--%`、列表区显示永久 spinner → 这是 `isBugCondition` 为真且 Property 2 失败的具体反例。
  3. 在"预测战绩"tab 下点击"全部联赛"下拉框 → DevTools Console 抛出 `ReferenceError: loadRecordMatches is not defined` → Property 3 的反例。
