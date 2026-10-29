import { useEffect } from 'react';
import { isRouteErrorResponse, Link, useRouteError } from 'react-router';

import { Button } from '@/components/ui/button';
import { EXPLORER_PATH } from '@/lib/filters';
import { isStaleChunkError, page, reloadOnce } from '@/lib/staleChunk';

/** Last-resort error screen when a page fails to render or load its code. */
export function RouteError() {
  const error = useRouteError();
  const stale = isStaleChunkError(error);
  useEffect(() => {
    if (stale) reloadOnce();
  }, [stale]);

  if (stale) {
    return (
      <main id="main" className="mx-auto max-w-2xl space-y-4 px-4 py-10">
        <title>Reload needed | hotel-curator</title>
        <h1 className="text-2xl font-bold">This page could not be loaded</h1>
        <p>
          The site may have been updated since this tab was opened, or the connection dropped.
          Reloading the page usually fixes it.
        </p>
        <Button onClick={() => page.reload()}>Reload the page</Button>
      </main>
    );
  }
  const message = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : error instanceof Error
      ? error.message
      : 'Unknown error';
  return (
    <main id="main" className="mx-auto max-w-2xl space-y-4 px-4 py-10">
      <title>Error | hotel-curator</title>
      <h1 className="text-2xl font-bold">Something went wrong</h1>
      <p>The page could not be shown. Reloading usually helps.</p>
      <p className="text-sm text-muted-foreground">Details: {message}</p>
      <p>
        <Link to={EXPLORER_PATH}>Back to the explorer</Link>
      </p>
    </main>
  );
}
