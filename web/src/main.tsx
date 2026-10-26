import './index.css';

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter } from 'react-router';
import { RouterProvider } from 'react-router/dom';

import { createQueryClient } from '@/api/queryClient';
import { Providers } from '@/App';
import { applyTheme, storedTheme, systemTheme } from '@/lib/theme';
import { routes } from '@/routes';

// Set the theme before the first paint (no inline script is allowed by the CSP).
applyTheme(storedTheme() ?? systemTheme());

const router = createBrowserRouter(routes);
const queryClient = createQueryClient();
const container = document.getElementById('root');
if (!container) throw new Error('Missing #root element');

createRoot(container).render(
  <StrictMode>
    <Providers client={queryClient}>
      <RouterProvider router={router} />
    </Providers>
  </StrictMode>,
);
