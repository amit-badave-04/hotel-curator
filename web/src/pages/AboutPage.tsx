import { ExternalLink, ThumbsDown, ThumbsUp } from 'lucide-react';
import { useId, useRef, useState, type FormEvent } from 'react';

import { ApiError } from '@/api/fetcher';
import { useAnalyzeReview, useGetMeta } from '@/api/generated/endpoints';
import type { AnalyzeResponse, Attribute, Evaluation, Meta, Tier } from '@/api/generated/model';
import { GradeBadge, TierBadge } from '@/components/badges';
import { IntervalChart } from '@/components/IntervalChart';
import { Section } from '@/components/Section';
import { ErrorState, LoadingBlock } from '@/components/states';
import { Button } from '@/components/ui/button';
import { Label, Textarea } from '@/components/ui/input';
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
  attributeName,
  formatCount,
  formatPercent,
  formatProbability,
  formatScore,
  formatSigned,
  isNotEnough,
  joinAttributes,
  MAX_PART_CHARS,
  TIERS,
} from '@/lib/domain';

/** Reads a number from the free-form policy object, with a fallback. */
function policyNumber(
  policy: Meta['policy'] | undefined,
  path: string[],
  fallback: number,
): number {
  let node: unknown = policy;
  for (const key of path) {
    if (typeof node !== 'object' || node === null) return fallback;
    node = (node as Record<string, unknown>)[key];
  }
  return typeof node === 'number' && Number.isFinite(node) ? node : fallback;
}

function policyAttributes(policy: Meta['policy'] | undefined, key: string, fallback: Attribute[]) {
  const value = policy?.[key];
  if (!Array.isArray(value)) return fallback;
  const attributes = value.filter((v): v is Attribute => ATTRIBUTES.includes(v as Attribute));
  return attributes.length > 0 ? attributes : fallback;
}

/** Tells the two "not enough" tiers apart; their badges read the same. */
function NotEnoughHint({ tier }: { tier: Tier }) {
  if (!isNotEnough(tier)) return null;
  return (
    <span className="ml-2 text-xs text-muted-foreground">
      {tier === 'not_enough_reviews' ? '(too few reviews)' : '(too few opinions)'}
    </span>
  );
}

