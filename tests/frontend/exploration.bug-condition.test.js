/**
 * Bug Condition Exploration Test — prediction-record-page
 *
 * Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.7
 * Properties: P1, P2, P3, P4, P5
 *
 * IMPORTANT: These tests are EXPECTED TO FAIL on unfixed code. Failure surfaces
 * counterexamples that confirm the bug exists. Do NOT attempt to fix the test
 * or the implementation when these fail — they will validate the fix later
 * after task 3 (the implementation tasks).
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import fc from 'fast-check';

// Sample legal match generator response field set
function fakeMatch(overrides = {}) {
  const defaults = {
    fixture_id: 1001,
    league: '西甲',
    home_team: 'Real Madrid',
    away_team: 'Barcelona',
    home_team_cn: '皇家马德里',
    away_team_cn: '巴塞罗那',
    match_time: '2024-10-26T20:00:00Z',
    predicted_result: '主胜',
    actual_result: '主胜',
    predicted_score: '2-1',
    actual_score: '3-1',
    predicted_home_goals: 2,
    predicted_away_goals: 1,
    actual_home_goals: 3,
    actual_away_goals: 1,
    result_correct: true,
    score_correct: false,
    goal_diff_error: 1,
    ml_probs: { home: 0.62, draw: 0.18, away: 0.20 },
    odds: { hhad: { h: 1.85, d: 3.40, a: 4.20 } },
  };
  return { ...defaults, ...overrides };
}

function summaryWithData() {
  return {
    success: true,
    summary: {
      total_finished: 5,
      total_predicted: 5,
      correct: 3,
      score_hit: 1,
      accuracy: 60,
      avg_goal_error: 0.8,
    },
    league_stats: [
      { league: '西甲', total: 3, correct: 2, score_hit: 1, accuracy: 66.7 },
    ],
    trend: [
      { date: '2024-10-25', total: 2, correct: 1, accuracy: 50 },
      { date: '2024-10-26', total: 3, correct: 2, accuracy: 66.7 },
    ],
  };
}

function summaryEmpty() {
  return {
    success: true,
    summary: {
      total_finished: 0,
      total_predicted: 0,
      correct: 0,
      score_hit: 0,
      accuracy: 0,
      avg_goal_error: 0,
    },
    league_stats: [],
    trend: [],
  };
}

function matchesPayload(matches) {
  return {
    success: true,
    total: matches.length,
    page: 1,
    per_page: 20,
    matches,
  };
}

let consoleErrorSpy;

beforeEach(() => {
  // Reset DOM and globals for each test
  document.body.innerHTML = '';
  // Wipe any leftover script.js globals from a previous test
  for (const k of Object.keys(globalThis)) {
    if (
      k.startsWith('switchTab') ||
      k === 'currentTab' ||
      k === 'teamsData' ||
      k === 'lotteryMatches' ||
      k === 'aiCart' ||
      k === 'teamZh' ||
      k === 'smartMatches' ||
      k === 'smartFilterLeague' ||
      k === 'loadRecordMatches'
    ) {
      try { delete globalThis[k]; } catch (_) { /* ignore non-configurable */ }
    }
  }
  consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  consoleErrorSpy.mockRestore();
  vi.restoreAllMocks();
});

