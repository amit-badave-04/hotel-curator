import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { RouteError } from '@/components/layout/RouteError';
import { page } from '@/lib/staleChunk';

function failingRoute(error: Error) {
  const router = createMemoryRouter(
    [
      {
        path: '/',
        errorElement: <RouteError />,
        lazy: async () => {
          throw error;
        },
      },
    ],
    { initialEntries: ['/'] },
  );
  return render(<RouterProvider router={router} />);
}

afterEach(() => window.sessionStorage.clear());

describe('RouteError', () => {
  it('reloads once when a page from before a deploy is gone', async () => {
    const reload = vi.spyOn(page, 'reload').mockImplementation(() => {});
    failingRoute(new Error('Failed to fetch dynamically imported module: /assets/About-1a2b.js'));
    expect(
      await screen.findByRole('heading', { name: 'This page could not be loaded' }),
    ).toBeInTheDocument();
    expect(reload).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole('button', { name: 'Reload the page' }));
    expect(reload).toHaveBeenCalledTimes(2);
  });

  it('shows other errors without reloading', async () => {
    const reload = vi.spyOn(page, 'reload').mockImplementation(() => {});
    failingRoute(new Error('Boom'));
    expect(await screen.findByRole('heading', { name: 'Something went wrong' })).toBeVisible();
    expect(screen.getByText('Details: Boom')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Back to the explorer' })).toHaveAttribute(
      'href',
      '/explore',
    );
    expect(reload).not.toHaveBeenCalled();
  });
});
