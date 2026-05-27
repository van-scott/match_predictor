/* ── MatchPredict Pro – Main Script ── */

// ── STATE ─────────────────────────────────────────────────────────────────
let currentTab = 'classic';
let teamsData = {};
let lotteryMatches = [];
let aiCart = [];
let recordState = {
  page: 1, perPage: 20, league: '', result: '',
  loading: false, loaded: false,
  summary: null, matchesPayload: null, evalPayload: null,
};

// ── INIT ──────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  fetchTeams();
  fetchSyncStatus();
});

// ── TAB SWITCHING ─────────────────────────────────────────────────────────
function switchTab(tab) {
  currentTab = tab;
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.add('hidden'));
  document.getElementById(`tab-${tab}`).classList.add('active');
  document.getElementById(`panel-${tab}`).classList.remove('hidden');

  if (tab === 'ai') {
    if (lotteryMatches.length === 0) loadLotteryMatches();
    initAIEnginePicker();
  }
  // 智能选场：首次进入时加载比赛
  if (tab === 'smart' && smartMatches.length === 0) {
    loadSmartMatches();
  }
  if (tab === 'record') ensureRecordLoaded();
}

// ── TEAMS ─────────────────────────────────────────────────────────────────
async function fetchTeams() {
  try {
    const res = await fetch('/api/teams');
    const data = await res.json();
    if (data.success) {
      teamsData = data.teams;
      teamZh = data.team_zh || {};
    }
  } catch (e) {
    console.error('无法获取球队数据', e);
  }
}

let teamZh = {};

function loadTeams() {
  const league = document.getElementById('c-league').value;
  const homeEl = document.getElementById('c-home');
  const awayEl = document.getElementById('c-away');
  const teams = teamsData[league] || [];

  const opts = teams.map(t => {
    const zh = teamZh[t] || t;
    return `<option value="${t}">${zh}</option>`;
  }).join('');
  homeEl.innerHTML = `<option value="">选择主队</option>${opts}`;
  awayEl.innerHTML = `<option value="">选择客队</option>${opts}`;
}

// ── CLASSIC MODE ──────────────────────────────────────────────────────────
async function runClassic() {
  const league = document.getElementById('c-league').value;
  const home = document.getElementById('c-home').value;
  const away = document.getElementById('c-away').value;
  const ho = parseFloat(document.getElementById('c-ho').value);
  const do_ = parseFloat(document.getElementById('c-do').value);
  const ao = parseFloat(document.getElementById('c-ao').value);

  if (!home || !away) { showToast('请选择主队和客队', 'error'); return; }
  if (!ho || !do_ || !ao) { showToast('请填写赔率', 'error'); return; }
  if (home === away) { showToast('主客队不能相同', 'error'); return; }

  const resultEl = document.getElementById('classic-result');
  const phEl = document.getElementById('classic-placeholder');
  const inner = document.getElementById('classic-result-inner');

  phEl.classList.add('hidden');
  resultEl.classList.remove('hidden');
  inner.innerHTML = `<div class="spinner-overlay"><i class="fas fa-brain fa-spin fa-2x" style="color:var(--c-blue)"></i><span>权重模型分析中...</span></div>`;

  try {
    const res = await fetch('/api/analyze/classic', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ matches: [{ home_team: home, away_team: away, league_code: league, odds: { hhad: { h: ho, d: do_, a: ao } } }] }),
    });
    const data = await res.json();

    if (res.status === 402 || data.error_code === 'INSUFFICIENT_CREDITS') {
      inner.innerHTML = '';
      resultEl.classList.add('hidden');
      phEl.classList.remove('hidden');
      showCreditsModal(data);
      return;
    }

    if (data.success && data.individual_predictions?.length > 0) {
      renderClassicResult(data.individual_predictions[0]);
      refreshCredits();
    } else {
      inner.innerHTML = `<div class="error-msg">❌ ${data.message || '分析失败'}</div>`;
    }
  } catch (e) {
    inner.innerHTML = `<div class="error-msg">❌ 请求失败，请检查登录状态</div>`;
  }
}

function renderClassicResult(p) {
  const inner = document.getElementById('classic-result-inner');
  const probs = p.probabilities || {};
  const ev = p.expected_values || {};
  const bet = p.best_bet || {};
  const scores = p.top_scores || [];

  const recClass = p.recommendation === '主胜' ? 'rec-home' : (p.recommendation === '平局' ? 'rec-draw' : 'rec-away');

  inner.innerHTML = `
    <div class="pred-match-header">
      <div class="pred-teams">
        <h3>${p.home_team} vs ${p.away_team}</h3>
        <small>${p.league || '通用模型'} · ${p.mode === 'statistical' ? '泊松权重分析' : '赔率推断'}</small>
      </div>
      <span class="rec-badge ${recClass}">推荐：${p.recommendation}</span>
    </div>

    <div class="prob-section">
      ${probBar('主胜', probs.home, 'var(--c-green)')}
      ${probBar('平局', probs.draw, 'var(--c-muted)')}
      ${probBar('客胜', probs.away, 'var(--c-blue)')}
    </div>

    <div class="detail-grid">
      <div class="detail-item">
        <div class="detail-label">最可能比分</div>
        <div class="detail-val accent">${p.score_prediction || '-'}</div>
      </div>
      <div class="detail-item">
        <div class="detail-label">半场结果</div>
        <div class="detail-val green">${p.halftime_prediction || '-'}（${p.halftime_score || '-'}）</div>
      </div>
      <div class="detail-item">
        <div class="detail-label">半全场组合</div>
        <div class="detail-val blue">${p.ht_ft_combo || '-'}</div>
      </div>
      <div class="detail-item">
        <div class="detail-label">最佳投注</div>
        <div class="detail-val ${bet.ev > 0 ? 'green ev-positive' : 'ev-negative'}">${bet.label || '-'} @${bet.odds || '-'} (EV: ${bet.ev > 0 ? '+' : ''}${bet.ev || 0})</div>
      </div>
    </div>

    ${scores.length > 0 ? `
    <div class="top-scores">
      <h4>⚽ TOP 5 比分预测</h4>
      ${scores.map((s, i) => `
        <div class="score-row">
          <span>#${i+1} <span class="score-val">${s.score}</span></span>
          <span style="color:var(--c-muted)">${s.prob}%</span>
        </div>`).join('')}
    </div>` : ''}

    <div style="margin-top:1.5rem;padding:1rem;background:rgba(255,255,255,0.02);border-radius:12px;border:1px solid var(--c-border);font-size:0.8rem;color:var(--c-muted)">
      ⚠️ 预测结果仅供参考，不构成投注建议。足球是圆的，请理性参考。
    </div>
  `;
}

function probBar(label, val, color) {
  const pct = Math.round((val || 0) * 100);
  return `
    <div class="prob-row">
      <div class="prob-label-row">
        <span>${label}</span>
        <span class="prob-pct" style="color:${color}">${pct}%</span>
      </div>
      <div class="prob-bar-bg">
        <div class="prob-bar-fill" style="width:${pct}%;background:${color}"></div>
      </div>
    </div>`;
}

// ── LOTTERY MATCHES ───────────────────────────────────────────────────────
async function loadLotteryMatches() {
  const listEl = document.getElementById('lottery-list');
  listEl.innerHTML = `<div class="loading-msg"><i class="fas fa-spinner fa-spin"></i> 正在加载赛事数据...</div>`;

  try {
    const res = await fetch('/api/lottery/matches?days=7');
    const data = await res.json();
    if (data.success && data.matches?.length > 0) {
      lotteryMatches = data.matches;
      renderLotteryList();
    } else {
      listEl.innerHTML = `
        <div class="error-msg">
          <p>ℹ️ 暂无赛事数据</p>
          <p style="font-size:.8rem;color:var(--c-muted);margin-top:.5rem">赛事数据每 10 分钟自动同步，包含五大联赛、解放者杯、巴甲等全年赛事</p>
        </div>`;
    }
  } catch (e) {
    listEl.innerHTML = `<div class="error-msg">❌ 获取失败，请检查网络</div>`;
  }
}

function renderLotteryList() {
  const listEl = document.getElementById('lottery-list');
  listEl.innerHTML = '';

  // 展示未开赛的比赛（不再强制要求有赔率）
  const now = Date.now();
  const upcoming = lotteryMatches.filter(m => {
    const t = m.match_time || m.match_date;
    if (t) {
      const ts = new Date(t.replace(' ', 'T')).getTime();
      if (!isNaN(ts) && ts < now) return false;
    }
    return true;
  });

  if (upcoming.length === 0) {
    listEl.innerHTML = `<div class="error-msg"><p>ℹ️ 暂无未开赛的比赛</p><p style="font-size:.8rem;color:var(--c-muted);margin-top:.5rem">赛事数据每日自动同步，请稍后再试</p></div>`;
    return;
  }

  // 按日期分组
  const weekDays = ['周日','周一','周二','周三','周四','周五','周六'];
  const todayKey = new Date().toISOString().slice(0, 10);
  const tomorrow = new Date(); tomorrow.setDate(tomorrow.getDate() + 1);
  const tomorrowKey = tomorrow.toISOString().slice(0, 10);

  const groups = {};
  upcoming.forEach(m => {
    const t = m.match_time || m.match_date;
    const d = t ? new Date(t.replace(' ', 'T')) : null;
    const key = d ? d.toISOString().slice(0, 10) : 'unknown';
    if (!groups[key]) groups[key] = [];
    groups[key].push(m);
  });

  const sortedKeys = Object.keys(groups).sort();
  const firstKey = sortedKeys.length > 0 ? sortedKeys[0] : '';

  sortedKeys.forEach(key => {
    let label;
    if (key === todayKey) label = `今天（${weekDays[new Date().getDay()]}）`;
    else if (key === tomorrowKey) label = `明天（${weekDays[tomorrow.getDay()]}）`;
    else {
      const d = new Date(key + 'T00:00:00');
      label = `${d.getMonth()+1}月${d.getDate()}日（${weekDays[d.getDay()]}）`;
    }
    const isExpanded = key === firstKey;
    const section = document.createElement('div');
    section.className = 'pr-day-group';
    section.innerHTML = `
      <div class="pr-day-header ${isExpanded ? 'expanded' : ''}" onclick="this.classList.toggle('expanded'); this.nextElementSibling.classList.toggle('hidden')">
        <span class="pr-day-label">${label}</span>
        <span class="pr-day-count">${groups[key].length} 场</span>
        <i class="fas fa-chevron-down pr-day-arrow"></i>
      </div>
      <div class="pr-day-body ${isExpanded ? '' : 'hidden'}">
      </div>`;
    const body = section.querySelector('.pr-day-body');
    groups[key].forEach(m => {
      const inCart = aiCart.some(c => c.match_id === m.match_id);
      const odds = m.odds?.hhad || {};
      const mlProbs = m.ml_probs || {};
      const card = document.createElement('div');
      card.className = `lotto-card ${inCart ? 'selected' : ''}`;
      card.id = `lotto-${m.match_id}`;
      card.onclick = () => toggleAiCart(m);
      card.innerHTML = `
        <div class="lotto-top">
          <span>${m.league_name}</span>
          <span>${formatTime(m.match_time)}</span>
        </div>
        <div class="lotto-teams">
          <span>${m.home_team_cn || m.home_team}</span>
          <span style="font-size:0.7rem;color:var(--c-muted)">VS</span>
          <span>${m.away_team_cn || m.away_team}</span>
        </div>
        <div class="lotto-odds">
          <div class="lotto-odd-box"><span class="lotto-odd-label">主胜</span><span class="lotto-odd-val">${odds.h || (mlProbs.home ? (mlProbs.home * 100).toFixed(0) + '%' : '-')}</span></div>
          <div class="lotto-odd-box"><span class="lotto-odd-label">平局</span><span class="lotto-odd-val">${odds.d || (mlProbs.draw ? (mlProbs.draw * 100).toFixed(0) + '%' : '-')}</span></div>
          <div class="lotto-odd-box"><span class="lotto-odd-label">客胜</span><span class="lotto-odd-val">${odds.a || (mlProbs.away ? (mlProbs.away * 100).toFixed(0) + '%' : '-')}</span></div>
        </div>`;
      body.appendChild(card);
    });
    listEl.appendChild(section);
  });
}

