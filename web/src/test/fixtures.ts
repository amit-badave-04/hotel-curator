/**
 * Test data shaped by the generated OpenAPI types, so a fixture that drifts
 * from web/openapi.json fails the type check. Values follow the synthetic dev
 * data (e2e/data/hotel_results.json.gz) with extra cases the dev data lacks:
 * counter-quotes, segments, null trends and scores, an evaluation.
 */
import type {
  AnalyzeResponse,
  Attribute,
  AttributeOut,
  City,
  Comparison,
  Evaluation,
  Grade,
  HotelDetail,
  HotelSummary,
  Meta,
  MonthCount,
  QuoteOut,
  Tier,
  TrendOut,
} from '@/api/generated/model';

import { DEFAULT_POLICY } from '@/lib/policy';

const NAMES: Record<Attribute, string> = {
  cleanliness: 'Cleanliness',
  room: 'Room',
  staff: 'Staff',
  location: 'Location',
  noise: 'Noise',
  wifi: 'Wifi',
  value: 'Value',
  food: 'Breakfast & food',
};
const ORDER: Attribute[] = [
  'cleanliness',
  'room',
  'staff',
  'location',
  'noise',
  'wifi',
  'value',
  'food',
];
const MUST_BE: Attribute[] = ['noise', 'wifi'];

const stableTrend: TrendOut = {
  state: 'stable',
  recent_mean: 0.96,
  baseline_mean: 0.97,
  prob_improved: 0.0005,
  prob_declined: 0.026,
};

export function quote(overrides: Partial<QuoteOut> = {}): QuoteOut {
  return {
    text: 'Lovely staff and comfortable room',
    part: 'liked',
    month: 'Aug 2017',
    traveler_type: 'Couple',
    probability: 0.97,
    polarity: 'positive',
    supports: true,
    ...overrides,
  };
}

interface AttributeSpec {
  grade: Grade;
  observed: number;
  low: number;
  high: number;
  opinions?: number;
  trend?: TrendOut | null;
  quotes?: QuoteOut[];
  material?: boolean;
  lean?: AttributeOut['lean'];
}

function attribute(name: Attribute, spec: AttributeSpec, reviews: number): AttributeOut {
  const mustBe = MUST_BE.includes(name);
  const opinions = spec.opinions ?? reviews * 0.98;
  const negative = mustBe ? spec.observed * reviews : (1 - spec.observed) * opinions;
  const positive = mustBe ? opinions - negative : spec.observed * opinions;
  return {
    attribute: name,
    display_name: NAMES[name],
    essential: ['cleanliness', 'room', 'staff'].includes(name),
    family: mustBe ? 'must_be' : 'two_sided',
    grade: spec.grade,
    lean: spec.lean ?? null,
    observed: spec.observed,
    mean: spec.observed,
    low: spec.low,
    high: spec.high,
    prob_strong: spec.grade === 'strong' ? 0.999 : 0.1,
    prob_weak: spec.grade === 'weak' ? 0.999 : 0.001,
    positive,
    negative,
    opinions,
    reviews,
    complaint_rate: mustBe ? spec.observed : negative / reviews,
    material: spec.material ?? false,
    complaining_reviews: Math.round(negative),
    positive_hard: Math.round(positive),
    negative_hard: Math.round(negative),
    trend: spec.trend === undefined ? stableTrend : spec.trend,
    quotes: spec.quotes ?? [],
  };
}

const strong = (extra: Partial<AttributeSpec> = {}): AttributeSpec => ({
  grade: 'strong',
  observed: 0.98,
  low: 0.95,
  high: 0.99,
  quotes: [
    quote(),
    quote({ text: 'Spotless and quiet room great staff', traveler_type: 'Solo traveller' }),
  ],
  ...extra,
});

const PROVENANCE = {
  params_version: '2026-10-24.1',
  model: 'test-model',
  data_source:
    'Booking.com 515K hotel reviews in Europe (2015-2017), via a pinned public mirror; see data/manifests/data.json',
  data_licence:
    'CC0 as declared by the uploader, originally Booking.com content, unconfirmed; legal review required before commercial use',
  data_period: 'Sep 2015 to Aug 2017',
  corrections: 'See NOTICE for how to request a correction or removal.',
};

