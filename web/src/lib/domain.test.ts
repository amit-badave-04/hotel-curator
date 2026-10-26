import { describe, expect, it } from 'vitest';

import type { HotelSummary } from '@/api/generated/model';
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

  it('says a catch holds a hotel back only when the tier rules make it binding', () => {
    const ok = { cleanliness: 'ok', room: 'ok', staff: 'ok' } as const;
    // Good allows one catch, so a single catch never keeps a hotel out of Good.
    const fairOneCatch: HotelSummary = {
      ...summary(charlie),
      tier: 'fair',
      essentials: ok,
      catches: ['value'],
    };
    expect(summaryWhy(fairOneCatch)).toBe('No essential is a confirmed strength yet.');
    // Two catches keep a hotel with three Strong essentials Fair.
    const fairTwoCatches: HotelSummary = {
      ...summary(charlie),
      tier: 'fair',
      catches: ['noise', 'value'],
    };
    expect(summaryWhy(fairTwoCatches)).toBe(
      'Guests confirmed cleanliness, room and staff as strengths; complaints about noise and value hold it back.',
    );
    // Caution is decided by the essentials; a catch there is not the reason.
    const cautionWithCatch: HotelSummary = { ...summary(bravo), catches: ['noise'] };
    expect(summaryWhy(cautionWithCatch)).toBe('Complaints about cleanliness outnumbered praise.');
  });

  it('names a Weak essential on a Fair hotel (Weak, but not by the test for Caution)', () => {
    const essentials = { cleanliness: 'ok', room: 'weak', staff: 'strong' } as const;
    const fair: HotelSummary = { ...summary(alpha), tier: 'fair', essentials, highlights: [] };
    expect(summaryWhy(fair)).toBe(
      'Guests confirmed staff as a strength; complaints about room outnumbered praise.',
    );
    expect(TIER_INFO.fair.meaning).not.toContain('No essential is weak');
    expect(TIER_INFO.fair.meaning).toContain(
      'an essential is graded Weak but does not pass the stricter test for Caution',
    );
    expect(TIER_INFO.good.meaning).toContain('none is graded Weak');
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
