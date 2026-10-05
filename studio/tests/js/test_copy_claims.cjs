/* DOM tests for the copy-claims fixes (ticket 39c/39d, ticket 43):
     39c — the Generate-backstory panel claimed "One metered LLM call";
           the run meter logged ~3 calls (generation + audit, a retry can
           add one). The panel must state the honest shape.
     39d — accept/discard/stage confirmations reused the spend header
           "This costs real credits" although accepting spends nothing.
           confirmRun(message, title) now lets the caller name the act;
           the default stays the spend header for the LLM runners.
     43 — the Authoring-lore card promised "the first click on a new
           folder asks Obsidian to add the vault"; Obsidian 1.13.7 shows
           "Vault not found" instead. The card now names the real error
           and the vault-manager registration step, with a static
           first-time hint under the buttons.
   All render the REAL panels through minidom. Zero dependencies:
     node studio/tests/js/test_copy_claims.cjs
   Run by run-tests.py's studio-js-copy-claims suite (skipped when node is
   absent). */

'use strict';
const assert = require('assert');
const { setTimeout: rawSetTimeout } = require('timers');
const { boot, newPage, installFetch, flush } = require('./minidom.cjs');

const { S } = boot(['world']);
S.state.campaign = 'testvale';

const TOTAL = 5; // keep in sync with the ok() calls below
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

/* A test whose promise never settles would otherwise drain node's event
   loop and exit 0 — a silent false green (this actually happened: the
   modal clicks below hung the 39d tests for their whole life until
   ticket 43). The stalled guard turns that into a loud red. */
const stalled = rawSetTimeout(() => {
  console.error(
    `${TOTAL - settled} copy-claims test(s) never settled — failing a ` +
    'hung suite instead of exiting 0 on a drained loop');
  process.exit(1);
}, 15000);

function clickModalAct(act) {
  // confirmRun listens on the overlay and reads ev.target — and minidom
  // does not bubble clicks, so the event must be fired on the overlay
  // with the button as its target
  const btn = document.querySelector(`[data-act="${act}"]`);
  assert.ok(btn, `the modal has a ${act} button`);
  btn.closest('.modal-overlay').fire('click', { target: btn });
}

async function renderWorld() {
  global.document = newPage();
  installFetch([
    { method: 'GET', match: (u) => /\/geography$/.test(u),
      reply: () => ({ meta: { seed: '12345' }, nations: [], burgs: [] }) },
    { method: 'GET', match: (u) => /\/lore$/.test(u),
      reply: () => ({ timeline: [], entities: [], style: {} }) },
    { method: 'GET', match: (u) => /\/quests$/.test(u),
      reply: () => ({ quests: [], findings: [] }) },
    { method: 'GET', match: (u) => /\/lore\/pages\/missing$/.test(u),
      reply: () => ({ targets: [] }) },
    { method: 'GET', match: (u) => /\/lore\/path$/.test(u),
      reply: () => ({ path: 'C:/nowhere/lore', obsidian_uri: 'obsidian://x' }) }
  ]);
  const sec = document.querySelector('.tab-section[data-tab="world"]');
  await S.tabs.world(sec);
  await flush();
  return sec;
}

function backstoryPanel(sec) {
  return sec.querySelectorAll('.panel').find((p) => {
    const t = p.querySelector('.panel-title');
    return t && t.textContent === 'Generate backstory';
  });
}

async function main() {
  await ok('39c: the backstory panel no longer claims one metered call', async () => {
    const sec = await renderWorld();
    const note = backstoryPanel(sec).querySelector('.panel-note').textContent;
    assert.ok(/couple of metered LLM passes/.test(note),
      `note reads: ${note}`);
    assert.ok(!/One metered LLM call/.test(note),
      'the false claim must be gone');
    assert.ok(/a few minutes/.test(note), 'the duration estimate stays');
    return null;
  });

  await ok('39d: an accept confirm states it becomes canon, not that it costs', async () => {
    global.document = newPage();
    const p = S.confirmRun('Accepting publishes the proposed week — exactly once.',
      'This becomes canon — no credits are spent');
    const modalTitle = document.querySelector('.modal-title').textContent;
    clickModalAct('cancel');
    await p;
    assert.strictEqual(modalTitle, 'This becomes canon — no credits are spent');
    assert.ok(!/costs real credits/.test(modalTitle));
    return null;
  });

  await ok('39d: the default header is still the spend header (LLM runners)', async () => {
    global.document = newPage();
    const p = S.confirmRun('Generate pending backstory entries with the LLM?');
    const modalTitle = document.querySelector('.modal-title').textContent;
    clickModalAct('cancel');
    await p;
    assert.strictEqual(modalTitle, 'This costs real credits');
    return null;
  });

  await ok('39d: the non-spending call sites pass their own headers (source pin)', () => {
    const sim = require('fs').readFileSync(
      require('path').join(__dirname, '..', '..', 'static', 'js', 'sim.js'),
      'utf8');
    assert.ok(sim.includes("'This becomes canon — no credits are spent'"),
      'proposal accept names the act');
    assert.ok(sim.includes("'Nothing was ever written'"),
      'discards do not claim a spend');
    assert.ok(sim.includes("'Staging is free — accept later'"),
      'project staging does not claim a spend');
    // every remaining bare confirmRun( in sim.js keeps the spend default,
    // which is correct — they are the forecast/week/goal-review runners
    return null;
  });

  await ok('43: the authoring card describes the real first-click Obsidian behavior', async () => {
    const sec = await renderWorld();
    const card = sec.querySelectorAll('.authoring-card')[0];
    assert.ok(card, 'the authoring card renders');
    const text = card.textContent;
    // the promise Obsidian 1.13.7 does not keep must be gone
    assert.ok(!/asks Obsidian to add the vault/.test(text),
      'the "asks Obsidian to add the vault" claim must be gone');
    // the real behavior: a not-yet-registered folder shows Vault not found
    assert.ok(/Vault not found/.test(text),
      `the honest first-click error is named — card reads: ${text}`);
    // the registration path, in the order a newcomer needs it
    assert.ok(/Open folder as vault/.test(text),
      'the vault-manager registration step is named');
    // and the walkthrough sits under the buttons, carrying the same guidance
    // (ticket 45 renamed it and added the one-click Register beside it)
    const hint = card.querySelector('.lore-walkthrough');
    assert.ok(hint, 'the walkthrough renders while the folder is unregistered');
    const hintText = hint.textContent;
    assert.ok(/Vault not found/.test(hintText) &&
              /Open folder as vault/.test(hintText) &&
              /Copy button/.test(hintText),
      `the hint itself carries the recovery guidance — reads: ${hintText}`);
    assert.ok(card.querySelector('.lore-register'),
      'the one-click register button is beside the guidance');
    return null;
  });
}

main().then(() => {
  clearTimeout(stalled);
  if (failed) {
    console.error(`${failed} copy-claims test(s) failed`);
    process.exit(1);
  }
  if (settled !== TOTAL) {
    console.error(`only ${settled}/${TOTAL} copy-claims tests ran`);
    process.exit(1);
  }
  console.log('all copy-claims tests passed');
});
