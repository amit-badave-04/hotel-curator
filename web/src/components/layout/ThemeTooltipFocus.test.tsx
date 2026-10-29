import { screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { renderApp } from '@/test/render';

// The tooltip's code arrives only when the test lets it.
const gate = vi.hoisted(() => {
  let open = () => {};
  const ready = new Promise<void>((resolve) => (open = resolve));
  return { ready, open: () => open() };
});
vi.mock('@/components/ui/tooltip', async (importOriginal) => {
  await gate.ready;
  return importOriginal();
});

describe('theme button when its tooltip arrives', () => {
  it('keeps keyboard focus on the button', async () => {
    renderApp('/');
    const before = await screen.findByRole('button', { name: 'Use dark theme' });
    before.focus();
    gate.open();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Use dark theme' })).not.toBe(before),
    );
    expect(screen.getByRole('button', { name: 'Use dark theme' })).toHaveFocus();
  });
});