function formatTime(t) {
  if (!t) return '';
  try {
    // match_time 存的就是北京时间（无时区标记），直接当本地时间解析
    const s = String(t).replace(' ', 'T');
    // 去掉可能的 Z 或 +00:00 后缀，当作本地时间
    const clean = s.replace(/Z$/, '').replace(/[+-]\d{2}:\d{2}$/, '');
    const d = new Date(clean);
    if (isNaN(d.getTime())) return t;
    return `${d.getMonth()+1}/${d.getDate()} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
  } catch { return t; }
}

// ── AUTO-SYNC STATUS ─────────────────────────────────────────────────────────
async function fetchSyncStatus() {
  try {
    const res = await fetch('/api/sync/status');
    const data = await res.json();
    const el = document.getElementById('sync-status-text');
    if (!el) return;
    if (data.last_sync_time) {
      el.textContent = `上次同步: ${data.last_sync_time}`;
    } else {
      el.textContent = '每日 6、8、14、18、22时自动更新';
    }
  } catch { /* silent */ }
}

// ── AI CART ───────────────────────────────────────────────────────────────
function toggleAiCart(match) {
  const idx = aiCart.findIndex(c => c.match_id === match.match_id);
  const cardEl = document.getElementById(`lotto-${match.match_id}`);
  if (idx > -1) {
    aiCart.splice(idx, 1);
    if (cardEl) cardEl.classList.remove('selected');
  } else {
    if (aiCart.length >= 10) { showToast('最多选择10场比赛', 'error'); return; }
    aiCart.push(match);
    if (cardEl) cardEl.classList.add('selected');
  }
  updateAiCartUI();
}

function updateAiCartUI() {
  const cartEl = document.getElementById('ai-cart');
  const costEl = document.getElementById('ai-cost');
  const btnEl = document.getElementById('ai-run-btn');

  const cost = aiCart.length * 3;
  costEl.textContent = cost;

  if (aiCart.length === 0) {
    cartEl.innerHTML = '<div class="empty-msg">请从左侧选择比赛</div>';
    btnEl.classList.add('disabled');
    return;
  }

  btnEl.classList.remove('disabled');
  cartEl.innerHTML = aiCart.map(m => `
    <div class="cart-item">
      <div class="cart-item-info">
        <b>${m.home_team_cn || m.home_team} vs ${m.away_team_cn || m.away_team}</b>
        <small>${m.league_name}</small>
      </div>
      <button class="cart-remove" onclick="removeFromCart('${m.match_id}')">✕</button>
    </div>`).join('');
}

function removeFromCart(id) {
  aiCart = aiCart.filter(c => c.match_id !== id);
  const cardEl = document.getElementById(`lotto-${id}`);
  if (cardEl) cardEl.classList.remove('selected');
  updateAiCartUI();
}

// ── AI ENGINE PICKER ──────────────────────────────────────────────────────
const AI_PRESET_MODELS = {
  kiro_cli:        ['auto', 'claude-sonnet-4.5', 'claude-sonnet-4', 'claude-haiku-4.5', 'deepseek-3.2', 'minimax-m2.5', 'minimax-m2.1', 'glm-5', 'qwen3-coder-next'],
  antigravity_cli: ['claude-sonnet-4-5', 'claude-opus-4', 'claude-haiku-3-5'],
  cursor_cli:      ['claude-sonnet-4-5', 'gpt-4o', 'gpt-4o-mini', 'gemini-2.0-flash-exp'],
  api_key:         [],
};

const AI_ENGINE_LABELS = {
  api_key:         '自定义 API',
  kiro_cli:        'Kiro CLI',
  antigravity_cli: 'Antigravity CLI',
  cursor_cli:      'Cursor CLI',
};

let _aiPickerConfig = null;  // 缓存用户配置

async function initAIEnginePicker() {
  const pickerEl = document.getElementById('ai-engine-picker');
  if (!pickerEl) return;

  // 已初始化过则只展示
  if (_aiPickerConfig) { pickerEl.style.display = 'block'; return; }

  try {
    const res = await fetch('/api/user/ai-config', { credentials: 'same-origin' });
    if (!res.ok) return;
    const data = await res.json();
    if (!data.success) return;

    _aiPickerConfig = data.config || {};
    const savedEngine = _aiPickerConfig.ai_engine_type || 'api_key';
    const savedModel  = _aiPickerConfig.ai_model || '';

    // 填充引擎下拉
    const engineSel = document.getElementById('ai-engine-sel');
    engineSel.innerHTML = Object.entries(AI_ENGINE_LABELS)
      .map(([v, l]) => `<option value="${v}"${v === savedEngine ? ' selected' : ''}>${l}</option>`)
      .join('');

    // 填充模型下拉
    _fillModelSel(savedEngine, savedModel);
    pickerEl.style.display = 'block';
  } catch (_) { /* 未登录或网络错误时不显示 */ }
}

function _fillModelSel(engineType, currentModel) {
  const modelSel = document.getElementById('ai-model-sel');
  if (!modelSel) return;
  const presets = AI_PRESET_MODELS[engineType] || [];

  let opts = '<option value="">(使用账户默认模型)</option>';
  presets.forEach(m => {
    opts += `<option value="${m}"${m === currentModel ? ' selected' : ''}>${m}</option>`;
  });
  if (currentModel && !presets.includes(currentModel)) {
    opts += `<option value="${currentModel}" selected>${currentModel}</option>`;
  }
  modelSel.innerHTML = opts;
}

function onAIEngineChange() {
  const engineSel = document.getElementById('ai-engine-sel');
  const savedModel = (_aiPickerConfig && _aiPickerConfig.ai_model) || '';
  _fillModelSel(engineSel.value, savedModel);
}

function getAIPickerValues() {
  const engineSel = document.getElementById('ai-engine-sel');
  const modelSel  = document.getElementById('ai-model-sel');
  return {
    engine: engineSel ? engineSel.value : null,
    model:  modelSel  ? modelSel.value  : null,
  };
}

// ── AI PREDICT ────────────────────────────────────────────────────────────
async function runAI() {
  if (aiCart.length === 0) return;

  const resultsEl = document.getElementById('ai-results');
  resultsEl.classList.remove('hidden');
  resultsEl.scrollIntoView({ behavior: 'smooth' });

  let elapsed = 0;
  resultsEl.innerHTML = `
    <div class="spinner-overlay">
      <i class="fas fa-robot fa-spin fa-3x" style="color:var(--c-gold)"></i>
      <span>AI大模型深度分析中，请稍候... (${aiCart.length} 场)</span>
      <small id="ai-elapsed" style="color:var(--c-muted)"></small>
    </div>`;

  const elapsedTimer = setInterval(() => {
    elapsed++;
    const el = document.getElementById('ai-elapsed');
    if (el) el.textContent = `已等待 ${elapsed}s，AI 正在深度分析...`;
  }, 1000);

  const { engine, model } = getAIPickerValues();

  try {
    // 一次请求，后端完成所有 AI 调用
    const res = await fetch('/api/ai/predict', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ matches: aiCart, override_engine: engine || undefined, override_model: model || undefined }),
    });
    const data = await res.json();

    clearInterval(elapsedTimer);

    if (res.status === 402 || data.error_code === 'INSUFFICIENT_CREDITS') {
      resultsEl.classList.add('hidden');
      showCreditsModal(data);
      return;
    }
    if (!data.success) {
      resultsEl.innerHTML = `<div class="error-msg">❌ ${data.message || '请求失败'}</div>`;
      return;
    }

    refreshCredits();

    // 展示结果
    const predictions = data.predictions || [];
    renderAIResults(predictions);

    // 后台保存到数据库
    if (predictions.length > 0) {
      fetch('/api/ai/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ results: predictions }),
      }).catch(() => {});
    }

  } catch (e) {
    clearInterval(elapsedTimer);
    resultsEl.innerHTML = `<div class="error-msg">❌ 请求失败，请检查登录状态和网络</div>`;
  }
}

function renderAIResults(predictions) {
  const resultsEl = document.getElementById('ai-results');
  resultsEl.innerHTML = `<h2 style="margin-bottom:1.5rem;font-size:1.2rem;"><i class="fas fa-file-invoice" style="color:var(--c-gold)"></i> AI 大模型深度研报</h2>`;

  predictions.forEach(p => {
    const card = document.createElement('div');
    card.className = 'ai-result-card';
    
    // 只有明确的错误消息才标红（短文本 + 包含失败关键字），正常分析内容可能也含 ⚠️ 不能误判
    const analysisText = p.ai_analysis || '';
    const isError = !analysisText ||
      (analysisText.length < 80 && (analysisText.includes('失败') || analysisText.includes('超时') || analysisText.includes('不可用') || analysisText.includes('错误')));
    if (isError) {
      card.style.border = '1px solid rgba(248, 113, 113, 0.2)';
      card.style.background = 'rgba(248, 113, 113, 0.03)';
    }

    const ho = p.home_odds || p.odds?.home || '-';
    const dro = p.draw_odds || p.odds?.draw || '-';
    const ao = p.away_odds || p.odds?.away || '-';
    // Convert markdown bold **text** to <strong>
    const formattedAnalysis = (p.ai_analysis || '').replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');

    card.innerHTML = `
      <div class="ai-result-header">
        <div>
          <h3>${p.home_team_cn || p.home_team} vs ${p.away_team_cn || p.away_team}</h3>
          <small style="color:var(--c-muted)">${p.league_name}</small>
        </div>
        <span class="ai-badge" style="${isError ? 'background:rgba(248,113,113,0.1);color:#f87171;' : ''}">🤖 ${isError ? 'AI 错误' : 'AI'}</span>
      </div>
      <div class="ai-odds-strip">
        <div class="ai-odd-item"><span class="ai-odd-label">主胜赔率</span><span class="ai-odd-val">${ho}</span></div>
        <div class="ai-odd-item"><span class="ai-odd-label">平局赔率</span><span class="ai-odd-val">${dro}</span></div>
        <div class="ai-odd-item"><span class="ai-odd-label">客胜赔率</span><span class="ai-odd-val">${ao}</span></div>
      </div>
      <div class="ai-analysis-text">${formattedAnalysis || '<span style="color:var(--muted)">⚠️ AI未返回分析内容</span>'}</div>
      <div style="margin-top:1.25rem;padding:0.75rem 1rem;background:rgba(255,255,255,0.02);border-radius:10px;font-size:0.75rem;color:var(--c-muted)">
        ⚠️ 以上内容由AI大模型生成，仅供参考，不构成投注建议。
      </div>`;
    resultsEl.appendChild(card);
  });
}

// ── AUTH ──────────────────────────────────────────────────────────────────
function openModal(type) {
  document.getElementById('modal-overlay').classList.remove('hidden');
  document.getElementById('modal-login').classList.add('hidden');
  document.getElementById('modal-register').classList.add('hidden');
  document.getElementById(`modal-${type}`).classList.remove('hidden');
}
function closeModal() {
  document.getElementById('modal-overlay').classList.add('hidden');
  document.getElementById('modal-login').classList.add('hidden');
  document.getElementById('modal-register').classList.add('hidden');
}

async function doLogin(e) {
  e.preventDefault();
  const username = document.getElementById('l-user').value;
  const password = document.getElementById('l-pass').value;
  try {
    const res = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
      credentials: 'same-origin'
    });
    const data = await res.json();
    if (data.success) {
      showToast('登录成功！', 'success');
      closeModal();
      // Update nav UI immediately without reload
      updateNavUser(data.user);
    } else {
      showToast(data.message || '登录失败', 'error');
    }
  } catch { showToast('网络错误', 'error'); }
}

function updateNavUser(user) {
  const navRight = document.getElementById('nav-right');
  if (!navRight || !user) return;
  const checked = user.already_checked || false;
  navRight.innerHTML = `
    <div class="user-pill">
      <span class="credits-badge">
        <i class="fas fa-coins"></i>
        <span class="credit-num" id="credits-display">${user.credits ?? 0}</span>
        <span class="credit-label">积分</span>
      </span>
      <button class="btn-checkin" id="checkin-btn" onclick="doCheckin()" ${checked ? 'disabled style="opacity:0.5; pointer-events:none;"' : ''}>
        <i class="fas fa-calendar-check"></i>
        <span>${checked ? '已签到' : '签到'}</span>
      </button>
      <div class="user-actions">
        <a href="/profile" class="btn-user-link">
          <i class="fas fa-user-circle"></i>
          <span>用户中心</span>
        </a>
        <button class="btn-logout" onclick="doLogout()">退出</button>
      </div>
    </div>`;
}

async function doRegister(e) {
  e.preventDefault();
  const username = document.getElementById('r-user').value;
  const email = document.getElementById('r-email').value;
  const password = document.getElementById('r-pass').value;
  try {
    const res = await fetch('/api/register', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, email, password }) });
    const data = await res.json();
    if (data.success) { showToast('注册成功，请登录', 'success'); openModal('login'); }
    else { showToast(data.message || '注册失败', 'error'); }
  } catch { showToast('网络错误', 'error'); }
}

async function doLogout() {
  await fetch('/api/logout', { method: 'POST', credentials: 'same-origin' });
  location.reload();
}

async function doCheckin() {
  const btn = document.getElementById('checkin-btn');
  if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>'; }
  try {
    const res = await fetch('/api/user/checkin', { method: 'POST', credentials: 'same-origin' });
    const data = await res.json();
    showToast(data.message || (data.already_checked ? '今日已签到，明天再来' : '签到成功'), data.success ? 'success' : 'error');
    if (data.credits !== undefined) {
      const el = document.getElementById('credits-display');
      if (el) el.textContent = data.credits;
    }
    if (data.success || data.already_checked) {
      if (btn) {
        btn.innerHTML = '<i class="fas fa-calendar-check"></i> <span>已签到</span>';
        btn.style.opacity = '0.5';
        btn.style.pointerEvents = 'none';
        btn.disabled = true;
      }
    }
  } catch {
    showToast('签到失败', 'error');
    if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-calendar-check"></i> <span>签到</span>'; }
  }
}

async function refreshCredits() {
  try {
    const res = await fetch('/api/user/credits', { credentials: 'same-origin' });
    const data = await res.json();
    if (data.success) {
      const el = document.getElementById('credits-display');
      if (el) el.textContent = data.credits;
    }
  } catch { /* silent */ }
}

// ── CREDITS MODAL ─────────────────────────────────────────────────────────
function showCreditsModal(data) {
  const cost = data.cost || 0;
  const cur  = data.current_credits ?? '—';
  const msg  = data.message || '积分不足';
  document.getElementById('credits-modal-msg').textContent = msg;
  document.getElementById('credits-modal-cost').textContent = cost;
  document.getElementById('credits-modal-cur').textContent  = cur;
  document.getElementById('credits-modal').classList.remove('hidden');
  document.getElementById('modal-overlay').classList.remove('hidden');
}
function closeCreditsModal() {
  document.getElementById('credits-modal').classList.add('hidden');
  document.getElementById('modal-overlay').classList.add('hidden');
}

// ── TOAST ─────────────────────────────────────────────────────────────────
let toastTimer = null;
function showToast(msg, type = 'success') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = `toast ${type}`;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.classList.add('hidden'); }, 3000);
}
// ── WORLD CUP MODE ────────────────────────────────────────────────────────
const WC_TEAMS = ["阿根廷", "法国", "巴西", "英格兰", "西班牙", "德国", "葡萄牙", "荷兰", "意大利", "比利时", "克罗地亚", "乌拉圭", "哥伦比亚", "美国", "墨西哥", "日本"];

function switchWCTab(tab) {
  document.querySelectorAll('.wc-tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.wc-panel').forEach(p => p.classList.add('hidden'));
  document.querySelector(`.wc-tab-btn[onclick="switchWCTab('${tab}')"]`).classList.add('active');
  document.getElementById(`wc-${tab}`).classList.remove('hidden');
}

function initWCTeams() {
  const home = document.getElementById('wc-home');
  const away = document.getElementById('wc-away');
  if (!home || !away) return;
  const opts = WC_TEAMS.map(t => `<option value="${t}">${t}</option>`).join('');
  home.innerHTML = `<option value="">选择主队</option>${opts}`;
  away.innerHTML = `<option value="">选择客队</option>${opts}`;
}

// Countdown to Jun 11, 2026
setInterval(() => {
  const target = new Date('2026-06-11T00:00:00Z').getTime();
  const now = new Date().getTime();
  const diff = target - now;

  if (diff < 0) return;

  const days = Math.floor(diff / (1000 * 60 * 60 * 24));
  const hours = Math.floor((diff % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));
  const mins = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
  const secs = Math.floor((diff % (1000 * 60)) / 1000);

  const elD = document.getElementById('wc-days');
  const elH = document.getElementById('wc-hours');
  const elM = document.getElementById('wc-mins');
  const elS = document.getElementById('wc-secs');
  
  if(elD) elD.textContent = String(days).padStart(3, '0');
  if(elH) elH.textContent = String(hours).padStart(2, '0');
  if(elM) elM.textContent = String(mins).padStart(2, '0');
  if(elS) elS.textContent = String(secs).padStart(2, '0');
}, 1000);

async function runWCSingle() {
  const home = document.getElementById('wc-home').value;
  const away = document.getElementById('wc-away').value;
  const ho = parseFloat(document.getElementById('wc-ho').value);
  const do_ = parseFloat(document.getElementById('wc-do').value);
  const ao = parseFloat(document.getElementById('wc-ao').value);

  if (!home || !away) { showToast('请选择主队和客队', 'error'); return; }
  if (!ho || !do_ || !ao) { showToast('请填写赔率', 'error'); return; }
  if (home === away) { showToast('主客队不能相同', 'error'); return; }

  const resultEl = document.getElementById('wc-single-result');
  resultEl.classList.remove('hidden');
  resultEl.innerHTML = `<div class="spinner-overlay"><i class="fas fa-magic fa-spin fa-2x" style="color:var(--c-blue)"></i><span>模型分析中...</span></div>`;

  try {
    const res = await fetch('/api/wc/predict', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ home_team: home, away_team: away, ho: ho, do: do_, ao: ao }),
    });
    const data = await res.json();

    if (res.status === 402 || data.error_code === 'INSUFFICIENT_CREDITS') {
      resultEl.classList.add('hidden');
      showCreditsModal(data);
      return;
    }

    if (data.success && data.prediction) {
      const p = data.prediction;
      const probHome = (p.probabilities.home * 100).toFixed(1);
      const probDraw = (p.probabilities.draw * 100).toFixed(1);
      const probAway = (p.probabilities.away * 100).toFixed(1);
      let rclass = '';
      if (p.recommendation.includes('主胜')) rclass = 'r-home';
      else if (p.recommendation.includes('客胜')) rclass = 'r-away';
      else rclass = 'r-draw';

      resultEl.innerHTML = `
        <div class="card-header-row" style="margin-bottom:1rem">
          <div><h3 style="margin-bottom:0.2rem">${p.home_team} vs ${p.away_team}</h3></div>
          <span class="prediction-badge ${rclass}">${p.recommendation}</span>
        </div>
        <div class="classic-prob-bar">
          <div class="prob-h" style="width:${probHome}%">主 ${probHome}%</div>
          <div class="prob-d" style="width:${probDraw}%">平 ${probDraw}%</div>
          <div class="prob-a" style="width:${probAway}%">客 ${probAway}%</div>
        </div>
        <div class="score-pred-box">
          <span>模型比分预测：</span>
          <strong>${p.home_score_pred} - ${p.away_score_pred}</strong>
        </div>
      `;
      refreshCredits();
    } else {
      resultEl.innerHTML = `<div class="error-msg">❌ ${data.message || '分析失败'}</div>`;
    }
  } catch (e) {
    resultEl.innerHTML = `<div class="error-msg">❌ 网络错误</div>`;
  }
}

async function runWCSimulate() {
  const resultEl = document.getElementById('wc-bracket');
  resultEl.classList.remove('hidden');
  resultEl.innerHTML = `<div class="spinner-overlay" style="min-height:300px;"><i class="fas fa-sitemap fa-spin fa-3x" style="color:var(--c-blue)"></i><span style="margin-top:1rem">推演从1/8决赛到冠军的赛程中...</span></div>`;

  try {
    const res = await fetch('/api/wc/simulate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin'
    });
    const data = await res.json();

    if (res.status === 402 || data.error_code === 'INSUFFICIENT_CREDITS') {
      resultEl.classList.add('hidden');
      showCreditsModal(data);
      return;
    }

    if (data.success && data.bracket) {
      renderWCBracket(data.bracket);
      refreshCredits();
    } else {
      resultEl.innerHTML = `<div class="error-msg">❌ ${data.message || '推演失败'}</div>`;
    }
  } catch (e) {
    resultEl.innerHTML = `<div class="error-msg">❌ 网络错误</div>`;
  }
}

function renderWCBracket(b) {
  const container = document.getElementById('wc-bracket');
  
  const mToHTML = (m) => `
    <div class="bracket-match">
      <div class="bracket-team ${m.winner === m.home ? 'winner' : ''}">
        <span>${m.home}</span><span class="bracket-score">${m.score.split('-')[0]}</span>
      </div>
      <div class="bracket-team ${m.winner === m.away ? 'winner' : ''}">
        <span>${m.away}</span><span class="bracket-score">${m.score.split('-')[1]}</span>
      </div>
    </div>
  `;

  let html = `<div class="bracket-container">`;
  
  // R16
  html += `<div class="bracket-round">`;
  html += `<div style="text-align:center;font-weight:700;color:var(--muted);font-size:0.8rem">1/8决赛</div>`;
  b.r16.forEach(m => html += mToHTML(m));
  html += `</div>`;

  // QF
  html += `<div class="bracket-round">`;
  html += `<div style="text-align:center;font-weight:700;color:var(--muted);font-size:0.8rem">1/4决赛</div>`;
  b.qf.forEach(m => html += mToHTML(m));
  html += `</div>`;

  // Final
  html += `<div class="bracket-round" style="justify-content:center">`;
  html += `<div style="text-align:center;font-weight:900;color:var(--orange);font-size:1.2rem;margin-bottom:1rem">冠军争夺战</div>`;
  b.final.forEach(m => html += mToHTML(m));
  html += `<div style="text-align:center;margin-top:2rem">
    <div style="font-size:0.8rem;color:var(--muted)">最终冠军</div>
    <div style="font-size:2rem;font-weight:900;color:var(--amber);text-shadow:var(--glow-o)">🏆 ${b.final[0].winner}</div>
  </div>`;
  html += `</div>`;

  html += `</div>`;
  container.innerHTML = html;
}

// Call initWCTeams when DOM loads
document.addEventListener('DOMContentLoaded', () => {
  initWCTeams();
});


// ═══════════════════════════════════════════════════════════════════════
// 智能选场 (Smart Pick) 模块
// ═══════════════════════════════════════════════════════════════════════

let smartMatches = [];         // 全部未开赛比赛缓存
let smartFilterLeague = 'all'; // 当前联赛筛选

// Tab 切换逻辑已合并到顶部 switchTab 函数中

// ── 加载未开赛比赛 ────────────────────────────────────────────────────
async function loadSmartMatches() {
  const listEl = document.getElementById('smart-match-list');
  const countEl = document.getElementById('smart-match-count');
  if (!listEl) return;

  listEl.innerHTML = `<div class="spinner-overlay"><i class="fas fa-spinner fa-spin fa-2x"></i><span>正在加载比赛数据...</span></div>`;

  try {
    const res = await fetch('/api/upcoming-matches?days=14&per_page=50');
    const data = await res.json();
    if (data.success && data.matches?.length > 0) {
      smartMatches = data.matches;
      if (countEl) countEl.textContent = `共 ${data.total} 场未开赛`;
      renderSmartMatches();
    } else {
      listEl.innerHTML = `<div class="error-msg"><i class="fas fa-info-circle"></i> 暂无未开赛比赛数据，请稍后刷新</div>`;
    }
  } catch (e) {
    listEl.innerHTML = `<div class="error-msg"><i class="fas fa-exclamation-triangle"></i> 加载失败，请检查网络连接</div>`;
  }
}

// ── 联赛筛选 ───────────────────────────────────────────────────────────
function filterSmartLeague(league, btn) {
  smartFilterLeague = league;
  document.querySelectorAll('.smart-filter-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  renderSmartMatches();
}

// ── 渲染比赛卡片（按天分组，可折叠） ────────────────────────────────────
function renderSmartMatches() {
  const listEl = document.getElementById('smart-match-list');
  const countEl = document.getElementById('smart-match-count');
  if (!listEl) return;

  let filtered = smartMatches;
  if (smartFilterLeague !== 'all') {
    filtered = smartMatches.filter(m => m.league === smartFilterLeague);
  }

  if (countEl) countEl.textContent = `${filtered.length} 场比赛`;

  if (filtered.length === 0) {
    listEl.innerHTML = `<div class="error-msg"><i class="fas fa-search"></i> 该联赛暂无未开赛比赛</div>`;
    return;
  }

  // 按日期分组
  const groups = groupMatchesByDate(filtered);
  // 默认展开最近一天的比赛（第一个分组）
  const firstDateKey = groups.length > 0 ? groups[0].dateKey : '';

  listEl.innerHTML = '';
  groups.forEach(({ dateKey, label, matches }) => {
    const isExpanded = dateKey === firstDateKey;
    const section = document.createElement('div');
    section.className = 'pr-day-group';
    section.innerHTML = `
      <div class="pr-day-header ${isExpanded ? 'expanded' : ''}" onclick="this.classList.toggle('expanded'); this.nextElementSibling.classList.toggle('hidden')">
        <span class="pr-day-label">${label}</span>
        <span class="pr-day-count">${matches.length} 场</span>
        <i class="fas fa-chevron-down pr-day-arrow"></i>
      </div>
      <div class="pr-day-body ${isExpanded ? '' : 'hidden'}"></div>
    `;
    const body = section.querySelector('.pr-day-body');
    matches.forEach(m => body.appendChild(buildSmartCard(m)));
    listEl.appendChild(section);
  });
}

function groupMatchesByDate(matches) {
  const weekDays = ['周日','周一','周二','周三','周四','周五','周六'];
  const now = new Date();
  const todayKey = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;
  const tomorrow = new Date(now); tomorrow.setDate(now.getDate() + 1);
  const tomorrowKey = `${tomorrow.getFullYear()}-${String(tomorrow.getMonth()+1).padStart(2,'0')}-${String(tomorrow.getDate()).padStart(2,'0')}`;

  const map = {};
  matches.forEach(m => {
    let t = null;
    if (m.match_time) {
      const s = String(m.match_time).replace(' ', 'T').replace(/Z$/, '').replace(/[+-]\d{2}:\d{2}$/, '');
      t = new Date(s);
    }
    const key = t && !isNaN(t.getTime()) ? `${t.getFullYear()}-${String(t.getMonth()+1).padStart(2,'0')}-${String(t.getDate()).padStart(2,'0')}` : 'unknown';
    if (!map[key]) map[key] = [];
    map[key].push(m);
  });

  return Object.keys(map).sort().map(key => {
    let label;
    if (key === todayKey) label = `今天（${weekDays[now.getDay()]}）`;
    else if (key === tomorrowKey) label = `明天（${weekDays[tomorrow.getDay()]}）`;
    else if (key === 'unknown') label = '日期未知';
    else {
      const parts = key.split('-');
      const d = new Date(parseInt(parts[0]), parseInt(parts[1])-1, parseInt(parts[2]));
      label = `${d.getMonth()+1}月${d.getDate()}日（${weekDays[d.getDay()]}）`;
    }
    return { dateKey: key, label, matches: map[key] };
  });
}

// ── 构建单张比赛卡片 ───────────────────────────────────────────────────
function buildSmartCard(m) {
  const card = document.createElement('div');
  card.className = 'smart-card';
  card.id = `smart-card-${m.fixture_id}`;

  const ml = m.ml_prediction;
  const odds = m.current_odds;
  const mov = m.odds_movement || {};

  // 时间处理
  const matchTime = m.match_time ? new Date(m.match_time) : null;
  const timeStr = matchTime ? formatSmartTime(matchTime) : '时间待定';
  const hoursUntil = matchTime ? (matchTime - Date.now()) / 3600000 : Infinity;
  const isUrgent = hoursUntil > 0 && hoursUntil < 24;
  const isPast = hoursUntil <= 0;

  // 联赛 emoji
  const leagueEmoji = {
    '英超': '🏴󠁧󠁢󠁥󠁮󠁧󠁿', '西甲': '🇪🇸', '意甲': '🇮🇹', '德甲': '🇩🇪', '法甲': '🇫🇷',
    '英冠': '🏴󠁧󠁢󠁥󠁮󠁧󠁿', '德乙': '🇩🇪', '荷甲': '🇳🇱', '葡超': '🇵🇹',
    '欧冠': '🏆', '欧联': '🥈', '欧协联': '🥈',
    '解放者杯': '🌎', '南美杯': '🌎', '巴甲': '🇧🇷', '巴乙': '🇧🇷',
    '美职联': '🇺🇸', '日职联': '🇯🇵', '中超': '🇨🇳',
  }[m.league] || '⚽';

  // source 来源标注（需先声明，在下面的 ml 判断块中使用）
  const mlSource = ml?.source === 'odds_fallback'
    ? '<span style="font-size:.6rem;color:var(--muted);margin-left:.3rem">（赔率推算）</span>'
    : '';

  // 概率块
  let probHtml = '';
  if (ml) {
    const ph = Math.round((ml.home_prob || 0) * 100);
    const pd = Math.round((ml.draw_prob || 0) * 100);
    const pa = Math.round((ml.away_prob || 0) * 100);
    const maxProb = Math.max(ph, pd, pa);
    probHtml = `
      <div class="smart-prob-section">
        <div class="smart-prob-label">
          <span>主胜 ${ph}%</span>
          <span style="color:var(--muted)">ML模型预测概率</span>
          <span>客胜 ${pa}%</span>
        </div>
        <div class="smart-prob-bar-wrap" style="grid-template-columns:${ph}fr ${pd}fr ${pa}fr">
          <div class="smart-prob-seg smart-prob-seg-home${ph===maxProb?' dominant':''}">主${ph}%</div>
          <div class="smart-prob-seg smart-prob-seg-draw${pd===maxProb?' dominant':''}">平${pd}%</div>
          <div class="smart-prob-seg smart-prob-seg-away${pa===maxProb?' dominant':''}">客${pa}%</div>
        </div>
      </div>`;

    // 推荐标签
    const rec = ml.recommendation || '';
    const recClass = rec.includes('强推') ? 'smart-rec-strong' :
                     rec.includes('偏向') ? 'smart-rec-normal' : 'smart-rec-caution';
    const recIcon = rec.includes('强推') ? '🔥' : rec.includes('偏向') ? '📊' : '⚖️';
    probHtml += `
      <div class="smart-rec-row">
        <span class="smart-rec-badge ${recClass}">${recIcon} ${rec || 'ML分析中'}</span>
        <span style="font-size:.68rem;color:var(--muted)">${ml?.source==='ml' ? '基于3500+场训练' : '赔率反推概率'}${mlSource||''}</span>
      </div>`;
  } else {
    // 无 ML 时显示一个更友好的占位（后端应已提供 odds_fallback）
    probHtml = `<div class="smart-no-ml"><i class="fas fa-info-circle"></i> 暂无预测数据（球队历史场次不足）</div>`;
  }

  // 赔率块
  let oddsHtml = '';
  if (odds && odds.home) {
    const homeChange = mov.home_change || 0;
    const drawChange = mov.draw_change || 0;
    const awayChange = mov.away_change || 0;
    const changeStr = (v) => {
      if (!v || Math.abs(v) < 0.01) return `<span class="smart-change-stable">—</span>`;
      return v < 0
        ? `<span class="smart-change-down">↓${Math.abs(v).toFixed(2)}</span>`
        : `<span class="smart-change-up">↑${v.toFixed(2)}</span>`;
    };
    oddsHtml = `
      <div class="smart-odds-row">
        <div class="smart-odd-box">
          <span class="smart-odd-label">主胜赔率</span>
          <div class="smart-odd-val">${odds.home?.toFixed(2) || '-'}</div>
          <div class="smart-odd-change">${changeStr(homeChange)}</div>
        </div>
        <div class="smart-odd-box">
          <span class="smart-odd-label">平局赔率</span>
          <div class="smart-odd-val">${odds.draw?.toFixed(2) || '-'}</div>
          <div class="smart-odd-change">${changeStr(drawChange)}</div>
        </div>
        <div class="smart-odd-box">
          <span class="smart-odd-label">客胜赔率</span>
          <div class="smart-odd-val">${odds.away?.toFixed(2) || '-'}</div>
          <div class="smart-odd-change">${changeStr(awayChange)}</div>
        </div>
      </div>`;
  }

  card.innerHTML = `
    <div class="smart-card-meta">
      <span class="smart-league-tag">${leagueEmoji} ${m.league}${m.matchday ? ' 第'+m.matchday+'轮' : ''}</span>
      <span class="smart-time-tag ${isUrgent ? 'smart-time-urgent' : ''}">
        <i class="fas fa-clock"></i> ${timeStr}
        ${isUrgent ? '<span style="margin-left:.3rem">即将开赛</span>' : ''}
      </span>
    </div>

    <div class="smart-matchup">
      <div class="smart-team smart-team-home">
        <div class="smart-team-name">${m.home_team_cn || m.home_team}</div>
        <div class="smart-team-form" style="font-size:.6rem;opacity:.6">${m.home_team_cn ? m.home_team : ''}</div>
        <div class="smart-team-form">主场</div>
      </div>
      <div class="smart-vs">${m.predicted_home_goals != null && m.predicted_away_goals != null ? `<div style="font-size:1.4rem;font-weight:900;color:var(--orange)">${m.predicted_home_goals} - ${m.predicted_away_goals}</div><div style="font-size:.6rem;color:var(--muted)">预测比分</div>` : 'VS'}</div>
      <div class="smart-team smart-team-away">
        <div class="smart-team-name">${m.away_team_cn || m.away_team}</div>
        <div class="smart-team-form" style="font-size:.6rem;opacity:.6">${m.away_team_cn ? m.away_team : ''}</div>
        <div class="smart-team-form">客场</div>
      </div>
    </div>

    ${probHtml}
    ${oddsHtml}

    <div class="smart-card-footer">
      <button class="smart-btn-ml" onclick="showSmartDetail('${m.fixture_id}', false)">
        <i class="fas fa-chart-bar"></i> 查看数据
      </button>
      <button class="smart-btn-ai" onclick="showSmartDetail('${m.fixture_id}', true)">
        <i class="fas fa-robot"></i> AI 深度分析 <span style="font-size:.65rem;opacity:.8">-2积分</span>
      </button>
    </div>
  `;

  return card;
}

// ── 时间格式化 ─────────────────────────────────────────────────────────
function formatSmartTime(d) {
  // d 已经是本地时间（数据库存的就是北京时间）
  const now = new Date();
  const isToday = d.toDateString() === now.toDateString();
  const tomorrow = new Date(now);
  tomorrow.setDate(now.getDate() + 1);
  const isTomorrow = d.toDateString() === tomorrow.toDateString();

  const hm = `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
  if (isToday) return `今天 ${hm}`;
  if (isTomorrow) return `明天 ${hm}`;
  return `${d.getMonth()+1}月${d.getDate()}日 ${hm}`;
}

// ── 智能选场：模型选择弹窗 ─────────────────────────────────────────────
function showSmartModelPicker(fixtureId) {
  // 构建当前引擎的模型列表
  const savedEngine = (_aiPickerConfig && _aiPickerConfig.ai_engine_type) || 'kiro_cli';
  const savedModel  = (_aiPickerConfig && _aiPickerConfig.ai_model) || '';
  const presets = AI_PRESET_MODELS[savedEngine] || [];

  let modelOpts = '<option value="">(使用账户默认模型)</option>';
  presets.forEach(m => {
    modelOpts += `<option value="${m}"${m === savedModel ? ' selected' : ''}>${m}</option>`;
  });
  if (savedModel && !presets.includes(savedModel)) {
    modelOpts += `<option value="${savedModel}" selected>${savedModel}</option>`;
  }

  let engineOpts = Object.entries(AI_ENGINE_LABELS)
    .map(([v, l]) => `<option value="${v}"${v === savedEngine ? ' selected' : ''}>${l}</option>`)
    .join('');

  const inner = document.getElementById('smart-detail-inner');
  inner.innerHTML = `
    <div style="padding:1.5rem; max-width:420px; margin:0 auto;">
      <h3 style="margin:0 0 1rem; font-size:1rem; display:flex; align-items:center; gap:0.5rem;">
        <i class="fas fa-sliders-h" style="color:var(--orange)"></i> 选择分析引擎 &amp; 模型
      </h3>
      <div style="margin-bottom:0.8rem;">
        <label style="font-size:0.8rem; color:var(--muted); display:block; margin-bottom:0.3rem;">引擎</label>
        <select id="smart-engine-pick"
          style="width:100%;padding:0.5rem 0.7rem;border-radius:8px;background:rgba(255,255,255,0.07);border:1px solid rgba(255,255,255,0.15);color:var(--cream);font-size:0.85rem;cursor:pointer;outline:none;"
          onchange="onSmartEngineChange()">
          ${engineOpts}
        </select>
      </div>
      <div style="margin-bottom:1.2rem;">
        <label style="font-size:0.8rem; color:var(--muted); display:block; margin-bottom:0.3rem;">模型</label>
        <select id="smart-model-pick"
          style="width:100%;padding:0.5rem 0.7rem;border-radius:8px;background:rgba(255,255,255,0.07);border:1px solid rgba(255,255,255,0.15);color:var(--cream);font-size:0.85rem;cursor:pointer;outline:none;">
          ${modelOpts}
        </select>
      </div>
      <div style="display:flex; gap:0.8rem;">
        <button onclick="closeSmartDetail()" style="flex:1; padding:0.6rem; border-radius:8px; background:rgba(255,255,255,0.07); border:1px solid rgba(255,255,255,0.1); color:var(--muted); cursor:pointer; font-size:0.85rem;">取消</button>
        <button onclick="confirmSmartAI('${fixtureId}')"
          style="flex:2; padding:0.6rem; border-radius:8px; background:linear-gradient(135deg,#f97316,#fb923c); border:none; color:#fff; cursor:pointer; font-size:0.85rem; font-weight:600;">
          <i class="fas fa-robot"></i> 开始 AI 分析
        </button>
      </div>
    </div>`;
}

function onSmartEngineChange() {
  const e = document.getElementById('smart-engine-pick');
  const m = document.getElementById('smart-model-pick');
  if (!e || !m) return;
  const presets = AI_PRESET_MODELS[e.value] || [];
  let opts = '<option value="">(使用账户默认模型)</option>';
  presets.forEach(p => { opts += `<option value="${p}">${p}</option>`; });
  m.innerHTML = opts;
}

let _smartAIRunning = false;
async function confirmSmartAI(fixtureId) {
  if (_smartAIRunning) return;  // 防止重复提交
  _smartAIRunning = true;
  try {
    const engineSel = document.getElementById('smart-engine-pick');
    const modelSel  = document.getElementById('smart-model-pick');
    const overrideEngine = engineSel ? engineSel.value : null;
    const overrideModel  = modelSel  ? modelSel.value  : null;
    await _doSmartAI(fixtureId, overrideEngine, overrideModel);
  } finally {
    _smartAIRunning = false;
  }
}

// ── 深度分析弹窗 ───────────────────────────────────────────────────────
async function showSmartDetail(fixtureId, withAI) {
  const overlay = document.getElementById('smart-detail-overlay');
  const modal   = document.getElementById('smart-detail-modal');
  const inner   = document.getElementById('smart-detail-inner');

  overlay.classList.remove('hidden');
  modal.classList.remove('hidden');

  // 先找本地缓存的 ML 数据
  const match = smartMatches.find(m => m.fixture_id === fixtureId);

  if (!withAI && match) {
    // 纯 ML 查看，无需 API 调用
    inner.innerHTML = buildDetailView(match, null);
    return;
  }

  // 确保引擎配置已加载，然后显示模型选择器
  if (!_aiPickerConfig) {
    try {
      const res = await fetch('/api/user/ai-config', { credentials: 'same-origin' });
      const d = await res.json();
      if (d.success) _aiPickerConfig = d.config || {};
    } catch (_) { _aiPickerConfig = {}; }
  }
  showSmartModelPicker(fixtureId);
}

async function _doSmartAI(fixtureId, overrideEngine, overrideModel) {
  const inner = document.getElementById('smart-detail-inner');

  // 显示 Loading
  inner.innerHTML = `
    <div class="smart-detail-loading">
      <i class="fas fa-robot fa-spin fa-2x" style="color:var(--orange)"></i>
      <p>AI 正在深度分析，通常需要 10-20 秒...</p>
      <small style="color:var(--muted)">正在调用 AI 大模型 + 历史数据库</small>
    </div>`;

  try {
    const res = await fetch('/api/smart-predict', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({
        fixture_id: fixtureId,
        with_ai: true,
        override_engine: overrideEngine || undefined,
        override_model:  overrideModel  || undefined,
      }),
    });
    const data = await res.json();

    if (res.status === 401) {
      closeSmartDetail();
      openModal('login');
      showToast('请先登录后再使用此功能', 'error');
      return;
    }
    if (res.status === 403) {
      inner.innerHTML = `
        <div class="smart-detail-loading">
          <i class="fas fa-coins fa-2x" style="color:var(--amber)"></i>
          <p>${data.message || '积分不足'}</p>
          <button class="btn-primary" onclick="closeSmartDetail(); doCheckin()" style="margin-top:1rem">
            <i class="fas fa-calendar-check"></i> 立即签到获取积分
          </button>
        </div>`;
      return;
    }
    if (!data.success) {
      inner.innerHTML = `<div class="error-msg">❌ ${data.message || '分析失败'}</div>`;
      return;
    }

    inner.innerHTML = buildDetailView(data, data.ai_analysis);
    refreshCredits();

  } catch (e) {
    inner.innerHTML = `<div class="error-msg">❌ 网络错误，请稍后重试</div>`;
  }
}

