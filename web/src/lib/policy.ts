/**
 * The tier policy before /v1/meta answers (or when it fails): the
 * pre-registered values from config/tier_policy.yaml, exported to
 * tier-policy.json by scripts/export_openapi.py (CI fails if they differ).
 */
import type { DataPeriod, Meta, TierPolicy } from '@/api/generated/model';

import tierPolicy from './tier-policy.json';

// Generated from the validated policy model, so it has the API's shape.
export const DEFAULT_POLICY = tierPolicy as TierPolicy;

/** The served policy, or the pre-registered one until it arrives. */
export function policyOf(meta: Meta | undefined): TierPolicy {
  return meta?.policy ?? DEFAULT_POLICY;
}

const monthYear = new Intl.DateTimeFormat('en-GB', {
  month: 'long',
  year: 'numeric',
  timeZone: 'UTC',
});

function formatMonthYear(month: string): string | null {
  const match = /^(\d{4})-(\d{2})$/.exec(month);
  if (!match) return null;
  const index = Number(match[2]) - 1;
  if (index < 0 || index > 11) return null;
  return monthYear.format(new Date(Date.UTC(Number(match[1]), index, 1)));
}

/** "August 2015 to August 2017", or null when the period is not known. */
export function formatPeriod(period: DataPeriod | null | undefined): string | null {
  if (!period) return null;
  const first = formatMonthYear(period.first_month);
  const last = formatMonthYear(period.last_month);
  return first && last ? `${first} to ${last}` : null;
}
