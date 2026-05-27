/**
 * Preservation Property Tests — prediction-record-page
 *
 * **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10**
 * **Properties: P6**
 *
 * These tests encode the PRESERVATION invariant: behaviors outside the bug
 * condition (other tabs, existing CSS, global functions, API contracts) must
 * remain unchanged after the fix.
 *
 * Methodology:
 *   1. Observe real behavior on UNFIXED code and encode as assertions.
 *   2. Run on UNFIXED code → must ALL PASS (confirms baseline accuracy).
 *   3. Run on FIXED code → must ALL PASS (confirms zero regression).
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import fc from 'fast-check';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

import { parseCssRules, isPrRule } from './lib/css-rules.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, '../..');

// ── Baseline snapshots ────────────────────────────────────────────────────
const baselineDir = path.join(__dirname, 'baselines');
const globalFunctionsBaseline = JSON.parse(
  fs.readFileSync(path.join(baselineDir, 'global-functions.snapshot.json'), 'utf8')
);
const cssSelectorBaseline = JSON.parse(
  fs.readFileSync(path.join(baselineDir, 'css-selectors.snapshot.json'), 'utf8')
);

// ── Helpers ───────────────────────────────────────────────────────────────
function sha256(s) {
  return crypto.createHash('sha256').update(s).digest('hex');
}

beforeEach(() => {
  document.body.innerHTML = '';
  // Clean up any leftover globals from previous tests
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
      k === 'loadRecordMatches' ||
      k === 'recordState'
    ) {
      try { delete globalThis[k]; } catch (_) { /* ignore non-configurable */ }
    }
  }
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('Preservation Property: P6 — Other tabs, CSS, global functions, API contract unchanged', () => {

  // ─────────────────────────────────────────────────────────────────────────
  // 1. OtherTabsSwitchPreservation
  // ─────────────────────────────────────────────────────────────────────────
  it('OtherTabsSwitchPreservation: random tab switch sequences → correct panel visibility and active class', async () => {
    /**
     * Validates: Requirements 3.1, 3.2, 3.3, 3.4
     *
     * For any random sequence of tab switches among {classic, ai, smart, wc},
     * after each switch the corresponding panel must be visible (no `hidden` class)
     * and the tab button must have `active` class. All other panels must have `hidden`.
     */
    const tabArb = fc.array(
      fc.constantFrom('classic', 'ai', 'smart', 'wc'),
      { minLength: 1, maxLength: 10 }
    );

    await fc.assert(
      fc.asyncProperty(tabArb, async (seq) => {
        // Reset DOM
        document.body.innerHTML = '';
        for (const k of ['switchTab', 'currentTab', 'lotteryMatches', 'aiCart', 'smartMatches', 'smartFilterLeague', 'loadRecordMatches', 'recordState']) {
          try { delete globalThis[k]; } catch (_) { /* ignore */ }
        }

        loadRecordPanel();
        stubFetch({
          '/api/teams': () => ({ success: true, teams: {}, team_zh: {} }),
          '/api/sync/status': () => ({ success: true }),
          '/api/lottery/matches': () => ({ success: true, matches: [] }),
          '/api/upcoming-matches': () => ({ success: true, matches: [], total: 0 }),
        });
        loadAppScript();

        const allTabs = ['classic', 'ai', 'smart', 'wc', 'record'];

        for (const tab of seq) {
          await triggerSwitchTab(tab);

          // The active panel should NOT have 'hidden' class
          const activePanel = document.getElementById(`panel-${tab}`);
          expect(activePanel).not.toBeNull();
          expect(activePanel.classList.contains('hidden')).toBe(false);

          // The active tab button should have 'active' class
          const activeBtn = document.getElementById(`tab-${tab}`);
          expect(activeBtn).not.toBeNull();
          expect(activeBtn.classList.contains('active')).toBe(true);

          // All other panels should have 'hidden' class
          for (const other of allTabs) {
            if (other === tab) continue;
            const otherPanel = document.getElementById(`panel-${other}`);
            if (otherPanel) {
              expect(otherPanel.classList.contains('hidden')).toBe(true);
            }
          }
        }
      }),
      { numRuns: 30 }
    );
  });

  // ─────────────────────────────────────────────────────────────────────────
  // 2. ExistingCssSelectorPreservation
  // ─────────────────────────────────────────────────────────────────────────
  it('ExistingCssSelectorPreservation: non-.pr-* CSS rules unchanged from baseline', () => {
    /**
     * Validates: Requirements 3.8
     *
     * Parse static/css/style.css, extract all rules that are NOT .pr-* / #pr-*,
     * and assert their ordered list of {selector, context, bodyHash} matches
     * the baseline snapshot exactly.
     *
     * On unfixed code: no .pr-* rules exist, so all rules are non-pr → baseline matches.
     * After fix: only .pr-* rules are appended → non-pr rules still match baseline.
     */
    const cssPath = path.join(repoRoot, 'static/css/style.css');
    const css = fs.readFileSync(cssPath, 'utf8');
    const rules = parseCssRules(css);
    const nonPrRules = rules.filter(r => !isPrRule(r.selector, r.context));

    // Build the same structure as the baseline
    const current = nonPrRules.map(r => ({
      selector: r.selector,
      context: r.context,
      bodyHash: sha256(r.body),
      bodyLength: r.body.length,
    }));

    // Compare count
    expect(current.length).toBe(cssSelectorBaseline.rules.length);

    // Compare each rule
    for (let i = 0; i < current.length; i++) {
      const cur = current[i];
      const base = cssSelectorBaseline.rules[i];
      expect(cur.selector).toBe(base.selector);
      expect(cur.context).toBe(base.context);
      expect(cur.bodyHash).toBe(base.bodyHash);
    }
  });

  // ─────────────────────────────────────────────────────────────────────────
  // 3. GlobalFunctionsPreservation
  // ─────────────────────────────────────────────────────────────────────────
  it('GlobalFunctionsPreservation: existing global functions exist with correct parameter counts', () => {
    /**
     * Validates: Requirements 3.9
     *
     * After loading script.js, the following global functions must exist and
     * have the same parameter count (Function.length) as the baseline:
     *   switchTab(1), fetchTeams(0), runClassic(0), runAI(0),
     *   loadLotteryMatches(0), loadSmartMatches(0)
     */
    loadRecordPanel();
    stubFetch({
      '/api/teams': () => ({ success: true, teams: {}, team_zh: {} }),
      '/api/sync/status': () => ({ success: true }),
    });
    loadAppScript();

    for (const [name, expected] of Object.entries(globalFunctionsBaseline)) {
      const fn = window[name];
      expect(typeof fn).toBe('function');
      expect(fn.length).toBe(expected.length);
    }

    // Additional functions that must exist per spec
    expect(typeof window.loadTeams).toBe('function');
    expect(typeof window.formatTime).toBe('function');
    expect(typeof window.showToast).toBe('function');

    // switchTab specifically must accept 1 parameter
    expect(window.switchTab.length).toBe(1);
  });

  // ─────────────────────────────────────────────────────────────────────────
  // 4. NonRecordTabDomNoMutation
  // ─────────────────────────────────────────────────────────────────────────
  it('NonRecordTabDomNoMutation: switching to non-record tabs does not mutate #panel-record innerHTML', async () => {
    /**
     * Validates: Requirements 3.1, 3.2, 3.3, 3.4
     *
     * After entering the record tab (to let it initialize), switching away to
     * other tabs should not modify #panel-record's innerHTML. When switching
     * back to record, the innerHTML should be the same as before switching away.
     */
    loadRecordPanel();
    stubFetch({
      '/api/teams': () => ({ success: true, teams: {}, team_zh: {} }),
      '/api/sync/status': () => ({ success: true }),
      '/api/accuracy/summary': () => ({
        success: true,
        summary: { total_finished: 0, total_predicted: 0, correct: 0, score_hit: 0, accuracy: 0, avg_goal_error: 0 },
        league_stats: [],
        trend: [],
      }),
      '/api/accuracy/matches': () => ({ success: true, total: 0, page: 1, per_page: 20, matches: [] }),
      '/api/lottery/matches': () => ({ success: true, matches: [] }),
      '/api/upcoming-matches': () => ({ success: true, matches: [], total: 0 }),
    });
    loadAppScript();

    // Enter record tab first to let it initialize
    await triggerSwitchTab('record');
    const panel = document.getElementById('panel-record');
    const baselineHtml = panel.innerHTML;

    // Switch to other tabs
    const otherTabs = ['classic', 'ai', 'smart', 'wc'];
    for (const tab of otherTabs) {
      await triggerSwitchTab(tab);
    }

    // Switch back to record
    await triggerSwitchTab('record');
    const afterHtml = panel.innerHTML;

    // innerHTML should be the same (no mutation from other tab switches)
    expect(afterHtml).toBe(baselineHtml);
  });

  // ─────────────────────────────────────────────────────────────────────────
  // 5. AccuracyApiContractPreservation
  // ─────────────────────────────────────────────────────────────────────────
  it('AccuracyApiContractPreservation: if fetch is called for accuracy endpoints, URLs use only whitelisted params', async () => {
    /**
     * Validates: Requirements 3.5, 3.6
     *
     * When the record tab triggers fetch calls, the URLs for /api/accuracy/*
     * must only contain query parameters from the whitelist: {page, per_page, league, result}.
     * No other parameters are allowed.
     *
     * On unfixed code: record tab doesn't call these APIs (no JS branch), so
     * the assertion is vacuously true (no calls to check).
     * After fix: calls happen and must conform to the whitelist.
     */
    const ALLOWED_PARAMS = new Set(['page', 'per_page', 'league', 'result']);

    loadRecordPanel();
    const fetchMock = stubFetch({
      '/api/teams': () => ({ success: true, teams: {}, team_zh: {} }),
      '/api/sync/status': () => ({ success: true }),
      '/api/accuracy/summary': () => ({
        success: true,
        summary: { total_finished: 5, total_predicted: 5, correct: 3, score_hit: 1, accuracy: 60, avg_goal_error: 0.8 },
        league_stats: [],
        trend: [],
      }),
      '/api/accuracy/matches': () => ({ success: true, total: 0, page: 1, per_page: 20, matches: [] }),
      '/api/lottery/matches': () => ({ success: true, matches: [] }),
      '/api/upcoming-matches': () => ({ success: true, matches: [], total: 0 }),
    });
    loadAppScript();
    await triggerSwitchTab('record');

    // Check all fetch calls to /api/accuracy/matches
    const calls = fetchMock.mock.calls.map(c => (typeof c[0] === 'string' ? c[0] : c[0]?.url));
    const accuracyMatchesCalls = calls.filter(u => u && u.includes('/api/accuracy/matches'));

    // For each call, verify only whitelisted params are used
    for (const url of accuracyMatchesCalls) {
      const qIdx = url.indexOf('?');
      if (qIdx === -1) continue; // no params is fine
      const params = new URLSearchParams(url.slice(qIdx + 1));
      for (const [key] of params) {
        expect(ALLOWED_PARAMS.has(key)).toBe(true);
      }
    }

    // Also verify /api/accuracy/summary has no query params (it's a simple GET)
    const summaryCalls = calls.filter(u => u && u.includes('/api/accuracy/summary'));
    for (const url of summaryCalls) {
      const qIdx = url.indexOf('?');
      if (qIdx !== -1) {
        const params = new URLSearchParams(url.slice(qIdx + 1));
        // summary should have no params, but if it does they must be empty
        expect([...params.keys()].length).toBe(0);
      }
    }
  });

  // ─────────────────────────────────────────────────────────────────────────
  // 6. OtherApiPreservation
  // ─────────────────────────────────────────────────────────────────────────
  it('OtherApiPreservation: loadLotteryMatches and loadSmartMatches functions exist and are callable', () => {
    /**
     * Validates: Requirements 3.2, 3.3
     *
     * Assert that loadLotteryMatches and loadSmartMatches exist as global
     * functions and can be called without throwing (given stubbed fetch).
     * This confirms the fix doesn't break the AI/Smart tab loading functions.
     */
    loadRecordPanel();
    stubFetch({
      '/api/teams': () => ({ success: true, teams: {}, team_zh: {} }),
      '/api/sync/status': () => ({ success: true }),
      '/api/lottery/matches': () => ({ success: true, matches: [] }),
      '/api/upcoming-matches': () => ({ success: true, matches: [], total: 0 }),
    });
    loadAppScript();

    // loadLotteryMatches must exist and be callable
    expect(typeof window.loadLotteryMatches).toBe('function');
    expect(() => window.loadLotteryMatches()).not.toThrow();

    // loadSmartMatches must exist and be callable
    expect(typeof window.loadSmartMatches).toBe('function');
    expect(() => window.loadSmartMatches()).not.toThrow();
  });
});
