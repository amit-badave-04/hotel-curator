import { Hotel, Moon, Sun } from 'lucide-react';
import { useEffect, useRef } from 'react';
import { NavLink, Outlet, ScrollRestoration, useLocation } from 'react-router';

import { useGetMeta, useListCities } from '@/api/generated/endpoints';
import { Button } from '@/components/ui/button';
import { Tooltip } from '@/components/ui/tooltip';
import { compareHref, useCompare } from '@/lib/compare';
import { formatCount } from '@/lib/domain';
import { useTheme } from '@/lib/theme';
import { cn } from '@/lib/utils';

function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const next = theme === 'dark' ? 'light' : 'dark';
  return (
    <Tooltip content={`Use ${next} theme`}>
      <Button
        variant="ghost"
        size="icon"
        onClick={() => setTheme(next)}
        aria-label={`Use ${next} theme`}
      >
        {theme === 'dark' ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
      </Button>
    </Tooltip>
  );
}

const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  cn(
    'inline-flex min-h-10 items-center rounded-md px-3 text-sm font-medium no-underline hover:bg-muted',
    isActive ? 'bg-muted text-foreground' : 'text-muted-foreground hover:text-foreground',
  );

function ScopeStatement() {
  const meta = useGetMeta();
  const cities = useListCities();
  const count = meta.data?.hotels;
  const names = cities.data?.map((c) => c.city) ?? [];
  const cityText =
    names.length > 1
      ? ` in ${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
      : names.length === 1
        ? ` in ${names[0]}`
        : '';
  return (
    <p>
      Based on guest reviews from August 2015 to August 2017
      {count !== undefined ? ` for ${formatCount(count)} hotels` : ''}
      {cityText}. Tiers describe what guests wrote then, not how the hotels are today.
    </p>
  );
}

export function AppShell() {
  const location = useLocation();
  const compare = useCompare();
  const mainRef = useRef<HTMLElement>(null);
  const firstRender = useRef(true);

  // Move focus to the main region when the page (not just its filters)
  // changes, so screen-reader and keyboard users start at the new content.
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    mainRef.current?.focus({ preventScroll: true });
  }, [location.pathname]);

  return (
    <div className="flex min-h-screen flex-col">
      <a
        href="#main"
        className="sr-only z-50 rounded-md bg-primary px-4 py-2 text-primary-foreground focus:not-sr-only focus:absolute focus:top-2 focus:left-2"
      >
        Skip to content
      </a>
      <header className="border-b bg-card">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-2 px-4 py-2">
          <NavLink
            to="/"
            className="inline-flex min-h-10 items-center gap-2 rounded-md font-semibold text-foreground no-underline"
          >
            <Hotel aria-hidden="true" className="size-5 text-primary" />
            hotel-curator
          </NavLink>
          <div className="flex items-center gap-1">
            <nav aria-label="Main">
              <ul className="flex flex-wrap items-center gap-1">
                <li>
                  <NavLink to="/" end className={navLinkClass}>
                    Explore
                  </NavLink>
                </li>
                <li>
                  <NavLink
                    to={compareHref(compare.items.map((item) => item.id))}
                    className={navLinkClass}
                  >
                    Compare
                    {compare.items.length > 0 && (
                      <span className="ml-1.5 rounded-full bg-primary px-1.5 text-xs text-primary-foreground">
                        {compare.items.length}
                        <span className="sr-only"> hotels selected</span>
                      </span>
                    )}
                  </NavLink>
                </li>
                <li>
                  <NavLink to="/about" className={navLinkClass}>
                    About
                  </NavLink>
                </li>
              </ul>
            </nav>
            <ThemeToggle />
          </div>
        </div>
      </header>
      <main
        id="main"
        ref={mainRef}
        tabIndex={-1}
        className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 sm:py-8"
      >
        <Outlet />
      </main>
      <footer className="border-t bg-card">
        <div className="mx-auto max-w-6xl space-y-1 px-4 py-4 text-sm text-muted-foreground">
          <ScopeStatement />
          <p>
            Labels are machine-made and checked against people; the data licence is unconfirmed. See{' '}
            <NavLink to="/about" className="text-link underline">
              how this works
            </NavLink>
            .
          </p>
        </div>
      </footer>
      <ScrollRestoration />
    </div>
  );
}
