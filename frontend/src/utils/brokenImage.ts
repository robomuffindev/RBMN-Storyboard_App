/**
 * Shared broken-image handler — replaces a failed <img> src with a
 * neutral SVG placeholder so the user sees a meaningful message
 * instead of the browser's broken-icon glyph.
 */

/** SVG data URI placeholder shown when an image fails to load (404, missing file, etc.) */
export const BROKEN_IMG_PLACEHOLDER =
  'data:image/svg+xml,' +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="150" viewBox="0 0 200 150">' +
      '<rect fill="#1f2937" width="200" height="150" rx="8"/>' +
      '<text x="100" y="70" text-anchor="middle" fill="#6b7280" font-size="14" font-family="sans-serif">Image unavailable</text>' +
      '<text x="100" y="90" text-anchor="middle" fill="#4b5563" font-size="11" font-family="sans-serif">File may have been moved or deleted</text>' +
    '</svg>'
  );

/** Handle broken image by swapping to placeholder — prevents browser broken-icon */
export const handleImgError = (e: React.SyntheticEvent<HTMLImageElement>) => {
  const el = e.target as HTMLImageElement;
  el.onerror = null; // prevent infinite loop
  el.src = BROKEN_IMG_PLACEHOLDER;
};
