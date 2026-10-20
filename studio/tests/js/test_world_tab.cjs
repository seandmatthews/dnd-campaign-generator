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

  // ---- the authoring card's vault registration (ticket 45). The default
  // /lore/path stub carries no obsidian_registered: falsy reads as
  // unregistered, which is the first-run state.

  function authoringCard(sec) {
    return sec.querySelectorAll('.authoring-card')[0];
  }

  await ok('an unregistered folder shows the walkthrough and the register button', async () => {
    resetFetch();
    const sec = await renderWorld();
    const card = authoringCard(sec);
    assert.ok(card, 'the authoring card rendered');
    const walk = card.querySelector('.lore-walkthrough');
    assert.ok(walk && !walk.classList.contains('hidden'),
      'the Vault-not-found walkthrough is visible');
    assert.ok(walk.textContent.indexOf('Vault not found') !== -1,
      'the walkthrough names the real first-click error');
    const reg = card.querySelector('.lore-register');
    assert.ok(reg && !reg.disabled, 'the register button is live');
    assert.ok(card.querySelector('.lore-registered-note')
      .classList.contains('hidden'),
      'the registered note stays hidden until registering');
    return null;
  });

  await ok('a registered folder hides the walkthrough — Open just works', async () => {
    resetFetch([{ method: 'GET', match: (u) => /\/lore\/path$/.test(u),
      reply: () => ({ path: 'C:/nowhere/lore',
        obsidian_uri: 'obsidian://x', obsidian_registered: true }) }]);
    const sec = await renderWorld();
    const card = authoringCard(sec);
    assert.ok(card.querySelector('.lore-walkthrough')
      .classList.contains('hidden'),
      'no walkthrough for a folder Obsidian already knows');
    assert.ok(card.querySelector('.lore-open'),
      'the Open link is present');
    return null;
  });

  await ok('clicking Register POSTs once and flips the card to registered', async () => {
    resetFetch([{ method: 'POST', match: (u) => /\/lore\/obsidian$/.test(u),
      reply: () => ({ registered: true, already: false }) }]);
    const sec = await renderWorld();
    const card = authoringCard(sec);
    card.querySelector('.lore-register').click();
    await flush();
    assert.ok(card.querySelector('.lore-walkthrough')
      .classList.contains('hidden'),
      'the walkthrough is gone after registering');
    assert.ok(!card.querySelector('.lore-registered-note')
      .classList.contains('hidden'),
      'the quit-and-reopen note shows');
    const posts = log.filter((e) => e.method === 'POST' &&
      /\/lore\/obsidian$/.test(e.url));
    assert.strictEqual(posts.length, 1, 'exactly one registration POST');
    return null;
  });

  // ---- the durable failed-run notes (ticket 46): a lore job that failed
  // server-side must persist on its panel, from the /jobs snapshot.

  const JOBS_ERROR = { last_finished: {
    'lore-entries': { status: 'error',
      error: 'TimeoutError: The read operation timed out' },
    'lore-missing-pages': { status: 'error',
      error: 'gen failed (rc=1): no JSON object in the model reply' },
  } };

  await ok('a failed lore job persists as a section error on its panel', async () => {
    resetFetch([{ method: 'GET', match: (u) => /\/jobs$/.test(u),
      reply: () => JOBS_ERROR }]);
    const sec = await renderWorld();
    const cards = sec.querySelectorAll('.panel-card');
    const backstory = cards.find((c) =>
      /Generate backstory/.test(c.querySelector('.panel-title')?.textContent || ''));
    const missing = cards.find((c) =>
      /Write the missing pages/.test(c.querySelector('.panel-title')?.textContent || ''));
    const noteA = backstory.querySelector('.section-error-slot .section-error');
    assert.ok(noteA, 'the backstory panel shows the persistent note');
    assert.ok(noteA.textContent.indexOf('backstory generation failed') !== -1,
      'the note names what failed');
    assert.ok(noteA.textContent.indexOf('TimeoutError') !== -1,
      'the note carries the error tail');
    const noteB = missing.querySelector('.section-error-slot .section-error');
    assert.ok(noteB, 'the missing-pages panel shows its own note');
    assert.ok(noteB.textContent.indexOf('missing-pages generation failed') !== -1);
    return null;
  });

  await ok('a finished (non-error) last run shows no section error', async () => {
    resetFetch([{ method: 'GET', match: (u) => /\/jobs$/.test(u),
      reply: () => ({ last_finished: {
        'lore-entries': { status: 'done' } } }) }]);
    const sec = await renderWorld();
    const slot = sec.querySelectorAll('.section-error-slot')
      .find((s) => s.closest('.panel-card') &&
        /Generate backstory/.test(s.closest('.panel-card')
          .querySelector('.panel-title')?.textContent || ''));
    assert.ok(slot && !slot.querySelector('.section-error'),
      'a done run clears the note');
    return null;
  });

  // ---- the table-setup card's scenes half (ticket 54): the picker lists
  // the campaign's site battlemaps; the publish button gates on the
  // companion AND a site; the click carries the picked slug.

  const TABLE_STATUS_OK = { method: 'GET',
    match: (u) => /\/table-setup\/status$/.test(u),
    reply: () => ({ companion_found: true, atlas_plugin: false }) };
  const MAPS_ONE_SITE = { method: 'GET', match: (u) => /\/maps$/.test(u),
    reply: () => ({ cities: [], sites: [
      { slug: 'the-drowned-crypt', label: 'The Drowned Crypt' }] }) };

  function tableCard(sec) {
    return sec.querySelector('.table-setup-card');
  }

  await ok('the scenes picker lists site battlemaps; the button is live', async () => {
    resetFetch([TABLE_STATUS_OK, MAPS_ONE_SITE]);
    const sec = await renderWorld();
    const card = tableCard(sec);
    assert.ok(card, 'the table-setup card rendered');
    const sel = card.querySelector('.table-site');
    assert.strictEqual(sel.options.length, 1, 'one site in the picker');
    assert.strictEqual(sel.value, 'the-drowned-crypt',
      'the first site is the picked one');
    assert.strictEqual(sel.options[0].textContent,
      'The Drowned Crypt (the-drowned-crypt)',
      'the option shows the human label with the slug');
    const btn = card.querySelector('.table-publish-scenes');
    assert.strictEqual(btn.disabled, false,
      'companion + site → the button is live');
    return null;
  });

  await ok('no sites keeps the scene button off with an honest title', async () => {
    resetFetch([TABLE_STATUS_OK,
      { method: 'GET', match: (u) => /\/maps$/.test(u),
        reply: () => ({ cities: [], sites: [] }) }]);
    const sec = await renderWorld();
    const card = tableCard(sec);
    const sel = card.querySelector('.table-site');
    assert.strictEqual(sel.options[0].textContent, 'no site battlemaps yet');
    const btn = card.querySelector('.table-publish-scenes');
    assert.strictEqual(btn.disabled, true);
    assert.ok(btn.title.indexOf('No site battlemaps') !== -1,
      'title: ' + btn.title);
    return null;
  });

  await ok('a missing companion keeps the scene button off too', async () => {
    resetFetch([{ method: 'GET',
        match: (u) => /\/table-setup\/status$/.test(u),
        reply: () => ({ companion_found: false, atlas_plugin: false }) },
      MAPS_ONE_SITE]);
    const sec = await renderWorld();
    const btn = tableCard(sec).querySelector('.table-publish-scenes');
    assert.strictEqual(btn.disabled, true);
    assert.ok(btn.title.indexOf('companion is not bundled') !== -1,
      'title: ' + btn.title);
    return null;
  });

  await ok('clicking Publish site scene POSTs the picked slug and shows stdout', async () => {
    resetFetch([TABLE_STATUS_OK, MAPS_ONE_SITE,
      { method: 'POST',
        match: (u) => /\/table-setup\/publish-scenes$/.test(u),
        reply: () => ({ ok: true, exit_code: 0,
          stdout: 'the-drowned-crypt: copied maps/sites/the-drowned-crypt/... '
            + '-> atlas-vtt/_inbox/the-drowned-crypt.uvtt\n',
          stderr: '' }) }]);
    const sec = await renderWorld();
    const card = tableCard(sec);
    card.querySelector('.table-publish-scenes').click();
    await flush();
    const posts = log.filter((e) => e.method === 'POST' &&
      /\/table-setup\/publish-scenes$/.test(e.url));
    assert.strictEqual(posts.length, 1, 'exactly one scenes POST');
    assert.deepStrictEqual(posts[0].body, { site: 'the-drowned-crypt' },
      'the picked slug rides the body');
    const out = card.querySelector('.table-output');
    assert.ok(!out.classList.contains('hidden'), 'the output shows');
    assert.ok(out.textContent.indexOf('_inbox') !== -1,
      'the publisher stdout renders verbatim');
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
