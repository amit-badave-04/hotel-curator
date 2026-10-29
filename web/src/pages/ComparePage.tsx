import { keepPreviousData } from '@tanstack/react-query';
import { ArrowDown, Info, Trophy, X } from 'lucide-react';
import { useEffect, useRef, useState, type Ref } from 'react';
import { Link, useSearchParams } from 'react-router';

import { useCompareHotels } from '@/api/generated/endpoints';
import type { CompareRow, CompareValue, Comparison, HotelSummary } from '@/api/generated/model';
import { GradeBadge, TierBadge } from '@/components/badges';
import { IntervalChart } from '@/components/IntervalChart';
import { ErrorState, LoadingBlock } from '@/components/states';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { compareHref, parseCompareIds, useCompare } from '@/lib/compare';
import {
  attributeName,
  complaintAxisMax,
  familyOf,
  formatCount,
  formatPercent,
  formatProbability,
  formatRange,
  isWhole,
  MAX_COMPARE,
} from '@/lib/domain';
import { EXPLORER_PATH } from '@/lib/filters';
import { cn } from '@/lib/utils';

/**
 * Hotels are tagged A to D in the cards, the table and the charts, so two
 * hotels of one chain ("Austria Trend Hotel ...") can always be told apart.
 */
function tagOf(index: number): string {
  return String.fromCharCode(65 + index);
}

function HotelTag({ tag }: { tag: string }) {
  return (
    <span
      aria-hidden="true"
      className="mr-1.5 inline-flex size-5 shrink-0 items-center justify-center rounded border border-input bg-muted align-[-0.15em] text-xs font-semibold text-foreground"
    >
      {tag}
    </span>
  );
}

/** Hotels whose likely range lies entirely on the better side of this one. */
function clearlyAhead(row: CompareRow, value: CompareValue): CompareValue[] {
  const lowerIsBetter = familyOf(row.attribute) === 'must_be';
  return row.values.filter(
    (other) =>
      other.hotel_id !== value.hotel_id &&
      (lowerIsBetter ? other.high < value.low : other.low > value.high),
  );
}

