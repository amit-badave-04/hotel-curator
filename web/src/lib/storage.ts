/**
 * localStorage for per-browser conveniences only (theme, compare list).
 * Storage can be missing or throw (private windows, blocked site data), so
 * every access is guarded and the app works without it.
 */
export function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writeStorage(key: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    // Not persisted; the in-memory state still works for this visit.
  }
}
