// The first-DM pass as a Playwright suite (ticket 36). One continuous
// evening, black-box: the suite drives ONLY visible UI (no JS injection,
// no API shortcuts for state the UI must produce; reads of in-page state
// for assertions are fine). The studio and the stub LLM provider boot via
// playwright.config.ts's webServer array against a throwaway
// CAMPAIGN_DATA_ROOT — a run touches nothing in campaign-data/.
//
// Adaptations from the 2026-10-04 hands-on pass, recorded on the ticket:
//   - the search probe looks for "bridge" (the stub campaign's word);
//   - accept-modals assert the honest headers ticket 39d introduced;
//   - the mid-run world-gen probe asserts the queue strip ("Running:
//     world-gen") instead of a disabled button — nothing re-disables the
//     button across a reload; the no-double-run guarantee is the
//     server's 409/force gate;
//   - steps 4-5 (FMG world/city generation) run only when the git-ignored
//     dnd-mapping/tools FMG build exists (docs/WORKFLOW.md prerequisite
//     rule — skip with a note, like the integration tier). Without it
//     (CI), the front half ends after step 3, a minimal geography.yaml
//     fixture drops into the throwaway data root, and the back half
//     covers steps 6-13.

import { test, expect, type Page } from '@playwright/test';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  navTab, confirmModal, cancelModal, waitToast,
  countPostsDuring, inlineError, setDesignDelay,
} from './helpers';

const DATA_ROOT = process.env.E2E_DATA_ROOT
  || join(require('node:os').tmpdir(), 'e2e-first-dm-data');
const REPO = join(__dirname, '..', '..', '..');
// E2E_SKIP_FMG=1 exercises the CI leg (no FMG tools) on a machine that
// has them
const HAS_FMG = process.env.E2E_SKIP_FMG
  ? false
  : existsSync(join(REPO, 'dnd-mapping', 'tools', 'Fantasy-Map-Generator'));

const CAMPAIGN = 'e2e-probe';

// ---------------------------------------------------------------- front

