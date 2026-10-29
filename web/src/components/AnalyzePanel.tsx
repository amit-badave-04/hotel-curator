import { ThumbsDown, ThumbsUp } from 'lucide-react';
import { useId, useRef, useState, type FormEvent, type Ref } from 'react';

import { ApiError } from '@/api/fetcher';
import { useAnalyzeReview } from '@/api/generated/endpoints';
import type { AnalyzeResponse, Attribute } from '@/api/generated/model';
import { ErrorState, LoadingBlock } from '@/components/states';
import { Button } from '@/components/ui/button';
import { Label, Textarea } from '@/components/ui/input';
import {
  ATTRIBUTES,
  attributeName,
  formatCount,
  formatProbability,
  MAX_PART_CHARS,
} from '@/lib/domain';

const POLARITY = {
  positive: { name: 'Praise', icon: ThumbsUp, className: 'text-strong' },
  negative: { name: 'Complaint', icon: ThumbsDown, className: 'text-weak' },
} as const;

const NO_MODEL_TITLE = 'No model loaded';

/** Why the box cannot answer, and what still works without the model. */
function NoModelText() {
  return (
    <p className="text-sm">
      The review classifier is not loaded on this server, so reviews cannot be analysed here.
      Search, hotel pages and compare still work; they use scores computed in advance.
    </p>
  );
}

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
      <ErrorState error={error} title={NO_MODEL_TITLE}>
        <NoModelText />
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

interface ReviewFieldProps {
  id: string;
  label: string;
  hint: string;
  value: string;
  onChange: (value: string) => void;
  problemId: string | null;
  inputRef?: Ref<HTMLTextAreaElement>;
  name: string;
}

function ReviewField({
  id,
  label,
  hint,
  value,
  onChange,
  problemId,
  inputRef,
  name,
}: ReviewFieldProps) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Textarea
        id={id}
        ref={inputRef}
        name={name}
        value={value}
        maxLength={MAX_PART_CHARS}
        onChange={(event) => onChange(event.target.value.slice(0, MAX_PART_CHARS))}
        aria-invalid={problemId ? true : undefined}
        aria-describedby={problemId ? `${problemId} ${id}-count` : `${id}-count`}
        placeholder={hint}
        rows={4}
      />
      <p id={`${id}-count`} className="text-xs text-muted-foreground">
        {formatCount(value.length)} of {formatCount(MAX_PART_CHARS)} characters
      </p>
    </div>
  );
}

function AnalyzeForm() {
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
  const edit = (setValue: (value: string) => void) => (value: string) => {
    setValue(value);
    if (problem) setProblem(null);
  };

  return (
    <>
      <form onSubmit={onSubmit} className="space-y-4" aria-describedby={`${ids}-intro`} noValidate>
        <p id={`${ids}-intro`} className="text-sm text-muted-foreground">
          Type a review the way the booking site splits it. The model labels each part with the
          attributes it mentions and whether it is praise or a complaint. Nothing is stored.
        </p>
        <div className="grid gap-4 md:grid-cols-2">
          <ReviewField
            id={`${ids}-liked`}
            name="liked"
            label="What the guest liked"
            hint="e.g. Friendly staff and a spotless room"
            value={liked}
            onChange={edit(setLiked)}
            problemId={problem ? problemId : null}
            inputRef={likedRef}
          />
          <ReviewField
            id={`${ids}-disliked`}
            name="disliked"
            label="What the guest disliked"
            hint="e.g. Street noise at night and slow wifi"
            value={disliked}
            onChange={edit(setDisliked)}
            problemId={problem ? problemId : null}
          />
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
    </>
  );
}

interface AnalyzePanelProps {
  /**
   * `analyze_available` from /v1/meta. When it is `false` the form is
   * replaced by a notice. When /v1/meta failed it is unknown: the form is
   * shown, and a 503 from the API says the same thing as the notice.
   */
  available?: boolean;
  /** /v1/meta has not answered yet, so whether to show the form is open. */
  checking?: boolean;
}

/** "Try the model": label a review with the served classifier. */
export function AnalyzePanel({ available, checking = false }: AnalyzePanelProps) {
  return (
    <div className="space-y-4 rounded-lg border bg-card p-4">
      {checking ? (
        <LoadingBlock label="Loading the model status" lines={3} />
      ) : available === false ? (
        <div className="space-y-2">
          <p className="font-semibold">{NO_MODEL_TITLE}</p>
          <NoModelText />
        </div>
      ) : (
        <AnalyzeForm />
      )}
    </div>
  );
}
