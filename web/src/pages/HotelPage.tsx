import {
  ArrowRight,
  Info,
  Minus,
  Quote,
  ThumbsDown,
  ThumbsUp,
  TrendingDown,
  TrendingUp,
  TriangleAlert,
} from 'lucide-react';
import { Link, useParams } from 'react-router';

import { ApiError } from '@/api/fetcher';
import { useGetHotel } from '@/api/generated/endpoints';
import type {
  AttributeOut,
  HotelDetail,
  QuoteOut,
  SegmentOut,
  TrendOut,
} from '@/api/generated/model';
import { GradeBadge, TierBadge } from '@/components/badges';
import { CompareButton } from '@/components/CompareButton';
import { IntervalChart, type IntervalRow } from '@/components/IntervalChart';
import { Section } from '@/components/Section';
import { VolumeChart } from '@/components/VolumeChart';
import { ErrorState, LoadingBlock } from '@/components/states';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { InfoTip } from '@/components/ui/popover';
import { Tabs, TabsList, TabsPanel, TabsTab } from '@/components/ui/tabs';
import { compareHref, useCompare } from '@/lib/compare';
import {
  attributeName,
  complaintAxisMax,
  ESSENTIALS,
  familyOf,
  formatCount,
  formatMonth,
  formatPercent,
  formatProbability,
  formatRange,
  gradeInfo,
  HOTEL_ID_PATTERN,
  isNotEnough,
  isWhole,
  joinAttributes,
  joinStrengths,
  plural,
  TIER_INFO,
  TREND_INFO,
} from '@/lib/domain';

function measureName(attribute: AttributeOut): string {
  return attribute.family === 'must_be' ? 'complaint rate' : 'share of praise';
}

/**
 * Counts are expected counts (sums of probabilities), so they are rounded
 * one by one and marked "about": the parts need not add up exactly.
 */
function opinionsText(attribute: AttributeOut): string {
  if (attribute.family === 'must_be') {
    const about = isWhole(attribute.negative) ? '' : 'About ';
    return `${about}${plural(attribute.negative, 'complaint')} in ${plural(attribute.reviews, 'review')}`;
  }
  const about = isWhole(attribute.opinions, attribute.positive, attribute.negative) ? '' : 'About ';
  return `${about}${plural(attribute.opinions, 'opinion')} (${plural(attribute.positive, 'praise', 'praises')}, ${plural(attribute.negative, 'complaint')})`;
}

const TREND_ICONS = {
  improved: TrendingUp,
  declined: TrendingDown,
  stable: Minus,
  too_few: Minus,
} as const;

function TrendText({
  trend,
  attribute,
}: {
  trend: TrendOut | null | undefined;
  attribute: AttributeOut;
}) {
  if (!trend) return <span className="text-muted-foreground">No trend data</span>;
  const info = TREND_INFO[trend.state];
  const Icon = TREND_ICONS[trend.state];
  const unit = attribute.family === 'must_be' ? 'complaints' : 'praise';
  return (
    <span className="inline-flex flex-col gap-0.5">
      <span className="inline-flex items-center gap-1 font-medium">
        <Icon aria-hidden="true" className="size-4" />
        {info.name}
      </span>
      {trend.recent_mean != null && trend.baseline_mean != null && (
        <span className="text-xs text-muted-foreground">
          Last 6 months {formatPercent(trend.recent_mean)} {unit}, before{' '}
          {formatPercent(trend.baseline_mean)}
        </span>
      )}
    </span>
  );
}

function QuoteItem({ quote }: { quote: QuoteOut }) {
  return (
    <li>
      <figure className="space-y-1.5 rounded-md border bg-background p-3">
        <blockquote className="flex gap-2 text-sm">
          <Quote aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          <p>{quote.text}</p>
        </blockquote>
        <figcaption className="text-xs text-muted-foreground">
          {quote.part === 'liked' ? 'From what the guest liked' : 'From what the guest disliked'}
          {' · '}
          {quote.month}
          {quote.traveler_type ? ` · ${quote.traveler_type}` : ''}
        </figcaption>
      </figure>
    </li>
  );
}

