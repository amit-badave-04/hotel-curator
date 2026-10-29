import { ArrowRight, BookOpenCheck, Scale, ScanText } from 'lucide-react';
import { useEffect, type ReactNode } from 'react';
import { Link, Navigate, useLocation } from 'react-router';

import { useGetMeta, useListCities } from '@/api/generated/endpoints';
import type { Evaluation, Meta, Tier } from '@/api/generated/model';
import { AnalyzePanel } from '@/components/AnalyzePanel';
import { GradeBadge, TierBadge } from '@/components/badges';
import { Section } from '@/components/Section';
import { StatTile } from '@/components/StatTile';
import { ErrorState } from '@/components/states';
import { buttonVariants } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import {
  formatCount,
  formatPercent,
  formatScore,
  formatSigned,
  joinAttributes,
  NOT_ENOUGH_TIERS,
  plural,
} from '@/lib/domain';
import { DEFAULT_FILTERS, EXPLORER_PATH, explorerHref, legacyExplorerTarget } from '@/lib/filters';
import { formatPeriod, policyOf } from '@/lib/policy';
import { ISSUES_URL, NOTICE_PATH } from '@/lib/site';
import { cn } from '@/lib/utils';

/** How a result reads, drawn with the real badges. Not a real hotel. */
function ExampleResult() {
  const grades = [
    ['Cleanliness', 'cleanliness', 'strong'],
    ['Room', 'room', 'strong'],
    ['Staff', 'staff', 'strong'],
    ['Noise', 'noise', 'weak'],
  ] as const;
  return (
    <figure
      aria-labelledby="example-caption"
      className="space-y-4 rounded-xl border bg-card p-5 shadow-sm"
    >
      <figcaption
        id="example-caption"
        className="text-xs font-semibold tracking-wider text-muted-foreground uppercase"
      >
        Example of a result (not a real hotel)
      </figcaption>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="text-lg font-semibold">A hotel by the river</p>
        <TierBadge tier="good" label="Good, but noisy" size="lg" />
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
        {grades.map(([name, attribute, grade]) => (
          <div key={attribute} className="flex flex-col items-start gap-1">
            <dt className="text-muted-foreground">{name}</dt>
            <dd>
              <GradeBadge grade={grade} attribute={attribute} />
            </dd>
          </div>
        ))}
      </dl>
      <p className="text-sm">
        Guests confirmed cleanliness, room and staff as strengths; complaints about noise hold it
        back.
      </p>
    </figure>
  );
}

function Hero({ period }: { period: string | null }) {
  return (
    <section aria-labelledby="landing-title" className="landing-hero">
      <div className="grid items-center gap-10 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <div className="space-y-5">
          <p className="text-sm font-semibold tracking-wider text-primary uppercase">
            Guest reviews, graded with evidence
          </p>
          <h1
            id="landing-title"
            className="text-4xl font-bold tracking-tight text-balance sm:text-5xl"
          >
            Hotel tiers you can check against what guests wrote
          </h1>
          <p className="max-w-prose text-lg text-muted-foreground">
            hotel-curator reads guest reviews of European hotels, grades eight things guests care
            about, and calls one a strength or a weakness only when the reviews make it clear. Every
            grade shows how sure it is, with quotes from the reviews wherever one is clear enough to
            cite.
          </p>
          <div className="flex flex-wrap gap-3">
            <Link to={EXPLORER_PATH} className={cn(buttonVariants({ size: 'lg' }), 'no-underline')}>
              Explore hotels
              <ArrowRight aria-hidden="true" />
            </Link>
            <Link
              to="#try"
              className={cn(buttonVariants({ size: 'lg', variant: 'outline' }), 'no-underline')}
            >
              Try the model
            </Link>
          </div>
          <p className="text-sm text-muted-foreground">
            {/* Space kept for the period, so nothing moves when it arrives. */}
            <span className="block min-h-5">{period ? `Reviews from ${period}.` : ''}</span>A tier
            describes what guests wrote then, not how a hotel is today.
          </p>
        </div>
        <ExampleResult />
      </div>
    </section>
  );
}

interface StepProps {
  icon: typeof ScanText;
  title: string;
  to: string;
  linkText: string;
  children: ReactNode;
}

