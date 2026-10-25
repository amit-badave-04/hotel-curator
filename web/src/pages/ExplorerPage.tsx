import { keepPreviousData } from '@tanstack/react-query';
import { ChevronLeft, ChevronRight, Search, SlidersHorizontal, TriangleAlert } from 'lucide-react';
import { useId, useMemo, useRef, useState, type FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router';

import { useListCities, useListHotels } from '@/api/generated/endpoints';
import type { Attribute, HotelSummary, SortOrder, Tier } from '@/api/generated/model';
import { GradeBadge, TierBadge } from '@/components/badges';
import { CompareButton } from '@/components/CompareButton';
import { EmptyState, ErrorState } from '@/components/states';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Input, Label } from '@/components/ui/input';
import { InfoTip } from '@/components/ui/popover';
import { Select } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import {
  ATTRIBUTES,
  attributeName,
  ESSENTIALS,
  joinAttributes,
  joinStrengths,
  MAX_QUERY_LENGTH,
  NOT_ENOUGH_TIERS,
  plural,
  SORT_LABELS,
  strengthName,
  summaryWhy,
} from '@/lib/domain';
import {
  activeFilterCount,
  DEFAULT_FILTERS,
  filtersToSearchParams,
  PAGE_SIZE,
  parseFilters,
  toApiParams,
  type ExplorerFilters,
} from '@/lib/filters';
import { cn } from '@/lib/utils';

/** Tier filter options; the two "not enough" tiers are one choice. */
const TIER_OPTIONS: { label: string; tiers: Tier[] }[] = [
  { label: 'Excellent', tiers: ['excellent'] },
  { label: 'Good', tiers: ['good'] },
  { label: 'Fair', tiers: ['fair'] },
  { label: 'Caution', tiers: ['caution'] },
  { label: 'Not enough evidence to judge', tiers: [...NOT_ENOUGH_TIERS] },
];

interface CheckItemProps {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}

function CheckItem({ label, checked, onChange }: CheckItemProps) {
  return (
    <li>
      <label className="flex min-h-8 cursor-pointer items-center gap-2 text-sm">
        <Checkbox checked={checked} onCheckedChange={(value) => onChange(value)} />
        {label}
      </label>
    </li>
  );
}

function toggle<T>(values: readonly T[], value: T, on: boolean): T[] {
  return on ? [...values.filter((v) => v !== value), value] : values.filter((v) => v !== value);
}

interface FiltersProps {
  filters: ExplorerFilters;
  update: (patch: Partial<ExplorerFilters>) => void;
  clear: () => void;
}

