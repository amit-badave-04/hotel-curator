import type { ComponentProps } from 'react';

import { cn } from '@/lib/utils';

/**
 * A table in a horizontally scrollable region, so wide tables never make the
 * page scroll sideways on small screens. The region is focusable so keyboard
 * users can scroll it.
 */
export function Table({
  className,
  containerLabel,
  ...props
}: ComponentProps<'table'> & { containerLabel?: string }) {
  return (
    <div
      className="relative w-full overflow-x-auto rounded-md border"
      // A labelled, focusable scroll region (WCAG 2.1.1 for scrollable content).
      role={containerLabel ? 'region' : undefined}
      aria-label={containerLabel}
      tabIndex={containerLabel ? 0 : undefined}
    >
      <table
        className={cn('w-full caption-bottom border-collapse text-sm', className)}
        {...props}
      />
    </div>
  );
}

export function TableCaption({ className, ...props }: ComponentProps<'caption'>) {
  return (
    <caption
      className={cn('px-3 py-2 text-left text-xs text-muted-foreground', className)}
      {...props}
    />
  );
}

export function TableHeader({ className, ...props }: ComponentProps<'thead'>) {
  return <thead className={cn('bg-muted/60 [&_tr]:border-b', className)} {...props} />;
}

export function TableBody({ className, ...props }: ComponentProps<'tbody'>) {
  return <tbody className={cn('[&_tr:last-child]:border-0', className)} {...props} />;
}

export function TableRow({ className, ...props }: ComponentProps<'tr'>) {
  return <tr className={cn('border-b align-top', className)} {...props} />;
}

export function TableHead({ className, ...props }: ComponentProps<'th'>) {
  return (
    <th
      className={cn(
        'h-10 px-3 py-2 text-left align-bottom text-xs font-semibold tracking-wide text-muted-foreground uppercase',
        className,
      )}
      {...props}
    />
  );
}

export function TableCell({ className, ...props }: ComponentProps<'td'>) {
  return <td className={cn('px-3 py-2 align-top tabular-nums', className)} {...props} />;
}
