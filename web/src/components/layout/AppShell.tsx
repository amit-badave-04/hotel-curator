import { ExternalLink, Hotel } from 'lucide-react';
import { useEffect, useRef, type ReactNode } from 'react';
import { NavLink, Outlet, ScrollRestoration, useLocation, useNavigation } from 'react-router';

import { useGetMeta, useListCities } from '@/api/generated/endpoints';
import { ThemeToggle } from '@/components/layout/ThemeToggle';
import { compareHref, useCompare } from '@/lib/compare';
import { formatCount } from '@/lib/domain';
import { EXPLORER_PATH } from '@/lib/filters';
import { formatPeriod } from '@/lib/policy';
import { ISSUES_URL, NOTICE_PATH, PUBLIC_ORIGIN, REPO_URL, THIRD_PARTY_PATH } from '@/lib/site';
import { cn } from '@/lib/utils';

const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  cn(
    'inline-flex min-h-10 items-center rounded-md px-3 text-sm font-medium no-underline hover:bg-muted',
    isActive ? 'bg-muted text-foreground' : 'text-muted-foreground hover:text-foreground',
  );

function ScopeStatement() {
  const meta = useGetMeta();
  const cities = useListCities();
  const count = meta.data?.hotels;
  const period = formatPeriod(meta.data?.data_period);
  const names = cities.data?.map((c) => c.city) ?? [];
  const cityText =
    names.length > 1
      ? ` in ${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
      : names.length === 1
        ? ` in ${names[0]}`
        : '';
  return (
    <p>
      Based on guest reviews{period ? ` from ${period}` : ''}
      {count !== undefined ? ` for ${formatCount(count)} hotels` : ''}
      {cityText}. Tiers describe what guests wrote then, not how the hotels are today.
    </p>
  );
}

/** A plain link out of the app (to a file, the API docs or GitHub). */
function FooterLink({
  href,
  external = false,
  children,
}: {
  href: string;
  external?: boolean;
  children: ReactNode;
}) {
  return (
    <a href={href} className="inline-flex min-h-6 items-center gap-1 text-link underline">
      {children}
      {external && <ExternalLink aria-hidden="true" className="size-3.5" />}
    </a>
  );
}

/**
 * The page's public address without its query string, for search engines:
 * explorer filters and compare lists are views of the same page. React
 * moves the link into <head>.
 */
function CanonicalLink({ pathname }: { pathname: string }) {
  return <link rel="canonical" href={`${PUBLIC_ORIGIN}${pathname}`} />;
}

export function AppShell() {
  const location = useLocation();
  const navigation = useNavigation();
  // A page whose code is still downloading (every page but the landing page).
  const loading = navigation.state === 'loading';
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
      <CanonicalLink pathname={location.pathname} />
      {loading && (
        <div
          aria-hidden="true"
          className="route-progress fixed inset-x-0 top-0 z-50 h-1 bg-primary"
        />
      )}
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
            end
            className="inline-flex min-h-10 items-center gap-2 rounded-md font-semibold text-foreground no-underline"
          >
            <Hotel aria-hidden="true" className="size-5 text-primary" />
            hotel-curator
          </NavLink>
          <div className="flex items-center gap-1">
            <nav aria-label="Main">
              <ul className="flex flex-wrap items-center gap-1">
                <li>
                  <NavLink to={EXPLORER_PATH} className={navLinkClass}>
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
        aria-busy={loading || undefined}
        className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 sm:py-8"
      >
        <Outlet />
      </main>
      <footer className="border-t bg-card">
        <div className="mx-auto max-w-6xl space-y-3 px-4 py-5 text-sm text-muted-foreground">
          <div className="space-y-1">
            <ScopeStatement />
            <p>
              Labels are machine-made and checked against people; the data licence is unconfirmed. A
              non-commercial demo, not affiliated with Booking.com. See{' '}
              <NavLink to="/about" className="text-link underline">
                how this works
              </NavLink>
              .
            </p>
          </div>
          <ul className="flex flex-wrap gap-x-5 gap-y-1">
            <li>
              <FooterLink href={REPO_URL} external>
                Source code on GitHub
              </FooterLink>
            </li>
            <li>
              <FooterLink href={NOTICE_PATH}>Data sources and attribution (NOTICE)</FooterLink>
            </li>
            <li>
              <FooterLink href={THIRD_PARTY_PATH}>Third-party licences</FooterLink>
            </li>
            <li>
              <FooterLink href={ISSUES_URL} external>
                Report a mistake or ask for a removal
              </FooterLink>
            </li>
            <li>
              <FooterLink href="/api/docs">API documentation</FooterLink>
            </li>
          </ul>
        </div>
      </footer>
      <ScrollRestoration />
    </div>
  );
}
