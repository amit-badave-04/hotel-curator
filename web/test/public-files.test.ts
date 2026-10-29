// @vitest-environment node
import { readdirSync, readFileSync } from 'node:fs';
import { join, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { BUILD_PACKAGES, shippedPackages, thirdPartyNotices } from '../scripts/third-party-notices';

const file = (path: string) => readFileSync(fileURLToPath(new URL(path, import.meta.url)));
const lockFile = () => JSON.parse(file('../package-lock.json').toString('utf8'));
const SRC = fileURLToPath(new URL('../src/', import.meta.url));
const IMPORT = /(?:\bfrom\s*|\bimport\s*\(?\s*|@import\s+|@plugin\s+)['"]([^'"]+)['"]/g;
const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

/** Width and height from a PNG's IHDR chunk, which always comes first. */
function pngSize(png: Buffer): [number, number] {
  expect(png.subarray(0, 8).equals(PNG_SIGNATURE)).toBe(true);
  expect(png.toString('latin1', 12, 16)).toBe('IHDR');
  return [png.readUInt32BE(16), png.readUInt32BE(20)];
}

describe('images in public/ (rendered by npm run images)', () => {
  it('has a 1200 x 630 link-preview image and a 180 x 180 touch icon', () => {
    expect(pngSize(file('../public/og-image.png'))).toEqual([1200, 630]);
    expect(pngSize(file('../public/apple-touch-icon.png'))).toEqual([180, 180]);
  });

  it('has a favicon.ico holding one 32 x 32 PNG', () => {
    const ico = file('../public/favicon.ico');
    expect(ico.readUInt16LE(0)).toBe(0);
    expect(ico.readUInt16LE(2)).toBe(1);
    expect(ico.readUInt16LE(4)).toBe(1);
    expect([ico[6], ico[7]]).toEqual([32, 32]);
    const size = ico.readUInt32LE(14);
    const offset = ico.readUInt32LE(18);
    expect(offset + size).toBe(ico.length);
    expect(pngSize(ico.subarray(offset))).toEqual([32, 32]);
  });
});

describe('NOTICE.txt', () => {
  it('is the repository NOTICE, so the site shows the current attribution', () => {
    // After changing NOTICE: cp NOTICE web/public/NOTICE.txt
    expect(file('../public/NOTICE.txt').equals(file('../../NOTICE'))).toBe(true);
  });

  it('is plain ASCII, so it reads the same without a charset header', () => {
    expect([...file('../public/NOTICE.txt')].every((byte) => byte < 0x80)).toBe(true);
  });
});

/** Each package the app's own code and styles import (not its tests), with
 * the first file that imports it. */
function importedPackages(): Map<string, string> {
  const found = new Map<string, string>();
  for (const path of readdirSync(SRC, { recursive: true, encoding: 'utf8' })) {
    const isTest = /\.test\.tsx?$/.test(path) || path.startsWith(`test${sep}`);
    if (isTest || !/\.(tsx?|css)$/.test(path)) continue;
    for (const [, specifier] of readFileSync(join(SRC, path), 'utf8').matchAll(IMPORT)) {
      if (/^(\.|@\/|node:)/.test(specifier)) continue;
      const parts = specifier.split('/');
      const name = specifier.startsWith('@') ? parts.slice(0, 2).join('/') : parts[0];
      if (!found.has(name)) found.set(name, path);
    }
  }
  return found;
}

describe('THIRD-PARTY-NOTICES.txt', () => {
  const notices = file('../public/THIRD-PARTY-NOTICES.txt');

  it('is what npm run notices writes from the installed packages', () => {
    // After changing dependencies: npm run notices
    expect(notices.toString('utf8')).toBe(thirdPartyNotices());
  });

  it('names every production package and the build tools that add code', () => {
    const shipped = shippedPackages(lockFile());
    const names = shipped.map((pkg) => pkg.name);
    expect(names).toContain('react');
    expect(names).toEqual(expect.arrayContaining([...BUILD_PACKAGES]));
    // Rolldown's module helpers come from esbuild; its notice is in
    // Rolldown's THIRD-PARTY-LICENSE, not its LICENSE.
    expect(notices.toString('utf8')).toContain('Copyright (c) 2020 Evan Wallace');
    for (const pkg of shipped) {
      expect(notices.toString('utf8')).toContain(`\n${pkg.name} ${pkg.version} (`);
    }
  });

  it("covers every package the app's own code and styles import", () => {
    // A development-only package imported by the app ships in its bundle:
    // move it to dependencies, or add it to BUILD_PACKAGES.
    const shipped = new Set(shippedPackages(lockFile()).map((pkg) => pkg.name));
    const imported = importedPackages();
    expect([...imported.keys()]).toEqual(expect.arrayContaining(['react', 'tailwindcss']));
    for (const [name, path] of imported) {
      expect(shipped.has(name), `src/${path} imports ${name}`).toBe(true);
    }
  });

  it('leaves out development-only packages other than the build tools', () => {
    const lock = {
      packages: {
        '': {},
        'node_modules/react': { version: '1.0.0', license: 'MIT' },
        'node_modules/vitest': { version: '2.0.0', license: 'MIT', dev: true },
        'node_modules/vite': { version: '4.0.0', license: 'MIT', dev: true },
        'node_modules/fsevents': { version: '3.0.0', devOptional: true },
        'node_modules/a/node_modules/react': { version: '1.0.0', license: 'MIT' },
        'node_modules/a/node_modules/vite': { version: '5.0.0', dev: true },
      },
    };
    expect(shippedPackages(lock).map((pkg) => `${pkg.name}@${pkg.version}`)).toEqual([
      'react@1.0.0',
      'vite@4.0.0',
    ]);
  });

  it('is UTF-8, as nginx labels it (licence texts quote names as written)', () => {
    expect(() => new TextDecoder('utf-8', { fatal: true }).decode(notices)).not.toThrow();
  });
});
