/**
 * The compare list: up to four hotels picked on the explorer or hotel pages.
 * The compare page itself reads its hotels from the URL (`?ids=a&ids=b`), so
 * a comparison can be shared; this list only remembers picks between pages.
 */
import { createContext, use, useCallback, useMemo, useState, type ReactNode } from 'react';

import { HOTEL_ID_PATTERN, MAX_COMPARE } from './domain';
import { readStorage, writeStorage } from './storage';

export interface CompareItem {
  id: string;
  name: string;
}

const STORAGE_KEY = 'hotel-curator:compare';

function load(): CompareItem[] {
  const raw = readStorage(STORAGE_KEY);
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(
        (item): item is CompareItem =>
          typeof item === 'object' &&
          item !== null &&
          typeof (item as CompareItem).id === 'string' &&
          HOTEL_ID_PATTERN.test((item as CompareItem).id) &&
          typeof (item as CompareItem).name === 'string',
      )
      .slice(0, MAX_COMPARE);
  } catch {
    return [];
  }
}

/** `/compare?ids=a&ids=b` */
export function compareHref(ids: readonly string[]): string {
  const params = new URLSearchParams();
  for (const id of ids) params.append('ids', id);
  const query = params.toString();
  return query ? `/compare?${query}` : '/compare';
}

/** Valid, distinct hotel ids from the URL, at most four. */
export function parseCompareIds(params: URLSearchParams): { ids: string[]; dropped: number } {
  const all = params.getAll('ids');
  const ids = [...new Set(all.filter((id) => HOTEL_ID_PATTERN.test(id)))];
  return {
    ids: ids.slice(0, MAX_COMPARE),
    dropped: all.length - Math.min(ids.length, MAX_COMPARE),
  };
}

interface CompareContextValue {
  items: CompareItem[];
  has: (id: string) => boolean;
  isFull: boolean;
  toggle: (item: CompareItem) => void;
  remove: (id: string) => void;
  replace: (items: CompareItem[]) => void;
}

const CompareContext = createContext<CompareContextValue | null>(null);

export function CompareProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<CompareItem[]>(load);

  const update = useCallback((next: (current: CompareItem[]) => CompareItem[]) => {
    setItems((current) => {
      const proposed = next(current);
      // Return the same array when nothing changed, so React skips the update.
      if (proposed === current) return current;
      const value = proposed.length > MAX_COMPARE ? proposed.slice(0, MAX_COMPARE) : proposed;
      writeStorage(STORAGE_KEY, value.length > 0 ? JSON.stringify(value) : null);
      return value;
    });
  }, []);

  // The actions keep their identity when the list changes: effects that
  // depend on them (the compare page's sync) must not re-run on every edit.
  const toggle = useCallback(
    (item: CompareItem) =>
      update((current) =>
        current.some((c) => c.id === item.id)
          ? current.filter((c) => c.id !== item.id)
          : current.length >= MAX_COMPARE
            ? current
            : [...current, item],
      ),
    [update],
  );
  const remove = useCallback(
    (id: string) =>
      update((current) =>
        current.some((c) => c.id === id) ? current.filter((c) => c.id !== id) : current,
      ),
    [update],
  );
  const replace = useCallback(
    (next: CompareItem[]) =>
      update((current) => {
        const same =
          current.length === next.length &&
          current.every((c, i) => c.id === next[i]?.id && c.name === next[i]?.name);
        return same ? current : next;
      }),
    [update],
  );

  const value = useMemo<CompareContextValue>(
    () => ({
      items,
      has: (id) => items.some((item) => item.id === id),
      isFull: items.length >= MAX_COMPARE,
      toggle,
      remove,
      replace,
    }),
    [items, toggle, remove, replace],
  );

  return <CompareContext value={value}>{children}</CompareContext>;
}

export function useCompare(): CompareContextValue {
  const value = use(CompareContext);
  if (!value) throw new Error('useCompare must be used inside CompareProvider');
  return value;
}
