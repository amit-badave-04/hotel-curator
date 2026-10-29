import { Link } from 'react-router';

import { EXPLORER_PATH } from '@/lib/filters';

export function NotFoundPage() {
  return (
    <div className="space-y-4">
      <title>Page not found | hotel-curator</title>
      <h1 className="text-2xl font-bold">Page not found</h1>
      <p>There is nothing at this address.</p>
      <p>
        <Link to={EXPLORER_PATH}>Go to the explorer</Link>, <Link to="/">the home page</Link> or{' '}
        <Link to="/about">read how it works</Link>.
      </p>
    </div>
  );
}
