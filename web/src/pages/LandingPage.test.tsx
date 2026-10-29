import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import type { Meta } from '@/api/generated/model';
import { EVALUATION, meta } from '@/test/fixtures';
import { apiError } from '@/test/handlers';
import { renderApp } from '@/test/render';
import { server } from '@/test/server';

function region(name: string | RegExp): HTMLElement {
  return screen.getByRole('region', { name });
}

function serveMeta(body: Meta) {
  server.use(http.get('*/api/v1/meta', () => HttpResponse.json(body)));
}

async function openLanding() {
  const app = renderApp('/');
  await screen.findByRole('heading', { level: 1 });
  return app;
}

describe('Landing page', () => {
  it('says what the product is and links to the explorer and the model', async () => {
    await openLanding();
    expect(document.title).toBe('hotel-curator: hotel quality tiers from guest reviews');
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(
      'Hotel tiers you can check against what guests wrote',
    );
    expect(screen.getByRole('link', { name: 'Explore hotels' })).toHaveAttribute(
      'href',
      '/explore',
    );
    expect(screen.getByRole('link', { name: 'Try the model' })).toHaveAttribute('href', '/#try');
    const example = screen.getByRole('figure', { name: /not a real hotel/ });
    expect(within(example).getByText('Good, but noisy')).toBeInTheDocument();
    expect(within(example).getByText('Weak')).toBeInTheDocument();
  });

  it('explains the method in three linked steps with the live confirmation level', async () => {
    const live = meta();
    live.policy = { ...live.policy, confirmation: 0.9 };
    serveMeta(live);
    await openLanding();
    const how = region('How it works, in three steps');
    const steps = within(how).getAllByRole('listitem');
    expect(steps).toHaveLength(3);
    await waitFor(() => expect(steps[1]).toHaveTextContent('at least 90% sure'));
    expect(within(how).getByRole('link', { name: 'How tiers work' })).toHaveAttribute(
      'href',
      '/about#tiers',
    );
  });

  it('shows the headline numbers from /v1/meta with their intervals', async () => {
    serveMeta(meta(EVALUATION));
    await openLanding();
    const numbers = region('The numbers');
    const tile = async (label: string) =>
      (await within(numbers).findByText(label)).parentElement as HTMLElement;
    expect(await tile('Hotels graded')).toHaveTextContent('5In London, Paris and Vienna');
    expect(await tile('Model score (macro-F1)')).toHaveTextContent(
      '0.712Average F1 over the 16 labels; 95% interval 0.681 to 0.741, on 1,200 held-out test reviews from 182 hotels',
    );
    expect(await tile('Gain over the first version')).toHaveTextContent(
      '+0.10895% interval +0.079 to +0.137; v1 scored 0.604',
    );
    expect(await tile('Calibration error')).toHaveTextContent('0.031');
    expect(numbers).not.toHaveTextContent('not published');
  });

  it('says so when the server has no test scores', async () => {
    await openLanding();
    const numbers = region('The numbers');
    expect(await within(numbers).findByText(/test scores are not published/)).toBeInTheDocument();
    expect(within(numbers).queryByText('Model score (macro-F1)')).toBeNull();
  });

  it('counts hotels per tier, the two "not enough" tiers together', async () => {
    await openLanding();
    const table = await screen.findByRole('table', { name: 'How the 5 hotels are graded' });
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows.map((row) => row.textContent)).toEqual([
      'Tier: Excellent120%',
      'Tier: Good120%',
      'Tier: Fair00%',
      'Tier: Caution120%',
      'Tier: Not enough evidence to judge(too few reviews or opinions)240%',
    ]);
  });

  it('shows a loading state, then an error with a retry', async () => {
    let calls = 0;
    server.use(
      http.get('*/api/v1/meta', () => {
        calls += 1;
        return calls === 1
          ? apiError(500, 'internal_error', 'Something broke.')
          : HttpResponse.json(meta());
      }),
    );
    const { user } = renderApp('/');
    expect(screen.getByText('Loading the numbers').closest('[role="status"]')).not.toBeNull();
    const numbers = region('The numbers');
    const alert = await within(numbers).findByRole('alert');
    expect(alert).toHaveTextContent('The numbers could not be loaded');
    expect(alert).toHaveTextContent('req-500-test');
    await user.click(within(alert).getByRole('button', { name: 'Try again' }));
    expect(await within(numbers).findByText('Hotels graded')).toBeInTheDocument();
  });
});

describe('The review period', () => {
  it('comes from /v1/meta, in the hero and the questions', async () => {
    await openLanding();
    const hero = screen.getByRole('region', { name: /Hotel tiers you can check/ });
    await waitFor(() =>
      expect(hero).toHaveTextContent('Reviews from September 2015 to August 2017.'),
    );
    expect(region('Questions')).toHaveTextContent(
      'The reviews date from September 2015 to August 2017.',
    );
  });

  it('is left out, not guessed, when the server has not recorded it', async () => {
    serveMeta({ ...meta(), data_period: null });
    await openLanding();
    await screen.findByText('Hotels graded');
    const hero = screen.getByRole('region', { name: /Hotel tiers you can check/ });
    expect(hero).not.toHaveTextContent('Reviews from');
    expect(region('Questions')).toHaveTextContent('The reviews cover a fixed period in the past.');
  });
});

