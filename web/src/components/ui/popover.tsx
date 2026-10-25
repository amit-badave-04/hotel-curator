import { Popover } from '@base-ui/react/popover';
import { Info } from 'lucide-react';
import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';

interface InfoTipProps {
  /** Accessible name of the trigger and title of the popup, e.g. "What is a likely range?". */
  label: string;
  children: ReactNode;
  className?: string;
}

/**
 * A small "i" button that explains a term. It is a popover, not a tooltip:
 * a click, tap, Enter or Space opens it (hover also does, for mouse users),
 * the popup is a labelled dialog, and Escape or a click outside closes it.
 * The About page states the same facts in full.
 */
export function InfoTip({ label, children, className }: InfoTipProps) {
  return (
    <Popover.Root>
      <Popover.Trigger
        openOnHover
        delay={300}
        aria-label={label}
        className={cn(
          'ml-1 inline-flex size-6 items-center justify-center rounded-full align-middle text-muted-foreground hover:text-foreground focus-visible:outline-3 focus-visible:outline-ring data-[popup-open]:text-foreground',
          className,
        )}
      >
        <Info aria-hidden="true" className="size-4" />
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner sideOffset={6} collisionPadding={8} className="z-50">
          <Popover.Popup className="max-w-72 space-y-1 rounded-md border bg-card px-3 py-2 text-sm text-card-foreground shadow-md focus-visible:outline-3 focus-visible:outline-ring">
            <Popover.Title className="font-semibold">{label}</Popover.Title>
            <Popover.Description>{children}</Popover.Description>
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}
