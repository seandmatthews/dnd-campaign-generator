/* Tests for js/esc.js — the escaping rule the whole frontend leans on.
   Zero dependencies:  node studio/tests/js/test_esc.cjs
   Run by run-tests.py's studio-js suite (skipped when node is absent). */

'use strict';
const assert = require('assert');
const { esc } = require('../../static/js/esc.js');

let failed = 0;
function ok(name, fn) {
  try { fn(); console.log(`  ${name}: OK`); }
  catch (e) { failed += 1; console.log(`  ${name}: FAIL ${e.message}`); }
}

ok('every dangerous char escapes', () => {
  assert.strictEqual(esc(`<script>alert("x"&'y')</script>`),
    '&lt;script&gt;alert(&quot;x&quot;&amp;&#39;y&#39;)&lt;/script&gt;');
});

ok('an XSS payload round-trips inert', () => {
  const payload = '<img src=x onerror="alert(1)">';
  const out = esc(payload);
  assert.ok(!out.includes('<img'));
  assert.ok(out.includes('&lt;img'));
});

ok('null/undefined become empty strings', () => {
  assert.strictEqual(esc(null), '');
  assert.strictEqual(esc(undefined), '');
});

ok('numbers and plain text pass through', () => {
  assert.strictEqual(esc(42), '42');
  assert.strictEqual(esc('plain text'), 'plain text');
});

ok('escaping is idempotent-safe for reuse in attributes', () => {
  // the studio interpolates esc() output into title="..." attributes; a
  // quote must never close the attribute
  const out = esc('Week 1 — "the" strike');
  assert.ok(!/["]/.test(out.replace(/&quot;/g, '')));
  assert.ok(out.includes('&quot;'));
});

if (failed) { console.log(`${failed} test(s) failed`); process.exit(1); }
console.log('all esc tests passed');
