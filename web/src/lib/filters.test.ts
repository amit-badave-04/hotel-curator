import { describe, expect, it } from 'vitest';

import {
  activeFilterCount,
  DEFAULT_FILTERS,
  filtersToSearchParams,
  parseFilters,
  toApiParams,
} from './filters';

describe('explorer filters', () => {
  it('round-trips through the URL in a canonical order', () => {
    const params = new URLSearchParams(
      'tier=good&tier=excellent&strong=staff&strong=cleanliness&avoid_weak=noise&city=Paris&q=grand&sort=reviews&page=3',
    );
    const filters = parseFilters(params);
    expect(filters).toEqual({
      city: 'Paris',
      tiers: ['excellent', 'good'],
      strong: ['cleanliness', 'staff'],
      avoidWeak: ['noise'],
      q: 'grand',
      sort: 'reviews',
      page: 3,
    });
    expect(parseFilters(filtersToSearchParams(filters))).toEqual(filters);
    expect(activeFilterCount(filters)).toBe(7);
  });

  it('drops invalid values instead of sending them to the API', () => {
    const filters = parseFilters(
      new URLSearchParams(
        'tier=best&strong=pool&city=<script>&sort=price&page=0&q=' + 'x'.repeat(200),
      ),
    );
    expect(filters.tiers).toEqual([]);
    expect(filters.strong).toEqual([]);
    expect(filters.city).toBeNull();
    expect(filters.sort).toBe('best');
    expect(filters.page).toBe(1);
    expect(filters.q).toHaveLength(80);
  });

  it('leaves defaults out of the URL and builds API params', () => {
    expect(filtersToSearchParams(DEFAULT_FILTERS).toString()).toBe('');
    expect(toApiParams({ ...DEFAULT_FILTERS, tiers: ['caution'] })).toEqual({
      sort: 'best',
      page: 1,
      page_size: 20,
      tier: ['caution'],
    });
  });
});
