#!/usr/bin/env node
/**
 * Baseline snapshot collector for the prediction-record-page bugfix.
 *
 * Usage:  node tests/frontend/collect-baselines.js
 *
 * Writes the following JSON files into tests/frontend/baselines/:
 *   - tabs-dom.snapshot.json
 *   - fetch-endpoints.snapshot.json
 *   - css-selectors.snapshot.json
 *   - global-functions.snapshot.json
 *   - accuracy-api-contract.snapshot.json
 *
 * Run this ONCE on the unfixed codebase (and re-run only when the baseline
 * itself needs to change). Commit the snapshot files to the repo as ground
 * truth.
 */

import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

import { parseCssRules, isPrRule } from './lib/css-rules.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, '../..');
const TEMPLATE_PATH = path.join(repoRoot, 'templates/index.html');
const SCRIPT_PATH = path.join(repoRoot, 'static/script.js');
const CSS_PATH = path.join(repoRoot, 'static/css/style.css');
const BASELINE_DIR = path.join(__dirname, 'baselines');

function sha256(s) {
  return crypto.createHash('sha256').update(s).digest('hex');
}

function ensureDir(p) {
  fs.mkdirSync(p, { recursive: true });
}

function writeJson(name, data) {
  const out = path.join(BASELINE_DIR, name);
  fs.writeFileSync(out, JSON.stringify(data, null, 2) + '\n', 'utf8');
  console.log(`  wrote ${path.relative(repoRoot, out)}`);
}

ensureDir(BASELINE_DIR);

// ─────────────────────────────────────────────────────────────────────────
// 1. tabs-dom.snapshot.json
//    Hash the static template body of #panel-{tab} for non-record tabs.
//    We use the template source (no JS executed) so the baseline is stable
//    and immune to fetch timing.
// ─────────────────────────────────────────────────────────────────────────
console.log('Collecting tabs-dom.snapshot.json …');
{
  const html = fs.readFileSync(TEMPLATE_PATH, 'utf8');
  const dom = new JSDOM(html);
  const doc = dom.window.document;
  const tabs = ['classic', 'ai', 'smart', 'wc'];
  const out = {};
  for (const tab of tabs) {
    const el = doc.getElementById(`panel-${tab}`);
    if (!el) throw new Error(`#panel-${tab} not found in template`);
    out[tab] = {
      sha256: sha256(el.outerHTML),
      length: el.outerHTML.length,
    };
  }
  writeJson('tabs-dom.snapshot.json', out);
}

