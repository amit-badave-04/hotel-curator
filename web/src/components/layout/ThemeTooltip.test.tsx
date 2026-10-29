import { screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { renderApp } from '@/test/render';

// The tooltip's code fails to load, as on a flaky connection.
vi.mock('@/components/ui/tooltip', () => {
  throw new Error('Failed to fetch dynamically imported module: /assets/tooltip-x.js');
});

describe('theme button without its tooltip', () => {
  it('keeps the page and the button working', async () => {
    const { user } = renderApp('/');
    const button = await screen.findByRole('button', { name: 'Use dark theme' });
    await vi.dynamicImportSettled();
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Hotel tiers');
    expect(screen.getByRole('button', { name: 'Use dark theme' })).toBe(button);
    await user.click(button);
    expect(document.documentElement.dataset.theme).toBe('dark');
  });
});
