import { afterEach, describe, expect, it, vi } from 'vitest';

import { isStaleChunkError, page, reloadOnce } from '@/lib/staleChunk';

afterEach(() => window.sessionStorage.clear());

describe('stale chunks after a deploy', () => {
  it('recognises a failed page load in each browser', () => {
    for (const message of [
      'Failed to fetch dynamically imported module: https://x.example/assets/About-1a2b.js',
      'error loading dynamically imported module: https://x.example/assets/About-1a2b.js',
      'Importing a module script failed.',
      'Unable to preload CSS for /assets/About-1a2b.css',
    ]) {
      expect(isStaleChunkError(new Error(message)), message).toBe(true);
    }
    expect(isStaleChunkError(new Error('Cannot read properties of undefined'))).toBe(false);
    expect(isStaleChunkError('Failed to fetch dynamically imported module')).toBe(false);
  });

  it('reloads once per 30 seconds', () => {
    const reload = vi.spyOn(page, 'reload').mockImplementation(() => {});
    expect(reloadOnce(1_000_000)).toBe(true);
    expect(reloadOnce(1_010_000)).toBe(false);
    expect(reloadOnce(1_031_000)).toBe(true);
    expect(reload).toHaveBeenCalledTimes(2);
  });

  it('does not reload while the browser is offline', () => {
    const reload = vi.spyOn(page, 'reload').mockImplementation(() => {});
    vi.spyOn(window.navigator, 'onLine', 'get').mockReturnValue(false);
    expect(reloadOnce()).toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });

  it('never reloads by itself without sessionStorage', () => {
    const reload = vi.spyOn(page, 'reload').mockImplementation(() => {});
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(reloadOnce()).toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });
});
