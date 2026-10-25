import '@testing-library/jest-dom/vitest';

import { cleanup, configure } from '@testing-library/react';
import { afterAll, afterEach, beforeAll } from 'vitest';

import { server } from './server';

// Lazy routes and a busy CI machine can take more than the default 1 s.
configure({ asyncUtilTimeout: 5000 });

// jsdom lacks these browser APIs; the app and its libraries only need stubs.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver;
const browser = typeof window !== 'undefined';
if (browser) window.scrollTo = () => {};

beforeAll(() => server.listen({ onUnhandledFrame: 'error' }));
afterEach(() => {
  server.resetHandlers();
  if (!browser) return;
  cleanup();
  window.localStorage.clear();
  delete document.documentElement.dataset.theme;
});
afterAll(() => server.close());
