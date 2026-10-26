// @vitest-environment node
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { CONTENT_SECURITY_POLICY, SECURITY_HEADERS } from '../security-headers';

const nginx = readFileSync(fileURLToPath(new URL('../nginx.conf', import.meta.url)), 'utf8');

function serverHeader(name: string): string | undefined {
  const match = new RegExp(`^ {4}add_header ${name} "([^"]*)" always;$`, 'm').exec(nginx);
  return match?.[1];
}

describe('nginx.conf', () => {
  it('sends the same security headers as vite preview', () => {
    for (const [name, value] of Object.entries(SECURITY_HEADERS)) {
      expect(serverHeader(name), name).toBe(value);
    }
  });

  it('has a strict CSP without inline scripts', () => {
    expect(CONTENT_SECURITY_POLICY).toContain("default-src 'self'");
    expect(CONTENT_SECURITY_POLICY).toContain("script-src 'self'");
    expect(CONTENT_SECURITY_POLICY).toContain("frame-ancestors 'none'");
    expect(CONTENT_SECURITY_POLICY).not.toContain('unsafe-inline');
    expect(CONTENT_SECURITY_POLICY).not.toContain('unsafe-eval');
  });

  it('gives API answers a deny-all CSP and the docs page a narrow one', () => {
    const block = (location: string) => {
      const start = nginx.indexOf(`location ${location} {`);
      expect(start, location).toBeGreaterThan(-1);
      return nginx.slice(start, nginx.indexOf('\n    }', start));
    };
    const denyAll = `add_header Content-Security-Policy "default-src 'none'; frame-ancestors 'none'" always;`;
    expect(block('/api/')).toContain(denyAll);
    expect(nginx).not.toContain('location = /openapi.json');

    const docsBlock = block('= /api/docs');
    const docs = /add_header Content-Security-Policy "([^"]*)" always;/.exec(docsBlock);
    expect(docs?.[1]).toContain("default-src 'none'");
    expect(docs?.[1]).toContain("script-src 'self';");
    expect(docs?.[1]).toContain("connect-src 'self'");
    expect(docs?.[1]).toContain("frame-ancestors 'none'");
    expect(docs?.[1]).not.toContain('https:');
    expect(docs?.[1]).not.toContain('unsafe-eval');
    expect(docsBlock).toContain('proxy_hide_header Content-Security-Policy;');
  });

  it('proxies /api to the API with the prefix stripped and falls back to index.html', () => {
    expect(nginx).toMatch(/location \/api\/ \{\s+proxy_pass http:\/\/api:8000\/;/);
    expect(nginx).toMatch(/try_files \$uri \/index\.html;/);
    expect(nginx).toMatch(/listen 8080;/);
    expect(nginx).toMatch(/gzip on;/);
  });
});
