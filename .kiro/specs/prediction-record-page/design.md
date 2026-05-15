# Prediction Record Page Bugfix Design

## Overview

"预测战绩"（Prediction Record）标签页当前是一个"半拉子"实现：HTML 模板里堆放了 4 张统计卡 / 联赛柱状条 / 趋势图 / 筛选 / 列表 / 分页 / 空状态等容器，但 `static/script.js` 里完全没有 `record` 分支，CSS 也没有对应规则，因此该 tab 无论数据库里有没有已结束比赛，呈现都是错的——要么永远是 spinner，要么是 `--` / `--%` 占位，且即使补齐 JS 也无法表达"我预测对了哪些"这个核心视角（缺少"预测 vs 真实"逐场对照）。

修复方案：保留 tab 入口（`#tab-record` 按钮 + `#panel-record` 根面板）以兼容 `switchTab` 路由，**重新设计** `#panel-record` 内部的 DOM 结构、CSS 命名空间与 JS 渲染函数，把整个 tab 重写为一个以"预测 vs 真实逐场对比"为核心的视图：

- 进入 tab 时并行调用 `/api/accuracy/summary` 与 `/api/accuracy/matches`，仅基于真实响应渲染（绝不 mock）。
- `summary.total_predicted = 0` 时整个 tab 只展示一个空状态提示，**不渲染**任何统计/列表/筛选/分页区块。
- `summary.total_predicted > 0` 时为每场比赛渲染一张三段式对照卡片，并排展示「胜平负」「比分」「总进球数」三维度的预测 vs 真实，并标注 ✅/❌ 命中标识与误差值。
- 全部使用 `.pr-*` 命名空间的新 CSS 类与新 JS 函数，避免污染既有的 `.record-*` / `.rsc-*` 选择器或其他 tab。
- 后端两个 API 的响应契约**不修改**，其他 tab、积分、登录、同步脚本行为完全保持。

## Glossary

- **Bug_Condition (C)**：用户进入"预测战绩" tab，或在该 tab 内操作筛选下拉框（`#record-filter-league` / `#record-filter-result`）所对应的交互输入。形式上参见 `bugfix.md` 中的 `isBugCondition`。
- **Property (P)**：满足 C 时系统应表现的正确行为——在有数据时为每场比赛并排呈现"预测 vs 真实"三维度对比与命中标识；在无数据时仅渲染单一空状态；筛选函数全局可调。
- **Preservation**：所有 ¬C 的输入（其他 tab 切换、其他 API 调用、同步脚本、登录/积分系统）的行为在修复前后完全一致。
- **`#panel-record`**：`templates/index.html` 中预测战绩 tab 的根面板元素。修复中保留此根元素与其 `id` / `class="tab-panel hidden"`，仅替换其内部子树。
- **`switchTab(tab)`**：`static/script.js` 中的 tab 路由函数，根据 `tab` 切换激活面板并触发各 tab 的首次加载逻辑。
- **`/api/accuracy/summary`**：返回 `{ success, summary{ total_finished, total_predicted, correct, score_hit, accuracy, avg_goal_error }, league_stats[], trend[] }` 的后端端点（契约不变）。
- **`/api/accuracy/matches`**：返回 `{ success, total, page, per_page, matches[] }`，每条 `match` 含 `predicted_result` / `actual_result` / `predicted_score` / `actual_score` / `predicted_home_goals` / `predicted_away_goals` / `actual_home_goals` / `actual_away_goals` / `result_correct` / `score_correct` / `goal_diff_error` / `ml_probs` / `odds` 等字段（契约不变）。
- **对比卡片（Match Compare Card）**：每场已结束比赛在列表中渲染的 `.pr-match-card` 节点。卡片内必含三个 `.pr-cmp-row`（胜平负 / 比分 / 总进球），每行结构性并排呈现预测列与真实列。

## Bug Details

### Bug Condition

该 bug 在两类输入下都会显现：(1) 用户切换到 `record` tab；(2) 用户在 record tab 内变更筛选下拉。前者暴露"渲染逻辑缺失 + 视角错位"；后者暴露 `loadRecordMatches` 未定义引发 `ReferenceError`。两种输入都属于"该 tab 应当负责的交互"，因此都进入 Bug Condition。

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type RecordTabInteraction with fields {
            tabClicked,         // 当前激活的 tab key
            filterChanged,      // 触发 onchange 的下拉 id（可空）
            hasFinishedMatches  // DB 中是否存在 actual_result IS NOT NULL 的记录
         }
  OUTPUT: boolean

  RETURN input.tabClicked = "record"
      OR input.filterChanged IN {"record-filter-league", "record-filter-result"}
