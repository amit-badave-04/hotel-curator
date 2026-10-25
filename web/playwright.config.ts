import { fileURLToPath } from 'node:url';

import { defineConfig, devices } from '@playwright/test';

/**
 * End-to-end tests against the real API with the synthetic dev data
 * (5 hotels, e2e/data/hotel_results.json.gz, committed so a fresh checkout
 * can run them) and the production build served by `vite preview` (same
 * proxy and security headers as nginx).
 *
 * Both servers are started here and stopped when the run ends. Locally an
 * already running server on the same port is reused.
 */
const repoRoot = fileURLToPath(new URL('..', import.meta.url));
const databaseUrl = process.env.CURATOR_E2E_DATABASE_URL ?? 'sqlite:////tmp/curator-e2e.db';
const chromiumPath = process.env.PLAYWRIGHT_CHROMIUM_PATH;

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : [['list']],
  use: {
    baseURL: 'http://127.0.0.1:4173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    // The preinstalled Chromium is found through PLAYWRIGHT_BROWSERS_PATH;
    // set PLAYWRIGHT_CHROMIUM_PATH to point at a specific binary instead.
    launchOptions: chromiumPath ? { executablePath: chromiumPath } : {},
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      name: 'api',
      cwd: repoRoot,
      command:
        '.venv/bin/curator db upgrade && ' +
        '.venv/bin/curator db seed --path web/e2e/data/hotel_results.json.gz && ' +
        '.venv/bin/curator serve --port 8000',
      env: {
        CURATOR_DATABASE_URL: databaseUrl,
        // One browser makes every request in these tests; lift the per-client limits.
        CURATOR_RATE_LIMIT_PER_MINUTE: '10000',
        CURATOR_ANALYZE_RATE_LIMIT_PER_MINUTE: '1000',
        // Errors still show; one access-log line per request does not.
        CURATOR_LOG_LEVEL: 'WARNING',
      },
      url: 'http://127.0.0.1:8000/readyz',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      stdout: 'ignore',
      stderr: 'pipe',
    },
    {
      name: 'web',
      command: 'npx vite preview --port 4173 --strictPort',
      url: 'http://127.0.0.1:4173',
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
  ],
});
