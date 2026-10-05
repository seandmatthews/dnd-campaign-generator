/* DOM tests for the Review tab's approval-order note (ticket 46): both
   draft kinds pin the lore/entities membership fingerprint, and only
   approving PAGE drafts changes it — which stales every pending entry
   draft (the guard refuses; the paid generation is lost). Entry drafts
   write the timeline (a feed, not a pin), so the safe order is
   deterministic: entries first, page drafts last. The note renders only
   when both kinds pend. Zero dependencies:
     node studio/tests/js/test_review_order.cjs
   Run by run-tests.py's studio-js-review-order suite (skipped when node
   is absent). */

'use strict';
const assert = require('assert');
const { boot, newPage, installFetch, flush } = require('./minidom.cjs');

const { S } = boot(['review']);
S.state.campaign = 'testvale';

function reviewPayload(withEntries, withPages) {
  const items = [];
  if (withEntries) {
    items.push(
      { kind: 'lore-entry', file: 'lore/pending-entries-1.yaml', id: 'e1',
        week: -6, name: 'The Charterhouse Called Its Paper',
        text: 'The Charterhouse called the arrears.' },
      { kind: 'finding', file: 'lore/pending-entries-1.yaml', id: 'f1',
        rule: 'vale/skipped', note: 'vale not installed — taste layer not run' });
  }
  if (withPages) {
    items.push(
      { kind: 'lore-page', file: 'lore/pending-pages-1.yaml', id: 'page-1',
        name: 'The Salt King', text: 'The old landing watches the water.',
        new_names: [] },
      { kind: 'finding', file: 'lore/pending-pages-1.yaml', id: 'f2',
        rule: 'links', note: 'advisory' });
  }
  return { items, notes: [], files: [] };
}

async function renderReview(payload) {
  global.document = newPage();
  installFetch([
    { method: 'GET', match: (u) => /\/review$/.test(u),
      reply: () => payload },
    { method: 'GET', match: (u) => /\/lore\/path$/.test(u),
      reply: () => ({ path: 'C:/nowhere/lore', obsidian_uri: 'obsidian://x' }) },
    { method: 'GET', match: (u) => /\/publication\/snapshot$/.test(u),
      reply: () => ({}) },
  ]);
  const sec = document.querySelector('.tab-section[data-tab="review"]');
  await S.tabs.review(sec);
  await flush();
  return sec;
}

let failed = 0;
function ok(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => console.log(`  ${name}: OK`))
    .catch((e) => { failed += 1; console.log(`  ${name}: FAIL ${e.message}`); });
}

async function main() {
  await ok('both kinds pending: the order note renders, entries first', async () => {
    const sec = await renderReview(reviewPayload(true, true));
    const note = sec.querySelector('.review-order-note');
    assert.ok(note, 'the order note renders');
    const t = note.textContent;
    assert.ok(/Approve the entry\s+drafts first/.test(t),
      'the note leads with the safe order');
    assert.ok(/stales every pending entry draft/.test(t),
      'the note says what page-draft approval costs');
    assert.ok(/never stale the page drafts/.test(t),
      'the note says why the order is safe');
    const groups = sec.querySelectorAll('.review-group');
    assert.strictEqual(groups.length, 2, 'both groups still render');
    return null;
  });

  await ok('entries only: no order note', async () => {
    const sec = await renderReview(reviewPayload(true, false));
    assert.strictEqual(sec.querySelector('.review-order-note'), null,
      'nothing to order');
    return null;
  });

  await ok('page drafts only: no order note', async () => {
    const sec = await renderReview(reviewPayload(false, true));
    assert.strictEqual(sec.querySelector('.review-order-note'), null,
      'nothing to order');
    return null;
  });
}

main().then(() => {
  if (failed) {
    console.error(`${failed} review-order test(s) failed`);
    process.exit(1);
  }
  console.log('all review-order tests passed');
});
