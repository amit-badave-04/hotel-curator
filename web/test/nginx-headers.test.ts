// @vitest-environment node
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { CONTENT_SECURITY_POLICY, SECURITY_HEADERS } from '../security-headers';

const read = (name: string) =>
  readFileSync(fileURLToPath(new URL(`../nginx/${name}`, import.meta.url)), 'utf8');
// Server-level settings and locations shared by every edge mode.
const nginx = read('site.conf');
const modes = ['local', 'fly', 'cloudflare'].map((mode) => read(`site-${mode}.conf.template`));

// Location blocks start at column 0 in site.conf and end at a lone "}".
const locations = nginx.match(/^location [^{]+\{[\s\S]*?^\}/gm) ?? [];
const serverLevel = locations.reduce((text, b) => text.replace(b, ''), nginx);

function serverHeader(name: string): string | undefined {
  const match = new RegExp(`^add_header ${name} "([^"]*)" always;$`, 'm').exec(nginx);
  return match?.[1];
}

describe('nginx site', () => {
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
      return nginx.slice(start, nginx.indexOf('\n}', start));
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
    expect(nginx).toMatch(/location \/api\/ \{\s+proxy_pass http:\/\/curator_api\/;/);
    expect(nginx).toMatch(/try_files \$uri \/index\.html;/);
    expect(nginx).toMatch(/gzip on;/);
    for (const mode of modes) {
      expect(mode).toMatch(/listen 8080;/);
      expect(mode).toContain('server ${CURATOR_API_UPSTREAM};');
      expect(mode).toContain('include curator/site.conf;');
    }
  });

  it('sends one HSTS value from every header block', () => {
    const hsts = /add_header Strict-Transport-Security \$curator_hsts always;/g;
    const apiHeaders = read('api-headers.conf');
    expect(apiHeaders.match(hsts)).toHaveLength(1);
    const withHeaders = [serverLevel, ...locations].filter((b) => b.includes('add_header'));
    expect(withHeaders).toHaveLength(3);
    for (const b of withHeaders) {
      const own = b.match(hsts)?.length ?? 0;
      const included = b.includes('include curator/api-headers.conf;') ? 1 : 0;
      expect(own + included).toBe(1);
    }
    expect(read('http.conf')).toMatch(/map "" \$curator_hsts \{\s+default "max-age=86400";/);
  });

  it('replaces, never duplicates, the headers the API sends itself', () => {
    const apiHeaders = read('api-headers.conf');
    for (const name of [
      'X-Content-Type-Options',
      'X-Frame-Options',
      'Referrer-Policy',
      'Cache-Control',
      'X-Request-ID',
      'Strict-Transport-Security',
    ]) {
      expect(apiHeaders).toContain(`proxy_hide_header ${name};`);
      expect(apiHeaders).toMatch(new RegExp(`^add_header ${name} .* always;$`, 'm'));
    }
  });

  it('sends the API exactly one client-address header', () => {
    const proxied = locations.filter((b) => b.includes('proxy_pass'));
    expect(proxied).toHaveLength(2);
    for (const b of proxied) {
      expect(b).toContain('proxy_set_header X-Real-IP $curator_client_ip;');
      for (const dropped of ['X-Forwarded-For', 'Forwarded', 'Fly-Client-IP', 'CF-Connecting-IP']) {
        expect(b).toContain(`proxy_set_header ${dropped} "";`);
      }
    }
  });
});