function Step({ icon: Icon, title, to, linkText, children }: StepProps) {
  return (
    <li className="flex flex-col gap-3 rounded-lg border bg-card p-5">
      <Icon aria-hidden="true" className="size-6 text-primary" />
      <h3 className="font-semibold">{title}</h3>
      <p className="text-sm">{children}</p>
      <Link to={to} className="mt-auto text-sm font-medium">
        {linkText}
      </Link>
    </li>
  );
}

function HowItWorks({ meta }: { meta: Meta | undefined }) {
  const policy = policyOf(meta);
  const confirmation = formatPercent(policy.confirmation);
  const essentials = joinAttributes(policy.essentials);
  return (
    <Section id="how" title="How it works, in three steps">
      <ol className="grid gap-4 md:grid-cols-3">
        <Step
          icon={ScanText}
          title="Read every review"
          to="/about#labels"
          linkText="How the labels were made"
        >
          A classifier labels what each guest liked and disliked: eight attributes, each as praise
          or a complaint, with a calibrated probability.
        </Step>
        <Step
          icon={Scale}
          title="Grade only what is clear"
          to="/about#grades"
          linkText="What the grades mean"
        >
          An attribute is graded Strong or Weak only when the reviews make it at least{' '}
          {confirmation} sure. Too few opinions means no grade, never a bad one.
        </Step>
        <Step
          icon={BookOpenCheck}
          title="Tier from the essentials"
          to="/about#tiers"
          linkText="How tiers work"
        >
          The essentials ({essentials}) decide the tier. A weak spot elsewhere can only hold a hotel
          back, and it is named in the label, as in &quot;Good, but noisy&quot;.
        </Step>
      </ol>
    </Section>
  );
}

function interval(low: number, high: number, format: (v: number) => string = formatScore) {
  return `95% interval ${format(low)} to ${format(high)}`;
}

/** "1,200 held-out test reviews from 182 hotels", as far as the report says. */
function testSet(evaluation: Evaluation): string {
  const { test_reviews: reviews, test_hotels: hotels } = evaluation;
  if (reviews == null) return 'held-out test reviews';
  const from = hotels == null ? '' : ` from ${plural(hotels, 'hotel')}`;
  return `${plural(reviews, 'held-out test review')}${from}`;
}

function ModelStats({ evaluation }: { evaluation: Evaluation }) {
  const diff = evaluation.difference;
  return (
    <>
      <StatTile
        label="Model score (macro-F1)"
        value={formatScore(evaluation.macro_f1.point)}
        detail={`Average F1 over the 16 labels; ${interval(evaluation.macro_f1.low, evaluation.macro_f1.high)}, on ${testSet(evaluation)}`}
      />
      <StatTile
        label="Gain over the first version"
        value={formatSigned(diff.point)}
        detail={`${interval(diff.low, diff.high, formatSigned)}; v1 scored ${formatScore(evaluation.v1_macro_f1.point)}`}
      />
      <StatTile
        label="Calibration error"
        value={formatScore(evaluation.mean_review_ece)}
        detail="Lower is better; at most 0.050 was required"
      />
    </>
  );
}

