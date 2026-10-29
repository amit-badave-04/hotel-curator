import { Tooltip as BaseTooltip } from '@base-ui/react/tooltip';
import type { ReactElement } from 'react';

/** How long the pointer rests on the trigger before the tooltip shows. */
const OPEN_DELAY_MS = 300;

interface TooltipProps {
  /** Short text; it must repeat the trigger's accessible name, never add to it. */
  content: string;
  /** The trigger, e.g. an icon button that already has an aria-label. */
  children: ReactElement;
}

/**
 * A hover and focus tooltip for icon buttons. Tooltips do not open on touch,
 * so they only ever repeat what the trigger's accessible name already says;
 * explanations use InfoTip (a popover) instead.
 */
export function Tooltip({ content, children }: TooltipProps) {
  return (
    <BaseTooltip.Root>
      <BaseTooltip.Trigger delay={OPEN_DELAY_MS} render={children} />
      <BaseTooltip.Portal>
        <BaseTooltip.Positioner sideOffset={6} className="z-50">
          <BaseTooltip.Popup className="rounded-md border bg-card px-2 py-1 text-xs text-card-foreground shadow-md">
            {content}
          </BaseTooltip.Popup>
        </BaseTooltip.Positioner>
      </BaseTooltip.Portal>
    </BaseTooltip.Root>
  );
}