interface HotelSpec {
  hotel_id: string;
  name: string;
  city: string;
  country: string;
  tier: Tier;
  label: string;
  reviews: number;
  attributes: Partial<Record<Attribute, AttributeSpec>>;
  catches?: Attribute[];
  highlights?: Attribute[];
  detail?: Partial<HotelDetail>;
}

function hotel(spec: HotelSpec): HotelDetail {
  const attributes = ORDER.map((name) =>
    attribute(
      name,
      spec.attributes[name] ??
        (MUST_BE.includes(name)
          ? { grade: 'strong', observed: 0.01, low: 0.002, high: 0.035 }
          : { grade: 'too_few', observed: 0.5, low: 0.18, high: 0.82, opinions: 4.8, trend: null }),
      spec.reviews,
    ),
  );
  const essentials = Object.fromEntries(
    attributes.filter((a) => a.essential).map((a) => [a.attribute, a.grade]),
  ) as HotelDetail['essentials'];
  return {
    hotel_id: spec.hotel_id,
    name: spec.name,
    city: spec.city,
    country: spec.country,
    address: `1 Street ${spec.city}`,
    lat: 48.0,
    lng: 2.0,
    tier: spec.tier,
    label: spec.label,
    sort_score: 0.9,
    reviews_eligible: spec.reviews,
    reviews_total: spec.reviews,
    catches: spec.catches ?? [],
    highlights: spec.highlights ?? [],
    essentials,
    attributes,
    explanation: {
      label: spec.label,
      verdict: `In reviews from Sep 2015 to Aug 2017, guests wrote about ${spec.name}.`,
      essentials: attributes
        .filter((a) => a.essential)
        .map((a) => `${a.display_name}: ${a.grade}. ${Math.round(a.observed * 100)}% praise.`),
      what_would_change: [],
      for_you: [],
      who_it_suits: [],
    },
    blockers: [],
    if_ignored: {},
    segments: [],
    first_month: '2015-09',
    last_month: '2017-08',
    site_average_score: 8.0,
    provenance: PROVENANCE,
    ...spec.detail,
  };
}

/**
 * 24 months, Sep 2015 to Aug 2017, 120 reviews: 5 a month, except none in
 * Dec 2015 and 10 in Jul 2016.
 */
function monthlyVolume(): MonthCount[] {
  return Array.from({ length: 24 }, (_, i) => {
    const year = 2015 + Math.floor((8 + i) / 12);
    const month = ((8 + i) % 12) + 1;
    const key = `${year}-${String(month).padStart(2, '0')}`;
    return { month: key, reviews: key === '2015-12' ? 0 : key === '2016-07' ? 10 : 5 };
  });
}

export const alpha = hotel({
  hotel_id: 'h_00000000a1',
  name: 'Alpha Grand',
  city: 'Paris',
  country: 'France',
  tier: 'excellent',
  label: 'Excellent',
  reviews: 120,
  attributes: {
    cleanliness: strong({ observed: 0.99, low: 0.964, high: 0.998 }),
    room: strong(),
    staff: strong(),
    location: strong(),
    value: strong({ observed: 0.96, low: 0.9, high: 0.99, opinions: 61, trend: null }),
  },
  highlights: ['location', 'noise', 'wifi', 'value'],
  detail: {
    explanation: {
      label: 'Excellent',
      verdict:
        'In reviews from Sep 2015 to Aug 2017, guests praised cleanliness, room and staff (confirmed).',
      essentials: [
        'Cleanliness: Strong. 99% of 118 opinions were praise (likely 96% to 100%).',
        'Room: Strong. 98% of 118 opinions were praise (likely 95% to 99%).',
        'Staff: Strong. 98% of 118 opinions were praise (likely 95% to 99%).',
      ],
      what_would_change: [],
      for_you: [],
      who_it_suits: [],
    },
    review_volume: monthlyVolume(),
  },
});

