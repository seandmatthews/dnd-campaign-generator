/* Tests for the session-staging state machines (ticket 29):
     - sim.js's Studio.effectAfterControl / Studio.effectAfterValue — the
       "Now" control must follow the effect kind, and a value foreign to
       the kind must never submit (the old Number('high') || 0 silently
       set a project's progress to 0);
     - sessions.js's Studio.draftSavePlan — a card whose id the open
       draft no longer knows aborts the save (the old silent skip shifted
       every later card's position against the server's positional id
       minting).
   Zero dependencies:
     node studio/tests/js/test_sessions_staging.cjs
   Run by run-tests.py's studio-js-staging suite (skipped when node is
   absent). */

'use strict';
const path = require('path');
const assert = require('assert');

// both scripts are plain IIFEs over window.Studio — stub enough of the
// namespace for them to load (sim.js also registers two document-level
// delegated listeners at load time), then read the pure helpers back
global.window = { Studio: { tabs: {} }, StudioHandoff: {} };
global.document = { addEventListener: function () {} };
require(path.join(__dirname, '..', '..', 'static', 'js', 'sim.js'));
require(path.join(__dirname, '..', '..', 'static', 'js', 'sessions.js'));
const S = global.window.Studio;

let failed = 0;
function ok(name, fn) {
  try { fn(); console.log(`  ${name}: OK`); }
  catch (e) { failed += 1; console.log(`  ${name}: FAIL ${e.message}`); }
}

/* ----- effectAfterControl: the dropdown follows the kind ----- */

const GRADES = ['none', 'low', 'medium', 'high'];
const COUNTER = ['0', '1', '2', '3', '4', '5', '6', '7', '8'];
const resourceRow = (value) =>
  ({ tag: 'select', options: GRADES.slice(), value: value });
const progressRow = (value) =>
  ({ tag: 'select', options: COUNTER.slice(), value: value });

ok('resource -> project progress rebuilds to the 0-8 counter', () => {
  const plan = S.effectAfterControl('set_project_progress', resourceRow('high'));
  assert.strictEqual(plan.rebuild, true);
  assert.deepStrictEqual(plan.control.options, COUNTER);
  assert.strictEqual(plan.control.value, '0');
  assert.strictEqual(plan.reset, true, 'a picked value was displaced');
});

ok('project progress -> resource rebuilds back to the grades', () => {
  const plan = S.effectAfterControl('set_resource', progressRow('4'));
  assert.strictEqual(plan.rebuild, true);
  assert.deepStrictEqual(plan.control.options, GRADES);
  assert.strictEqual(plan.control.value, 'none');
  assert.strictEqual(plan.reset, true);
});

ok('the old bug shape: a non-empty select never survives a kind switch', () => {
  // pre-fix sync() only rebuilt INPUT or EMPTY selects, so both these
  // rows kept the other kind's options; both directions must rebuild
  assert.strictEqual(
    S.effectAfterControl('set_resource', resourceRow('high')).rebuild, false);
  assert.strictEqual(
    S.effectAfterControl('set_project_progress', progressRow('4')).rebuild,
    false);
  assert.strictEqual(
    S.effectAfterControl('set_resource', progressRow('4')).rebuild, true);
  assert.strictEqual(
    S.effectAfterControl('set_project_progress', resourceRow('high')).rebuild,
    true);
});

ok('a fresh row (the template ships an empty select) builds without a reset flag', () => {
  const fresh = { tag: 'select', options: [], value: '' };
  const plan = S.effectAfterControl('set_resource', fresh);
  assert.strictEqual(plan.rebuild, true);
  assert.deepStrictEqual(plan.control.options, GRADES);
  assert.strictEqual(plan.control.value, 'none');
  assert.strictEqual(plan.reset, false, 'nothing was displaced');
});

