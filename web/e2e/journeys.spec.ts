import { expect, HOTELS, test } from './fixtures';

test.describe('J1: find a hotel with the explorer', () => {
  test('filter by city, tier and attribute, then open a hotel', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { level: 1, name: 'Explore hotels' })).toBeVisible();
    await expect(page.getByRole('article')).toHaveCount(5);

    await page.getByLabel('City').selectOption('Paris');
    await expect(page).toHaveURL(/[?&]city=Paris/);
    await expect(page.getByRole('article')).toHaveCount(2);

    await page.getByRole('checkbox', { name: 'Excellent' }).click();
    await page
      .getByRole('group', { name: 'Strong in' })
      .getByRole('checkbox', { name: 'Location' })
      .click();
    await expect(page).toHaveURL(/tier=excellent/);
    await expect(page).toHaveURL(/strong=location/);
    await expect(page.getByRole('article')).toHaveCount(1);

    const card = page.getByRole('article', { name: 'Alpha Grand' });
    await expect(card.getByText('Excellent')).toBeVisible();
    await expect(
      card.getByText('Guests confirmed cleanliness, room and staff as strengths.'),
    ).toBeVisible();

    // The filters survive a reload because they live in the URL.
    await page.reload();
    await expect(page.getByRole('article')).toHaveCount(1);
    await expect(page.getByLabel('City')).toHaveValue('Paris');

    await card.getByRole('link', { name: 'Alpha Grand' }).click();
    await expect(page).toHaveURL(new RegExp(`/hotels/${HOTELS.excellent}$`));
    await expect(page.getByRole('heading', { level: 1, name: 'Alpha Grand' })).toBeVisible();

    await page.goBack();
    await expect(page).toHaveURL(/city=Paris/);
  });

  test('shows "not enough evidence to judge" and an empty state', async ({ page }) => {
    await page.goto('/?q=Delta');
    const delta = page.getByRole('article', { name: 'Delta Rooms' });
    await expect(delta.getByText('Not enough evidence to judge')).toBeVisible();
    await page.goto('/?q=no-such-hotel');
    await expect(page.getByText('No hotels match these filters')).toBeVisible();
  });
});

