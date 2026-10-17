// @ts-check
import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright configuration for Shizuha Mail E2E tests
 * @see https://playwright.dev/docs/test-configuration
 */
export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [
    ['html', { open: 'never' }],
    ['list']
  ],

  use: {
    // Base URL - use root since we need to access both /id/ and /mail/
    baseURL: process.env.BASE_URL || 'http://shizuha-nginx',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'on-first-retry',

    // Per-action timeout (15 seconds)
    actionTimeout: 15000,

    // Navigation timeout
    navigationTimeout: 30000,
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    // Firefox/Webkit disabled for now - install if needed
    // {
    //   name: 'firefox',
    //   use: { ...devices['Desktop Firefox'] },
    // },
  ],

  // Timeout settings
  timeout: 30000,

  // Global timeout for entire test run (10 minutes max)
  globalTimeout: 10 * 60 * 1000,

  expect: {
    timeout: 5000,
  },
});