test('first-DM front half: create -> design -> world -> simulation', async ({ page }) => {
  test.setTimeout(300_000);
  await page.goto('/');
  await expect(page.locator('#campaign-select')).toBeVisible();

  // step 1 — create campaign (with the hostile-name probe)
  await page.getByRole('button', { name: '+ New' }).click();
  const dialog = page.locator('.modal-overlay form.modal');
  await expect(dialog.locator('.modal-title')).toHaveText('New campaign');
  await dialog.locator('#new-camp-name').fill('probe & friends!');
  await dialog.locator('#new-camp-title').fill('Probe And Friends');
  await dialog.locator('[data-act="create"]').click();
  await expect(dialog.locator('.modal-error'))
    .toContainText('lowercase slug');            // inline 422, dialog open
  await expect(dialog).toBeVisible();
  await dialog.locator('#new-camp-name').fill(CAMPAIGN);
  await dialog.locator('#new-camp-title').fill('E2E Probe');
  await dialog.locator('[data-act="create"]').click();
  await expect(dialog).toHaveCount(0);
  await waitToast(page, /Campaign created: E2E Probe/);
  await expect(page.locator('#campaign-select option:checked'))
    .toHaveText('E2E Probe');

  // step 2 — design the sandbox (Agent Simulation) — a queued job on the
  // no-lock lane now (ticket 42), so the step drives the queue strip, a
  // mid-run reload, and the record restore with a slowed stub reply
  await navTab(page, 'sim', 'Agent Simulation');
  const designBlock = page.locator('.design-block');
  const designBtn = designBlock.locator('.design-run');

  await designBtn.click(); // empty premise
  await waitToast(page, /Describe the tension first/);

  await designBlock.locator('.design-premise')
    .fill('The toll bridge burned; wardens and ferrymen blame each other.');
  // no places AND no world yet: refused, naming the missing grounding —
  // a synchronous 422, the cheap refusals never cost a queue slot
  await designBtn.click();
  await expect(designBlock.locator('.design-error'))
    .toContainText('world/geography.yaml');

  await designBlock.locator('.design-places').fill('Harnost');
  await setDesignDelay(page, 8);   // the stub answers slowly from here
  // CANCEL-WHILE-QUEUED first (ticket 42): a second design queues behind
  // the running one and is cancelled from the queue banner — the running
  // pass must not even flinch. (The restore fills the CREATE form, not
  // the design inputs — a fresh pass needs its premise again.)
  const designPosts = await countPostsDuring(page, '/simulation/design',
    async () => {
      await designBtn.click();
      // the queue strip names the pass while it runs (ticket 42)
      await expect(page.locator('.queue-strip'))
        .toContainText('sim-design', { timeout: 15_000 });
      await expect(designBtn).toBeDisabled({ timeout: 5_000 });
      await designBtn.click({ force: true });  // double-submit probe: a
      // disabled button swallows it — the click must not become a run
      await page.reload();  // the fresh panel's button is live again
      await expect(page.locator('.design-inflight'))
        .toContainText('already running', { timeout: 15_000 });
      await designBlock.locator('.design-premise')
        .fill('The toll bridge burned; wardens and ferrymen blame each other.');
      await designBlock.locator('.design-places').fill('Harnost');
      await designBtn.click();
      const banner = page.locator('.design-block .queue-banner');
      await expect(banner).toContainText('Queued:', { timeout: 15_000 });
      await banner.locator('.queue-cancel').click();
      await waitToast(page, /Job cancelled — it was still queued, nothing ran/);
      // the running pass survived the cancel untouched
      await expect(page.locator('.queue-strip'))
        .toContainText('sim-design', { timeout: 15_000 });
    });
  expect(designPosts).toBe(2);     // the real pass + the cancelled duplicate
  // let the real pass land (the stub delay only shortens future replies)
  await setDesignDelay(page, 0);
  await expect(page.locator('.queue-strip'))
    .toHaveCount(0, { timeout: 60_000 });
  // RELOAD mid-run cost the WAIT, not the paid result: the fresh panel
  // restores the prefill from the finished job's record
  await page.reload();
  await expect(page.locator('.design-restored'))
    .toBeVisible({ timeout: 15_000 });
  await expect(page.locator('.sand-premise'))
    .toHaveValue(/toll bridge burned/);        // restored, not lost
  await expect(designBlock.locator('.design-result')).toBeVisible();
  await expect(page.locator('.sand-faction')).toHaveCount(2);
  await expect(designBlock.locator('.design-regenerate')).toBeVisible();

  // step 3 — create the simulation
  const cityIn = page.locator('.sand-city');
  if ((await cityIn.inputValue()) === '') await cityIn.fill('Harnost');
  await page.locator('.sand-create').click();
  await waitToast(page, /Simulation created — factions are live below/);
  await expect(page.locator('.clock-week').first()).toHaveText(/Week 0/);
  await expect(
    page.locator('article').filter({ hasText: 'The Bridge Wardens' }).first(),
  ).toBeVisible();
  await expect(
    page.locator('article').filter({ hasText: 'The Ferrymen' }).first(),
  ).toBeVisible();

  // step 4 — world gen (FMG) — or end here and let the fixture back half run
  if (!HAS_FMG) {
    test.info().annotations.push({
      type: 'note',
      description: 'FMG tools absent (git-ignored prerequisite): steps ' +
        '4-5 skipped, a geography fixture backs steps 6-13',
    });
    return;
  }
  await navTab(page, 'maps', 'Maps');
  const seedIn = page.locator('.world-seed');
  await seedIn.fill('4242');
  await page.locator('details.world-steer > summary').click(); // open steering
  await page.locator('.opt-culture-set').selectOption('highFantasy');
  await page.locator('.world-gen-btn').click();
  await expect(page.locator('.world-gen-btn')).toBeDisabled({ timeout: 5_000 });
  await page.reload();                         // mid-run reload probe
  // the reload detached the page's job watcher — the honest "still
  // running" surface is the queue strip, and the finished world shows up
  // on the next refresh (what a user does)
  await expect(page.locator('.queue-strip'))
    .toContainText('Running:', { timeout: 15_000 });
  await expect(async () => {
    await page.reload();
    await expect(page.locator('.worldmap-img').first())
      .toBeVisible({ timeout: 4_000 });
  }).toPass({ timeout: 300_000, intervals: [5_000] });
  await expect(page.locator('.burg-select'))
    .toHaveValue(/.+/, { timeout: 30_000 });   // the picker is populated
  await expect(page.locator('.current-seed-note')).toContainText('4242');
  // regenerate now requires force: disabled without it, and the form says why
  await expect(page.locator('.world-gen-btn')).toBeDisabled();
  await expect(page.locator('.world-gen-btn').locator('xpath=ancestor::div[contains(@class,"form-block")]'))
    .toContainText('Without force, the server refuses (409)');

  // step 5 — city map
  await page.locator('.burg-select').selectOption({ index: 0 });
  const mapIt = page.locator('form:has(.burg-select) button[type="submit"]');
  await mapIt.click();
  await expect(mapIt).toBeDisabled({ timeout: 5_000 });
  await expect(mapIt).toBeEnabled({ timeout: 300_000 });
  await expect(page.locator('.cities-grid').locator('a, article, .artifact-card')
    .first()).toBeVisible();

  // steps 6-13 continue right here when the FMG path ran
  await runBackHalf(page);
});

