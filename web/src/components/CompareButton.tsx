import { Check, Plus } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { useCompare } from '@/lib/compare';
import { MAX_COMPARE } from '@/lib/domain';

interface CompareButtonProps {
  hotelId: string;
  name: string;
  size?: 'sm' | 'default';
}

/** Adds a hotel to (or removes it from) the compare list. */
export function CompareButton({ hotelId, name, size = 'sm' }: CompareButtonProps) {
  const compare = useCompare();
  const selected = compare.has(hotelId);
  const blocked = !selected && compare.isFull;
  const label = selected ? 'Remove from compare' : blocked ? 'Compare list full' : 'Add to compare';
  return (
    <Button
      variant={selected ? 'secondary' : 'outline'}
      size={size}
      disabled={blocked}
      aria-label={`${label}: ${name}`}
      title={blocked ? `The compare list holds up to ${MAX_COMPARE} hotels` : undefined}
      onClick={() => compare.toggle({ id: hotelId, name })}
    >
      {selected ? <Check aria-hidden="true" /> : <Plus aria-hidden="true" />}
      {label}
    </Button>
  );
}