export const bravo = hotel({
  hotel_id: 'h_00000000b2',
  name: 'Bravo Inn',
  city: 'Paris',
  country: 'France',
  tier: 'caution',
  label: 'Caution',
  reviews: 120,
  attributes: {
    cleanliness: {
      grade: 'weak',
      observed: 0.01,
      low: 0.002,
      high: 0.036,
      material: true,
      quotes: [
        quote({
          text: 'Dirty bathroom and stained sheets',
          part: 'disliked',
          polarity: 'negative',
          traveler_type: 'Family with young children',
          month: 'Jul 2017',
        }),
      ],
    },
    room: strong(),
    staff: strong(),
    location: strong(),
  },
  highlights: ['location', 'noise', 'wifi'],
  detail: {
    explanation: {
      label: 'Caution',
      verdict:
        'In reviews from Sep 2015 to Aug 2017, complaints about cleanliness outnumbered praise (at least 95% sure).',
      essentials: [
        'Cleanliness: Weak. 1% of 118 opinions were praise (likely 0% to 4%).',
        'Room: Strong. 98% of 118 opinions were praise (likely 95% to 99%).',
        'Staff: Strong. 98% of 118 opinions were praise (likely 95% to 99%).',
      ],
      what_would_change: [
        'Complaints about cleanliness outnumber praise; this has to change first.',
      ],
      for_you: [],
      who_it_suits: [],
    },
  },
});

export const charlie = hotel({
  hotel_id: 'h_00000000c3',
  name: 'Charlie House',
  city: 'London',
  country: 'United Kingdom',
  tier: 'good',
  label: 'Good, but noisy',
  reviews: 120,
  attributes: {
    cleanliness: strong({
      quotes: [
        quote({ text: 'Very clean room and friendly staff' }),
        quote({
          text: 'Hair in the shower on arrival',
          part: 'disliked',
          polarity: 'negative',
          supports: false,
          month: 'Mar 2017',
          traveler_type: 'Friends',
        }),
      ],
    }),
    room: strong({
      trend: {
        state: 'declined',
        recent_mean: 0.88,
        baseline_mean: 0.97,
        prob_improved: 0.0,
        prob_declined: 0.93,
      },
    }),
    staff: strong(),
    location: strong(),
    noise: {
      grade: 'weak',
      observed: 0.4,
      low: 0.33,
      high: 0.47,
      material: true,
      trend: {
        state: 'improved',
        recent_mean: 0.3,
        baseline_mean: 0.45,
        prob_improved: 0.94,
        prob_declined: 0.0,
      },
      quotes: [
        quote({
          text: 'Street noise at night',
          part: 'disliked',
          polarity: 'negative',
          traveler_type: null,
          month: 'Aug 2017',
        }),
      ],
    },
    wifi: { grade: 'ok', observed: 0.02, low: 0.006, high: 0.05, lean: 'trending_strong' },
    value: strong({ observed: 0.96, low: 0.9, high: 0.99, opinions: 61, trend: null }),
  },
  catches: ['noise'],
  highlights: ['location', 'value'],
  detail: {
    explanation: {
      label: 'Good, but noisy',
      verdict:
        'In reviews from Sep 2015 to Aug 2017, guests praised cleanliness, room and staff (confirmed).',
      essentials: [
        'Cleanliness: Strong. 98% of 119 opinions were praise (likely 95% to 99%).',
        'Room: Strong. 98% of 119 opinions were praise (likely 95% to 99%).',
        'Staff: Strong. 98% of 119 opinions were praise (likely 95% to 99%).',
      ],
      what_would_change: ['Complaints about noise (40% of guests) hold it back.'],
      for_you: ["If noise doesn't bother you, this hotel would be Excellent."],
      who_it_suits: [
        'Families complained about noise more often than other guests (an estimated 55% vs 35% of reviews).',
      ],
    },
    if_ignored: { noise: 'excellent' },
    segments: [
      {
        segment: 'families',
        attribute: 'noise',
        direction: 'worse',
        segment_value: 0.55,
        others_value: 0.35,
        segment_size: 40,
        probability: 0.97,
        text: 'Families complained about noise more often than other guests (an estimated 55% vs 35% of reviews).',
      },
    ],
  },
});

