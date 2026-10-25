/**
 * Plain-language wording for the API's enums (ADR 006). Numbers shown here
 * are the pre-registered policy values; the About page reads the live values
 * from /v1/meta.
 */
import type {
  Attribute,
  Grade,
  HotelSummary,
  Lean,
  SortOrder,
  Tier,
  TrendState,
} from '@/api/generated/model';

export const ATTRIBUTES: readonly Attribute[] = [
  'cleanliness',
  'room',
  'staff',
  'location',
  'noise',
  'wifi',
  'value',
  'food',
];

export const ESSENTIALS: readonly Attribute[] = ['cleanliness', 'room', 'staff'];

export const MUST_BE: readonly Attribute[] = ['noise', 'wifi'];

export const ATTRIBUTE_NAMES: Record<Attribute, string> = {
  cleanliness: 'Cleanliness',
  room: 'Room',
  staff: 'Staff',
  location: 'Location',
  noise: 'Noise',
  wifi: 'Wifi',
  value: 'Value',
  food: 'Breakfast & food',
};

export function attributeName(attribute: Attribute): string {
  return ATTRIBUTE_NAMES[attribute] ?? attribute;
}

/**
 * Names for "strong in" contexts (filters, highlights). Noise and wifi are
 * graded on complaints, so "strong in noise" would read as "noisy".
 */
const STRENGTH_NAMES: Partial<Record<Attribute, string>> = {
  noise: 'Quiet (few noise complaints)',
  wifi: 'Wifi (few complaints)',
};

export function strengthName(attribute: Attribute): string {
  return STRENGTH_NAMES[attribute] ?? attributeName(attribute);
}

/** "cleanliness, quiet (few noise complaints) and value" */
export function joinStrengths(attributes: readonly Attribute[]): string {
  return joinNames(attributes.map((a) => lowerFirst(strengthName(a))));
}

function lowerFirst(text: string): string {
  return text.charAt(0).toLowerCase() + text.slice(1);
}

function joinNames(names: readonly string[]): string {
  if (names.length <= 1) return names.join('');
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

/** "cleanliness, room and staff" */
export function joinAttributes(attributes: readonly Attribute[], lower = true): string {
  return joinNames(
    attributes.map((a) => (lower ? attributeName(a).toLowerCase() : attributeName(a))),
  );
}

export const TIERS: readonly Tier[] = [
  'excellent',
  'good',
  'fair',
  'caution',
  'not_enough_evidence',
  'not_enough_reviews',
];

export const NOT_ENOUGH_TIERS: readonly Tier[] = ['not_enough_evidence', 'not_enough_reviews'];

export type TierTone = 'excellent' | 'good' | 'fair' | 'caution' | 'unknown';

export interface TierInfo {
  /** Short name shown in the badge. */
  name: string;
  tone: TierTone;
  /** One sentence, plain language. */
  meaning: string;
}

export const TIER_INFO: Record<Tier, TierInfo> = {
  excellent: {
    name: 'Excellent',
    tone: 'excellent',
    meaning:
      'Guests confirmed cleanliness, room and staff as strengths, with no material weak spot, over at least six months of reviews.',
  },
  good: {
    name: 'Good',
    tone: 'good',
    meaning:
      'At least two of the three essentials are confirmed strengths, with at most one material weak spot elsewhere.',
  },
  fair: {
    name: 'Fair',
    tone: 'fair',
    meaning:
      'No essential is weak, but fewer than two are confirmed strengths, or there are several weak spots.',
  },
  caution: {
    name: 'Caution',
    tone: 'caution',
    meaning:
      'Complaints about an essential (cleanliness, room or staff) outnumbered praise, and we are at least 95% sure.',
  },
  not_enough_evidence: {
    name: 'Not enough evidence to judge',
    tone: 'unknown',
    meaning:
      'Too few guests gave an opinion on one of the essentials to judge it. This is not a bad sign, just not enough to go on.',
  },
  not_enough_reviews: {
    name: 'Not enough evidence to judge',
    tone: 'unknown',
    meaning:
      'Fewer than 30 usable reviews, too few to give a tier. This is not a bad sign, just not enough to go on.',
  },
};

export function isNotEnough(tier: Tier): boolean {
  return NOT_ENOUGH_TIERS.includes(tier);
}

export type Family = 'two_sided' | 'must_be';

export function familyOf(attribute: Attribute): Family {
  return MUST_BE.includes(attribute) ? 'must_be' : 'two_sided';
}

export interface GradeInfo {
  name: string;
  tone: 'strong' | 'weak' | 'neutral' | 'unknown';
  meaning: string;
}

export function gradeInfo(grade: Grade, family: Family, lean?: Lean | null): GradeInfo {
  switch (grade) {
    case 'strong':
      return {
        name: 'Strong',
        tone: 'strong',
        meaning:
          family === 'must_be'
            ? 'At most 1 guest in 20 complained (95% sure).'
            : 'At least 4 praises per complaint (95% sure).',
      };
    case 'weak':
      return {
        name: 'Weak',
        tone: 'weak',
        meaning:
          family === 'must_be'
            ? 'At least 1 guest in 10 complained (95% sure).'
            : 'Complaints outnumbered praise (95% sure).',
      };
    case 'ok':
      return {
        name:
          lean === 'trending_strong'
            ? 'OK, leaning strong'
            : lean === 'trending_weak'
              ? 'OK, leaning weak'
              : 'OK',
        tone: 'neutral',
        meaning:
          lean === 'trending_strong'
            ? 'No confirmed strength yet, but the estimate is past the Strong line.'
            : lean === 'trending_weak'
              ? 'No confirmed weakness, but the estimate is past the Weak line.'
              : 'No confirmed strength or weakness.',
      };
    case 'too_few':
    default:
      return {
        name: 'Too few opinions',
        tone: 'unknown',
        meaning:
          family === 'must_be'
            ? 'Fewer than 30 reviews, so this is not graded.'
            : 'Fewer than 10 guests gave an opinion, so this is not graded.',
      };
  }
}

export const TREND_INFO: Record<TrendState, { name: string; meaning: string }> = {
  improved: {
    name: 'Improved',
    meaning: 'The last 6 months are credibly better than the 18 months before.',
  },
  declined: {
    name: 'Declined',
    meaning: 'The last 6 months are credibly worse than the 18 months before.',
  },
  stable: { name: 'Stable', meaning: 'No credible change between the two periods.' },
  too_few: {
    name: 'Too few to tell',
    meaning: 'Not enough opinions in one of the periods to compare them.',
  },
};

export const SORT_LABELS: Record<SortOrder, string> = {
  best: 'Best tier first',
  name: 'Name (A to Z)',
  reviews: 'Most reviews first',
};

const percentWhole = new Intl.NumberFormat('en-GB', {
  style: 'percent',
  maximumFractionDigits: 0,
});
const percentOne = new Intl.NumberFormat('en-GB', {
  style: 'percent',
  minimumFractionDigits: 0,
  maximumFractionDigits: 1,
});
const integer = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 0 });
const decimal2 = new Intl.NumberFormat('en-GB', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const decimal3 = new Intl.NumberFormat('en-GB', {
  minimumFractionDigits: 3,
  maximumFractionDigits: 3,
});

/** 0.983 -> "98%", 0.012 -> "1.2%" (small shares keep one decimal). */
export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return 'n/a';
  return Math.abs(value) < 0.1 ? percentOne.format(value) : percentWhole.format(value);
}

