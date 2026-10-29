/** A headline number in a `<dl>`: label, value and one line of detail. */
export function StatTile({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="rounded-lg border bg-card p-4">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="mt-1 text-2xl font-semibold tabular-nums">{value}</dd>
      <dd className="text-sm text-muted-foreground">{detail}</dd>
    </div>
  );
}
