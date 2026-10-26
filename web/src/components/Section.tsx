import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';

interface SectionProps {
  /** Id of the heading; the section is labelled by it. */
  id: string;
  title: ReactNode;
  children: ReactNode;
  className?: string;
}

/** A page section labelled by its own h2, so it is listed as a region. */
export function Section({ id, title, children, className }: SectionProps) {
  return (
    <section aria-labelledby={id} className={cn('space-y-3', className)}>
      <h2 id={id} className="text-xl font-semibold tracking-tight">
        {title}
      </h2>
      {children}
    </section>
  );
}
