import { render } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter } from 'react-router';
import { RouterProvider } from 'react-router/dom';

import { createQueryClient } from '@/api/queryClient';
import { Providers } from '@/App';
import { routes } from '@/routes';

/** Render the whole app at `path`, with a fresh cache and no retries. */
export function renderApp(path: string) {
  const client = createQueryClient();
  client.setDefaultOptions({
    queries: { ...client.getDefaultOptions().queries, retry: false },
  });
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const user = userEvent.setup();
  const view = render(
    <Providers client={client}>
      <RouterProvider router={router} />
    </Providers>,
  );
  /** The current URL's search params. */
  const search = () => new URLSearchParams(router.state.location.search);
  return { ...view, router, user, client, search };
}