describe('Excellent, honestly', () => {
  it('explains why no hotel is Excellent, from the live counts and policy', async () => {
    const live = meta();
    live.tiers = { ...live.tiers, excellent: 0, good: 58 };
    live.policy = { ...live.policy, excellent: { min_months: 9, max_month_share: 0.3 } };
    serveMeta(live);
    await openLanding();
    const note = await screen.findByRole('region', { name: 'Why no hotel is Excellent' });
    expect(note).toHaveTextContent('None of the 5 hotels is rated Excellent.');
    expect(note).toHaveTextContent(
      'cleanliness, room and staff all confirmed Strong (at least 95% sure)',
    );
    expect(note).toHaveTextContent(
      'reviews from at least 9 different months, with no month holding more than 30% of them.',
    );
    expect(within(note).getByRole('link', { name: '58 Good hotels' })).toHaveAttribute(
      'href',
      '/explore?tier=good',
    );
  });

  it('links to the Excellent hotels when there are some', async () => {
    await openLanding();
    const note = await screen.findByRole('region', { name: 'What it takes to be Excellent' });
    expect(note).toHaveTextContent('1 hotel of 5 is rated Excellent.');
    expect(within(note).getByRole('link', { name: 'See the Excellent hotels' })).toHaveAttribute(
      'href',
      '/explore?tier=excellent',
    );
  });
});

describe('Try the model on the landing page', () => {
  it('jumps there with focus on the section, so Tab reaches the form', async () => {
    const { user, router } = await openLanding();
    await user.click(screen.getByRole('link', { name: 'Try the model' }));
    expect(router.state.location.hash).toBe('#try');
    expect(screen.getByRole('heading', { level: 2, name: 'Try the model' })).toHaveFocus();
    await user.tab();
    expect(screen.getByLabelText('What the guest liked')).toHaveFocus();
  });

  it('says no model is loaded when /v1/meta says so', async () => {
    serveMeta(meta(null, false));
    await openLanding();
    const panel = region('Try the model');
    expect(await within(panel).findByText('No model loaded')).toBeInTheDocument();
    expect(within(panel).queryByRole('textbox')).toBeNull();
  });

  it('says no model is loaded when the API answers 503', async () => {
    const { user } = await openLanding();
    const panel = region('Try the model');
    await user.type(await within(panel).findByLabelText('What the guest liked'), 'Nice staff');
    await user.click(within(panel).getByRole('button', { name: 'Analyse review' }));
    expect(await within(panel).findByRole('alert')).toHaveTextContent('No model loaded');
  });

  it('shows neither form nor notice until /v1/meta says which', async () => {
    let answer: (() => void) | undefined;
    server.use(
      http.get('*/api/v1/meta', async () => {
        await new Promise<void>((resolve) => (answer = resolve));
        return HttpResponse.json(meta(null, false));
      }),
    );
    renderApp('/');
    const panel = await screen.findByRole('region', { name: 'Try the model' });
    expect(within(panel).getByText('Loading the model status')).toBeInTheDocument();
    expect(within(panel).queryByRole('textbox')).toBeNull();
    await waitFor(() => expect(answer).toBeDefined());
    answer?.();
    expect(await within(panel).findByText('No model loaded')).toBeInTheDocument();
    expect(within(panel).queryByRole('textbox')).toBeNull();
  });
});

describe('Questions and old links', () => {
  it('answers the common questions and links the notice and the issue tracker', async () => {
    await openLanding();
    const faq = region('Questions');
    expect(faq.querySelectorAll('details')).toHaveLength(7);
    expect(within(faq).getByText('Is this how the hotels are today?')).toBeInTheDocument();
    expect(within(faq).getByRole('link', { name: 'attribution notice' })).toHaveAttribute(
      'href',
      '/NOTICE.txt',
    );
    expect(within(faq).getByRole('link', { name: 'open an issue on GitHub' })).toHaveAttribute(
      'href',
      'https://github.com/amit-badave-04/hotel-curator/issues',
    );
  });

  it('sends an old explorer link on to /explore with valid filters only', async () => {
    const { router } = renderApp('/?city=Paris&tier=good&tier=bogus');
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Explore hotels' }),
    ).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/explore');
    expect(router.state.location.search).toBe('?city=Paris&tier=good');
  });

  it('keeps other query strings on the landing page', async () => {
    const { router } = renderApp('/?utm_source=mail');
    expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent(
      'Hotel tiers you can check',
    );
    expect(router.state.location.pathname).toBe('/');
  });
});
