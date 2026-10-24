/* DOM tests for core.js's recovery surfaces (ticket 30):
     - P3-34: a conflict-parked transaction (a crashed publication that
       met an external edit, 409-blocking every publication write) had no
       UI — snapshotBanner checked snapshot states only, and
       POST .../publication/resolve had no frontend caller. The banner
       must render against the transaction shape the server's summary
       actually carries and its Resolve actions must hit the endpoint
       with the right body.
     - P3-35: the queue-strip poll had no sequencing — a slow older
       response could render after a newer snapshot, briefly
       resurrecting a finished job. Like pollJob, one request in the air
       at a time. Zero dependencies:
     node studio/tests/js/test_core_banners.cjs
   Run by run-tests.py's studio-js-banners suite (skipped when node is
   absent). */

'use strict';
const assert = require('assert');
const { boot, newPage, installFetch, flush } = require('./minidom.cjs');

const { S } = boot([]);
S.state.campaign = 'ruravaq';

// the fixture transaction: exactly what publication.transactions() puts
// on the summary for a crashed-then-parked approval (studio/publication.py)
const CONFLICT_TX = {
  transaction: '20261002T1015-approve-7f3a',
  operation: 'review/approve',
  status: 'conflict',
  created: '2026-10-02T10:15:42Z',
  conflicts: ['lore/entities/Kestrel.md'],
  snapshot: null,
  idempotency_key: null,
  auto: false
};

let failed = 0;
function ok(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => console.log(`  ${name}: OK`))
    .catch((e) => { failed += 1; console.log(`  ${name}: FAIL ${e.message}`); });
}

function toasts() {
  return document.querySelectorAll('#toasts .toast').map((t) => t.textContent);
}

/* ----- the conflict banner (P3-34) ----- */