// ----------------------------------------------------------------- back

test('first-DM back half: lore -> week 1 -> project -> session -> knowledge -> search', async ({ page }) => {
  test.setTimeout(420_000);
  test.skip(HAS_FMG, 'the front-half test covers the full path when FMG exists');

  await page.goto('/');
  await expect(page.locator('#campaign-select option:checked'))
    .toHaveText('E2E Probe');

  // fixture geography: harness setup, not app state through the UI
  const geoDir = join(DATA_ROOT, 'campaigns', CAMPAIGN, 'world');
  mkdirSync(geoDir, { recursive: true });
  writeFileSync(join(geoDir, 'geography.yaml'), [
    'meta:',
    '  source: e2e fixture',
    '  map_name: E2E Probe',
    '  seed: "4242"',
    'summary:',
    '  nations: 1',
    '  settlements: 1',
    'nations:',
    '- id: harnost-1',
    '  name: Harnost',
    '  kind: Free City',
    '  capital: Harnost',
    '  burgs: 1',
    '',
  ].join('\n'), 'utf-8');
  await navTab(page, 'maps', 'Maps'); // reload the tab so geography is read
  await page.reload();
  await expect(page.locator('.current-seed-note')).toContainText('4242');

  await runBackHalf(page);
});

/** Steps 6-13, shared by both halves. The front half leaves the browser
 *  on the Maps tab; the back half arrives there too. */
