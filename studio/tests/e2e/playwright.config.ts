// Playwright config for the first-DM E2E (ticket 36). The suite is
// SELF-CONTAINED: Playwright boots the stub LLM provider and the studio
// itself (webServer array), against a throwaway CAMPAIGN_DATA_ROOT, so a
// run touches nothing in campaign-data/. One worker — the studio's job
// queue is serial by design, and the pass replays one continuous evening.

import { defineConfig } from '@playwright/test';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const STUB_PORT = Number(process.env.E2E_LLM_PORT || 8399);
const STUDIO_PORT = Number(process.env.E2E_STUDIO_PORT || 8366);
const BASE = `http://127.0.0.1:${STUDIO_PORT}`;

// throwaway data root per run — override with E2E_DATA_ROOT to keep a
// failing run's campaign around for inspection (deliberately NOT deleted
// on exit: the studio process must die first, and on Windows it holds locks)
const DATA_ROOT = process.env.E2E_DATA_ROOT || mkdtempSync(join(tmpdir(), 'e2e-first-dm-'));
// specs that write fixture FILES must land them in the SAME root the
// studio reads — export the resolved root to the test workers (ticket 44;
// without this the spec's fs writes went to a directory the studio never
// saw, and the first_dm spec only got away with it because its fixture
// leg skips on FMG machines)
process.env.E2E_DATA_ROOT = DATA_ROOT;

// env both the studio AND its engine subprocesses inherit (engines._run
// passes the parent env down): the stub provider, plus the port the host
// allowlist derives from
const STUDIO_ENV = {
  ...process.env,
  STUDIO_PORT: String(STUDIO_PORT),
  CAMPAIGN_DATA_ROOT: DATA_ROOT,
  LLM_BASE_URL: `http://127.0.0.1:${STUB_PORT}/v1`,
  LLM_MODEL: 'e2e-stub',
  LLM_API_KEY: 'e2e-stub-key',
};

export default defineConfig({
  testDir: '.',
  testMatch: /(first_dm|second_evening)\.spec\.ts$/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  reporter: [['list']],
  use: {
    baseURL: BASE,
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: `${process.env.E2E_PYTHON || 'python'} llm_stub.py --port ${STUB_PORT}`,
      url: `http://127.0.0.1:${STUB_PORT}/health`,
      cwd: __dirname,
      reuseExistingServer: false,
      timeout: 15_000,
    },
    {
      command: `python -m uvicorn server:app --host 127.0.0.1 --port ${STUDIO_PORT}`,
      url: `${BASE}/api/campaigns`,
      cwd: join(__dirname, '..', '..'),
      env: STUDIO_ENV,
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