function FiltersForm({ filters, update, clear }: FiltersProps) {
  const cities = useListCities();
  const ids = useId();

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = new FormData(event.currentTarget).get('q');
    update({
      q: String(value ?? '')
        .trim()
        .slice(0, MAX_QUERY_LENGTH),
    });
  };

  const attributeGroup = (
    legend: string,
    hint: string,
    selected: Attribute[],
    key: 'strong' | 'avoidWeak',
    nameOf: (attribute: Attribute) => string,
  ) => (
    <fieldset className="space-y-1">
      <legend className="text-sm font-semibold">{legend}</legend>
      <p className="text-xs text-muted-foreground">{hint}</p>
      <ul className="grid grid-cols-2 gap-x-2 lg:grid-cols-1">
        {ATTRIBUTES.map((attribute) => (
          <CheckItem
            key={attribute}
            label={nameOf(attribute)}
            checked={selected.includes(attribute)}
            onChange={(on) => update({ [key]: toggle(selected, attribute, on) })}
          />
        ))}
      </ul>
    </fieldset>
  );

  return (
    <form role="search" aria-label="Filter hotels" onSubmit={onSubmit} className="space-y-5">
      <div className="space-y-1.5">
        <Label htmlFor={`${ids}-q`}>Hotel name contains</Label>
        <div className="flex gap-2">
          <Input
            id={`${ids}-q`}
            key={filters.q}
            name="q"
            type="search"
            defaultValue={filters.q}
            maxLength={MAX_QUERY_LENGTH}
            autoComplete="off"
          />
          <Button type="submit" variant="outline" size="icon" aria-label="Search by name">
            <Search aria-hidden="true" />
          </Button>
        </div>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor={`${ids}-city`}>City</Label>
        <Select
          id={`${ids}-city`}
          value={filters.city ?? ''}
          onChange={(event) => update({ city: event.target.value || null })}
        >
          <option value="">All cities</option>
          {cities.data?.map((city) => (
            <option key={city.city} value={city.city}>
              {city.city}, {city.country} ({city.hotels})
            </option>
          ))}
          {filters.city && !cities.data?.some((c) => c.city === filters.city) && (
            <option value={filters.city}>{filters.city}</option>
          )}
        </Select>
        {cities.isError && <p className="text-xs text-weak">The city list could not be loaded.</p>}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor={`${ids}-sort`}>Sort by</Label>
        <Select
          id={`${ids}-sort`}
          value={filters.sort}
          onChange={(event) => update({ sort: event.target.value as SortOrder })}
        >
          {(Object.keys(SORT_LABELS) as SortOrder[]).map((sort) => (
            <option key={sort} value={sort}>
              {SORT_LABELS[sort]}
            </option>
          ))}
        </Select>
      </div>

      <fieldset className="space-y-1">
        <legend className="text-sm font-semibold">Tier</legend>
        <ul>
          {TIER_OPTIONS.map((option) => {
            const checked = option.tiers.every((t) => filters.tiers.includes(t));
            return (
              <CheckItem
                key={option.label}
                label={option.label}
                checked={checked}
                onChange={(on) =>
                  update({
                    tiers: on
                      ? [...filters.tiers.filter((t) => !option.tiers.includes(t)), ...option.tiers]
                      : filters.tiers.filter((t) => !option.tiers.includes(t)),
                  })
                }
              />
            );
          })}
        </ul>
      </fieldset>

      {attributeGroup(
        'Strong in',
        'Only hotels confirmed Strong in all of these.',
        filters.strong,
        'strong',
        strengthName,
      )}
      {attributeGroup(
        'Not weak in',
        'Leave out hotels graded Weak in any of these.',
        filters.avoidWeak,
        'avoidWeak',
        attributeName,
      )}

      <Button
        type="button"
        variant="outline"
        className="w-full"
        onClick={clear}
        disabled={activeFilterCount(filters) === 0 && filters.sort === 'best'}
        // Stays focusable once the filters are cleared, so focus is not lost.
        focusableWhenDisabled
      >
        Clear filters
      </Button>
    </form>
  );
}

function HotelCard({ hotel }: { hotel: HotelSummary }) {
  const headingId = useId();
  const otherHighlights = hotel.highlights.filter((a) => !ESSENTIALS.includes(a));
  return (
    <Card className="flex h-full flex-col">
      <article aria-labelledby={headingId} className="flex flex-1 flex-col gap-3 p-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <h3 id={headingId} className="text-lg leading-snug font-semibold">
              <Link
                to={`/hotels/${hotel.hotel_id}`}
                className="text-foreground no-underline hover:text-link hover:underline"
              >
                {hotel.name}
              </Link>
            </h3>
            <p className="text-sm text-muted-foreground">
              {hotel.city}, {hotel.country}
            </p>
          </div>
          <TierBadge tier={hotel.tier} label={hotel.label} />
        </div>

        <p className="text-sm">{summaryWhy(hotel)}</p>

        <dl className="grid grid-cols-3 gap-2 text-sm">
          {ESSENTIALS.map((attribute) => {
            const grade = hotel.essentials[attribute];
            return (
              <div key={attribute} className="min-w-0 space-y-1">
                <dt className="text-xs text-muted-foreground">{attributeName(attribute)}</dt>
                <dd>{grade ? <GradeBadge grade={grade} attribute={attribute} /> : 'n/a'}</dd>
              </div>
            );
          })}
        </dl>

        {hotel.catches.length > 0 && (
          <p className="flex flex-wrap items-center gap-1.5 text-sm">
            <TriangleAlert aria-hidden="true" className="size-4 text-weak" />
            <span className="font-medium">Catch:</span>
            {joinAttributes(hotel.catches)}
          </p>
        )}
        {otherHighlights.length > 0 && (
          <p className="text-sm text-muted-foreground">
            <span className="font-medium text-foreground">Also strong:</span>{' '}
            {joinStrengths(otherHighlights)}
          </p>
        )}

        <div className="mt-auto flex flex-wrap items-center justify-between gap-2 border-t pt-3">
          <p className="text-sm text-muted-foreground">
            {plural(hotel.reviews_eligible, 'review')} used
          </p>
          <CompareButton hotelId={hotel.hotel_id} name={hotel.name} />
        </div>
      </article>
    </Card>
  );
}

