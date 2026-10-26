import { Button as BaseButton } from '@base-ui/react/button';
import { cva, type VariantProps } from 'class-variance-authority';
import type { ComponentProps } from 'react';

import { cn } from '@/lib/utils';

export const buttonVariants = cva(
  'inline-flex shrink-0 items-center justify-center gap-2 rounded-md text-sm font-medium whitespace-nowrap transition-colors focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:pointer-events-none disabled:opacity-60 data-[disabled]:pointer-events-none data-[disabled]:opacity-60 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        default: 'bg-primary text-primary-foreground hover:bg-primary-hover',
        outline: 'border border-input bg-card text-foreground hover:bg-muted',
        secondary: 'bg-muted text-foreground hover:bg-accent',
        ghost: 'text-foreground hover:bg-muted',
        link: 'text-link underline underline-offset-4 hover:no-underline',
      },
      size: {
        default: 'min-h-10 px-4 py-2',
        sm: 'min-h-9 px-3 py-1.5',
        lg: 'min-h-11 px-6 py-2.5 text-base',
        icon: 'size-10',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  },
);

export type ButtonProps = ComponentProps<typeof BaseButton> & VariantProps<typeof buttonVariants>;

export function Button({ className, variant, size, ...props }: ButtonProps) {
  return <BaseButton className={cn(buttonVariants({ variant, size }), className)} {...props} />;
}
