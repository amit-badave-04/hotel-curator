import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import { alpha, summary } from '@/test/fixtures';
import { apiError } from '@/test/handlers';
import { renderApp } from '@/test/render';
import { server } from '@/test/server';

function card(name: string): HTMLElement {
  return screen.getByRole('article', { name });
}

describe('Explorer page', () => {
  it('shows each hotel with tier label, essentials, catches, review count and a why line', async () => {
    renderApp('/');
    expect(await screen.findByRole('heading', { level: 1, name: 'Explore hotels' })).toBeVisible();
    expect(document.title).toBe('Explore hotels | hotel-curator');
    await screen.findByRole('article', { name: 'Charlie House' });

    const charlie = card('Charlie House');
    expect(within(charlie).getByText('Good, but noisy')).toBeInTheDocument();
    expect(within(charlie).getByText('London, United Kingdom')).toBeInTheDocument();
    for (const essential of ['Cleanliness', 'Room', 'Staff']) {
      expect(within(charlie).getByText(essential)).toBeInTheDocument();
    }
    expect(within(charlie).getAllByText('Strong')).toHaveLength(3);
    expect(within(charlie).getByText('Catch:')).toBeInTheDocument();
    expect(within(charlie).getByText('noise')).toBeInTheDocument();
    expect(within(charlie).getByText('120 reviews used')).toBeInTheDocument();
    expect(
      within(charlie).getByText(
        'Guests confirmed cleanliness, room and staff as strengths; complaints about noise hold it back.',
      ),
    ).toBeInTheDocument();
    expect(within(charlie).getByRole('link', { name: 'Charlie House' })).toHaveAttribute(
      'href',
      '/hotels/h_00000000c3',
    );
    expect(screen.getByText('5 hotels match')).toHaveAttribute('role', 'status');
  });

  it('reads the two "not enough" tiers as not enough evidence, not as bad', async () => {
    renderApp('/');
    const delta = await screen.findByRole('article', { name: 'Delta Rooms' });
    expect(within(delta).getByText('Not enough evidence to judge')).toBeInTheDocument();
    expect(within(delta).queryByText('Caution')).not.toBeInTheDocument();
    expect(within(delta).getByText(/at least 30 are needed/)).toBeInTheDocument();
    const echo = card('Echo Stay');
    expect(within(echo).getByText('Not enough evidence to judge')).toBeInTheDocument();
  });

  it('puts every filter in the URL query string', async () => {
    const { user, search } = renderApp('/');
    await screen.findByRole('article', { name: 'Alpha Grand' });

    await user.selectOptions(screen.getByLabelText('City'), 'Paris');
    await waitFor(() => expect(search().get('city')).toBe('Paris'));

    await user.click(screen.getByRole('checkbox', { name: 'Excellent' }));
    await waitFor(() => expect(search().getAll('tier')).toEqual(['excellent']));

    const strongIn = screen.getByRole('group', { name: 'Strong in' });
    await user.click(within(strongIn).getByRole('checkbox', { name: 'Location' }));
    const notWeak = screen.getByRole('group', { name: 'Not weak in' });
    await user.click(within(notWeak).getByRole('checkbox', { name: 'Noise' }));
    await user.selectOptions(screen.getByLabelText('Sort by'), 'name');
    await user.type(screen.getByLabelText('Hotel name contains'), 'alpha{Enter}');

    await waitFor(() => {
      const params = search();
      expect(params.get('q')).toBe('alpha');
      expect(params.get('city')).toBe('Paris');
      expect(params.getAll('tier')).toEqual(['excellent']);
      expect(params.getAll('strong')).toEqual(['location']);
      expect(params.getAll('avoid_weak')).toEqual(['noise']);
      expect(params.get('sort')).toBe('name');
    });
    await waitFor(() => expect(screen.getAllByRole('article')).toHaveLength(1));
    expect(screen.getByRole('article', { name: 'Alpha Grand' })).toBeInTheDocument();
  });

  it('maps "Not enough evidence to judge" to both underlying tiers', async () => {
    const { user, search } = renderApp('/');
    await screen.findByRole('article', { name: 'Alpha Grand' });
    await user.click(screen.getByRole('checkbox', { name: 'Not enough evidence to judge' }));
    await waitFor(() =>
      expect(search().getAll('tier')).toEqual(['not_enough_evidence', 'not_enough_reviews']),
    );
    await waitFor(() => expect(screen.getAllByRole('article')).toHaveLength(2));
    // One box, one filter, although it puts two tiers in the URL.
    expect(screen.getByRole('heading', { level: 2, name: /^Filters/ })).toHaveTextContent(
      /\(1 active\)$/,
    );
  });

  it('checks "Not enough evidence to judge" for a URL with only one of its tiers', async () => {
    renderApp('/?tier=not_enough_reviews');
    await waitFor(() => expect(screen.getAllByRole('article')).toHaveLength(2));
    expect(screen.getByRole('article', { name: 'Echo Stay' })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Not enough evidence to judge' })).toBeChecked();
    expect(screen.getByRole('heading', { level: 2, name: /^Filters/ })).toHaveTextContent(
      /\(1 active\)$/,
    );
  });

  it('reads filters from the URL, ignoring invalid values', async () => {
    renderApp('/?city=London&tier=good&tier=bogus&strong=nope&page=-3');
    await waitFor(() => expect(screen.getAllByRole('article')).toHaveLength(1));
    expect(screen.getByRole('article', { name: 'Charlie House' })).toBeInTheDocument();
    expect(screen.getByLabelText('City')).toHaveValue('London');
    expect(screen.getByRole('checkbox', { name: 'Good' })).toBeChecked();
  });

  it('shows an empty state with a way out', async () => {
    const { user, search } = renderApp('/?q=nothing-like-this');
    expect(await screen.findByText('No hotels match these filters')).toBeInTheDocument();
    const emptyClear = screen.getAllByRole('button', { name: 'Clear filters' });
    await user.click(emptyClear[emptyClear.length - 1]!);
    await waitFor(() => expect(search().toString()).toBe(''));
    expect(await screen.findByRole('article', { name: 'Alpha Grand' })).toBeInTheDocument();
    // The button is gone; focus is on the results heading, not lost on <body>.
    expect(screen.getByRole('heading', { level: 2, name: 'Results' })).toHaveFocus();
  });

  it("keeps the form's Clear filters button focusable once the filters are cleared", async () => {
    const { user, search } = renderApp('/?city=Paris');
    await screen.findByRole('article', { name: 'Alpha Grand' });
    const clear = within(screen.getByRole('search', { name: 'Filter hotels' })).getByRole(
      'button',
      { name: 'Clear filters' },
    );
    await user.click(clear);
    await waitFor(() => expect(search().toString()).toBe(''));
    expect(clear).toHaveAttribute('aria-disabled', 'true');
    expect(clear).toHaveFocus();
  });

  it('names noise and wifi as strengths without reading as "noisy"', async () => {
    const { user, search } = renderApp('/');
    const alpha = await screen.findByRole('article', { name: 'Alpha Grand' });
    expect(alpha).toHaveTextContent(
      'Also strong: location, quiet (few noise complaints), wifi (few complaints) and value',
    );
    const strongIn = screen.getByRole('group', { name: 'Strong in' });
    await user.click(
      within(strongIn).getByRole('checkbox', { name: 'Quiet (few noise complaints)' }),
    );
    await waitFor(() => expect(search().getAll('strong')).toEqual(['noise']));
    expect(screen.getByText(/not a judgement on them/)).toBeInTheDocument();
  });

  it('shows the API error message and request id', async () => {
    server.use(
      http.get('*/api/v1/hotels', () =>
        apiError(
          429,
          'rate_limited',
          'Too many requests; try again later.',
          {},
          {
            'Retry-After': '12',
          },
        ),
      ),
    );
    renderApp('/');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('The hotel list could not be loaded');
    expect(alert).toHaveTextContent('Too many requests; try again later.');
    expect(alert).toHaveTextContent('Try again in 12 seconds.');
    expect(alert).toHaveTextContent('req-429-test');
    expect(within(alert).getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('paginates through server pages with links', async () => {
    server.use(
      http.get('*/api/v1/hotels', ({ request }) => {
        const page = Number(new URL(request.url).searchParams.get('page') ?? '1');
        return HttpResponse.json({
          items: [summary(alpha)],
          total: 1500,
          page,
          page_size: 20,
        });
      }),
    );
    renderApp('/?page=2&city=Paris');
    const nav = await screen.findByRole('navigation', { name: 'Pagination' });
    expect(nav).toHaveTextContent('Page 2 of 75');
    expect(within(nav).getByRole('link', { name: /Next/ })).toHaveAttribute(
      'href',
      '/?city=Paris&page=3',
    );
    expect(within(nav).getByRole('link', { name: /Previous/ })).toHaveAttribute(
      'href',
      '/?city=Paris',
    );
  });

  it('moves focus to the results heading when a page link is used', async () => {
    server.use(
      http.get('*/api/v1/hotels', ({ request }) => {
        const page = Number(new URL(request.url).searchParams.get('page') ?? '1');
        return HttpResponse.json({ items: [summary(alpha)], total: 40, page, page_size: 20 });
      }),
    );
    const { user, search } = renderApp('/?page=1');
    const nav = await screen.findByRole('navigation', { name: 'Pagination' });
    // On the last page "Next" is no longer a link; focus must not be lost with it.
    await user.click(within(nav).getByRole('link', { name: /Next/ }));
    await waitFor(() => expect(search().get('page')).toBe('2'));
    expect(screen.getByRole('heading', { level: 2, name: 'Results' })).toHaveFocus();
  });

  it('adds hotels to the compare list', async () => {
    const { user } = renderApp('/');
    const alpha = await screen.findByRole('article', { name: 'Alpha Grand' });
    await user.click(within(alpha).getByRole('button', { name: /Add to compare/ }));
    expect(within(alpha).getByRole('button', { name: /Remove from compare/ })).toBeInTheDocument();
    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(within(nav).getByRole('link', { name: /Compare/ })).toHaveAttribute(
      'href',
      '/compare?ids=h_00000000a1',
    );
  });
});
