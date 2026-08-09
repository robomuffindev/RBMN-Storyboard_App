/**
 * ImageLightbox — the ONE image viewer, with zoom and pan.  (v1.276.0)
 *
 * Lifted verbatim out of VNCCSNativePage.tsx, where the only implementation
 * with real zoom+pan had been sitting inlined and unexported while nine other
 * places rendered a flat "big picture on a dark background" copy.  Lorenzo's
 * ask: click any generated image, get it large, then zoom in and pan around to
 * actually review it.  That needs one component, not ten.
 *
 * Behaviour worth keeping (each of these is a bug someone already hit):
 *  - cursor-anchored zoom, clamped 0.2x-12x, so the pixel under the pointer
 *    stays under the pointer instead of the image sliding away from you;
 *  - pointer-capture drag pan, double-click to reset;
 *  - Esc closes, arrows step through a gallery when `nav` is supplied;
 *  - zoom/pan RESET when `src` changes, so each new image opens fitted;
 *  - page scroll locked while open, and the wheel listener registered
 *    NON-passively on the overlay.  React's onWheel is passive, so its
 *    preventDefault silently does nothing and the page scrolls behind the
 *    overlay.  That is the subtle one every other copy got wrong.
 *
 * Props accept `src` OR `url` because the two implementations this replaces
 * disagreed on the name; keeping both means existing call sites do not have to
 * be touched in the same change that swaps the component.
 */
import React, { useEffect, useRef, useState } from 'react';

const btnBase: React.CSSProperties = {
  padding: '6px 12px', borderRadius: 8, fontSize: 13, cursor: 'pointer',
  fontFamily: 'inherit',
};
const btnGhost: React.CSSProperties = {
  ...btnBase, background: '#161a22', border: '1px solid #2a2f3a', color: '#cbd2dc',
};
const btnGreen: React.CSSProperties = {
  ...btnBase, background: '#166534', border: '1px solid #1d7a3f', color: '#eafff933',
};

export interface LightboxVersionCtl {
  index: number; count: number; isActive: boolean;
  onPrev: () => void; onNext: () => void; onSetActive: () => void;
}
export interface LightboxNavCtl {
  index: number; count: number; onPrev: () => void; onNext: () => void;
}

export interface ImageLightboxProps {
  /** Image to show. `url` is an accepted alias. */
  src?: string;
  url?: string;
  onClose: () => void;
  /** Optional caption — usually the character / shot name. */
  title?: string;
  version?: LightboxVersionCtl;
  nav?: LightboxNavCtl;
}

