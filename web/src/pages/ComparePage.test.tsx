import { screen, waitFor, within } from '@testing-library/react';
import { delay, http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import { comparison } from '@/test/fixtures';
import { apiError } from '@/test/handlers';
import { renderApp } from '@/test/render';
import { server } from '@/test/server';

const A = 'h_00000000a1';
const B = 'h_00000000b2';
const C = 'h_00000000c3';

/** Hotel ids in the remembered compare list (localStorage). */
function remembered(): string[] {
  const raw = window.localStorage.getItem('hotel-curator:compare') ?? '[]';
  return (JSON.parse(raw) as { id: string }[]).map((item) => item.id);
}

function sideBySide(): HTMLElement {
  return within(screen.getByRole('region', { name: 'Side by side' })).getByRole('table');
}

function row(attribute: string): HTMLElement {
  return within(sideBySide())
    .getByRole('rowheader', { name: new RegExp(`^${attribute}`) })
    .closest('tr')!;
}

describe('Compare page', () => {
  it('shows the hotels side by side and highlights the credible leader with text', async () => {
    renderApp(`/compare?ids=${A}&ids=${B}`);
    await screen.findByRole('region', { name: 'Side by side' });

    const headers = within(sideBySide()).getAllByRole('columnheader');
    // The tags (A, B) are visual only; the names are the accessible names.
    expect(headers.map((h) => h.textContent)).toEqual(['Attribute', 'AAlpha Grand', 'BBravo Inn']);
    expect(within(sideBySide()).getByRole('columnheader', { name: 'Alpha Grand' })).toBeVisible();
    expect(within(sideBySide()).getAllByRole('row')).toHaveLength(9);

    const cleanliness = row('Cleanliness');
    const [alphaCell, bravoCell] = within(cleanliness).getAllByRole('cell');
    expect(alphaCell).toHaveTextContent('Credible leader (more than 99% sure)');
    expect(alphaCell).toHaveTextContent('Alpha Grand is credibly best on Cleanliness.');
    expect(bravoCell).not.toHaveTextContent('Credible leader');
    expect(bravoCell).toHaveTextContent('Weak');

    // The ranges do not overlap: Bravo is clearly behind, also in text.
    expect(bravoCell).toHaveTextContent('Clearly behind Alpha Grand (ranges do not overlap)');
    expect(alphaCell).not.toHaveTextContent('Clearly behind');

    // No leader: the row says so in text, and no cell is highlighted.
    const room = row('Room');
    expect(room).toHaveTextContent('No single credible leader');
    expect(room).not.toHaveTextContent('Credible leader (');
    expect(room).not.toHaveTextContent('Clearly behind');

    // Never claims that an unhighlighted gap is noise.
    expect(screen.queryByText(/not credible: they could be noise/)).not.toBeInTheDocument();
    expect(screen.getByText(/Other gaps may still be real or may be noise/)).toBeInTheDocument();
    expect(screen.getByText(/Credible leader: Alpha Grand\./)).toBeInTheDocument();
  });

  it('marks a real gap as "clearly behind" when no single hotel leads', async () => {
    renderApp(`/compare?ids=${A}&ids=${B}&ids=${C}`);
    await screen.findByRole('region', { name: 'Side by side' });
    const cleanliness = row('Cleanliness');
    // Alpha (96-100%) and Charlie (95-99%) overlap: neither leads alone ...
    expect(cleanliness).toHaveTextContent('No single credible leader');
    expect(cleanliness).not.toHaveTextContent('Credible leader (');
    const [alphaCell, bravoCell, charlieCell] = within(cleanliness).getAllByRole('cell');
    // ... but Bravo (0.2-3.6%) is clearly behind both, and says so.
    expect(bravoCell).toHaveTextContent('Clearly behind Alpha Grand and Charlie House');
    expect(alphaCell).not.toHaveTextContent('Clearly behind');
    expect(charlieCell).not.toHaveTextContent('Clearly behind');
    // Every chart caption says no hotel leads alone (no leader in this fixture).
    expect(
      screen.getAllByText(/No hotel is credibly better than all the others here\./),
    ).toHaveLength(8);
  });

  it('lays out values by hotel id, whatever their order in the row', async () => {
    server.use(
      http.get('*/api/v1/compare', () => {
        const data = comparison([A, B]);
        data.attributes = data.attributes.map((r) => ({ ...r, values: [...r.values].reverse() }));
        return HttpResponse.json(data);
      }),
    );
    renderApp(`/compare?ids=${A}&ids=${B}`);
    await screen.findByRole('region', { name: 'Side by side' });
    const [alphaCell, bravoCell] = within(row('Cleanliness')).getAllByRole('cell');
    expect(alphaCell).toHaveTextContent('Credible leader');
    expect(bravoCell).toHaveTextContent('Weak');
  });

  it('removes a hotel and keeps the selection in the URL', async () => {
    const { user, search } = renderApp(`/compare?ids=${A}&ids=${B}&ids=${C}`);
    await screen.findByRole('region', { name: 'Side by side' });
    expect(within(sideBySide()).getAllByRole('columnheader')).toHaveLength(4);

    await user.click(
      screen.getByRole('button', { name: 'Remove Charlie House from the comparison' }),
    );
    await waitFor(() => expect(search().getAll('ids')).toEqual([A, B]));
    await waitFor(() => expect(within(sideBySide()).getAllByRole('columnheader')).toHaveLength(3));
    // Focus moves to the remaining hotel's Remove button, and the change is announced.
    expect(
      screen.getByRole('button', { name: 'Remove Bravo Inn from the comparison' }),
    ).toHaveFocus();
    expect(screen.getByText('Charlie House was removed from the comparison.')).toHaveAttribute(
      'role',
      'status',
    );

    await user.click(screen.getByRole('button', { name: 'Remove Bravo Inn from the comparison' }));
    await waitFor(() => expect(search().getAll('ids')).toEqual([A]));
    expect(await screen.findByText(/Pick two to 4 hotels to compare/)).toBeInTheDocument();
    // The comparison ended: focus is on the page heading, not lost on <body>.
    expect(screen.getByRole('heading', { level: 1, name: 'Compare hotels' })).toHaveFocus();
  });

  it('does not put a removed hotel back while the next comparison loads', async () => {
    const { user, search } = renderApp(`/compare?ids=${A}&ids=${B}&ids=${C}`);
    await screen.findByRole('region', { name: 'Side by side' });
    await waitFor(() => expect(remembered()).toEqual([A, B, C]));
    server.use(
      http.get('*/api/v1/compare', async () => {
        await delay(300);
        return HttpResponse.json(comparison([A, B]));
      }),
    );
    await user.click(
      screen.getByRole('button', { name: 'Remove Charlie House from the comparison' }),
    );
    await waitFor(() => expect(search().getAll('ids')).toEqual([A, B]));
    // While the old comparison is still on screen, it says so ...
    expect(await screen.findByText('Updating the comparison...')).toBeInTheDocument();
    // ... and the remembered list does not get Charlie back.
    expect(remembered()).toEqual([A, B]);
    await waitFor(() => expect(within(sideBySide()).getAllByRole('columnheader')).toHaveLength(3));
    expect(remembered()).toEqual([A, B]);
    expect(
      within(screen.getByRole('navigation', { name: 'Main' })).getByRole('link', {
        name: /Compare/,
      }),
    ).toHaveTextContent('2');
  });

  it('does not put a removed hotel back when the next comparison fails', async () => {
    const { user, search } = renderApp(`/compare?ids=${A}&ids=${B}&ids=${C}`);
    await screen.findByRole('region', { name: 'Side by side' });
    await waitFor(() => expect(remembered()).toEqual([A, B, C]));
    server.use(
      http.get('*/api/v1/compare', () =>
        apiError(
          429,
          'rate_limited',
          'Too many requests; try again later.',
          {},
          {
            'Retry-After': '1',
          },
        ),
      ),
    );
    await user.click(
      screen.getByRole('button', { name: 'Remove Charlie House from the comparison' }),
    );
    await waitFor(() => expect(search().getAll('ids')).toEqual([A, B]));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Try again in 1 second.');
    expect(remembered()).toEqual([A, B]);
  });

  it('asks for at least two hotels', async () => {
    renderApp('/compare');
    expect(await screen.findByText(/Pick two to 4 hotels to compare/)).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('drops invalid and repeated ids from the link', async () => {
    renderApp(`/compare?ids=${A}&ids=${A}&ids=bad&ids=${B}`);
    await screen.findByRole('region', { name: 'Side by side' });
    expect(screen.getByRole('note')).toHaveTextContent('2 hotel ids in the link were not valid');
  });

  it('shows the API error for an unknown hotel', async () => {
    renderApp(`/compare?ids=${A}&ids=h_ffffffffff`);
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Hotel not found.');
    expect(alert).toHaveTextContent('req-404-test');
    expect(
      within(alert).getByRole('button', { name: 'Start a new comparison' }),
    ).toBeInTheDocument();
  });

  it('is reachable from the explorer through the compare list', async () => {
    const { user, router } = renderApp('/explore');
    await user.click(
      within(await screen.findByRole('article', { name: 'Alpha Grand' })).getByRole('button', {
        name: /Add to compare/,
      }),
    );
    await user.click(
      within(screen.getByRole('article', { name: 'Bravo Inn' })).getByRole('button', {
        name: /Add to compare/,
      }),
    );
    await user.click(
      within(screen.getByRole('navigation', { name: 'Main' })).getByRole('link', {
        name: /Compare/,
      }),
    );
    await waitFor(() => expect(router.state.location.pathname).toBe('/compare'));
    expect(new URLSearchParams(router.state.location.search).getAll('ids')).toEqual([A, B]);
    expect(await screen.findByRole('region', { name: 'Side by side' })).toBeInTheDocument();
  });
});