async function runBackHalf(page: Page) {
  // step 6 — backstory (World Building): cost modal cancel first
  await navTab(page, 'world', 'World Building');
  const backstory = page.locator('.panel').filter({
    has: page.locator('.panel-title', { hasText: 'Generate backstory' }),
  });
  await expect(backstory.locator('.panel-note'))
    .toContainText('metered LLM passes');      // 39c copy
  await backstory.locator('.focus-input').fill('the toll war');
  await backstory.locator('button[type="submit"]').click();
  await cancelModal(page, 'This costs real credits'); // nothing may start
  await backstory.locator('button[type="submit"]').click();
  await confirmModal(page, 'This costs real credits');
  await waitToast(page, /Backstory staged to Review/);

  // the queue shows the pending entries with their findings containers
  await navTab(page, 'review', 'Review');
  const entryItems = page.locator('.review-card')
    .filter({ hasText: 'authored' });
  await expect(entryItems.first()).toBeVisible({ timeout: 20_000 });
  const entryCount = await entryItems.count();
  expect(entryCount).toBeGreaterThanOrEqual(3);

  // step 7 — propose week 1, and probe the honesty surfaces mid-run
  await navTab(page, 'sim', 'Agent Simulation');
  await page.locator('.run-week-btn').click();  // "Propose week 1"
  await confirmModal(page, 'This costs real credits');

  // the job is running: the strip says so, and the studio refuses writers
  await expect(page.locator('.queue-strip'))
    .toContainText('Running:', { timeout: 20_000 });

  // 409 probe A — staging a project while the week runs
  await page.locator('.project-new').click();
  const pf = page.locator('.project-form-panel');
  await pf.locator('.pf-title').fill('Buy the ferry concession');
  await pf.locator('.pf-objective')
    .fill('The Wardens hold the ferry concession before spring.');
  await pf.locator('.pf-completion')
    .fill('The Wardens acquired the ferry concession.');
  await pf.locator('.pf-stage').click();
  await expect(pf.locator('.pf-error, .projects-error').first())
    .toContainText('another job is already modifying');
  await expect(pf.locator('.pf-error, .projects-error').first())
    .toContainText('Blocked by');
  await expect(pf).toBeVisible();              // the form stayed open
  await expect(pf.locator('.pf-title'))
    .toHaveValue('Buy the ferry concession');  // values intact

  // 409 probe B — approving the pending entries while the week runs
  await navTab(page, 'review', 'Review');
  await page.locator('.review-approve-all').click();
  await expect(page.locator('.review-inline-error').first())
    .toContainText('another job is already modifying');
  await waitToast(page, /another job|Blocked by/i);
  await expect(page.locator('.review-approve-all')).toBeVisible(); // still pending

  // step 12a — queue honesty: a job queued behind the week can be
  // cancelled, says so, and the strip drops it
  await navTab(page, 'world', 'World Building');
  await backstoryGen(page).click();
  await confirmModal(page, 'This costs real credits');
  const banner = page.locator('.queue-banner');
  await expect(banner).toBeVisible({ timeout: 10_000 });
  await banner.locator('.queue-cancel').click();
  await waitToast(page, /Job cancelled — it was still queued, nothing ran/);
  await expect(page.locator('.queue-banner')).toHaveCount(0);
  await expect(page.locator('.queue-strip')).not.toContainText('Queued:',
    { timeout: 10_000 });                      // the strip dropped it

  // back to the week: it stages while the stub chronicles slowly
  await navTab(page, 'sim', 'Agent Simulation');
  await expect(page.locator('.proposal-panel'))
    .toContainText('Proposed week 1 — not canon yet', { timeout: 180_000 });
  await expect(page.locator('.proposal-panel table tr').nth(1))
    .toBeVisible();                            // the diff table has rows
  await page.locator('.proposal-accept').click();
  await confirmModal(page, 'This becomes canon — no credits are spent');
  await waitToast(page, /Week 1 is canon — accepted exactly once/);
  await expect(page.locator('.clock-week').first()).toHaveText(/Week 1/);

  // step 8 — approving the pending entries AFTER the week accept, in the
  // order the app's own Help recommends. Ticket 37 (ids renumber) and
  // ticket 40 (drafts no longer pin the timeline; the identity guard is
  // in-batch only) cleared both gates this sequence used to dead-end:
  // the paid-for entries approve directly.
  await navTab(page, 'review', 'Review');
  await page.locator('.review-approve-all').click();
  await waitToast(page, /Approved /);
  await expect(page.locator('.review-card:not(.is-finding)')).toHaveCount(0,
    { timeout: 30_000 });                      // the queue drained

  // step 9 — project: stage for real, accept, card shows the counter
  await navTab(page, 'sim', 'Agent Simulation');
  await page.locator('.project-new').click();
  const pf2 = page.locator('.project-form-panel');
  await pf2.locator('.pf-title').fill('Buy the ferry concession');
  await pf2.locator('.pf-objective')
    .fill('The Wardens hold the ferry concession before spring.');
  await pf2.locator('.pf-completion')
    .fill('The Wardens acquired the ferry concession.');
  await pf2.locator('.pf-stage').click();
  await expect(page.locator('.proposal-panel'))
    .toContainText('Proposed project changes — not canon yet',
      { timeout: 30_000 });
  await page.locator('.proposal-accept').click();
  await confirmModal(page, 'This becomes canon — no credits are spent');
  await waitToast(page, /applied exactly once/);
  // the ticket's "project card 'active 0/4'" is two pills in the markup:
  // the status pill and the progress/target pill
  const projCard = page.locator('.project-card').filter({
    hasText: 'Buy the ferry concession' });
  await expect(projCard.locator('.pill-kind')).toHaveText('active');
  await expect(projCard.locator('.pill-when')).toHaveText('0/4');

  // step 10 — session: notes -> extract -> prepare with grade confirm
  await navTab(page, 'sessions', 'Sessions');
  await page.locator('.ssn-new-title').fill('The Night at the Ferry House');
  await page.locator('.ssn-new-notes').fill(
    'the party argued the toll question through the night\n' +
    'ledgers moved to the salt warehouse');
  await page.locator('.ssn-new-btn').click();
  await waitToast(page, /session/i);
  await page.locator('.ssn-open').first().click();
  const extractBtn = page.locator('.ssn-extract');
  await expect(extractBtn).toBeEnabled();
  await extractBtn.click();
  await confirmModal(page, 'This costs real credits');
  // completion kicks back to the sessions LIST (shipped behavior) —
  // reopen the session to read the staged cards
  await waitToast(page, /Extraction staged \d+ suggestion/);
  await page.locator('.ssn-open').first().click();
  const cards = page.locator('.ssn-card');
  await expect(cards.first()).toBeVisible({ timeout: 30_000 });
  await expect(cards.first().locator('blockquote')).toBeVisible();
  await expect(cards.first().locator('.ssn-evidence'))
    .toContainText('notes/0001.md:1-1');
  await page.locator('.ssn-check').first().check();
  const allChecks = page.locator('.ssn-check');
  const n = await allChecks.count();
  for (let i = 1; i < n; i++) await allChecks.nth(i).check();
  await page.locator('.ssn-prepare').click();
  // refusal: the resource_change needs its exact grade confirmed first.
  // The refusal re-renders to the LIST and rides a toast (shipped
  // ticket-30 behavior: the re-render IS the reload the 409 advises)
  await waitToast(page, /Prepare failed: .*grade|CONFIRM/i);
  await page.locator('.ssn-open').first().click();
  await page.locator('.ssn-confirm-grade').first().check();
  await page.locator('.ssn-prepare').click();
  await waitToast(page, /Staged proposal/);

  // accept the session proposal on the Agent Simulation tab
  await navTab(page, 'sim', 'Agent Simulation');
  await expect(page.locator('.proposal-panel'))
    .toContainText('Session changes', { timeout: 30_000 });
  await page.locator('.proposal-accept').click();
  await confirmModal(page, 'This becomes canon — no credits are spent');
  await waitToast(page, /Session changes are canon/);

  // step 11 — knowledge: tell the party, briefing cap, next-session prep
  const tellBtn = page.locator('button', { hasText: 'Tell the party' }).first();
  await tellBtn.click();
  await expect(page.locator('.tab-section[data-tab="knowledge"].active'))
    .toBeVisible();
  const factForm = page.locator('.panel').filter({
    has: page.locator('.panel-title', { hasText: 'Record a fact' }),
  });
  await expect(factForm.locator('.fact-text')).not.toHaveValue('');
  await expect(factForm).toContainText(/its own step/i);
  await factForm.locator('.fact-record').click();
  await waitToast(page, /fact|recorded|knowledge/i);

  // drive more weeks until the briefing overflows the cap (events +
  // rumors are what a briefing item is), then assert the honest cap line
  await runWeeksUntilBriefingOverflows(page, 4);
  const briefing = page.locator('.panel').filter({
    has: page.locator('.panel-title', { hasText: 'DM briefing' }),
  });
  await expect(briefing).toContainText(/more not shown/);
  const shown = await briefing.locator('.briefing-item').count();
  expect(shown).toBeLessThanOrEqual(10);

  const prepBtn = briefing.locator('.briefing-prep');
  await prepBtn.click();
  await expect(briefing.locator('.prep-status a, .prep-status code').first())
    .toBeVisible({ timeout: 30_000 });
  await prepBtn.click();                       // re-run never overwrites
  await expect(briefing.locator('.prep-status')).toContainText('-2',
    { timeout: 30_000 });

  // step 12b — discard honesty: a staged proposal discards, nothing changes
  await navTab(page, 'sim', 'Agent Simulation');
  await page.locator('.project-new').click();
  const pf3 = page.locator('.project-form-panel');
  await pf3.locator('.pf-owner').selectOption({ label: 'The Ferrymen' });
  await pf3.locator('.pf-title').fill('Rope the ferry landing');
  await pf3.locator('.pf-objective').fill('A roped landing for night fares.');
  await pf3.locator('.pf-completion').fill('A roped landing exists.');
  await pf3.locator('.pf-stage').click();
  await expect(page.locator('.proposal-panel'))
    .toContainText('Proposed project changes', { timeout: 30_000 });
  await page.locator('.proposal-discard').click();
  await confirmModal(page, 'Nothing was ever written');
  await waitToast(page, /the campaign never changed/);
  await expect(page.locator('.proposal-panel')).toHaveCount(0);

  // step 13 — header search (pins ticket 38's working flow)
  await page.locator('#search-input').fill('bridge');
  await page.locator('#search-input').press('Enter');
  const panel = page.locator('.search-panel');
  await expect(panel).toBeVisible();
  await expect(page.locator('.search-hit').first()).toBeVisible();
  await page.keyboard.press('Escape');          // close: the panel overlays
  await expect(page.locator('.search-panel')).toHaveCount(0);
  await page.locator('#search-input').fill('');
  await page.locator('#search-input').press('Enter');
  await waitToast(page, /Type something to search for first/);
}