ok('select -> input and input -> select both rebuild', () => {
  const toField = S.effectAfterControl('set_field', resourceRow('high'));
  assert.strictEqual(toField.rebuild, true);
  assert.strictEqual(toField.control.tag, 'input');
  assert.strictEqual(toField.control.placeholder, 'the new value');
  assert.strictEqual(toField.reset, true);
  const toStatus = S.effectAfterControl('set_project_status', progressRow('2'));
  assert.strictEqual(toStatus.rebuild, true);
  assert.strictEqual(toStatus.control.placeholder, 'active | paused | abandoned');
  const backToResource = S.effectAfterControl('set_resource',
    { tag: 'input', placeholder: 'the new value', value: 'a secret goal' });
  assert.strictEqual(backToResource.rebuild, true);
  assert.strictEqual(backToResource.control.tag, 'select');
  assert.deepStrictEqual(backToResource.control.options, GRADES);
  assert.strictEqual(backToResource.reset, true);
});

ok('re-syncing the same kind keeps the control (selection stays put)', () => {
  const current = resourceRow('high');
  const plan = S.effectAfterControl('set_resource', current);
  assert.strictEqual(plan.rebuild, false);
  assert.strictEqual(plan.control, current,
    'no replacement control when nothing rebuilds');
  assert.strictEqual(plan.reset, false);
});

ok('set_field <-> set_project_status rebuild (different placeholders)', () => {
  const field = { tag: 'input', placeholder: 'the new value', value: 'x' };
  assert.strictEqual(
    S.effectAfterControl('set_project_status', field).rebuild, true);
  const status = { tag: 'input', placeholder: 'active | paused | abandoned', value: 'active' };
  assert.strictEqual(S.effectAfterControl('set_field', status).rebuild, true);
  assert.strictEqual(S.effectAfterControl('set_field', field).rebuild, false);
});

ok('an empty input displacing nothing does not flag the row', () => {
  const plan = S.effectAfterControl('set_project_progress',
    { tag: 'input', placeholder: 'the new value', value: '' });
  assert.strictEqual(plan.rebuild, true);
  assert.strictEqual(plan.reset, false);
});

/* ----- effectAfterValue: a stale value never submits ----- */

ok('a project-progress row can never submit a resource word', () => {
  assert.strictEqual(S.effectAfterValue('set_project_progress', 'high'), null);
  assert.strictEqual(S.effectAfterValue('set_project_progress', 'none'), null);
  assert.strictEqual(S.effectAfterValue('set_project_progress', 'medium'), null);
  assert.strictEqual(S.effectAfterValue('set_project_progress', '9'), null);
  // the valid counter options pass through as submit-ready strings
  COUNTER.forEach((n) =>
    assert.strictEqual(S.effectAfterValue('set_project_progress', n), n));
});

ok('a resource row can never submit 0-8 as a level', () => {
  COUNTER.forEach((n) =>
    assert.strictEqual(S.effectAfterValue('set_resource', n), null));
  assert.strictEqual(S.effectAfterValue('set_resource', '4'), null);
  GRADES.forEach((g) =>
    assert.strictEqual(S.effectAfterValue('set_resource', g), g));
});

ok('free-text kinds pass their string through; unknown kinds never submit', () => {
  assert.strictEqual(S.effectAfterValue('set_field', 'the mint burned'), 'the mint burned');
  assert.strictEqual(S.effectAfterValue('set_project_status', 'paused'), 'paused');
  assert.strictEqual(S.effectAfterValue('set_resource', 4), null,
    'a numeric value is not a grade');
  assert.strictEqual(S.effectAfterValue('made_up_kind', 'high'), null);
});

/* ----- draftSavePlan: the PUT body stays aligned with the minting ----- */

