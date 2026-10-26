import { useSyncExternalStore } from 'react';
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  ReferenceLine,
  Scatter,
  Text,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { cn } from '@/lib/utils';

export interface IntervalRow {
  key: string;
  /** Category label on the vertical axis; must be unique, wraps to three lines. */
  label: string;
  /** Shown in the tooltip when the axis label may be cut short. */
  fullLabel?: string;
  /** The point estimate (observed share, F1, ...). */
  value: number;
  low: number;
  high: number;
  /** Drawn with the contrast colour; the label must also say why. */
  emphasis?: boolean;
}

export interface ReferenceMark {
  value: number;
  /** Named in the legend, e.g. "80% (Strong)". */
  label: string;
  /** Drawn at the top of the line, e.g. "80%". */
  short: string;
}

interface IntervalChartProps {
  rows: IntervalRow[];
  domain: [number, number];
  format: (value: number) => string;
  /** Legend text for the point, e.g. "Observed share of praise". */
  valueName: string;
  /** Legend text for the bar, e.g. "90% likely range". */
  rangeName: string;
  /** Legend text for emphasised points, when any row has `emphasis`. */
  emphasisName?: string;
  references?: ReferenceMark[];
  /** Read by screen readers as the chart's title; a table nearby has the numbers. */
  title: string;
  description: string;
  labelWidth?: number;
  /** Height per category; raise it when labels wrap. */
  rowHeight?: number;
  className?: string;
}

const NARROW_QUERY = '(max-width: 520px)';

function subscribeNarrow(onChange: () => void): () => void {
  const media = window.matchMedia?.(NARROW_QUERY);
  media?.addEventListener('change', onChange);
  return () => media?.removeEventListener('change', onChange);
}

/** True on phone-width screens, where category labels get less room. */
function useNarrow(): boolean {
  return useSyncExternalStore(
    subscribeNarrow,
    () => window.matchMedia?.(NARROW_QUERY).matches ?? false,
    () => false,
  );
}

interface Datum extends IntervalRow {
  range: [number, number];
}

interface DotProps {
  cx?: number;
  cy?: number;
  payload?: Datum;
}

function Dot({ cx, cy, payload }: DotProps) {
  if (cx === undefined || cy === undefined || !payload) return null;
  return (
    <circle
      cx={cx}
      cy={cy}
      r={payload.emphasis ? 7 : 6}
      fill={payload.emphasis ? 'var(--chart-contrast)' : 'var(--chart-primary)'}
      stroke="var(--chart-surface)"
      strokeWidth={2}
    />
  );
}

interface TooltipBodyProps {
  active?: boolean;
  payload?: ReadonlyArray<{ payload?: Datum }>;
  format: (value: number) => string;
  valueName: string;
  rangeName: string;
}

function TooltipBody({ active, payload, format, valueName, rangeName }: TooltipBodyProps) {
  const datum = payload?.[0]?.payload;
  if (!active || !datum) return null;
  return (
    <div className="rounded-md border bg-card px-3 py-2 text-sm text-card-foreground shadow-md">
      <p className="font-semibold">{datum.fullLabel ?? datum.label}</p>
      <p>
        {valueName}: {format(datum.value)}
      </p>
      <p>
        {rangeName}: {format(datum.low)} to {format(datum.high)}
      </p>
    </div>
  );
}

interface CategoryTickProps {
  x?: number | string;
  y?: number | string;
  payload?: { value?: unknown };
  width: number;
  fontSize: number;
}

/** A category label that wraps onto up to three lines instead of being cut. */
function CategoryTick({ x, y, payload, width, fontSize }: CategoryTickProps) {
  return (
    <Text
      x={Number(x ?? 0)}
      y={Number(y ?? 0)}
      width={width}
      maxLines={3}
      textAnchor="end"
      verticalAnchor="middle"
      fontSize={fontSize}
      // Recharts measures words with this style to decide where to wrap.
      style={{ fontSize: `${fontSize}px` }}
      fill="var(--foreground)"
    >
      {String(payload?.value ?? '')}
    </Text>
  );
}

interface ReferenceLabelProps {
  viewBox?: { x?: number; y?: number };
  text: string;
  anchor: 'start' | 'middle' | 'end';
}

/**
 * The value of a grade line, above the plot. With two lines the lower one is
 * labelled on its left and the upper one on its right, so the labels never
 * overlap, even when the lines are a few pixels apart.
 */
function ReferenceLabel({ viewBox, text, anchor }: ReferenceLabelProps) {
  if (viewBox?.x === undefined || viewBox.y === undefined) return null;
  const dx = anchor === 'start' ? 2 : anchor === 'end' ? -2 : 0;
  return (
    <text
      x={viewBox.x + dx}
      y={viewBox.y - 6}
      textAnchor={anchor}
      fontSize={11}
      fill="var(--muted-foreground)"
    >
      {text}
    </text>
  );
}

