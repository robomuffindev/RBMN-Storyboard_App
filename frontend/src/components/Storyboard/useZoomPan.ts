import { useCallback, useRef, useState } from 'react';

/**
 * useZoomPan — ComfyUI-style wheel-zoom + pointer-drag pan for a canvas layer.
 *
 * The returned `transform` should be applied to a child "world" element via
 * CSS `transform: translate(x,y) scale(k)` with `transform-origin: 0 0`.
 * Bind `bind.onWheel` / `bind.onPointerDown` to the OUTER viewport element.
 */
export interface ZoomPanState {
  x: number;
  y: number;
  k: number;
}

export function useZoomPan(initial?: Partial<ZoomPanState>) {
  const [state, setState] = useState<ZoomPanState>({
    x: initial?.x ?? 40,
    y: initial?.y ?? 40,
    k: initial?.k ?? 1,
  });

  const viewportRef = useRef<HTMLDivElement | null>(null);
  const dragging = useRef(false);
  const last = useRef<{ x: number; y: number } | null>(null);
  const moved = useRef(false);

  const MIN_K = 0.15;
  const MAX_K = 3;

  const onWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    const vp = viewportRef.current;
    if (!vp) return;
    const rect = vp.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    setState((s) => {
      const factor = Math.exp(-e.deltaY * 0.0015);
      const k = Math.min(MAX_K, Math.max(MIN_K, s.k * factor));
      // keep the point under the cursor fixed
      const wx = (px - s.x) / s.k;
      const wy = (py - s.y) / s.k;
      return { k, x: px - wx * k, y: py - wy * k };
    });
  }, []);

  const onPointerDown = useCallback((e: React.PointerEvent) => {
    // Only pan on primary button / touch, and never when starting on an
    // interactive element (buttons, inputs, cards handle their own clicks).
    const target = e.target as HTMLElement;
    if (target.closest('[data-sb-interactive]')) return;
    if (e.button !== 0 && e.pointerType === 'mouse') return;
    dragging.current = true;
    moved.current = false;
    last.current = { x: e.clientX, y: e.clientY };
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
  }, []);

  const onPointerMove = useCallback((e: React.PointerEvent) => {
    if (!dragging.current || !last.current) return;
    const dx = e.clientX - last.current.x;
    const dy = e.clientY - last.current.y;
    if (Math.abs(dx) > 2 || Math.abs(dy) > 2) moved.current = true;
    last.current = { x: e.clientX, y: e.clientY };
    setState((s) => ({ ...s, x: s.x + dx, y: s.y + dy }));
  }, []);

  const endDrag = useCallback((e: React.PointerEvent) => {
    dragging.current = false;
    last.current = null;
    (e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId);
  }, []);

  const zoomBy = useCallback((factor: number) => {
    const vp = viewportRef.current;
    setState((s) => {
      const k = Math.min(MAX_K, Math.max(MIN_K, s.k * factor));
      if (!vp) return { ...s, k };
      const rect = vp.getBoundingClientRect();
      const cx = rect.width / 2;
      const cy = rect.height / 2;
      const wx = (cx - s.x) / s.k;
      const wy = (cy - s.y) / s.k;
      return { k, x: cx - wx * k, y: cy - wy * k };
    });
  }, []);

  const reset = useCallback(() => setState({ x: 40, y: 40, k: 1 }), []);

  const transform = `translate(${state.x}px, ${state.y}px) scale(${state.k})`;

  return {
    viewportRef,
    transform,
    state,
    zoomBy,
    reset,
    /** True if the last pointer sequence was a drag (suppress click). */
    didDrag: () => moved.current,
    bind: { onWheel, onPointerDown, onPointerMove, onPointerUp: endDrag, onPointerLeave: endDrag },
  };
}
