/* Tests for Studio.api's opts.timeoutMs (the client wait ceiling added
   with the design-form silent-wedge fix, 2026-10-03): the abort must
   surface as {status: 504, timedOut: true} naming the wait (NOT a fake
   network error), the happy path must stay untouched, and the timer must
   be cleared once the response lands. Zero dependencies:
     node studio/tests/js/test_api_timeout.cjs
   Run by run-tests.py's studio-js-api-timeout suite (skipped when node
   is absent). */

'use strict';
const path = require('path');
const assert = require('assert');

// core.js loads over window.Studio and window.StudioEsc.esc, and binds
// one DOMContentLoaded listener at load — stub all three
global.window = { Studio: {}, StudioEsc: { esc: (s) => String(s) } };
global.document = { addEventListener: function () {} };
require(path.join(__dirname, '..', '..', 'static', 'js', 'core.js'));
const S = global.window.Studio;

let failed = 0;
async function ok(name, fn) {
  try { await fn(); console.log(`  ${name}: OK`); }
  catch (e) { failed += 1; console.log(`  ${name}: FAIL ${e.message}`); }
}

function abortError() {
  const e = new Error('The operation was aborted.');
  e.name = 'AbortError';
  return e;
}

// a fetch that never answers unless the caller's own signal aborts it —
// the provider-wedge shape the ceiling exists for
function hangingFetch() {
  return function (p, init) {
    captured = { path: p, init: init || {} };
    return new Promise((resolve, reject) => {
      if (init && init.signal && init.signal.aborted) { reject(abortError()); return; }
      if (init && init.signal) {
        init.signal.addEventListener('abort', () => reject(abortError()));
      }
    });
  };
}

let captured = null;
const cleared = [];
const realClear = clearTimeout;

async function main() {
  const spyClear = () => {
    cleared.length = 0;
    global.clearTimeout = (t) => { cleared.push(t); return realClear(t); };
  };
  const unspyClear = () => { global.clearTimeout = realClear; };

  await ok('timeoutMs aborts the wait into {status: 504, timedOut}', async () => {
    global.fetch = hangingFetch();
    let err = null;
    try { await S.api('/api/slow', { timeoutMs: 40 }); }
    catch (e) { err = e; }
    assert.ok(err, 'the call must reject');
    assert.strictEqual(err.status, 504, 'status: ' + err.status);
    assert.strictEqual(err.timedOut, true);
    assert.ok(/Gave up waiting/.test(err.error), 'message: ' + err.error);
    assert.ok(/NOT cancelled/.test(err.error),
      'the message says the server-side work survives');
  });

  await ok('a real answer clears the timer (no dangling abort after success)', async () => {
    spyClear();
    global.fetch = () => Promise.resolve({
      ok: true,
      text: async () => JSON.stringify({ a: 1 })
    });
    const data = await S.api('/api/fast', { timeoutMs: 10000 });
    assert.deepStrictEqual(data, { a: 1 });
    assert.strictEqual(captured.init.signal instanceof AbortSignal, true,
      'the signal is attached when timeoutMs is set');
    assert.strictEqual(cleared.length, 1, 'exactly one clearTimeout');
    unspyClear();
  });

  await ok('no timeoutMs: no signal, plain passthrough', async () => {
    captured = null;
    global.fetch = (p, init) => {
      captured = { init: init || {} };
      return Promise.resolve({ ok: true, text: async () => '{"b":2}' });
    };
    const data = await S.api('/api/plain', { method: 'POST', body: { x: 1 } });
    assert.deepStrictEqual(data, { b: 2 });
    assert.strictEqual(captured.init.signal, undefined,
      'callers without timeoutMs must be untouched');
    assert.strictEqual(captured.init.method, 'POST');
    global.clearTimeout = realClear;
  });

  await ok('a plain network error keeps its old shape', async () => {
    global.fetch = () => Promise.reject(new Error('ECONNREFUSED'));
    let err = null;
    try { await S.api('/api/down'); } catch (e) { err = e; }
    assert.strictEqual(err.status, 0);
    assert.ok(/Cannot reach the studio server/.test(err.error));
    global.clearTimeout = realClear;
  });

  console.log(failed ? `\n${failed} test(s) FAILED` : '\nall api-timeout tests passed');
  process.exit(failed ? 1 : 0);
}

main();