function Quotes({ attribute }: { attribute: AttributeOut }) {
  const quotes = attribute.quotes ?? [];
  const supporting = quotes.filter((q) => q.supports);
  const counter = quotes.filter((q) => !q.supports);
  const name = attributeName(attribute.attribute).toLowerCase();
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <div className="space-y-2">
        <h4 className="flex items-center gap-1.5 text-sm font-semibold">
          <ThumbsUp aria-hidden="true" className="size-4 text-strong" />
          Supporting quotes (back the grade)
        </h4>
        {supporting.length > 0 ? (
          <ul className="space-y-2">
            {supporting.map((quote, i) => (
              <QuoteItem key={i} quote={quote} />
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">
            No clear quote: no review part about {name} was confident enough to cite.
          </p>
        )}
      </div>
      <div className="space-y-2">
        <h4 className="flex items-center gap-1.5 text-sm font-semibold">
          <ThumbsDown aria-hidden="true" className="size-4 text-weak" />
          Counter-examples (go against the grade)
        </h4>
        {counter.length > 0 ? (
          <ul className="space-y-2">
            {counter.map((quote, i) => (
              <QuoteItem key={i} quote={quote} />
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">No clear counter-example.</p>
        )}
      </div>
    </div>
  );
}

function AttributePanel({ attribute }: { attribute: AttributeOut }) {
  const info = gradeInfo(attribute.grade, attribute.family, attribute.lean);
  return (
    <div className="space-y-4 rounded-lg border bg-card p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-lg font-semibold">{attribute.display_name}</h3>
        <GradeBadge grade={attribute.grade} family={attribute.family} lean={attribute.lean} />
        {attribute.essential && <Badge tone="outline">Essential</Badge>}
        {attribute.material && attribute.grade === 'weak' && !attribute.essential && (
          <Badge tone="weak">
            <TriangleAlert aria-hidden="true" />
            Catch
          </Badge>
        )}
      </div>
      <p className="text-sm">{info.meaning}</p>
      <dl className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <dt className="text-muted-foreground">Observed {measureName(attribute)}</dt>
          <dd className="font-semibold tabular-nums">{formatPercent(attribute.observed)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">90% likely range</dt>
          <dd className="font-semibold tabular-nums">
            {formatRange(attribute.low, attribute.high)}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">
            {attribute.family === 'must_be' ? 'Complaints' : 'Opinions'}
          </dt>
          <dd className="tabular-nums">{opinionsText(attribute)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Trend</dt>
          <dd>
            <TrendText trend={attribute.trend} attribute={attribute} />
          </dd>
        </div>
      </dl>
      {attribute.trend && attribute.trend.state !== 'too_few' && (
        <p className="text-xs text-muted-foreground">
          {TREND_INFO[attribute.trend.state].meaning} Chance it improved:{' '}
          {formatProbability(attribute.trend.prob_improved)}; chance it declined:{' '}
          {formatProbability(attribute.trend.prob_declined)}.
        </p>
      )}
      <Quotes attribute={attribute} />
    </div>
  );
}

function AttributeTable({ attributes }: { attributes: AttributeOut[] }) {
  return (
    <Table containerLabel="All eight attributes (scrollable table)">
      <TableCaption>
        Observed is the share of praise among guests who gave an opinion; for noise and wifi it is
        the complaint rate among all guests. The likely range is a 90% interval.
      </TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead scope="col">Attribute</TableHead>
          <TableHead scope="col">Grade</TableHead>
          <TableHead scope="col">Observed</TableHead>
          <TableHead scope="col">90% likely range</TableHead>
          <TableHead scope="col">Evidence</TableHead>
          <TableHead scope="col">Trend</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {attributes.map((a) => (
          <TableRow key={a.attribute}>
            <TableHead
              scope="row"
              className="h-auto align-top text-sm font-medium tracking-normal text-foreground normal-case"
            >
              {a.display_name}
              {a.essential && (
                <span className="block text-xs text-muted-foreground">Essential</span>
              )}
            </TableHead>
            <TableCell>
              <GradeBadge grade={a.grade} family={a.family} lean={a.lean} />
            </TableCell>
            <TableCell>
              {formatPercent(a.observed)}
              <span className="block text-xs text-muted-foreground">
                {a.family === 'must_be' ? 'complained' : 'praise'}
              </span>
            </TableCell>
            <TableCell className="whitespace-nowrap">{formatRange(a.low, a.high)}</TableCell>
            <TableCell className="min-w-40">{opinionsText(a)}</TableCell>
            <TableCell className="min-w-40">
              <TrendText trend={a.trend} attribute={a} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function rowsFor(attributes: AttributeOut[]): IntervalRow[] {
  return attributes.map((a) => ({
    key: a.attribute,
    label: a.display_name,
    value: a.observed,
    low: a.low,
    high: a.high,
  }));
}

function AttributeCharts({ attributes }: { attributes: AttributeOut[] }) {
  const twoSided = attributes.filter((a) => a.family === 'two_sided');
  const mustBe = attributes.filter((a) => a.family === 'must_be');
  const mustBeMax = complaintAxisMax(mustBe.map((a) => a.high));
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      {twoSided.length > 0 && (
        <figure className="space-y-2 rounded-lg border bg-card p-4">
          <figcaption>
            <h3 className="font-semibold">Share of praise</h3>
            <p className="text-sm text-muted-foreground">
              Among guests who gave an opinion. Strong needs the range above 80%; Weak below 50%.
            </p>
          </figcaption>
          <IntervalChart
            rows={rowsFor(twoSided)}
            domain={[0, 1]}
            format={formatPercent}
            valueName="Observed share of praise"
            rangeName="90% likely range"
            references={[
              { value: 0.5, label: '50% (Weak below)', short: '50%' },
              { value: 0.8, label: '80% (Strong above)', short: '80%' },
            ]}
            title="Share of praise by attribute"
            description="Observed share of praise with 90% likely ranges; the table below lists the same numbers."
          />
        </figure>
      )}
      {mustBe.length > 0 && (
        <figure className="space-y-2 rounded-lg border bg-card p-4">
          <figcaption>
            <h3 className="font-semibold">Complaint rate (noise, wifi)</h3>
            <p className="text-sm text-muted-foreground">
              Among all guests; lower is better. Strong needs the range below 5%; Weak above 10%.
            </p>
          </figcaption>
          <IntervalChart
            rows={rowsFor(mustBe)}
            domain={[0, mustBeMax]}
            format={formatPercent}
            valueName="Observed complaint rate"
            rangeName="90% likely range"
            references={[
              { value: 0.05, label: '5% (Strong below)', short: '5%' },
              { value: 0.1, label: '10% (Weak above)', short: '10%' },
            ]}
            title="Complaint rate for noise and wifi"
            description="Observed complaint rate with 90% likely ranges; the table below lists the same numbers."
          />
        </figure>
      )}
    </div>
  );
}

function BulletList({ items, empty }: { items: string[]; empty: string }) {
  if (items.length === 0) return <p className="text-sm text-muted-foreground">{empty}</p>;
  return (
    <ul className="space-y-2 text-sm">
      {items.map((item, i) => (
        <li key={i} className="flex gap-2">
          <ArrowRight aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          <span>{item}</span>
        </li>
      ))}
    </ul>
  );
}

/**
 * "For you" lists only catches whose removal changes the tier, so an empty
 * list can mean no catch at all or catches that do not decide the tier.
 */
function forYouEmptyText(catches: HotelDetail['catches']): string {
  if (catches.length === 0) return 'No catch to set aside.';
  const names = joinAttributes(catches);
  return catches.length === 1
    ? `Setting aside the catch (${names}) would not change the tier.`
    : `Setting aside any one of the catches (${names}) would not change the tier.`;
}

function WhoItSuits({ lines, segments }: { lines: string[]; segments: SegmentOut[] }) {
  if (lines.length === 0 && segments.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No credible difference between kinds of guests (couples, families, groups, solo travellers,
        business trips): the evidence is too thin, or they agree.
      </p>
    );
  }
  return (
    <div className="space-y-4">
      {lines.length > 0 && <BulletList items={lines} empty="" />}
      {segments.length > 0 && (
        <Table containerLabel="Differences between kinds of guests (scrollable table)">
          <TableCaption>
            Shown only when we are at least 95% sure the difference is real. Values are the share of
            praise, or the complaint rate for noise and wifi.
          </TableCaption>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">Guests</TableHead>
              <TableHead scope="col">Attribute</TableHead>
              <TableHead scope="col">Compared with others</TableHead>
              <TableHead scope="col">This group</TableHead>
              <TableHead scope="col">Other guests</TableHead>
              <TableHead scope="col">Evidence</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {segments.map((segment, i) => (
              <TableRow key={i}>
                <TableCell className="font-sans capitalize">{segment.segment}</TableCell>
                <TableCell>{attributeName(segment.attribute)}</TableCell>
                <TableCell>
                  <span className="inline-flex items-center gap-1">
                    {segment.direction === 'better' ? (
                      <TrendingUp aria-hidden="true" className="size-4 text-strong" />
                    ) : (
                      <TrendingDown aria-hidden="true" className="size-4 text-weak" />
                    )}
                    {segment.direction === 'better' ? 'Happier' : 'Less happy'}
                  </span>
                </TableCell>
                <TableCell>{formatPercent(segment.segment_value)}</TableCell>
                <TableCell>{formatPercent(segment.others_value)}</TableCell>
                <TableCell>
                  {isWhole(segment.segment_size) ? '' : 'about '}
                  {plural(
                    segment.segment_size,
                    familyOf(segment.attribute) === 'must_be' ? 'review' : 'opinion',
                  )}
                  , {formatProbability(segment.probability)} sure
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}

function HotelView({ hotel }: { hotel: HotelDetail }) {
  const compare = useCompare();
  const tier = TIER_INFO[hotel.tier];
  const essentials = hotel.attributes.filter((a) => a.essential);
  const otherStrengths = hotel.highlights.filter((a) => !ESSENTIALS.includes(a));
  const firstAttribute = hotel.attributes[0]?.attribute;
  const p = hotel.provenance;

  return (
    <article className="space-y-8" aria-labelledby="hotel-name">
      <title>{`${hotel.name} | hotel-curator`}</title>
      <nav aria-label="Breadcrumb" className="text-sm">
        <ol className="flex flex-wrap items-center gap-1 text-muted-foreground">
          <li>
            <Link to="/">Explore</Link>
          </li>
          <li aria-hidden="true">/</li>
          <li aria-current="page" className="text-foreground">
            {hotel.name}
          </li>
        </ol>
      </nav>

      <header className="space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 space-y-1">
            <h1 id="hotel-name" className="text-2xl font-bold tracking-tight sm:text-3xl">
              {hotel.name}
            </h1>
            <p className="text-muted-foreground">
              {hotel.address ? `${hotel.address}, ` : ''}
              {hotel.city}, {hotel.country}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <CompareButton hotelId={hotel.hotel_id} name={hotel.name} size="default" />
            {compare.items.length >= 2 && compare.has(hotel.hotel_id) && (
              <Link
                to={compareHref(compare.items.map((i) => i.id))}
                className="text-sm font-medium"
              >
                Compare {compare.items.length} hotels
              </Link>
            )}
          </div>
        </div>
        <div className="space-y-3 rounded-lg border bg-card p-4">
          <div className="flex flex-wrap items-center gap-2">
            <TierBadge tier={hotel.tier} label={hotel.label} size="lg" />
            {isNotEnough(hotel.tier) && (
              <span className="text-sm text-muted-foreground">({hotel.label})</span>
            )}
          </div>
          <p className="text-lg leading-relaxed">{hotel.explanation.verdict}</p>
          <p className="text-sm text-muted-foreground">{tier.meaning}</p>
          <p className="text-sm text-muted-foreground">
            Based on {plural(hotel.reviews_eligible, 'usable review')} of{' '}
            {formatCount(hotel.reviews_total)} from {formatMonth(hotel.first_month)} to{' '}
            {formatMonth(hotel.last_month)}.
            <InfoTip label="What is a usable review?">
              Reviews used to train the classifier and near-duplicate reviews are left out, so no
              grade rests on the model&apos;s own training data or on copies.
            </InfoTip>
          </p>
          {(hotel.catches.length > 0 || otherStrengths.length > 0) && (
            <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
              {hotel.catches.length > 0 && (
                <p className="flex items-center gap-1.5">
                  <TriangleAlert aria-hidden="true" className="size-4 text-weak" />
                  <span className="font-medium">Catch:</span> {joinAttributes(hotel.catches)}
                </p>
              )}
              {otherStrengths.length > 0 && (
                <p>
                  <span className="font-medium">Also strong:</span> {joinStrengths(otherStrengths)}
                </p>
              )}
            </div>
          )}
        </div>
      </header>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader className="pb-2">
            <h2 className="text-lg font-semibold">The three essentials</h2>
          </CardHeader>
          <CardContent>
            <ul className="space-y-3 text-sm">
              {hotel.explanation.essentials.map((line, i) => {
                // Lines read "Cleanliness: Strong. ..."; match by name, fall back to order.
                const attribute =
                  essentials.find((a) => line.startsWith(`${a.display_name}:`)) ?? essentials[i];
                return (
                  <li key={i} className="space-y-1">
                    {attribute && (
                      <GradeBadge
                        grade={attribute.grade}
                        family={attribute.family}
                        lean={attribute.lean}
                      />
                    )}
                    <p>{line}</p>
                  </li>
                );
              })}
            </ul>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <h2 className="text-lg font-semibold">What would change it</h2>
          </CardHeader>
          <CardContent>
            <BulletList
              items={hotel.explanation.what_would_change}
              empty={
                hotel.tier === 'excellent'
                  ? 'Nothing to add: Excellent is the top tier.'
                  : 'No single change was identified.'
              }
            />
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <h2 className="text-lg font-semibold">For you</h2>
          </CardHeader>
          <CardContent>
            <BulletList items={hotel.explanation.for_you} empty={forYouEmptyText(hotel.catches)} />
          </CardContent>
        </Card>
      </div>

      <Section id="who-it-suits" title="Who it suits">
        <WhoItSuits lines={hotel.explanation.who_it_suits ?? []} segments={hotel.segments ?? []} />
      </Section>

      {(hotel.review_volume?.length ?? 0) > 0 && (
        <Section id="volume" title="Reviews over time">
          <p className="max-w-3xl text-sm text-muted-foreground">
            Usable reviews per month. Grades use all of them; a month with few reviews adds little
            to any grade.
          </p>
          <VolumeChart
            months={hotel.review_volume ?? []}
            title={`Usable reviews per month for ${hotel.name}`}
          />
        </Section>
      )}

      <Section id="attributes" title="All eight attributes">
        <p className="max-w-3xl text-sm text-muted-foreground">
          Each grade comes with what guests wrote (observed) and a 90% likely range: where the true
          value probably lies, given how many guests gave an opinion. A grade is only Strong or Weak
          when we are at least 95% sure.
        </p>
        <div className="space-y-6">
          <AttributeCharts attributes={hotel.attributes} />
          <AttributeTable attributes={hotel.attributes} />
        </div>
      </Section>

      <Section id="evidence" title="Evidence by attribute">
        <p className="flex max-w-3xl gap-2 text-sm text-muted-foreground">
          <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          <span>
            Quotes are whole review parts, shortened to 200 characters, with names and contact
            details removed. They show the month and traveler type only. The data publisher removed
            punctuation from the reviews, so quotes can read as run-on text.
          </span>
        </p>
        {firstAttribute && (
          <Tabs defaultValue={firstAttribute}>
            <TabsList aria-label="Attributes">
              {hotel.attributes.map((a) => (
                <TabsTab key={a.attribute} value={a.attribute}>
                  {a.display_name}
                </TabsTab>
              ))}
            </TabsList>
            {hotel.attributes.map((a) => (
              <TabsPanel key={a.attribute} value={a.attribute}>
                <AttributePanel attribute={a} />
              </TabsPanel>
            ))}
          </Tabs>
        )}
      </Section>

      <Section id="site-score" title="The booking site's own score">
        <div className="flex gap-3 rounded-lg border border-dashed p-4">
          <Info aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-muted-foreground" />
          <div className="space-y-1">
            <p>
              <span className="font-semibold">For reference, not used:</span>{' '}
              {hotel.site_average_score != null
                ? `the booking site's average score is ${hotel.site_average_score.toFixed(1)} out of 10.`
                : 'the booking site did not publish an average score for this hotel.'}
            </p>
            <p className="text-sm text-muted-foreground">
              The tier rules never read this score, so it stays an independent check.
            </p>
          </div>
        </div>
      </Section>

      <footer aria-labelledby="provenance" className="rounded-lg border bg-muted/40 p-4">
        <h2 id="provenance" className="mb-3 text-lg font-semibold">
          Where this comes from
        </h2>
        <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[max-content_1fr]">
          <dt className="font-medium">Reviews</dt>
          <dd className="text-muted-foreground">{p.data_period}</dd>
          <dt className="font-medium">Tier policy version</dt>
          <dd className="font-mono text-muted-foreground">{p.params_version}</dd>
          <dt className="font-medium">Model</dt>
          <dd className="font-mono break-all text-muted-foreground">{p.model}</dd>
          <dt className="font-medium">Data source</dt>
          <dd className="text-muted-foreground">{p.data_source}</dd>
          <dt className="font-medium">Licence status</dt>
          <dd className="text-muted-foreground">{p.data_licence}</dd>
          <dt className="font-medium">Corrections</dt>
          <dd className="text-muted-foreground">{p.corrections}</dd>
        </dl>
      </footer>
    </article>
  );
}

function NotFound({ error }: { error?: ApiError }) {
  return (
    <div className="space-y-4">
      <title>Hotel not found | hotel-curator</title>
      <h1 className="text-2xl font-bold">Hotel not found</h1>
      <p>There is no hotel with this address. It may have been removed, or the link is wrong.</p>
      {error?.requestId && (
        <p className="text-sm text-muted-foreground">
          Request id: <code className="font-mono">{error.requestId}</code>
        </p>
      )}
      <Link to="/">Back to the explorer</Link>
    </div>
  );
}

export function HotelPage() {
  const { hotelId = '' } = useParams();
  const valid = HOTEL_ID_PATTERN.test(hotelId);
  const hotel = useGetHotel(hotelId, { query: { enabled: valid } });

  if (!valid) return <NotFound />;
  if (hotel.isError) {
    if (hotel.error instanceof ApiError && hotel.error.status === 404) {
      return <NotFound error={hotel.error} />;
    }
    return (
      <div className="space-y-4">
        <title>Hotel | hotel-curator</title>
        <h1 className="text-2xl font-bold">Hotel</h1>
        <ErrorState
          error={hotel.error}
          title="This hotel could not be loaded"
          onRetry={() => void hotel.refetch()}
        />
      </div>
    );
  }
  if (hotel.isPending) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-bold">Loading hotel</h1>
        <LoadingBlock label="Loading the hotel's grades and evidence" lines={6} />
      </div>
    );
  }
  return <HotelView hotel={hotel.data} />;
}