function joinNames(names: string[]): string {
  if (names.length <= 1) return names.join('');
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

function LeaderBadge({ probability }: { probability: number | null | undefined }) {
  return (
    <Badge tone="leader">
      <Trophy aria-hidden="true" />
      Credible leader
      {probability != null && ` (${formatProbability(probability)} sure)`}
    </Badge>
  );
}

function measureHint(row: CompareRow): string {
  return familyOf(row.attribute) === 'must_be'
    ? 'Complaint rate; lower is better'
    : 'Share of praise; higher is better';
}

function CompareTable({ comparison }: { comparison: Comparison }) {
  const names = new Map(comparison.hotels.map((h) => [h.hotel_id, h.name]));
  return (
    <Table containerLabel="Attributes side by side (scrollable table)">
      <TableCaption>
        Each cell shows the grade, the observed value and its 90% likely range. A cell is
        highlighted as &quot;Credible leader&quot; only when that hotel is credibly better than
        every other hotel here (at least 95% sure). &quot;Clearly behind&quot; marks a hotel whose
        range does not overlap a better hotel&apos;s range.
      </TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead scope="col" className="min-w-36">
            Attribute
          </TableHead>
          {comparison.hotels.map((hotel, index) => (
            <TableHead
              key={hotel.hotel_id}
              scope="col"
              className="min-w-44 text-sm tracking-normal text-foreground normal-case"
            >
              <HotelTag tag={tagOf(index)} />
              {hotel.name}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {comparison.attributes.map((row) => {
          const family = familyOf(row.attribute);
          // Laid out by hotel id, not by position in the row.
          const values = new Map(row.values.map((v) => [v.hotel_id, v]));
          return (
            <TableRow key={row.attribute}>
              <TableHead
                scope="row"
                className="h-auto align-top text-sm font-medium tracking-normal text-foreground normal-case"
              >
                {attributeName(row.attribute)}
                <span className="block text-xs font-normal text-muted-foreground">
                  {measureHint(row)}
                </span>
                {!row.leader && (
                  <span className="mt-1 block text-xs font-normal text-muted-foreground">
                    No single credible leader
                  </span>
                )}
              </TableHead>
              {comparison.hotels.map((hotel) => {
                const value = values.get(hotel.hotel_id);
                if (!value) {
                  return (
                    <TableCell key={hotel.hotel_id} className="text-muted-foreground">
                      n/a
                    </TableCell>
                  );
                }
                const leader = row.leader === value.hotel_id;
                const ahead = clearlyAhead(row, value);
                return (
                  <TableCell
                    key={value.hotel_id}
                    className={cn(
                      'space-y-1',
                      leader && 'border-2 border-leader-border bg-leader-bg',
                    )}
                  >
                    <GradeBadge grade={value.grade} family={family} />
                    <p className="font-semibold">{formatPercent(value.observed)}</p>
                    <p className="text-xs text-muted-foreground">
                      90%: {formatRange(value.low, value.high)}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {isWhole(value.opinions) ? '' : 'about '}
                      {formatCount(value.opinions)} {family === 'must_be' ? 'reviews' : 'opinions'}
                    </p>
                    {leader && <LeaderBadge probability={row.leader_probability} />}
                    {leader && (
                      <span className="sr-only">
                        {names.get(value.hotel_id)} is credibly best on{' '}
                        {attributeName(row.attribute)}.
                      </span>
                    )}
                    {ahead.length > 0 && (
                      <p className="flex items-start gap-1 text-xs font-medium">
                        <ArrowDown aria-hidden="true" className="mt-px size-3.5 shrink-0" />
                        <span>
                          Clearly behind{' '}
                          {joinNames(ahead.map((o) => names.get(o.hotel_id) ?? o.hotel_id))}
                          <span className="font-normal text-muted-foreground">
                            {' '}
                            (ranges do not overlap)
                          </span>
                        </span>
                      </p>
                    )}
                  </TableCell>
                );
              })}
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

function CompareCharts({ comparison }: { comparison: Comparison }) {
  const names = new Map(comparison.hotels.map((h) => [h.hotel_id, h.name]));
  const labels = new Map(
    comparison.hotels.map((h, index) => [h.hotel_id, `(${tagOf(index)}) ${h.name}`]),
  );
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {comparison.attributes.map((row) => {
        const family = familyOf(row.attribute);
        const max = family === 'must_be' ? complaintAxisMax(row.values.map((v) => v.high)) : 1;
        const leaderName = row.leader ? names.get(row.leader) : null;
        return (
          <figure key={row.attribute} className="space-y-2 rounded-lg border bg-card p-4">
            <figcaption>
              <h3 className="font-semibold">{attributeName(row.attribute)}</h3>
              <p className="text-sm text-muted-foreground">
                {measureHint(row)}.{' '}
                {leaderName
                  ? `Credible leader: ${leaderName}.`
                  : 'No hotel is credibly better than all the others here.'}
              </p>
            </figcaption>
            <IntervalChart
              rows={row.values.map((v) => ({
                key: v.hotel_id,
                label: labels.get(v.hotel_id) ?? v.hotel_id,
                fullLabel: labels.get(v.hotel_id) ?? v.hotel_id,
                value: v.observed,
                low: v.low,
                high: v.high,
                emphasis: row.leader === v.hotel_id,
              }))}
              domain={[0, max]}
              format={formatPercent}
              valueName={
                family === 'must_be' ? 'Observed complaint rate' : 'Observed share of praise'
              }
              rangeName="90% likely range"
              emphasisName="Credible leader"
              title={`${attributeName(row.attribute)} by hotel`}
              description="Observed values with 90% likely ranges; the table above lists the same numbers."
              labelWidth={150}
              rowHeight={52}
            />
          </figure>
        );
      })}
    </div>
  );
}

interface HotelHeaderProps {
  hotel: HotelSummary;
  tag: string;
  onRemove: () => void;
  removeRef: Ref<HTMLButtonElement>;
}

function HotelHeader({ hotel, tag, onRemove, removeRef }: HotelHeaderProps) {
  return (
    <Card className="flex w-full flex-col gap-2 p-4">
      <h2 className="text-base leading-snug font-semibold">
        <HotelTag tag={tag} />
        <Link
          to={`/hotels/${hotel.hotel_id}`}
          className="text-foreground no-underline hover:text-link hover:underline"
        >
          {hotel.name}
        </Link>
      </h2>
      <p className="text-sm text-muted-foreground">
        {hotel.city}, {hotel.country}
      </p>
      <TierBadge tier={hotel.tier} label={hotel.label} className="self-start" />
      <Button
        variant="ghost"
        size="sm"
        className="mt-auto self-start"
        onClick={onRemove}
        ref={removeRef}
        aria-label={`Remove ${hotel.name} from the comparison`}
      >
        <X aria-hidden="true" />
        Remove
      </Button>
    </Card>
  );
}

function PickMore({ ids }: { ids: string[] }) {
  const compare = useCompare();
  const listed = compare.items;
  const listIds = listed.map((i) => i.id);
  const showList = listed.length > 0 && listIds.join() !== ids.join();
  return (
    <div className="space-y-4 rounded-lg border bg-card p-4">
      <p>
        Pick two to {MAX_COMPARE} hotels to compare. Use &quot;Add to compare&quot; on the{' '}
        <Link to={EXPLORER_PATH}>explorer</Link> or on a hotel page.
      </p>
      {listed.length > 0 && (
        <div className="space-y-2">
          <h2 className="font-semibold">Your compare list</h2>
          <ul className="space-y-1">
            {listed.map((item) => (
              <li key={item.id} className="flex flex-wrap items-center gap-2">
                <Link to={`/hotels/${item.id}`}>{item.name}</Link>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => compare.remove(item.id)}
                  aria-label={`Remove ${item.name} from the compare list`}
                >
                  <X aria-hidden="true" />
                  Remove
                </Button>
              </li>
            ))}
          </ul>
          {showList && listed.length >= 2 && (
            <Link
              to={compareHref(listIds)}
              className="inline-flex min-h-10 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground no-underline hover:bg-primary-hover"
            >
              Compare these {listed.length} hotels
            </Link>
          )}
        </div>
      )}
    </div>
  );
}

export function ComparePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { ids, dropped } = parseCompareIds(searchParams);
  const enough = ids.length >= 2;
  const query = useCompareHotels(
    { ids },
    { query: { enabled: enough, placeholderData: keepPreviousData } },
  );
  const { replace, remove } = useCompare();
  const heading = useRef<HTMLHeadingElement>(null);
  const removeButtons = useRef(new Map<string, HTMLButtonElement>());
  const [announcement, setAnnouncement] = useState('');

  // The compare list follows the comparison on screen, but never the
  // previous comparison still shown while the next one loads.
  const shown = enough && !query.isPlaceholderData ? query.data : undefined;
  useEffect(() => {
    if (shown) replace(shown.hotels.map((h) => ({ id: h.hotel_id, name: h.name })));
  }, [shown, replace]);

  // Keep the scroll position: the page stays where the reader was.
  const setIds = (next: string[]) => {
    const params = new URLSearchParams();
    for (const id of next) params.append('ids', id);
    setSearchParams(params, { preventScrollReset: true });
  };
  const startOver = () => {
    heading.current?.focus();
    setIds([]);
  };
  const removeHotel = (hotel: HotelSummary) => {
    const rest = ids.filter((x) => x !== hotel.hotel_id);
    // The button is about to go: focus the next hotel's Remove button (or
    // the previous one), or the page heading when the comparison ends.
    const index = ids.indexOf(hotel.hotel_id);
    const nextId = rest.length >= 2 ? (rest[index] ?? rest[index - 1]) : undefined;
    const target = nextId ? removeButtons.current.get(nextId) : undefined;
    if (target) target.focus();
    else heading.current?.focus();
    setAnnouncement(`${hotel.name} was removed from the comparison.`);
    remove(hotel.hotel_id);
    setIds(rest);
  };
  const removeRef = (id: string) => (element: HTMLButtonElement | null) => {
    if (element) removeButtons.current.set(id, element);
    else removeButtons.current.delete(id);
  };

  return (
    <div className="space-y-6">
      <title>Compare hotels | hotel-curator</title>
      <div className="space-y-2">
        <h1 ref={heading} tabIndex={-1} className="text-2xl font-bold tracking-tight sm:text-3xl">
          Compare hotels
        </h1>
        <p className="flex max-w-3xl gap-2 text-muted-foreground">
          <Info aria-hidden="true" className="mt-1 size-4 shrink-0" />
          <span>
            A hotel is highlighted as &quot;Credible leader&quot; on an attribute only when it is
            better than every other hotel here with at least 95% certainty. A hotel is marked
            &quot;Clearly behind&quot; when its likely range does not overlap a better hotel&apos;s
            range. Other gaps may still be real or may be noise from a small number of reviews:
            check whether the ranges overlap.
          </span>
        </p>
        {dropped > 0 && (
          <p className="text-sm text-muted-foreground" role="note">
            {dropped === 1 ? 'One hotel id' : `${dropped} hotel ids`} in the link{' '}
            {dropped === 1 ? 'was' : 'were'} not valid, repeated or over the limit of {MAX_COMPARE},
            and {dropped === 1 ? 'was' : 'were'} left out.
          </p>
        )}
        <p role="status" className="sr-only">
          {announcement}
        </p>
      </div>

      {!enough ? (
        <PickMore ids={ids} />
      ) : query.isError ? (
        <ErrorState
          error={query.error}
          title="The comparison could not be loaded"
          onRetry={() => void query.refetch()}
        >
          <Button variant="outline" size="sm" onClick={startOver}>
            Start a new comparison
          </Button>
        </ErrorState>
      ) : query.isPending ? (
        <LoadingBlock label="Loading the comparison" lines={6} />
      ) : (
        <div className="space-y-6" aria-busy={query.isPlaceholderData}>
          {query.isPlaceholderData && (
            <p className="text-sm text-muted-foreground">Updating the comparison...</p>
          )}
          <section aria-label="Hotels compared" className="space-y-2">
            <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {query.data.hotels.map((hotel, index) => (
                <li key={hotel.hotel_id} className="flex">
                  <HotelHeader
                    hotel={hotel}
                    tag={tagOf(index)}
                    onRemove={() => removeHotel(hotel)}
                    removeRef={removeRef(hotel.hotel_id)}
                  />
                </li>
              ))}
            </ul>
            {ids.length < MAX_COMPARE && (
              <p className="text-sm">
                <Link to={EXPLORER_PATH}>Add another hotel from the explorer</Link>
              </p>
            )}
          </section>
          <section aria-labelledby="side-by-side" className="space-y-3">
            <h2 id="side-by-side" className="text-xl font-semibold">
              Side by side
            </h2>
            <CompareTable comparison={query.data} />
          </section>
          <section aria-labelledby="intervals" className="space-y-3">
            <h2 id="intervals" className="text-xl font-semibold">
              Likely ranges by attribute
            </h2>
            <p className="max-w-3xl text-sm text-muted-foreground">
              Ranges that do not overlap show a real difference; overlapping ranges usually mean the
              hotels cannot be told apart on that attribute. The credible leader is drawn in orange
              and named in each caption.
            </p>
            <CompareCharts comparison={query.data} />
          </section>
        </div>
      )}
    </div>
  );
}