function Policy({ meta }: { meta: Meta | undefined }) {
  const p = meta?.policy;
  const n = (path: string[], fallback: number) => policyNumber(p, path, fallback);
  const confirmation = n(['confirmation'], 0.95);
  const interval = n(['interval_level'], 0.9);
  const strong2 = n(['two_sided', 'strong'], 0.8);
  const weak2 = n(['two_sided', 'weak'], 0.5);
  const strongM = n(['must_be_lines', 'strong'], 0.05);
  const weakM = n(['must_be_lines', 'weak'], 0.1);
  const minOpinions = n(['min_opinions'], 10);
  const minReviews = n(['min_reviews'], 30);
  const minComplaining = n(['min_complaining_reviews'], 3);
  const materiality = n(['materiality'], 0.05);
  const months = n(['excellent', 'min_months'], 6);
  const monthShare = n(['excellent', 'max_month_share'], 0.25);
  const comparePr = n(['compare', 'probability'], 0.95);
  const recent = n(['trends', 'recent_months'], 6);
  const baseline = n(['trends', 'baseline_months'], 18);
  const trendPr = n(['trends', 'probability'], 0.9);
  const essentials = policyAttributes(p, 'essentials', ['cleanliness', 'room', 'staff']);
  const mustBe = policyAttributes(p, 'must_be', ['noise', 'wifi']);
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
            Policy version {meta?.params_version ?? 'unknown'}. These values were fixed before any
            hotel was scored.
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
                counts as a full praise or complaint when the model gives it 50% or more, and not at
                all below that, instead of by its probability. This rule comes before the next one,
                so too few opinions on one essential never hide a Weak one.
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

function Stat({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="rounded-lg border bg-card p-4">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="mt-1 text-2xl font-semibold">{value}</dd>
      <dd className="text-sm text-muted-foreground">{detail}</dd>
    </div>
  );
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
        <Stat
          label="Served model, macro-F1"
          value={formatScore(evaluation.macro_f1.point)}
          detail={`95% interval ${formatScore(evaluation.macro_f1.low)} to ${formatScore(evaluation.macro_f1.high)}`}
        />
        <Stat
          label="v1, macro-F1"
          value={formatScore(evaluation.v1_macro_f1.point)}
          detail={`95% interval ${formatScore(evaluation.v1_macro_f1.low)} to ${formatScore(evaluation.v1_macro_f1.high)}`}
        />
        <Stat
          label="Difference (served minus v1)"
          value={formatSigned(diff.point)}
          detail={`95% interval ${formatSigned(diff.low)} to ${formatSigned(diff.high)}; ${
            better ? 'credibly better than v1' : 'not credibly better than v1'
          }`}
        />
        <Stat
          label="Mean calibration error (ECE)"
          value={formatScore(evaluation.mean_review_ece)}
          detail="Lower is better; at most 0.050 was required"
        />
      </dl>
      <p className="text-sm text-muted-foreground">
        Served model: <code className="font-mono">{evaluation.model}</code>. v1 configuration:{' '}
        <code className="font-mono">{evaluation.v1_configuration}</code>. Macro-F1 is the average F1
        over the 16 labels on the held-out test reviews; intervals come from a bootstrap that
        resamples hotels.
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

const POLARITY = {
  positive: { name: 'Praise', icon: ThumbsUp, className: 'text-strong' },
  negative: { name: 'Complaint', icon: ThumbsDown, className: 'text-weak' },
} as const;

function AnalyzeResult({ result }: { result: AnalyzeResponse }) {
  return (
    <div className="space-y-4">
      <h3 className="font-semibold">What the model found</h3>
      {result.parts.length === 0 && <p>No text was analysed.</p>}
      {result.parts.map((part) => (
        <div key={part.part} className="space-y-2">
          <h4 className="text-sm font-semibold">
            {part.part === 'liked' ? 'Liked text' : 'Disliked text'}{' '}
            <span className="font-normal text-muted-foreground">
              ({formatCount(part.characters)} characters)
            </span>
          </h4>
          {part.labels.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No attribute was detected with enough confidence.
            </p>
          ) : (
            <ul className="space-y-1">
              {part.labels.map((label) => {
                const polarity =
                  label.polarity === 'negative' ? POLARITY.negative : POLARITY.positive;
                const Icon = polarity.icon;
                const name = ATTRIBUTES.includes(label.attribute as Attribute)
                  ? attributeName(label.attribute as Attribute)
                  : label.attribute;
                return (
                  <li
                    key={label.label}
                    className="flex flex-wrap items-center gap-2 rounded-md border bg-background px-3 py-2 text-sm"
                  >
                    <Icon aria-hidden="true" className={`size-4 ${polarity.className}`} />
                    <span className="font-medium">{name}</span>
                    <span>{polarity.name}</span>
                    <span className="ml-auto tabular-nums text-muted-foreground">
                      {formatProbability(label.probability)} likely
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      ))}
      <p className="text-xs text-muted-foreground">
        Model: <code className="font-mono">{result.model}</code>. Probabilities are calibrated: of
        the labels shown at 80%, about 8 in 10 are right. Your text is not stored or sent back.
      </p>
    </div>
  );
}

function AnalyzeError({ error }: { error: unknown }) {
  if (error instanceof ApiError && error.status === 503) {
    return (
      <ErrorState error={error} title="The model is not available right now">
        <p className="text-sm">
          The review classifier is not loaded on this server. Search, hotel pages and compare still
          work; they use scores computed in advance.
        </p>
      </ErrorState>
    );
  }
  if (error instanceof ApiError && error.status === 429) {
    return <ErrorState error={error} title="Too many requests" />;
  }
  if (error instanceof ApiError && error.status === 422) {
    return (
      <ErrorState error={error} title="The text could not be analysed">
        {error.fields.length > 0 && (
          <ul className="list-disc pl-5 text-sm">
            {error.fields.map((field, i) => (
              <li key={i}>
                {field.location.filter((part) => part !== 'body').join(' ')}: {field.message}
              </li>
            ))}
          </ul>
        )}
      </ErrorState>
    );
  }
  return <ErrorState error={error} title="The text could not be analysed" />;
}

/** One sentence for screen readers when a result arrives. */
function resultSummary(result: AnalyzeResponse): string {
  const count = result.parts.reduce((total, part) => total + part.labels.length, 0);
  if (count === 0) return 'Analysis done: no attribute was detected with enough confidence.';
  return `Analysis done: ${count === 1 ? '1 label' : `${count} labels`} found. They are listed below the form.`;
}

function AnalyzePanel() {
  const ids = useId();
  const [liked, setLiked] = useState('');
  const [disliked, setDisliked] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const likedRef = useRef<HTMLTextAreaElement>(null);
  const analyze = useAnalyzeReview();
  const problemId = `${ids}-problem`;

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!liked.trim() && !disliked.trim()) {
      setProblem('Write something in at least one of the two boxes.');
      likedRef.current?.focus();
      return;
    }
    setProblem(null);
    analyze.mutate({ data: { liked: liked.trim(), disliked: disliked.trim() } });
  };

  const field = (
    key: 'liked' | 'disliked',
    label: string,
    hint: string,
    value: string,
    setValue: (value: string) => void,
  ) => (
    <div className="space-y-1.5">
      <Label htmlFor={`${ids}-${key}`}>{label}</Label>
      <Textarea
        id={`${ids}-${key}`}
        ref={key === 'liked' ? likedRef : undefined}
        name={key}
        value={value}
        maxLength={MAX_PART_CHARS}
        onChange={(event) => {
          setValue(event.target.value.slice(0, MAX_PART_CHARS));
          if (problem) setProblem(null);
        }}
        aria-invalid={problem ? true : undefined}
        aria-describedby={problem ? `${problemId} ${ids}-${key}-count` : `${ids}-${key}-count`}
        placeholder={hint}
        rows={4}
      />
      <p id={`${ids}-${key}-count`} className="text-xs text-muted-foreground">
        {formatCount(value.length)} of {formatCount(MAX_PART_CHARS)} characters
      </p>
    </div>
  );

  return (
    <div className="space-y-4 rounded-lg border bg-card p-4">
      <form onSubmit={onSubmit} className="space-y-4" aria-describedby={`${ids}-intro`} noValidate>
        <p id={`${ids}-intro`} className="text-sm text-muted-foreground">
          Type a review the way the booking site splits it. The model labels each part with the
          attributes it mentions and whether it is praise or a complaint. Nothing is stored.
        </p>
        <div className="grid gap-4 md:grid-cols-2">
          {field(
            'liked',
            'What the guest liked',
            'e.g. Friendly staff and a spotless room',
            liked,
            setLiked,
          )}
          {field(
            'disliked',
            'What the guest disliked',
            'e.g. Street noise at night and slow wifi',
            disliked,
            setDisliked,
          )}
        </div>
        {problem && (
          <p id={problemId} role="alert" className="text-sm font-medium text-weak">
            {problem}
          </p>
        )}
        <Button type="submit" disabled={analyze.isPending}>
          {analyze.isPending ? 'Analysing...' : 'Analyse review'}
        </Button>
      </form>
      {/* Always on the page, so the result is announced when it arrives. */}
      <p role="status" className="sr-only">
        {analyze.isSuccess ? resultSummary(analyze.data) : analyze.isPending ? 'Analysing...' : ''}
      </p>
      {analyze.isError && <AnalyzeError error={analyze.error} />}
      {analyze.isSuccess && <AnalyzeResult result={analyze.data} />}
    </div>
  );
}

export function AboutPage() {
  const meta = useGetMeta();

  return (
    <div className="max-w-4xl space-y-10">
      <title>About the model | hotel-curator</title>
      <div className="space-y-3">
        <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">How hotel-curator works</h1>
        <p className="text-lg">
          hotel-curator reads what guests wrote and grades each hotel on eight attributes, with how
          sure we are and the review text behind each grade. The grades add up to a tier.
        </p>
        <p className="text-muted-foreground">
          The reviews date from August 2015 to August 2017
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
      {meta.isPending ? (
        <LoadingBlock label="Loading the tier policy" lines={4} />
      ) : (
        <Policy meta={meta.data} />
      )}

      <Section id="limits" title="Limits and caveats">
        <ul className="list-disc space-y-2 pl-6">
          <li>
            <strong>Machine-made labels.</strong> The training and test labels come from automated
            annotators checked against people, not from people reading every review. Their mistakes
            carry into the model.
          </li>
          <li>
            <strong>Old data.</strong> Reviews are from August 2015 to August 2017. Hotels change;
            the product never claims current quality.
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
        <AnalyzePanel />
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
