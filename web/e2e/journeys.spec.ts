import { expect, HOTELS, test } from './fixtures';

test.describe('J1: find a hotel with the explorer', () => {
  test('filter by city, tier and attribute, then open a hotel', async ({ page }) => {
    await page.goto('/explore');
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
    await page.goto('/explore?q=Delta');
    const delta = page.getByRole('article', { name: 'Delta Rooms' });
    await expect(delta.getByText('Not enough evidence to judge')).toBeVisible();
    await page.goto('/explore?q=no-such-hotel');
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
    await page.goto('/explore?city=Paris');
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
    const form = panel.getByLabel('What the guest liked');
    await expect(form.or(panel.getByText('No model loaded'))).toBeVisible();
    if (!(await form.isVisible())) {
      // /v1/meta says no model is loaded, so there is no form to fill in.
      await expect(panel).toContainText('Search, hotel pages and compare still work');
      return;
    }
    await panel.getByLabel('What the guest liked').fill('Friendly staff and a spotless room');
    await panel.getByLabel('What the guest disliked').fill('Street noise at night');
    await expect(panel.getByText('34 of 2,000 characters')).toBeVisible();
    await panel.getByRole('button', { name: 'Analyse review' }).click();

    const labels = panel.getByText('What the model found');
    const unavailable = panel.getByRole('alert').filter({ hasText: 'No model loaded' });
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
    await expect(
      page.getByRole('main').getByRole('link', { name: /API documentation/ }),
    ).toHaveAttribute('href', '/api/docs');
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
    await page.goto('/explore');
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
    await page.goto('/explore');
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
    await page.goto('/explore?q=no-such-hotel');
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

  test('no page scrolls sideways at 360 or 768 px', async ({ page }) => {
    for (const [width, path] of [360, 768].flatMap((w) =>
      [
        '/',
        '/explore',
        `/hotels/${HOTELS.goodButNoisy}`,
        `/compare?ids=${HOTELS.excellent}&ids=${HOTELS.caution}&ids=${HOTELS.goodButNoisy}`,
        '/about',
      ].map((p) => [w, p] as const),
    )) {
      await page.setViewportSize({ width, height: 780 });
      await page.goto(path);
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
      await page.waitForLoadState('networkidle');
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, `${path} at ${width} px`).toBeLessThanOrEqual(0);
    }
    await page.setViewportSize({ width: 360, height: 780 });
    await page.goto('/explore');
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

test.describe('J0: the landing page', () => {
  test('says what the product is, shows live numbers and leads to the explorer', async ({
    page,
  }) => {
    await page.goto('/');
    await expect(page).toHaveTitle('hotel-curator: hotel quality tiers from guest reviews');
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(
      'Hotel tiers you can check against what guests wrote',
    );
    const numbers = page.getByRole('region', { name: 'The numbers' });
    await expect(numbers.getByText('Hotels graded')).toBeVisible();
    await expect(page.getByRole('table', { name: 'How the 5 hotels are graded' })).toBeVisible();
    await expect(page.getByRole('region', { name: /Excellent/ })).toBeVisible();
    await page.getByRole('link', { name: 'Explore hotels' }).click();
    await expect(page).toHaveURL(/\/explore$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Explore hotels' })).toBeVisible();
  });

  test('"Try the model" jumps to the box, and Back returns to the top', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('link', { name: 'Try the model' }).click();
    await expect(page).toHaveURL(/\/#try$/);
    const heading = page.getByRole('heading', { level: 2, name: 'Try the model' });
    await expect(heading).toBeInViewport();
    await expect(heading).toBeFocused();
    await page.goBack();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole('heading', { level: 1 })).toBeInViewport();
  });

  test('an old explorer link opens the explorer with its filters', async ({ page }) => {
    await page.goto('/?city=Paris&tier=excellent');
    await expect(page).toHaveURL(/\/explore\?city=Paris&tier=excellent$/);
    await expect(page.getByLabel('City')).toHaveValue('Paris');
    await expect(page.getByRole('checkbox', { name: 'Excellent' })).toBeChecked();
  });

  test('serves robots.txt, the sitemap, the notice and absolute preview URLs', async ({
    page,
    request,
  }) => {
    const robots = await request.get('/robots.txt');
    expect(robots.ok()).toBe(true);
    expect(await robots.text()).toContain('Sitemap: https://hotel-curator.fly.dev/sitemap.xml');
    const sitemap = await request.get('/sitemap.xml');
    expect(await sitemap.text()).toContain('<loc>https://hotel-curator.fly.dev/explore</loc>');
    const notice = await request.get('/NOTICE.txt');
    expect(await notice.text()).toContain('Booking.com');
    for (const path of ['/favicon.ico', '/apple-touch-icon.png', '/og-image.png']) {
      const response = await request.get(path);
      expect(response.headers()['content-type'], path).toMatch(/^image\//);
    }
    await page.goto('/');
    await expect(page.locator('meta[property="og:image"]')).toHaveAttribute(
      'content',
      'https://hotel-curator.fly.dev/og-image.png',
    );
  });
});

test.describe('After a deploy', () => {
  test('a page whose code is gone reloads once, then asks for a reload', async ({ page }) => {
    await page.goto('/');
    // The About page's chunk is missing, as when a deploy renamed it.
    await page.route(/\/assets\/AboutPage-[^/]+\.js$/, (route) =>
      route.fulfill({ status: 404, contentType: 'text/plain', body: 'gone' }),
    );
    let loads = 0;
    page.on('load', () => (loads += 1));
    await page
      .getByRole('navigation', { name: 'Main' })
      .getByRole('link', { name: 'About' })
      .click();
    await expect(
      page.getByRole('heading', { name: 'This page could not be loaded' }),
    ).toBeVisible();
    await expect(page).toHaveURL(/\/about$/);
    // One reload, and the guard stops a second one while the chunk stays gone.
    await page.waitForTimeout(1500);
    expect(loads).toBe(1);
    await page.unroute(/\/assets\/AboutPage-[^/]+\.js$/);
    await page.getByRole('button', { name: 'Reload the page' }).click();
    await expect(
      page.getByRole('heading', { level: 1, name: 'How hotel-curator works' }),
    ).toBeVisible();
  });
});
