import { cva, type VariantProps } from 'class-variance-authority';
import type { ComponentProps } from 'react';

import { cn } from '@/lib/utils';

export const badgeVariants = cva(
  'inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap [&_svg]:size-3.5 [&_svg]:shrink-0',
  {
    variants: {
      tone: {
        neutral: 'border-ok-border bg-ok-bg text-ok',
        strong: 'border-strong-border bg-strong-bg text-strong',
        weak: 'border-weak-border bg-weak-bg text-weak',
        unknown: 'border-dashed border-unknown-border bg-unknown-bg text-unknown',
        info: 'border-info-border bg-info-bg text-info',
        leader: 'border-leader-border bg-leader-bg text-leader-foreground',
        outline: 'border-border bg-transparent text-foreground',
      },
      size: {
        default: '',
        lg: 'px-2.5 py-1 text-sm [&_svg]:size-4',
      },
    },
    defaultVariants: {
      tone: 'neutral',
      size: 'default',
    },
  },
);

export type BadgeProps = ComponentProps<'span'> & VariantProps<typeof badgeVariants>;

export function Badge({ className, tone, size, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone, size }), className)} {...props} />;
}
