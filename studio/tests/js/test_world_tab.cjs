/* DOM tests for the World tab's entities table (ticket 30 / P2-6):
     normalizeEntity used to rebuild each entity WITHOUT the `file` field,
     so entitiesTable keyed its row styling and click-to-open handler on a
     value that was always undefined — the page-open feature was
     unreachable. These tests load the real world.js through the minidom
   shim and prove the rows are styled clickable and actually open the
   page view. Zero dependencies:
     node studio/tests/js/test_world_tab.cjs
   Run by run-tests.py's studio-js-world suite (skipped when node is
   absent). */

'use strict';
const assert = require('assert');
const { boot, newPage, installFetch, flush } = require('./minidom.cjs');

const { S } = boot(['world']);
S.state.campaign = 'ruravaq';

const PAGE = { // what GET /lore/pages/Kestrel answers
  name: 'Kestrel', kind: 'faction', body: 'She runs **the Eyrie**.',
  aliases: ['the spymistress'], backlinks: []
};
const ENTITIES = [
  { name: 'Kestrel', kind: 'faction', note: 'the spymistress', aliases: [],
    file: 'lore/entities/Kestrel.md' },
  { name: 'The Mint', kind: 'place', note: 'burned', aliases: [],
    file: 'lore/entities/The Mint.md' },
  { name: 'The Idea Of A Mint', kind: 'concept', note: 'no page', aliases: [] }
];

let log;
function resetFetch(overrides) {
  log = installFetch((overrides || []).concat([
    { method: 'GET', match: (u) => /\/quests$/.test(u),
      reply: () => ({ quests: [], findings: [] }) },
    { method: 'GET', match: (u) => /\/lore\/pages\/missing$/.test(u),
      reply: () => ({ targets: [] }) },
    { method: 'GET', match: (u) => /\/lore\/path$/.test(u),
      reply: () => ({ path: 'C:/nowhere/lore', obsidian_uri: 'obsidian://x' }) },
    { method: 'GET', match: (u) => /\/geography$/.test(u),
      reply: () => ({ nations: [], burgs: [] }) },
    { method: 'GET', match: (u) => /\/lore\/pages\/The%20Mint$/.test(u),
      reply: () => ({ name: 'The Mint', kind: 'place', body: 'ash' }) },
    { method: 'GET', match: (u) => /\/lore\/pages\/Kestrel$/.test(u),
      reply: () => PAGE },
    { method: 'GET', match: (u) => /\/lore$/.test(u),
      reply: () => ({ timeline: [], entities: ENTITIES, style: {} }) }
  ]));
}

let failed = 0;
function ok(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => console.log(`  ${name}: OK`))
    .catch((e) => { failed += 1; console.log(`  ${name}: FAIL ${e.message}`); });
}

async function renderWorld() {
  global.document = newPage();
  const sec = document.querySelector('.tab-section[data-tab="world"]');
  await S.tabs.world(sec);
  await flush();
  return sec;
}

// the Entities panel specifically (the geography digest renders its own
// .data-table tables with placeholder rows)
function entitiesPanel(sec) {
  return sec.querySelectorAll('.panel').find((p) => {
    const t = p.querySelector('.panel-title');
    return t && t.textContent === 'Entities';
  });
}

async function main() {
  await ok('normalizeEntities carries file through all three store shapes', () => {
    const list = S.normalizeEntities([{ name: 'A', file: 'lore/entities/A.md' }]);
    assert.strictEqual(list[0].file, 'lore/entities/A.md');
    const keyed = S.normalizeEntities({ B: { kind: 'faction',
      file: 'lore/entities/B.md' } });
    assert.strictEqual(keyed[0].file, 'lore/entities/B.md');
    const grouped = S.normalizeEntities({ faction: [
      { name: 'C', file: 'lore/entities/C.md' }] });
    assert.strictEqual(grouped[0].file, 'lore/entities/C.md',
      'the kind-grouped shape must keep the page too');
    assert.strictEqual(S.normalizeEntities([{ name: 'D' }])[0].file, null,
      'an entity with no backing page has no file, not undefined');
  });

  await ok('entities with backing pages get the clickable row class; others do not', async () => {
    resetFetch();
    const sec = await renderWorld();
    const rows = entitiesPanel(sec).querySelectorAll('tbody tr');
    assert.strictEqual(rows.length, 3, 'one row per entity');
    const clickable = entitiesPanel(sec).querySelectorAll('tr.entity-row');
    assert.strictEqual(clickable.length, 2,
      'Kestrel and The Mint are page-backed; The Idea Of A Mint is not');
    assert.strictEqual(clickable[0].getAttribute('title'), 'Open page');
    assert.strictEqual(rows[2].classList.contains('entity-row'), false);
    return null;
  });

  await ok('clicking a page-backed row opens the page view with its body', async () => {
    resetFetch();
    const sec = await renderWorld();
    entitiesPanel(sec).querySelectorAll('tr.entity-row')[0].click();
    await flush();
    const view = sec.querySelector('.entity-page-host .entity-page');
    assert.ok(view, 'the page view rendered into the entities panel');
    const title = view.querySelector('.entity-page-title');
    assert.strictEqual(title.textContent, 'Kestrel');
    assert.ok(view.querySelector('.entity-page-body').innerHTML
      .indexOf('<strong>the Eyrie</strong>') !== -1,
      'the markdown body rendered');
    assert.ok(view.textContent.indexOf('Nothing links here yet.') !== -1,
      'the (empty) backlinks block rendered');
    assert.ok(log.some((e) => e.method === 'GET' &&
      /\/lore\/pages\/Kestrel$/.test(e.url)),
      'the page was fetched by its stem');
    return null;
  });

  await ok('a row without a page neither styles nor fetches', async () => {
    resetFetch();
    const sec = await renderWorld();
    const before = log.length;
    entitiesPanel(sec).querySelectorAll('tbody tr')[2].click();
    await flush();
    assert.strictEqual(sec.querySelector('.entity-page'), null,
      'no page view opened');
    assert.strictEqual(log.length, before,
      'no page fetch went out for the page-less entity');
    return null;
  });

  await ok('a page fetch failure shows the error panel inside the page view', async () => {
    resetFetch([{ method: 'GET',
      match: (u) => /\/lore\/pages\/Kestrel$/.test(u),
      reply: () => ({ __status: 500, error: 'the vault is unreadable' }) }]);
    const sec = await renderWorld();
    entitiesPanel(sec).querySelectorAll('tr.entity-row')[0].click();
    await flush();
    const panel = sec.querySelector('.entity-page .error-panel');
    assert.ok(panel, 'the error panel rendered in place of the body');
    assert.ok(panel.textContent.indexOf('the vault is unreadable') !== -1);
    return null;
  });
}

main().then(() => {
  if (failed) {
    console.error(`${failed} world-tab test(s) failed`);
    process.exit(1);
  }
  console.log('all world-tab tests passed');
});
