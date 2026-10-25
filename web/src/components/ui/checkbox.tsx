import { Checkbox as BaseCheckbox } from '@base-ui/react/checkbox';
import { Check } from 'lucide-react';
import type { ComponentProps } from 'react';

import { cn } from '@/lib/utils';

export type CheckboxProps = ComponentProps<typeof BaseCheckbox.Root>;

/** A checkbox on Base UI's primitive; wrap it in a <label> with its text. */
export function Checkbox({ className, ...props }: CheckboxProps) {
  return (
    <BaseCheckbox.Root
      className={cn(
        'peer inline-flex size-5 shrink-0 items-center justify-center rounded border border-input bg-card text-primary-foreground focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-ring data-[checked]:border-primary data-[checked]:bg-primary data-[disabled]:opacity-60',
        className,
      )}
      {...props}
    >
      <BaseCheckbox.Indicator className="flex items-center justify-center data-[unchecked]:hidden">
        <Check aria-hidden="true" className="size-3.5" strokeWidth={3} />
      </BaseCheckbox.Indicator>
    </BaseCheckbox.Root>
  );
}
