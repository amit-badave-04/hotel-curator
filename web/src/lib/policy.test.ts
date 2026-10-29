import { describe, expect, it } from 'vitest';

import { formatPeriod, policyOf } from '@/lib/policy';
import { meta } from '@/test/fixtures';

import tierPolicy from './tier-policy.json';

describe('policyOf', () => {
  it('uses the served policy, else the pre-registered one', () => {
    const served = meta();
    served.policy = { ...served.policy, confirmation: 0.9 };
    expect(policyOf(served).confirmation).toBe(0.9);
    expect(policyOf(undefined)).toEqual(tierPolicy);
  });
});

describe('formatPeriod', () => {
  it('names the first and last month', () => {
    expect(formatPeriod({ first_month: '2015-08', last_month: '2017-08' })).toBe(
      'August 2015 to August 2017',
    );
  });

  it('gives null when the period is missing or malformed', () => {
    expect(formatPeriod(null)).toBeNull();
    expect(formatPeriod(undefined)).toBeNull();
    expect(formatPeriod({ first_month: '2015-13', last_month: '2017-08' })).toBeNull();
    expect(formatPeriod({ first_month: '2015-08', last_month: 'soon' })).toBeNull();
  });
});
