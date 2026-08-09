/**
 * useLightbox — wire the shared zoom/pan viewer into a panel in two lines.
 *
 *     const lb = useLightbox();
 *     <img onClick={() => lb.open(urls, i, 'Dorian — front view')} ... />
 *     {lb.node}
 *
 * Written because the alternative was copying four pieces of state and a
 * conditional render into every panel that shows a picture, which is exactly
 * how this app ended up with ten different lightboxes in the first place.
 *
 * `open` takes the whole gallery plus a starting index, so ← / → step through
 * the set rather than trapping you on the one image you happened to click.
 * Passing a single string is fine too — it becomes a one-image gallery.
 */
import React, { useCallback, useMemo, useState } from 'react';
import ImageLightbox from './ImageLightbox';

export interface LightboxHandle {
  /** Open the viewer. `urls` may be one URL or a gallery; `index` selects. */
  open: (urls: string | string[], index?: number, title?: string) => void;
  close: () => void;
  isOpen: boolean;
  /** Render this once, anywhere in the panel. */
  node: React.ReactNode;
}

export default function useLightbox(): LightboxHandle {
  const [urls, setUrls] = useState<string[]>([]);
  const [index, setIndex] = useState(0);
  const [title, setTitle] = useState<string | undefined>(undefined);

  const open = useCallback((u: string | string[], i = 0, t?: string) => {
    const list = (Array.isArray(u) ? u : [u]).filter(Boolean);
    if (!list.length) return;
    setUrls(list);
    setIndex(Math.max(0, Math.min(i, list.length - 1)));
    setTitle(t);
  }, []);

  const close = useCallback(() => { setUrls([]); setTitle(undefined); }, []);

  const node = useMemo(() => {
    const list = urls;
    if (!list.length) return null;
    const safe = Math.max(0, Math.min(index, list.length - 1));
    return (
      <ImageLightbox
        src={list[safe]}
        title={title}
        onClose={close}
        nav={list.length > 1 ? {
          index: safe,
          count: list.length,
          onPrev: () => setIndex((n) => (n - 1 + list.length) % list.length),
          onNext: () => setIndex((n) => (n + 1) % list.length),
        } : undefined}
      />
    );
  }, [urls, index, title, close]);

  return { open, close, isOpen: urls.length > 0, node };
}
