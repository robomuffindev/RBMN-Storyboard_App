import { useState, useRef } from 'react';

/** Color map for section labels — translucent so waveform shows through */
const sectionColorMap: Record<string, { bg: string; border: string; text: string }> = {
  intro:   { bg: 'rgba(139, 92, 246, 0.35)', border: 'rgba(139, 92, 246, 0.7)', text: '#c4b5fd' },
  verse:   { bg: 'rgba(59, 130, 246, 0.35)',  border: 'rgba(59, 130, 246, 0.7)',  text: '#93c5fd' },
  chorus:  { bg: 'rgba(34, 197, 94, 0.35)',   border: 'rgba(34, 197, 94, 0.7)',   text: '#86efac' },
  bridge:  { bg: 'rgba(234, 179, 8, 0.35)',   border: 'rgba(234, 179, 8, 0.7)',   text: '#fde68a' },
  outro:   { bg: 'rgba(239, 68, 68, 0.35)',   border: 'rgba(239, 68, 68, 0.7)',   text: '#fca5a5' },
  other:   { bg: 'rgba(107, 114, 128, 0.35)', border: 'rgba(107, 114, 128, 0.7)', text: '#d1d5db' },
};

/** Scene colors cycle through a palette */
const sceneColorPalette = [
  { bg: 'rgba(236, 72, 153, 0.3)',  border: 'rgba(236, 72, 153, 0.7)',  text: '#f9a8d4' },
  { bg: 'rgba(59, 130, 246, 0.3)',  border: 'rgba(59, 130, 246, 0.7)',  text: '#93c5fd' },
  { bg: 'rgba(34, 197, 94, 0.3)',   border: 'rgba(34, 197, 94, 0.7)',   text: '#86efac' },
  { bg: 'rgba(168, 85, 247, 0.3)',  border: 'rgba(168, 85, 247, 0.7)',  text: '#c4b5fd' },
  { bg: 'rgba(245, 158, 11, 0.3)',  border: 'rgba(245, 158, 11, 0.7)',  text: '#fcd34d' },
  { bg: 'rgba(20, 184, 166, 0.3)',  border: 'rgba(20, 184, 166, 0.7)',  text: '#5eead4' },
  { bg: 'rgba(239, 68, 68, 0.3)',   border: 'rgba(239, 68, 68, 0.7)',   text: '#fca5a5' },
  { bg: 'rgba(99, 102, 241, 0.3)',  border: 'rgba(99, 102, 241, 0.7)',  text: '#a5b4fc' },
];

export interface TimelineItem {
  id: string;
  label: string;
  start_time: number;
  end_time: number;
  type: 'section' | 'scene';
}

/** Boundary between two adjacent scenes. */
interface SceneBoundary {
  /** ID of the scene on the left. */
  leftId: string;
  /** ID of the scene on the right. */
  rightId: string;
  /** The time value at the boundary. */
  time: number;
  /** Minimum time the boundary can be dragged to (left scene's start_time + min). */
  minTime: number;
  /** Maximum time the boundary can be dragged to (right scene's end_time - min). */
  maxTime: number;
}

interface TimelineOverlayProps {
  items: TimelineItem[];
  duration: number;
  activeItemId: string | null;
  onItemClick: (item: TimelineItem) => void;
  onSeek: (time: number) => void;
  /** Called when a scene boundary is dragged to a new position. */
  onBoundaryDrag?: (leftSceneId: string, rightSceneId: string, newTime: number) => void;
}

const MIN_SCENE_DURATION = 0.5; // seconds

