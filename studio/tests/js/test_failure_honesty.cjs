/* DOM tests for the frontend failure-honesty fixes (ticket 63):
     - a failed week/forecast/goal-review/extract run persists as a
       section error on its tab, read from the campaign-jobs snapshot's
       last_finished record — the maps/world pattern the sim and sessions
       tabs now join. Navigation and reloads cannot lose it; a later
       finished run of the kind clears it.
     - a refused cancel (409 "already running") toasts "nothing was
       cancelled" and re-enables the button — the old swallow let the job
       spend to completion looking cancelled.
     - double-click cannot fire a second extract/record/save POST.
     - "Suggest options" confirms before it spends.
     - the copy claims: a lost job names the restart and the lost spend
       (never "it may still finish" again); the extract toast points at
       the queue strip; a failed /simulation read renders as a failed
       read, not as "No factions — create the simulation first".
   All drive the REAL tab scripts through minidom. Zero dependencies:
     node studio/tests/js/test_failure_honesty.cjs
   Run by run-tests.py's studio-js-failure-honesty suite (skipped when
   node is absent). */

'use strict';
const assert = require('assert');
const { setTimeout: rawSetTimeout } = require('timers');
const { boot, newPage, installFetch, flush } = require('./minidom.cjs');

const { S } = boot(['sim', 'sessions', 'knowledge', 'maps']);
S.state.campaign = 'testvale';

// pollJob reads the bare global StudioLogTail; the UMD wrapper only sets
// that in a browser, so seed it here (same as test_maps_burg_form.cjs)
global.StudioLogTail = require('../../static/js/logtail.js');
// the maps tab builds images through new Image() — node has none
global.Image = class Image { constructor() { this.alt = ''; } };

const TOTAL = 10; // keep in sync with the ok() calls below
let failed = 0;
let settled = 0;
function ok(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => { settled += 1; console.log(`  ${name}: OK`); })
    .catch((e) => {
      failed += 1; settled += 1;
      console.log(`  ${name}: FAIL ${e.message}`);
    });
}

const stalled = rawSetTimeout(() => {
  console.error(
    `${TOTAL - settled} failure-honesty test(s) never settled — failing a ` +
    'hung suite instead of exiting 0 on a drained loop');
  process.exit(1);
}, 30000);

function toasts() {
  return document.querySelectorAll('#toasts .toast').map((t) => t.textContent);
}

function clickModalAct(act) {
  // confirmRun listens on the overlay and reads ev.target — minidom does
  // not bubble, so fire on the overlay with the button as its target
  const btn = document.querySelector(`[data-act="${act}"]`);
  assert.ok(btn, `the modal has a ${act} button`);
  btn.closest('.modal-overlay').fire('click', { target: btn });
}

/* ----- fixtures: exactly the shapes the server puts on the wire ----- */

// GET /api/campaign/testvale/jobs — the payload shape test_job_queue.py
// pins server-side; these records are what S.renderSectionError reads
const JOBS_FAIL = { campaign: 'testvale', running: null, queued: [],
  running_estimate: null,
  last_finished: {
    'sim-week': { id: 'jw', kind: 'sim-week', status: 'error',
      error: 'EngineError: week 4 aborted — provider timeout after 3 '
           + 'attempts' },
    'goal-review': { id: 'jg', kind: 'goal-review', status: 'error',
      error: 'LLMError: no JSON object in the model reply' },
    'forecast': { id: 'jf', kind: 'forecast', status: 'error',
      error: 'budget exhausted after 2 calls' },
    'session-extract': { id: 'je', kind: 'session-extract', status: 'error',
      error: 'Extraction failed: the model refused the notes' }
  } };

const SIM_STATE = {
  clock: { week: 3, season: 'spring' },
  premise: 'The toll bridge burned the night the wardens raised the toll.',
  factions: [
    { id: 'wardens', name: 'The Bridge Wardens',
      resources: { coin: 'low', muscle: 'medium' } },
    { id: 'ferry', name: 'The Ferry League', resources: {} }
  ],
  places: [], rumors: [], events: [], chronicles: [],
  views_available: [], projects: [],
  run_contract: { week_timeout_s: 3600, queue_deadline_s: 300 }
};

