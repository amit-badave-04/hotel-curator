import { isRouteErrorResponse, Link, useRouteError } from 'react-router';

/** Last-resort error screen when a page fails to render or load its code. */
export function RouteError() {
  const error = useRouteError();
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
        <Link to="/">Back to the explorer</Link>
      </p>
    </main>
  );
}