// ── 构建弹窗内容 ───────────────────────────────────────────────────────
function buildDetailView(data, aiText) {
  const ml  = data.ml_prediction || {};
  const mov = data.odds_movement || {};
  const odds = data.current_odds || {};

  const ph = Math.round((ml.home_prob || 0) * 100);
  const pd = Math.round((ml.draw_prob || 0) * 100);
  const pa = Math.round((ml.away_prob || 0) * 100);

  const rec = ml.recommendation || data.ai_recommendation || '';
  const recClass = rec.includes('强推') ? 'sdr-rec-strong' :
                   rec.includes('偏向') ? 'sdr-rec-normal' : 'sdr-rec-caution';

  const matchTime = data.match_time ? new Date(data.match_time) : null;

  // 赔率变动说明
  const signalMap = {
    'strong_down': '🔥 赔率大幅下降，市场资金大量涌入看好该方，值得关注',
    'down':        '📉 赔率略有下降，市场小幅看好',
    'stable':      '➡️ 赔率稳定，市场分歧均衡',
    'up':          '📈 赔率略有上升，部分资金流出',
    'strong_up':   '⚠️ 赔率大幅上升，市场资金撤离，慎重参考',
  };
  const signal = mov.signal ? (signalMap[mov.signal] || '') : '';

  // AI 文本格式化
  const formattedAI = aiText
    ? aiText
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        .replace(/###\s*/g, '')
    : null;

  return `
    <div class="sdr-header">
      <div class="sdr-matchup">${data.home_team} <span style="color:var(--orange);font-size:.9rem">vs</span> ${data.away_team}</div>
      <div class="sdr-league">${data.league || ''} ${matchTime ? '· ' + formatSmartTime(matchTime) : ''}</div>
    </div>

    ${ml.home_prob ? `
    <div class="sdr-prob-grid">
      <div class="sdr-prob-item sdr-prob-home">
        <div class="sdr-prob-label">主胜概率</div>
        <div class="sdr-prob-val">${ph}%</div>
        <div class="sdr-prob-sub">${data.home_team}</div>
      </div>
      <div class="sdr-prob-item sdr-prob-draw">
        <div class="sdr-prob-label">平局概率</div>
        <div class="sdr-prob-val">${pd}%</div>
        <div class="sdr-prob-sub">两队互相制约</div>
      </div>
      <div class="sdr-prob-item sdr-prob-away">
        <div class="sdr-prob-label">客胜概率</div>
        <div class="sdr-prob-val">${pa}%</div>
        <div class="sdr-prob-sub">${data.away_team}</div>
      </div>
    </div>
    ${rec ? `<div class="sdr-rec-banner ${recClass}"><i class="fas fa-crosshairs"></i> ${rec}</div>` : ''}
    ` : '<div class="smart-no-ml"><i class="fas fa-clock"></i> 暂无 ML 概率数据（比赛球队在我们数据库中尚无足够历史记录）</div>'}

    ${odds.home ? `
    <div style="margin-bottom:1rem">
      <div class="sdr-ai-title"><i class="fas fa-chart-line"></i> 当前赔率</div>
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:.5rem">
        <div style="text-align:center;padding:.6rem;background:rgba(255,255,255,.04);border-radius:10px;border:1px solid var(--border)">
          <div style="font-size:.62rem;color:var(--muted);margin-bottom:.2rem">主胜</div>
          <div style="font-size:1.1rem;font-weight:800;color:var(--amber)">${odds.home?.toFixed(2)}</div>
          ${mov.home_change ? `<div style="font-size:.6rem;color:${mov.home_change < 0 ? 'var(--green)' : 'var(--red)'}">开盘 ${mov.home_change < 0 ? '↓' : '↑'}${Math.abs(mov.home_change).toFixed(2)}</div>` : ''}
        </div>
        <div style="text-align:center;padding:.6rem;background:rgba(255,255,255,.04);border-radius:10px;border:1px solid var(--border)">
          <div style="font-size:.62rem;color:var(--muted);margin-bottom:.2rem">平局</div>
          <div style="font-size:1.1rem;font-weight:800;color:var(--amber)">${odds.draw?.toFixed(2)}</div>
        </div>
        <div style="text-align:center;padding:.6rem;background:rgba(255,255,255,.04);border-radius:10px;border:1px solid var(--border)">
          <div style="font-size:.62rem;color:var(--muted);margin-bottom:.2rem">客胜</div>
          <div style="font-size:1.1rem;font-weight:800;color:var(--amber)">${odds.away?.toFixed(2)}</div>
        </div>
      </div>
      ${signal ? `<div style="margin-top:.7rem;font-size:.75rem;color:var(--sub);padding:.5rem .8rem;background:rgba(255,255,255,.03);border-radius:8px">${signal}</div>` : ''}
    </div>
    ` : ''}

    ${formattedAI ? `
    <div class="sdr-ai-title"><i class="fas fa-robot"></i> AI 大模型深度研报</div>
    <div class="sdr-ai-text">${formattedAI}</div>
    <div class="sdr-credits-note">
      <i class="fas fa-coins" style="color:var(--amber)"></i>
      本次深度分析已扣除 2 积分。每日签到可补充积分。
    </div>
    ` : `
    <div style="margin-top:1rem;padding:1rem;background:rgba(232,146,74,.06);border:1px solid rgba(232,146,74,.15);border-radius:12px;font-size:.82rem;color:var(--sub)">
      <i class="fas fa-lightbulb" style="color:var(--orange)"></i>
      想要 AI 深度分析这场比赛？点击右下角<strong style="color:var(--text)">「AI 深度分析」</strong>按钮，
      Gemini 将结合历史数据、球队近期状态、赔率变动信号给出完整研报（消耗 2 积分）。
    </div>
    `}
  `;
}

function closeSmartDetail() {
  document.getElementById('smart-detail-overlay').classList.add('hidden');
  document.getElementById('smart-detail-modal').classList.add('hidden');
}

// ── 初始化 DOMContentLoaded 补充 ─────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initWCTeams();
  // 更新 nav credits 显示（若有）
  const navCredits = document.getElementById('credits-display') || document.getElementById('nav-credits');
  if (navCredits) {
    fetch('/api/user/credits', { credentials: 'same-origin' })
      .then(r => r.json())
      .then(d => { if (d.success) navCredits.textContent = d.credits; })
      .catch(() => {});
  }
});


