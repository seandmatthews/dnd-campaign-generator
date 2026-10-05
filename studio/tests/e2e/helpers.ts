// Shared selector discipline for the first-DM E2E (ticket 36). Everything
// the 2026-10-04 hands-on pass learned the hard way lives here, so the
// spec reads like the pass and not like a selector fight:
//
//   - nav tab names need { exact: true } ("Review" substring-matches
//     "Stage … for review" buttons) — we go one better and key on the
//     stable data-tab attribute, asserting the visible name too;
//   - form refusals render in an inline .error/.form-error box INSIDE the
//     form — assert there first; toasts (.toast) are secondary and
//     disappear after ~4s;
//   - in-flight buttons disable: assert `disabled` during the wait, then
//     wait for the state you actually care about.

import { expect, type Page, type Response, type Locator } from '@playwright/test';

export const STUDIO_URL = process.env.E2E_STUDIO_URL || 'http://127.0.0.1:8366';
export const STUB_URL =
  `http://127.0.0.1:${process.env.E2E_LLM_PORT || 8399}`;

/**
 * Harness control (not app state): set the stub LLM's sandbox-design
 * reply delay in seconds (ticket 42 — the design is a queued job now,
 * and the reload/cancel probes need a pass that stays running). Only the
 * design step pays the wait; reset to 0 when done.
 */
export async function setDesignDelay(page: Page, seconds: number) {
  const res = await page.request.post(`${STUB_URL}/__delay`,
    { data: { seconds } });
  if (!res.ok()) throw new Error(`setDesignDelay(${seconds}) failed: ${res.status()}`);
}

export async function navTab(page: Page, tabId: string, visibleName: string) {
  const tab = page.locator(`#tab-bar .tab[data-tab="${tabId}"]`);
  await expect(tab).toHaveCount(1);
  await expect(tab).toHaveText(visibleName);
  await tab.click();
  await expect(page.locator(`.tab-section[data-tab="${tabId}"].active`))
    .toBeVisible();
}

/** The toast texts currently on screen. */
export function toasts(page: Page): Locator {
  return page.locator('#toasts .toast');
}

export async function waitToast(page: Page, pattern: string | RegExp) {
  await expect(toasts(page).filter({ hasText: pattern }).first())
    .toBeVisible();
}

/**
 * Confirm a modal whose header states what the click does (ticket 39d):
 * spenders keep "This costs real credits"; accepts say "This becomes
 * canon — no credits are spent"; discards "Nothing was ever written";
 * staging "Staging is free — accept later".
 */
export async function confirmModal(page: Page, title: string | RegExp) {
  const overlay = page.locator('.modal-overlay');
  await expect(overlay.locator('.modal-title')).toHaveText(title);
  await overlay.locator('[data-act="confirm"]').click();
  await expect(overlay).toHaveCount(0);
}

export async function cancelModal(page: Page, title: string | RegExp) {
  const overlay = page.locator('.modal-overlay');
  await expect(overlay.locator('.modal-title')).toHaveText(title);
  await overlay.locator('[data-act="cancel"]').click();
  await expect(overlay).toHaveCount(0);
}

/**
 * Count POSTs to a URL fragment while `work` runs. Network observation
 * only — the suite never constructs requests itself (the black-box
 * ruling in ticket 36).
 */
export async function countPostsDuring(
  page: Page, urlFragment: string, work: () => Promise<void>,
): Promise<number> {
  let posts = 0;
  const onResponse = (r: Response) => {
    if (r.request().method() === 'POST' &&
        r.url().includes(urlFragment)) posts += 1;
  };
  page.on('response', onResponse);
  try {
    await work();
    // let in-flight responses settle before unhooking
    await page.waitForTimeout(300);
  } finally {
    page.off('response', onResponse);
  }
  return posts;
}

/**
 * The in-flight contract: a form's primary button is disabled the whole
 * time its job runs. `whileDisabled` runs the mid-flight probes.
 */
export async function withButtonDisabled(
  page: Page, button: Locator, work: () => Promise<void>,
) {
  await expect(button).toBeDisabled({ timeout: 5_000 });
  await work();
}

/** The inline error box inside a form block (scope = the form block). */
export function inlineError(scope: Locator): Locator {
  return scope.locator('.form-error, .design-error, .proposal-error, ' +
    '.pf-error, .projects-error').first();
}
