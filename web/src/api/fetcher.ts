/**
 * The one place the app talks HTTP. The orval-generated client calls
 * `apiFetch` for every endpoint (see orval.config.ts), so error handling,
 * the `/api` prefix and JSON parsing live here and nowhere else.
 */
import type { ErrorResponse, FieldError } from './generated/model';

export interface ApiErrorInit {
  status: number;
  code: string;
  message: string;
  requestId?: string | null;
  fields?: FieldError[];
  retryAfter?: number | null;
  body?: unknown;
}

/** A failed API call, normalised from the API's `{error: {...}}` shape. */
export class ApiError<TBody = unknown> extends Error {
  /** HTTP status; 0 when the API could not be reached at all. */
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly fields: FieldError[];
  /** Seconds to wait before retrying (from `Retry-After`), if given. */
  readonly retryAfter: number | null;
  readonly body: TBody | null;

  constructor(init: ApiErrorInit) {
    super(init.message);
    this.name = 'ApiError';
    this.status = init.status;
    this.code = init.code;
    this.requestId = init.requestId ?? null;
    this.fields = init.fields ?? [];
    this.retryAfter = init.retryAfter ?? null;
    this.body = (init.body ?? null) as TBody | null;
  }
}

/** Error type used by the generated hooks (orval reads this export). */
export type ErrorType<TBody> = ApiError<TBody>;
/** Request body type used by the generated hooks (orval reads this export). */
export type BodyType<TBody> = TBody;

const FALLBACK_MESSAGES: Record<number, string> = {
  400: 'The request was not understood.',
  404: 'Not found.',
  413: 'The request is too large.',
  422: 'The request is invalid.',
  429: 'Too many requests; try again later.',
  500: 'The API had an internal error.',
  502: 'The API is not reachable right now.',
  503: 'The API is unavailable right now.',
  504: 'The API took too long to answer.',
};

function isErrorResponse(value: unknown): value is ErrorResponse {
  if (typeof value !== 'object' || value === null || !('error' in value)) return false;
  const error = (value as { error: unknown }).error;
  return (
    typeof error === 'object' &&
    error !== null &&
    typeof (error as { message?: unknown }).message === 'string' &&
    typeof (error as { code?: unknown }).code === 'string'
  );
}

/** Seconds from a `Retry-After` header (delta-seconds or an HTTP date). */
export function parseRetryAfter(value: string | null, now: number = Date.now()): number | null {
  if (!value) return null;
  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds >= 0) return Math.ceil(seconds);
  const date = Date.parse(value);
  if (!Number.isNaN(date)) return Math.max(0, Math.ceil((date - now) / 1000));
  return null;
}

function absoluteUrl(url: string): string {
  // Browsers resolve relative URLs themselves; Node's fetch (used in unit
  // tests) does not, so resolve against the page origin explicitly.
  if (typeof window === 'undefined') return url;
  return new URL(url, window.location.origin).toString();
}

function isAbort(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError';
}

export async function apiFetch<T>(url: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set('Accept', 'application/json');

  let response: Response;
  try {
    response = await fetch(absoluteUrl(url), {
      ...init,
      headers,
      credentials: 'same-origin',
    });
  } catch (cause) {
    if (isAbort(cause)) throw cause;
    throw new ApiError({
      status: 0,
      code: 'network_error',
      message: 'Could not reach the hotel-curator API. Check your connection and try again.',
    });
  }

  const text = await response.text();
  let body: unknown = null;
  let parsed = false;
  if (text) {
    try {
      body = JSON.parse(text);
      parsed = true;
    } catch {
      body = null;
    }
  }

  if (!response.ok) {
    const error = isErrorResponse(body) ? body.error : null;
    throw new ApiError({
      status: response.status,
      code: error?.code ?? `http_${response.status}`,
      message:
        error?.message ??
        FALLBACK_MESSAGES[response.status] ??
        `The API answered with status ${response.status}.`,
      requestId: error?.request_id ?? response.headers.get('x-request-id'),
      fields: error?.fields ?? [],
      retryAfter: parseRetryAfter(response.headers.get('retry-after')),
      body,
    });
  }

  if (text && !parsed) {
    throw new ApiError({
      status: response.status,
      code: 'invalid_response',
      message: 'The API returned a response that is not JSON.',
      requestId: response.headers.get('x-request-id'),
    });
  }
  return body as T;
}