// ═══════════════════════════════════════════════════════════════════════
// 预测战绩 (Prediction Record) 模块
// ═══════════════════════════════════════════════════════════════════════

// ── 预测战绩：加载与渲染 ──────────────────────────────────────────────────
function ensureRecordLoaded() {
  if (recordState.loaded || recordState.loading) return;
  const leagueFilter = document.getElementById('pr-filter-league');
  const resultFilter = document.getElementById('pr-filter-result');
  if (leagueFilter) {
    leagueFilter.addEventListener('change', () => {
      recordState.league = leagueFilter.value;
      recordState.page = 1;
      loadRecordMatches();
    });
  }
  if (resultFilter) {
    resultFilter.addEventListener('change', () => {
      recordState.result = resultFilter.value;
      recordState.page = 1;
      loadRecordMatches();
    });
  }
  loadRecord();
}

async function loadRecord() {
  recordState.loading = true;
  const statusEl = document.getElementById('pr-status');
  const contentEl = document.getElementById('pr-content');
  if (statusEl) statusEl.innerHTML = '<div class="pr-loading"><i class="fas fa-spinner fa-spin fa-2x"></i><span>加载中…</span></div>';
  if (contentEl) contentEl.hidden = true;

  try {
    const [summaryData, matchesData, evalData] = await Promise.all([
      loadRecordSummary(),
      loadRecordMatchesInternal(),
      loadRecordEval(),
    ]);
    recordState.summary = summaryData;
    recordState.matchesPayload = matchesData;
    recordState.evalPayload = evalData;
    renderRecord(summaryData, matchesData, evalData);
  } catch (err) {
    renderRecordError(err.message || '加载失败');
  } finally {
    recordState.loading = false;
    recordState.loaded = true;
  }
}

