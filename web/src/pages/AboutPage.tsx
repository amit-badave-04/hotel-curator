import { ExternalLink } from 'lucide-react';

import { useGetMeta } from '@/api/generated/endpoints';
import type { Evaluation, Meta, Tier } from '@/api/generated/model';
import { AnalyzePanel } from '@/components/AnalyzePanel';
import { GradeBadge, TierBadge } from '@/components/badges';
import { IntervalChart } from '@/components/IntervalChart';
import { Section } from '@/components/Section';
import { StatTile } from '@/components/StatTile';
import { ErrorState, LoadingBlock } from '@/components/states';
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  ATTRIBUTES,
  formatCount,
  formatPercent,
  formatScore,
  formatSigned,
  isNotEnough,
  joinAttributes,
  TIERS,
} from '@/lib/domain';
import { formatPeriod, policyOf } from '@/lib/policy';

/** Tells the two "not enough" tiers apart; their badges read the same. */
function NotEnoughHint({ tier }: { tier: Tier }) {
  if (!isNotEnough(tier)) return null;
  return (
    <span className="ml-2 text-xs text-muted-foreground">
      {tier === 'not_enough_reviews' ? '(too few reviews)' : '(too few opinions)'}
    </span>
  );
}

/**
 * The rules, from the served policy or the pre-registered one until it
 * arrives. Rendered from the first paint, so links to #tiers and #grades
 * find their target.
 */