export const delta = hotel({
  hotel_id: 'h_00000000d4',
  name: 'Delta Rooms',
  city: 'London',
  country: 'United Kingdom',
  tier: 'not_enough_reviews',
  label: 'Not enough reviews',
  reviews: 12,
  attributes: {
    cleanliness: strong({ observed: 0.99, low: 0.83, high: 0.999, opinions: 11.8, trend: null }),
    room: strong({ observed: 0.99, low: 0.83, high: 0.999, opinions: 11.8, trend: null }),
    staff: strong({ observed: 0.99, low: 0.83, high: 0.999, opinions: 11.8, trend: null }),
    noise: { grade: 'too_few', observed: 0.01, low: 0.001, high: 0.165, trend: null },
    wifi: { grade: 'too_few', observed: 0.01, low: 0.001, high: 0.165, trend: null },
  },
  detail: {
    explanation: {
      label: 'Not enough reviews',
      verdict:
        'In reviews from Sep 2015 to Nov 2015, there are too few reviews to give a tier (at least 30 are needed).',
      essentials: [
        'Cleanliness: Strong. 99% of 12 opinions were praise (likely 83% to 100%).',
        'Room: Strong. 99% of 12 opinions were praise (likely 83% to 100%).',
        'Staff: Strong. 99% of 12 opinions were praise (likely 83% to 100%).',
      ],
      what_would_change: ['18 more reviews are needed before a tier is given.'],
      for_you: [],
    },
    first_month: '2015-09',
    last_month: '2015-11',
    site_average_score: null,
  },
});

export const echo = hotel({
  hotel_id: 'h_00000000e5',
  name: 'Echo Stay',
  city: 'Vienna',
  country: 'Austria',
  tier: 'not_enough_evidence',
  label: 'Not enough evidence',
  reviews: 45,
  attributes: {
    cleanliness: strong(),
    room: strong(),
    staff: { grade: 'too_few', observed: 0.9, low: 0.6, high: 0.99, opinions: 7 },
  },
});

/**
 * Not in HOTELS: Fair with one catch, as the tier rules give it. No essential
 * is Strong (Good needs two) and none is Weak, so the strength blockers are
 * the reason; Good allows one catch, so setting noise aside leaves it Fair.
 */
export const foxtrot = hotel({
  hotel_id: 'h_00000000f6',
  name: 'Foxtrot Inn',
  city: 'Paris',
  country: 'France',
  tier: 'fair',
  label: 'Fair, but noisy',
  reviews: 120,
  attributes: {
    cleanliness: { grade: 'ok', observed: 0.78, low: 0.7, high: 0.85, trend: null },
    room: { grade: 'ok', observed: 0.76, low: 0.68, high: 0.83, trend: null },
    staff: { grade: 'ok', observed: 0.72, low: 0.64, high: 0.79, trend: null },
    location: strong(),
    noise: {
      grade: 'weak',
      observed: 0.4,
      low: 0.33,
      high: 0.47,
      material: true,
      trend: null,
    },
  },
  catches: ['noise'],
  highlights: ['location', 'wifi'],
  detail: {
    explanation: {
      label: 'Fair, but noisy',
      verdict:
        'In reviews from Sep 2015 to Aug 2017, none of cleanliness, room and staff is a confirmed strength.',
      essentials: [
        'Cleanliness: OK. 78% of about 118 opinions were praise (likely 70% to 85%).',
        'Room: OK. 76% of about 118 opinions were praise (likely 68% to 83%).',
        'Staff: OK. 72% of about 118 opinions were praise (likely 64% to 79%).',
      ],
      what_would_change: [
        'Cleanliness needs more praise to count as a strength (now 78%; the line is 80%).',
        'Room needs more praise to count as a strength (now 76%; the line is 80%).',
      ],
      for_you: [],
      who_it_suits: [],
    },
    blockers: [
      {
        kind: 'share_too_low',
        attribute: 'cleanliness',
        more: null,
        text: 'Cleanliness needs more praise to count as a strength (now 78%; the line is 80%).',
      },
      {
        kind: 'share_too_low',
        attribute: 'room',
        more: null,
        text: 'Room needs more praise to count as a strength (now 76%; the line is 80%).',
      },
    ],
  },
});

export const HOTELS: HotelDetail[] = [alpha, charlie, bravo, delta, echo];

