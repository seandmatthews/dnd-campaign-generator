/* DOM tests for the Sessions tab's failure surfaces (ticket 30):
     - P2-7: the session click handler cleared the DOM before two
       unguarded awaits — a dead server left a blank tab. The standard
       error panel must render instead.
     - P3-33: prepare's catch set errBox.textContent BEFORE deciding to
       re-render, so the re-render branch (including the draft-revision
       409's "reload and reapply" guidance) wiped its own message. Both
       409 flavors must leave something visible and actionable.
   These load the real sessions.js through the minidom shim and drive
   the actual click handlers against a stubbed fetch. Zero dependencies:
     node studio/tests/js/test_sessions_tab.cjs
   Run by run-tests.py's studio-js-sessions-tab suite (skipped when node
   is absent). */

'use strict';
const assert = require('assert');
const { boot, newPage, installFetch, flush } = require('./minidom.cjs');

const { S } = boot(['sessions']);
S.state.campaign = 'ruravaq';

const SESSION = {
  session_id: 's-1', title: 'The treasury fire', event_week: 3,
  revision: 1, future_reason: null, drafts: [{ draft_id: 'd-1' }],
  decisions: []
};
const DETAIL = {
  session: SESSION,
  notes: [{ text: 'The party burned the Mint warehouse at the east quay.' }],
  drafts: [{
    draft_id: 'd-1', revision: 2, extraction_findings: [],
    suggestions: [{
      id: 's-101', kind: 'history_event', actor: 'world',
      text: 'the mint burned', status: 'open', selected: false,
      validation: [], findings: [],
      evidence: [{ quote: 'The party burned the Mint',
        notes_path: 'sessions/s-1/notes/0001.md',
        line_start: 1, line_end: 1 }]
    }]
  }]
};

let log;
function resetFetch(routes) {
  log = installFetch(routes);
}

let failed = 0;
function ok(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => console.log(`  ${name}: OK`))
    .catch((e) => { failed += 1; console.log(`  ${name}: FAIL ${e.message}`); });
}

async function renderTab() {
  global.document = newPage();
  const sec = document.querySelector('.tab-section[data-tab="sessions"]');
  await S.tabs.sessions(sec);
  await flush();
  return sec;
}

function toasts() {
  return document.querySelectorAll('#toasts .toast').map((t) => t.textContent);
}

/* open the one session in the list and wait for the detail view */
async function openSession(sec) {
  sec.querySelector('.ssn-open').click();
  await flush();
  return sec.querySelector('.ssn-detail');
}

async function prepareChecked(sec) {
  sec.querySelector('.ssn-check').checked = true;
  sec.querySelector('.ssn-prepare').click();
  await flush();
}