function ResultsSkeleton() {
  return (
    <div role="status" aria-live="polite">
      <span className="sr-only">Loading hotels</span>
      <ul className="grid gap-4 md:grid-cols-2" aria-hidden="true">
        {Array.from({ length: 4 }, (_, i) => (
          <li key={i}>
            <Card className="space-y-3 p-4">
              <Skeleton className="h-6 w-2/3" />
              <Skeleton className="h-4 w-1/3" />
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-10 w-full" />
            </Card>
          </li>
        ))}
      </ul>
    </div>
  );
}

interface PaginationProps {
  page: number;
  totalPages: number;
  hrefFor: (page: number) => string;
  /** Called on a page link click; the link may not exist on the next page. */
  onNavigate: () => void;
}

function Pagination({ page, totalPages, hrefFor, onNavigate }: PaginationProps) {
  if (totalPages <= 1) return null;
  const linkClass =
    'inline-flex min-h-10 items-center gap-1 rounded-md border border-input bg-card px-3 text-sm font-medium text-foreground no-underline hover:bg-muted';
  const disabledClass =
    'inline-flex min-h-10 items-center gap-1 rounded-md border px-3 text-sm text-muted-foreground opacity-60';
  return (
    <nav aria-label="Pagination" className="flex items-center justify-between gap-2">
      {page > 1 ? (
        <Link to={hrefFor(page - 1)} className={linkClass} onClick={onNavigate}>
          <ChevronLeft aria-hidden="true" className="size-4" />
          Previous
        </Link>
      ) : (
        <span className={disabledClass} aria-disabled="true">
          <ChevronLeft aria-hidden="true" className="size-4" />
          Previous
        </span>
      )}
      <p className="text-sm text-muted-foreground">
        Page {page} of {totalPages}
      </p>
      {page < totalPages ? (
        <Link to={hrefFor(page + 1)} className={linkClass} onClick={onNavigate}>
          Next
          <ChevronRight aria-hidden="true" className="size-4" />
        </Link>
      ) : (
        <span className={disabledClass} aria-disabled="true">
          Next
          <ChevronRight aria-hidden="true" className="size-4" />
        </span>
      )}
    </nav>
  );
}

