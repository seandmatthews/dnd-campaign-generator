/* Tests for the one week-run time contract (ticket 55) on the frontend:
     - the week/forecast poll ceiling is sized from the SERVER's run
       contract (week watchdog x weeks + the queue dead-man cap), never
       pollJob's 31-minute default — the default counted queue time
       against a cap sized for one run and reported a false failure
       while the job sat legally queued;
     - the week run's runJob call passes that explicit timeoutMs (the
       poll call shape, pinned against the source the way the durations
       contract is);
     - pollJob's give-up copy distinguishes QUEUED from RUNNING (the job
       snapshot knows which — a queued job has spent nothing).
   Zero dependencies:
     node studio/tests/js/test_run_timeouts.cjs
   Run by run-tests.py's studio-js-run-timeouts suite (skipped when node
   is absent). */

'use strict';
const path = require('path');
const fs = require('fs');
const assert = require('assert');
const { boot } = require('./minidom.cjs');

const { S } = boot(['sim']);
// pollJob reads the bare global StudioLogTail; the UMD wrapper only sets
// that in a browser (module.exports wins under node), so seed it here
global.StudioLogTail = require('../../static/js/logtail.js');

const sim = fs.readFileSync(
  path.join(__dirname, '..', '..', 'static', 'js', 'sim.js'), 'utf8');

let failed = 0;
function ok(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => console.log(`  ${name}: OK`))
    .catch((e) => { failed += 1; console.log(`  ${name}: FAIL ${e.message}`); });
}

// the server's DEFAULT derived contract (engines.py: 24-POST ceiling x
// 300 s + 600 s margin = 7800; queue dead-man 3 x (7800 + 60) = 23580)
const CONTRACT = { week_timeout_s: 7800, queue_deadline_s: 23580 };
const POLL_DEFAULT_MS = 31 * 60 * 1000;

function jobSnapshot(status) {
  return { id: 'j1', kind: 'sim-week', campaign: 'testvale',
           status: status, log_tail: [], position: 0,
           started: null, result: null, error: null };
}

// pollJob ticks on a 1 s interval; a tiny giveUpAfter rejects on the
// SECOND tick (the first runs before any time passes), so each give-up
// case costs ~1.1 s of wall clock
function pollGiveUp(status) {
  global.fetch = () => Promise.resolve({
    ok: true, status: 200,
    text: () => Promise.resolve(JSON.stringify(jobSnapshot(status)))
  });
  return S.pollJob('j1', null, 80).then(
    () => { throw new Error('the poll must give up, not resolve'); },
    (err) => err);
}

async function main() {
  await ok('weekPollTimeoutMs sizes from the server contract', () => {
    // queue cap + 1 week of (watchdog + a minute of slack) + a minute
    assert.strictEqual(S.weekPollTimeoutMs(CONTRACT, 1),
      (23580 + (7800 + 60) + 60) * 1000);
    // a forecast scales per projected week, the shape the old
    // n x 31 min + 5 had
    assert.strictEqual(S.weekPollTimeoutMs(CONTRACT, 3),
      (23580 + 3 * (7800 + 60) + 60) * 1000);
    return null;
  });

  await ok('the sized ceiling clears the 31-minute poll default', () => {
    assert.ok(S.weekPollTimeoutMs(CONTRACT, 1) > POLL_DEFAULT_MS,
      'a queued-then-running week legally outlasts the default');
    assert.ok(S.weekPollTimeoutMs(CONTRACT, 3)
      > S.weekPollTimeoutMs(CONTRACT, 1),
      'more forecast weeks mean more legal runtime');
    return null;
  });

  await ok('a missing contract degrades to the old sizing, never throws', () => {
    assert.strictEqual(S.weekPollTimeoutMs(null, 1),
      (5 * 60 + (31 * 60 + 60) + 60) * 1000);
    assert.strictEqual(S.weekPollTimeoutMs({}, 1),
      S.weekPollTimeoutMs(null, 1));
    return null;
  });

  await ok('the week runJob passes the explicit run-kind-sized timeoutMs', () => {
    // the poll call shape, pinned against the source (core.js initializes
    // the DOM at load, so the runJob wiring is checked as text — same
    // rule as the durations contract)
    assert.ok(/path: S\.campUrl\('\/simulation\/week'\),[\s\S]*?timeoutMs: S\.weekPollTimeoutMs\(sim\.run_contract, 1\),/.test(sim),
      'the week POST passes timeoutMs sized by the run contract');
    assert.ok(/path: S\.campUrl\('\/simulation\/forecast'\),[\s\S]*?timeoutMs: S\.weekPollTimeoutMs\(contract, n\),/.test(sim),
      'the forecast POST keeps its per-week scaling, now from the contract');
    return null;
  });

  await ok('pollJob give-up says QUEUED — nothing of yours has started', async () => {
    const err = await pollGiveUp('queued');
    assert.strictEqual(err.status, 504);
    assert.ok(/QUEUED/.test(err.error), 'names the state: ' + err.error);
    assert.ok(/nothing of yours has started or been spent/.test(err.error),
      'a queued job has spent nothing: ' + err.error);
    assert.ok(/reopen this tab later/.test(err.error));
    return null;
  });

  await ok('pollJob give-up says RUNNING — the watchdog is the server\'s', async () => {
    const err = await pollGiveUp('running');
    assert.strictEqual(err.status, 504);
    assert.ok(/RUNNING/.test(err.error), 'names the state: ' + err.error);
    assert.ok(/watchdog/.test(err.error),
      'the copy points at the server-side kill, not a silent wedge: '
      + err.error);
    assert.ok(!/QUEUED/.test(err.error),
      'a running job must not read as queued');
    return null;
  });

  console.log(failed ? `\n${failed} run-timeouts test(s) FAILED`
    : '\nall run-timeouts tests passed');
  process.exit(failed ? 1 : 0);
}

main();