describe('Bug Condition Exploration: prediction-record-page', () => {
  it('EnterTabWithData: 5 matches → 15 .pr-cmp-row + at least one [data-dim="result"]', async () => {
    // Property 1 (Bug): "预测 vs 真实" 对照视图缺失 — expected to FAIL on unfixed code
    loadRecordPanel();

    const matches = [
      fakeMatch({ fixture_id: 1, predicted_score: '2-1', actual_score: '3-1', score_correct: false, result_correct: true,  goal_diff_error: 1 }),
      fakeMatch({ fixture_id: 2, predicted_score: '1-1', actual_score: '1-1', score_correct: true,  result_correct: true,  goal_diff_error: 0, predicted_result: '平', actual_result: '平' }),
      fakeMatch({ fixture_id: 3, predicted_score: '0-2', actual_score: '0-1', score_correct: false, result_correct: true,  goal_diff_error: 1, predicted_result: '客胜', actual_result: '客胜' }),
      fakeMatch({ fixture_id: 4, predicted_score: '2-0', actual_score: '1-2', score_correct: false, result_correct: false, goal_diff_error: 1, predicted_result: '主胜', actual_result: '客胜' }),
      fakeMatch({ fixture_id: 5, predicted_score: '1-0', actual_score: '1-0', score_correct: true,  result_correct: true,  goal_diff_error: 0 }),
    ];

    stubFetch({
      '/api/accuracy/summary': () => summaryWithData(),
      '/api/accuracy/matches': () => matchesPayload(matches),
      '/api/teams': () => ({ success: true, teams: {}, team_zh: {} }),
      '/api/sync/status': () => ({ success: true }),
    });

    loadAppScript();
    await triggerSwitchTab('record');

    const cmpRows = document.querySelectorAll('.pr-cmp-row');
    expect(cmpRows.length).toBeGreaterThanOrEqual(15);
    expect(document.querySelector('.pr-cmp-row[data-dim="result"]')).not.toBeNull();
  });

  it('EnterTabEmpty: total_predicted=0 → only empty-state visible inside #panel-record', async () => {
    // Property 2 (Bug): 空数据未退化 — expected to FAIL on unfixed code (still renders 4 placeholder cards + filter + spinner)
    loadRecordPanel();

    stubFetch({
      '/api/accuracy/summary': () => summaryEmpty(),
      '/api/accuracy/matches': () => matchesPayload([]),
      '/api/teams': () => ({ success: true, teams: {}, team_zh: {} }),
      '/api/sync/status': () => ({ success: true }),
    });

    loadAppScript();
    await triggerSwitchTab('record');

    const panel = document.getElementById('panel-record');
    expect(panel).not.toBeNull();

    // Helper: visible = not display:none and not has hidden attr; ancestor chain must also be visible.
    function isVisible(el) {
      let node = el;
      while (node && node !== document) {
        if (node.nodeType === 1) {
          if (node.hasAttribute && node.hasAttribute('hidden')) return false;
          // jsdom doesn't compute style; use inline style + class hidden
          const style = node.getAttribute && node.getAttribute('style');
          if (style && /display\s*:\s*none/i.test(style)) return false;
          if (node.classList && node.classList.contains('hidden')) return false;
        }
        node = node.parentNode;
      }
      return true;
    }

    // Find visible non-root, non-hero descendants of #panel-record.
    const allDesc = panel.querySelectorAll('*');
    const visibleNonHeroNodes = [];
    for (const el of allDesc) {
      if (!isVisible(el)) continue;
      // Skip nodes inside the hero header (titles/icons)
      if (el.closest('.pr-hero, .record-hero')) continue;
      visibleNonHeroNodes.push(el);
    }

    // After fix, the only visible non-root/non-hero subtree should be a single empty-state region.
    // We assert that an empty-state element exists AND nothing else (stats grid / list / filter / pagination)
    // is rendered as a sibling.
    const visibleText = panel.textContent;
    expect(visibleText).toContain('暂无已结束的比赛数据');

    // No stats grid container should be visible
    const statsGrid = panel.querySelector('#record-stats-grid, #pr-stats');
    if (statsGrid) {
      expect(isVisible(statsGrid)).toBe(false);
    }
    // No match list or spinner should be visible
    const matchList = panel.querySelector('#record-match-list, #pr-list');
    if (matchList) {
      expect(isVisible(matchList)).toBe(false);
    }
    // No filter bar should be visible
    const filter = panel.querySelector('.record-filter-bar, #pr-filter');
    if (filter) {
      expect(isVisible(filter)).toBe(false);
    }
  });

  it('FilterChangeRefSafe: window.loadRecordMatches is a function and change event triggers a new fetch with league=', async () => {
    // Property 3 (Bug): ReferenceError on filter change — expected to FAIL on unfixed code
    loadRecordPanel();

    const fetchMock = stubFetch({
      '/api/accuracy/summary': () => summaryWithData(),
      '/api/accuracy/matches': () => matchesPayload([fakeMatch()]),
      '/api/teams': () => ({ success: true, teams: {}, team_zh: {} }),
      '/api/sync/status': () => ({ success: true }),
    });

    loadAppScript();
    await triggerSwitchTab('record');

    expect(typeof window.loadRecordMatches).toBe('function');

    // Try the new filter ID first; fall back to legacy id used in unfixed template
    const select =
      document.getElementById('pr-filter-league') ||
      document.getElementById('record-filter-league');
    expect(select).not.toBeNull();

    // Set a value and dispatch change. The fix must not throw a ReferenceError.
    select.value = '西甲';
    let threwReferenceError = false;
    try {
      select.dispatchEvent(new Event('change', { bubbles: true }));
      // give async loaders a chance
      for (let i = 0; i < 10; i++) await new Promise(r => setTimeout(r, 0));
    } catch (err) {
      if (err instanceof ReferenceError) threwReferenceError = true;
    }
    expect(threwReferenceError).toBe(false);

    // Assert that some matches fetch URL after the change includes league=
    const calls = fetchMock.mock.calls.map(c => (typeof c[0] === 'string' ? c[0] : c[0]?.url));
    const matchesCalls = calls.filter(u => u && u.includes('/api/accuracy/matches'));
    const withLeague = matchesCalls.filter(u => /[?&]league=/.test(u));
    expect(withLeague.length).toBeGreaterThan(0);
  });

  it('HitMarkConsistency (fast-check): result_correct true→✅, false→❌, null→-', async () => {
    // Property 4 (Bug): hit mark not consistent / not present — expected to FAIL on unfixed code
    const resultCorrectArb = fc.constantFrom(true, false, null);
    const scoreCorrectArb = fc.constantFrom(true, false, null);
    const goalDiffErrArb = fc.option(fc.integer({ min: 0, max: 6 }), { nil: null });

    const matchArb = fc.record({
      fixture_id: fc.integer({ min: 1, max: 10000 }),
      league: fc.constantFrom('英超', '西甲', '意甲', '德甲', '法甲'),
      home_team: fc.string({ minLength: 1, maxLength: 12 }),
      away_team: fc.string({ minLength: 1, maxLength: 12 }),
      home_team_cn: fc.string({ minLength: 1, maxLength: 8 }),
      away_team_cn: fc.string({ minLength: 1, maxLength: 8 }),
      match_time: fc.constant('2024-10-26T20:00:00Z'),
      predicted_result: fc.constantFrom('主胜', '平', '客胜'),
      actual_result: fc.constantFrom('主胜', '平', '客胜'),
      predicted_score: fc.constantFrom('1-0', '2-1', '0-0', '1-1', '0-1'),
      actual_score: fc.constantFrom('1-0', '2-1', '0-0', '1-1', '0-1'),
      predicted_home_goals: fc.integer({ min: 0, max: 5 }),
      predicted_away_goals: fc.integer({ min: 0, max: 5 }),
      actual_home_goals: fc.integer({ min: 0, max: 5 }),
      actual_away_goals: fc.integer({ min: 0, max: 5 }),
      result_correct: resultCorrectArb,
      score_correct: scoreCorrectArb,
      goal_diff_error: goalDiffErrArb,
      ml_probs: fc.constant(null),
      odds: fc.constant(null),
    });

    await fc.assert(
      fc.asyncProperty(matchArb, async (m) => {
        // Reset DOM and globals for each property iteration
        document.body.innerHTML = '';
        for (const k of ['switchTab', 'currentTab', 'lotteryMatches', 'aiCart', 'smartMatches', 'loadRecordMatches']) {
          try { delete globalThis[k]; } catch (_) { /* ignore */ }
        }

        loadRecordPanel();
        stubFetch({
          '/api/accuracy/summary': () => summaryWithData(),
          '/api/accuracy/matches': () => matchesPayload([m]),
          '/api/teams': () => ({ success: true, teams: {}, team_zh: {} }),
          '/api/sync/status': () => ({ success: true }),
        });

        loadAppScript();
        await triggerSwitchTab('record');

        const card = document.querySelector('.pr-match-card');
        expect(card).not.toBeNull();

        const resultRow = card.querySelector('.pr-cmp-row[data-dim="result"]');
        const scoreRow = card.querySelector('.pr-cmp-row[data-dim="score"]');
        const goalsRow = card.querySelector('.pr-cmp-row[data-dim="goals"]');

        expect(resultRow).not.toBeNull();
        expect(scoreRow).not.toBeNull();
        expect(goalsRow).not.toBeNull();

        const resultMark = resultRow.querySelector('.pr-hit-mark').textContent.trim();
        const scoreMark = scoreRow.querySelector('.pr-hit-mark').textContent.trim();

        if (m.result_correct === true) expect(resultMark).toContain('✅');
        else if (m.result_correct === false) expect(resultMark).toContain('❌');
        else expect(resultMark).toBe('-');

        if (m.score_correct === true) expect(scoreMark).toContain('✅');
        else if (m.score_correct === false) expect(scoreMark).toContain('❌');
        else expect(scoreMark).toBe('-');
      }),
      { numRuns: 50 }
    );
  });

  it('ApiError: summary 500 → .pr-error visible (or readable error text), no permanent spinner; console.error called', async () => {
    // Property 5 (Bug): 错误状态不可见 — expected to FAIL on unfixed code (永久 spinner)
    loadRecordPanel();

    stubFetch({
      '/api/accuracy/summary': () => ({ ok: false, status: 500, json: async () => ({ success: false, error: 'internal' }) }),
      '/api/accuracy/matches': () => matchesPayload([]),
      '/api/teams': () => ({ success: true, teams: {}, team_zh: {} }),
      '/api/sync/status': () => ({ success: true }),
    });

    loadAppScript();
    await triggerSwitchTab('record');

    const panel = document.getElementById('panel-record');
    const errorEl = panel.querySelector('.pr-error');
    const visibleText = (panel.textContent || '').toLowerCase();
    const hasErrorSemantic =
      errorEl !== null ||
      visibleText.includes('错误') ||
      visibleText.includes('失败') ||
      visibleText.includes('error');

    expect(hasErrorSemantic).toBe(true);
    expect(consoleErrorSpy).toHaveBeenCalled();
  });
});
