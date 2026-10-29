import { screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { renderApp } from '@/test/render';

describe('App shell', () => {
  it('has a skip link, landmarks and the scope statement', async () => {
    renderApp('/');
    expect(screen.getByRole('link', { name: 'Skip to content' })).toHaveAttribute('href', '#main');
    expect(screen.getByRole('banner')).toBeInTheDocument();
    expect(screen.getByRole('main')).toHaveAttribute('id', 'main');
    expect(screen.getByRole('contentinfo')).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole('contentinfo')).toHaveTextContent(
        'Based on guest reviews from September 2015 to August 2017 for 5 hotels in London, Paris and Vienna.',
      ),
    );
  });

  it('toggles the theme and remembers the choice', async () => {
    const { user } = renderApp('/');
    const html = document.documentElement;
    expect(html.dataset.theme).toBe('light');
    await user.click(screen.getByRole('button', { name: 'Use dark theme' }));
    expect(html.dataset.theme).toBe('dark');
    expect(window.localStorage.getItem('hotel-curator:theme')).toBe('dark');
    await user.click(screen.getByRole('button', { name: 'Use light theme' }));
    expect(html.dataset.theme).toBe('light');
  });

  it('shows a not-found page for unknown paths', async () => {
    renderApp('/nowhere');
    expect(await screen.findByRole('heading', { name: 'Page not found' })).toBeInTheDocument();
    const main = screen.getByRole('main');
    expect(within(main).getByRole('link', { name: 'Go to the explorer' })).toHaveAttribute(
      'href',
      '/explore',
    );
    expect(within(main).getByRole('link', { name: 'the home page' })).toHaveAttribute('href', '/');
  });

  it('links Explore to /explore and the footer to the source, NOTICE and issues', async () => {
    renderApp('/about');
    const nav = await screen.findByRole('navigation', { name: 'Main' });
    expect(within(nav).getByRole('link', { name: 'Explore' })).toHaveAttribute('href', '/explore');
    expect(screen.getByRole('link', { name: 'hotel-curator' })).toHaveAttribute('href', '/');
    const footer = screen.getByRole('contentinfo');
    const href = (name: RegExp) => within(footer).getByRole('link', { name }).getAttribute('href');
    expect(href(/Source code on GitHub/)).toBe('https://github.com/amit-badave-04/hotel-curator');
    expect(href(/NOTICE/)).toBe('/NOTICE.txt');
    expect(href(/Report a mistake/)).toBe('https://github.com/amit-badave-04/hotel-curator/issues');
    expect(href(/API documentation/)).toBe('/api/docs');
    // Without the query string; the origin is the build's (seo.ts default).
    const canonical = document.head.querySelector('link[rel="canonical"]');
    expect(canonical).toHaveAttribute('href', 'https://hotel-curator.fly.dev/about');
    expect(footer).toHaveTextContent('not affiliated with Booking.com');
  });
});
