import { Link } from 'react-router';

export function NotFoundPage() {
  return (
    <div className="space-y-4">
      <title>Page not found | hotel-curator</title>
      <h1 className="text-2xl font-bold">Page not found</h1>
      <p>There is nothing at this address.</p>
      <p>
        <Link to="/">Go to the explorer</Link> or <Link to="/about">read how it works</Link>.
      </p>
    </div>
  );
}
