/* Ticket 41, pinned in the DOM: the Maps tab's burg picker submits the
   burg's dump id, not the bare name (a generated world can hold two
   same-named burgs in different nations — seed 6161 had two Stormbergs,
   and gen-city refuses ambiguous names), and a FAILED map job renders a
   persistent section error that a reload cannot wipe (it renders from the
   campaign-jobs snapshot's last_finished, cleared by the next finished
   run of the kind). Zero dependencies:
     node studio/tests/js/test_maps_burg_form.cjs
   Run by run-tests.py's studio-js-maps-burg-form suite (skipped when node
   is absent). */

'use strict';
const assert = require('assert');
const { boot, newPage, installFetch, flush } = require('./minidom.cjs');

const { S } = boot(['maps']);
S.state.campaign = 'testvale';

// the maps tab builds hero/artifact images through new Image() — node has
// none, and the tests never need one to actually load
global.Image = class Image { constructor() { this.alt = ''; } };
// pollJob reads the bare global StudioLogTail; the UMD wrapper only sets
// that in a browser (module.exports wins under node), so seed it here
global.StudioLogTail = require('../../static/js/logtail.js');

let failed = 0;
function ok(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => { global.__mapsSettled += 1; console.log(`  ${name}: OK`); })
    .catch((e) => {
      global.__mapsSettled += 1; failed += 1;
      console.log(`  ${name}: FAIL ${e.message}`);
    });
}

// seed 6161's trap: two Stormbergs in different nations, plus one id-less
// burg (hand-written geography never carries ids — the name stays its value)
const GEO = {
  meta: { seed: '6161' },
  nations: [],
  major_settlements: [
    { name: 'Harnost', nation: 'Harnost', population_thousands: 12 },
    { name: 'Stormberg', nation: 'Sildgeria', burg_id: 2,
      population_thousands: 27.4 },
    { name: 'Stormberg', nation: 'Rathgar', burg_id: 266,
      population_thousands: 9.1 }
  ]
};

function renderMaps({ lastFinished }) {
  const posted = [];
  global.document = newPage();
  installFetch([
    { method: 'GET', match: (u) => /\/maps$/.test(u),
      reply: () => ({ cities: [], sites: [] }) },
    { method: 'GET', match: (u) => /\/geography$/.test(u),
      reply: () => GEO },
    { method: 'GET', match: (u) => /\/jobs$/.test(u),
      reply: () => ({ campaign: 'testvale', running: null, queued: [],
                      running_estimate: null,
                      last_finished: lastFinished || {} }) },
    { method: 'POST', match: (u) => /\/maps\/cities$/.test(u),
      reply: (req) => { posted.push(req.body); return { job_id: 'j1' }; } },
    { method: 'GET', match: (u) => /\/api\/jobs\/j1$/.test(u),
      reply: () => ({ id: 'j1', status: 'done', log_tail: [],
                      result: { slug: 'stormberg' } }) }
  ]);
  const sec = document.querySelector('.tab-section[data-tab="maps"]');
  return S.tabs.maps(sec).then(() => flush()).then(() => {
    return { sec, posted };
  });
}

function panelByTitle(sec, title) {
  return sec.querySelectorAll('.panel').find((p) => {
    const t = p.querySelector('.panel-title');
    return t && t.textContent === title;
  });
}

async function main() {
  await ok('the picker submits the burg id; the label and place default stay names', async () => {
    const { sec, posted } = await renderMaps({});
    const select = sec.querySelector('.burg-select');
    assert.ok(select, 'the burg picker rendered');
    const values = select.querySelectorAll('option').map((o) => o.attrs.value);
    // values are ids where geography carries them, names where it does not
    assert.deepStrictEqual(values, ['Harnost', '2', '266'],
      'option values: id-less name fallback, then the two burg ids');
    const texts = select.querySelectorAll('option').map((o) => o.textContent);
    assert.ok(texts[1].includes('Sildgeria') && texts[2].includes('Rathgar'),
      'the human-readable nation stays in the option text');
    assert.ok(/pop /.test(texts[1]) && /\[port\]|pop/.test(texts[1]),
      'the label shape is unchanged');

    // pick the second Stormberg: the place default follows the NAME,
    // the payload carries the id
    select.value = '266';
    select.fire('change');
    const placeIn = sec.querySelector('.burg-place');
    assert.strictEqual(placeIn.value, 'Stormberg',
      'the attach-to-place default resolved id 266 to the name');
    select.closest('form').fire('submit', { preventDefault: () => {} });
    await flush(10);
    assert.ok(posted.some((b) => b.burg === '266' && b.place === 'Stormberg'),
      `the submitted payload carries the id: ${JSON.stringify(posted)}`);
    assert.ok(!posted.some((b) => b.burg === 'Stormberg'),
      'no bare name was submitted');
    return null;
  });

  await ok('a failed map job renders a persistent error in its section', async () => {
    const { sec } = await renderMaps({
      lastFinished: {
        'map-city': { id: 'a1', kind: 'map-city', status: 'error',
          error: 'RuntimeError: gen-city.mjs failed (rc=1): burg ' +
                 '"Stormberg": ambiguous, 2 burgs share the name (ids 2, 266)' },
        'map-site': { id: 'a2', kind: 'map-site', status: 'error',
          error: 'RuntimeError: render failed (rc=1): sharp crashed' }
      }
    });
    const cities = panelByTitle(sec, 'Cities');
    const cityErr = cities.querySelector('.section-error');
    assert.ok(cityErr, 'the Cities section carries the error line');
    assert.ok(/last city map failed/.test(cityErr.textContent),
      'it names what failed: ' + JSON.stringify(cityErr.textContent));
    assert.ok(/ambiguous/.test(cityErr.textContent),
      'the error tail is shown');
    const sites = panelByTitle(sec, 'Battlemap scenes');
    const siteErr = sites.querySelector('.section-error');
    assert.ok(siteErr && /battlemap scene failed/.test(siteErr.textContent),
      'the Battlemap section carries its own error line');
    return null;
  });

  await ok('a finished (or no) last run leaves the sections clean', async () => {
    const { sec } = await renderMaps({
      lastFinished: {
        'map-city': { id: 'b1', kind: 'map-city', status: 'done',
          result: { slug: 'stormberg' } },
        'map-site': { id: 'b2', kind: 'map-site', status: 'cancelled' }
      }
    });
    assert.strictEqual(sec.querySelector('.section-error'), null,
      'done/cancelled last runs render no error');
    return null;
  });
}

global.__mapsSettled = 0;
const TOTAL = 3;
// a test whose promise never settles must fail loudly, not drain the loop
// into a silent exit-0 (the copy-claims suite hung exactly like this)
const stalled = require('timers').setTimeout(() => {
  console.error(`${TOTAL - global.__mapsSettled} maps-burg-form test(s) never settled`);
  process.exit(1);
}, 15000);

main().then(() => {
  clearTimeout(stalled);
  if (failed) {
    console.error(`${failed} maps-burg-form test(s) failed`);
    process.exit(1);
  }
  console.log('all maps-burg-form tests passed');
});