async function loadRecordSummary() {
  const res = await fetch('/api/accuracy/summary');
  if (!res.ok) throw new Error(`服务器错误 (${res.status})`);
  const data = await res.json();
  if (!data.success) throw new Error(data.message || '获取统计失败');
  return data;
}

async function loadRecordEval() {
  try {
    const res = await fetch('/api/accuracy/eval?days=30');
    if (!res.ok) return null;
    const data = await res.json();
    return data.success ? data : null;
  } catch (_) {
    return null;
  }
}

async function loadRecordMatchesInternal() {
  const params = new URLSearchParams();
  params.set('page', recordState.page);
  params.set('per_page', recordState.perPage);
  if (recordState.league) params.set('league', recordState.league);
  if (recordState.result) params.set('result', recordState.result);
  const res = await fetch(`/api/accuracy/matches?${params.toString()}`);
  if (!res.ok) throw new Error(`服务器错误 (${res.status})`);
  const data = await res.json();
  if (!data.success) throw new Error(data.message || '获取比赛列表失败');
  return data;
}

async function loadRecordMatches() {
  try {
    const data = await loadRecordMatchesInternal();
    recordState.matchesPayload = data;
    renderRecordList(data.matches);
    renderRecordPagination(data.total, data.page, data.per_page);
  } catch (err) {
    renderRecordError(err.message || '加载失败');
  }
}
window.loadRecordMatches = loadRecordMatches;

