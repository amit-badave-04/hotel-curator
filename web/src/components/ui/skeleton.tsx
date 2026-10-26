import type { ComponentProps } from 'react';

import { cn } from '@/lib/utils';

/** A placeholder block; hidden from assistive technology (announce loading separately). */
export function Skeleton({ className, ...props }: ComponentProps<'div'>) {
  return (
    <div
      aria-hidden="true"
      className={cn('animate-pulse rounded-md bg-muted motion-reduce:animate-none', className)}
      {...props}
    />
  );
}