function simRoutes(lastFinished) {
  return [
    { method: 'GET', match: (u) => /\/testvale$/.test(u),
      reply: () => ({ publication: { transactions: [] } }) },
    { method: 'GET', match: (u) => /\/simulation$/.test(u),
      reply: () => SIM_STATE },
    { method: 'GET', match: (u) => /\/simulation\/projects$/.test(u),
      reply: () => ({ projects: [] }) },
    { method: 'GET', match: (u) => /\/jobs$/.test(u),
      reply: () => Object.assign({}, JOBS_FAIL,
        { last_finished: lastFinished || JOBS_FAIL.last_finished }) }
  ];
}

async function renderSim(lastFinished) {
  global.document = newPage();
  installFetch(simRoutes(lastFinished));
  const sec = document.querySelector('.tab-section[data-tab="sim"]');
  await S.tabs.sim(sec);
  await flush();
  return sec;
}

function panelByClass(sec, cls) {
  return sec.querySelectorAll('.panel').find((p) => p.classList.contains(cls));
}

/* ----- 1+2: the durable failed-week note on the Sim tab ----- */

async function main() {
  await ok('a failed week (and forecast, goal review) persists as a section error on the Sim tab', async () => {
    const sec = await renderSim();
    const run = panelByClass(sec, 'run-panel');
    assert.ok(run, 'the week run panel rendered');
    const note = run.querySelector('.section-error-slot .section-error');
    assert.ok(note, 'the run panel shows the persistent note');
    assert.ok(note.textContent.indexOf('The last week run failed') !== -1,
      `the note names what failed — reads: ${note.textContent}`);
    assert.ok(note.textContent.indexOf('provider timeout') !== -1,
      'the note carries the error tail');
    const goals = panelByClass(sec, 'goals-panel');
    assert.ok(/The last goal review failed/.test(
      goals.querySelector('.section-error-slot').textContent),
      'the goal-review panel carries its own note');
    const forecast = panelByClass(sec, 'forecast-card-panel');
    assert.ok(/The last forecast run failed/.test(
      forecast.querySelector('.section-error-slot').textContent),
      'the forecast panel carries its own note');
    return null;
  });

  await ok('navigation cannot lose the week note, and a finished week clears it', async () => {
    // navigation IS a fresh render of the tab: the note lives in the jobs
    // payload, not in page state, so the re-render reads it back
    const sec = await renderSim();
    assert.ok(panelByClass(sec, 'run-panel')
      .querySelector('.section-error-slot .section-error'),
      'the note is up before the navigation');
    const again = await renderSim();
    assert.ok(panelByClass(again, 'run-panel')
      .querySelector('.section-error-slot .section-error'),
      'the note survives navigating away and back');
    const cleared = await renderSim(
      { 'sim-week': { id: 'jw2', kind: 'sim-week', status: 'done' },
        'goal-review': JOBS_FAIL.last_finished['goal-review'],
        'forecast': JOBS_FAIL.last_finished['forecast'] });
    assert.ok(!panelByClass(cleared, 'run-panel')
      .querySelector('.section-error-slot .section-error'),
      'a finished week of the kind clears the note');
    return null;
  });

  /* ----- 3: the failed extract note on the session detail ----- */

  const SESSION_LIST = { sessions: [{ session_id: 's1',
    title: 'The treasury fire', event_week: 3, drafts: [], decisions: [] }] };
  const SESSION_DETAIL = { session: { title: 'The treasury fire',
      event_week: 3, revision: 1 },
    notes: [{ text: 'wardens: The party bullied the toll collector.' }],
    drafts: [{ draft_id: 'd1', revision: 1, suggestions: [] }] };

  async function openSessionDetail() {
    global.document = newPage();
    installFetch(simRoutes().concat([
      { method: 'GET', match: (u) => /\/sessions$/.test(u),
        reply: () => SESSION_LIST },
      { method: 'GET', match: (u) => /\/sessions\/s1$/.test(u),
        reply: () => SESSION_DETAIL }
    ]));
    const sec = document.querySelector('.tab-section[data-tab="sessions"]');
    await S.tabs.sessions(sec);
    await flush();
    sec.querySelector('.ssn-open').click();
    await flush();
    return sec;
  }

  await ok('a failed session extract persists as a section error on the session detail', async () => {
    const sec = await openSessionDetail();
    const review = sec.querySelectorAll('.panel')
      .find((p) => p.classList.contains('ssn-review'));
    assert.ok(review, 'the review panel rendered');
    const note = review.querySelector('.section-error-slot .section-error');
    assert.ok(note, 'the extract failure note rendered from the jobs payload');
    assert.ok(note.textContent.indexOf('The last extract run failed') !== -1,
      `the note names what failed — reads: ${note.textContent}`);
    assert.ok(note.textContent.indexOf('the model refused') !== -1,
      'the note carries the error tail');
    return null;
  });

  /* ----- 4: the refused cancel ----- */

  await ok('a refused cancel (409) toasts "nothing was cancelled" and re-enables the button', async () => {
    global.document = newPage();
    let cancelledAsked = false;
    installFetch([
      { method: 'POST', match: (u) => /\/simulation\/week$/.test(u),
        reply: () => ({ job_id: 'jw' }) },
      { method: 'GET', match: (u) => /\/api\/jobs\/jw$/.test(u),
        reply: () => cancelledAsked
          ? { id: 'jw', kind: 'sim-week', status: 'done', log_tail: [],
              result: {} }
          : { id: 'jw', kind: 'sim-week', status: 'queued', position: 0,
              log_tail: [] } },
      { method: 'POST', match: (u) => /\/cancel$/.test(u),
        reply: () => {
          cancelledAsked = true;
          return { __status: 409,
            error: 'job jw is not queued — it is already running or '
                 + 'finished, so there is nothing to dequeue' };
        } }
    ]);
    const btn = document.createElement('button');
    const errBox = document.createElement('div');
    document.body.appendChild(btn);
    document.body.appendChild(errBox);
    const watch = S.runJob({
      path: '/api/campaign/testvale/simulation/week', body: { confirm: true },
      btn: btn, errBox: errBox, failLabel: 'Week run failed' });
    await flush();
    const banner = errBox.querySelector('.queue-banner');
    assert.ok(banner, 'the queued banner rendered');
    const cancelBtn = banner.querySelector('.queue-cancel');
    cancelBtn.click();
    await flush();
    const said = toasts().find((t) => t.indexOf('Cancel refused') === 0);
    assert.ok(said, 'the refusal was surfaced as a toast');
    assert.ok(/nothing was cancelled/.test(said),
      `the toast says the job was NOT cancelled — reads: ${said}`);
    assert.ok(/already running or finished/.test(said),
      'the toast carries the server reason');
    assert.strictEqual(cancelBtn.disabled, false,
      'the cancel button came back (it used to stick at Cancelling…)');
    assert.strictEqual(cancelBtn.textContent, 'Cancel');
    // let the watch settle (the job is done now) so the poll interval ends
    await watch;
    return null;
  });

  /* ----- 5-7: the double-submit guards ----- */

  await ok('double-click cannot fire a second extract POST', async () => {
    const sec = await openSessionDetail();
    let release;
    const log = installFetch([
      { method: 'POST', match: (u) => /\/extract$/.test(u),
        reply: () => new Promise((resolve) => { release = resolve; }) }
    ]);
    const btn = sec.querySelector('.ssn-extract');
    assert.ok(btn && !btn.disabled, 'the extract button starts enabled');
    btn.click();
    await flush();
    clickModalAct('confirm');
    await flush();
    assert.strictEqual(btn.disabled, true,
      'the button is disabled while the metered call is in flight');
    btn.click();
    await flush();
    const posts = log.filter((e) => e.method === 'POST' &&
      /\/extract$/.test(e.url));
    assert.strictEqual(posts.length, 1,
      'the second click fired nothing — the disabled guard held');
    release({ job_id: 'jx' }); // nothing waits on it; just unblocks the stub
    return null;
  });

  await ok('double-submit cannot fire a second record-fact POST', async () => {
    global.document = newPage();
    let release;
    const log = installFetch([
      { method: 'GET', match: (u) => /\/simulation$/.test(u),
        reply: () => SIM_STATE },
      { method: 'GET', match: (u) => /\/knowledge$/.test(u),
        reply: () => ({ facts: [] }) },
      { method: 'GET', match: (u) => /\/disclosure$/.test(u),
        reply: () => ({ revealed: [] }) },
      { method: 'GET', match: (u) => /\/briefing$/.test(u),
        reply: () => ({ total: 0, shown: 0, omitted: 0, items: [] }) },
      { method: 'POST', match: (u) => /\/knowledge$/.test(u),
        reply: () => new Promise((resolve) => { release = resolve; }) }
    ]);
    const sec = document.querySelector('.tab-section[data-tab="knowledge"]');
    await S.tabs.knowledge(sec);
    await flush();
    const form = sec.querySelector('.fact-form');
    assert.ok(form, 'the record form rendered');
    form.querySelector('.fact-text').value = 'The harbor master lies about the tide.';
    form.fire('submit', { currentTarget: form });
    await flush();
    const btn = form.querySelector('.fact-record');
    assert.strictEqual(btn.disabled, true,
      'Record fact is disabled while the write is in flight');
    form.fire('submit', { currentTarget: form });
    await flush();
    const posts = log.filter((e) => e.method === 'POST' &&
      /\/knowledge$/.test(e.url));
    assert.strictEqual(posts.length, 1,
      'the second submit fired nothing — the disabled guard held');
    release({}); // unblocks the stub; nothing waits on it
    return null;
  });

  await ok('double-click cannot fire a second save-session POST', async () => {
    global.document = newPage();
    let release;
    const log = installFetch([
      { method: 'GET', match: (u) => /\/sessions$/.test(u),
        reply: () => SESSION_LIST },
      { method: 'POST', match: (u) => /\/sessions$/.test(u),
        reply: () => new Promise((resolve) => { release = resolve; }) }
    ]);
    const sec = document.querySelector('.tab-section[data-tab="sessions"]');
    await S.tabs.sessions(sec);
    await flush();
    sec.querySelector('.ssn-new-title').value = 'The treasury fire';
    sec.querySelector('.ssn-new-notes').value = 'The party burned the Mint.';
    const btn = sec.querySelector('.ssn-new-btn');
    btn.click();
    await flush();
    assert.strictEqual(btn.disabled, true,
      'Save session is disabled while the write is in flight');
    btn.click();
    await flush();
    const posts = log.filter((e) => e.method === 'POST' &&
      /\/sessions$/.test(e.url));
    assert.strictEqual(posts.length, 1,
      'the second click fired nothing — one session, not two');
    release({ session_id: 's2' }); // unblocks the stub
    return null;
  });

  /* ----- 8: suggest-options confirms before it spends ----- */

  await ok('suggest-options confirms first; cancelling spends nothing, confirming spends once', async () => {
    global.document = newPage();
    let release;
    const log = installFetch([
      { method: 'GET', match: (u) => /\/maps$/.test(u),
        reply: () => ({ cities: [], sites: [] }) },
      { method: 'GET', match: (u) => /\/geography$/.test(u),
        reply: () => ({ meta: { seed: '12345' }, nations: [], burgs: [] }) },
      { method: 'GET', match: (u) => /\/jobs$/.test(u),
        reply: () => ({ last_finished: {} }) },
      { method: 'POST', match: (u) => /\/world-options$/.test(u),
        reply: () => new Promise((resolve) => { release = resolve; }) }
    ]);
    const sec = document.querySelector('.tab-section[data-tab="maps"]');
    await S.tabs.maps(sec);
    await flush();
    const wrap = sec.querySelectorAll('.form-block').find((b) =>
      b.querySelector('.world-suggest-btn'));
    const premise = wrap.querySelector('.world-premise');
    const suggest = wrap.querySelector('.world-suggest-btn');
    premise.value = 'A frozen northern world: dwarven holds, troll warbands.';
    suggest.click();
    await flush();
    const modal = document.querySelector('.modal-overlay');
    assert.ok(modal, 'the spend confirmation appeared before any POST');
    assert.ok(/costs real credits/.test(
      modal.querySelector('.modal-title').textContent),
      'the modal carries the spend header');
    assert.strictEqual(log.filter((e) => e.method === 'POST').length, 0,
      'no spend happened behind the open modal');
    clickModalAct('cancel');
    await flush();
    assert.strictEqual(log.filter((e) => e.method === 'POST').length, 0,
      'cancelling the confirm spent nothing');
    suggest.click();
    await flush();
    clickModalAct('confirm');
    await flush();
    const posts = log.filter((e) => e.method === 'POST' &&
      /\/world-options$/.test(e.url));
    assert.strictEqual(posts.length, 1,
      'confirming fired exactly one metered call');
    assert.strictEqual(posts[0].body.premise,
      'A frozen northern world: dwarven holds, troll warbands.');
    release({ options: { cultures: 7, states: 4 } }); // unblocks the stub
    return null;
  });

  /* ----- 9: the knowledge read-failure is not a confident diagnosis ----- */

  await ok('a failed /simulation read renders the failure in the visibility field, not "No factions — create the simulation first"', async () => {
    global.document = newPage();
    installFetch([
      { method: 'GET', match: (u) => /\/simulation$/.test(u),
        reply: () => ({ __status: 500,
          error: 'state.yaml could not be read: Permission denied' }) },
      { method: 'GET', match: (u) => /\/knowledge$/.test(u),
        reply: () => ({ facts: [] }) },
      { method: 'GET', match: (u) => /\/disclosure$/.test(u),
        reply: () => ({ revealed: [] }) },
      { method: 'GET', match: (u) => /\/briefing$/.test(u),
        reply: () => ({ total: 0, shown: 0, omitted: 0, items: [] }) }
    ]);
    const sec = document.querySelector('.tab-section[data-tab="knowledge"]');
    await S.tabs.knowledge(sec);
    await flush();
    const vis = sec.querySelector('.fact-vis');
    assert.ok(vis, 'the visibility field rendered');
    assert.ok(/could not be read/.test(vis.textContent),
      `the read failure is named — reads: ${vis.textContent}`);
    assert.ok(/Permission denied/.test(vis.textContent),
      'the server reason is carried');
    assert.ok(!/No factions — create the simulation first/.test(vis.textContent),
      'the confident wrong diagnosis is gone');
    // a healthy read keeps the faction checkboxes (positive control)
    global.document = newPage();
    installFetch([
      { method: 'GET', match: (u) => /\/simulation$/.test(u),
        reply: () => SIM_STATE },
      { method: 'GET', match: (u) => /\/knowledge$/.test(u),
        reply: () => ({ facts: [] }) },
      { method: 'GET', match: (u) => /\/disclosure$/.test(u),
        reply: () => ({ revealed: [] }) },
      { method: 'GET', match: (u) => /\/briefing$/.test(u),
        reply: () => ({ total: 0, shown: 0, omitted: 0, items: [] }) }
    ]);
    const sec2 = document.querySelector('.tab-section[data-tab="knowledge"]');
    await S.tabs.knowledge(sec2);
    await flush();
    const vis2 = sec2.querySelector('.fact-vis');
    assert.ok(vis2.querySelector('.vis-faction'),
      'a healthy read still lists the factions');
    assert.ok(!/could not be read/.test(vis2.textContent),
      'no failure copy on a healthy read');
    return null;
  });

  /* ----- 10: the copy-claims pins (ticket-63 style source assertions) ----- */

  await ok('the shipped sources carry the honest copy and none of the old comfort claims', () => {
    const read = (f) => require('fs').readFileSync(
      require('path').join(__dirname, '..', '..', 'static', 'js', f), 'utf8');
    const core = read('core.js');
    // the lost-contact comfort claim must be gone (api()'s timeout copy
    // "it may still finish. Check before retrying." is a different,
    // honest context and stays)
    assert.ok(!core.includes('it may still finish; reopen'),
      'the lost-contact copy no longer promises the job may finish');
    assert.ok(core.includes('restart wiped'),
      'the unknown-job copy names the restart that wiped the registry');
    assert.ok(core.includes('the spend already happened'),
      'the unknown-job copy says the spend is spent');
    assert.ok(core.includes('nothing was cancelled'),
      'the cancel-refusal toast says the job was NOT cancelled');
    const sessions = read('sessions.js');
    assert.ok(!sessions.includes('watch the job log on the Simulation tab'),
      'the phantom job-log pointer is gone');
    assert.ok(sessions.includes('queue strip'),
      'the extract toast points at the queue strip (the surface that '
      + 'names the job)');
    const maps = read('maps.js');
    assert.ok(/confirmRun/.test(maps),
      'suggest-options goes through the spend confirmation');
    assert.ok(/timeoutMs: 120000/.test(maps),
      'suggest-options caps its wait');
    return null;
  });

}

main().then(() => {
  clearTimeout(stalled);
  if (failed) {
    console.error(`${failed} failure-honesty test(s) failed`);
    process.exit(1);
  }
  if (settled !== TOTAL) {
    console.error(`only ${settled}/${TOTAL} failure-honesty tests ran`);
    process.exit(1);
  }
  console.log('all failure-honesty tests passed');
});
