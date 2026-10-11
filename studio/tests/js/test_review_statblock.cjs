/* DOM tests for the Review tab's statblock-fence kind (ticket 49): a
   pending fence entry renders as its own kind — statblock pill, the target
   page as the title, the fence text as the card body — with the same
   approve/delete buttons every queue item carries. Zero dependencies:
     node studio/tests/js/test_review_statblock.cjs
   Run by run-tests.py's studio-js-review-statblock suite (skipped when
   node is absent). */

'use strict';
const assert = require('assert');
const { boot, newPage, installFetch, flush } = require('./minidom.cjs');

const { S } = boot(['review']);
S.state.campaign = 'testvale';

const payload = {
  items: [
    { kind: 'statblock', file: 'lore/pending-statblocks-1.yaml', id: 'sb-1',
      source: 'authored', title: 'Nyx the Ferryman.md',
      body: '```statblock\nhearts: 4\n```' },
  ],
  notes: [],
  files: [{ file: 'lore/pending-statblocks-1.yaml', kind: 'lore-entry',
            entries: 1, findings: 0, revision: 'rev1' }],
};

async function renderReview(p) {
  global.document = newPage();
  installFetch([
    { method: 'GET', match: (u) => /\/review$/.test(u), reply: () => p },
    { method: 'GET', match: (u) => /\/lore\/path$/.test(u),
      reply: () => ({ path: 'C:/nowhere/lore' }) },
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
  await ok('a fence entry renders: statblock pill, target title, fence body',
    async () => {
      const sec = await renderReview(payload);
      const card = sec.querySelector('.review-card');
      assert.ok(card, 'the card renders');
      const pill = card.querySelector('.pill');
      assert.ok(pill && pill.textContent === 'statblock',
        `the pill names the kind (got ${pill && pill.textContent})`);
      assert.ok(pill.className.includes('kind-statblock'),
        'the pill carries its own class');
      const title = card.querySelector('.review-title');
      assert.ok(title && title.textContent === 'Nyx the Ferryman.md',
        `the target page is the title (got ${title && title.textContent})`);
      const body = card.querySelector('.review-body');
      assert.ok(body && body.textContent.includes('hearts: 4'),
        'the fence text is the card body');
      assert.ok(card.querySelector('.review-approve'),
        'approve is wired like every queue item');
      return null;
    });
}

main().then(() => {
  if (failed) {
    console.error(`${failed} review-statblock test(s) failed`);
    process.exit(1);
  }
  console.log('all review-statblock tests passed');
});
