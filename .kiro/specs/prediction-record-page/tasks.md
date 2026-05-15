# Implementation Plan

> 本任务列表严格遵循 bugfix 方法论：**先在未修复代码上写探索性测试重现 bug → 写保留性属性测试观察非 bug 行为 → 实现修复 → 验证两类测试**。
>
> **测试基础设施**：本项目目前没有 JS 单测框架。建议在仓库根新增 `package.json` 并安装 `vitest` + `jsdom` + `fast-check` 用于前端单元/属性测试，安装 `playwright`（可选）用于端到端验证。所有测试文件放在 `tests/frontend/` 下。
>
> **数据真实性硬约束**：禁止任何 mock 业务数据。测试中只允许 stub `fetch`/网络层返回符合 API 契约的合法响应；缺失字段在 UI 中以 `-` 显示，不伪造。

---

- [x] 1. Write bug condition exploration test (BEFORE implementing fix)

  - **Property 1: Bug Condition** - "预测 vs 真实" 对照视图缺失与空数据未退化
  - **CRITICAL**: This test MUST FAIL on unfixed code — failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior — it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the bug exists for all inputs satisfying `isBugCondition`
  - **Scoped PBT Approach**: Combine fast-check generators (随机生成 `matches[]` payload) 与 deterministic concrete cases（`total_predicted=0` 空数据 / 5 场已结束 / 筛选 onchange）以保证可复现
  - 测试基础设施搭建（如尚未存在）：
    - 在仓库根创建 `package.json`，devDependencies 添加 `vitest`、`@vitest/ui`、`jsdom`、`fast-check`
    - 在 `vitest.config.js` 中配置 `environment: 'jsdom'`、`setupFiles: ['./tests/frontend/setup.js']`
    - `tests/frontend/setup.js` 中加载 `templates/index.html` 的 `#panel-record` 片段到 `document.body`，并 `import 'static/script.js'`（用 `vm.runInNewContext` 隔离）
  - 测试文件：`tests/frontend/exploration.bug-condition.test.js`
  - 覆盖以下 case（每个 case 都对应 design.md 中 isBugCondition 的一种取值组合）：
    - `EnterTabWithData`：stub `/api/accuracy/summary` 返回 `total_predicted=5, accuracy=60`，stub `/api/accuracy/matches` 返回 5 条合法 match → 触发 `switchTab('record')` → 断言 `document.querySelectorAll('.pr-cmp-row').length >= 15`（5 张卡 × 3 维度），断言 `document.querySelector('.pr-cmp-row[data-dim="result"]')` 存在
    - `EnterTabEmpty`：stub summary 返回 `total_predicted=0` → 触发 `switchTab('record')` → 断言 `#panel-record` 内可见的非根/非 hero 子元素数量等于 1（仅空状态），断言可见文本包含「暂无已结束的比赛数据」
    - `FilterChangeRefSafe`：进入 record tab 后断言 `typeof window.loadRecordMatches === 'function'`；触发 `#pr-filter-league` 的 `change` 事件 → 断言无 `ReferenceError` 抛出且新 fetch 调用带正确的 `league=` query
    - `HitMarkConsistency`（fast-check 属性）：fc.assert(fc.property(matchArb, m => 渲染 → result_correct=true 时 ✅，false 时 ❌，null 时 `-`，never 伪造）)
    - `ApiError`：stub summary 返回 500 → 断言 `.pr-error` 节点出现（不再是永久 spinner），且 `console.error` 被调用过
  - **EXPECTED OUTCOME on UNFIXED code**:
    - `EnterTabWithData` FAILS：`.pr-cmp-row` 数量为 0（DOM 视角错位 + JS 渲染分支缺失）
    - `EnterTabEmpty` FAILS：`#record-stats-grid` / `#record-match-list` 等占位仍可见（无空数据退化分支）
    - `FilterChangeRefSafe` FAILS：抛出 `ReferenceError: loadRecordMatches is not defined`
    - `HitMarkConsistency` FAILS：根本没有命中徽章节点
    - `ApiError` FAILS：永久 spinner，无 `.pr-error`
  - 在 `tests/frontend/exploration.counterexamples.md` 记录每个 case 的实际反例输出，用于 root cause review
  - Mark task complete when：所有 case 都已编写、运行过、失败已记录到 counterexamples.md
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.7; Properties: P1, P2, P3, P4, P5_

- [x] 2. Write preservation property tests (BEFORE implementing fix)

  - **Property 2: Preservation** - 其他 tab / 既有 API 契约 / 既有 CSS 选择器 / 既有全局函数行为不变
  - **IMPORTANT**: Follow observation-first methodology
    1. 在 UNFIXED 代码上观察并录制 baseline（其他 tab DOM 快照、fetch URL 序列、CSS rule 列表、全局函数签名）
    2. 把观察到的真实行为编码为 fast-check 属性测试
    3. 在 UNFIXED 代码上跑测试 → 必须全部 PASS（确认 baseline 准确）
    4. 修复完成后再跑同一套测试 → 必须仍然全部 PASS（确认零回归）
  - 测试文件：`tests/frontend/preservation.property.test.js`
  - 在 `tests/frontend/baselines/` 下建立基线快照（运行一次 unfixed 代码采集）：
    - `tabs-dom.snapshot.json`：`classic` / `ai` / `smart` / `wc` 四 tab 切换后 `#panel-{tab}` 的 outerHTML 哈希
    - `fetch-sequence.snapshot.json`：每个 tab 首次进入时发起的 fetch URL 序列
    - `css-selectors.snapshot.json`：解析 `static/css/style.css` 后所有 `.pr-*` / `#pr-*` 之外选择器的有序列表与每条规则的哈希
    - `global-functions.snapshot.json`：`switchTab` / `fetchTeams` / `runClassic` / `runAI` / `loadLotteryMatches` / `loadSmartMatches` 等函数的 `toString().length` 与 `Function.prototype.length`（参数数）
    - `accuracy-api-contract.snapshot.json`：用合法参数请求 `/api/accuracy/summary` 与 `/api/accuracy/matches`，记录响应字段名集合 + 字段类型映射
  - 属性测试用例（基于 fast-check）：
    - **OtherTabsSwitchPreservation**：`fc.assert(fc.property(fc.array(fc.constantFrom('classic','ai','smart','wc'), {minLength: 1, maxLength: 10}), seq => 按 seq 切换 → 每次切换后 #panel-{tab} 的 outerHTML 哈希等于 baseline ))`
    - **OtherApiPreservation**：对非 accuracy 端点（`/api/teams`、`/api/leagues`、`/api/predict`、`/api/ai/*`、`/api/smart/*`、`/api/auth/*`、`/api/credits/*`）随机生成合法参数 → 断言响应 shape 与 baseline 一致
    - **AccuracyApiContractPreservation**：对 `/api/accuracy/summary` 与 `/api/accuracy/matches` 用 fast-check 生成合法 query params → 断言响应字段名集合与字段类型与 `accuracy-api-contract.snapshot.json` 完全相等
    - **ExistingCssSelectorPreservation**：解析 fixed `static/css/style.css` → 提取所有非 `.pr-*` / `#pr-*` 选择器 → 断言其有序列表与每条规则哈希等于 `css-selectors.snapshot.json`
    - **GlobalFunctionsPreservation**：断言 `window.switchTab` / `window.fetchTeams` / `window.runClassic` / `window.runAI` / `window.loadLotteryMatches` / `window.loadSmartMatches` 的存在性、参数数（`Function.prototype.length`）等于 baseline
    - **NonRecordTabDomNoMutation**：MutationObserver 监听 `#panel-record` → 在 `switchTab` 切到非 record tab 序列下断言 mutation 计数为 0
  - **EXPECTED OUTCOME on UNFIXED code**: 所有 case PASS（确认 baseline 准确）
  - **EXPECTED OUTCOME after fix**: 所有 case 仍 PASS（零回归）
  - Mark task complete when：基线已采集、属性测试已编写、在 unfixed 代码上跑过且全部通过
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10; Properties: P6_

- [x] 3. Fix for "预测战绩" tab 缺失对照视角与空数据退化

  - [x] 3.1 重写 `#panel-record` HTML 子树（保留根元素）
    - **文件**：`templates/index.html`
    - **改动范围**：仅替换 `<div id="panel-record" class="tab-panel hidden">` 内部子树；保留根元素的 `id` 与 `class`
    - 删除现有所有 `record-*` / `rsc-*` 子元素（`#record-stats-grid`、`#rsc-total`、`#rsc-accuracy`、`#rsc-score`、`#rsc-goal-error`、`#record-league-bars`、`#record-trend-chart`、`#record-filter-league`、`#record-filter-result`、`#record-match-list`、`#record-pagination`、`#record-empty` 等）
    - 注入 design.md 「Changes Required」中给出的 `.pr-*` 命名空间骨架：
      - `<header class="pr-hero">`：图标 + 标题「预测战绩」+ 副标题「ML 模型预测 vs 真实比赛结果，逐场对照」
      - `<div id="pr-status" class="pr-status">`：默认含 `.pr-loading`（spinner + 「加载中…」）
      - `<section id="pr-content" class="pr-content" hidden>`：内含 `#pr-stats` / `#pr-leagues[hidden]` / `#pr-trend[hidden]` / `#pr-filter` / `#pr-list` / `#pr-pagination`
      - `#pr-filter` 内含 `<select id="pr-filter-league">`（option `value=""`「全部联赛」）与 `<select id="pr-filter-result">`（option `value=""`「全部结果」、`correct`「✅ 命中」、`wrong`「❌ 未命中」）
    - **关键约束**：移除所有 HTML 内联 `onchange="loadRecordMatches()"` 绑定（改由 JS `addEventListener` 在 `ensureRecordLoaded` 中绑定）
    - 验证：`grep_search` 确认 `#panel-record` 内不再含 `record-*` / `rsc-*` 选择器，且不含任何 `onchange=` 内联绑定
    - _Requirements: 2.5_

  - [x] 3.2 追加 `.pr-*` 命名空间 CSS 到样式文件末尾
    - **文件**：`static/css/style.css`
    - **改动范围**：仅 append，不修改/不删除任何已有规则
    - 新增样式段落（顶部加分隔注释 `/* ============== Prediction Record (.pr-*) ============== */`）：
      - `.pr-hero` / `.pr-hero-icon` / `.pr-hero-title` / `.pr-hero-subtitle`
      - `.pr-status` / `.pr-loading` / `.pr-error` / `.pr-empty`
      - `.pr-content` / `.pr-stats` / `.pr-stat-card` / `.pr-stat-label` / `.pr-stat-value`
      - `.pr-leagues` / `.pr-league-row` / `.pr-league-bar`
      - `.pr-trend` / `.pr-trend-bar`
      - `.pr-filter` / `.pr-filter select`
      - `.pr-list` / `.pr-match-card` / `.pr-match-head` / `.pr-league` / `.pr-teams` / `.pr-time`
      - `.pr-cmp` / `.pr-cmp-row` / `.pr-cmp-label` / `.pr-cmp-pred` / `.pr-cmp-actual`
      - `.pr-hit-mark` / `.pr-hit-ok` / `.pr-hit-no` / `.pr-hit-diff`
      - `.pr-match-foot`
      - `.pr-pagination` / `.pr-pagination-btn` / `.pr-pagination-page`
    - 关键规则：
      - `.pr-cmp-row { display: grid; grid-template-columns: 80px 1fr auto 1fr; align-items: center; gap: 12px; }`
      - `.pr-hit-ok { background: var(--success-color); color: #fff; }`
      - `.pr-hit-no { background: var(--danger-color); color: #fff; }`
      - `.pr-hit-diff { background: var(--bg-secondary); color: var(--text-light); }`
      - 全部 color/spacing/shadow 使用项目既有 CSS variables（`--card-bg` / `--shadow-light` / `--primary-color` / `--success-color` / `--danger-color` / `--text-primary` / `--text-secondary` / `--text-light` / `--border-color` / `--gradient-primary`）
    - 响应式：`@media (max-width: 640px) { .pr-cmp-row { grid-template-columns: 1fr; } }`
    - **零污染约束**：所有选择器必须以 `.pr-` 或 `#pr-` 开头；不写 `.record-*` / `.rsc-*` 选择器；不写 `body .xxx` / `.tab-panel .xxx` 等可能影响其他 tab 的全局选择器
    - 验证：运行 task 2 中的 `ExistingCssSelectorPreservation` 属性测试，确认非 `.pr-*` / `#pr-*` 选择器与基线一致
    - _Requirements: 2.10, 3.8_

  - [x] 3.3 实现 JS 状态变量 + `switchTab` 分支 + `ensureRecordLoaded`
    - **文件**：`static/script.js`
    - 在文件顶部 STATE 段（与现有 state 同位置）新增：
      ```js
      let recordState = {
        page: 1, perPage: 20, league: '', result: '',
        loading: false, loaded: false,
        summary: null, matchesPayload: null,
      };
      ```
    - 修改 `switchTab(tab)` 函数，在切换面板可见性的逻辑后追加：
      ```js
      if (tab === 'record') ensureRecordLoaded();
      ```
    - **关键约束**：现有 `if (tab === 'ai') loadLotteryMatches()` / `if (tab === 'smart') loadSmartMatches()` 等分支必须**完全保留**，不得修改
    - 实现 `ensureRecordLoaded()`：
      - 若 `recordState.loaded || recordState.loading` → return（防重复加载）
      - 一次性绑定 `#pr-filter-league` 与 `#pr-filter-result` 的 `change` 事件 → 调用 `loadRecordMatches()` 并把 `recordState.page` 重置为 1
      - 调用 `loadRecord()`
    - 验证：在 unfixed 基线快照下运行 task 2 的 `GlobalFunctionsPreservation`，确认 `window.switchTab` 的参数数与 `toString().length` 在已有分支保留情况下与基线兼容（参数数仍为 1）
    - _Requirements: 2.1, 3.9_

  - [x] 3.4 实现 `loadRecord` + `loadRecordSummary` + 全局 `loadRecordMatches`
    - **文件**：`static/script.js`
    - 实现 `async function loadRecord()`：
      - 标记 `recordState.loading = true`
      - 显示 `#pr-status` 为 `.pr-loading`，隐藏 `#pr-content`
      - `Promise.all([ loadRecordSummary(), loadRecordMatches({ silent: true }) ])`
      - 写入 `recordState.summary` 与 `recordState.matchesPayload` 后调用 `renderRecord(...)`
      - try/catch：异常调用 `renderRecordError(err.message)` 并 `console.error('[record]', err)`
      - finally：`recordState.loading = false; recordState.loaded = true`
    - 实现 `async function loadRecordSummary()`：
      - `fetch('/api/accuracy/summary')` → 校验 `res.ok` 与 `body.success === true`
      - 返回 `{ summary, league_stats, trend }`，任一字段为 null/undefined 时不补造，向上抛错让 `loadRecord` 走 error 分支
    - 实现 **全局** `async function loadRecordMatches(opts = {})`：
      - 函数声明完成后立即 `window.loadRecordMatches = loadRecordMatches;`（即便移除了 HTML 内联绑定，仍保证健壮性 + 通过 task 1 `FilterChangeRefSafe` 测试）
      - 读取 `recordState` 中 `page / perPage / league / result` 拼出 query
      - 关键约束：query 参数白名单为 `{ league, result, page, per_page }`，空字符串 league/result 不写入 URL（避免 noise）
      - `fetch('/api/accuracy/matches?...')` → 校验 `res.ok` 与 `body.success === true`
      - 写入 `recordState.matchesPayload`
      - 非 `opts.silent` 模式下调用 `renderRecordList(payload.matches)` + `renderRecordPagination(payload.total, payload.page, payload.per_page)`
      - try/catch + `renderRecordError`
    - 验证：task 2 `AccuracyApiContractPreservation` 属性测试覆盖前端 query 参数白名单
    - _Requirements: 2.1, 2.8, 2.9, 2.11, 2.12, 2.13, 3.5, 3.6_

  - [x] 3.5 实现 `renderRecord` 顶层路由 + 空状态 + 错误状态
    - **文件**：`static/script.js`
    - 实现 `function renderRecord(summary, matchesPayload)`：
      - 顶层路由（依据 design.md 「数据流」与 P2）：
        - `summary.total_predicted === 0` → 调用 `renderRecordEmpty()` 后 return（**绝不**渲染 stats / leagues / trend / filter / list / pagination）
        - 否则：隐藏 `#pr-status`、移除 `#pr-content` 的 `hidden` 属性 → 依次调用 `renderRecordStats(summary)`、`renderRecordLeagues(league_stats)`、`renderRecordTrend(trend)`、`renderRecordList(matchesPayload.matches)`、`renderRecordPagination(matchesPayload.total, matchesPayload.page, matchesPayload.per_page)`
    - 实现 `function renderRecordEmpty()`：
      - 把 `#pr-status` innerHTML 替换为 `<div class="pr-empty"><i class="fas fa-inbox"></i><p>暂无已结束的比赛数据，比赛结束后会自动同步并生成对比</p></div>`
      - 给 `#pr-content` 加 `hidden` 属性
    - 实现 `function renderRecordError(message)`：
      - `#pr-status` innerHTML 替换为 `<div class="pr-error"><i class="fas fa-exclamation-circle"></i><p>${escapeHtml(message)}</p></div>`
      - 给 `#pr-content` 加 `hidden` 属性
      - `console.error('[record]', message)`
    - 关键约束：所有插入 DOM 的字符串必须经 `escapeHtml`，避免 XSS（即使数据来自后端，也走防御性渲染）
    - 验证：task 1 `EnterTabEmpty` 与 `ApiError` case 此时应 PASS
    - _Requirements: 2.2, 2.11, 2.12_

  - [x] 3.6 实现 `renderRecordStats` / `renderRecordLeagues` / `renderRecordTrend`
    - **文件**：`static/script.js`
    - 实现 `function renderRecordStats(summary)`：
      - 渲染 4 张 `.pr-stat-card` 到 `#pr-stats`：
        - 已对比场次：`summary.total_predicted`
        - 胜平负命中率：`summary.accuracy + '%'`
        - 比分精确命中率：`(summary.total_predicted > 0 ? round(summary.score_hit / summary.total_predicted * 100, 1) : 0) + '%'`
        - 平均进球数偏差：`summary.avg_goal_error ?? '-'`
      - 任一字段缺失/null 时显示 `-`，不补造
    - 实现 `function renderRecordLeagues(leagueStats)`：
      - `leagueStats.length === 0` → 给 `#pr-leagues` 加 `hidden` 属性后 return
      - 否则移除 `hidden`，注入分联赛准确率展示（每联赛 1 行：联赛名 + `correct/total` + `accuracy%` + 进度条）
    - 实现 `function renderRecordTrend(trend)`：
      - `trend.length === 0` → 加 `hidden` 后 return
      - 否则注入近 14 天命中趋势（每日命中数 / 总数 / 准确率，可用 mini bar）
    - _Requirements: 2.3, 2.6, 2.7_

  - [x] 3.7 实现 `renderRecordMatchCard`（核心：三维度并排对比卡）
    - **文件**：`static/script.js`
    - 这是修复的**核心函数**，必须严格按 design.md 「`renderRecordMatchCard` 骨架」实现
    - 函数签名：`function renderRecordMatchCard(m) -> string`（纯函数，方便单元测试 + 属性测试）
    - 必须满足以下不变量（被 task 1 `HitMarkConsistency` 与 task 10 P1/P4 验证）：
      - **每张卡片**恰好包含 **3 个 `.pr-cmp-row`** 节点，`data-dim` 集合 = `{result, score, goals}`
      - 每个 `.pr-cmp-row` 内**结构性并排**包含 `.pr-cmp-label` + `.pr-cmp-pred` + `.pr-hit-mark` + `.pr-cmp-actual`
      - 命中徽章规则：
        - `data-dim="result"`：`m.result_correct === true` → `✅` + class `.pr-hit-ok`；`=== false` → `❌` + `.pr-hit-no`；`null/undefined` → `-`
        - `data-dim="score"`：`m.score_correct` 同上
        - `data-dim="goals"`：`m.goal_diff_error` 非 null → 文本 `差 N 球` + class `.pr-hit-diff`；null → `-`
      - 缺失字段（`predicted_result` / `actual_result` / `predicted_score` / `actual_score` / `predicted_home_goals` / `predicted_away_goals` / `actual_home_goals` / `actual_away_goals` / `league` / `home_team_cn` / `away_team_cn` / `match_time`）一律显示 `-`，**不伪造**
      - 总进球计算：`predTotal = (ph != null && pa != null) ? ph + pa : null`，`realTotal` 同理
      - 卡片根：`<article class="pr-match-card" data-fixture-id="${m.fixture_id}">`
      - footer 仅在 `m.ml_probs` 非 null 时渲染（`renderProbBar(m.ml_probs)` + `renderOdds(m.odds)`）
    - 所有插值字段经 `escapeHtml`
    - 单元测试（写在同一 task 的 sub-spec 里，`tests/frontend/render-card.unit.test.js`）覆盖：
      - `result_correct=true` / `=false` / `=null`
      - `score_correct=true` / `=false` / `=null`
      - `goal_diff_error=null` → 显示 `-`
      - `predicted_*` 任一缺失 → 显示 `-`
      - `ml_probs=null` → 不渲染 footer
    - _Requirements: 2.4, 2.13_

  - [x] 3.8 实现 `renderRecordList` / `renderRecordPagination` + 筛选/分页事件绑定
    - **文件**：`static/script.js`
    - 实现 `function renderRecordList(matches)`：
      - `console.assert(Array.isArray(matches), '[record] matches must be array')`
      - `#pr-list.innerHTML = matches.map(renderRecordMatchCard).join('')`
      - `matches.length === 0` 且当前有筛选 → 注入「当前筛选下无匹配比赛」轻量空态（不与 P2 的全局空态冲突，因为此时 `total_predicted > 0`）
    - 实现 `function renderRecordPagination(total, page, perPage)`：
      - `total <= perPage` → `#pr-pagination.innerHTML = ''` 且 return
      - 否则渲染上一页（disabled 当 `page === 1`）/ 页码（最多展示 7 个，省略中段用 `…`）/ 下一页
      - 每个页码 button 绑定 `click` → 设置 `recordState.page = N` → 调用 `loadRecordMatches()`
    - 筛选事件绑定（在 task 3.3 的 `ensureRecordLoaded` 已一次性绑定，这里只补充处理逻辑）：
      - `#pr-filter-league` change → `recordState.league = e.target.value; recordState.page = 1; loadRecordMatches()`
      - `#pr-filter-result` change → `recordState.result = e.target.value; recordState.page = 1; loadRecordMatches()`
    - 验证：task 1 `FilterChangeRefSafe` case 此时应 PASS（无 ReferenceError、新 fetch 带 league 参数）
    - _Requirements: 2.4, 2.5, 2.8, 2.9_

  - [x] 3.9 Verify bug condition exploration test now passes (Fix Checking)
    - **Property 1: Expected Behavior** - "预测 vs 真实" 对照视图正确呈现
    - **IMPORTANT**: Re-run the SAME tests from task 1 — do NOT write new tests
    - The tests from task 1 encode the expected behavior; when they pass, it confirms the expected behavior is satisfied
    - 运行命令：`npx vitest run tests/frontend/exploration.bug-condition.test.js`
    - **EXPECTED OUTCOME**: 全部 case PASS（confirms bug is fixed）
      - `EnterTabWithData` PASS：5 张卡片 × 3 `.pr-cmp-row` = 15 节点
      - `EnterTabEmpty` PASS：仅空状态可见
      - `FilterChangeRefSafe` PASS：`window.loadRecordMatches` is function，无 ReferenceError
      - `HitMarkConsistency` PASS：fast-check 1000+ runs 无反例
      - `ApiError` PASS：错误状态可见
    - 同时运行 task 3.7 的单元测试：`npx vitest run tests/frontend/render-card.unit.test.js`
    - 若任一 case 失败 → 回到 3.1–3.8 修正实现，**不要**修改测试以让它通过
    - _Requirements: Expected Behavior Properties from design (2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.11, 2.12, 2.13); Properties: P1, P2, P3, P4, P5_

  - [x] 3.10 Verify preservation property tests still pass (Preservation Checking)
    - **Property 2: Preservation** - 其他 tab / API 契约 / CSS / 全局函数零回归
    - **IMPORTANT**: Re-run the SAME tests from task 2 — do NOT write new tests
    - 运行命令：`npx vitest run tests/frontend/preservation.property.test.js`
    - **EXPECTED OUTCOME**: 全部 case PASS（confirms no regressions）
      - `OtherTabsSwitchPreservation` PASS：4 个非 record tab 的 outerHTML 哈希等于 baseline
      - `OtherApiPreservation` PASS：非 accuracy 端点响应 shape 与 baseline 一致
      - `AccuracyApiContractPreservation` PASS：accuracy 两个端点字段名/类型与 baseline 一致（前端无修改后端，必然成立）
      - `ExistingCssSelectorPreservation` PASS：非 `.pr-*` / `#pr-*` 选择器与基线字节级一致
      - `GlobalFunctionsPreservation` PASS：6 个既有全局函数的存在性与参数数等于 baseline
      - `NonRecordTabDomNoMutation` PASS：切换到非 record tab 时 `#panel-record` 子树 mutation 计数为 0
    - 若任一 case 失败 → 说明引入了回归，必须回到 3.1–3.8 修正（典型陷阱：CSS 选择器写成全局选择器污染了其他 tab；JS 修改了 `switchTab` 已有分支；HTML 删除了根元素的 `id` 或 `class`）
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10; Properties: P6_

- [x] 4. Checkpoint - Ensure all tests pass
  - 运行完整测试套件：`npx vitest run`
  - 手工 smoke test（建议浏览器实际打开）：
    - 用真实 DB 启动 Flask（`python app.py` 或 `make run`）
    - 浏览器进入首页 → 点击「预测战绩」tab → 观察行为是否符合 design.md 「卡片视觉草图」
    - DB 有数据时：4 张统计卡 + 联赛区（如果 `league_stats` 非空）+ 趋势区（如果 `trend` 非空）+ 筛选器 + 列表卡片（每张三段对比 + ✅/❌）+ 分页（如 total > per_page）
    - DB 无已结束比赛时：仅空状态文案
    - 改筛选下拉：无 console error，列表刷新带新 query
    - DevTools Network panel：每次进入 tab 仅 2 个 request（summary + matches?page=1）；改筛选只触发 matches；改页码只触发 matches
    - DevTools Console：无 `ReferenceError` 与 `Uncaught (in promise)`
  - 跨 tab 回归 smoke：
    - 切到 classic / ai / smart / wc 各 tab → 行为与未修复时一致（首次进入 ai 仍触发 `loadLotteryMatches`，首次进入 smart 仍触发 `loadSmartMatches`）
  - 视觉一致性 smoke：
    - `#panel-record` 的卡片圆角 / 阴影 / 字体与其他 tab 协调，无浏览器默认样式
  - **遇到问题 → 立即向用户提问**（不要静默 fallback 到 mock 数据或修改测试）
  - 全部通过后：将 task 1 / 2 / 3 / 4 标记为 `[x]`
  - _Requirements: 全部 (1.x + 2.x + 3.x); Properties: P1, P2, P3, P4, P5, P6_