// ─────────────────────────────────────────────────────────────────────────
// 2. fetch-endpoints.snapshot.json
//    Static scan of static/script.js for `fetch('/api/...')` URL paths.
//    We capture the path *prefix* up to the first whitespace / quote /
//    `?` so query strings and dynamic concatenation are normalised.
// ─────────────────────────────────────────────────────────────────────────
console.log('Collecting fetch-endpoints.snapshot.json …');
{
  const src = fs.readFileSync(SCRIPT_PATH, 'utf8');
  // Match: fetch( '/api/...' )  or  fetch("/api/...")  or  fetch(`/api/...`)
  const re = /fetch\s*\(\s*['"`](\/api\/[^'"`)\s,?]+)/g;
  const set = new Set();
  let m;
  while ((m = re.exec(src)) !== null) {
    set.add(m[1]);
  }
  const sorted = [...set].sort();
  writeJson('fetch-endpoints.snapshot.json', {
    endpoints: sorted,
    count: sorted.length,
    source_sha256: sha256(src),
  });
}

// ─────────────────────────────────────────────────────────────────────────
// 3. css-selectors.snapshot.json
//    Tokenise static/css/style.css into rule blocks. For each non-`.pr-*` /
//    non-`#pr-*` rule, record { selector, context, ruleHash }. Order is
//    preserved so we can detect re-orderings as well as content changes.
// ─────────────────────────────────────────────────────────────────────────
console.log('Collecting css-selectors.snapshot.json …');
{
  const css = fs.readFileSync(CSS_PATH, 'utf8');
  const rules = parseCssRules(css);
  const filtered = rules.filter(r => !isPrRule(r.selector, r.context));
  const out = filtered.map(r => ({
    selector: r.selector,
    context: r.context,
    bodyHash: sha256(r.body),
    bodyLength: r.body.length,
  }));
  writeJson('css-selectors.snapshot.json', {
    rules: out,
    total: out.length,
    file_sha256: sha256(css),
    file_length: css.length,
  });
}

// ─────────────────────────────────────────────────────────────────────────
// 4. global-functions.snapshot.json
//    Load static/script.js into a jsdom window, then sample the existence and
//    declared parameter count of the 6 globals we contract on. We only record
//    `exists` + `length` (parameter count); we deliberately do NOT record
//    toString().length because the fix is allowed to add a single `record`
//    branch inside switchTab.
// ─────────────────────────────────────────────────────────────────────────
console.log('Collecting global-functions.snapshot.json …');
{
  const html = fs.readFileSync(TEMPLATE_PATH, 'utf8');
  const dom = new JSDOM(html, { runScripts: 'outside-only' });
  const { window } = dom;
  const code = fs.readFileSync(SCRIPT_PATH, 'utf8');
  // Indirect-eval into the window so top-level function declarations attach.
  window.eval(code);

  const names = [
    'switchTab',
    'fetchTeams',
    'runClassic',
    'runAI',
    'loadLotteryMatches',
    'loadSmartMatches',
  ];
  const out = {};
  for (const name of names) {
    const fn = window[name];
    out[name] = {
      exists: typeof fn === 'function',
      length: typeof fn === 'function' ? fn.length : null,
    };
  }
  writeJson('global-functions.snapshot.json', out);
}

// ─────────────────────────────────────────────────────────────────────────
// 5. accuracy-api-contract.snapshot.json
//    Static contract record for the two accuracy endpoints. The frontend
//    preservation tests assert (a) the endpoint paths in script.js (after
//    fix) still match these URLs, and (b) the field-name sets the frontend
//    consumes are a subset of these. The backend contract itself is a
//    separate concern — this file is the authoritative declaration of what
//    the frontend assumes.
// ─────────────────────────────────────────────────────────────────────────
console.log('Writing accuracy-api-contract.snapshot.json …');
{
  const contract = {
    '/api/accuracy/summary': {
      method: 'GET',
      query_params: [],
      top_level_fields: ['success', 'summary', 'league_stats', 'trend'],
      summary_fields: [
        'total_finished',
        'total_predicted',
        'correct',
        'score_hit',
        'accuracy',
        'avg_goal_error',
      ],
      league_stats_item_fields: [
        'league',
        'total',
        'correct',
        'score_hit',
        'accuracy',
      ],
      trend_item_fields: ['date', 'total', 'correct', 'accuracy'],
      field_types: {
        success: 'boolean',
        summary: 'object',
        league_stats: 'array',
        trend: 'array',
        'summary.total_finished': 'number',
        'summary.total_predicted': 'number',
        'summary.correct': 'number',
        'summary.score_hit': 'number',
        'summary.accuracy': 'number',
        'summary.avg_goal_error': 'number',
        'league_stats[].league': 'string',
        'league_stats[].total': 'number',
        'league_stats[].correct': 'number',
        'league_stats[].score_hit': 'number',
        'league_stats[].accuracy': 'number',
        'trend[].date': 'string',
        'trend[].total': 'number',
        'trend[].correct': 'number',
        'trend[].accuracy': 'number',
      },
    },
    '/api/accuracy/matches': {
      method: 'GET',
      query_params: ['league', 'result', 'page', 'per_page'],
      top_level_fields: ['success', 'total', 'page', 'per_page', 'matches'],
      match_item_fields: [
        'fixture_id',
        'league',
        'home_team',
        'away_team',
        'home_team_cn',
        'away_team_cn',
        'match_time',
        'predicted_result',
        'actual_result',
        'predicted_score',
        'actual_score',
        'predicted_home_goals',
        'predicted_away_goals',
        'actual_home_goals',
        'actual_away_goals',
        'result_correct',
        'score_correct',
        'goal_diff_error',
        'ml_probs',
        'odds',
      ],
      field_types: {
        success: 'boolean',
        total: 'number',
        page: 'number',
        per_page: 'number',
        matches: 'array',
        'matches[].fixture_id': 'number',
        'matches[].league': 'string',
        'matches[].home_team': 'string',
        'matches[].away_team': 'string',
        'matches[].home_team_cn': 'string|null',
        'matches[].away_team_cn': 'string|null',
        'matches[].match_time': 'string|null',
        'matches[].predicted_result': 'string|null',
        'matches[].actual_result': 'string|null',
        'matches[].predicted_score': 'string|null',
        'matches[].actual_score': 'string|null',
        'matches[].predicted_home_goals': 'number|null',
        'matches[].predicted_away_goals': 'number|null',
        'matches[].actual_home_goals': 'number|null',
        'matches[].actual_away_goals': 'number|null',
        'matches[].result_correct': 'boolean|null',
        'matches[].score_correct': 'boolean|null',
        'matches[].goal_diff_error': 'number|null',
        'matches[].ml_probs': 'object|null',
        'matches[].odds': 'object|null',
      },
    },
  };
  writeJson('accuracy-api-contract.snapshot.json', contract);
}

console.log('\nDone. All baselines written to tests/frontend/baselines/');
