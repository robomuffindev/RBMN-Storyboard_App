/**
 * The CURRENT character — shared context across every studio tab (v1.277.10).
 *
 * Two keys, two jobs:
 *   rbmn_focus_char   — the legacy ONE-SHOT jump ("open THIS character in that
 *                       tab"). Consumed on read. Kept because every writer in
 *                       the app already uses it.
 *   rbmn_current_char — PERSISTENT. Written whenever a jump happens or the
 *                       user picks a character in any panel, never deleted.
 *
 * Why: the one-shot key made tab focus fragile three ways (all reported
 * 2026-08-14): a panel that unmounts/remounts loses it (Klein3Panel was
 * remounted by an async settings load), switching tabs INSIDE the studio
 * loses it (the key was already consumed), and the LoRA tab never read it at
 * all. His rule: "when we are inside an individual's screen of tabs, all
 * those tabs should default to that character's information."
 */
export const FOCUS_KEY = 'rbmn_focus_char';
export const CURRENT_KEY = 'rbmn_current_char';

/** Read the character a panel should open with: an explicit jump wins, else
 *  the persistent current character. Promotes a jump into the current key. */
export function consumeFocusChar(): string {
  try {
    const f = window.localStorage.getItem(FOCUS_KEY) || '';
    if (f) {
      window.localStorage.removeItem(FOCUS_KEY);
      window.localStorage.setItem(CURRENT_KEY, f);
      return f;
    }
    return window.localStorage.getItem(CURRENT_KEY) || '';
  } catch { return ''; }
}

/** Call whenever the user picks a character anywhere — other tabs follow. */
export function setCurrentChar(slug: string): void {
  try { if (slug) window.localStorage.setItem(CURRENT_KEY, slug); } catch { /* fine */ }
}

/** Jump helper for writers: set both keys before navigating. */
export function setFocusChar(slug: string): void {
  try {
    if (slug) {
      window.localStorage.setItem(FOCUS_KEY, slug);
      window.localStorage.setItem(CURRENT_KEY, slug);
    }
  } catch { /* fine */ }
}