export default function ImageLightbox(
  { src, url, onClose, title, version, nav }: ImageLightboxProps,
) {
  const href = src || url || '';
  const [scale, setScale] = useState(1);
  const [tx, setTx] = useState(0);
  const [ty, setTy] = useState(0);
  const [drag, setDrag] = useState<{ x: number; y: number; tx: number; ty: number } | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      else if (e.key === 'ArrowLeft') { if (nav) nav.onPrev(); else if (version) version.onPrev(); }
      else if (e.key === 'ArrowRight') { if (nav) nav.onNext(); else if (version) version.onNext(); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, nav, version]);

  // fresh image -> reset zoom/pan so each one opens fitted
  useEffect(() => { setScale(1); setTx(0); setTy(0); }, [href]);

  // Lock page scrolling and register a NON-passive wheel listener: React's
  // onWheel is passive, so preventDefault there cannot stop the page behind us.
  useEffect(() => {
    const prevBody = document.body.style.overflow;
    const prevHtml = document.documentElement.style.overflow;
    document.body.style.overflow = 'hidden';
    document.documentElement.style.overflow = 'hidden';
    const el = rootRef.current;
    const block = (e: WheelEvent) => e.preventDefault();
    el?.addEventListener('wheel', block, { passive: false });
    return () => {
      document.body.style.overflow = prevBody;
      document.documentElement.style.overflow = prevHtml;
      el?.removeEventListener('wheel', block);
    };
  }, []);

  const zoomAt = (clientX: number, clientY: number, factor: number) => {
    setScale((prev) => {
      const next = Math.min(12, Math.max(0.2, prev * factor));
      const k = next / prev;
      const cx = clientX - window.innerWidth / 2;
      const cy = clientY - window.innerHeight / 2;
      setTx((t) => cx - (cx - t) * k);
      setTy((t) => cy - (cy - t) * k);
      return next;
    });
  };

  if (!href) return null;

  return (
    <div
      ref={rootRef}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.88)', zIndex: 9995,
        overflow: 'hidden', overscrollBehavior: 'contain',
        cursor: drag ? 'grabbing' : 'grab', touchAction: 'none',
      }}
      onWheel={(e) => zoomAt(e.clientX, e.clientY, Math.exp(-e.deltaY * 0.0015))}
      onPointerDown={(e) => {
        (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
        setDrag({ x: e.clientX, y: e.clientY, tx, ty });
      }}
      onPointerMove={(e) => { if (drag) { setTx(drag.tx + e.clientX - drag.x); setTy(drag.ty + e.clientY - drag.y); } }}
      onPointerUp={() => setDrag(null)}
      onDoubleClick={() => { setScale(1); setTx(0); setTy(0); }}
    >
      <img
        src={href}
        alt={title || 'preview'}
        draggable={false}
        style={{
          position: 'absolute', left: '50%', top: '50%', maxWidth: '92vw', maxHeight: '92vh',
          transform: `translate(-50%, -50%) translate(${tx}px, ${ty}px) scale(${scale})`,
          transformOrigin: 'center center', userSelect: 'none',
        }}
      />

      {title && (
        <div style={{
          position: 'absolute', top: 16, left: 16, fontSize: 13, color: '#e6e9ee',
          fontWeight: 600, background: 'rgba(22,26,34,0.9)', border: '1px solid #2a2f3a',
          borderRadius: 8, padding: '4px 12px', maxWidth: '50vw',
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>{title}</div>
      )}

      {nav && nav.count > 1 && (
        <>
          <button
            title="Previous (←)"
            onClick={(e) => { e.stopPropagation(); nav.onPrev(); }}
            onPointerDown={(e) => e.stopPropagation()}
            onDoubleClick={(e) => e.stopPropagation()}
            style={{
              ...btnGhost, position: 'absolute', left: 14, top: '50%',
              transform: 'translateY(-50%)', background: 'rgba(22,26,34,0.9)',
              fontSize: 22, padding: '14px 16px', lineHeight: 1,
            }}
          >‹</button>
          <button
            title="Next (→)"
            onClick={(e) => { e.stopPropagation(); nav.onNext(); }}
            onPointerDown={(e) => e.stopPropagation()}
            onDoubleClick={(e) => e.stopPropagation()}
            style={{
              ...btnGhost, position: 'absolute', right: 14, top: '50%',
              transform: 'translateY(-50%)', background: 'rgba(22,26,34,0.9)',
              fontSize: 22, padding: '14px 16px', lineHeight: 1,
            }}
          >›</button>
          <div style={{
            position: 'absolute', top: 16, left: '50%', transform: 'translateX(-50%)',
            fontSize: 12, color: '#cbd2dc', fontWeight: 600, background: 'rgba(22,26,34,0.9)',
            border: '1px solid #2a2f3a', borderRadius: 12, padding: '3px 12px',
          }}>{nav.index + 1} / {nav.count}</div>
        </>
      )}

      <div
        style={{ position: 'absolute', top: 14, right: 14, display: 'flex', gap: 8 }}
        onPointerDown={(e) => e.stopPropagation()}
      >
        <button style={btnGhost} title="Zoom in"
                onClick={(e) => { e.stopPropagation(); zoomAt(window.innerWidth / 2, window.innerHeight / 2, 1.3); }}>＋</button>
        <button style={btnGhost} title="Zoom out"
                onClick={(e) => { e.stopPropagation(); zoomAt(window.innerWidth / 2, window.innerHeight / 2, 1 / 1.3); }}>－</button>
        <button style={btnGhost} title="Reset zoom"
                onClick={(e) => { e.stopPropagation(); setScale(1); setTx(0); setTy(0); }}>1:1</button>
        <a style={{ ...btnGhost, textDecoration: 'none' }} href={href} download title="Download"
           onClick={(e) => e.stopPropagation()}>⬇</a>
        <button style={btnGhost} onClick={(e) => { e.stopPropagation(); onClose(); }}>✕ Close</button>
      </div>

      {version && (
        <div
          style={{
            position: 'absolute', bottom: 40, left: '50%', transform: 'translateX(-50%)',
            display: 'flex', alignItems: 'center', gap: 10, background: '#161a22',
            border: '1px solid #2a2f3a', borderRadius: 8, padding: '6px 12px',
          }}
          onPointerDown={(e) => e.stopPropagation()}
          onDoubleClick={(e) => e.stopPropagation()}
        >
          <button style={{ ...btnGhost, padding: '2px 10px' }}
                  onClick={(e) => { e.stopPropagation(); version.onPrev(); }}>‹</button>
          <span style={{ fontSize: 13, color: '#cbd2dc', fontWeight: 600 }}>
            {version.index + 1} / {version.count}
          </span>
          <button style={{ ...btnGhost, padding: '2px 10px' }}
                  onClick={(e) => { e.stopPropagation(); version.onNext(); }}>›</button>
          {version.isActive ? (
            <span style={{ fontSize: 12, color: '#5ee08a' }}>● Active</span>
          ) : (
            <button style={{ ...btnGreen, padding: '3px 10px', fontSize: 12, color: '#eafff9' }}
                    onClick={(e) => { e.stopPropagation(); version.onSetActive(); }}>Set active</button>
          )}
        </div>
      )}

      <div style={{
        position: 'absolute', bottom: 12, left: '50%', transform: 'translateX(-50%)',
        fontSize: 12, color: '#a8b2c0',
      }}>
        scroll = zoom · drag = pan · double-click = reset · Esc = close
      </div>
    </div>
  );
}
