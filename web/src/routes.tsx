import type { RouteObject } from 'react-router';

import { AppShell } from '@/components/layout/AppShell';
import { RouteError } from '@/components/layout/RouteError';
import { LoadingBlock } from '@/components/states';
import { LandingPage } from '@/pages/LandingPage';
import { NotFoundPage } from '@/pages/NotFoundPage';

/**
 * Routes. The landing page is in the main bundle; every other page loads on
 * demand, so the first page a visitor sees stays small.
 */
export const routes: RouteObject[] = [
  {
    path: '/',
    element: <AppShell />,
    errorElement: <RouteError />,
    hydrateFallbackElement: (
      <div className="mx-auto max-w-6xl px-4 py-8">
        <LoadingBlock label="Loading" />
      </div>
    ),
    children: [
      { index: true, element: <LandingPage /> },
      {
        path: 'explore',
        lazy: async () => ({ Component: (await import('@/pages/ExplorerPage')).ExplorerPage }),
      },
      {
        path: 'hotels/:hotelId',
        lazy: async () => ({ Component: (await import('@/pages/HotelPage')).HotelPage }),
      },
      {
        path: 'compare',
        lazy: async () => ({ Component: (await import('@/pages/ComparePage')).ComparePage }),
      },
      {
        path: 'about',
        lazy: async () => ({ Component: (await import('@/pages/AboutPage')).AboutPage }),
      },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
];