async function main() {
  await ok('a conflict-parked transaction renders a banner naming the tx and the edited file', async () => {
    global.document = newPage();
    const resolveCalls = installFetch([
      { method: 'GET', match: (u) => /\/ruravaq$/.test(u),
        reply: () => ({ publication: { transactions: [CONFLICT_TX] } }) }
    ]);
    const banner = await S.snapshotBanner();
    assert.ok(banner, 'the banner rendered (null before the fix)');
    const host = document.querySelector('.tab-section[data-tab="review"]');
    host.appendChild(banner);
    const conflict = host.querySelector('.conflict-banner');
    assert.ok(conflict, 'the conflict banner is on screen');
    assert.strictEqual(
      host.querySelector('.conflict-tx').textContent,
      CONFLICT_TX.transaction);
    assert.ok(conflict.textContent
      .indexOf('lore/entities/Kestrel.md') !== -1,
      'the externally-edited file is named');
    assert.ok(host.querySelector('.conflict-apply'),
      'the apply decision exists');
    assert.ok(host.querySelector('.conflict-discard'),
      'the discard decision exists');
    assert.strictEqual(resolveCalls.length, 1, 'only the summary was read');
    return null;
  });

  await ok('Apply posts to publication/resolve with the transaction and resolution', async () => {
    global.document = newPage();
    const log = installFetch([
      { method: 'GET', match: (u) => /\/ruravaq$/.test(u),
        reply: () => ({ publication: { transactions: [CONFLICT_TX] } }) },
      { method: 'POST', match: (u) => /\/publication\/resolve$/.test(u),
        reply: () => ({ resolved: CONFLICT_TX.transaction,
          resolution: 'apply' }) }
    ]);
    const banner = await S.snapshotBanner();
    const host = document.querySelector('.tab-section[data-tab="review"]');
    host.appendChild(banner);
    host.querySelector('.conflict-apply').click();
    await flush();
    const call = log.find((e) => e.method === 'POST');
    assert.ok(call, 'the resolve endpoint was called');
    assert.strictEqual(call.url,
      '/api/campaign/ruravaq/publication/resolve');
    assert.deepStrictEqual(call.body,
      { transaction: CONFLICT_TX.transaction, resolution: 'apply' });
    assert.ok(toasts().some((t) => /Conflict resolved/.test(t)),
      'the decision was confirmed on screen');
    return null;
  });

  await ok('Keep my edits resolves with discard, and a failed resolve re-enables the buttons', async () => {
    global.document = newPage();
    installFetch([
      { method: 'GET', match: (u) => /\/ruravaq$/.test(u),
        reply: () => ({ publication: { transactions: [CONFLICT_TX] } }) },
      { method: 'POST', match: (u) => /\/publication\/resolve$/.test(u),
        reply: () => ({ __status: 409,
          error: 'transaction 20261002T1015-approve-7f3a is \'complete\', not a conflict' }) }
    ]);
    const banner = await S.snapshotBanner();
    const host = document.querySelector('.tab-section[data-tab="review"]');
    host.appendChild(banner);
    const discard = host.querySelector('.conflict-discard');
    discard.click();
    await flush();
    const err = toasts().find((t) => /Resolve failed/.test(t));
    assert.ok(err, 'the failure was surfaced');
    assert.ok(/not a conflict/.test(err), 'the server reason is carried');
    assert.strictEqual(discard.disabled, false,
      'a failed decision can be retried');
    assert.strictEqual(
      host.querySelector('.conflict-apply').disabled, false);
    return null;
  });

  await ok('a failed snapshot still gets its retry banner; both conditions coexist', async () => {
    global.document = newPage();
    const broken = Object.assign({}, CONFLICT_TX,
      { status: 'complete', conflicts: [], snapshot: { state: 'failed' } });
    installFetch([
      { method: 'GET', match: (u) => /\/ruravaq$/.test(u),
        reply: () => ({ publication: {
          transactions: [broken, CONFLICT_TX] } }) }
    ]);
    const banner = await S.snapshotBanner();
    const host = document.querySelector('.tab-section[data-tab="review"]');
    host.appendChild(banner);
    assert.ok(host.querySelector('.conflict-banner'),
      'the conflict banner renders');
    assert.ok(host.querySelector('.snapshot-retry'),
      'the snapshot-retry banner still renders');
    return null;
  });

  await ok('a clean summary renders no banner; an unreachable one stays quiet', async () => {
    global.document = newPage();
    installFetch([
      { method: 'GET', match: (u) => /\/ruravaq$/.test(u),
        reply: () => ({ publication: { transactions: [
          { status: 'complete', snapshot: { state: 'committed' } }] } }) }
    ]);
    assert.strictEqual(await S.snapshotBanner(), null,
      'a healthy campaign has no banner');

    global.document = newPage();
    installFetch([
      { method: 'GET', match: (u) => /\/ruravaq$/.test(u),
        reply: () => ({ __reject: 'server down' }) }
    ]);
    assert.strictEqual(await S.snapshotBanner(), null,
      'advisory only: a missing summary never blocks the tab');
    return null;
  });

  /* ----- the git-less degrade (ticket 58): `unavailable` is designed ----- */

  const GITLESS_KEY = 'campaign-studio.gitless-notice.ruravaq';
  const GITLESS_TX = Object.assign({}, CONFLICT_TX,
    { status: 'complete', conflicts: [], snapshot: { state: 'unavailable' } });

  await ok('an unavailable snapshot renders the one-time notice, not a failure banner or a dead Retry', async () => {
    global.document = newPage();
    try { localStorage.removeItem(GITLESS_KEY); } catch (_) {}
    installFetch([
      { method: 'GET', match: (u) => /\/ruravaq$/.test(u),
        reply: () => ({ publication: { transactions: [GITLESS_TX] } }) }
    ]);
    const banner = await S.snapshotBanner();
    assert.ok(banner, 'something rendered for the gitless state');
    const host = document.querySelector('.tab-section[data-tab="review"]');
    host.appendChild(banner);
    assert.ok(host.querySelector('.gitless-notice'),
      'the informational notice rendered');
    assert.strictEqual(host.querySelector('.snapshot-retry'), null,
      'NO retry banner: unavailable can never be retried into success');
    assert.ok(host.querySelector('.gitless-notice').textContent
      .indexOf('undo') !== -1,
      'the notice names what is missing (the undo history)');
    assert.ok(host.querySelector('.gitless-notice').textContent
      .indexOf('Back up') !== -1,
      'the notice points at the backup story');
    return null;
  });

  await ok('the gitless notice is one-time per campaign: a later render stays quiet', async () => {
    global.document = newPage();
    // the previous case rendered once, which set the flag — no re-render
    installFetch([
      { method: 'GET', match: (u) => /\/ruravaq$/.test(u),
        reply: () => ({ publication: { transactions: [GITLESS_TX] } }) }
    ]);
    const banner = await S.snapshotBanner();
    assert.strictEqual(banner, null,
      'the notice never becomes a permanent banner');
    try { localStorage.removeItem(GITLESS_KEY); } catch (_) {}
    return null;
  });

  await ok('a failed snapshot keeps its retry banner beside the gitless notice, and conflicts coexist', async () => {
    global.document = newPage();
    try { localStorage.removeItem(GITLESS_KEY); } catch (_) {}
    const failed = Object.assign({}, CONFLICT_TX,
      { status: 'complete', conflicts: [], snapshot: { state: 'failed' } });
    installFetch([
      { method: 'GET', match: (u) => /\/ruravaq$/.test(u),
        reply: () => ({ publication: { transactions: [
          failed, GITLESS_TX, CONFLICT_TX] } }) }
    ]);
    const banner = await S.snapshotBanner();
    const host = document.querySelector('.tab-section[data-tab="review"]');
    host.appendChild(banner);
    assert.ok(host.querySelector('.snapshot-retry'),
      'a genuinely FAILED snapshot still gets its retry banner');
    assert.ok(host.querySelector('.gitless-notice'),
      'the gitless notice renders for the unavailable one');
    assert.ok(host.querySelector('.conflict-banner'),
      'the conflict banner is unaffected');
    try { localStorage.removeItem(GITLESS_KEY); } catch (_) {}
    return null;
  });

  await ok('a retry that comes back unavailable says the designed-degrade copy, not an error', async () => {
    global.document = newPage();
    installFetch([
      { method: 'POST', match: (u) => /\/snapshot$/.test(u),
        reply: () => ({ snapshot: { state: 'unavailable', commit: null } }) }
    ]);
    await S.retrySnapshot();
    const said = toasts();
    assert.ok(said.some((t) => /No undo trail on this install/.test(t)),
      'the unavailable retry toast names the real situation');
    assert.ok(!said.some((t) => /still unavailable/i.test(t)),
      'the old "Snapshot still unavailable" error copy is gone');
    return null;
  });

  /* ----- the queue-strip poll sequencing (P3-35) ----- */

  await ok('no second request while one is in the air, and snapshots render in request order', async () => {
    global.document = newPage();
    const pending = [];
    const log = installFetch([
      { method: 'GET', match: (u) => /\/jobs$/.test(u),
        reply: () => new Promise((resolve) => pending.push(resolve)) }
    ]);
    S.pollQueueStrip();
    assert.strictEqual(log.length, 1, 'the first poll sent one request');
    S.pollQueueStrip();
    S.pollQueueStrip();
    assert.strictEqual(log.length, 1,
      'ticks while a response is pending send nothing — the guard');
    pending.shift()({ running: { id: 'j-1', kind: 'week' },
      running_estimate: { low_s: 480, high_s: 900 }, queued: [
        { id: 'j-2', kind: 'forecast', position: 1 }] });
    await flush();
    const strip = document.querySelector('.queue-strip');
    assert.ok(strip, 'the strip rendered');
    assert.ok(strip.textContent.indexOf('Running: week') !== -1);
    assert.ok(strip.textContent.indexOf('Queued: forecast') !== -1);
    assert.ok(strip.textContent.indexOf('position 1') !== -1);

    // the finished-job snapshot: only after the NEXT request (which could
    // not start until the previous one landed) — an older response can
    // never overwrite a newer render
    S.pollQueueStrip();
    assert.strictEqual(log.length, 2, 'the next tick sent exactly one');
    S.pollQueueStrip();
    assert.strictEqual(log.length, 2, 'still guarded while pending');
    pending.shift()({ running: null, queued: [] });
    await flush();
    assert.strictEqual(document.querySelector('.queue-strip'), null,
      'the strip cleared for the idle snapshot');
    return null;
  });

  await ok('with no campaign selected the poll renders nothing and sends nothing', async () => {
    global.document = newPage();
    const log = installFetch([]);
    S.state.campaign = null;
    try {
      S.pollQueueStrip();
      await flush();
      assert.strictEqual(log.length, 0, 'no request without a campaign');
      assert.strictEqual(document.querySelector('.queue-strip'), null);
    } finally {
      S.state.campaign = 'ruravaq';
    }
    return null;
  });

  /* ----- cancelled jobs drop off the strip (ticket 39a) ----- */

  await ok('a cancelled-while-queued job is not rendered as still queued', async () => {
    global.document = newPage();
    // the server keeps a cancelled job in the queue's pending list until
    // its turn would come, with status "cancelled" and no position — the
    // strip used to render it as "Queued: map-site (position ?)" for as
    // long as the polls kept coming
    installFetch([
      { method: 'GET', match: (u) => /\/jobs$/.test(u),
        reply: () => ({ running: { id: 'j-live', kind: 'week' },
          running_estimate: { low_s: 480, high_s: 900 },
          queued: [
            { id: 'j-dead', kind: 'map-site', status: 'cancelled' },
            { id: 'j-wait', kind: 'forecast', status: 'queued', position: 1 }
          ] }) }
    ]);
    S.pollQueueStrip();
    await flush();
    const strip = document.querySelector('.queue-strip');
    assert.ok(strip, 'the strip rendered');
    assert.ok(strip.textContent.indexOf('Cancelled') === -1,
      'no cancelled label: the toast already told the DM');
    assert.ok(strip.textContent.indexOf('map-site') === -1,
      'the cancelled job is dropped, not mislabeled');
    assert.ok(strip.textContent.indexOf('Queued: forecast') !== -1,
      'the genuinely queued job still shows, with its position');
    assert.ok(strip.textContent.indexOf('position 1') !== -1);
    return null;
  });
}

main().then(() => {
  if (failed) {
    console.error(`${failed} core-banners test(s) failed`);
    process.exit(1);
  }
  console.log('all core-banners tests passed');
});