export function ExplorerPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo(() => parseFilters(searchParams), [searchParams]);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const panelId = useId();
  const resultsHeading = useRef<HTMLHeadingElement>(null);

  const hotels = useListHotels(toApiParams(filters), {
    query: { placeholderData: keepPreviousData },
  });

  // Filter changes keep the scroll position: the control just used stays in view.
  const update = (patch: Partial<ExplorerFilters>) => {
    setSearchParams(filtersToSearchParams({ ...filters, ...patch, page: patch.page ?? 1 }), {
      preventScrollReset: true,
    });
  };
  const clear = () =>
    setSearchParams(filtersToSearchParams(DEFAULT_FILTERS), { preventScrollReset: true });
  const hrefFor = (page: number) => `/?${filtersToSearchParams({ ...filters, page }).toString()}`;
  /**
   * For controls that vanish once used (page links, the empty state's
   * buttons): move focus to the results heading instead of losing it.
   */
  const focusResults = () => resultsHeading.current?.focus();

  const data = hotels.data;
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const active = activeFilterCount(filters);

  return (
    <>
      <title>Explore hotels | hotel-curator</title>
      <div className="mb-6 space-y-2">
        <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">Explore hotels</h1>
        <p className="max-w-3xl text-muted-foreground">
          Hotels graded on what guests wrote about cleanliness, room, staff and five more
          attributes, with how sure we are. Filter by city, tier or the attributes that matter to
          you.
          <InfoTip label="How are tiers decided?">
            The essentials (cleanliness, room, staff) decide the tier. A weak spot elsewhere can
            only hold a hotel back. See the About page for every rule.
          </InfoTip>
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[17rem_1fr]">
        <section aria-labelledby={`${panelId}-heading`} className="lg:self-start">
          <div className="flex items-center justify-between gap-2">
            <h2 id={`${panelId}-heading`} className="text-lg font-semibold">
              Filters
              {active > 0 && (
                <span className="ml-2 text-sm font-normal text-muted-foreground">
                  ({active} active)
                </span>
              )}
            </h2>
            <Button
              variant="outline"
              size="sm"
              className="lg:hidden"
              aria-expanded={filtersOpen}
              aria-controls={panelId}
              onClick={() => setFiltersOpen((open) => !open)}
            >
              <SlidersHorizontal aria-hidden="true" />
              {filtersOpen ? 'Hide filters' : 'Show filters'}
            </Button>
          </div>
          <div
            id={panelId}
            className={cn('mt-4 rounded-lg border bg-card p-4 lg:block', !filtersOpen && 'hidden')}
          >
            <FiltersForm filters={filters} update={update} clear={clear} />
          </div>
        </section>

        <section aria-labelledby={`${panelId}-results`} className="min-w-0 space-y-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2
              id={`${panelId}-results`}
              ref={resultsHeading}
              tabIndex={-1}
              className="text-lg font-semibold"
            >
              Results
            </h2>
            <p role="status" aria-live="polite" className="text-sm text-muted-foreground">
              {data
                ? `${plural(data.total, 'hotel')} match${data.total === 1 ? 'es' : ''}${
                    hotels.isPlaceholderData ? ' (updating)' : ''
                  }`
                : hotels.isPending
                  ? 'Loading'
                  : ''}
            </p>
          </div>

          {hotels.isError ? (
            <ErrorState
              error={hotels.error}
              title="The hotel list could not be loaded"
              onRetry={() => void hotels.refetch()}
            />
          ) : hotels.isPending ? (
            <ResultsSkeleton />
          ) : data && data.items.length === 0 ? (
            <EmptyState title="No hotels match these filters">
              <p className="max-w-md text-sm text-muted-foreground">
                {filters.page > 1 && data.total > 0
                  ? 'This page is past the end of the results.'
                  : 'Try fewer "Strong in" attributes, another city, or more tiers.'}
              </p>
              {filters.page > 1 && data.total > 0 ? (
                <Link to={hrefFor(1)} onClick={focusResults}>
                  Go to the first page
                </Link>
              ) : (
                <Button
                  variant="outline"
                  onClick={() => {
                    focusResults();
                    clear();
                  }}
                >
                  Clear filters
                </Button>
              )}
            </EmptyState>
          ) : data ? (
            <>
              <ul className="grid gap-4 md:grid-cols-2" aria-busy={hotels.isPlaceholderData}>
                {data.items.map((hotel) => (
                  <li key={hotel.hotel_id}>
                    <HotelCard hotel={hotel} />
                  </li>
                ))}
              </ul>
              <Pagination
                page={filters.page}
                totalPages={totalPages}
                hrefFor={hrefFor}
                onNavigate={focusResults}
              />
              <p className="text-xs text-muted-foreground">
                Showing up to {PAGE_SIZE} hotels per page. &quot;Best tier first&quot; orders hotels
                within a tier by a cautious score, so hotels with more evidence come first. Hotels
                without enough evidence come after the graded tiers; that is not a judgement on
                them.
              </p>
            </>
          ) : null}
        </section>
      </div>
    </>
  );
}
