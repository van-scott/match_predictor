/* ── MatchPredict Pro – Main Script ── */

// ── STATE ─────────────────────────────────────────────────────────────────
let currentTab = 'classic';
let teamsData = {};
let lotteryMatches = [];
let aiCart = [];

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

  if (tab === 'ai' && lotteryMatches.length === 0) {
    loadLotteryMatches();
  }
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
          <p style="font-size:.8rem;color:var(--c-muted);margin-top:.5rem">数据将在每日 06:00 / 10:00 / 14:00 / 18:00 / 22:00（北京时间）自动同步</p>
        </div>`;
    }
  } catch (e) {
    listEl.innerHTML = `<div class="error-msg">❌ 获取失败，请检查网络</div>`;
  }
}

function renderLotteryList() {
  const listEl = document.getElementById('lottery-list');
  listEl.innerHTML = '';

  // 只展示有赔率 且 未开赛 的比赛
  const now = Date.now();
  const withOdds = lotteryMatches.filter(m => {
    const o = m.odds?.hhad || {};
    if (!o.h || !o.d || !o.a) return false;
    const t = m.match_time || m.match_date;
    if (t) {
      const ts = new Date(t.replace(' ', 'T')).getTime();
      if (!isNaN(ts) && ts < now) return false;
    }
    return true;
  });

  if (withOdds.length === 0) {
    listEl.innerHTML = `<div class="error-msg"><p>ℹ️ 暂无含赔率的赛事</p><p style="font-size:.8rem;color:var(--c-muted);margin-top:.5rem">赔率数据每日自动同步，请稍后再试</p></div>`;
    return;
  }

  withOdds.forEach(m => {
    const inCart = aiCart.some(c => c.match_id === m.match_id);
    const odds = m.odds?.hhad || {};
    const card = document.createElement('div');
    card.className = `lotto-card ${inCart ? 'selected' : ''}`;
    card.id = `lotto-${m.match_id}`;
    card.onclick = () => toggleAiCart(m);
    card.innerHTML = `
      <div class="lotto-top">
        <span>${m.league_name}</span>
        <span>${m.match_num || ''} · ${formatTime(m.match_time)}</span>
      </div>
      <div class="lotto-teams">
        <span>${m.home_team}</span>
        <span style="font-size:0.7rem;color:var(--c-muted)">VS</span>
        <span>${m.away_team}</span>
      </div>
      <div class="lotto-odds">
        <div class="lotto-odd-box"><span class="lotto-odd-label">主胜</span><span class="lotto-odd-val">${odds.h || '-'}</span></div>
        <div class="lotto-odd-box"><span class="lotto-odd-label">平局</span><span class="lotto-odd-val">${odds.d || '-'}</span></div>
        <div class="lotto-odd-box"><span class="lotto-odd-label">客胜</span><span class="lotto-odd-val">${odds.a || '-'}</span></div>
      </div>`;
    listEl.appendChild(card);
  });
}

function formatTime(t) {
  if (!t) return '';
  try {
    const d = new Date(t.replace(' ', 'T'));
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
    if (aiCart.length >= 5) { showToast('最多选择5场比赛', 'error'); return; }
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
        <b>${m.home_team} vs ${m.away_team}</b>
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

// ── AI PREDICT ────────────────────────────────────────────────────────────
async function runAI() {
  if (aiCart.length === 0) return;

  const resultsEl = document.getElementById('ai-results');
  resultsEl.classList.remove('hidden');
  resultsEl.scrollIntoView({ behavior: 'smooth' });

  let elapsed = 0;
  const showSpinner = (msg) => {
    resultsEl.innerHTML = `
      <div class="spinner-overlay">
        <i class="fas fa-robot fa-spin fa-3x" style="color:var(--c-gold)"></i>
        <span>${msg}</span>
        <small id="ai-elapsed" style="color:var(--c-muted)"></small>
      </div>`;
  };
  showSpinner(`AI大模型深度分析中，请稍候... (${aiCart.length} 场)`);

  const elapsedTimer = setInterval(() => {
    elapsed++;
    const el = document.getElementById('ai-elapsed');
    if (el) el.textContent = `已等待 ${elapsed}s，owl-alpha 正在深度思考...`;
  }, 1000);

  try {
    // Step 1: 鉴权+扣积分，获取 prompts+apikey
    const initRes = await fetch('/api/ai/predict', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ matches: aiCart }),
    });
    const initData = await initRes.json();

    if (initRes.status === 402 || initData.error_code === 'INSUFFICIENT_CREDITS') {
      clearInterval(elapsedTimer);
      resultsEl.classList.add('hidden');
      showCreditsModal(initData);
      return;
    }
    if (!initData.success) {
      clearInterval(elapsedTimer);
      resultsEl.innerHTML = `<div class="error-msg">❌ ${initData.message || initData.error || '请求失败'}</div>`;
      return;
    }
    refreshCredits();

    const { api_key, model, matches: enrichedMatches } = initData;

    // Step 2: 浏览器直接调用 OpenRouter（无 Vercel 超时限制）
    const predictions = [];
    const uncachedPredictions = []; // 仅保存新生成的以备后台存储

    for (let i = 0; i < enrichedMatches.length; i++) {
      const m = enrichedMatches[i];
      const el = document.getElementById('ai-elapsed');

      if (m.from_cache) {
        if (el) el.textContent = `从缓存读取第 ${i+1}/${enrichedMatches.length} 场：${m.home_team} vs ${m.away_team}...`;
        predictions.push({
          match_id:    m.match_id,
          home_team:   m.home_team,
          away_team:   m.away_team,
          league_name: m.league_name,
          match_time:  m.match_time,
          home_odds:   m.home_odds,
          draw_odds:   m.draw_odds,
          away_odds:   m.away_odds,
          ai_analysis: m.ai_analysis,
          from_cache:  true,
          odds: { home: m.home_odds, draw: m.draw_odds, away: m.away_odds },
        });
        // 为了视觉效果，稍微等一下
        await new Promise(r => setTimeout(r, 500));
        continue;
      }

      if (el) el.textContent = `正在分析第 ${i+1}/${enrichedMatches.length} 场：${m.home_team} vs ${m.away_team}...`;

      try {
        const aiRes = await fetch('https://openrouter.ai/api/v1/chat/completions', {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${api_key}`,
            'Content-Type': 'application/json',
            'HTTP-Referer': window.location.origin,
            'X-Title': 'MatchPredict Football Analysis',
          },
          body: JSON.stringify({
            model: model || 'openrouter/owl-alpha',
            messages: [
              { role: 'system', content: '你是一位经验丰富的足球分析专家，擅长从数据和赔率中发现价值投注机会，分析精准、专业。' },
              { role: 'user',   content: m.prompt },
            ],
            temperature: 0.3,
            max_tokens: 2048,
          }),
        });
        const aiData = await aiRes.json();
        const analysis = aiData.choices?.[0]?.message?.content?.trim() || '⚠️ AI未返回分析内容';
        const newPred = {
          match_id:    m.match_id,
          home_team:   m.home_team,
          away_team:   m.away_team,
          league_name: m.league_name,
          match_time:  m.match_time,
          home_odds:   m.home_odds,
          draw_odds:   m.draw_odds,
          away_odds:   m.away_odds,
          ai_analysis: analysis,
          from_cache:  false,
          odds: { home: m.home_odds, draw: m.draw_odds, away: m.away_odds },
        };
        predictions.push(newPred);
        uncachedPredictions.push(newPred);
      } catch (e) {
        predictions.push({
          match_id: m.match_id, home_team: m.home_team, away_team: m.away_team,
          league_name: m.league_name, match_time: m.match_time,
          home_odds: m.home_odds, draw_odds: m.draw_odds, away_odds: m.away_odds,
          ai_analysis: `⚠️ 分析失败：${e.message}`,
          from_cache:  false,
          odds: { home: m.home_odds, draw: m.draw_odds, away: m.away_odds },
        });
      }
    }

    clearInterval(elapsedTimer);

    // Step 3: 展示结果
    renderAIResults(predictions);

    // Step 4: 后台保存到数据库（fire-and-forget）
    // 即便是缓存命中，也需要存一条记录，这样用户的"个人预测历史"里才会显示
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
    const odds = p.odds || {};
    // Convert markdown bold **text** to <strong>
    const formattedAnalysis = (p.ai_analysis || '').replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');

    card.innerHTML = `
      <div class="ai-result-header">
        <div>
          <h3>${p.home_team} vs ${p.away_team}</h3>
          <small style="color:var(--c-muted)">${p.league_name}</small>
        </div>
        <span class="ai-badge">🤖 OWL-ALPHA</span>
      </div>
      <div class="ai-odds-strip">
        <div class="ai-odd-item"><span class="ai-odd-label">主胜赔率</span><span class="ai-odd-val">${odds.home || '-'}</span></div>
        <div class="ai-odd-item"><span class="ai-odd-label">平局赔率</span><span class="ai-odd-val">${odds.draw || '-'}</span></div>
        <div class="ai-odd-item"><span class="ai-odd-label">客胜赔率</span><span class="ai-odd-val">${odds.away || '-'}</span></div>
      </div>
      <div class="ai-analysis-text">${formattedAnalysis}</div>
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
  navRight.innerHTML = `
    <div class="user-pill">
      <i class="fas fa-coins"></i>
      <span class="credit-num" id="credits-display">${user.credits ?? 0}</span>
      <span style="color:var(--muted);font-size:.7rem">积分</span>
      <button class="btn-checkin" id="checkin-btn" onclick="doCheckin()" title="每日签到+6积分">
        <i class="fas fa-calendar-check"></i> 签到
      </button>
      <a href="/profile" class="btn-ghost" style="text-decoration:none">
        <i class="fas fa-user"></i> ${user.username}
      </a>
      <button class="btn-ghost" onclick="doLogout()">退出</button>
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
  } catch { showToast('签到失败', 'error'); }
  finally {
    if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-calendar-check"></i> 签到'; }
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

