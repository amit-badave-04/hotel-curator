import { QueryClient } from '@tanstack/react-query';

import { ApiError } from './fetcher';

/**
 * The data are a fixed historical corpus, so results stay fresh for a long
 * time. Client errors (4xx, including 429) are not retried automatically.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 10 * 60_000,
        gcTime: 30 * 60_000,
        refetchOnWindowFocus: false,
        retry: (failureCount, error) => {
          if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
          return failureCount < 1;
        },
      },
      mutations: {
        retry: false,
      },
    },
  });
}