async function main() {
  await ok('a session click against a dead server shows the error panel, not a blank tab', async () => {
    resetFetch([
      { method: 'GET', match: (u) => /\/sessions$/.test(u),
        reply: () => ({ sessions: [SESSION] }) },
      { method: 'GET', match: (u) => /\/simulation$/.test(u),
        reply: () => ({ __reject: 'server down' }) }
    ]);
    const sec = await renderTab();
    assert.ok(sec.querySelector('.ssn-open'),
      'the list rendered (the server was up for it)');
    await openSession(sec);
    const panel = sec.querySelector('.error-panel');
    assert.ok(panel,
      'the standard error panel rendered — the tab is not blank');
    assert.ok(panel.textContent.indexOf('Cannot reach the studio server') !== -1,
      'the network failure is named');
    assert.ok(panel.textContent.indexOf('Is the studio server running?') !== -1,
      'the status-0 hint rendered');
    assert.strictEqual(sec.querySelector('.ssn-detail'), null,
      'no half-rendered detail view');
    return null;
  });

  await ok('a missing simulation state (fetch failure) also degrades to the error panel', async () => {
    resetFetch([
      { method: 'GET', match: (u) => /\/sessions$/.test(u),
        reply: () => ({ sessions: [SESSION] }) },
      { method: 'GET', match: (u) => /\/simulation$/.test(u),
        reply: () => ({ __status: 404, error: 'no simulation/state.yaml in this campaign' }) }
    ]);
    const sec = await renderTab();
    await openSession(sec);
    const panel = sec.querySelector('.error-panel');
    assert.ok(panel, 'the error panel rendered');
    assert.ok(panel.textContent
      .indexOf('no simulation/state.yaml in this campaign') !== -1);
    return null;
  });

  await ok('the awaiting-review 409 keeps the detail view, the inline message, and toasts', async () => {
    resetFetch([
      { method: 'GET', match: (u) => /\/sessions$/.test(u),
        reply: () => ({ sessions: [SESSION] }) },
      { method: 'GET', match: (u) => /\/simulation$/.test(u),
        reply: () => ({ factions: [], places: [] }) },
      { method: 'GET', match: (u) => /\/sessions\/s-1$/.test(u),
        reply: () => DETAIL },
      { method: 'PUT', match: (u) => /\/drafts\/d-1$/.test(u),
        reply: () => ({ draft_id: 'd-1', revision: 3,
          suggestions: DETAIL.drafts[0].suggestions }) },
      { method: 'POST', match: (u) => /\/prepare$/.test(u),
        reply: () => ({ __status: 409,
          error: 'a proposal is awaiting review (wk-3) — accept or discard it first; '
            + 'you can return to it and prepare this session afterwards' }) }
    ]);
    const sec = await renderTab();
    const detail = await openSession(sec);
    assert.ok(detail, 'the detail view rendered');
    await prepareChecked(sec);
    assert.ok(sec.querySelector('.ssn-detail'),
      'the detail view survived — no re-render for this flavor');
    const err = sec.querySelector('.ssn-detail .proposal-error');
    assert.ok(err && err.textContent.indexOf('awaiting review') !== -1,
      'the inline box carries the server guidance');
    assert.ok(toasts().some((t) => /already pending/.test(t)),
      'the pointer toast fired');
    return null;
  });

  await ok('the draft-revision 409 leaves a visible, actionable message that survives the re-render', async () => {
    resetFetch([
      { method: 'GET', match: (u) => /\/sessions$/.test(u),
        reply: () => ({ sessions: [SESSION] }) },
      { method: 'GET', match: (u) => /\/simulation$/.test(u),
        reply: () => ({ factions: [], places: [] }) },
      { method: 'GET', match: (u) => /\/sessions\/s-1$/.test(u),
        reply: () => DETAIL },
      { method: 'PUT', match: (u) => /\/drafts\/d-1$/.test(u),
        reply: () => ({ __status: 409,
          error: 'this draft changed since you opened it (another editor or an '
            + 'extraction finished?) — reload and reapply your edits; nothing '
            + 'was written' }) },
      { method: 'POST', match: (u) => /\/prepare$/.test(u),
        reply: () => ({ proposal_id: 'never-reached' }) }
    ]);
    const sec = await renderTab();
    const detail = await openSession(sec);
    assert.ok(detail, 'the detail view rendered');
    await prepareChecked(sec);
    // this flavor re-renders (validations changed): the reload the message
    // tells the DM to do IS the re-render, so the message itself must ride
    // the toast — the old code set errBox first and the re-render wiped it
    assert.strictEqual(sec.querySelector('.ssn-detail'), null,
      'the tab re-rendered to the fresh list');
    assert.ok(sec.querySelector('.ssn-open'),
      'the re-render produced the session list (the reload)');
    const msg = toasts().find((t) => /Prepare failed/.test(t));
    assert.ok(msg, 'a failure toast survived the re-render');
    assert.ok(/reload and reapply/.test(msg),
      'the toast carries the actionable server guidance');
    assert.ok(log.every((e) => !(e.method === 'POST' && /\/prepare$/.test(e.url))),
      'nothing was staged past the failed save');
    return null;
  });

  await ok('a non-409 prepare failure also toasts through the re-render', async () => {
    resetFetch([
      { method: 'GET', match: (u) => /\/sessions$/.test(u),
        reply: () => ({ sessions: [SESSION] }) },
      { method: 'GET', match: (u) => /\/simulation$/.test(u),
        reply: () => ({ factions: [], places: [] }) },
      { method: 'GET', match: (u) => /\/sessions\/s-1$/.test(u),
        reply: () => DETAIL },
      { method: 'PUT', match: (u) => /\/drafts\/d-1$/.test(u),
        reply: () => ({ draft_id: 'd-1', revision: 3,
          suggestions: DETAIL.drafts[0].suggestions }) },
      { method: 'POST', match: (u) => /\/prepare$/.test(u),
        reply: () => ({ __status: 422,
          error: 'suggestion s-101 is blocked: unresolved actor' }) }
    ]);
    const sec = await renderTab();
    await openSession(sec);
    await prepareChecked(sec);
    assert.strictEqual(sec.querySelector('.ssn-detail'), null,
      'the tab re-rendered (validations changed)');
    assert.ok(toasts().some((t) =>
      /Prepare failed/.test(t) && /unresolved actor/.test(t)),
      'the failure toast names the blocker');
    return null;
  });
}

main().then(() => {
  if (failed) {
    console.error(`${failed} sessions-tab test(s) failed`);
    process.exit(1);
  }
  console.log('all sessions-tab tests passed');
});