test.describe('J2: is this hotel good, and why?', () => {
  test('the hotel page shows the why and the evidence', async ({ page }) => {
    await page.goto(`/hotels/${HOTELS.goodButNoisy}`);
    await expect(page.getByRole('heading', { level: 1, name: 'Charlie House' })).toBeVisible();
    await expect(page.getByText('Good, but noisy').first()).toBeVisible();
    await expect(
      page.getByText(/^In reviews from .* guests praised cleanliness, room and staff/),
    ).toBeVisible();
    await expect(page.getByRole('heading', { name: 'The three essentials' })).toBeVisible();
    await expect(
      page.getByText(/^Complaints about noise .* hold it back\.$/).first(),
    ).toBeVisible();
    await expect(
      page.getByText(/If noise doesn't bother you, this hotel would be Excellent/),
    ).toBeVisible();

    const volume = page.getByRole('region', { name: 'Reviews over time' });
    await expect(volume.locator('svg.recharts-surface')).toHaveCount(1);
    await expect(volume).toContainText(/usable reviews over \d+ months/);

    const attributes = page.getByRole('region', { name: 'All eight attributes' });
    await expect(attributes.getByRole('table').getByRole('row')).toHaveCount(9);
    await expect(attributes.locator('svg.recharts-surface')).toHaveCount(2);
    await expect(
      attributes.getByRole('row').filter({ has: page.getByRole('rowheader', { name: 'Noise' }) }),
    ).toContainText('Weak');

    const evidence = page.getByRole('region', { name: 'Evidence by attribute' });
    await expect(evidence).toContainText('The data publisher removed punctuation');
    await evidence.getByRole('tab', { name: 'Noise' }).click();
    await expect(evidence.getByText(/^Street noise at night/).first()).toBeVisible();
    await expect(evidence.getByRole('heading', { name: /Supporting quotes/ })).toBeVisible();
    await expect(evidence.getByRole('heading', { name: /Counter-examples/ })).toBeVisible();

    const suits = page.getByRole('region', { name: 'Who it suits' });
    await expect(suits).toContainText(
      'Solo travellers complained about noise less often than other guests',
    );
    await expect(suits.getByRole('row').filter({ hasText: 'solo travellers' })).toContainText(
      'Happier',
    );
    await expect(suits.getByRole('row').filter({ hasText: 'couples' })).toContainText('Less happy');

    await expect(page.getByText('For reference, not used:')).toBeVisible();
    const provenance = page.getByRole('heading', { name: 'Where this comes from' }).locator('..');
    await expect(provenance).toContainText('2026-10-24.1');
    await expect(provenance).toContainText('legal review required');
  });

  test('an unknown hotel shows not found', async ({ page }) => {
    await page.goto('/hotels/h_ffffffffff');
    await expect(page.getByRole('heading', { name: 'Hotel not found' })).toBeVisible();
    await expect(page.getByText('Request id:')).toBeVisible();
  });
});

test.describe('J3: compare hotels', () => {
  test('compare two hotels from the explorer and see the credible difference', async ({ page }) => {
    await page.goto('/?city=Paris');
    await page
      .getByRole('article', { name: 'Alpha Grand' })
      .getByRole('button', { name: /Add to compare/ })
      .click();
    await page
      .getByRole('article', { name: 'Bravo Inn' })
      .getByRole('button', { name: /Add to compare/ })
      .click();
    await page
      .getByRole('navigation', { name: 'Main' })
      .getByRole('link', { name: /Compare/ })
      .click();

    await expect(page).toHaveURL(
      new RegExp(`/compare\\?ids=${HOTELS.excellent}&ids=${HOTELS.caution}$`),
    );
    const table = page.getByRole('region', { name: 'Side by side' }).getByRole('table');
    // Each hotel is tagged A, B, ... in the cards, the table and the charts.
    await expect(table.getByRole('columnheader')).toHaveText([
      'Attribute',
      /^A\s*Alpha Grand$/,
      /^B\s*Bravo Inn$/,
    ]);

    const cleanliness = table
      .getByRole('row')
      .filter({ has: page.getByRole('rowheader', { name: /^Cleanliness/ }) });
    await expect(cleanliness.getByRole('cell').first()).toContainText('Credible leader');
    await expect(cleanliness.getByRole('cell').nth(1)).not.toContainText('Credible leader');
    const room = table
      .getByRole('row')
      .filter({ has: page.getByRole('rowheader', { name: /^Room/ }) });
    await expect(room).toContainText('No single credible leader');
    await expect(page.getByText(/Other gaps may still be real or may be noise/)).toBeVisible();
    await expect(cleanliness.getByRole('cell').nth(1)).toContainText('Clearly behind Alpha Grand');
    await expect(page.locator('svg.recharts-surface')).toHaveCount(8);
    await expect(
      page.locator('svg.recharts-surface').first().getByText('(A) Alpha Grand'),
    ).toBeVisible();

    // The selection is in the URL: a fresh visit shows the same comparison.
    await page.reload();
    await expect(table.getByRole('columnheader')).toHaveCount(3);

    await page.getByRole('button', { name: 'Remove Bravo Inn from the comparison' }).click();
    await expect(page).toHaveURL(new RegExp(`/compare\\?ids=${HOTELS.excellent}$`));
    await expect(page.getByText(/Pick two to 4 hotels to compare/)).toBeVisible();
  });
});

test.describe('J5: analyse my own review text', () => {
  test('returns labels, or says the model is unavailable', async ({ page }) => {
    await page.goto('/about');
    const panel = page.getByRole('region', { name: 'Try the model' });
    await panel.getByLabel('What the guest liked').fill('Friendly staff and a spotless room');
    await panel.getByLabel('What the guest disliked').fill('Street noise at night');
    await expect(panel.getByText('34 of 2,000 characters')).toBeVisible();
    await panel.getByRole('button', { name: 'Analyse review' }).click();

    const labels = panel.getByText('What the model found');
    const unavailable = panel
      .getByRole('alert')
      .filter({ hasText: 'The model is not available right now' });
    await expect(labels.or(unavailable)).toBeVisible();
    if (await unavailable.isVisible()) {
      await expect(unavailable).toContainText('Request id:');
    } else {
      await expect(panel.getByText(/likely$/).first()).toBeVisible();
    }
  });

  test('the about page explains the method and links the API docs', async ({ page }) => {
    await page.goto('/about');
    await expect(
      page.getByRole('heading', { level: 1, name: 'How hotel-curator works' }),
    ).toBeVisible();
    const evaluation = page.getByRole('region', { name: 'Evaluation' });
    await expect(
      evaluation
        .getByText('Evaluation not available')
        .or(evaluation.getByText('Served model, macro-F1')),
    ).toBeVisible();
    await expect(page.getByRole('link', { name: /API documentation/ })).toHaveAttribute(
      'href',
      '/api/docs',
    );
    const docs = await page.request.get('/api/openapi.json');
    expect(docs.ok()).toBe(true);
  });
});

test.describe('J3 with three hotels', () => {
  test('a real gap is not called noise when no single hotel leads', async ({ page }) => {
    await page.goto(
      `/compare?ids=${HOTELS.excellent}&ids=${HOTELS.caution}&ids=${HOTELS.goodButNoisy}`,
    );
    const table = page.getByRole('region', { name: 'Side by side' }).getByRole('table');
    const cleanliness = table
      .getByRole('row')
      .filter({ has: page.getByRole('rowheader', { name: /^Cleanliness/ }) });
    // 99% vs 1% vs 98%: no single leader, but Bravo is clearly behind both.
    await expect(cleanliness).toContainText('No single credible leader');
    await expect(cleanliness.getByRole('cell').nth(1)).toContainText(
      'Clearly behind Alpha Grand and Charlie House',
    );
    await expect(page.getByText(/could be noise/)).toHaveCount(0);
  });
});

test.describe('Focus and scroll position', () => {
  test('a filter change keeps the scroll position and the focused checkbox', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('article')).toHaveCount(5);
    const box = page
      .getByRole('group', { name: 'Not weak in' })
      .getByRole('checkbox', { name: 'Breakfast & food' });
    await box.scrollIntoViewIfNeeded();
    await box.focus();
    const before = await page.evaluate(() => window.scrollY);
    expect(before).toBeGreaterThan(0);
    await page.keyboard.press('Space');
    await expect(page).toHaveURL(/avoid_weak=food/);
    await expect(box).toBeChecked();
    await expect(box).toBeFocused();
    await expect(box).toBeInViewport();
    expect(Math.abs((await page.evaluate(() => window.scrollY)) - before)).toBeLessThan(2);
  });

  test('on a phone, tapping a filter does not jump to the top', async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 780 });
    await page.goto('/');
    await page.getByRole('button', { name: 'Show filters' }).click();
    const box = page
      .getByRole('group', { name: 'Not weak in' })
      .getByRole('checkbox', { name: 'Breakfast & food' });
    await box.scrollIntoViewIfNeeded();
    const before = await page.evaluate(() => window.scrollY);
    expect(before).toBeGreaterThan(0);
    await box.click();
    await expect(page).toHaveURL(/avoid_weak=food/);
    await expect(box).toBeInViewport();
    expect(Math.abs((await page.evaluate(() => window.scrollY)) - before)).toBeLessThan(2);
  });

  test('clearing from the empty state focuses the results heading', async ({ page }) => {
    await page.goto('/?q=no-such-hotel');
    const empty = page.getByText('No hotels match these filters').locator('..');
    await empty.getByRole('button', { name: 'Clear filters' }).click();
    await expect(page.getByRole('article')).toHaveCount(5);
    await expect(page.getByRole('heading', { level: 2, name: 'Results' })).toBeFocused();
  });

  test('removing a hotel from a comparison keeps keyboard focus on the page', async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 780 });
    await page.goto(
      `/compare?ids=${HOTELS.excellent}&ids=${HOTELS.caution}&ids=${HOTELS.goodButNoisy}`,
    );
    await page.getByRole('button', { name: 'Remove Charlie House from the comparison' }).click();
    await expect(page).toHaveURL(
      new RegExp(`/compare\\?ids=${HOTELS.excellent}&ids=${HOTELS.caution}$`),
    );
    const next = page.getByRole('button', { name: 'Remove Bravo Inn from the comparison' });
    await expect(next).toBeFocused();
    await expect(next).toBeInViewport();
    await page.keyboard.press('Enter');
    await expect(page.getByText(/Pick two to 4 hotels to compare/)).toBeVisible();
    await expect(page.getByRole('heading', { level: 1, name: 'Compare hotels' })).toBeFocused();
  });
});