export function summary(detail: HotelDetail): HotelSummary {
  return {
    hotel_id: detail.hotel_id,
    name: detail.name,
    city: detail.city,
    country: detail.country,
    address: detail.address,
    lat: detail.lat,
    lng: detail.lng,
    tier: detail.tier,
    label: detail.label,
    sort_score: detail.sort_score,
    reviews_eligible: detail.reviews_eligible,
    reviews_total: detail.reviews_total,
    catches: detail.catches,
    highlights: detail.highlights,
    essentials: detail.essentials,
  };
}

export const CITIES: City[] = [
  { city: 'London', country: 'United Kingdom', hotels: 2 },
  { city: 'Paris', country: 'France', hotels: 2 },
  { city: 'Vienna', country: 'Austria', hotels: 1 },
];

export const EVALUATION: Evaluation = {
  model: 'encoder-small',
  v1_configuration: 'v1-parts-tuned',
  macro_f1: { point: 0.712, low: 0.681, high: 0.741 },
  v1_macro_f1: { point: 0.604, low: 0.571, high: 0.636 },
  difference: { point: 0.108, low: 0.079, high: 0.137 },
  mean_review_ece: 0.031,
  test_reviews: 1200,
  test_hotels: 182,
  rungs: [
    { name: 'tfidf-logreg', family: 'linear', test_macro_f1: 0.62, low: 0.59, high: 0.65 },
    { name: 'encoder-small', family: 'encoder', test_macro_f1: 0.712, low: 0.681, high: 0.741 },
    { name: 'v1-parts-tuned', family: 'v1', test_macro_f1: 0.604, low: 0.571, high: 0.636 },
  ],
};

export function meta(evaluation: Evaluation | null = null, analyzeAvailable = true): Meta {
  return {
    analyze_available: analyzeAvailable,
    params_version: '2026-10-24.1',
    model: 'test-model',
    hotels: 5,
    tiers: { caution: 1, excellent: 1, good: 1, not_enough_reviews: 1, not_enough_evidence: 1 },
    attributes: ORDER.map((a) => ({
      attribute: a,
      display_name: NAMES[a],
      description: `${NAMES[a]} description.`,
      essential: ['cleanliness', 'room', 'staff'].includes(a),
      family: MUST_BE.includes(a) ? 'must_be' : 'two_sided',
    })),
    policy: structuredClone(DEFAULT_POLICY),
    data_period: { first_month: '2015-09', last_month: '2017-08' },
    evaluation,
  };
}

/** A comparison where Alpha is the credible leader on cleanliness only. */
export function comparison(ids: string[]): Comparison {
  const details = ids
    .map((id) => HOTELS.find((h) => h.hotel_id === id))
    .filter((h): h is HotelDetail => h !== undefined);
  return {
    hotels: details.map(summary),
    attributes: ORDER.map((name) => {
      const values = details.map((d) => {
        const a = d.attributes.find((x) => x.attribute === name);
        if (!a) throw new Error(`missing ${name}`);
        return {
          hotel_id: d.hotel_id,
          grade: a.grade,
          observed: a.observed,
          low: a.low,
          high: a.high,
          opinions: a.family === 'two_sided' ? a.opinions : a.reviews,
        };
      });
      const leader =
        name === 'cleanliness' && ids.includes(alpha.hotel_id) && !ids.includes(charlie.hotel_id)
          ? alpha.hotel_id
          : null;
      return { attribute: name, values, leader, leader_probability: leader ? 0.999 : null };
    }),
  };
}

export const ANALYZE_OK: AnalyzeResponse = {
  model: 'test-model',
  parts: [
    {
      part: 'liked',
      characters: 32,
      labels: [
        { label: 'staff_positive', attribute: 'staff', polarity: 'positive', probability: 0.93 },
        {
          label: 'cleanliness_positive',
          attribute: 'cleanliness',
          polarity: 'positive',
          probability: 0.81,
        },
      ],
    },
    {
      part: 'disliked',
      characters: 21,
      labels: [
        { label: 'noise_negative', attribute: 'noise', polarity: 'negative', probability: 0.88 },
      ],
    },
  ],
};
