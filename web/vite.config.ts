import { fileURLToPath, URL } from 'node:url';

import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

import { SECURITY_HEADERS } from './security-headers.ts';

/** Where the FastAPI app listens; the browser only ever sees `/api`. */
const API_TARGET = process.env.CURATOR_API_URL ?? 'http://127.0.0.1:8000';

const apiProxy = {
  // `/api/v1/hotels` -> `http://127.0.0.1:8000/v1/hotels`
  '^/api(/|$)': {
    target: API_TARGET,
    changeOrigin: true,
    rewrite: (path: string) => path.replace(/^\/api/, '') || '/',
  },
  // FastAPI's docs page (served at /api/docs) loads the schema from /openapi.json.
  '/openapi.json': {
    target: API_TARGET,
    changeOrigin: true,
  },
};

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: apiProxy,
  },
  preview: {
    host: '127.0.0.1',
    port: 4173,
    strictPort: true,
    proxy: apiProxy,
    headers: SECURITY_HEADERS,
  },
  build: {
    sourcemap: false,
    target: 'es2022',
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}', 'test/**/*.test.ts'],
    css: false,
    restoreMocks: true,
    testTimeout: 15_000,
    pool: 'forks',
    maxWorkers: 2,
  },
});