function Policy({ meta, pending }: { meta: Meta | undefined; pending: boolean }) {
  const p = policyOf(meta);
  const confirmation = p.confirmation;
  const interval = p.interval_level;
  const strong2 = p.two_sided.strong;
  const weak2 = p.two_sided.weak;
  const strongM = p.must_be_lines.strong;
  const weakM = p.must_be_lines.weak;
  const minOpinions = p.min_opinions;
  const minReviews = p.min_reviews;
  const minComplaining = p.min_complaining_reviews;
  const materiality = p.materiality;
  const months = p.excellent.min_months;
  const monthShare = p.excellent.max_month_share;
  const comparePr = p.compare.probability;
  const recent = p.trends.recent_months;
  const baseline = p.trends.baseline_months;
  const trendPr = p.trends.probability;
  const essentials = p.essentials;
  const mustBe = p.must_be;
  const praisePerComplaint = Math.round(strong2 / (1 - strong2));

  return (
    <>
      <Section id="tiers" title="How tiers work">
        <p>
          The essentials ({joinAttributes(essentials)}) decide the tier. A material weak spot
          elsewhere can only hold a hotel back, and strengths elsewhere never lift it; they are
          shown as highlights. The first rule that matches gives the tier:
        </p>
        <Table containerLabel="Tier rules (scrollable table)">
          <TableCaption>
            Policy version {p.params_version}. These values were fixed before any hotel was scored.
          </TableCaption>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">Tier</TableHead>
              <TableHead scope="col">Rule</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            <TableRow>
              <TableCell>
                <TierBadge tier="not_enough_reviews" />
                <NotEnoughHint tier="not_enough_reviews" />
              </TableCell>
              <TableCell>
                Fewer than {minReviews} usable reviews. Not a bad sign, just not enough to go on.
              </TableCell>
            </TableRow>
            <TableRow>
              <TableCell>
                <TierBadge tier="caution" />
              </TableCell>
              <TableCell>
                An essential is graded Weak, and is still Weak by the stricter test: every review
                counts as a full praise or complaint when the model gives it{' '}
                {formatPercent(p.hard_label_probability)} or more, and not at all below that,
                instead of by its probability. This rule comes before the next one, so too few
                opinions on one essential never hide a Weak one.
              </TableCell>
            </TableRow>
            <TableRow>
              <TableCell>
                <TierBadge tier="not_enough_evidence" />
                <NotEnoughHint tier="not_enough_evidence" />
              </TableCell>
              <TableCell>
                Fewer than {minOpinions} opinions on an essential. Not a bad sign either.
              </TableCell>
            </TableRow>
            <TableRow>
              <TableCell>
                <TierBadge tier="excellent" />
              </TableCell>
              <TableCell>
                All three essentials Strong, no material weak spot elsewhere, and reviews from at
                least {months} different months with no month holding more than{' '}
                {formatPercent(monthShare)} of them.
              </TableCell>
            </TableRow>
            <TableRow>
              <TableCell>
                <TierBadge tier="good" />
              </TableCell>
              <TableCell>
                At least two essentials Strong, no essential Weak, and at most one material weak
                spot elsewhere.
              </TableCell>
            </TableRow>
            <TableRow>
              <TableCell>
                <TierBadge tier="fair" />
              </TableCell>
              <TableCell>
                Everything else: fewer than two essentials Strong, an essential graded Weak that
                does not pass the stricter test for Caution, or two or more material weak spots
                elsewhere.
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
        <p id="usable-reviews">
          A <strong>usable review</strong> is one the grades can rely on. Reviews used to train the
          classifier are left out (its scores on them would be too confident), and so are
          near-duplicates: reviews of the same hotel whose text, of 10 or more words, repeats an
          earlier review. So no grade rests on the model&apos;s own training data or on copies.
        </p>
        <p>
          A <strong>catch</strong> is a material weak spot outside the essentials: graded Weak, and
          raised by at least {formatPercent(materiality)} of guests. It is named in the label, as in
          &quot;Good, but noisy&quot;. <strong>For you</strong> shows the tier with a catch set
          aside (&quot;if noise doesn&apos;t bother you&quot;), and{' '}
          <strong>what would change it</strong> names the condition between the hotel and the next
          tier up.
        </p>
        {pending && <LoadingBlock label="Loading the tier counts" lines={6} />}
        {meta && (
          <Table containerLabel="Tier counts (scrollable table)">
            <TableCaption>Hotels per tier in the data loaded now.</TableCaption>
            <TableHeader>
              <TableRow>
                <TableHead scope="col">Tier</TableHead>
                <TableHead scope="col">Hotels</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {TIERS.map((tier) => (
                <TableRow key={tier}>
                  <TableCell>
                    <TierBadge tier={tier} />
                    <NotEnoughHint tier={tier} />
                  </TableCell>
                  <TableCell>{formatCount(meta.tiers[tier] ?? 0)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Section>

      <Section id="grades" title="What the grades mean">
        <p>
          For {joinAttributes(ATTRIBUTES.filter((a) => !mustBe.includes(a)))} we measure the{' '}
          <strong>share of praise</strong> among guests who gave an opinion. Guests mostly mention{' '}
          {joinAttributes(mustBe)} only when something is wrong, so for those we measure the{' '}
          <strong>complaint rate</strong> among all guests. Strong and Weak need{' '}
          {formatPercent(confirmation)} certainty; the likely range shown everywhere is a{' '}
          {formatPercent(interval)} interval.
        </p>
        <Table containerLabel="Grade rules (scrollable table)">
          <TableHeader>
            <TableRow>
              <TableHead scope="col">Grade</TableHead>
              <TableHead scope="col">Share of praise</TableHead>
              <TableHead scope="col">Complaint rate ({joinAttributes(mustBe)})</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            <TableRow>
              <TableCell>
                <GradeBadge grade="strong" />
              </TableCell>
              <TableCell>
                At least {formatPercent(strong2)} praise (about {praisePerComplaint} praises per
                complaint)
              </TableCell>
              <TableCell>
                At most {formatPercent(strongM)} complained (1 guest in {Math.round(1 / strongM)})
              </TableCell>
            </TableRow>
            <TableRow>
              <TableCell>
                <GradeBadge grade="ok" />
              </TableCell>
              <TableCell colSpan={2}>No confirmed strength or weakness.</TableCell>
            </TableRow>
            <TableRow>
              <TableCell>
                <GradeBadge grade="weak" />
              </TableCell>
              <TableCell>
                Under {formatPercent(weak2)} praise (complaints outnumbered praise)
              </TableCell>
              <TableCell>
                At least {formatPercent(weakM)} complained (1 guest in {Math.round(1 / weakM)})
              </TableCell>
            </TableRow>
            <TableRow>
              <TableCell>
                <GradeBadge grade="too_few" />
              </TableCell>
              <TableCell>Fewer than {minOpinions} opinions</TableCell>
              <TableCell>Fewer than {minReviews} reviews</TableCell>
            </TableRow>
          </TableBody>
        </Table>
        <p>
          A Weak grade also needs at least {minComplaining} different complaining reviews, so no
          warning rests on one or two guests. An OK grade whose estimate is already past a line is
          shown as &quot;leaning strong&quot; or &quot;leaning weak&quot;. Trends compare the last{' '}
          {recent} months of data with the {baseline} months before, and flag a change only when it
          is at least {formatPercent(trendPr)} likely. In a comparison, a hotel is highlighted as
          the leader on an attribute only when it is better than all the others with at least{' '}
          {formatPercent(comparePr)} certainty.
        </p>
      </Section>
    </>
  );
}

interface Rung {
  name: string;
  family: string;
  value: number;
  low: number;
  high: number;
}

function parseRungs(evaluation: Evaluation): Rung[] {
  const rungs: Rung[] = [];
  for (const raw of evaluation.rungs) {
    const name = raw.name;
    const value = raw.test_macro_f1;
    const low = raw.low;
    const high = raw.high;
    if (
      typeof name === 'string' &&
      typeof value === 'number' &&
      typeof low === 'number' &&
      typeof high === 'number'
    ) {
      rungs.push({
        name,
        family: typeof raw.family === 'string' ? raw.family : '',
        value,
        low,
        high,
      });
    }
  }
  return rungs;
}

/**
 * The served model's row in the ladder, when it shows other numbers than the
 * headline: the headline is the served ONNX package's own score, the ladder
 * row its checkpoint's (they differ slightly, e.g. with int8 weights).
 */
function servedCheckpoint(evaluation: Evaluation, rungs: Rung[]): Rung | undefined {
  const rung = rungs.find((r) => r.name === evaluation.model);
  if (!rung) return undefined;
  const headline = evaluation.macro_f1;
  const pairs: [number, number][] = [
    [rung.value, headline.point],
    [rung.low, headline.low],
    [rung.high, headline.high],
  ];
  return pairs.every(([a, b]) => formatScore(a) === formatScore(b)) ? undefined : rung;
}

function EvaluationView({ evaluation }: { evaluation: Evaluation }) {
  const rungs = parseRungs(evaluation);
  const diff = evaluation.difference;
  const better = diff.low > 0;
  const checkpoint = servedCheckpoint(evaluation, rungs);
  const servedMark = checkpoint ? '(served, checkpoint)' : '(served)';
  const rungLabel = (rung: Rung) =>
    rung.name === evaluation.model
      ? `${rung.name} ${servedMark}`
      : rung.name === evaluation.v1_configuration
        ? `${rung.name} (v1)`
        : rung.name;
  return (
    <div className="space-y-4">
      <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile
          label="Served model, macro-F1"
          value={formatScore(evaluation.macro_f1.point)}
          detail={`95% interval ${formatScore(evaluation.macro_f1.low)} to ${formatScore(evaluation.macro_f1.high)}`}
        />
        <StatTile
          label="v1, macro-F1"
          value={formatScore(evaluation.v1_macro_f1.point)}
          detail={`95% interval ${formatScore(evaluation.v1_macro_f1.low)} to ${formatScore(evaluation.v1_macro_f1.high)}`}
        />
        <StatTile
          label="Difference (served minus v1)"
          value={formatSigned(diff.point)}
          detail={`95% interval ${formatSigned(diff.low)} to ${formatSigned(diff.high)}; ${
            better ? 'credibly better than v1' : 'not credibly better than v1'
          }`}
        />
        <StatTile
          label="Mean calibration error (ECE)"
          value={formatScore(evaluation.mean_review_ece)}
          detail="Lower is better; at most 0.050 was required"
        />
      </dl>
      <p className="text-sm text-muted-foreground">
        Served model: <code className="font-mono">{evaluation.model}</code>. v1 configuration:{' '}
        <code className="font-mono">{evaluation.v1_configuration}</code>. Macro-F1 is the average F1
        over the 16 labels on the held-out test reviews
        {evaluation.test_reviews != null
          ? ` (${formatCount(evaluation.test_reviews)}${
              evaluation.test_hotels != null
                ? ` from ${formatCount(evaluation.test_hotels)} hotels`
                : ''
            })`
          : ''}
        ; intervals come from a bootstrap that resamples hotels.
      </p>
      {checkpoint && (
        <p className="text-sm text-muted-foreground">
          The served model is an ONNX package built from the{' '}
          <code className="font-mono">{checkpoint.name}</code> checkpoint. The served model&apos;s
          numbers above (macro-F1, the difference from v1 and the calibration error) are the
          package&apos;s own. The chart and table below show each model&apos;s checkpoint, so the
          served model&apos;s row there reads {formatScore(checkpoint.value)} (95% interval{' '}
          {formatScore(checkpoint.low)} to {formatScore(checkpoint.high)}).
        </p>
      )}
      {rungs.length > 0 && (
        <>
          <figure className="space-y-2 rounded-lg border bg-card p-4">
            <figcaption>
              <h3 className="font-semibold">All models tried, macro-F1 on the test set</h3>
              <p className="text-sm text-muted-foreground">
                With 95% intervals. The served model is drawn in orange and marked{' '}
                {`"${servedMark}"`}.
              </p>
            </figcaption>
            <IntervalChart
              rows={rungs.map((rung) => ({
                key: rung.name,
                label: rungLabel(rung),
                value: rung.value,
                low: rung.low,
                high: rung.high,
                emphasis: rung.name === evaluation.model,
              }))}
              domain={[0, 1]}
              format={(v) => formatScore(v, 2)}
              valueName="Macro-F1"
              rangeName="95% interval"
              emphasisName={checkpoint ? 'Served model (checkpoint)' : 'Served model'}
              title="Macro-F1 by model"
              description="Macro-F1 with 95% intervals for every model tried; the table below lists the same numbers."
              labelWidth={170}
            />
          </figure>
          <Table containerLabel="Models tried (scrollable table)">
            <TableCaption>
              Every model tried, scored on the same test reviews. Names ending in -train2 were
              trained on both labelled training sets, the other new models on the first set only;
              the v1 family is the earlier project&apos;s model.
            </TableCaption>
            <TableHeader>
              <TableRow>
                <TableHead scope="col">Model</TableHead>
                <TableHead scope="col">Family</TableHead>
                <TableHead scope="col">Macro-F1</TableHead>
                <TableHead scope="col">95% interval</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rungs.map((rung) => (
                <TableRow key={rung.name}>
                  <TableHead
                    scope="row"
                    className="h-auto align-top text-sm font-medium tracking-normal text-foreground normal-case"
                  >
                    {rungLabel(rung)}
                  </TableHead>
                  <TableCell>{rung.family || 'n/a'}</TableCell>
                  <TableCell>{formatScore(rung.value)}</TableCell>
                  <TableCell>
                    {formatScore(rung.low)} to {formatScore(rung.high)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </>
      )}
    </div>
  );
}

export function AboutPage() {
  const meta = useGetMeta();
  const period = formatPeriod(meta.data?.data_period);

  return (
    <div className="max-w-4xl space-y-10">
      <title>About the model | hotel-curator</title>
      <div className="space-y-3">
        <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">How hotel-curator works</h1>
        <p className="text-lg">
          hotel-curator reads what guests wrote and grades each hotel on eight attributes, with how
          sure we are and, wherever a review is clear enough to cite, quotes behind each grade. The
          grades add up to a tier.
        </p>
        <p className="text-muted-foreground">
          {period
            ? `The reviews date from ${period}`
            : 'The reviews cover a fixed period in the past'}
          {meta.data ? ` and cover ${formatCount(meta.data.hotels)} hotels` : ''}. A tier describes
          what guests wrote then, not how a hotel is today.
        </p>
      </div>

      <Section id="labels" title="How the labels were made">
        <p>
          Each review on the booking site has two parts: what the guest liked and what they
          disliked. Every part is labelled for eight attributes (cleanliness, room, staff, location,
          noise, wifi, value, breakfast and food), each as praise or as a complaint: 16 labels in
          all.
        </p>
        <p>
          The labels used for training and testing are machine-made: automated annotators (large
          language models) applied a written guideline, with three annotators and a majority vote
          for the development and test sets. Their agreement with people was checked on a separate
          set of hotel reviews labelled by human annotators. So the labels are checked against
          people, but no person read every review.
        </p>
      </Section>

      <Section id="ladder" title="The model ladder">
        <p>
          Several models, from simple to complex, were scored the same way on the same test reviews:
          a word-count baseline with logistic regression, the same features with gradient-boosted
          trees, ready-made sentence embeddings with simple classifiers, and fine-tuned transformer
          encoders of different sizes. They were not all trained on the same data: models whose name
          ends in <code className="font-mono">-train2</code> were trained on both labelled training
          sets (8,006 reviews), the others on the first set only (4,006 reviews). The first version
          of this project (v1) was scored on the same test reviews as the target to beat.
        </p>
        <p>The rule for shipping a model was written before any result was seen:</p>
        <ol className="list-decimal space-y-1 pl-6">
          <li>
            Its macro-F1 on the held-out test reviews beats v1, and the 95% interval of the
            difference lies above zero.
          </li>
          <li>
            Among those, it has the best score on the development set (ties go to the smaller
            model).
          </li>
          <li>Its mean calibration error is at most 0.05.</li>
          <li>It can label the whole corpus on a 4-core machine in under 4 hours.</li>
        </ol>
        <p>
          The model&apos;s probabilities are calibrated on the development set, so a label given at
          80% is right about 8 times in 10. Hotel grades add up these probabilities rather than
          yes/no labels.
        </p>
      </Section>

      {meta.isError ? (
        <ErrorState
          error={meta.error}
          title="The tier policy could not be loaded"
          onRetry={() => void meta.refetch()}
        >
          <p className="text-sm">The rules below use the pre-registered default values.</p>
        </ErrorState>
      ) : null}
      <Policy meta={meta.data} pending={meta.isPending} />

      <Section id="limits" title="Limits and caveats">
        <ul className="list-disc space-y-2 pl-6">
          <li>
            <strong>Machine-made labels.</strong> The training and test labels come from automated
            annotators checked against people, not from people reading every review. Their mistakes
            carry into the model.
          </li>
          <li>
            <strong>Old data.</strong>{' '}
            {period ? `Reviews are from ${period}.` : 'Reviews cover a fixed period in the past.'}{' '}
            Hotels change; the product never claims current quality.
          </li>
          <li>
            <strong>Licence unconfirmed.</strong> The reviews were published as CC0 by a third party
            but are originally Booking.com content. Legal review is needed before any commercial
            use.
          </li>
          <li>
            <strong>Intervals are somewhat too narrow.</strong> Probabilities are treated as
            observations, which understates uncertainty a little.
          </li>
          <li>
            <strong>Text quality.</strong> The data publisher removed punctuation, and only the text
            guests wrote is used: the site&apos;s own scores are never read by the tier rules.
          </li>
        </ul>
      </Section>

      <Section id="evaluation" title="Evaluation">
        {meta.isPending ? (
          <LoadingBlock label="Loading the evaluation" lines={3} />
        ) : meta.data?.evaluation ? (
          <EvaluationView evaluation={meta.data.evaluation} />
        ) : (
          <p className="rounded-lg border border-dashed p-4">
            <strong>Evaluation not available.</strong> This server has no promotion report for the
            served model yet, so no test-set numbers are shown.
            {meta.data?.model ? (
              <>
                {' '}
                Scores were computed with model <code className="font-mono">{meta.data.model}</code>
                .
              </>
            ) : null}
          </p>
        )}
      </Section>

      <Section id="try" title="Try the model">
        <AnalyzePanel available={meta.data?.analyze_available} checking={meta.isPending} />
      </Section>

      <Section id="developers" title="For developers">
        <p>
          Everything on these pages comes from a REST API with typed, documented endpoints: search,
          hotel details, compare, cities, metadata and review analysis. Errors share one shape with
          a request id, and requests are rate limited.
        </p>
        <p>
          <a href="/api/docs" className="inline-flex items-center gap-1 font-medium">
            API documentation (OpenAPI)
            <ExternalLink aria-hidden="true" className="size-4" />
          </a>
        </p>
      </Section>
    </div>
  );
}
