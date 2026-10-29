// @vitest-environment node
/**
 * Some pages word policy values inline ("90% likely range", "Fewer than 30
 * usable reviews") instead of reading /v1/meta. Each phrase is rebuilt here
 * from the pre-registered policy (src/lib/tier-policy.json, exported from
 * config/tier_policy.yaml), so changing the policy fails this test until
 * the wording follows. The About page and the landing page read the
 * served policy instead (src/lib/policy.ts), so they are not listed.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import policy from '../src/lib/tier-policy.json';

const source = (path: string) =>
  readFileSync(fileURLToPath(new URL(`../src/${path}`, import.meta.url)), 'utf8');
const pct = (value: number) => `${Math.round(value * 100)}%`;
const oneIn = (rate: number) => Math.round(1 / rate);
const WORDS: Record<number, string> = { 6: 'six', 12: 'twelve' };

const strongShare = policy.two_sided.strong;
const range = `${pct(policy.interval_level)} likely range`;

const PHRASES: Record<string, string[]> = {
  'lib/domain.ts': [
    `over at least ${WORDS[policy.excellent.min_months]} months of reviews`,
    `and we are at least ${pct(policy.confirmation)} sure`,
    `Fewer than ${policy.min_reviews} usable reviews`,
    `At most 1 guest in ${oneIn(policy.must_be_lines.strong)} complained (${pct(policy.confirmation)} sure)`,
    `At least ${Math.round(strongShare / (1 - strongShare))} praises per complaint (${pct(policy.confirmation)} sure)`,
    `At least 1 guest in ${oneIn(policy.must_be_lines.weak)} complained (${pct(policy.confirmation)} sure)`,
    `Fewer than ${policy.min_reviews} reviews, so this is not graded`,
    `Fewer than ${policy.min_opinions} guests gave an opinion`,
    `The last ${policy.trends.recent_months} months are credibly better than the ${policy.trends.baseline_months} months before`,
    `at least ${policy.min_reviews} are needed to give a tier`,
  ],
  'pages/HotelPage.tsx': [
    `Last ${policy.trends.recent_months} months`,
    `rangeName="${range}"`,
    `{ value: ${policy.two_sided.weak}, label: '${pct(policy.two_sided.weak)} (Weak below)'`,
    `{ value: ${policy.two_sided.strong}, label: '${pct(policy.two_sided.strong)} (Strong above)'`,
    `{ value: ${policy.must_be_lines.strong}, label: '${pct(policy.must_be_lines.strong)} (Strong below)'`,
    `{ value: ${policy.must_be_lines.weak}, label: '${pct(policy.must_be_lines.weak)} (Weak above)'`,
    `Strong needs the range below ${pct(policy.must_be_lines.strong)}; Weak above ${pct(policy.must_be_lines.weak)}`,
    `at least ${pct(policy.compare.probability)} sure the difference is real`,
    `a ${range}`,
    `when we are at least ${pct(policy.confirmation)} sure`,
  ],
  'pages/ComparePage.tsx': [
    `its ${range}`,
    `(at least ${pct(policy.compare.probability)} sure)`,
    `with at least ${pct(policy.compare.probability)} certainty`,
    `rangeName="${range}"`,
  ],
};

describe('policy values worded in the pages', () => {
  for (const [path, phrases] of Object.entries(PHRASES)) {
    it(`${path} matches config/tier_policy.yaml`, () => {
      // Prettier may wrap a sentence, so compare with whitespace collapsed.
      const text = source(path).replace(/\s+/g, ' ');
      for (const phrase of phrases) expect(text, phrase).toContain(phrase);
    });
  }
});
