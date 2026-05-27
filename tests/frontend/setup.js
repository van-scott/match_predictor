/**
 * Vitest + jsdom setup for prediction-record-page bug condition tests.
 *
 * Provides global helpers used by exploration tests:
 *   - loadRecordPanel()   — injects the #panel-record fragment (and tab buttons / sibling panels) into document.body
 *   - loadAppScript()     — loads static/script.js into the global scope and dispatches DOMContentLoaded
 *   - stubFetch(routes)   — replaces globalThis.fetch with a router-style mock; unmatched routes return { ok:true, success:false }
 *   - triggerSwitchTab(tab) — calls window.switchTab(tab) and awaits microtasks/timers
 */

import { vi } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, '../..');

const TEMPLATE_PATH = path.join(repoRoot, 'templates/index.html');
const SCRIPT_PATH = path.join(repoRoot, 'static/script.js');

// Cache files once
let cachedTemplate = null;
let cachedScript = null;

function readTemplate() {
  if (cachedTemplate === null) cachedTemplate = fs.readFileSync(TEMPLATE_PATH, 'utf8');
  return cachedTemplate;
}

function readScript() {
  if (cachedScript === null) cachedScript = fs.readFileSync(SCRIPT_PATH, 'utf8');
  return cachedScript;
}

function extractFragment(selector) {
  const html = readTemplate();
  const parser = new DOMParser();
  const doc = parser.parseFromString(html, 'text/html');
  const el = doc.querySelector(selector);
  return el ? el.outerHTML : '';
}

/**
 * Inject the #panel-record fragment plus minimum required sibling DOM (tab buttons + sibling panels)
 * so that switchTab() can operate without throwing on missing nodes.
 */
globalThis.loadRecordPanel = function loadRecordPanel() {
  const panelRecord = extractFragment('#panel-record');

  // Mirror minimal nav + sibling tab-panels so switchTab works.
  const skeleton = `
    <header id="site-header">
      <nav class="nav-tabs">
        <button id="tab-classic" class="tab-btn active"></button>
        <button id="tab-ai" class="tab-btn"></button>
        <button id="tab-smart" class="tab-btn"></button>
        <button id="tab-wc" class="tab-btn"></button>
        <button id="tab-record" class="tab-btn"></button>
      </nav>
    </header>
    <main>
      <div id="panel-classic" class="tab-panel"></div>
      <div id="panel-ai" class="tab-panel hidden">
        <div id="lottery-list"></div>
      </div>
      <div id="panel-smart" class="tab-panel hidden">
        <div id="smart-match-list"></div>
        <div id="smart-match-count"></div>
      </div>
      <div id="panel-wc" class="tab-panel hidden"></div>
      ${panelRecord}
    </main>
    <div id="toast"></div>
  `;
  document.body.innerHTML = skeleton;
};

/**
 * Load static/script.js so that its top-level functions (switchTab, etc.) become global.
 * Uses indirect eval so function declarations attach to the global scope (window in jsdom).
 * Then dispatches DOMContentLoaded so script.js's init handlers run in this test's DOM.
 */
globalThis.loadAppScript = function loadAppScript() {
  const code = readScript();
  // Indirect eval so declarations land in the global scope.
  // eslint-disable-next-line no-eval
  (0, eval)(code);
  // jsdom in vitest setup may have already fired DOMContentLoaded before script.js attached
  // its listeners. Dispatch again so init runs against this test's DOM.
  document.dispatchEvent(new Event('DOMContentLoaded'));
};

/**
 * Replace globalThis.fetch with a vi.fn() that routes by URL prefix.
 *
 * @param {Object<string, (url: string, init?: RequestInit) => any>} routes
 *   Map from URL prefix to a handler returning a Response-like object.
 *   A handler may return one of:
 *     - { ok: boolean, status?: number, json?: () => any, body?: any }
 *     - a plain object body (auto-wrapped into a 200 success response with success:true)
 */
globalThis.stubFetch = function stubFetch(routes) {
  function toResponse(out) {
    if (out && typeof out === 'object' && ('ok' in out || 'status' in out || 'json' in out)) {
      const status = out.status ?? (out.ok === false ? 500 : 200);
      const ok = out.ok ?? (status >= 200 && status < 300);
      const body = out.body !== undefined ? out.body : (out.json ? null : {});
      return {
        ok,
        status,
        json: out.json
          ? out.json
          : async () => body,
      };
    }
    // Treat as JSON body for a 200 OK
    return {
      ok: true,
      status: 200,
      json: async () => out,
    };
  }

  const mock = vi.fn(async (url, init) => {
    const u = typeof url === 'string' ? url : (url?.url ?? String(url));
    const keys = Object.keys(routes);
    // Prefer the longest matching prefix to avoid surprises
    keys.sort((a, b) => b.length - a.length);
    for (const k of keys) {
      if (u.startsWith(k)) {
        const out = await routes[k](u, init);
        return toResponse(out);
      }
    }
    // Default: benign empty success-false so init handlers (fetchTeams, fetchSyncStatus) don't crash.
    return toResponse({ ok: true, status: 200, json: async () => ({ success: false }) });
  });

  globalThis.fetch = mock;
  if (typeof window !== 'undefined') window.fetch = mock;
  return mock;
};

/**
 * Call window.switchTab and wait a couple of microtask/macrotask ticks so that any
 * fetch().then() chains complete before assertions run.
 */
globalThis.triggerSwitchTab = async function triggerSwitchTab(tab) {
  if (typeof window.switchTab !== 'function') {
    throw new Error('window.switchTab is not a function (loadAppScript() was not called yet)');
  }
  window.switchTab(tab);
  // flush microtasks and any setTimeout(0) the loaders may queue
  for (let i = 0; i < 10; i++) {
    await new Promise(r => setTimeout(r, 0));
  }
};
