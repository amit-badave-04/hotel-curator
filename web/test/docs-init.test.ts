// @vitest-environment node
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { runInNewContext } from 'node:vm';

import { describe, expect, it } from 'vitest';

const SCRIPT = readFileSync(
  fileURLToPath(
    new URL('../../src/hotel_curator/api/static/swagger-ui/docs-init.js', import.meta.url),
  ),
  'utf8',
);

/** The server URL the API docs page gives "Try it out" when opened at `href`. */
async function serverUrl(href: string): Promise<string> {
  let onLoad: () => void = () => {};
  let options: { spec: { servers: { url: string }[] } } | undefined;
  const window = {
    location: new URL(href),
    addEventListener: (_event: string, listener: () => void) => {
      onLoad = listener;
    },
  };
  runInNewContext(SCRIPT, {
    window,
    fetch: async () => ({ json: async () => ({ openapi: '3.1.0' }) }),
    SwaggerUIBundle: Object.assign((given: typeof options) => (options = given), {
      presets: { apis: {} },
    }),
  });
  onLoad();
  await new Promise((resolve) => setTimeout(resolve, 0));
  return options?.spec.servers[0].url ?? '';
}

describe('the API docs page (docs-init.js)', () => {
  it('sends "Try it out" to the prefix it was served under', async () => {
    expect(await serverUrl('https://site.example/api/docs')).toBe('https://site.example/api');
    expect(await serverUrl('http://127.0.0.1:8000/docs')).toBe('http://127.0.0.1:8000');
  });

  it('never takes the host from a crafted path nginx normalises', async () => {
    const crafted = await serverUrl('https://site.example//evil.example/..%2Fapi/docs');
    expect(crafted).toBe('https://site.example/api');
  });
});
