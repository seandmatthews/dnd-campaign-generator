// The shell a GM lives in before any lore machinery: the help modal, the
// campaign switcher (create a second campaign and prove no state bleeds
// between the two), the design pass's Regenerate button (ticket 42's
// queued job, re-run), and the Obsidian authoring card's first-run copy
// (ticket 43). Self-contained ON PURPOSE: Playwright runs spec files in
// alphabetical order, so this runs BEFORE first_dm and must not depend on
// the other specs' campaigns. Same harness as the rest: webServers +
// throwaway CAMPAIGN_DATA_ROOT from playwright.config.ts.

import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { navTab, waitToast, setDesignDelay } from './helpers';

const DATA_ROOT = process.env.E2E_DATA_ROOT
  || join(require('node:os').tmpdir(), 'e2e-first-dm-data');

const PREMISE = 'The toll bridge burned; wardens and ferrymen blame each other.';

test('the shell: help, switcher isolation, design regenerate, obsidian card', async ({ page }) => {
  test.setTimeout(300_000);

  await page.goto('/');
  await expect(page.locator('#campaign-select')).toBeVisible();

  // the help modal: what this is, who is in charge, and the honest
  // "export is a command, not a button" line
  await page.locator('#help-btn').click();
  const help = page.locator('.modal-overlay .modal');
  await expect(help.locator('.modal-title')).toHaveText('What is this?');
  await expect(help).toContainText('You are the DM');
  await expect(help).toContainText('export_campaign.py');
  await help.locator('[data-act="close"]').click();
  await expect(page.locator('.modal-overlay')).toHaveCount(0);

  // campaign A: design -> prefill -> create the simulation
  await page.getByRole('button', { name: '+ New' }).click();
  const dialog = page.locator('.modal-overlay form.modal');
  await dialog.locator('#new-camp-name').fill('shell-a');
  await dialog.locator('#new-camp-title').fill('Shell A');
  await dialog.locator('[data-act="create"]').click();
  await waitToast(page, /Campaign created: Shell A/);

  await navTab(page, 'sim', 'Agent Simulation');
  const designBlock = page.locator('.design-block');
  await designBlock.locator('.design-premise').fill(PREMISE);
  await designBlock.locator('.design-places').fill('Harnost');
  await setDesignDelay(page, 0);
  await designBlock.locator('.design-run').click();
  await expect(page.locator('.sand-premise')).toHaveValue(/toll bridge burned/,
    { timeout: 60_000 });

  // Regenerate re-runs the pass on the same form — a queued job again,
  // made observable with a slowed stub reply, and the re-run lands in
  // the form just like the first pass did
  await setDesignDelay(page, 3);
  await designBlock.locator('.design-regenerate').click();
  await expect(page.locator('.queue-strip'))
    .toContainText('sim-design', { timeout: 15_000 });
  await expect(page.locator('.queue-strip')).toHaveCount(0,
    { timeout: 60_000 });
  await setDesignDelay(page, 0);
  await expect(page.locator('.sand-faction')).toHaveCount(2);

  const cityIn = page.locator('.sand-city');
  if ((await cityIn.inputValue()) === '') await cityIn.fill('Harnost');
  await page.locator('.sand-create').click();
  await waitToast(page, /Simulation created — factions are live below/);
  await expect(page.locator('.clock-week').first()).toHaveText(/Week 0/);

  // campaign B: a fresh shell — none of A's state may bleed across
  // (#new-campaign-btn: '+ New' strict-matches the sim tab's
  // '+ New project' once a simulation exists)
  await page.locator('#new-campaign-btn').click();
  const dialog2 = page.locator('.modal-overlay form.modal');
  await dialog2.locator('#new-camp-name').fill('shell-b');
  await dialog2.locator('#new-camp-title').fill('Shell B');
  await dialog2.locator('[data-act="create"]').click();
  await waitToast(page, /Campaign created: Shell B/);
  await expect(page.locator('#campaign-select option:checked'))
    .toHaveText('Shell B');
  await navTab(page, 'sim', 'Agent Simulation');
  await expect(page.locator('.clock-week')).toHaveCount(0);      // no sim
  await expect(page.locator('.design-restored')).toHaveCount(0); // no record
  await expect(page.locator('.sand-premise')).toHaveValue('');
  await expect(page.locator('.sand-faction').first().locator('.f-name'))
    .toHaveValue('');

  // switch back to A: its simulation is all still there
  await page.locator('#campaign-select').selectOption({ label: 'Shell A' });
  await expect(page.locator('#campaign-select option:checked'))
    .toHaveText('Shell A');
  await navTab(page, 'sim', 'Agent Simulation');
  await expect(page.locator('.clock-week').first()).toHaveText(/Week 0/,
    { timeout: 15_000 });
  await expect(page.locator('article')
    .filter({ hasText: 'The Bridge Wardens' }).first()).toBeVisible();

  // the picker survives a refresh (localStorage)
  await page.reload();
  await expect(page.locator('#campaign-select option:checked'))
    .toHaveText('Shell A');

  // the authoring card's vault registration (ticket 45), hermetic via the
  // throwaway OBSIDIAN_CONFIG: a folder Obsidian has never seen shows the
  // walkthrough (naming the real first-click error and the by-hand fix),
  // Register merges the entry server-side, and the card flips to the
  // registered branch — the config file on disk is the write's receipt
  await navTab(page, 'world', 'World Building');
  const card = page.locator('.authoring-card');
  await expect(card).toBeVisible();
  const walkthrough = card.locator('.lore-walkthrough');
  await expect(walkthrough).toBeVisible();
  await expect(walkthrough).toContainText('Vault not found');
  await expect(walkthrough).toContainText('Open folder as vault');
  await expect(card.locator('.lore-path')).toContainText('shell-a');
  await expect(card.locator('.lore-copy')).toBeEnabled();
  await expect(card.locator('.lore-open'))
    .toHaveAttribute('href', /^obsidian:\/\//);
  await expect(card.locator('.lore-registered-note')).toBeHidden();

  await card.locator('.lore-register').click();
  await waitToast(page, /Registered/);
  await expect(walkthrough).toBeHidden();
  await expect(card.locator('.lore-registered-note')).toBeVisible();
  const vaultConfig = join(DATA_ROOT, 'obsidian.json');
  await expect.poll(() => readFileSync(vaultConfig, 'utf-8'),
    { timeout: 15_000 }).toContain('shell-a');
  const doc = JSON.parse(readFileSync(vaultConfig, 'utf-8'));
  const [vault] = Object.values(doc.vaults);
  expect(vault.path).toContain('shell-a');
  expect(vault.open).toBe(false);          // never steals focus
  // the flip is server truth: a reload keeps the calm registered card
  await page.reload();
  await expect(page.locator('.authoring-card .lore-walkthrough'))
    .toBeHidden({ timeout: 15_000 });
});