function escapeHtml(str) {
  if (!str) return '';
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function renderRecord(summaryData, matchesData, evalData) {
  const statusEl = document.getElementById('pr-status');
  const contentEl = document.getElementById('pr-content');
  const summary = summaryData.summary || {};

  if (!summary.total_predicted || summary.total_predicted === 0) {
    renderRecordEmpty();
    return;
  }

  if (statusEl) { statusEl.innerHTML = ''; statusEl.style.display = 'none'; }
  if (contentEl) contentEl.hidden = false;

  renderRecordStats(summary);
  renderRecordLeagues(summaryData.league_stats || []);
  renderRecordTrend(summaryData.trend || []);
  renderRecordEval(evalData);
  renderRecordList(matchesData.matches || []);
  renderRecordPagination(matchesData.total || 0, matchesData.page || 1, matchesData.per_page || 20);
}

function renderRecordEmpty() {
  const statusEl = document.getElementById('pr-status');
  const contentEl = document.getElementById('pr-content');
  if (statusEl) {
    statusEl.style.display = '';
    statusEl.innerHTML = '<div class="pr-empty"><i class="fas fa-inbox fa-3x"></i><p>暂无已结束的比赛数据，比赛结束后会自动同步并生成对比</p></div>';
  }
  if (contentEl) contentEl.hidden = true;
}

function renderRecordError(message) {
  const statusEl = document.getElementById('pr-status');
  const contentEl = document.getElementById('pr-content');
  if (statusEl) {
    statusEl.style.display = '';
    statusEl.innerHTML = `<div class="pr-error"><i class="fas fa-exclamation-circle fa-2x"></i><p>${escapeHtml(message)}</p></div>`;
  }
  if (contentEl) contentEl.hidden = true;
  console.error('[record]', message);
}

function renderRecordStats(summary) {
  const el = document.getElementById('pr-stats');
  if (!el) return;
  const scoreHitRate = summary.total_predicted > 0
    ? (summary.score_hit / summary.total_predicted * 100).toFixed(1) : '0';
  el.innerHTML = `
    <div class="pr-stat-card"><div class="pr-stat-value">${summary.total_predicted ?? '-'}</div><div class="pr-stat-label">已对比场次</div></div>
    <div class="pr-stat-card"><div class="pr-stat-value">${summary.accuracy != null ? summary.accuracy + '%' : '-'}</div><div class="pr-stat-label">胜平负命中率</div></div>
    <div class="pr-stat-card"><div class="pr-stat-value">${scoreHitRate}%</div><div class="pr-stat-label">比分精确命中率</div></div>
    <div class="pr-stat-card"><div class="pr-stat-value">${summary.avg_goal_error != null ? summary.avg_goal_error : '-'}</div><div class="pr-stat-label">平均进球偏差</div></div>`;
}

function renderRecordLeagues(leagueStats) {
  const el = document.getElementById('pr-leagues');
  if (!el) return;
  if (!leagueStats || leagueStats.length === 0) { el.hidden = true; return; }
  el.hidden = false;
  el.innerHTML = '<h3><i class="fas fa-flag"></i> 分联赛准确率</h3>' +
    leagueStats.map(lg => `<div class="pr-league-row"><span class="pr-league-name">${escapeHtml(lg.league)}</span><div class="pr-league-bar-bg"><div class="pr-league-bar-fill" style="width:${lg.accuracy||0}%"></div></div><span class="pr-league-pct">${lg.correct||0}/${lg.total||0} (${lg.accuracy||0}%)</span></div>`).join('');
}

function renderRecordTrend(trend) {
  const el = document.getElementById('pr-trend');
  if (!el) return;
  if (!trend || trend.length === 0) { el.hidden = true; return; }
  el.hidden = false;
  const maxTotal = Math.max(...trend.map(t => t.total || 1));
  const sorted = [...trend].sort((a, b) => a.date.localeCompare(b.date));
  el.innerHTML = '<h3><i class="fas fa-chart-area"></i> 近期命中趋势</h3><div class="pr-trend-bars">' +
    sorted.map(t => {
      const h = Math.max(((t.correct||0)/maxTotal)*100, 5);
      return `<div class="pr-trend-bar" style="height:${h}%" title="${t.date}: ${t.correct}/${t.total} (${t.accuracy}%)"><span class="pr-trend-label">${t.date?t.date.slice(5):''}</span></div>`;
    }).join('') + '</div>';
}

function renderRecordEval(evalData) {
  const el = document.getElementById('pr-eval');
  if (!el) return;
  const latest = evalData && evalData.latest;
  if (!latest) { el.hidden = true; return; }
  el.hidden = false;

  const snapTime = latest.snapshot_at
    ? new Date(latest.snapshot_at).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
    : '-';
  const periodLabel = latest.period_days ? `最近 ${latest.period_days} 天` : '全量';

  const clsRows = ['H', 'D', 'A'].map(cls => {
    const m = latest.metrics[cls] || {};
    const label = cls === 'H' ? '主胜' : cls === 'D' ? '平局' : '客胜';
    const brier = latest.brier && latest.brier[cls] != null ? latest.brier[cls].toFixed(3) : '-';
    return `<tr>
      <td class="pr-eval-cls">${label}</td>
      <td>${m.precision != null ? (m.precision * 100).toFixed(1) + '%' : '-'}</td>
      <td>${m.recall != null ? (m.recall * 100).toFixed(1) + '%' : '-'}</td>
      <td><strong>${m.f1 != null ? (m.f1 * 100).toFixed(1) + '%' : '-'}</strong></td>
      <td class="pr-eval-brier">${brier}</td>
    </tr>`;
  }).join('');

  const relH = (latest.reliability && latest.reliability.H) || [];
  const relChart = relH.length ? relH.map(b => {
    const predH = Math.max(b.pred_prob * 100, 2);
    const actH = Math.max(b.actual_rate * 100, 2);
    const ok = Math.abs(b.actual_rate - b.pred_prob) < 0.05;
    return `<div class="pr-rel-bin" title="预测 ${(b.pred_prob*100).toFixed(0)}% / 实际 ${(b.actual_rate*100).toFixed(0)}% (n=${b.count})">
      <div class="pr-rel-bars">
        <div class="pr-rel-bar pr-rel-pred" style="height:${predH}%"></div>
        <div class="pr-rel-bar pr-rel-act ${ok ? 'pr-rel-ok' : ''}" style="height:${actH}%"></div>
      </div>
      <span class="pr-rel-label">${Math.round(b.bin_mid * 100)}%</span>
    </div>`;
  }).join('') : '<p class="pr-eval-muted">样本不足，暂无校准数据</p>';

  const history = (evalData.history || []).filter(h => h.accuracy != null);
  const histChart = history.length > 1 ? history.map(h => {
    const hPct = Math.max(h.accuracy, 8);
    const t = h.snapshot_at ? h.snapshot_at.slice(5, 10) : '';
    return `<div class="pr-eval-hist-bar" style="height:${hPct}%" title="${t}: ${h.accuracy}% (${h.total}场)"><span>${t}</span></div>`;
  }).join('') : '';

  el.innerHTML = `
    <div class="pr-eval-head">
      <h3><i class="fas fa-microscope"></i> ML 模型质量评估</h3>
      <span class="pr-eval-meta">${periodLabel} · ${latest.total} 场 · 更新 ${snapTime}</span>
    </div>
    <div class="pr-eval-grid">
      <div class="pr-eval-panel">
        <div class="pr-eval-acc">${latest.accuracy != null ? latest.accuracy + '%' : '-'}</div>
        <div class="pr-eval-acc-label">评估窗口命中率</div>
      </div>
      <div class="pr-eval-panel pr-eval-table-wrap">
        <table class="pr-eval-table">
          <thead><tr><th></th><th>精确率</th><th>召回率</th><th>F1</th><th>Brier↓</th></tr></thead>
          <tbody>${clsRows}</tbody>
        </table>
      </div>
    </div>
    <div class="pr-eval-section">
      <h4>主胜概率校准 <span class="pr-eval-hint">橙=预测概率 · 绿=实际命中率</span></h4>
      <div class="pr-reliability">${relChart}</div>
    </div>
    ${histChart ? `<div class="pr-eval-section"><h4>评估快照趋势</h4><div class="pr-eval-history">${histChart}</div></div>` : ''}
  `;
}

// ── 核心：比赛对比卡片（表格式布局，表头为"实际比分"和"预测比分"）──
function renderRecordMatchCard(m) {
  // 实际总进球：优先用独立字段，fallback 从 actual_score 解析
  let realTotal = null;
  if (m.actual_home_goals != null && m.actual_away_goals != null) {
    realTotal = m.actual_home_goals + m.actual_away_goals;
  } else if (m.actual_score && m.actual_score.includes('-')) {
    const parts = m.actual_score.split('-');
    realTotal = parseInt(parts[0]) + parseInt(parts[1]);
  }

  // 预测总进球：优先用独立字段，fallback 从 predicted_score 解析，再 fallback 从 ML 概率推算
  let predTotal = null;
  let predScore = m.predicted_score || null;
  if (m.predicted_home_goals != null && m.predicted_away_goals != null) {
    predTotal = m.predicted_home_goals + m.predicted_away_goals;
    if (!predScore) predScore = m.predicted_home_goals + '-' + m.predicted_away_goals;
  } else if (m.predicted_score && m.predicted_score.includes('-')) {
    const parts = m.predicted_score.split('-');
    predTotal = parseInt(parts[0]) + parseInt(parts[1]);
  } else if (m.ml_probs && m.ml_probs.home != null && m.ml_probs.away != null) {
    // 从 ML 概率用泊松模型推算预测比分（与后端 generate_predicted_scores 同算法）
    const ph = m.ml_probs.home, pa = m.ml_probs.away;
    const lamH = Math.max(-Math.log(Math.max(1 - ph, 0.01)) * 1.5, 0.3);
    const lamA = Math.max(-Math.log(Math.max(1 - pa, 0.01)) * 1.5, 0.3);
    const predH = Math.min(Math.round(lamH), 5);
    const predA = Math.min(Math.round(lamA), 5);
    predScore = predH + '-' + predA;
    predTotal = predH + predA;
  }

  // 预测比分显示
  const predScoreDisplay = predScore || '-';
  const actualScoreDisplay = m.actual_score || '-';

  // A5-fix: 三态展示 true=命中 false=未命中 null=未评估
  const resultHit = m.result_correct === true;
  const resultEvaluated = m.result_correct !== null && m.result_correct !== undefined;
  // 比分命中：后端有就用，没有就前端算
  let scoreHit = m.score_correct;
  if (scoreHit == null && predScore && m.actual_score) {
    scoreHit = (predScore === m.actual_score);
  }
  // 总进球误差
  // E3-fix: goal_diff_error = 净胜球预测误差（|pred_diff - actual_diff|）
  let goalErr = m.goal_diff_error;
  if (goalErr == null && realTotal != null && predTotal != null) {
    // 前端 fallback：用总进球差估算（精度低，仅显示用）
    const predH = m.predicted_home_goals, predA = m.predicted_away_goals;
    const actH  = m.actual_home_goals,    actA  = m.actual_away_goals;
    if (predH != null && predA != null && actH != null && actA != null) {
      goalErr = Math.abs((predH - predA) - (actH - actA));
    }
  }

  const timeStr = m.match_time ? formatTime(m.match_time) : '-';

  // 净胜球误差展示
  let goalHitDisplay = '-', goalHitClass = 'pr-hit-diff';
  if (goalErr != null) {
    if (goalErr === 0) { goalHitDisplay = '✅ 准确'; goalHitClass = 'pr-hit-ok'; }
    else { goalHitDisplay = '净差' + goalErr + '球'; }
  }

  // 比分命中显示
  let scoreHitDisplay, scoreHitClass;
  if (predScore) {
    scoreHitDisplay = scoreHit ? '✅' : '❌';
    scoreHitClass = scoreHit ? 'pr-hit-ok' : 'pr-hit-no';
  } else {
    scoreHitDisplay = '-';
    scoreHitClass = 'pr-hit-diff';
  }

  return `<article class="pr-match-card" data-fixture-id="${m.fixture_id || ''}">
    <header class="pr-match-head">
      <span class="pr-league">${escapeHtml(m.league || '')}</span>
      <span class="pr-teams">${escapeHtml(m.home_team_cn || m.home_team || '-')} <em>vs</em> ${escapeHtml(m.away_team_cn || m.away_team || '-')}</span>
      <span class="pr-time">${escapeHtml(timeStr)}</span>
    </header>
    <table class="pr-table">
      <thead>
        <tr>
          <th></th>
          <th class="pr-th-actual">实际结果</th>
          <th class="pr-th-pred">预测结果</th>
          <th>命中</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td class="pr-td-label">胜平负</td>
          <td class="pr-td-actual">${escapeHtml(m.actual_result || '-')}</td>
          <td class="pr-td-pred">${escapeHtml(m.predicted_result || '-')}</td>
          <td class="pr-td-hit"><span class="pr-hit-mark ${!resultEvaluated ? 'pr-hit-diff' : (resultHit ? 'pr-hit-ok' : 'pr-hit-no')}">${!resultEvaluated ? '—' : (resultHit ? '✅' : '❌')}</span></td>
        </tr>
        <tr>
          <td class="pr-td-label">比分</td>
          <td class="pr-td-actual">${escapeHtml(actualScoreDisplay)}</td>
          <td class="pr-td-pred">${escapeHtml(predScoreDisplay)}</td>
          <td class="pr-td-hit"><span class="pr-hit-mark ${scoreHitClass}">${scoreHitDisplay}</span></td>
        </tr>
        <tr>
          <td class="pr-td-label">总进球</td>
          <td class="pr-td-actual">${realTotal != null ? realTotal : '-'}</td>
          <td class="pr-td-pred">${predTotal != null ? predTotal : '暂无'}</td>
          <td class="pr-td-hit"><span class="pr-hit-mark ${goalHitClass}">${goalHitDisplay}</span></td>
        </tr>
      </tbody>
    </table>
  </article>`;
}

function renderRecordList(matches) {
  const el = document.getElementById('pr-list');
  if (!el) return;
  if (!Array.isArray(matches) || matches.length === 0) {
    el.innerHTML = '<div class="pr-empty" style="padding:2rem"><p>当前筛选下无匹配比赛</p></div>';
    return;
  }

  // 按天分组（按比赛时间倒序）
  const weekDays = ['周日','周一','周二','周三','周四','周五','周六'];
  const groups = {};
  matches.forEach(m => {
    const t = m.match_time ? new Date(m.match_time) : null;
    const key = t ? t.toISOString().slice(0, 10) : 'unknown';
    if (!groups[key]) groups[key] = [];
    groups[key].push(m);
  });

  // 按日期倒序排列（最近的在前）
  const sortedKeys = Object.keys(groups).sort().reverse();

  let html = '';
  sortedKeys.forEach((key, idx) => {
    let label;
    if (key === 'unknown') {
      label = '日期未知';
    } else {
      const d = new Date(key + 'T00:00:00');
      label = `${d.getFullYear()}-${d.getMonth()+1}-${d.getDate()}（${weekDays[d.getDay()]}）`;
    }
    // 默认展开第一天（最近的）
    const isExpanded = idx === 0;
    html += `<div class="pr-day-group">
      <div class="pr-day-header ${isExpanded ? 'expanded' : ''}" onclick="this.classList.toggle('expanded'); this.nextElementSibling.classList.toggle('hidden')">
        <span class="pr-day-label">${label}</span>
        <span class="pr-day-count">${groups[key].length} 场</span>
        <i class="fas fa-chevron-down pr-day-arrow"></i>
      </div>
      <div class="pr-day-body ${isExpanded ? '' : 'hidden'}">
        ${groups[key].map(renderRecordMatchCard).join('')}
      </div>
    </div>`;
  });

  el.innerHTML = html;
}

function renderRecordPagination(total, page, perPage) {
  const el = document.getElementById('pr-pagination');
  if (!el) return;
  if (total <= perPage) { el.innerHTML = ''; return; }
  const totalPages = Math.ceil(total / perPage);
  let html = `<button class="pr-page-btn" ${page<=1?'disabled':''} onclick="recordGoPage(${page-1})">‹</button>`;
  let start = Math.max(1, page - 3);
  let end = Math.min(totalPages, start + 6);
  if (end - start < 6) start = Math.max(1, end - 6);
  if (start > 1) { html += `<button class="pr-page-btn" onclick="recordGoPage(1)">1</button>`; if (start > 2) html += '<span style="color:var(--sub);padding:0 .3rem">…</span>'; }
  for (let i = start; i <= end; i++) html += `<button class="pr-page-btn ${i===page?'active':''}" onclick="recordGoPage(${i})">${i}</button>`;
  if (end < totalPages) { if (end < totalPages-1) html += '<span style="color:var(--sub);padding:0 .3rem">…</span>'; html += `<button class="pr-page-btn" onclick="recordGoPage(${totalPages})">${totalPages}</button>`; }
  html += `<button class="pr-page-btn" ${page>=totalPages?'disabled':''} onclick="recordGoPage(${page+1})">›</button>`;
  el.innerHTML = html;
}

function recordGoPage(p) { recordState.page = p; loadRecordMatches(); }
window.recordGoPage = recordGoPage;