export default function TimelineOverlay({
  items,
  duration,
  activeItemId,
  onItemClick,
  onSeek,
  onBoundaryDrag,
}: TimelineOverlayProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [selectedBoundary, setSelectedBoundary] = useState<string | null>(null); // "leftId|rightId"
  const [draggingBoundary, setDraggingBoundary] = useState<SceneBoundary | null>(null);
  const [dragTime, setDragTime] = useState<number | null>(null);
  const isDragging = useRef(false);

  if (!items.length || duration <= 0) return null;

  // Build scene boundaries (only for scenes, sorted by start_time)
  const sceneItems = items.filter((i) => i.type === 'scene').sort((a, b) => a.start_time - b.start_time);
  const boundaries: SceneBoundary[] = [];
  for (let i = 0; i < sceneItems.length - 1; i++) {
    const left = sceneItems[i];
    const right = sceneItems[i + 1];
    boundaries.push({
      leftId: left.id,
      rightId: right.id,
      time: left.end_time,
      minTime: left.start_time + MIN_SCENE_DURATION,
      maxTime: right.end_time - MIN_SCENE_DURATION,
    });
  }

  const boundaryKey = (b: { leftId: string; rightId: string }) => `${b.leftId}|${b.rightId}`;

  // ── Boundary drag handling ─────────────────────────────────────────
  // First click selects the boundary; only starts dragging after a 4px deadzone.
  const DRAG_DEADZONE = 4; // pixels of mouse movement before drag begins

  const startBoundaryDrag = (boundary: SceneBoundary, e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();

    // Always select the boundary on mousedown
    setSelectedBoundary(boundaryKey(boundary));

    const startX = e.clientX;
    let dragStarted = false;

    const handleMouseMove = (ev: MouseEvent) => {
      if (!containerRef.current) return;

      // Only start the actual drag after exceeding the deadzone
      if (!dragStarted) {
        if (Math.abs(ev.clientX - startX) < DRAG_DEADZONE) return;
        // Cross the deadzone — begin dragging
        dragStarted = true;
        isDragging.current = true;
        setDraggingBoundary(boundary);
        setDragTime(boundary.time);
      }

      const rect = containerRef.current.getBoundingClientRect();
      const pct = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width));
      const time = pct * duration;
      const clamped = Math.max(boundary.minTime, Math.min(boundary.maxTime, time));
      setDragTime(clamped);
    };

    const handleMouseUp = (ev: MouseEvent) => {
      if (dragStarted && containerRef.current) {
        // Commit the drag
        const rect = containerRef.current.getBoundingClientRect();
        const pct = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width));
        const time = pct * duration;
        const clamped = Math.max(boundary.minTime, Math.min(boundary.maxTime, time));
        if (onBoundaryDrag) {
          onBoundaryDrag(boundary.leftId, boundary.rightId, parseFloat(clamped.toFixed(2)));
        }
      }
      // Reset drag state (selection stays)
      isDragging.current = false;
      setDraggingBoundary(null);
      setDragTime(null);
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
  };

  const handleBackgroundClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (isDragging.current) return;
    // Deselect boundary when clicking the background
    setSelectedBoundary(null);
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const percent = x / rect.width;
    onSeek(percent * duration);
  };

  // Build a map from item ID to its display boundary time (when dragging)
  const getDragAdjustedTimes = (item: TimelineItem) => {
    if (!draggingBoundary || dragTime === null) return { start: item.start_time, end: item.end_time };
    if (item.id === draggingBoundary.leftId) {
      return { start: item.start_time, end: dragTime };
    }
    if (item.id === draggingBoundary.rightId) {
      return { start: dragTime, end: item.end_time };
    }
    return { start: item.start_time, end: item.end_time };
  };

  return (
    <div
      ref={containerRef}
      className="absolute inset-0 z-10"
      onClick={handleBackgroundClick}
    >
      {items.map((item, index) => {
        const { start, end } = getDragAdjustedTimes(item);
        const startPct = (start / duration) * 100;
        const widthPct = ((end - start) / duration) * 100;
        const isActive = item.id === activeItemId;

        // Color based on type
        let colors;
        if (item.type === 'section') {
          const key = item.label.toLowerCase();
          colors = sectionColorMap[key] || sectionColorMap.other;
        } else {
          colors = sceneColorPalette[index % sceneColorPalette.length];
        }

        return (
          <div
            key={item.id}
            className="absolute top-0 bottom-0 cursor-pointer transition-none"
            style={{
              left: `${startPct}%`,
              width: `${widthPct}%`,
              backgroundColor: colors.bg,
              borderLeft: `2px solid ${colors.border}`,
              borderRight: index === items.length - 1 ? `2px solid ${colors.border}` : 'none',
              boxShadow: isActive ? `inset 0 0 0 2px ${colors.border}` : 'none',
            }}
            onClick={(e) => {
              e.stopPropagation();
              setSelectedBoundary(null);
              onItemClick(item);
              onSeek(item.start_time);
            }}
            title={`${item.label} (${start.toFixed(1)}s \u2013 ${end.toFixed(1)}s)`}
          >
            {/* Label at the bottom of the block */}
            <div
              className="absolute bottom-0 left-0 right-0 px-1.5 py-0.5 text-[10px] font-semibold truncate"
              style={{ color: colors.text, textShadow: '0 1px 3px rgba(0,0,0,0.8)' }}
            >
              {item.label}
            </div>

            {/* Active indicator — bright top border */}
            {isActive && (
              <div
                className="absolute top-0 left-0 right-0 h-0.5"
                style={{ backgroundColor: colors.border }}
              />
            )}
          </div>
        );
      })}

      {/* ── Draggable scene boundaries ───────────────────────────────── */}
      {boundaries.map((b) => {
        const bKey = boundaryKey(b);
        const isSelected = selectedBoundary === bKey;
        const isDrag = draggingBoundary && boundaryKey(draggingBoundary) === bKey;
        const displayTime = isDrag && dragTime !== null ? dragTime : b.time;
        const leftPct = (displayTime / duration) * 100;

        return (
          <div
            key={bKey}
            className="absolute top-0 bottom-0 z-20"
            style={{
              left: `${leftPct}%`,
              width: '12px',
              marginLeft: '-6px',
              cursor: 'col-resize',
            }}
            onMouseDown={(e) => startBoundaryDrag(b, e)}
          >
            {/* Visual handle */}
            <div
              style={{
                position: 'absolute',
                top: 0,
                bottom: 0,
                left: '5px',
                width: '2px',
                backgroundColor: isSelected || isDrag ? '#fbbf24' : 'rgba(255,255,255,0.3)',
                transition: isDrag ? 'none' : 'background-color 150ms',
              }}
            />
            {/* Wider glow when selected */}
            {(isSelected || isDrag) && (
              <div
                style={{
                  position: 'absolute',
                  top: 0,
                  bottom: 0,
                  left: '3px',
                  width: '6px',
                  backgroundColor: 'rgba(251, 191, 36, 0.25)',
                  borderRadius: '2px',
                }}
              />
            )}
            {/* Time tooltip when selected or dragging */}
            {(isSelected || isDrag) && (
              <div
                style={{
                  position: 'absolute',
                  top: '-18px',
                  left: '50%',
                  transform: 'translateX(-50%)',
                  backgroundColor: '#1f2937',
                  border: '1px solid #fbbf24',
                  borderRadius: '3px',
                  padding: '1px 4px',
                  fontSize: '9px',
                  fontFamily: 'monospace',
                  color: '#fbbf24',
                  whiteSpace: 'nowrap',
                  pointerEvents: 'none',
                  zIndex: 50,
                }}
              >
                {displayTime.toFixed(1)}s
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
