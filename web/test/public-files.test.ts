// @vitest-environment node
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const file = (path: string) => readFileSync(fileURLToPath(new URL(path, import.meta.url)));
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