END FUNCTION
```

### Examples

- **DB 有 5 场已结束比赛 → 切换到 record tab**：当前列表只显示真实比分（甚至完全是 spinner），没有同位置并排的预测比分、没有 ✅/❌ 命中标识、没有总进球误差。期望：每场卡片同时展示三维度对照与命中标识。
- **DB 无任何已结束比赛（`total_predicted = 0`）→ 切换到 record tab**：当前页面渲染 4 张 `--` / `--%` 占位卡 + 联赛柱状条 + 趋势图容器 + 筛选下拉 + 列表 spinner，给用户"系统坏了"的错觉。期望：仅显示一个空状态文案"暂无已结束的比赛数据，比赛结束后会自动同步并生成对比"，其他区块全部不渲染。
- **DB 有数据 → 在 record tab 点击"全部联赛"或"全部结果"下拉**：DevTools Console 抛出 `ReferenceError: loadRecordMatches is not defined`，列表不刷新。期望：筛选触发新一轮 `/api/accuracy/matches` 请求并刷新列表与分页。
- **任何路径下 → record tab 元素都没有 CSS 样式**：呈现浏览器默认外观，与其他 tab 视觉割裂。期望：与其他 tab 的卡片圆角 / 阴影 / 间距 / 字体一致。
- **边界**：`/api/accuracy/summary` 返回非 2xx 或 `success: false`，当前永久 spinner；期望：渲染可读错误提示并在 console 记录。

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- "深度权重"（`tab-classic`）的联赛/球队选择、赔率输入、预测调用与结果展示完全不变。
- "AI 大模型"（`tab-ai`）首次进入仍调用 `loadLotteryMatches()` 加载赛事广场，并支持加入待分析队列。
- "智能选场"（`tab-smart`）首次进入仍调用 `loadSmartMatches()` 加载比赛。
- "世界杯预测"（`tab-wc`）的倒计时与既有内容完全不变。
- 后端 `GET /api/accuracy/summary`、`GET /api/accuracy/matches` 的响应字段、字段名、字段类型、字段含义不变。
- 后台脚本 `scripts/sync_results.py` 同步 `actual_result`、`actual_home_goals`、`actual_away_goals`、`result_correct`、`score_correct`、`goal_diff_error`、`finished_at` 的行为不变。
- `static/script.js` 中 `switchTab`、`fetchTeams`、`runClassic`、`runAI`、`loadLotteryMatches`、`loadSmartMatches` 等既有全局函数的签名与行为保持。
- 登录、登出、积分徽章、AI 预测扣分流程完全不变。
- `static/css/style.css` 中所有现有选择器与 CSS 变量（`--primary-color` / `--secondary-color` / `--accent-color` / `--success-color` / `--warning-color` / `--danger-color` / `--text-primary` / `--text-secondary` / `--text-light` / `--bg-primary` / `--bg-secondary` / `--card-bg` / `--border-color` / `--gradient-primary` / `--shadow-light` / `--shadow-medium` 等）的语义与样式不变。

**Scope:**
所有不进入 `isBugCondition` 的输入都应完全不受本次修复影响。具体包括但不限于：
- 切换到 `classic` / `ai` / `smart` / `wc` 任意 tab。
- 后端 `/api/teams`、`/api/leagues`、`/api/predict`、`/api/ai/*`、`/api/smart/*`、`/api/auth/*`、`/api/credits/*` 等所有非 accuracy 端点的请求与响应。
- 同步脚本（`sync_daily_matches.py` / `sync_historical.py` / `sync_results.py` / `sync_upcoming.py`）。
- 数据库 schema、`upcoming_fixtures` 表的字段与索引。

## Hypothesized Root Cause

基于 bug 描述与现有代码勘察，最可能的根因有四类：

1. **JS 渲染分支缺失（最主要）**：`static/script.js` 中 `switchTab` 没有 `tab === 'record'` 分支，也没有 `loadRecord*` / `renderRecord*` 系列函数，更没有全局 `loadRecordMatches`。后端 API 永不被调用，所有占位永远不会被替换。
   - 直接证据：`grep_search switchTab|loadRecord|record-` 在 `static/script.js` 内无任何与 record 相关的命中。
   - 解释 1.4、1.5、1.7 的现象。

2. **HTML 视角错位**：现有 `#panel-record` 子树围绕"统计聚合 + 平铺真实结果列表"组织（`record-stats-grid` / `record-league-bars` / `record-trend-chart` / `record-match-list`），结构上没有为每场比赛留出"预测列 / 真实列 / 命中标识"的并排槽位。即便补齐 JS，把数据塞进去后也无法呈现核心视角。
   - 解释 1.1、1.2 的现象。

3. **空数据时未做退化分支**：现有模板在初始化时同时把统计卡 / 联赛柱状条 / 趋势图 / 筛选 / 列表 / 分页 / 空状态全部渲染（空状态默认 `display:none`）。没有"`total_predicted = 0` 时仅显示空状态、其他全部不渲染"的分支。
   - 解释 1.3 的现象。

4. **CSS 缺失**：`static/css/style.css` 中没有 `.record-*` / `.rsc-*` 选择器规则，导致即便结构存在也是浏览器默认样式，与项目其他 tab 视觉割裂。
   - 解释 1.6 的现象。

修复方向：把 1 + 2 + 3 + 4 一起处理——重写 `#panel-record` 内部 DOM、用 `.pr-*` 命名空间新增 CSS、在 JS 中新增完整的 `loadRecord` / `renderRecord*` 模块以及全局 `loadRecordMatches`。

## Correctness Properties

Property 1: Bug Condition - "预测 vs 真实"逐场对比正确呈现

_For any_ 用户进入 `record` tab 的输入（`isBugCondition` 返回 true），当 `summary.total_predicted > 0` 时，`#panel-record` SHALL 渲染：(a) 统计聚合区（已对比场次 / 胜平负命中率 / 比分精确命中率 / 平均进球数偏差 4 项）；(b) 比赛列表中每场已结束比赛的对比卡片，每张卡片同时包含三个 `.pr-cmp-row` 节点，分别结构性并排展示「预测胜平负 vs 实际胜平负 + ✅/❌」「预测比分 vs 实际比分 + ✅/❌」「预测总进球数 vs 实际总进球数 + 误差值」；(c) 渲染的所有 `fixture_id` 均来自 `/api/accuracy/matches` 响应，前端不构造任何虚假数据。

**Validates: Requirements 2.1, 2.3, 2.4, 2.5, 2.13**

Property 2: Bug Condition - 空数据时仅显示空状态

_For any_ 用户进入 `record` tab 的输入且 `summary.total_predicted = 0`，`#panel-record` 内 SHALL 仅渲染一个空状态区块（文案为"暂无已结束的比赛数据，比赛结束后会自动同步并生成对比"），并 SHALL **不渲染**统计聚合区 / 分联赛准确率区 / 近期命中趋势区 / 筛选条 / 比赛列表 / 分页器中的任何一项。

**Validates: Requirements 2.2**

Property 3: Bug Condition - 筛选交互可用

_For any_ 用户在 record tab 内变更 `#record-filter-league` 或 `#record-filter-result` 的输入，`window.loadRecordMatches` SHALL 是一个 `function`，其调用 SHALL 以新的 `league` / `result` 查询参数重新请求 `/api/accuracy/matches?page=1&...` 并刷新列表与分页，且 SHALL NOT 抛出 `ReferenceError`。

**Validates: Requirements 2.8, 2.9**

Property 4: Bug Condition - 命中标识与三维度数据一致

_For any_ 渲染出的对比卡片，当 `match.result_correct = true` 时该卡片的胜平负行 SHALL 显示 ✅ 标识，反之显示 ❌；当 `match.score_correct = true` 时该卡片的比分行 SHALL 显示 ✅，反之显示 ❌；总进球行 SHALL 显示数值等于 `match.goal_diff_error` 的误差值（若该字段为 `null` 则显示 `-`，不伪造）。

**Validates: Requirements 2.4**

Property 5: Bug Condition - 错误状态可见

_For any_ `/api/accuracy/summary` 或 `/api/accuracy/matches` 返回非 2xx 或 `success: false` 的输入，`#panel-record` SHALL 显示一条可读的错误文案（而非永久 spinner），并 SHALL 在浏览器 console 记录错误详情。

**Validates: Requirements 2.11, 2.12**

Property 6: Preservation - 其他 tab 与 API 行为完全保持

_For any_ 不满足 `isBugCondition` 的输入（其他 tab 切换、其他 API 调用、同步脚本、登录/积分系统、`#panel-record` 之外的 DOM 与 CSS），修复后的代码 SHALL 产生与修复前完全相同的结果，保持 `classic` / `ai` / `smart` / `wc` 四个 tab 的渲染、`/api/accuracy/*` 的响应契约、`scripts/sync_*.py` 的行为、所有现有 `.record-*` / `.rsc-*` 之外的 CSS 选择器、以及 `switchTab` / `fetchTeams` / `loadLotteryMatches` / `loadSmartMatches` 等全局函数的语义不变。

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10**

## Fix Implementation

### Architecture / 总体方案

修复仅触及前端三层文件：

| 层 | 文件 | 改动方式 |
|---|---|---|
| 模板 | `templates/index.html` | **替换** `#panel-record` 子树为新 `.pr-*` 结构（保留根元素 `<div id="panel-record" class="tab-panel hidden">`）。移除 HTML 内联 `onchange="loadRecordMatches()"`，改为 JS 初始化时 `addEventListener`，但同时仍把 `loadRecordMatches` 暴露到 `window` 以保证健壮性。 |
| 脚本 | `static/script.js` | **新增** `record` tab 的加载/渲染模块；`switchTab` 增加 `record` 分支；声明全局可调用的 `loadRecordMatches`。 |
| 样式 | `static/css/style.css` | **追加** `.pr-*` 命名空间样式段到文件末尾，不修改任何现有选择器。 |

数据流：

```
user clicks #tab-record
  → switchTab('record')
      → 切换面板可见性（既有逻辑）
      → if (!recordState.loaded) ensureRecordLoaded()
          → loadRecord()
              → Promise.all([ loadRecordSummary(), loadRecordMatches() ])
                  → fetch('/api/accuracy/summary')   → summary
                  → fetch('/api/accuracy/matches?…') → matchesPayload
              → renderRecord(summary, matchesPayload)
                  → if summary.total_predicted == 0 → renderRecordEmpty()
                  → else → renderRecordStats / renderRecordLeagues / renderRecordTrend
                          / renderRecordFilters / 列表(map renderRecordMatchCard)
                          / renderRecordPagination
```

### Changes Required

**File**: `templates/index.html`

**Target**: `#panel-record` 子树（保留根元素及其 `id` / `class`）。

**Specific Changes**:

1. **保留根、替换内部**：保留 `<div id="panel-record" class="tab-panel hidden">…</div>` 根，删除现有内部所有 `record-*` / `rsc-*` 子元素，替换为 `.pr-*` 命名空间的新结构。

2. **新 DOM 结构（建议骨架）**：
   ```html
   <div id="panel-record" class="tab-panel hidden">
     <!-- Hero：始终可见（标题 + 副标题） -->
     <header class="pr-hero">
       <div class="pr-hero-icon"><i class="fas fa-chart-pie"></i></div>
       <h2 class="pr-hero-title">预测战绩</h2>
       <p class="pr-hero-subtitle">ML 模型预测 vs 真实比赛结果，逐场对照</p>
     </header>

     <!-- 三个互斥状态：loading / error / empty / content -->
     <div id="pr-status" class="pr-status">
       <!-- loading -->
       <div class="pr-loading"><i class="fas fa-spinner fa-spin"></i><span>加载中…</span></div>
     </div>

     <!-- content：仅在 total_predicted > 0 时由 JS 注入 -->
     <section id="pr-content" class="pr-content" hidden>
       <div id="pr-stats"   class="pr-stats"></div>           <!-- 4 统计卡 -->
       <div id="pr-leagues" class="pr-leagues" hidden></div>  <!-- 分联赛区 -->
       <div id="pr-trend"   class="pr-trend"   hidden></div>  <!-- 14 天趋势 -->
       <div id="pr-filter"  class="pr-filter">
         <select id="pr-filter-league">
           <option value="">全部联赛</option>
           <!-- 联赛 option 由 JS 注入或保持静态 5 选项 -->
         </select>
         <select id="pr-filter-result">
           <option value="">全部结果</option>
           <option value="correct">✅ 命中</option>
           <option value="wrong">❌ 未命中</option>
         </select>
       </div>
       <div id="pr-list"       class="pr-list"></div>         <!-- 比赛对比卡片列表 -->
       <div id="pr-pagination" class="pr-pagination"></div>
     </section>
   </div>
   ```

3. **移除内联 onchange**：删去 HTML 中 `onchange="loadRecordMatches()"` 内联绑定，改由 JS 用 `addEventListener('change', …)`（`loadRecordMatches` 仍以 `window.loadRecordMatches` 形式暴露作为兜底）。

4. **空状态文案**：通过 JS `renderRecordEmpty()` 注入到 `#pr-status`，模板里不预置占位卡。

**File**: `static/script.js`

**Target**: 新增模块；修改 `switchTab`；新增全局函数。

**Specific Changes**:

1. **新增模块状态**（顶部 STATE 段）：
   ```js
   let recordState = {
     page: 1,
     perPage: 20,
     league: '',
     result: '',
     loading: false,
     loaded: false,
     summary: null,
     matchesPayload: null,
   };
   ```

2. **`switchTab` 增加分支**：
   ```js
   if (tab === 'record') ensureRecordLoaded();
   ```
   注意：所有现有分支（`ai` / `smart`）保持不变。

3. **新增函数（建议命名 / 职责）**：

   | 函数 | 职责 |
   |---|---|
   | `ensureRecordLoaded()` | 若 `recordState.loaded || recordState.loading` 则 return；否则调用 `loadRecord()`。同时是绑定 `#pr-filter-*` change 事件的一次性钩子。 |
   | `loadRecord()` | 入口；切到 loading 状态；`Promise.all` 调用 summary + matches；写入 `recordState`；调用 `renderRecord`；try/catch 渲染错误状态。 |
   | `loadRecordSummary()` | `fetch('/api/accuracy/summary')`，校验 `success`，返回 `{summary, league_stats, trend}`。 |
   | `loadRecordMatches()` | **全局**（挂到 `window.loadRecordMatches`）。读取 `recordState` 中的 `page/perPage/league/result` 拼出 query，请求 `/api/accuracy/matches?…`，更新 `recordState.matchesPayload`，调用 `renderRecordList` + `renderRecordPagination`。筛选/分页变化都走这个函数。 |
   | `renderRecord(summary, matchesPayload)` | 顶层路由：`summary.total_predicted === 0` → `renderRecordEmpty()`；否则隐藏 `#pr-status`、显示 `#pr-content`，依次调用各 render*。 |
   | `renderRecordEmpty()` | 把 `#pr-status` 替换为单一空状态节点；隐藏 `#pr-content`。 |
   | `renderRecordError(message)` | 把 `#pr-status` 替换为可读错误提示；隐藏 `#pr-content`；`console.error` 详情。 |
   | `renderRecordStats(summary)` | 渲染 4 张统计卡到 `#pr-stats`。 |
   | `renderRecordLeagues(leagueStats)` | `leagueStats.length > 0` 时显示并渲染分联赛准确率，否则保持 hidden。 |
   | `renderRecordTrend(trend)` | `trend.length > 0` 时显示并渲染 14 天命中趋势，否则保持 hidden。 |
   | `renderRecordList(matches)` | 把 `matches.map(renderRecordMatchCard).join('')` 写入 `#pr-list`。 |
   | `renderRecordMatchCard(match)` | **核心**。返回单张三段式对比卡片 HTML。见下文骨架。 |
   | `renderRecordPagination(total, page, perPage)` | `total <= perPage` 不渲染；否则渲染上一页/页码/下一页，点击触发 `loadRecordMatches()`。 |

4. **`renderRecordMatchCard` 骨架**：
   ```js
   function renderRecordMatchCard(m) {
     const predTotal = (m.predicted_home_goals ?? null) !== null && (m.predicted_away_goals ?? null) !== null
       ? m.predicted_home_goals + m.predicted_away_goals : null;
     const realTotal = (m.actual_home_goals ?? null) !== null && (m.actual_away_goals ?? null) !== null
       ? m.actual_home_goals + m.actual_away_goals : null;
     const resultMark = m.result_correct === true ? '✅' : (m.result_correct === false ? '❌' : '-');
     const scoreMark  = m.score_correct  === true ? '✅' : (m.score_correct  === false ? '❌' : '-');
     const goalErr    = m.goal_diff_error ?? null;
     return `
       <article class="pr-match-card" data-fixture-id="${m.fixture_id}">
         <header class="pr-match-head">
           <span class="pr-league">${m.league ?? '-'}</span>
           <span class="pr-teams">${m.home_team_cn || m.home_team || '-'} <em>vs</em> ${m.away_team_cn || m.away_team || '-'}</span>
           <span class="pr-time">${m.match_time ? formatTime(m.match_time) : '-'}</span>
         </header>
         <div class="pr-cmp">
           <div class="pr-cmp-row" data-dim="result">
             <span class="pr-cmp-label">胜平负</span>
             <span class="pr-cmp-pred">${m.predicted_result ?? '-'}</span>
             <span class="pr-hit-mark pr-hit-${m.result_correct ? 'ok' : 'no'}">${resultMark}</span>
             <span class="pr-cmp-actual">${m.actual_result ?? '-'}</span>
           </div>
           <div class="pr-cmp-row" data-dim="score">
             <span class="pr-cmp-label">比分</span>
             <span class="pr-cmp-pred">${m.predicted_score ?? '-'}</span>
             <span class="pr-hit-mark pr-hit-${m.score_correct ? 'ok' : 'no'}">${scoreMark}</span>
             <span class="pr-cmp-actual">${m.actual_score ?? '-'}</span>
           </div>
           <div class="pr-cmp-row" data-dim="goals">
             <span class="pr-cmp-label">总进球</span>
             <span class="pr-cmp-pred">${predTotal ?? '-'}</span>
             <span class="pr-hit-mark pr-hit-diff">${goalErr === null ? '-' : `差 ${goalErr} 球`}</span>
             <span class="pr-cmp-actual">${realTotal ?? '-'}</span>
           </div>
         </div>
         ${m.ml_probs ? `<footer class="pr-match-foot">${renderProbBar(m.ml_probs)}${renderOdds(m.odds)}</footer>` : ''}
       </article>
     `;
   }
   ```

5. **错误处理**：`loadRecord` / `loadRecordMatches` 全部 try/catch；任何异常调用 `renderRecordError(err.message)`，并 `console.error('[record]', err)`，绝不静默 fallback 到任何 mock 数据。

6. **数据真实性兜底**：在 `renderRecordList` 入口加 `console.assert(Array.isArray(matches), '[record] matches must be array')`，并在 dev 期 `console.assert(matches.length === payload.matches.length)`。所有缺失字段一律用 `-` 显示，不伪造。

7. **重复请求防护**：`recordState.loading` 标记进行中状态；`recordState.loaded` 标记首次加载完成；筛选/分页变化绕过 `loaded` 直接调用 `loadRecordMatches()`，但用 `recordState.loading` 防抖。

**File**: `static/css/style.css`

**Target**: 文件末尾追加 `.pr-*` 命名空间样式段，绝不修改既有选择器。

**Specific Changes**:

1. **沿用项目 CSS 变量**：使用 `--bg-primary` / `--bg-secondary` / `--card-bg` / `--border-color` / `--text-primary` / `--text-secondary` / `--text-light` / `--primary-color` / `--secondary-color` / `--accent-color` / `--success-color` / `--warning-color` / `--danger-color` / `--shadow-light` / `--shadow-medium` / `--gradient-primary`，保持与其他 tab 一致的圆角 / 阴影 / 字体层级。

2. **关键类样式**：
   - `.pr-hero` / `.pr-hero-title` / `.pr-hero-subtitle`：与其他 tab hero 一致的标题区。
   - `.pr-stats`：grid，4 列（`auto-fit, minmax(180px, 1fr)`）；`.pr-stat-card` 用 `--card-bg` + `--shadow-light` + `border-radius: 12px`。
   - `.pr-leagues`、`.pr-trend`：和 `.pr-stat-card` 同款卡片容器，标题用 `--primary-color` icon。
   - `.pr-match-card`：白底卡片；`header.pr-match-head` 横排（联赛 + 主客 + 时间）；`.pr-cmp` 内三行 `.pr-cmp-row`；`.pr-cmp-row` 使用 `display: grid; grid-template-columns: 80px 1fr auto 1fr;`，标签 / 预测列（`--primary-color` 蓝色调）/ 命中徽章（绿/红/中性灰）/ 真实列（`--success-color` 绿色调）。
   - `.pr-hit-mark`：圆形/胶囊；`.pr-hit-ok` 用 `--success-color` 背景；`.pr-hit-no` 用 `--danger-color` 背景；`.pr-hit-diff` 用 `--text-light` 中性灰。
   - `.pr-pagination`：与项目一致的按钮风格（圆角、`--primary-color` hover）。
   - `.pr-empty`：居中、icon + 双行文案，颜色 `--text-secondary`。
   - `.pr-loading` / `.pr-error`：与项目一致的居中加载/错误条。

3. **响应式**：`@media (max-width: 640px)` 下 `.pr-cmp-row` 退化为单列堆叠（`grid-template-columns: 1fr`），命中徽章移到行尾。

4. **零污染**：所有规则限定在 `.pr-*` 命名空间或 `#pr-*` ID 内；不写 `.record-*` / `.rsc-*` / `#panel-record .xxx`（除根 `#panel-record .pr-...`）以避免污染历史选择器或其他 tab。

### 字段映射表（API 字段 → DOM 槽位）

| API 字段 | 用途 / DOM 槽位 |
|---|---|
| `summary.total_predicted` | 决定渲染分支（=0 空状态 / >0 完整视图）；同时填入 `.pr-stat-card[已对比场次]` |
| `summary.accuracy` | `.pr-stat-card[胜平负命中率]`，单位 % |
| `round(summary.score_hit / summary.total_predicted * 100, 1)` | `.pr-stat-card[比分精确命中率]`，单位 % |
| `summary.avg_goal_error` | `.pr-stat-card[平均进球数偏差]` |
| `summary.league_stats[]` | `#pr-leagues` 区，每联赛 1 行 progress bar |
| `summary.trend[]` | `#pr-trend` 区，14 天 mini chart |
| `matches[].fixture_id` | `.pr-match-card[data-fixture-id]` |
| `matches[].league` / `home_team_cn` / `away_team_cn` / `match_time` | `.pr-match-head` |
| `matches[].predicted_result` / `actual_result` | `.pr-cmp-row[data-dim=result]` 内 `.pr-cmp-pred` / `.pr-cmp-actual` |
| `matches[].result_correct` | `.pr-cmp-row[data-dim=result]` 内 `.pr-hit-mark`（✅/❌） |
| `matches[].predicted_score` / `actual_score` | `.pr-cmp-row[data-dim=score]` 内 `.pr-cmp-pred` / `.pr-cmp-actual` |
| `matches[].score_correct` | `.pr-cmp-row[data-dim=score]` 内 `.pr-hit-mark` |
| `matches[].predicted_home_goals + predicted_away_goals` | `.pr-cmp-row[data-dim=goals]` `.pr-cmp-pred` |
| `matches[].actual_home_goals + actual_away_goals` | `.pr-cmp-row[data-dim=goals]` `.pr-cmp-actual` |
| `matches[].goal_diff_error` | `.pr-cmp-row[data-dim=goals]` `.pr-hit-mark.pr-hit-diff` |
| `matches[].ml_probs` / `matches[].odds` | `.pr-match-foot`（可选） |

### 卡片视觉草图（ASCII）

```
┌─────────────────────────────────────────────────────────────┐
│ 🇪🇸 西甲    皇家马德里 vs 巴塞罗那        2024-10-26 22:00     │
├─────────────────────────────────────────────────────────────┤
│ 胜平负   │  主胜 (PRED)  │ ✅ │  主胜 (REAL)                  │
│ 比分     │  2-1  (PRED)  │ ❌ │  3-1  (REAL)                  │
│ 总进球   │   3   (PRED)  │差1球│   4   (REAL)                  │
├─────────────────────────────────────────────────────────────┤
│ ▓▓▓▓▓▓▓░░░ 主胜 62%  ░░░ 平 18%  ░░░ 客胜 20% │ 1.85/3.40/4.20 │
└─────────────────────────────────────────────────────────────┘
```

## Testing Strategy

### Validation Approach

两阶段：(1) 在**未修复**代码上跑探索测试，先确认 bug 表现并验证 / 推翻根因假设；(2) 修复后跑 fix-checking 与 preservation-checking，确认核心视角实现且既有行为零回归。

### Exploratory Bug Condition Checking

**Goal**: 在写修复代码之前，先用测试在 unfixed 代码上重现 bug，验证根因假设——尤其是「JS 缺失分支」「视角错位」「空数据未退化」这三条。

**Test Plan**: 用浏览器 e2e（或 jsdom + puppeteer/playwright）模拟用户切换到 record tab 与改变筛选下拉，断言 DOM 与网络。先在当前 `static/script.js`（无 record 分支）下运行，**预期失败**——这就是我们要的反例。

**Test Cases**:
1. **EnterTabWithData**：DB 中 mock 5 场已结束（在 API 层 stub，不在前端 mock）→ 切换到 record tab → 断言列表里至少存在 1 个 `.pr-cmp-row[data-dim=result]` 节点（**will fail on unfixed code**：根本没有 `.pr-cmp-row`，列表区永远是 spinner）。
2. **EnterTabEmpty**：API summary 返回 `total_predicted = 0` → 切换到 record tab → 断言 `#panel-record` 内可见的非根/非 hero 元素 ≤ 1（仅空状态）（**will fail on unfixed code**：当前会渲染 4 张占位卡 + 联赛柱状条容器 + 趋势图容器 + 筛选 + 列表 spinner）。
3. **FilterChange**：进入 record tab → 改 `#record-filter-league` → 断言 `typeof window.loadRecordMatches === 'function'` 且无 `ReferenceError`（**will fail on unfixed code**：抛 `ReferenceError`）。
4. **OutOfRangePagination（边界）**：API 返回 `total = 0, matches = []` → 断言 `#pr-pagination` 不渲染分页控件（may fail on unfixed code）。
5. **ApiError**：summary 端点返回 500 → 断言 `#panel-record` 内显示可读错误而非永久 spinner（will fail on unfixed code）。

**Expected Counterexamples**:
- 用例 1：`document.querySelectorAll('.pr-cmp-row').length === 0`，证明 JS 渲染分支缺失（根因 1）+ DOM 视角错位（根因 2）。
- 用例 2：`#record-stats-grid` 与 `#record-match-list` 都可见，证明无空数据退化分支（根因 3）。
- 用例 3：`ReferenceError: loadRecordMatches is not defined`，证明全局函数未声明（根因 1 子项）。
- 用例 5：`.spinner-overlay` 永远存在，证明缺少错误状态分支。
- 可能的其他原因：CSS 缺失（根因 4）会让用例的视觉断言失败但不影响 DOM 断言；如果上述 DOM 断言全部通过反而是反对根因 1+2 的证据，需要重新假设。

### Fix Checking

**Goal**: 验证对所有满足 `isBugCondition` 的输入，修复后的前端能产生期望行为。

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  result := renderRecordTabAfterFix(input)
  ASSERT  expectedBehavior(result)
  WHERE expectedBehavior(result) =
        ( input.hasFinishedMatches = true ⇒
            result.containsStatsAggregation
            AND result.matchCards.every(card =>
                  card.has('.pr-cmp-row[data-dim=result]')
                  AND card.has('.pr-cmp-row[data-dim=score]')
                  AND card.has('.pr-cmp-row[data-dim=goals]'))
            AND result.matchCards.every(card =>
                  hitMarkConsistentWith(card, apiMatch))
            AND result.fixtureIds ⊆ apiResponse.matches.map(m => m.fixture_id)
        )
        AND
        ( input.hasFinishedMatches = false ⇒
            result.visibleNonRootChildren = [emptyState]
            AND result.visibleText.includes("暂无已结束的比赛数据") )
        AND
        ( input.filterChanged ≠ null ⇒
            typeof window.loadRecordMatches = "function"
            AND new fetch('/api/accuracy/matches?…') was triggered
            AND no ReferenceError thrown )
END FOR
```

### Preservation Checking

**Goal**: 验证对所有 ¬`isBugCondition` 的输入，修复后行为与修复前完全一致。

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT F(input) = F'(input)
  // input 涵盖：切换到 classic / ai / smart / wc tab；
  //            调用 /api/teams、/api/leagues、/api/predict、/api/ai/*、/api/smart/*、/api/auth/*、/api/credits/*；
  //            调用 /api/accuracy/summary、/api/accuracy/matches 的响应字段、字段名、字段类型；
  //            执行 sync_results.py / sync_daily_matches.py 等脚本；
  //            登录/登出/积分扣减流程；
  //            所有非 .pr-* / 非 #pr-* 的 CSS 选择器。
END FOR
```

**Testing Approach**: 推荐用 property-based testing 来跑 preservation：
- 它能在大量随机输入下确认行为不变，覆盖手写单测难以枚举的边角。
- 对于"前端不污染既有 CSS"这类全量断言，PBT 比逐选择器枚举更鲁棒。
- 对于其他 tab 切换的回归，可以随机化 tab 切换序列，断言每个 tab 切换前后 DOM 与发起的 fetch URL 序列与 unfixed 一致。

**Test Plan**: 先在 unfixed 代码上录制其他 tab 与所有 API 端点的行为快照，再在 fixed 代码上跑 PBT 对比快照。

**Test Cases**:
1. **OtherTabsSwitchPreservation**：在 unfixed 上录制 `classic` / `ai` / `smart` / `wc` 切换的 DOM 与 fetch 序列 → 在 fixed 上跑同样的随机切换序列，断言一致。
2. **OtherApiPreservation**：对 `/api/teams` / `/api/leagues` / `/api/predict` / `/api/ai/*` / `/api/smart/*` / `/api/auth/*` / `/api/credits/*` 各端点分别 stub 输入并断言响应在修复前后字节级一致（后端未改动，必然成立，但显式断言以防误改）。
3. **AccuracyApiContractPreservation**：对 `/api/accuracy/summary` 与 `/api/accuracy/matches` 用相同的查询参数请求，断言 JSON shape（字段名 / 字段类型 / 字段顺序无关）在修复前后一致。
4. **ExistingCssSelectorPreservation**：解析 `static/css/style.css`，断言所有 `.pr-*` / `#pr-*` 之外的选择器与规则字节级未变。
5. **GlobalFunctionsPreservation**：断言 `window.switchTab` / `window.fetchTeams` / `window.runClassic` / `window.runAI` / `window.loadLotteryMatches` / `window.loadSmartMatches` 等既有全局函数的存在性与最简调用行为不变。

### Unit Tests

- `renderRecordMatchCard(match)`：纯函数，给定一个 `match` 对象返回 HTML 字符串。覆盖：
  - `result_correct=true / score_correct=true` → 两处 ✅。
  - `result_correct=false / score_correct=false` → 两处 ❌。
  - `goal_diff_error=null` → 误差显示 `-`，不伪造数字。
  - `predicted_*` 字段缺失 → `-` 占位。
  - `ml_probs=null` → 不渲染 footer。
- `renderRecord(summary, matchesPayload)` 的分支：
  - `summary.total_predicted=0` → 仅空状态可见，`#pr-content` 隐藏。
  - `summary.total_predicted>0` 但 `league_stats=[]` → `#pr-leagues` 保持 hidden。
  - `summary.total_predicted>0` 但 `trend=[]` → `#pr-trend` 保持 hidden。
- `loadRecordMatches()` 的查询字符串构造：覆盖 `league=''` / `result=''` / `page=1` 等组合，断言生成的 URL 无空参 noise。
- 错误路径：`fetch` reject 与 `success:false` 都进入 `renderRecordError` 分支。

### Property-Based Tests

- **P1（对比完整性）**：随机生成合法的 `matchesPayload`（fast-check / hypothesis 生成 `matches[]`，每条字段在合法域中取值，可空字段允许 null），渲染后断言每张卡片都包含恰好 3 个 `.pr-cmp-row`，且 `data-dim` 集合等于 `{result, score, goals}`。
- **P3（数据真实性）**：随机生成 payload，渲染后采集所有 `.pr-match-card[data-fixture-id]` 的集合，断言它是 `payload.matches.map(m=>m.fixture_id)` 的子集。
- **P4（命中标识一致性）**：随机生成 `result_correct` / `score_correct` 真值表，断言 ✅/❌ 的渲染严格遵从布尔值；当为 `null` 时显示 `-`。
- **P2（空状态唯一性）**：随机生成 `summary.total_predicted ∈ {0, 1, …, 1000}`，当 = 0 时断言 `#pr-content[hidden]` 且 `#pr-status` 文案匹配；> 0 时断言空状态不存在。
- **P5（路由保留）**：随机生成 tab 切换序列（`classic` / `ai` / `smart` / `wc` / `record` 五元字母表），断言切换到非 record tab 时 `#panel-record` 子树未被读取或修改（mutation observer 计数为 0）。
- **P6（API 契约保留）**：随机生成请求参数，断言前端发起的 fetch URL 的 path 与 query 参数名属于既有契约白名单 `{league, result, page, per_page}`，无新增参数。

### Integration Tests

- **完整流程（有数据）**：seed DB 5 场已结束 + 5 场未结束 → 启 Flask → 浏览器进入首页 → 点击"预测战绩" → 断言 4 张统计卡数值正确、列表渲染 5 张卡片、每张卡三段对比 + 命中标识正确、分页器在 total > per_page 时出现。
- **完整流程（无数据）**：seed DB 0 场已结束 → 进入 tab → 断言仅空状态可见，无任何统计/列表/筛选元素。
- **筛选 + 分页交互**：seed 多联赛多结果 → 改"全部联赛=西甲" → 断言新请求带 `league=西甲` 且列表刷新；改"全部结果=未命中" → 同理；点击下一页 → 断言 `page=2` 请求并刷新；过程中 console 无 `ReferenceError`。
- **错误状态**：mock summary 返回 500 → 断言可读错误文案显示且 `console.error` 有记录。
- **跨 tab 回归**：record tab 加载完后切换到 classic / ai / smart / wc，断言其余 tab 行为与未修复时一致（首次进入 ai 时仍触发 `loadLotteryMatches`，首次进入 smart 时仍触发 `loadSmartMatches`）。
- **视觉一致性（轻量）**：在 fixed 构建上对 `#panel-record` 截屏，比对 `.pr-stat-card` 与既有 `.tab-panel` 卡片的圆角/阴影/字体在视觉上协调（不要求像素级，但要求无浏览器默认无样式表现）。
