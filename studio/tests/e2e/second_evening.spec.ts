// The SECOND evening as a Playwright suite (ticket 44). The first-DM pass
// (first_dm.spec.ts) pins one continuous first evening; a live GM pass that
// drove the app end to end found three defects (tickets 41-43) in territory
// that suite never visits. This spec walks the rest of the map, on the same
// self-contained harness (webServers + throwaway CAMPAIGN_DATA_ROOT from
// playwright.config.ts; workers: 1 serializes the specs):
//
//   Places (suggested join -> Link -> place_id write-back -> Mentions),
//   Quests (grouping, the due timer, a status flip regrouping the card),
//   the missing-pages job (dangling link -> grounded drafts -> approve),
//   the rumor chain (Tell the party -> prefill -> Record -> Reveal ->
//   handout), the fact lifecycle beyond record (supersede provenance,
//   Reveal no-op), session extras (uncited manual suggestion refused,
//   append-revision evidence), goal review (propose -> discard), and the
//   forecast (HYPOTHETICAL, discard-only, receipt kept).
//
// Fixture lore files drop in with node:fs (the geography-fixture
// precedent): quests/places have no authoring UI BY RULING (the no-in-app-
// lore-editor ruling — "open Obsidian"), so writing the vault directly IS
// the honest way to drive those surfaces. The quest status flip is the same
// maneuver: the app has no flip UI by that ruling; the flip happens on the
// file and the studio picks it up.
//
// The battlemap leg runs only when the git-ignored FMG tools exist
// (docs/WORKFLOW.md prerequisite rule — skip with a note).

import { test, expect, type Page } from '@playwright/test';
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  navTab, confirmModal, waitToast, setDesignDelay,
} from './helpers';

const DATA_ROOT = process.env.E2E_DATA_ROOT
  || join(require('node:os').tmpdir(), 'e2e-first-dm-data');
const REPO = join(__dirname, '..', '..', '..');
const HAS_FMG = process.env.E2E_SKIP_FMG
  ? false
  : existsSync(join(REPO, 'dnd-mapping', 'tools', 'Fantasy-Map-Generator'));

const CAMPAIGN = 'second-evening';
const PREMISE = 'The toll bridge burned; wardens and ferrymen blame each other.';

const QUEST_PAGE = 'The Salt Tithe';
const DANGLING_PAGE = 'Old Ferry Landing';
const MISSING_NAME = 'The Salt King';

function loreDir() {
  const dir = join(DATA_ROOT, 'campaigns', CAMPAIGN, 'lore', 'entities');
  mkdirSync(dir, { recursive: true });
  return dir;
}

function writePage(name: string, text: string) {
  writeFileSync(join(loreDir(), `${name}.md`), text, 'utf-8');
}

function pageText(name: string): string {
  return readFileSync(join(loreDir(), `${name}.md`), 'utf-8');
}

/** The quest fixture: active, due week 2, anchored to Harnost (the wiki-
 *  link in the body is what a place detail's Mentions section lists). */
function writeQuestPage(status: string) {
  writePage(QUEST_PAGE, [
    '---',
    'kind: quest',
    `name: ${QUEST_PAGE}`,
    `status: ${status}`,
    'given_by: The Bridge Wardens',
    'due: 2',
    'places: [Harnost]',
    '---',
    '',
    `Sworn at [[Harnost]]: the crossing pays a salt tithe before storm`,
    `season, or the ferrymen hold the bridge for a week.`,
    '',
  ].join('\n'));
}

