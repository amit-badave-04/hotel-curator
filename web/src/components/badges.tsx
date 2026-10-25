import type { Attribute, Grade, Lean, Tier } from '@/api/generated/model';
import { Badge } from '@/components/ui/badge';
import { familyOf, gradeInfo, TIER_INFO, type Family } from '@/lib/domain';
import { cn } from '@/lib/utils';
import {
  Award,
  CircleCheck,
  CircleDashed,
  CircleHelp,
  CircleMinus,
  ThumbsUp,
  TriangleAlert,
} from 'lucide-react';

const GRADE_ICONS = {
  strong: CircleCheck,
  weak: TriangleAlert,
  neutral: CircleMinus,
  unknown: CircleDashed,
} as const;

interface GradeBadgeProps {
  grade: Grade;
  /** Decides the plain meaning; inferred from the attribute when not given. */
  family?: Family;
  attribute?: Attribute;
  lean?: Lean | null;
  className?: string;
  size?: 'default' | 'lg';
}

/** A grade as text + icon + colour (never colour alone). */
export function GradeBadge({ grade, family, attribute, lean, className, size }: GradeBadgeProps) {
  const info = gradeInfo(grade, family ?? (attribute ? familyOf(attribute) : 'two_sided'), lean);
  const Icon = GRADE_ICONS[info.tone];
  return (
    <Badge tone={info.tone} size={size} className={className} title={info.meaning}>
      <Icon aria-hidden="true" />
      {info.name}
    </Badge>
  );
}

const TIER_STYLES = {
  excellent: { tone: 'strong', icon: Award },
  good: { tone: 'info', icon: ThumbsUp },
  fair: { tone: 'neutral', icon: CircleMinus },
  caution: { tone: 'weak', icon: TriangleAlert },
  unknown: { tone: 'unknown', icon: CircleHelp },
} as const;

interface TierBadgeProps {
  tier: Tier;
  /** The API label, e.g. "Good, but noisy"; shown instead of the tier name. */
  label?: string;
  size?: 'default' | 'lg';
  className?: string;
}

/**
 * The tier with its label. The two "not enough" tiers always read as "Not
 * enough evidence to judge", styled neutrally, never as a bad grade.
 */
export function TierBadge({ tier, label, size, className }: TierBadgeProps) {
  const info = TIER_INFO[tier];
  const style = TIER_STYLES[info.tone];
  const Icon = style.icon;
  const text = info.tone === 'unknown' ? info.name : (label ?? info.name);
  return (
    <Badge tone={style.tone} size={size} className={cn('whitespace-normal', className)}>
      <Icon aria-hidden="true" />
      <span>
        <span className="sr-only">Tier: </span>
        {text}
      </span>
    </Badge>
  );
}