test.describe('Info buttons on a touch screen', () => {
  test.use({ viewport: { width: 360, height: 780 }, hasTouch: true, isMobile: true });

  test('tapping "i" opens the explanation', async ({ page }) => {
    await page.goto(`/hotels/${HOTELS.goodButNoisy}`);
    await page.getByRole('button', { name: 'What is a usable review?' }).tap();
    const popup = page.getByRole('dialog', { name: 'What is a usable review?' });
    await expect(popup).toBeVisible();
    await expect(popup).toContainText('near-duplicate reviews are left out');
  });
});

test.describe('Keyboard, theme and small screens', () => {
  test('the skip link moves focus to the main content', async ({ page }) => {
    await page.goto('/');
    await page.keyboard.press('Tab');
    const skip = page.getByRole('link', { name: 'Skip to content' });
    await expect(skip).toBeFocused();
    await expect(skip).toBeVisible();
    await page.keyboard.press('Enter');
    await expect(page.locator('#main')).toBeFocused();
  });

  test('the theme toggle wins over the system setting and persists', async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.goto('/');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await page.getByRole('button', { name: 'Use light theme' }).click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  });

  test('no page scrolls sideways at 360 px', async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 780 });
    for (const path of [
      '/',
      `/hotels/${HOTELS.goodButNoisy}`,
      `/compare?ids=${HOTELS.excellent}&ids=${HOTELS.caution}&ids=${HOTELS.goodButNoisy}`,
      '/about',
    ]) {
      await page.goto(path);
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
      await page.waitForLoadState('networkidle');
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, path).toBeLessThanOrEqual(0);
    }
    await page.goto('/');
    await page.getByRole('button', { name: 'Show filters' }).click();
    await expect(page.getByLabel('City')).toBeVisible();
  });

  test('the security headers are sent', async ({ page }) => {
    const response = await page.goto('/');
    const headers = response?.headers() ?? {};
    expect(headers['content-security-policy']).toContain("default-src 'self'");
    expect(headers['content-security-policy']).toContain("frame-ancestors 'none'");
    expect(headers['x-content-type-options']).toBe('nosniff');
    expect(headers['referrer-policy']).toBe('no-referrer');
  });
});
