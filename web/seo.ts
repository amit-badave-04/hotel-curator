/**
 * The site's public address, and the files that need it: robots.txt,
 * sitemap.xml and the absolute URLs in index.html's Open Graph tags (link
 * previews need absolute URLs). Set VITE_PUBLIC_ORIGIN when building for
 * another address (the Cloudflare domain, ADR 011); the default is the Fly
 * address.
 */
import type { Plugin } from 'vite';

export const DEFAULT_PUBLIC_ORIGIN = 'https://hotel-curator.fly.dev';

/** Pages that stand on their own; hotel and compare pages are reached from them. */
export const SITEMAP_PATHS: readonly string[] = ['/', '/explore', '/about'];

/** Replaced in index.html by the public origin at build time. */
export const ORIGIN_PLACEHOLDER = '%PUBLIC_ORIGIN%';

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]']);

/**
 * A lower-case DNS name, as scripts/deploy.py accepts for --domain. The URL
 * parser keeps characters such as `"`, `'` and `&` in a host, and the origin
 * is written into HTML attributes and XML, so the host is checked itself.
 */
const DNS_NAME = /^(?=.{4,100}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/;

/**
 * The validated origin: https with a DNS name (http only for a local
 * address), lower-case, no port that is the default, no path, query or
 * trailing slash.
 */
export function publicOrigin(value: string | undefined): string {
  const raw = value ? value : DEFAULT_PUBLIC_ORIGIN;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error(`VITE_PUBLIC_ORIGIN is not a URL: ${JSON.stringify(raw)}`);
  }
  const local = url.protocol === 'http:' && LOCAL_HOSTS.has(url.hostname);
  const named = url.protocol === 'https:' && DNS_NAME.test(url.hostname);
  if (!(local || named) || url.origin !== raw) {
    throw new Error(
      `VITE_PUBLIC_ORIGIN must be an origin like ${DEFAULT_PUBLIC_ORIGIN} ` +
        `(https, lower case, no path or trailing slash), not ${JSON.stringify(raw)}`,
    );
  }
  return url.origin;
}

/** Crawl the pages, not the API (every filter combination is a URL there). */
export function robotsTxt(origin: string): string {
  return ['User-agent: *', 'Disallow: /api/', '', `Sitemap: ${origin}/sitemap.xml`, ''].join('\n');
}

export function sitemapXml(origin: string, paths: readonly string[] = SITEMAP_PATHS): string {
  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ...paths.map((path) => `  <url><loc>${origin}${path}</loc></url>`),
    '</urlset>',
    '',
  ].join('\n');
}

/** Fills in index.html's absolute URLs and writes robots.txt and sitemap.xml. */
export function seoFiles(origin: string): Plugin {
  return {
    name: 'curator-seo-files',
    transformIndexHtml: {
      order: 'pre',
      handler: (html) => html.replaceAll(ORIGIN_PLACEHOLDER, origin),
    },
    generateBundle() {
      this.emitFile({ type: 'asset', fileName: 'robots.txt', source: robotsTxt(origin) });
      this.emitFile({ type: 'asset', fileName: 'sitemap.xml', source: sitemapXml(origin) });
    },
  };
}
