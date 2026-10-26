import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import { server } from '@/test/server';

import { ApiError, apiFetch, parseRetryAfter } from './fetcher';

describe('apiFetch', () => {
  it('returns parsed JSON for 2xx responses', async () => {
    server.use(http.get('*/api/ping', () => HttpResponse.json({ ok: true })));
    await expect(apiFetch('/api/ping')).resolves.toEqual({ ok: true });
  });

  it('turns the API error shape into an ApiError with message, code and request id', async () => {
    server.use(
      http.get('*/api/broken', () =>
        HttpResponse.json(
          {
            error: {
              code: 'invalid_request',
              message: 'The request is invalid.',
              request_id: 'abc123',
              fields: [{ location: ['query', 'tier', 0], message: 'Input should be ...' }],
            },
          },
          { status: 422 },
        ),
      ),
    );
    const error = await apiFetch('/api/broken').catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.status).toBe(422);
    expect(apiError.code).toBe('invalid_request');
    expect(apiError.message).toBe('The request is invalid.');
    expect(apiError.requestId).toBe('abc123');
    expect(apiError.fields).toHaveLength(1);
  });

  it('handles non-JSON errors from a proxy, using the request id header', async () => {
    server.use(
      http.get(
        '*/api/down',
        () =>
          new HttpResponse('<html>Bad gateway</html>', {
            status: 502,
            headers: { 'Content-Type': 'text/html', 'X-Request-Id': 'proxy-1' },
          }),
      ),
    );
    const error = (await apiFetch('/api/down').catch((e: unknown) => e)) as ApiError;
    expect(error.status).toBe(502);
    expect(error.message).toBe('The API is not reachable right now.');
    expect(error.requestId).toBe('proxy-1');
  });

  it('reads Retry-After on 429', async () => {
    server.use(
      http.get('*/api/busy', () =>
        HttpResponse.json(
          { error: { code: 'rate_limited', message: 'Too many requests; try again later.' } },
          { status: 429, headers: { 'Retry-After': '7' } },
        ),
      ),
    );
    const error = (await apiFetch('/api/busy').catch((e: unknown) => e)) as ApiError;
    expect(error.retryAfter).toBe(7);
  });

  it('reports a network failure as status 0', async () => {
    server.use(http.get('*/api/offline', () => HttpResponse.error()));
    const error = (await apiFetch('/api/offline').catch((e: unknown) => e)) as ApiError;
    expect(error.status).toBe(0);
    expect(error.code).toBe('network_error');
  });
});

describe('parseRetryAfter', () => {
  it('accepts seconds and HTTP dates', () => {
    expect(parseRetryAfter('30')).toBe(30);
    expect(parseRetryAfter(null)).toBeNull();
    expect(parseRetryAfter('soon')).toBeNull();
    const now = Date.parse('2026-10-24T10:00:00Z');
    expect(parseRetryAfter('Sat, 24 Oct 2026 10:00:20 GMT', now)).toBe(20);
  });
});