/**
 * Horizontal interval chart: a grey bar for the likely range and a dot for
 * the point estimate, one row per category. Always paired with a table or a
 * text summary of the same numbers.
 */
export function IntervalChart({
  rows,
  domain,
  format,
  valueName,
  rangeName,
  emphasisName,
  references = [],
  title,
  description,
  labelWidth = 128,
  rowHeight = 40,
  className,
}: IntervalChartProps) {
  const narrow = useNarrow();
  const data: Datum[] = rows.map((row) => ({ ...row, range: [row.low, row.high] }));
  const yWidth = narrow ? Math.min(labelWidth, 96) : labelWidth;
  const fontSize = narrow ? 11 : 12;
  // Room above the plot for the grade-line labels.
  const top = references.length > 0 ? 22 : 8;
  const height = Math.max(120, rows.length * rowHeight + 36 + top);
  const hasEmphasis = rows.some((row) => row.emphasis);

  return (
    <div className={cn('space-y-2', className)}>
      <ComposedChart
        layout="vertical"
        data={data}
        responsive
        style={{ width: '100%', height }}
        margin={{ top, right: narrow ? 16 : 24, bottom: 4, left: 0 }}
        title={title}
        desc={description}
      >
        <CartesianGrid horizontal={false} stroke="var(--chart-grid)" />
        <XAxis
          type="number"
          domain={domain}
          tickFormatter={format}
          tickCount={6}
          stroke="var(--chart-grid)"
          tick={{ fill: 'var(--muted-foreground)', fontSize: 12 }}
          allowDataOverflow
        />
        <YAxis
          type="category"
          dataKey="label"
          width={yWidth}
          tickLine={false}
          stroke="var(--chart-grid)"
          tick={(props) => <CategoryTick {...props} width={yWidth - 10} fontSize={fontSize} />}
          interval={0}
        />
        {references.map((ref, index) => {
          const anchor =
            references.length < 2
              ? 'middle'
              : index === 0
                ? 'end'
                : index === references.length - 1
                  ? 'start'
                  : 'middle';
          return (
            <ReferenceLine
              key={ref.label}
              x={ref.value}
              stroke="var(--chart-reference)"
              strokeDasharray="4 3"
              // Labelled at the top so the line is named even when the axis
              // drops ticks on a narrow screen.
              label={(props: { viewBox?: ReferenceLabelProps['viewBox'] }) => (
                <ReferenceLabel viewBox={props.viewBox} text={ref.short} anchor={anchor} />
              )}
            />
          );
        })}
        <Bar
          dataKey="range"
          name={rangeName}
          barSize={8}
          radius={4}
          fill="var(--chart-interval)"
          isAnimationActive={false}
        />
        <Scatter dataKey="value" name={valueName} shape={Dot} isAnimationActive={false} />
        <Tooltip
          cursor={{ fill: 'var(--muted)', opacity: 0.6 }}
          content={(props) => (
            <TooltipBody
              active={props.active}
              payload={props.payload as TooltipBodyProps['payload']}
              format={format}
              valueName={valueName}
              rangeName={rangeName}
            />
          )}
        />
      </ComposedChart>
      <ul
        className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground"
        aria-label="Legend"
      >
        <li className="flex items-center gap-1.5">
          <svg aria-hidden="true" width="12" height="12" viewBox="0 0 12 12">
            <circle cx="6" cy="6" r="5" fill="var(--chart-primary)" />
          </svg>
          {valueName}
        </li>
        <li className="flex items-center gap-1.5">
          <svg aria-hidden="true" width="20" height="8" viewBox="0 0 20 8">
            <rect x="0" y="0" width="20" height="8" rx="4" fill="var(--chart-interval)" />
          </svg>
          {rangeName}
        </li>
        {hasEmphasis && emphasisName && (
          <li className="flex items-center gap-1.5">
            <svg aria-hidden="true" width="14" height="14" viewBox="0 0 14 14">
              <circle cx="7" cy="7" r="6" fill="var(--chart-contrast)" />
            </svg>
            {emphasisName}
          </li>
        )}
        {references.length > 0 && (
          <li className="flex items-center gap-1.5">
            <svg aria-hidden="true" width="20" height="8" viewBox="0 0 20 8">
              <line
                x1="0"
                y1="4"
                x2="20"
                y2="4"
                stroke="var(--chart-reference)"
                strokeWidth="1.5"
                strokeDasharray="4 3"
              />
            </svg>
            Grade lines at {references.map((ref) => ref.label).join(' and ')}
          </li>
        )}
      </ul>
    </div>
  );
}
