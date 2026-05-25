import { useState, useRef, useEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { X, Minimize2, Maximize2, Image, Video, Clock, MessageSquare } from 'lucide-react';
import { useAppStore } from '@/store';

/**
 * Floating Picture-in-Picture preview that shows the last generated asset
 * during batch processing. Draggable, resizable, with scene info overlay.
 */
export default function BatchPreviewPIP() {
  const { lastCompletedAsset, batchPreviewVisible, setBatchPreviewVisible } = useAppStore();
  const [minimized, setMinimized] = useState(false);
  const [position, setPosition] = useState({ x: 20, y: 20 });
  const [size, setSize] = useState<'small' | 'medium' | 'large'>('medium');
  const isDragging = useRef(false);
  const dragOffset = useRef({ x: 0, y: 0 });
  const containerRef = useRef<HTMLDivElement>(null);

  // Size presets (width in px)
  const sizeMap = { small: 280, medium: 420, large: 600 };
  const pipWidth = sizeMap[size];

  // Auto-position to bottom-right on first show
  useEffect(() => {
    if (batchPreviewVisible && lastCompletedAsset) {
      const w = window.innerWidth;
      const h = window.innerHeight;
      setPosition({
        x: Math.max(20, w - pipWidth - 30),
        y: Math.max(20, h - 400),
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batchPreviewVisible]);

  // Drag handlers
  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest('button')) return; // don't drag from buttons
    e.preventDefault();
    isDragging.current = true;
    dragOffset.current = {
      x: e.clientX - position.x,
      y: e.clientY - position.y,
    };

    const handleMouseMove = (ev: MouseEvent) => {
      if (!isDragging.current) return;
      setPosition({
        x: Math.max(0, Math.min(window.innerWidth - 100, ev.clientX - dragOffset.current.x)),
        y: Math.max(0, Math.min(window.innerHeight - 50, ev.clientY - dragOffset.current.y)),
      });
    };

    const handleMouseUp = () => {
      isDragging.current = false;
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
  }, [position]);

  // Touch drag for mobile
  const handleTouchStart = useCallback((e: React.TouchEvent) => {
    if ((e.target as HTMLElement).closest('button')) return;
    const touch = e.touches[0];
    isDragging.current = true;
    dragOffset.current = {
      x: touch.clientX - position.x,
      y: touch.clientY - position.y,
    };

    const handleTouchMove = (ev: TouchEvent) => {
      if (!isDragging.current) return;
      const t = ev.touches[0];
      setPosition({
        x: Math.max(0, Math.min(window.innerWidth - 100, t.clientX - dragOffset.current.x)),
        y: Math.max(0, Math.min(window.innerHeight - 50, t.clientY - dragOffset.current.y)),
      });
    };

    const handleTouchEnd = () => {
      isDragging.current = false;
      window.removeEventListener('touchmove', handleTouchMove);
      window.removeEventListener('touchend', handleTouchEnd);
    };

    window.addEventListener('touchmove', handleTouchMove, { passive: true });
    window.addEventListener('touchend', handleTouchEnd);
  }, [position]);

  if (!batchPreviewVisible || !lastCompletedAsset) return null;

  const formatDuration = (ms: number) => {
    const secs = Math.floor(ms / 1000);
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
  };

  const isVideo = lastCompletedAsset.jobType === 'video';

  return createPortal(
    <div
      ref={containerRef}
      onMouseDown={handleMouseDown}
      onTouchStart={handleTouchStart}
      style={{
        position: 'fixed',
        left: position.x,
        top: position.y,
        width: minimized ? 200 : pipWidth,
        zIndex: 10000,
        borderRadius: 12,
        overflow: 'hidden',
        boxShadow: '0 8px 32px rgba(0,0,0,0.6), 0 0 0 1px rgba(139,92,246,0.3)',
        background: '#0f0f1a',
        cursor: isDragging.current ? 'grabbing' : 'grab',
        transition: isDragging.current ? 'none' : 'width 0.2s ease',
        userSelect: 'none',
      }}
    >
      {/* Header Bar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '8px 12px',
          background: 'linear-gradient(135deg, #1e1b4b, #312e81)',
          borderBottom: '1px solid rgba(139,92,246,0.3)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          {isVideo ? <Video size={14} color="#a78bfa" /> : <Image size={14} color="#a78bfa" />}
          <span style={{ color: '#e0e0e0', fontSize: 12, fontWeight: 600 }}>
            {minimized ? 'Preview' : 'Live Batch Preview'}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          {!minimized && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                setSize(s => s === 'small' ? 'medium' : s === 'medium' ? 'large' : 'small');
              }}
              style={{ background: 'none', border: 'none', color: '#888', cursor: 'pointer', padding: 4, display: 'flex' }}
              title="Resize"
            >
              <Maximize2 size={14} />
            </button>
          )}
          <button
            onClick={(e) => {
              e.stopPropagation();
              setMinimized(m => !m);
            }}
            style={{ background: 'none', border: 'none', color: '#888', cursor: 'pointer', padding: 4, display: 'flex' }}
            title={minimized ? 'Expand' : 'Minimize'}
          >
            <Minimize2 size={14} />
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation();
              setBatchPreviewVisible(false);
            }}
            style={{ background: 'none', border: 'none', color: '#888', cursor: 'pointer', padding: 4, display: 'flex' }}
            title="Close preview"
          >
            <X size={14} />
          </button>
        </div>
      </div>

      {/* Content — hidden when minimized */}
      {!minimized && (
        <>
          {/* Asset Preview */}
          <div style={{ position: 'relative', background: '#000', minHeight: size === 'small' ? 160 : size === 'medium' ? 240 : 340 }}>
            {lastCompletedAsset.assetUrl ? (
              isVideo ? (
                <video
                  key={lastCompletedAsset.assetUrl}
                  src={lastCompletedAsset.assetUrl}
                  autoPlay
                  loop
                  muted
                  playsInline
                  style={{ width: '100%', height: '100%', objectFit: 'contain', display: 'block' }}
                />
              ) : (
                <img
                  key={lastCompletedAsset.assetUrl}
                  src={lastCompletedAsset.assetUrl}
                  alt={lastCompletedAsset.sceneName}
                  style={{ width: '100%', height: '100%', objectFit: 'contain', display: 'block' }}
                />
              )
            ) : (
              <div style={{
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                height: '100%', minHeight: 160, color: '#555', fontSize: 13,
              }}>
                No preview available
              </div>
            )}

            {/* Scene badge overlay */}
            <div style={{
              position: 'absolute', top: 8, left: 8,
              background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(4px)',
              borderRadius: 6, padding: '4px 10px',
              display: 'flex', alignItems: 'center', gap: 6,
            }}>
              <span style={{
                background: isVideo ? '#7c3aed' : '#059669',
                color: 'white', fontSize: 10, fontWeight: 700,
                padding: '2px 6px', borderRadius: 4,
              }}>
                {isVideo ? 'VIDEO' : 'IMAGE'}
              </span>
              <span style={{ color: '#e0e0e0', fontSize: 12, fontWeight: 600 }}>
                {lastCompletedAsset.sceneName}
              </span>
            </div>
          </div>

          {/* Info Bar */}
          <div style={{
            padding: '10px 12px',
            background: '#141425',
            borderTop: '1px solid rgba(255,255,255,0.06)',
          }}>
            {/* Time info */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: lastCompletedAsset.prompt ? 8 : 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                <Clock size={12} color="#60a5fa" />
                <span style={{ color: '#60a5fa', fontSize: 11, fontWeight: 500 }}>
                  {formatDuration(lastCompletedAsset.elapsedMs)}
                </span>
              </div>
              <div style={{ color: '#555', fontSize: 11 }}>
                Scene {lastCompletedAsset.sceneIndex + 1}
              </div>
              <div style={{ color: '#444', fontSize: 10 }}>
                {new Date(lastCompletedAsset.completedAt).toLocaleTimeString()}
              </div>
            </div>

            {/* Prompt snippet */}
            {lastCompletedAsset.prompt && (
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6 }}>
                <MessageSquare size={11} color="#666" style={{ marginTop: 2, flexShrink: 0 }} />
                <div style={{
                  color: '#888', fontSize: 10, lineHeight: 1.5,
                  overflow: 'hidden', textOverflow: 'ellipsis',
                  display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' as const,
                }}>
                  {lastCompletedAsset.prompt}
                </div>
              </div>
            )}
          </div>
        </>
      )}
    </div>,
    document.body
  );
}
