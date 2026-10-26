import { test as base, expect } from '@playwright/test';

/**
 * Every test fails on a page error or a Content-Security-Policy violation,
 * so the strict policy is exercised on every journey.
 */
export const test = base.extend<{ problems: string[] }>({
  problems: [
    async ({ page }, use) => {
      const problems: string[] = [];
      page.on('pageerror', (error) => problems.push(`page error: ${error.message}`));
      page.on('console', (message) => {
        const text = message.text();
        if (message.type() === 'error' && /Content Security Policy|Refused to/i.test(text)) {
          problems.push(`CSP: ${text}`);
        }
      });
      await use(problems);
      expect(problems, 'page errors or CSP violations').toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };

export const HOTELS = {
  excellent: 'h_00000000a1',
  caution: 'h_00000000b2',
  goodButNoisy: 'h_00000000c3',
  notEnoughReviews: 'h_00000000d4',
  excellent2: 'h_00000000e5',
} as const;
