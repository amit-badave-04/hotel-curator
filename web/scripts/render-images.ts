/**
 * Renders the committed images in public/ with Playwright's Chromium:
 * og-image.png (1200 x 630, link previews) from scripts/og-image.html, and
 * apple-touch-icon.png (180 x 180) and favicon.ico (32 x 32) from
 * public/favicon.svg. Run `npm run images` after changing a source; builds
 * use the committed files and need no browser, and test/public-files.test.ts
 * checks their sizes. Text uses the fonts of the machine that runs this
 * (Inter when installed), so another machine gives slightly different
 * pixels. Set PLAYWRIGHT_CHROMIUM_PATH to use a specific Chromium.
 */
import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

import { chromium, type Page } from 'playwright-core';

const local = (path: string) => fileURLToPath(new URL(path, import.meta.url));
const inPublic = (name: string) => local(`../public/${name}`);

const OG_SIZE = { width: 1200, height: 630 };
const TOUCH_ICON = 180;
const FAVICON = 32;
const ICO_HEADER_BYTES = 22;

/** The favicon drawn edge to edge: iOS rounds the corners itself. */
function squareIcon(favicon: string): string {
  return favicon.replace(/(<rect[^>]*?) rx="[^"]*"/, '$1');
}

function svgPage(svg: string, size: number): string {
  const sized = svg.replace(
    '<svg ',
    `<svg width="${size}" height="${size}" style="display:block" `,
  );
  return `<!doctype html><html><body style="margin:0">${sized}</body></html>`;
}

async function render(
  page: Page,
  html: string,
  width: number,
  height: number,
  transparent = false,
) {
  await page.setViewportSize({ width, height });
  await page.setContent(html, { waitUntil: 'load' });
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  return page.screenshot({ type: 'png', omitBackground: transparent });
}

/** An ICO file holding one PNG image, which every current browser reads. */
function icoFromPng(png: Buffer, size: number): Buffer {
  const header = Buffer.alloc(ICO_HEADER_BYTES);
  header.writeUInt16LE(1, 2); // type: icon (bytes 0-1 are reserved, zero)
  header.writeUInt16LE(1, 4); // one image
  header.writeUInt8(size, 6); // width
  header.writeUInt8(size, 7); // height
  header.writeUInt16LE(1, 10); // colour planes
  header.writeUInt16LE(32, 12); // bits per pixel
  header.writeUInt32LE(png.length, 14);
  header.writeUInt32LE(ICO_HEADER_BYTES, 18); // where the image starts
  return Buffer.concat([header, png]);
}

async function main(): Promise<void> {
  const favicon = await readFile(inPublic('favicon.svg'), 'utf8');
  const og = await readFile(local('og-image.html'), 'utf8');
  const browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH });
  try {
    const page = await browser.newPage({ deviceScaleFactor: 1 });
    const ogPng = await render(page, og, OG_SIZE.width, OG_SIZE.height);
    await writeFile(inPublic('og-image.png'), ogPng);
    const touch = svgPage(squareIcon(favicon), TOUCH_ICON);
    await writeFile(
      inPublic('apple-touch-icon.png'),
      await render(page, touch, TOUCH_ICON, TOUCH_ICON),
    );
    const small = await render(page, svgPage(favicon, FAVICON), FAVICON, FAVICON, true);
    await writeFile(inPublic('favicon.ico'), icoFromPng(small, FAVICON));
  } finally {
    await browser.close();
  }
  process.stdout.write('wrote og-image.png, apple-touch-icon.png and favicon.ico in public/\n');
}

await main();
