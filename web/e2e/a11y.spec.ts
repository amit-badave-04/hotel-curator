import AxeBuilder from '@axe-core/playwright';
import type { Page } from '@playwright/test';

import { expect, HOTELS, test } from './fixtures';

const ROUTES = [
  { name: 'landing', path: '/' },
  { name: 'explorer', path: '/explore' },
  { name: 'explorer (filtered, empty)', path: '/explore?q=no-such-hotel' },
  { name: 'hotel', path: `/hotels/${HOTELS.goodButNoisy}` },
  { name: 'hotel (not enough reviews)', path: `/hotels/${HOTELS.notEnoughReviews}` },
  { name: 'hotel (not found)', path: '/hotels/h_ffffffffff' },
  { name: 'compare', path: `/compare?ids=${HOTELS.excellent}&ids=${HOTELS.caution}` },
  { name: 'compare (empty)', path: '/compare' },
  { name: 'about', path: '/about' },
  { name: 'not found', path: '/no-such-page' },
];

const BLOCKING = ['serious', 'critical'];

async function audit(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'])
    .analyze();
  const blocking = results.violations.filter((v) => BLOCKING.includes(v.impact ?? ''));
  const summary = blocking.map((v) => ({
    id: v.id,
    impact: v.impact,
    help: v.help,
    targets: v.nodes.slice(0, 5).map((n) => n.target.join(' ')),
  }));
  return { blocking: summary, all: results.violations.length };
}

for (const scheme of ['light', 'dark'] as const) {
  test.describe(`axe, ${scheme} mode`, () => {
    for (const route of ROUTES) {
      test(`${route.name} has no serious or critical violations`, async ({ page }) => {
        await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
        await page.goto(route.path);
        await expect(page.locator('html')).toHaveAttribute('data-theme', scheme);
        await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
        await page.waitForLoadState('networkidle');
        await expect(page.getByRole('status').filter({ hasText: /^Loading/ })).toHaveCount(0);
        const { blocking } = await audit(page);
        expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([]);
      });
    }

    // The pages most people see, at phone and tablet widths.
    for (const width of [360, 768]) {
      for (const route of ROUTES.filter((r) =>
        ['landing', 'explorer', 'hotel', 'compare', 'about'].includes(r.name),
      )) {
        test(`${route.name} at ${width} px has no serious or critical violations`, async ({
          page,
        }) => {
          await page.setViewportSize({ width, height: 900 });
          await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
          await page.goto(route.path);
          await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
          await page.waitForLoadState('networkidle');
          await expect(page.getByRole('status').filter({ hasText: /^Loading/ })).toHaveCount(0);
          const { blocking } = await audit(page);
          expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([]);
        });
      }
    }

    test('interactive states have no serious or critical violations', async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
      // Analyze result or error, a second evidence tab, mobile filter panel.
      await page.goto('/about');
      const panel = page.getByRole('region', { name: 'Try the model' });
      const button = panel.getByRole('button', { name: 'Analyse review' });
      await expect(button.or(panel.getByText('No model loaded'))).toBeVisible();
      if (await button.isVisible()) {
        await button.click();
        await expect(panel.getByRole('alert')).toBeVisible();
        await panel.getByLabel('What the guest liked').fill('Clean room');
        await button.click();
        await expect(
          panel.getByText('What the model found').or(panel.getByRole('alert')),
        ).toBeVisible();
      }
      expect((await audit(page)).blocking).toEqual([]);

      await page.goto(`/hotels/${HOTELS.goodButNoisy}`);
      await page.getByRole('tab', { name: 'Noise' }).click();
      await page.getByRole('button', { name: /Add to compare/ }).click();
      expect((await audit(page)).blocking).toEqual([]);

      // An open info popover.
      await page.getByRole('button', { name: 'What is a usable review?' }).click();
      await expect(page.getByRole('dialog', { name: 'What is a usable review?' })).toBeVisible();
      expect((await audit(page)).blocking).toEqual([]);
      await page.keyboard.press('Escape');

      // Every FAQ answer open on the landing page.
      await page.goto('/');
      for (const summary of await page.locator('details > summary').all()) await summary.click();
      await expect(page.locator('details[open]')).toHaveCount(7);
      expect((await audit(page)).blocking).toEqual([]);

      await page.setViewportSize({ width: 360, height: 780 });
      await page.goto('/explore');
      await page.getByRole('button', { name: 'Show filters' }).click();
      await page.getByRole('checkbox', { name: 'Good' }).click();
      await expect(page.getByRole('article')).toHaveCount(1);
      expect((await audit(page)).blocking).toEqual([]);
    });
  });
}
