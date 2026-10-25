import { describe, expect, it } from 'vitest';

import { alpha, bravo, charlie, delta, echo, summary } from '@/test/fixtures';

import {
  complaintAxisMax,
  formatMonth,
  formatPercent,
  formatProbability,
  gradeInfo,
  isWhole,
  joinStrengths,
  strengthName,
  summaryWhy,
  TIER_INFO,
} from './domain';

describe('wording', () => {
  it('formats shares, probabilities and months', () => {
    expect(formatPercent(0.983)).toBe('98%');
    expect(formatPercent(0.012)).toBe('1.2%');
    expect(formatPercent(null)).toBe('n/a');
    expect(formatProbability(0.999)).toBe('more than 99%');
    expect(formatProbability(0.95)).toBe('95%');
    expect(formatMonth('2015-09')).toBe('Sep 2015');
    expect(formatMonth('Aug 2017')).toBe('Aug 2017');
  });

  it('never words the "not enough" tiers as bad', () => {
    for (const tier of ['not_enough_reviews', 'not_enough_evidence'] as const) {
      expect(TIER_INFO[tier].name).toBe('Not enough evidence to judge');
      expect(TIER_INFO[tier].tone).toBe('unknown');
      expect(TIER_INFO[tier].meaning).toContain('not a bad sign');
    }
  });

  it('explains grades in guest terms for both measures', () => {
    expect(gradeInfo('strong', 'two_sided').meaning).toBe(
      'At least 4 praises per complaint (95% sure).',
    );
    expect(gradeInfo('weak', 'must_be').meaning).toBe(
      'At least 1 guest in 10 complained (95% sure).',
    );
    expect(gradeInfo('ok', 'two_sided', 'trending_weak').name).toBe('OK, leaning weak');
  });

  it('builds the one-line why from summary fields', () => {
    expect(summaryWhy(summary(alpha))).toBe(
      'Guests confirmed cleanliness, room and staff as strengths.',
    );
    expect(summaryWhy(summary(bravo))).toBe('Complaints about cleanliness outnumbered praise.');
    expect(summaryWhy(summary(charlie))).toContain('complaints about noise hold it back');
    expect(summaryWhy(summary(delta))).toBe(
      'Only 12 usable reviews; at least 30 are needed to give a tier.',
    );
    expect(summaryWhy(summary(echo))).toContain('Too few guests gave an opinion');
  });
});

describe('helpers', () => {
  it('names noise and wifi strengths by what they mean', () => {
    expect(strengthName('noise')).toBe('Quiet (few noise complaints)');
    expect(strengthName('wifi')).toBe('Wifi (few complaints)');
    expect(strengthName('food')).toBe('Breakfast & food');
    expect(joinStrengths(['location', 'noise', 'value'])).toBe(
      'location, quiet (few noise complaints) and value',
    );
  });

  it('marks expected counts and sizes the complaint axis', () => {
    expect(isWhole(48, 120)).toBe(true);
    expect(isWhole(117.6, 2)).toBe(false);
    expect(complaintAxisMax([0.03, 0.07])).toBe(0.2);
    expect(complaintAxisMax([0.47, 0.06])).toBe(0.5);
    expect(complaintAxisMax([0.999])).toBe(1);
  });
});