/** The backstory Generate submit button, wherever the World tab put it. */
function backstoryGen(page: Page) {
  return page.locator('.panel').filter({
    has: page.locator('.panel-title', { hasText: 'Generate backstory' }),
  }).locator('button[type="submit"]');
}

/** Propose + accept whole weeks (stub LLM: seconds per week plus the
 *  chronicle delay) until the DM briefing holds more than `cap` items —
 *  the cap line only renders on overflow, so the honest way to pin it is
 *  more canon, not filler. Guarded: gives up after `maxWeeks` weeks. */
async function runWeeksUntilBriefingOverflows(page: Page, maxWeeks: number) {
  for (let i = 0; i < maxWeeks; i++) {
    await navTab(page, 'knowledge', 'Knowledge');
    const note = await page.locator('.panel')
      .filter({ hasText: 'DM briefing' })
      .first().textContent().catch(() => '');
    if (note && /more not shown/.test(note)) return;
    await navTab(page, 'sim', 'Agent Simulation');
    await page.locator('.run-week-btn').click();
    await confirmModal(page, 'This costs real credits');
    await expect(page.locator('.proposal-panel'))
      .toContainText(/Proposed week \d+ — not canon yet/, { timeout: 180_000 });
    await page.locator('.proposal-accept').click();
    await confirmModal(page, 'This becomes canon — no credits are spent');
    await waitToast(page, /is canon — accepted exactly once/);
    await navTab(page, 'knowledge', 'Knowledge');
    const after = await page.locator('.panel')
      .filter({ hasText: 'DM briefing' })
      .first().textContent().catch(() => '');
    if (after && /more not shown/.test(after)) return;
  }
  throw new Error('briefing never overflowed the cap after ' + maxWeeks + ' weeks');
}