test('second evening: places, quests, missing pages, rumors, facts, sessions, goals, forecast', async ({ page }) => {
  test.setTimeout(600_000);

  // ---- the walk to the state the surfaces need: create -> design ->
  // create -> week 1 canon (stub-fast; first_dm pins these steps in full)
  await page.goto('/');
  await page.getByRole('button', { name: '+ New' }).click();
  const dialog = page.locator('.modal-overlay form.modal');
  await dialog.locator('#new-camp-name').fill(CAMPAIGN);
  await dialog.locator('#new-camp-title').fill('Second Evening');
  await dialog.locator('[data-act="create"]').click();
  await waitToast(page, /Campaign created: Second Evening/);

  // design (stub answers instantly with delay 0) -> prefill -> create
  await navTab(page, 'sim', 'Agent Simulation');
  const designBlock = page.locator('.design-block');
  await designBlock.locator('.design-premise').fill(PREMISE);
  await designBlock.locator('.design-places').fill('Harnost');
  await setDesignDelay(page, 0);
  await designBlock.locator('.design-run').click();
  await expect(page.locator('.sand-premise')).toHaveValue(/toll bridge burned/,
    { timeout: 60_000 });
  const cityIn = page.locator('.sand-city');
  if ((await cityIn.inputValue()) === '') await cityIn.fill('Harnost');
  await page.locator('.sand-create').click();
  await waitToast(page, /Simulation created — factions are live below/);

  // week 1 -> canon
  await page.locator('.run-week-btn').click();
  await confirmModal(page, 'This costs real credits');
  await expect(page.locator('.proposal-panel'))
    .toContainText('Proposed week 1 — not canon yet', { timeout: 180_000 });
  await page.locator('.proposal-accept').click();
  await confirmModal(page, 'This becomes canon — no credits are spent');
  await waitToast(page, /Week 1 is canon — accepted exactly once/);
  await expect(page.locator('.clock-week').first()).toHaveText(/Week 1/);

  // ---- fixture lore files (vault writes, per the no-in-app-editor ruling)
  writePage('Harnost', [
    '---',
    'kind: place',
    'name: Harnost',
    '---',
    '',
    'The toll town on the Vane crossing. The wardens keep the bridge;',
    'the ferrymen keep the boats.',
    '',
  ].join('\n'));
  writePage(DANGLING_PAGE, [
    '---',
    'kind: place',
    `name: ${DANGLING_PAGE}`,
    '---',
    '',
    `The ruined landing watches [[${MISSING_NAME}]] across the water;`,
    'the ferrymen still moor here in storm weeks.',
    '',
  ].join('\n'));
  writeQuestPage('active');

  // =============================================================== Places
  await navTab(page, 'places', 'Places');
  const harnostRow = page.locator('.place-row').filter({ hasText: 'Harnost' });
  await expect(harnostRow.locator('.pill-when')).toHaveText('suggested join');
  // the suggested-joins section names both sides of the join
  await expect(page.locator('.suggest-row').filter({ hasText: 'Harnost' }))
    .toBeVisible();
  await page.locator('.suggest-row').filter({ hasText: 'Harnost' })
    .locator('.suggest-link').click();
  await waitToast(page, /Linked Harnost/);
  // the write lands: the pill flips to linked AND the page carries place_id
  await expect(harnostRow.locator('.pill-ok')).toHaveText('linked');
  await expect.poll(() => pageText('Harnost'), { timeout: 15_000 })
    .toContain('place_id:');

  // place detail: the quest page's wiki-link lists under Mentions
  await harnostRow.locator('.place-open').click();
  const detail = page.locator('.place-detail');
  await expect(detail.locator('.place-title')).toHaveText('Harnost');
  await expect(detail).toContainText('Mentions');
  await expect(detail).toContainText(`${QUEST_PAGE}.md`);

  // =============================================================== Quests
  await navTab(page, 'world', 'World Building');
  const questsPanel = page.locator('.panel').filter({
    has: page.locator('.panel-title', { hasText: 'Quests' }),
  });
  await expect(questsPanel.locator('.sub-title').filter({ hasText: 'active' }))
    .toContainText('(1)');
  const questRow = questsPanel.locator('.quest-row')
    .filter({ hasText: QUEST_PAGE });
  await expect(questRow).toBeVisible();
  await expect(questRow.locator('.pill-when')).toHaveText('due week 2');
  // the row opens the lore-page view (the same one the entities table uses)
  await questRow.click();
  await expect(page.locator('.entity-page-title').filter({ hasText: QUEST_PAGE }))
    .toBeVisible();
  await page.locator('.entity-page-close').click();

  // status flip: there is no in-app editor (by ruling) — the flip is a
  // vault write the studio picks up, and the card regroups on re-render
  writeQuestPage('resolved');
  await navTab(page, 'places', 'Places');   // leave and come back
  await navTab(page, 'world', 'World Building');
  await expect(questsPanel.locator('.sub-title').filter({ hasText: 'resolved' }))
    .toContainText('(1)', { timeout: 15_000 });
  await expect(questsPanel.locator('.sub-title').filter({ hasText: 'active' }))
    .toHaveCount(0);

  // ========================================================= Missing pages
  const missingPanel = page.locator('.panel').filter({
    has: page.locator('.panel-title', { hasText: 'Write the missing pages' }),
  });
  await expect(missingPanel.locator('.pill-name').filter({ hasText: MISSING_NAME }))
    .toBeVisible();
  await missingPanel.locator('.missing-limit-n').fill('1');
  await missingPanel.locator('.missing-write').click();
  await confirmModal(page, 'This costs real credits');
  await waitToast(page, /page drafts? staged to Review/i);
  await missingPanel.locator('.gen-result button', { hasText: 'Go to Review' })
    .click();
  const draftCard = page.locator('.review-card:not(.is-finding)')
    .filter({ hasText: MISSING_NAME });
  await expect(draftCard).toBeVisible({ timeout: 20_000 });
  await expect(draftCard.locator('.review-obsidian-pointer')).toBeVisible();
  await draftCard.locator('.review-approve').click();
  await waitToast(page, /Approved /);
  // approval wrote the page into the vault
  await expect.poll(() => existsSync(join(loreDir(), `${MISSING_NAME}.md`)),
    { timeout: 15_000 }).toBe(true);
  expect(pageText(MISSING_NAME)).toContain('old landing');

  // ======================================================== Session extras
  await navTab(page, 'sessions', 'Sessions');
  await page.locator('.ssn-new-title').fill('The Night at the Salt House');
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
  await waitToast(page, /Extraction staged \d+ suggestion/);
  await page.locator('.ssn-open').first().click();
  await expect(page.locator('.ssn-card').first()).toBeVisible({ timeout: 30_000 });

  // a hand-authored card on an EXTRACTION draft needs a citation: add one,
  // check only it, and prepare refuses it by name of the missing support
  await page.locator('.ssn-add-kind').selectOption('history_event');
  await page.locator('.ssn-add-kind-btn').click();
  const manualCard = page.locator('.ssn-card').last();
  await manualCard.locator('.ssn-edit[data-field="actor"]').selectOption('world');
  await manualCard.locator('.ssn-edit[data-field="text"]')
    .fill('The party rented the salt warehouse for the night.');
  await manualCard.locator('.ssn-check').check();
  await page.locator('.ssn-prepare').click();
  await waitToast(page, /no evidence citation/);

  // excluded, the extraction cards prepare: the fresh render shows the
  // manual card still selected (the refusal's save kept it) — uncheck it,
  // check one clean card, and prepare stages without it
  await page.locator('.ssn-open').first().click();
  await manualCard.locator('.ssn-check').setChecked(false);
  await page.locator('.ssn-card').first().locator('.ssn-check').check();
  await page.locator('.ssn-prepare').click();
  await waitToast(page, /Staged proposal/);

  // the staged session proposal is NOT wanted here — discard it (which
  // also clears the pending gate the goal review 409s on)
  await navTab(page, 'sim', 'Agent Simulation');
  await expect(page.locator('.proposal-panel'))
    .toContainText('Session changes', { timeout: 30_000 });
  await page.locator('.proposal-discard').click();
  await confirmModal(page, 'Nothing was ever written');
  await waitToast(page, /the campaign never changed/);

  // append an immutable notes revision, extract again: the fresh evidence
  // cites the SECOND revision — the notes trail is load-bearing
  await navTab(page, 'sessions', 'Sessions');
  await page.locator('.ssn-open').first().click();
  await expect(page.locator('.panel-title', { hasText: 'Notes (' }))
    .toContainText('1 revision');
  await page.locator('.ssn-append-text').fill(
    'the party argued the toll question through the night\n' +
    'ledgers moved to the salt warehouse\n' +
    'the wardens posted a reward at the ferry house');
  await page.locator('.ssn-append-btn').click();
  await waitToast(page, /Notes revision appended/);
  await page.locator('.ssn-open').first().click();
  await expect(page.locator('.panel-title', { hasText: 'Notes (' }))
    .toContainText('2 revisions');
  await page.locator('.ssn-extract').click();
  await confirmModal(page, 'This costs real credits');
  await waitToast(page, /Extraction staged \d+ suggestion/);
  await page.locator('.ssn-open').first().click();
  await expect(page.locator('.ssn-evidence').first())
    .toContainText('notes/0002.md', { timeout: 30_000 });

  // ============================================================== Rumors
  // the week born the board its first rumor: tell the party, record,
  // reveal — the handout is the only thing the party ever sees
  await navTab(page, 'sim', 'Agent Simulation');
  const tellBtn = page.locator('.rumor-tell').first();
  await expect(tellBtn).toBeVisible({ timeout: 30_000 });
  const claim = await page.locator('.rumor-claim').first().textContent();
  await tellBtn.click();
  await expect(page.locator('.tab-section[data-tab="knowledge"].active'))
    .toBeVisible();
  const factForm = page.locator('.panel').filter({
    has: page.locator('.panel-title', { hasText: 'Record a fact' }),
  });
  await expect(factForm.locator('.fact-prefill-note'))
    .toContainText('Prefilled from a rumor');
  await expect(factForm.locator('.fact-text')).not.toHaveValue('');
  await expect(factForm.locator('.fact-vis .vis-mode')).toHaveValue('public');
  await factForm.locator('.fact-record').click();
  await waitToast(page, /Fact recorded/);

  // reveal puts it in the party handout — the body, not just the list
  const revealedCard = page.locator('.fact-card').filter({ hasText: claim! });
  await expect(revealedCard).toBeVisible();
  await revealedCard.locator('.fact-reveal').click();
  await waitToast(page, /Revealed to the party/);
  const handout = await page.request.get(
    `/api/campaign/${CAMPAIGN}/disclosure/handout`);
  expect(handout.status()).toBe(200);
  expect(await handout.text()).toContain((claim || '').trim());

  // =============================================================== Facts
  // supersede: the corrected text replaces it; the old fact stays as a
  // labeled provenance row and never compiles again
  await factForm.locator('.fact-text')
    .fill('The ferry league keeps a second ledger in the salt warehouse.');
  await factForm.locator('.fact-vis .vis-mode').selectOption('public');
  await factForm.locator('.fact-record').click();
  await waitToast(page, /Fact recorded/);
  const ledgerFact = page.locator('.fact-card')
    .filter({ hasText: 'second ledger' });
  await expect(ledgerFact.locator('.fact-reveal')).toBeVisible();
  await ledgerFact.locator('.fact-supersede').click();
  const supForm = page.locator('.supersede-form');
  await expect(supForm).toBeVisible();
  await supForm.locator('.supersede-text')
    .fill('The ferry league burned its second ledger the same night.');
  await supForm.locator('button[type="submit"]').click();
  await waitToast(page, /Superseded/);
  const oldFact = page.locator('.fact-card')
    .filter({ hasText: 'keeps a second ledger' });
  await expect(oldFact.locator('.pill-superseded')).toBeVisible();
  await expect(oldFact.locator('.fact-reveal')).toHaveCount(0);
  const corrected = page.locator('.fact-card')
    .filter({ hasText: 'burned its second ledger' });
  await expect(corrected.locator('.fact-reveal')).toHaveText('Reveal to party');

  // the reveal no-op: a second reveal is offered honestly as a no-op
  await corrected.locator('.fact-reveal').click();
  await waitToast(page, /Revealed to the party/);
  await expect(page.locator('.fact-card')
    .filter({ hasText: 'burned its second ledger' })
    .locator('.fact-reveal')).toHaveText('Re-reveal (no-op)');

  // ========================================================== Goal review
  await navTab(page, 'sim', 'Agent Simulation');
  // scoped to the active section: inactive tab sections stay in the DOM
  // and a sessions draft card also contains the faction's name (its
  // unresolved-actor select lists the roster)
  const simSection = page.locator('.tab-section[data-tab="sim"]');
  const wardensCard = simSection.locator('article')
    .filter({ hasText: 'The Bridge Wardens' }).first();
  const factionGoal = await wardensCard.textContent();
  await page.locator('.goal-review-btn').click();
  await confirmModal(page, 'This costs real credits');
  await expect(page.locator('.proposal-panel'))
    .toContainText('Proposed goal updates — not canon yet', { timeout: 120_000 });
  await expect(page.locator('.proposal-panel'))
    .toContainText('outlast the ferry league');
  await page.locator('.proposal-discard').click();
  await confirmModal(page, 'Nothing was ever written');
  await waitToast(page, /the campaign never changed/);
  await expect(page.locator('.proposal-panel')).toHaveCount(0);
  // the state the discard promises untouched: clock and goal, byte for byte
  await expect(page.locator('.clock-week').first()).toHaveText(/Week 1/);
  await expect(wardensCard).toHaveText(factionGoal!);

  // ============================================================ Forecast
  await page.locator('.forecast-weeks').selectOption('2');
  await page.locator('.forecast-run-btn').click();
  await confirmModal(page, 'This costs real credits');
  const forecastPanel = page.locator('.forecast-panel');
  await expect(forecastPanel.locator('.panel-title'))
    .toContainText('not canon', { timeout: 180_000 });
  // HYPOTHETICAL labels, discard only — no accept exists on a forecast
  await expect(forecastPanel.locator('.forecast-week-title').first())
    .toContainText('forecast');
  await expect(forecastPanel.locator('.forecast-discard')).toBeVisible();
  expect(await forecastPanel.locator('.proposal-accept').count()).toBe(0);
  await forecastPanel.locator('.forecast-discard').click();
  await confirmModal(page, 'Nothing was ever written');
  await waitToast(page, /Forecast discarded — the campaign never changed/);
  await expect(page.locator('.forecast-panel')).toHaveCount(0);
  // the receipt stays: the bundle was never canon, but it was never hidden
  const forecastsDir = join(DATA_ROOT, 'campaigns', CAMPAIGN,
    'simulation', 'forecasts');
  expect(readdirSync(forecastsDir).length).toBeGreaterThan(0);

  // ========================================================== Battlemap
  if (!HAS_FMG) {
    test.info().annotations.push({
      type: 'note',
      description: 'FMG tools absent (git-ignored prerequisite): the ' +
        'battlemap leg skipped, like first_dm steps 4-5',
    });
    return;
  }
  await navTab(page, 'maps', 'Maps');
  await page.locator('.site-kind').selectOption('dungeon');
  await page.locator('.site-name').fill('The Salt Vault');
  await page.locator('.site-place').fill('Harnost');
  await page.locator('form:has(.site-name) button[type="submit"]').click();
  await expect(page.locator('.sites-grid .artifact-card')
    .filter({ hasText: 'The Salt Vault' }).first())
    .toBeVisible({ timeout: 300_000 });
  const siteDir = join(DATA_ROOT, 'campaigns', CAMPAIGN, 'maps', 'sites',
    'the-salt-vault');
  expect(existsSync(join(siteDir, 'the-salt-vault.uvtt'))).toBe(true);
  const companion = readFileSync(join(siteDir, 'the-salt-vault.md'), 'utf-8');
  expect(companion).toContain('[[Harnost]]');
});