/** A probability for "how sure": never rounds up to a certain-sounding 100%. */
export function formatProbability(value: number): string {
  if (value > 0.99) return 'more than 99%';
  if (value < 0.01) return 'less than 1%';
  return percentWhole.format(value);
}

/** "95% to 99%" */
export function formatRange(low: number, high: number): string {
  return `${formatPercent(low)} to ${formatPercent(high)}`;
}

export function formatCount(value: number): string {
  return integer.format(value);
}

export function formatScore(value: number, digits: 2 | 3 = 3): string {
  return digits === 2 ? decimal2.format(value) : decimal3.format(value);
}

/** Signed score difference, e.g. "+0.042". */
export function formatSigned(value: number): string {
  const text = decimal3.format(Math.abs(value));
  return value > 0 ? `+${text}` : value < 0 ? `-${text}` : text;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "2015-09" -> "Sep 2015"; anything else is returned unchanged. */
export function formatMonth(value: string): string {
  const match = /^(\d{4})-(\d{2})$/.exec(value);
  if (!match) return value;
  const month = MONTHS[Number(match[2]) - 1];
  return month ? `${month} ${match[1]}` : value;
}

/** "120 reviews" / "1 review" */
export function plural(count: number, singular: string, pluralForm = `${singular}s`): string {
  return `${formatCount(count)} ${Math.round(count) === 1 ? singular : pluralForm}`;
}

/**
 * Opinion counts are sums of probabilities (expected counts), so they are
 * rounded and marked "about" unless every one is a whole number.
 */
export function isWhole(...counts: number[]): boolean {
  return counts.every((count) => Number.isInteger(count));
}

/**
 * Upper end of the axis for complaint rates (noise, wifi): at least 20%,
 * else the highest likely-range end rounded up to the next 5%.
 */
export function complaintAxisMax(highs: readonly number[]): number {
  return Math.min(1, Math.max(0.2, ...highs.map((high) => Math.ceil(high * 20) / 20)));
}

/**
 * One line on why a search result has its tier, built from the summary
 * fields only (the hotel page has the full generated explanation).
 */
export function summaryWhy(hotel: HotelSummary): string {
  if (hotel.tier === 'not_enough_reviews') {
    return `Only ${plural(hotel.reviews_eligible, 'usable review')}; at least 30 are needed to give a tier.`;
  }
  if (hotel.tier === 'not_enough_evidence') {
    return 'Too few guests gave an opinion on one of the essentials to judge it yet.';
  }
  const grades = ESSENTIALS.map((a) => [a, hotel.essentials[a]] as const);
  const weak = grades.filter(([, g]) => g === 'weak').map(([a]) => a);
  const strong = grades.filter(([, g]) => g === 'strong').map(([a]) => a);
  if (hotel.tier === 'caution' && weak.length > 0) {
    return `Complaints about ${joinAttributes(weak)} outnumbered praise.`;
  }
  const parts: string[] = [];
  parts.push(
    strong.length > 0
      ? `Guests confirmed ${joinAttributes(strong)} as strengths`
      : 'No essential is a confirmed strength yet',
  );
  if (hotel.catches.length > 0) {
    parts.push(`complaints about ${joinAttributes(hotel.catches)} hold it back`);
  }
  return `${parts.join('; ')}.`;
}

export const HOTEL_ID_PATTERN = /^h_[0-9a-f]{10}$/;
export const CITY_PATTERN = /^[A-Za-z][A-Za-z .'-]{0,59}$/;
export const MAX_QUERY_LENGTH = 80;
export const MAX_COMPARE = 4;
export const MAX_PART_CHARS = 2000;
