/**
 * Timestamp utilities.
 *
 * Backend uses `datetime.utcnow().isoformat()` which produces strings WITHOUT
 * a trailing `Z`. JavaScript's `new Date(...)` then interprets the string as
 * LOCAL time instead of UTC — every elapsed/relative computation is off by
 * the local TZ offset (often 5–9 hours).
 *
 * Use `parseBackendDate(ts)` (or `parseBackendMs(ts)`) instead of `new Date(ts)`
 * for any ISO timestamp coming from the backend.
 *
 * See feedback_comfyui_gotchas memory note #46.
 */

/** Normalize a backend ISO timestamp by appending `Z` if it's missing. */
export function normalizeBackendIso(ts: string | null | undefined): string | null {
  if (!ts) return null;
  return ts.endsWith("Z") ? ts : ts + "Z";
}

/** Parse a backend ISO timestamp as a Date. Returns null for empty input. */
export function parseBackendDate(ts: string | null | undefined): Date | null {
  const n = normalizeBackendIso(ts);
  return n ? new Date(n) : null;
}

/** Parse a backend ISO timestamp as milliseconds since epoch. Returns null. */
export function parseBackendMs(ts: string | null | undefined): number | null {
  const d = parseBackendDate(ts);
  return d ? d.getTime() : null;
}
