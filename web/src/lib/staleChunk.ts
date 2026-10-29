/**
 * After a deploy, a tab that is still open runs the old bundle. Pages it
 * has not loaded yet now have new hashed file names, so loading one fails.
 * A reload fetches the new bundle. It happens at most once per window, so
 * a server that keeps failing shows the error page instead of reloading
 * forever.
 */
const RELOAD_KEY = 'hotel-curator:stale-chunk-reload';
const RELOAD_WINDOW_MS = 30_000;

// Chrome, Firefox and Safari word a failed dynamic import differently;
// Vite adds its own message for a stylesheet that fails to preload.
const STALE_CHUNK =
  /Failed to fetch dynamically imported module|error loading dynamically imported module|Importing a module script failed|Unable to preload CSS/i;

export function isStaleChunkError(error: unknown): boolean {
  return error instanceof Error && STALE_CHUNK.test(error.message);
}

/** Indirection so tests can watch the reload (jsdom cannot navigate). */
export const page = { reload: () => window.location.reload() };

/**
 * Reloads unless it already did in the last 30 seconds, or the browser is
 * offline (the same error then means "no connection", and a reload would
 * only show the browser's offline page).
 *
 * @returns Whether it reloaded. Without sessionStorage there is no loop
 *   guard, so it never reloads by itself.
 */
export function reloadOnce(now: number = Date.now()): boolean {
  if (!window.navigator.onLine) return false;
  try {
    const last = Number(window.sessionStorage.getItem(RELOAD_KEY));
    if (last > 0 && now - last < RELOAD_WINDOW_MS) return false;
    window.sessionStorage.setItem(RELOAD_KEY, String(now));
  } catch {
    return false;
  }
  page.reload();
  return true;
}
