/**
 * MSW handlers that answer like the real API (same paths, error shape and
 * filtering rules), from the fixtures. Tests override single endpoints with
 * `server.use(...)`.
 */
import { http, HttpResponse } from 'msw';

import type { ErrorResponse, HotelPage } from '@/api/generated/model';

import { CITIES, comparison, HOTELS, meta, summary } from './fixtures';

const TIER_RANK = [
  'excellent',
  'good',
  'fair',
  'caution',
  'not_enough_evidence',
  'not_enough_reviews',
];

export function apiError(
  status: number,
  code: string,
  message: string,
  extra: Partial<ErrorResponse['error']> = {},
  headers: Record<string, string> = {},
) {
  const body: ErrorResponse = {
    error: { code, message, request_id: `req-${status}-test`, ...extra },
  };
  return HttpResponse.json(body, { status, headers });
}

export const handlers = [
  http.get('*/api/v1/hotels', ({ request }) => {
    const url = new URL(request.url);
    const city = url.searchParams.get('city');
    const tiers = url.searchParams.getAll('tier');
    const strong = url.searchParams.getAll('strong');
    const avoidWeak = url.searchParams.getAll('avoid_weak');
    const q = url.searchParams.get('q')?.toLowerCase();
    const sort = url.searchParams.get('sort') ?? 'best';
    const page = Number(url.searchParams.get('page') ?? '1');
    const size = Number(url.searchParams.get('page_size') ?? '20');

    let rows = HOTELS.filter(
      (h) =>
        (!city || h.city === city) &&
        (tiers.length === 0 || tiers.includes(h.tier)) &&
        (!q || h.name.toLowerCase().includes(q)) &&
        strong.every((a) => h.attributes.some((x) => x.attribute === a && x.grade === 'strong')) &&
        !avoidWeak.some((a) => h.attributes.some((x) => x.attribute === a && x.grade === 'weak')),
    );
    rows = [...rows].sort((a, b) =>
      sort === 'name'
        ? a.name.localeCompare(b.name)
        : sort === 'reviews'
          ? b.reviews_eligible - a.reviews_eligible
          : TIER_RANK.indexOf(a.tier) - TIER_RANK.indexOf(b.tier),
    );
    const body: HotelPage = {
      items: rows.slice((page - 1) * size, page * size).map(summary),
      total: rows.length,
      page,
      page_size: size,
    };
    return HttpResponse.json(body);
  }),

  http.get('*/api/v1/hotels/:hotelId', ({ params }) => {
    const hotel = HOTELS.find((h) => h.hotel_id === params.hotelId);
    return hotel ? HttpResponse.json(hotel) : apiError(404, 'not_found', 'Hotel not found.');
  }),

  http.get('*/api/v1/compare', ({ request }) => {
    const ids = new URL(request.url).searchParams.getAll('ids');
    if (new Set(ids).size < 2) return apiError(422, 'invalid_request', 'Give two distinct hotels.');
    if (ids.some((id) => !HOTELS.some((h) => h.hotel_id === id))) {
      return apiError(404, 'not_found', 'Hotel not found.');
    }
    return HttpResponse.json(comparison([...new Set(ids)]));
  }),

  http.get('*/api/v1/cities', () => HttpResponse.json(CITIES)),

  http.get('*/api/v1/meta', () => HttpResponse.json(meta())),

  // The model went away after /v1/meta said it was loaded (or a test
  // overrides this with an answer).
  http.post('*/api/v1/analyze', () => apiError(503, 'unavailable', 'The model is not loaded.')),
];
