/**
 * Explorer filters live in the URL query string, so a search can be
 * bookmarked, shared and navigated with Back/Forward. Values from the URL are
 * validated against the API's enums and patterns before they reach the API.
 */
import type { Attribute, ListHotelsParams, SortOrder, Tier } from '@/api/generated/model';

import { ATTRIBUTES, CITY_PATTERN, isNotEnough, MAX_QUERY_LENGTH, TIERS } from './domain';

export const PAGE_SIZE = 20;
const MAX_PAGE = 10_000;
const SORTS: readonly SortOrder[] = ['best', 'name', 'reviews'];

export interface ExplorerFilters {
  city: string | null;
  tiers: Tier[];
  strong: Attribute[];
  avoidWeak: Attribute[];
  q: string;
  sort: SortOrder;
  page: number;
}

export const DEFAULT_FILTERS: ExplorerFilters = {
  city: null,
  tiers: [],
  strong: [],
  avoidWeak: [],
  q: '',
  sort: 'best',
  page: 1,
};

function pick<T extends string>(values: string[], allowed: readonly T[]): T[] {
  const set = new Set<T>();
  for (const value of values) {
    if ((allowed as readonly string[]).includes(value)) set.add(value as T);
  }
  // Keep the canonical order so equal filters give equal URLs and cache keys.
  return allowed.filter((a) => set.has(a));
}

/**
 * The two "not enough" tiers are one filter choice, so a URL naming only one
 * of them (hand-edited or old) selects both: the checkbox, the count and the
 * results then agree.
 */
function groupNotEnough(tiers: Tier[]): Tier[] {
  if (!tiers.some(isNotEnough)) return tiers;
  return TIERS.filter((tier) => tiers.includes(tier) || isNotEnough(tier));
}

/** A search term the API accepts: control characters (a pasted tab, say),
 * which it refuses, become spaces; then trimmed and cut to its limit. */
export function cleanQuery(value: string): string {
  // eslint-disable-next-line no-control-regex -- matching them is the point
  const controls = /[\u0000-\u001f\u007f]+/g;
  return value.replace(controls, ' ').trim().slice(0, MAX_QUERY_LENGTH);
}

export function parseFilters(params: URLSearchParams): ExplorerFilters {
  const city = params.get('city');
  const sort = params.get('sort');
  const page = Number.parseInt(params.get('page') ?? '1', 10);
  return {
    city: city && CITY_PATTERN.test(city) ? city : null,
    tiers: groupNotEnough(pick(params.getAll('tier'), TIERS)),
    strong: pick(params.getAll('strong'), ATTRIBUTES),
    avoidWeak: pick(params.getAll('avoid_weak'), ATTRIBUTES),
    q: cleanQuery(params.get('q') ?? ''),
    sort: sort && (SORTS as readonly string[]).includes(sort) ? (sort as SortOrder) : 'best',
    page: Number.isFinite(page) && page >= 1 && page <= MAX_PAGE ? page : 1,
  };
}

/** The URL form of the filters; defaults are left out to keep URLs short. */
export function filtersToSearchParams(filters: ExplorerFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.q) params.set('q', filters.q);
  if (filters.city) params.set('city', filters.city);
  for (const tier of filters.tiers) params.append('tier', tier);
  for (const attribute of filters.strong) params.append('strong', attribute);
  for (const attribute of filters.avoidWeak) params.append('avoid_weak', attribute);
  if (filters.sort !== 'best') params.set('sort', filters.sort);
  if (filters.page > 1) params.set('page', String(filters.page));
  return params;
}

export function toApiParams(filters: ExplorerFilters): ListHotelsParams {
  const params: ListHotelsParams = {
    sort: filters.sort,
    page: filters.page,
    page_size: PAGE_SIZE,
  };
  if (filters.city) params.city = filters.city;
  if (filters.q) params.q = filters.q;
  if (filters.tiers.length > 0) params.tier = filters.tiers;
  if (filters.strong.length > 0) params.strong = filters.strong;
  if (filters.avoidWeak.length > 0) params.avoid_weak = filters.avoidWeak;
  return params;
}

/** Tier choices selected; both "not enough" tiers together count once. */
function tierChoiceCount(tiers: readonly Tier[]): number {
  const ranked = tiers.filter((tier) => !isNotEnough(tier)).length;
  return ranked + (tiers.some(isNotEnough) ? 1 : 0);
}

/** Number of filters that narrow the results (sort and page excluded). */
export function activeFilterCount(filters: ExplorerFilters): number {
  return (
    (filters.city ? 1 : 0) +
    (filters.q ? 1 : 0) +
    tierChoiceCount(filters.tiers) +
    filters.strong.length +
    filters.avoidWeak.length
  );
}

/** The explorer's address; `/` is the landing page. */
export const EXPLORER_PATH = '/explore';

const EXPLORER_KEYS = ['q', 'city', 'tier', 'strong', 'avoid_weak', 'sort', 'page'] as const;

/** An explorer link: the filters as a query string on the explorer's path. */
export function explorerHref(filters: ExplorerFilters): string {
  const query = filtersToSearchParams(filters).toString();
  return query ? `${EXPLORER_PATH}?${query}` : EXPLORER_PATH;
}

/**
 * Where an old explorer link (from when the explorer was at `/`) should go:
 * `/?city=Paris` becomes `/explore?city=Paris`. The filters are parsed and
 * written back, so only valid values travel. `null` when the query names
 * no explorer filter, so `/` and links like `/?utm_source=x` stay on the
 * landing page.
 */
export function legacyExplorerTarget(search: string): string | null {
  const params = new URLSearchParams(search);
  if (!EXPLORER_KEYS.some((key) => params.has(key))) return null;
  return explorerHref(parseFilters(params));
}