function HeadlineStats({ meta }: { meta: Meta }) {
  const cities = useListCities();
  const names = cities.data?.map((c) => c.city) ?? [];
  const where =
    names.length > 1
      ? `In ${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
      : names.length === 1
        ? `In ${names[0]}`
        : 'From guest reviews';
  return (
    <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <StatTile label="Hotels graded" value={formatCount(meta.hotels)} detail={where} />
      {meta.evaluation && <ModelStats evaluation={meta.evaluation} />}
    </dl>
  );
}

interface TierRow {
  key: string;
  tier: Tier;
  tiers: readonly Tier[];
  note?: string;
}

const TIER_ROWS: readonly TierRow[] = [
  { key: 'excellent', tier: 'excellent', tiers: ['excellent'] },
  { key: 'good', tier: 'good', tiers: ['good'] },
  { key: 'fair', tier: 'fair', tiers: ['fair'] },
  { key: 'caution', tier: 'caution', tiers: ['caution'] },
  {
    key: 'not-enough',
    tier: 'not_enough_evidence',
    tiers: NOT_ENOUGH_TIERS,
    note: 'too few reviews or opinions',
  },
];

/** Hotels per tier as a table; the bars only repeat the counts. */
function TierDistribution({ meta }: { meta: Meta }) {
  const rows = TIER_ROWS.map((row) => ({
    ...row,
    count: row.tiers.reduce((sum, tier) => sum + (meta.tiers[tier] ?? 0), 0),
  }));
  const largest = Math.max(1, ...rows.map((row) => row.count));
  return (
    <table className="w-full text-sm">
      <caption className="pb-2 text-left font-semibold">
        How the {formatCount(meta.hotels)} hotels are graded
      </caption>
      <thead className="sr-only">
        <tr>
          <th scope="col">Tier</th>
          <th scope="col">Hotels</th>
          <th scope="col">Share of all hotels</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.key} className="border-b last:border-0">
            <th scope="row" className="w-2/5 py-2 pr-3 text-left align-middle font-normal">
              <TierBadge tier={row.tier} />
              {row.note && (
                <span className="block text-xs text-muted-foreground">({row.note})</span>
              )}
            </th>
            <td className="w-16 py-2 pr-3 text-right align-middle font-semibold tabular-nums">
              {formatCount(row.count)}
            </td>
            <td className="py-2 align-middle">
              <div className="flex items-center gap-2">
                <span
                  aria-hidden="true"
                  className="h-2.5 rounded-r bg-(--chart-primary)"
                  style={{ width: `${(row.count / largest) * 70}%` }}
                />
                <span className="text-muted-foreground tabular-nums">
                  {formatPercent(meta.hotels > 0 ? row.count / meta.hotels : 0)}
                </span>
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ExcellentNote({ meta }: { meta: Meta }) {
  const excellent = meta.tiers.excellent ?? 0;
  const good = meta.tiers.good ?? 0;
  const { essentials, excellent: gate, confirmation } = meta.policy;
  const months = gate.min_months;
  const sure = formatPercent(confirmation);
  const spread = `reviews from at least ${months} different months, with no month holding more than ${formatPercent(gate.max_month_share)} of them`;
  const rule = `${joinAttributes(essentials)} all confirmed Strong (at least ${sure} sure), no material weak spot elsewhere, and ${spread}`;
  const tierLink = (tier: Tier) => explorerHref({ ...DEFAULT_FILTERS, tiers: [tier] });
  if (excellent > 0) {
    return (
      <Section id="excellent" title="What it takes to be Excellent">
        <p>
          {plural(excellent, 'hotel')} of {formatCount(meta.hotels)}{' '}
          {excellent === 1 ? 'is' : 'are'} rated Excellent. That needs {rule}.
        </p>
        <p>
          <Link to={tierLink('excellent')}>See the Excellent hotels</Link>
        </p>
      </Section>
    );
  }
  return (
    <Section id="excellent" title="Why no hotel is Excellent">
      <p>
        None of the {formatCount(meta.hotels)} hotels is rated Excellent. That tier needs {rule}. No
        hotel in this data meets every condition, and the rules were fixed before any hotel was
        scored, so none was moved up to fill the tier.
      </p>
      {good > 0 && (
        <p>
          The closest are the <Link to={tierLink('good')}>{plural(good, 'Good hotel')}</Link>.
        </p>
      )}
    </Section>
  );
}

function NumbersLoading() {
  return (
    <div role="status" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <span className="sr-only">Loading the numbers</span>
      {Array.from({ length: 4 }, (_, i) => (
        <Skeleton key={i} className="h-28 w-full" />
      ))}
    </div>
  );
}

function Numbers() {
  const meta = useGetMeta();
  const data = meta.data;
  return (
    <>
      <Section id="numbers" title="The numbers">
        {meta.isPending ? (
          <NumbersLoading />
        ) : meta.isError || !data ? (
          <ErrorState
            error={meta.error}
            title="The numbers could not be loaded"
            onRetry={() => void meta.refetch()}
          />
        ) : (
          <div className="space-y-6">
            <HeadlineStats meta={data} />
            {!data.evaluation && (
              <p className="rounded-lg border border-dashed p-4 text-sm">
                The model&apos;s test scores are not published on this server, so none are shown.
              </p>
            )}
            <TierDistribution meta={data} />
          </div>
        )}
      </Section>
      {data && <ExcellentNote meta={data} />}
    </>
  );
}

function Question({ question, children }: { question: string; children: ReactNode }) {
  return (
    <details className="landing-faq group rounded-lg border bg-card">
      <summary className="flex min-h-12 cursor-pointer items-center justify-between gap-3 px-4 py-3 font-medium">
        {question}
        <span aria-hidden="true" className="text-muted-foreground group-open:rotate-45">
          +
        </span>
      </summary>
      <div className="space-y-2 px-4 pb-4 text-sm">{children}</div>
    </details>
  );
}

function Faq({ period }: { period: string | null }) {
  return (
    <Section id="faq" title="Questions">
      <div className="space-y-2">
        <Question question="Is this how the hotels are today?">
          <p>
            No.{' '}
            {period
              ? `The reviews date from ${period}.`
              : 'The reviews cover a fixed period in the past.'}{' '}
            A tier describes what guests wrote then, not how a hotel is today.
          </p>
        </Question>
        <Question question="Where do the reviews come from?">
          <p>
            From a public dataset of Booking.com reviews of European hotels, published under CC0 by
            a third party who says the content was originally Booking.com&apos;s. That could not be
            confirmed first-hand, so this is a non-commercial demo, and legal review would be needed
            before any commercial use. The <a href={NOTICE_PATH}>attribution notice</a> lists every
            source.
          </p>
        </Question>
        <Question question='What does "Not enough evidence to judge" mean?'>
          <p>
            The hotel has too few usable reviews, or too few guests gave an opinion on one of the
            essentials. It is not a bad sign, just not enough to go on.
          </p>
        </Question>
        <Question question="Who labelled the reviews the model learned from?">
          <p>
            Automated annotators applied a written guideline, and their agreement with people was
            checked on reviews labelled by human annotators. No person read every review;{' '}
            <Link to="/about#labels">the About page</Link> has the details and the limits.
          </p>
        </Question>
        <Question question='Is the text I type into "Try the model" kept?'>
          <p>It is labelled and then dropped: not stored, and not sent back.</p>
        </Question>
        <Question question="Is there an API?">
          <p>
            Yes. Everything on the site comes from a documented REST API:{' '}
            <a href="/api/docs">API documentation</a>.
          </p>
        </Question>
        <Question question="Found a mistake, or want a review excerpt removed?">
          <p>
            Please <a href={ISSUES_URL}>open an issue on GitHub</a>. Corrections and removal
            requests are handled there.
          </p>
        </Question>
      </div>
    </Section>
  );
}

/**
 * After a jump to #try (the hero's link, or a shared link), keyboard focus
 * goes to that section, so the next Tab reaches the form. The router has
 * already scrolled there.
 */
function useFocusOnTry() {
  const { hash, key } = useLocation();
  useEffect(() => {
    if (hash === '#try') document.getElementById('try')?.focus({ preventScroll: true });
  }, [hash, key]);
}

function Landing() {
  const meta = useGetMeta();
  const period = formatPeriod(meta.data?.data_period);
  useFocusOnTry();
  return (
    <div className="space-y-14 sm:space-y-16">
      <title>hotel-curator: hotel quality tiers from guest reviews</title>
      <Hero period={period} />
      <HowItWorks meta={meta.data} />
      <Numbers />
      <Section id="try" title="Try the model" focusable>
        <p className="text-muted-foreground">
          Write a short review and see which attributes the model finds, and how sure it is.
        </p>
        <AnalyzePanel available={meta.data?.analyze_available} checking={meta.isPending} />
      </Section>
      <Faq period={period} />
    </div>
  );
}

/**
 * The home page. Explorer links from before the explorer moved to
 * `/explore` (`/?city=Paris`) are sent on, with their filters.
 */
export function LandingPage() {
  const { search } = useLocation();
  const target = legacyExplorerTarget(search);
  return target ? <Navigate to={target} replace /> : <Landing />;
}