ok('every card serializes in DOM order, ids preserved, new ones as null', () => {
  const cards = [
    { id: 's-101', selected: true, fresh: null,
      edits: { reason: 'the treasury burned' }, confirmed: {}, visibility: undefined },
    { id: null, selected: true, fresh: { kind: 'history_event', text: '' },
      edits: { text: 'the mint reopened' }, confirmed: {}, visibility: undefined },
    { id: 's-102', selected: false, fresh: null, edits: {}, confirmed: {},
      visibility: undefined }
  ];
  const draft = { suggestions: [
    { id: 's-101', kind: 'resource_change', resource: 'coin', after: 'high',
      reason: 'old', status: 'open' },
    { id: 's-102', kind: 'field_change', field: 'goal', after: 'x' }
  ] };
  const plan = S.draftSavePlan(cards, draft.suggestions);
  assert.deepStrictEqual(plan.foreign, []);
  // one body entry per card, positionally aligned with the cards (the
  // server mints the new id at the same index prepareSelection reads it)
  assert.strictEqual(plan.suggestions.length, 3);
  assert.strictEqual(plan.suggestions[0].id, 's-101');
  assert.strictEqual(plan.suggestions[0].reason, 'the treasury burned',
    'edits land on the stored shape');
  assert.strictEqual(plan.suggestions[0].after, 'high',
    'untouched stored fields survive');
  assert.strictEqual(plan.suggestions[1].id, null,
    'a new card carries no id — the server mints it');
  assert.strictEqual(plan.suggestions[1].kind, 'history_event',
    'a new card starts from the blank it was authored from');
  assert.strictEqual(plan.suggestions[2].id, 's-102');
  assert.strictEqual(plan.suggestions[2].selected, false);
});

ok('a foreign card aborts the save instead of silently skipping it', () => {
  const cards = [
    { id: 's-101', selected: true, fresh: null, edits: {}, confirmed: {},
      visibility: undefined },
    { id: 's-ghost', selected: true, fresh: null, edits: {}, confirmed: {},
      visibility: undefined },
    { id: 's-102', selected: true, fresh: null, edits: {}, confirmed: {},
      visibility: undefined }
  ];
  const draft = { suggestions: [{ id: 's-101' }, { id: 's-102' }] };
  const plan = S.draftSavePlan(cards, draft.suggestions);
  assert.deepStrictEqual(plan.foreign, ['s-ghost']);
  assert.strictEqual(plan.suggestions, null,
    'no PUT body when a card is foreign — saving around it would shift ' +
    'every later card against the minted ids');
});

ok('confirmations merge over the stored shape; visibility rides along', () => {
  const cards = [
    { id: 's-201', selected: true, fresh: null, edits: {},
      confirmed: { resource_grade: true },
      visibility: undefined },
    { id: 's-202', selected: true, fresh: null, edits: {},
      confirmed: { knowledge_visibility: true },
      visibility: 'public' }
  ];
  const draft = { suggestions: [
    { id: 's-201', confirmed: { other: 1 } },
    { id: 's-202', visibility: 'dm_only' }
  ] };
  const plan = S.draftSavePlan(cards, draft.suggestions);
  assert.deepStrictEqual(plan.suggestions[0].confirmed,
    { other: 1, resource_grade: true });
  assert.strictEqual(plan.suggestions[1].visibility, 'public');
  assert.deepStrictEqual(plan.suggestions[1].confirmed,
    { knowledge_visibility: true });
});

ok('empty and degenerate inputs behave', () => {
  assert.deepStrictEqual(S.draftSavePlan([], []), { foreign: [], suggestions: [] });
  assert.deepStrictEqual(S.draftSavePlan(null, null), { foreign: [], suggestions: [] });
  const blank = S.draftSavePlan(
    [{ id: null, selected: false, fresh: null, edits: {}, confirmed: {},
       visibility: undefined }], []);
  assert.strictEqual(blank.suggestions[0].id, null);
  assert.strictEqual(blank.suggestions[0].selected, false);
});

if (failed) {
  console.error(`${failed} sessions-staging test(s) failed`);
  process.exit(1);
}
console.log('all sessions-staging tests passed');
