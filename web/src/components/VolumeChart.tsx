import { Bar, BarChart, CartesianGrid, Tooltip, XAxis, YAxis } from 'recharts';

import type { MonthCount } from '@/api/generated/model';
import { formatCount, formatMonth, plural } from '@/lib/domain';

interface VolumeChartProps {
  months: MonthCount[];
  /** Read by screen readers as the chart's title; the table has the numbers. */
  title: string;
}

interface TooltipBodyProps {
  active?: boolean;
  payload?: ReadonlyArray<{ payload?: MonthCount }>;
}

function TooltipBody({ active, payload }: TooltipBodyProps) {
  const datum = payload?.[0]?.payload;
  if (!active || !datum) return null;
  return (
    <div className="rounded-md border bg-card px-3 py-2 text-sm text-card-foreground shadow-md">
      <p className="font-semibold">{formatMonth(datum.month)}</p>
      <p>{plural(datum.reviews, 'usable review')}</p>
    </div>
  );
}

/** January of each year, plus the first month, as axis ticks. */
function yearTicks(months: MonthCount[]): string[] {
  return months.filter((m, index) => index === 0 || m.month.endsWith('-01')).map((m) => m.month);
}

/**
 * Usable reviews per month: one series, so no legend; the section heading
 * names it. Always followed by a table of the same numbers.
 */
export function VolumeChart({ months, title }: VolumeChartProps) {
  const total = months.reduce((sum, m) => sum + m.reviews, 0);
  const busiest = months.reduce<MonthCount | undefined>(
    (best, m) => (best === undefined || m.reviews > best.reviews ? m : best),
    undefined,
  );
  const description = busiest
    ? `${plural(total, 'usable review')} over ${plural(months.length, 'month')}; ` +
      `the busiest month is ${formatMonth(busiest.month)} with ${formatCount(busiest.reviews)}.`
    : 'No reviews.';

  return (
    <div className="space-y-3">
      <BarChart
        data={months}
        responsive
        style={{ width: '100%', height: 200 }}
        margin={{ top: 8, right: 16, bottom: 4, left: 0 }}
        barCategoryGap={2}
        title={title}
        desc={description}
      >
        <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
        <XAxis
          dataKey="month"
          ticks={yearTicks(months)}
          tickFormatter={formatMonth}
          stroke="var(--chart-grid)"
          tick={{ fill: 'var(--muted-foreground)', fontSize: 12 }}
        />
        <YAxis
          allowDecimals={false}
          width={40}
          stroke="var(--chart-grid)"
          tick={{ fill: 'var(--muted-foreground)', fontSize: 12 }}
        />
        <Bar
          dataKey="reviews"
          name="Usable reviews"
          fill="var(--chart-primary)"
          radius={[4, 4, 0, 0]}
          isAnimationActive={false}
        />
        <Tooltip
          cursor={{ fill: 'var(--muted)', opacity: 0.6 }}
          content={(props) => (
            <TooltipBody
              active={props.active}
              payload={props.payload as TooltipBodyProps['payload']}
            />
          )}
        />
      </BarChart>
      <p className="text-sm text-muted-foreground">{description}</p>
      <details className="text-sm">
        <summary className="cursor-pointer font-medium">Reviews per month as a table</summary>
        <table className="mt-2 w-full max-w-sm text-left">
          <caption className="sr-only">{title}</caption>
          <thead>
            <tr className="border-b">
              <th scope="col" className="py-1 font-medium">
                Month
              </th>
              <th scope="col" className="py-1 text-right font-medium">
                Usable reviews
              </th>
            </tr>
          </thead>
          <tbody>
            {months.map((m) => (
              <tr key={m.month} className="border-b border-border/60">
                <th scope="row" className="py-1 font-normal">
                  {formatMonth(m.month)}
                </th>
                <td className="py-1 text-right tabular-nums">{formatCount(m.reviews)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
