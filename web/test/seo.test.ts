// @vitest-environment node
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import {
  DEFAULT_PUBLIC_ORIGIN,
  ORIGIN_PLACEHOLDER,
  publicOrigin,
  robotsTxt,
  sitemapXml,
} from '../seo';

const read = (path: string) => readFileSync(fileURLToPath(new URL(path, import.meta.url)), 'utf8');

describe('public origin', () => {
  it('defaults to the Fly address and accepts a clean origin', () => {
    expect(publicOrigin(undefined)).toBe(DEFAULT_PUBLIC_ORIGIN);
    expect(publicOrigin('')).toBe(DEFAULT_PUBLIC_ORIGIN);
    expect(publicOrigin('https://curator.example')).toBe('https://curator.example');
    expect(publicOrigin('http://127.0.0.1:4173')).toBe('http://127.0.0.1:4173');
  });

  it.each([
    'curator.example',
    'http://curator.example',
    'https://curator.example/',
    'https://curator.example/app',
    'https://curator.example?x=1',
    'https://Curator.example',
    'https://curator.example:443',
    'https://user:pass@curator.example',
    'https://curator.example"><script>',
    'https://a"b.example',
    "https://a'b.example",
    'https://a&b.example',
    'https://127.0.0.1',
    'https://curator',
    'javascript:alert(1)',
  ])('refuses %s', (value) => {
    expect(() => publicOrigin(value)).toThrow(/VITE_PUBLIC_ORIGIN/);
  });
});

describe('robots.txt and sitemap.xml', () => {
  it('keeps crawlers off the API and points at the sitemap', () => {
    expect(robotsTxt('https://curator.example')).toBe(
      'User-agent: *\nDisallow: /api/\n\nSitemap: https://curator.example/sitemap.xml\n',
    );
  });

  it('lists the pages that stand on their own', () => {
    const xml = sitemapXml('https://curator.example');
    expect(xml).toContain('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">');
    expect(xml.match(/<loc>[^<]+<\/loc>/g)).toEqual([
      '<loc>https://curator.example/</loc>',
      '<loc>https://curator.example/explore</loc>',
      '<loc>https://curator.example/about</loc>',
    ]);
  });
});

describe('index.html', () => {
  const html = read('../index.html');
  const meta = (attribute: 'property' | 'name', key: string) =>
    new RegExp(`<meta\\s+${attribute}="${key}"\\s+content="([^"]*)"`).exec(html)?.[1];

  it('has absolute link-preview URLs built from the public origin', () => {
    // Served for every page, so a fixed og:url would point shares at "/".
    expect(meta('property', 'og:url')).toBeUndefined();
    expect(meta('property', 'og:image')).toBe(`${ORIGIN_PLACEHOLDER}/og-image.png`);
    expect(meta('name', 'twitter:image')).toBe(`${ORIGIN_PLACEHOLDER}/og-image.png`);
    expect(meta('name', 'twitter:card')).toBe('summary_large_image');
    expect(meta('property', 'og:image:width')).toBe('1200');
    expect(meta('property', 'og:image:height')).toBe('630');
  });

  it('only references files that exist in public/', () => {
    const local = [
      ...html.matchAll(/(?:href|content)="(?:%PUBLIC_ORIGIN%)?(\/[^"]+\.(?:png|ico|svg))"/g),
    ];
    expect(local.map((m) => m[1]).sort()).toEqual([
      '/apple-touch-icon.png',
      '/favicon.ico',
      '/favicon.svg',
      '/og-image.png',
      '/og-image.png',
    ]);
    for (const [, path] of local) expect(() => read(`../public${path}`)).not.toThrow();
  });
});
