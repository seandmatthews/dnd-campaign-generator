/* DOM tests for the campaign dropdown order (ticket 39f) and the Help
   panel's export line (ticket 39g):
     39f — options were appended in the API's slug order, so the slug
           "the-cinder-vale" sorted the title "The Cinder Vale" LAST.
           The dropdown now sorts by display title.
     39g — export_campaign.py was CLI-only and no surface named it. The
           Help panel now says the command and where Foundry .uvtt files
           come from.
   Zero dependencies:
     node studio/tests/js/test_campaign_select_order.cjs
   Run by run-tests.py's studio-js-campaign-select-order suite (skipped
   when node is absent). */

'use strict';
const assert = require('assert');
const { boot, newPage, installFetch, flush } = require('./minidom.cjs');

const { S } = boot([]);

let failed = 0;
function ok(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => console.log(`  ${name}: OK`))
    .catch((e) => { failed += 1; console.log(`  ${name}: FAIL ${e.message}`); });
}

// slug order deliberately adversarial: "the-…" first from the API, so slug
// sorting would put its TITLE last; "alpha" proves titles sort first
const CAMPAIGNS = [
  { name: 'the-cinder-vale', title: 'The Cinder Vale' },
  { name: 'reliability-probe', title: 'Reliability Probe' },
  { name: 'ashen-marches', title: 'Ashen Marches' },
  { name: 'alpha', title: 'Alpha Camp' }
];

async function refresh(prefer) {
  global.document = newPage();
  // newPage's minimal header has no campaign picker; refreshCampaigns
  // looks the real one up by id
  const select = document.createElement('select');
  select.setAttribute('id', 'campaign-select');
  document.querySelector('.app-header').appendChild(select);
  const log = installFetch([
    { method: 'GET', match: (u) => /\/api\/campaigns$/.test(u),
      reply: () => ({ campaigns: CAMPAIGNS }) }
  ]);
  const okR = await S.refreshCampaigns(prefer);
  await flush();
  return { okR, log };
}

function optionTexts() {
  return document.getElementById('campaign-select')
    .querySelectorAll('option').map((o) => o.textContent);
}

async function main() {
  await ok('39f: options render in TITLE order, not slug order', async () => {
    await refresh();
    assert.deepStrictEqual(optionTexts(), [
      'Alpha Camp', 'Ashen Marches', 'Reliability Probe', 'The Cinder Vale'
    ]);
    return null;
  });

  await ok('39f: prefer still selects the remembered campaign by slug', async () => {
    await refresh('the-cinder-vale');
    const select = document.getElementById('campaign-select');
    const selected = select.querySelectorAll('option')
      .find((o) => o.selected);
    assert.ok(selected, 'something is selected');
    assert.strictEqual(selected.getAttribute('value'), 'the-cinder-vale');
    assert.strictEqual(S.state.campaign, 'the-cinder-vale');
    return null;
  });

  await ok('39f: name fallback sorts by name when no title', async () => {
    global.document = newPage();
    const select = document.createElement('select');
    select.setAttribute('id', 'campaign-select');
    document.querySelector('.app-header').appendChild(select);
    installFetch([
      { method: 'GET', match: (u) => /\/api\/campaigns$/.test(u),
        reply: () => ({ campaigns: [
          { name: 'zeta' }, { name: 'beta' }] }) }
    ]);
    await S.refreshCampaigns();
    await flush();
    assert.deepStrictEqual(optionTexts(), ['beta', 'zeta']);
    return null;
  });

  await ok('39g: Help names the export command and the .uvtt source', () => {
    S.helpModal();
    const body = document.querySelector('.modal').textContent;
    assert.ok(body.includes('export_campaign.py'),
      'Help must name the export command');
    assert.ok(body.includes('.uvtt'), 'Help must name the Foundry files');
    assert.ok(body.includes('Maps tab'),
      'Help must say where the .uvtt files drop');
    return null;
  });

  await ok('39g: the spend-header default and Help keep their existing copy', () => {
    S.helpModal();
    const body = document.querySelector('.modal').textContent;
    assert.ok(body.includes('First campaign?'), 'the help flow stays');
    return null;
  });
}

main().then(() => {
  if (failed) {
    console.error(`${failed} campaign-select test(s) failed`);
    process.exit(1);
  }
  console.log('all campaign-select tests passed');
});
