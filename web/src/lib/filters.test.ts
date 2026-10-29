import { describe, expect, it } from 'vitest';

import {
  activeFilterCount,
  cleanQuery,
  DEFAULT_FILTERS,
  explorerHref,
  filtersToSearchParams,
  legacyExplorerTarget,
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

  it('turns control characters in a search term into spaces', () => {
    // The API refuses them (Postgres cannot store a NUL), so a pasted tab
    // would otherwise turn the results into an error.
    expect(cleanQuery(' Grand\tHotel\u0000Paris\n')).toBe('Grand Hotel Paris');
    expect(parseFilters(new URLSearchParams('q=a%09b%00')).q).toBe('a b');
    expect(cleanQuery('Café "Rosé" 100% d_é')).toBe('Café "Rosé" 100% d_é');
  });

  it('treats the two "not enough" tiers as one choice', () => {
    const both = parseFilters(
      new URLSearchParams('tier=not_enough_evidence&tier=not_enough_reviews'),
    );
    expect(both.tiers).toEqual(['not_enough_evidence', 'not_enough_reviews']);
    expect(activeFilterCount(both)).toBe(1);

    // A hand-edited or old URL with only one of them selects both, so the
    // checkbox, the count and the results agree.
    const one = parseFilters(new URLSearchParams('tier=not_enough_reviews&tier=good'));
    expect(one.tiers).toEqual(['good', 'not_enough_evidence', 'not_enough_reviews']);
    expect(activeFilterCount(one)).toBe(2);
    expect(toApiParams(one).tier).toEqual(['good', 'not_enough_evidence', 'not_enough_reviews']);
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

describe('explorer links', () => {
  it('puts the filters on the explorer path', () => {
    expect(explorerHref(DEFAULT_FILTERS)).toBe('/explore');
    expect(explorerHref({ ...DEFAULT_FILTERS, tiers: ['good'], page: 2 })).toBe(
      '/explore?tier=good&page=2',
    );
  });

  it('sends old explorer links on, with only valid filters', () => {
    expect(legacyExplorerTarget('?city=Paris&tier=good')).toBe('/explore?city=Paris&tier=good');
    expect(legacyExplorerTarget('?q=grand&utm_source=mail')).toBe('/explore?q=grand');
    expect(legacyExplorerTarget('?tier=bogus&page=-3')).toBe('/explore');
    expect(legacyExplorerTarget('?city=%3Cscript%3E')).toBe('/explore');
  });

  it('leaves the landing page alone without explorer filters', () => {
    expect(legacyExplorerTarget('')).toBeNull();
    expect(legacyExplorerTarget('?utm_source=mail')).toBeNull();
  });
});
