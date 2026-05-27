/**
 * Lightweight CSS rule tokenizer for preservation testing.
 *
 * Walks through CSS source character-by-character, tracking brace depth and
 * skipping strings + comments. For each non-nested rule (or rule nested inside
 * an @media/@supports/@container/@document block), produces an entry:
 *
 *   { selector, body, context }
 *
 * - `selector` is the literal selector text as written (whitespace-normalised).
 * - `body`     is the literal body between matching `{ ... }` (whitespace
 *              preserved; subsequent comparison should hash this).
 * - `context`  is the `@media (...)`-style wrapper chain joined with ` >> `,
 *              empty string when at top-level.
 *
 * Rules whose at-rule has no body (e.g. `@charset`, `@import`) are emitted with
 * `body: ''` and `selector: '@charset "utf-8"'` etc.
 *
 * @keyframes / @font-face / @page are treated as opaque (body kept verbatim).
 */
export function parseCssRules(css) {
  const rules = [];
  let i = 0;
  const n = css.length;

  function skipWsAndComments() {
    while (i < n) {
      const c = css[i];
      if (c === ' ' || c === '\t' || c === '\n' || c === '\r' || c === '\f') {
        i++;
        continue;
      }
      if (c === '/' && css[i + 1] === '*') {
        const end = css.indexOf('*/', i + 2);
        i = end === -1 ? n : end + 2;
        continue;
      }
      return;
    }
  }

  function skipString(quote) {
    i++; // consume opening quote
    while (i < n) {
      const c = css[i];
      if (c === '\\') { i += 2; continue; }
      if (c === quote) { i++; return; }
      i++;
    }
  }

  function readPrelude() {
    // Read selector / at-rule prelude until top-level `{`, `;`, or `}`.
    const start = i;
    let parenDepth = 0;
    while (i < n) {
      const c = css[i];
      if (c === '"' || c === "'") { skipString(c); continue; }
      if (c === '/' && css[i + 1] === '*') {
        const end = css.indexOf('*/', i + 2);
        i = end === -1 ? n : end + 2;
        continue;
      }
      if (c === '(') { parenDepth++; i++; continue; }
      if (c === ')') { parenDepth--; i++; continue; }
      if (parenDepth === 0 && (c === '{' || c === ';' || c === '}')) break;
      i++;
    }
    return css.slice(start, i).trim();
  }

  function readBody() {
    // Read body between matching `{` ... `}`. Caller must consume the opening
    // `{` before calling. Returns body text (excluding the closing `}`),
    // leaves `i` positioned just after the closing `}`.
    const start = i;
    let depth = 1;
    while (i < n && depth > 0) {
      const c = css[i];
      if (c === '"' || c === "'") { skipString(c); continue; }
      if (c === '/' && css[i + 1] === '*') {
        const end = css.indexOf('*/', i + 2);
        i = end === -1 ? n : end + 2;
        continue;
      }
      if (c === '{') depth++;
      else if (c === '}') depth--;
      i++;
    }
    return css.slice(start, i - 1);
  }

  function isNestedAtRule(selector) {
    return /^@(media|supports|container|document)\b/i.test(selector);
  }

  function parse(stopAtClose, context) {
    while (i < n) {
      skipWsAndComments();
      if (i >= n) return;
      if (stopAtClose && css[i] === '}') { i++; return; }

      const sel = readPrelude();
      if (i >= n) {
        if (sel) rules.push({ selector: sel, body: '', context });
        return;
      }

      const ch = css[i];
      if (ch === ';') {
        i++;
        if (sel) rules.push({ selector: sel, body: '', context });
        continue;
      }
      if (ch === '}') {
        // dangling closer; let outer level handle
        return;
      }
      // ch === '{'
      i++; // consume '{'

      if (isNestedAtRule(sel)) {
        const newContext = context ? `${context} >> ${sel}` : sel;
        parse(true, newContext);
      } else {
        const body = readBody();
        rules.push({ selector: sel, body, context });
      }
    }
  }

  parse(false, '');
  return rules;
}

/**
 * Returns true if the given rule's selector group references `.pr-*` or `#pr-*`.
 *
 * The check is conservative: we filter the rule out if ANY token in the
 * selector starts with `.pr-` or `#pr-`. This is sufficient because the bugfix
 * only adds new `.pr-*` rules; it never modifies existing rules to add a
 * `.pr-*` selector to them.
 */
export function isPrRule(selector, context = '') {
  const text = `${context} ${selector}`;
  return /(?:^|[^a-zA-Z0-9_-])(?:\.pr-|#pr-)/.test(text);
}
