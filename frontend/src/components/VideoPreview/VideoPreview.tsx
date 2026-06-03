import { useRef, useState, useMemo, useEffect, useCallback } from 'react';
import { Image, Film, X, MonitorPlay } from 'lucide-react';
import { useAppStore } from '@/store';
import { handleImgError } from '@/utils/brokenImage';
import type { WordTimestamp, SrtBlock } from '@/types';

export interface SubtitleStyle {
  font?: string;
  size?: number;
  color?: string;
  position?: string;
  outline?: number;
  bold?: boolean;
}

interface VideoPreviewProps {
  assembledPreviewUrl?: string | null;
  onExitPreview?: () => void;
  words?: WordTimestamp[];
  srtBlocks?: SrtBlock[];
  subtitlesEnabled?: boolean;
  subtitleStyle?: SubtitleStyle;
}

/**
 * Canvas-based video preview with double-buffered preloading.
 *
 * Instead of swapping visible <video> elements (which causes flicker),
 * we keep two hidden video elements as decode sources and paint the
 * active frame onto a single <canvas> via requestAnimationFrame +
 * drawImage().  The canvas surface never flickers because we control
 * exactly which pixel data is painted each frame.
 *
 * This is the same approach used by CapCut, DaVinci web, and most
 * browser-based video editors.
 */
export default function VideoPreview({ assembledPreviewUrl, onExitPreview, words, srtBlocks, subtitlesEnabled, subtitleStyle }: VideoPreviewProps = {}) {
  // Debug: log subtitle state on mount and when key props change
  useEffect(() => {
    if (subtitlesEnabled) {
      console.debug(`[VideoPreview] Subtitles enabled. words=${words?.length ?? 0}, first word:`, words?.[0]);
    }
  }, [subtitlesEnabled, words?.length]);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const videoARef = useRef<HTMLVideoElement>(null);
  const videoBRef = useRef<HTMLVideoElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const rafRef = useRef<number>(0);

  // Which video buffer is active: 'A' or 'B'
  const [activeBuffer, setActiveBuffer] = useState<'A' | 'B'>('A');
  // Track what URL each buffer has loaded
  const bufferUrlA = useRef<string>('');
  const bufferUrlB = useRef<string>('');
  // Track which scene we've queued a preload for
  const preloadedSceneId = useRef<string>('');
  // Track the last painted video URL to detect source changes
  const lastPaintedUrl = useRef<string>('');

  const activeScene = useAppStore(s => s.activeScene);
  const scenes = useAppStore(s => s.scenes);
  const playbackPosition = useAppStore(s => s.playbackPosition);
  const isPlaying = useAppStore(s => s.isPlaying);
  // Narration Images mode: the deliverable is a still-image slideshow.
  // Even if a scene has a leftover chosen_video_path from before the
  // mode lock, the preview must show the image, not the video — matches
  // what export will actually render.
  //
  // Defensive default: while `currentProject` is still null (one render
  // tick before the AppLayout effect sets it), treat the mode as
  // "unknown but probably image-safe" — i.e. don't allow video playback
  // until we're certain we're NOT in narration_images mode.  This
  // prevents a one-frame race where a narration_images project briefly
  // plays a leftover video on mount.
  const currentProject = useAppStore(s => s.currentProject);
  const projectLoaded = !!currentProject;
  const forceImagesOnly = projectLoaded
    ? currentProject?.mode === 'narration_images'
    : true;

  // ─── Scene resolution ──────────────────────────────────────────
  const sceneAtPlayhead = useMemo(() => {
    if (!scenes || scenes.length === 0) return null;
    return scenes.find(
      (s) => s.start_time <= playbackPosition && s.end_time > playbackPosition
    ) || null;
  }, [scenes, playbackPosition]);

  const displayScene = sceneAtPlayhead || activeScene;

  // ─── Determine what to display ──────────────────────────────────
  // In narration_images mode, force image regardless of what's stored
  // on the scene — leftover video paths from before the mode lock
  // should not appear in the preview because they won't appear in the
  // export either.
  const rawSourceType = displayScene?.parameters?.scene_source_type || 'image';
  const sourceType = forceImagesOnly ? 'image' : rawSourceType;
  const chosenVideoPath = forceImagesOnly ? null : displayScene?.parameters?.chosen_video_path;
  const videoUrl = sourceType === 'video' && chosenVideoPath ? `/api/files/${chosenVideoPath}` : '';

  // Image paths
  const chosenFirstFramePath = displayScene?.parameters?.chosen_image_path;
  const chosenLastFramePath = displayScene?.parameters?.chosen_last_frame_path;
  const hasFirstFrame = !!chosenFirstFramePath;
  const hasLastFrame = !!chosenLastFramePath;
  const isFFLF = hasFirstFrame && hasLastFrame;

  const displayImageUrl = useMemo(() => {
    if (!displayScene || !hasFirstFrame) return '';
    if (isFFLF) {
      const sceneMidpoint = (displayScene.start_time + displayScene.end_time) / 2;
      const showLastFrame = playbackPosition >= sceneMidpoint;
      const path = showLastFrame ? chosenLastFramePath : chosenFirstFramePath;
      return `/api/files/${path}`;
    }
    return `/api/files/${chosenFirstFramePath}`;
  }, [displayScene, chosenFirstFramePath, chosenLastFramePath, hasFirstFrame, isFFLF, playbackPosition]);

  const showVideo = sourceType === 'video' && !!videoUrl;
  const showImage = !showVideo && !!displayImageUrl;
  const showEmpty = !showVideo && !showImage;

  const frameLabel = useMemo(() => {
    if (!isFFLF || !displayScene) return null;
    const sceneMidpoint = (displayScene.start_time + displayScene.end_time) / 2;
    return playbackPosition >= sceneMidpoint ? 'Last Frame' : 'First Frame';
  }, [isFFLF, displayScene, playbackPosition]);

  const sceneName = displayScene
    ? (displayScene.name || `Scene ${displayScene.order_index + 1}`)
    : null;

  // ─── CSS Animation for image movement effects ──────────────────────
  const imageMovementStyle = useMemo(() => {
    if (!displayScene || sourceType !== 'image') return {};
    const movement = displayScene.parameters?.image_movement;
    if (!movement || movement.effect === 'none') return {};

    const intensity = (movement.intensity ?? 50) / 100;
    const sceneDuration = (displayScene.end_time || 0) - (displayScene.start_time || 0);
    const duration = Math.max(sceneDuration, 3);

    const easing: Record<string, string> = {
      linear: 'linear',
      ease_in: 'ease-in',
      ease_out: 'ease-out',
      ease_in_out: 'ease-in-out',
    };
    const easingValue = easing[movement.easing || 'ease_in_out'] || 'ease-in-out';

    const maxScale = 1 + 0.5 * intensity;
    const panPercent = 10 * intensity;

    const effects: Record<string, { from: string; to: string }> = {
      zoom_in_center: { from: 'scale(1)', to: `scale(${maxScale})` },
      zoom_out_center: { from: `scale(${maxScale})`, to: 'scale(1)' },
      zoom_in_top_left: {
        from: 'scale(1) translate(0, 0)',
        to: `scale(${maxScale}) translate(-${panPercent / 2}%, -${panPercent / 2}%)`,
      },
      zoom_in_top_right: {
        from: 'scale(1) translate(0, 0)',
        to: `scale(${maxScale}) translate(${panPercent / 2}%, -${panPercent / 2}%)`,
      },
      zoom_in_bottom_left: {
        from: 'scale(1) translate(0, 0)',
        to: `scale(${maxScale}) translate(-${panPercent / 2}%, ${panPercent / 2}%)`,
      },
      zoom_in_bottom_right: {
        from: 'scale(1) translate(0, 0)',
        to: `scale(${maxScale}) translate(${panPercent / 2}%, ${panPercent / 2}%)`,
      },
      pan_left: { from: `scale(1.2) translateX(${panPercent}%)`, to: `scale(1.2) translateX(-${panPercent}%)` },
      pan_right: { from: `scale(1.2) translateX(-${panPercent}%)`, to: `scale(1.2) translateX(${panPercent}%)` },
      pan_up: { from: `scale(1.2) translateY(${panPercent}%)`, to: `scale(1.2) translateY(-${panPercent}%)` },
      pan_down: { from: `scale(1.2) translateY(-${panPercent}%)`, to: `scale(1.2) translateY(${panPercent}%)` },
      pan_left_to_right: { from: `scale(1.2) translateX(-${panPercent}%)`, to: `scale(1.2) translateX(${panPercent}%)` },
      pan_right_to_left: { from: `scale(1.2) translateX(${panPercent}%)`, to: `scale(1.2) translateX(-${panPercent}%)` },
      zoom_in_pan_left: { from: 'scale(1) translateX(0)', to: `scale(${maxScale}) translateX(-${panPercent}%)` },
      zoom_in_pan_right: { from: 'scale(1) translateX(0)', to: `scale(${maxScale}) translateX(${panPercent}%)` },
      zoom_out_pan_left: { from: `scale(${maxScale}) translateX(0)`, to: `scale(1) translateX(-${panPercent}%)` },
      zoom_out_pan_right: { from: `scale(${maxScale}) translateX(0)`, to: `scale(1) translateX(${panPercent}%)` },
    };

    const fx = effects[movement.effect];
    if (!fx) return {};

    return {
      animationName: `kb-${movement.effect}`,
      animationDuration: `${duration}s`,
      animationTimingFunction: easingValue,
      animationIterationCount: 'infinite',
      animationDirection: 'alternate',
      '--kb-from': fx.from,
      '--kb-to': fx.to,
    } as React.CSSProperties;
  }, [displayScene, sourceType]);

  const hasMovementEffect = Object.keys(imageMovementStyle).length > 0;

  // ─── Helpers ──────────────────────────────────────────────────────
  const getVideoRef = useCallback((buf: 'A' | 'B') => buf === 'A' ? videoARef : videoBRef, []);
  const getBufferUrl = useCallback((buf: 'A' | 'B') => buf === 'A' ? bufferUrlA : bufferUrlB, []);

  // ─── Find the NEXT scene that has a video ──────────────────────────
  const nextVideoScene = useMemo(() => {
    // In narration_images mode there are no video scenes to preload.
    if (forceImagesOnly) return null;
    if (!displayScene || !scenes) return null;
    const currentIdx = scenes.findIndex(s => s.id === displayScene.id);
    if (currentIdx < 0) return null;
    for (let i = currentIdx + 1; i < scenes.length; i++) {
      const s = scenes[i];
      if (s.parameters?.scene_source_type === 'video' && s.parameters?.chosen_video_path) {
        return s;
      }
    }
    return null;
  }, [displayScene, scenes, forceImagesOnly]);

  // ─── Double-buffer: load current video + swap if preloaded ─────────
  useEffect(() => {
    if (!videoUrl) return;

    const currentBufUrl = getBufferUrl(activeBuffer);
    if (currentBufUrl.current === videoUrl) return;

    const otherBuffer = activeBuffer === 'A' ? 'B' : 'A';
    const otherBufUrl = getBufferUrl(otherBuffer);

    if (otherBufUrl.current === videoUrl) {
      setActiveBuffer(otherBuffer);
      return;
    }

    const el = getVideoRef(activeBuffer).current;
    if (el) {
      el.src = videoUrl;
      el.load();
      currentBufUrl.current = videoUrl;
    }
  }, [videoUrl, activeBuffer, getVideoRef, getBufferUrl]);

  // ─── Preload the next scene's video into the inactive buffer ───────
  useEffect(() => {
    if (!nextVideoScene) return;

    const nextPath = nextVideoScene.parameters?.chosen_video_path;
    if (!nextPath) return;
    const nextUrl = `/api/files/${nextPath}`;

    if (preloadedSceneId.current === nextVideoScene.id) return;

    const inactiveBuffer = activeBuffer === 'A' ? 'B' : 'A';
    const inactiveBufUrl = getBufferUrl(inactiveBuffer);

    if (inactiveBufUrl.current === nextUrl) {
      preloadedSceneId.current = nextVideoScene.id as string;
      return;
    }

    const el = getVideoRef(inactiveBuffer).current;
    if (el) {
      el.src = nextUrl;
      el.load();
      el.muted = true;
      inactiveBufUrl.current = nextUrl;
      preloadedSceneId.current = nextVideoScene.id as string;
    }
  }, [nextVideoScene, activeBuffer, getVideoRef, getBufferUrl]);

  // ─── Sync active video with timeline playback ──────────────────────
  useEffect(() => {
    const el = getVideoRef(activeBuffer).current;
    if (!el || !videoUrl || !displayScene) return;

    const sceneStart = displayScene.start_time || 0;
    const sceneDuration = (displayScene.end_time || 0) - sceneStart;
    if (sceneDuration <= 0) return;

    const sceneOffset = playbackPosition - sceneStart;
    const videoDuration = el.duration;
    if (!videoDuration || isNaN(videoDuration)) return;

    const videoTime = Math.max(0, Math.min(videoDuration, (sceneOffset / sceneDuration) * videoDuration));

    if (isPlaying) {
      if (Math.abs(el.currentTime - videoTime) > 0.5) {
        el.currentTime = videoTime;
      }
      if (el.paused) {
        el.muted = true;
        el.play().catch(() => {});
      }
    } else {
      el.pause();
      if (Math.abs(el.currentTime - videoTime) > 0.1) {
        el.currentTime = videoTime;
      }
    }
  }, [playbackPosition, isPlaying, videoUrl, displayScene, activeBuffer, getVideoRef]);

  // ─── Pause inactive buffer ─────────────────────────────────────────
  useEffect(() => {
    const inactiveBuffer = activeBuffer === 'A' ? 'B' : 'A';
    const el = getVideoRef(inactiveBuffer).current;
    if (el && !el.paused) el.pause();
  }, [activeBuffer, getVideoRef]);

  // ─── Canvas paint loop ─────────────────────────────────────────────
  // This is the core of flicker-free rendering.  We paint the active
  // video's current frame onto the canvas every animation frame.  The
  // canvas content is only updated when we explicitly call drawImage(),
  // so there's never a blank/black frame between scenes.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let running = true;

    const paint = () => {
      if (!running) return;

      const el = getVideoRef(activeBuffer).current;
      const currentUrl = getBufferUrl(activeBuffer).current;

      // Only paint if we have a video scene and the video has data
      if (showVideo && el && el.readyState >= 2 && el.videoWidth > 0) {
        // Resize canvas to match video dimensions (only when they change)
        if (canvas.width !== el.videoWidth || canvas.height !== el.videoHeight) {
          canvas.width = el.videoWidth;
          canvas.height = el.videoHeight;
        }
        ctx.drawImage(el, 0, 0, canvas.width, canvas.height);
        lastPaintedUrl.current = currentUrl;
      }
      // If the video isn't ready yet, keep the last painted frame
      // (canvas retains its content until we clear it)

      rafRef.current = requestAnimationFrame(paint);
    };

    rafRef.current = requestAnimationFrame(paint);

    return () => {
      running = false;
      cancelAnimationFrame(rafRef.current);
    };
  }, [showVideo, activeBuffer, getVideoRef, getBufferUrl]);

  // ─── Clear canvas when switching away from video to image/empty ────
  useEffect(() => {
    if (!showVideo && lastPaintedUrl.current) {
      lastPaintedUrl.current = '';
      // Don't clear — let the last frame persist until the image loads on top
    }
  }, [showVideo]);

  // ─── Handle scene changes ─────────────────────────────────────────
  // When the scene changes, reset the paint tracker so the paint loop
  // will draw the new video's frame as soon as it's decoded.
  // We do NOT clear the canvas — the old frame acts as a "hold frame"
  // until new content is ready, preventing a black flash.
  // We only clear if the new scene has no content at all.
  const prevSceneIdRef = useRef<string>('');
  useEffect(() => {
    const currentSceneId = displayScene?.id as string || '';
    if (prevSceneIdRef.current && currentSceneId !== prevSceneIdRef.current) {
      // Reset paint tracker so next frame from new source will be drawn
      lastPaintedUrl.current = '';

      // Only clear canvas if new scene has no video or image content
      if (showEmpty) {
        const canvas = canvasRef.current;
        if (canvas) {
          const ctx = canvas.getContext('2d');
          if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
        }
      }
    }
    prevSceneIdRef.current = currentSceneId;
  }, [displayScene?.id, showEmpty]);

  // ─── Assembled preview mode ─────────────────────────────────────
  if (assembledPreviewUrl) {
    return (
      <div className="h-full flex flex-col bg-gray-950 rounded-lg overflow-hidden border border-gray-800">
        <div className="flex-1 flex items-center justify-center bg-black relative overflow-hidden">
          <video
            src={assembledPreviewUrl}
            controls
            autoPlay
            className="h-full max-w-full object-contain"
          />
          {onExitPreview && (
            <button
              onClick={onExitPreview}
              className="absolute top-3 right-3 flex items-center gap-1.5 text-xs text-gray-200 bg-black/70 hover:bg-black/90 px-3 py-2 rounded transition-colors"
            >
              <X size={14} />
              Exit Preview
            </button>
          )}
          <div className="absolute top-3 left-3 flex items-center gap-1.5 text-xs text-emerald-300 bg-black/60 px-2.5 py-1.5 rounded font-medium">
            <MonitorPlay size={12} />
            Assembled Preview
          </div>
        </div>
      </div>
    );
  }

  // ─── Layer stack ───────────────────────────────────────────────────
  // z-index 3: overlays (scene name, badges, empty state)
  // z-index 2: canvas (video frames painted here) — visible during video scenes
  // z-index 1: image layer — visible during image scenes
  // Hidden: video A & B elements (off-screen, used as decode sources only)

  return (
    <div className="h-full flex flex-col bg-gray-950 rounded-lg overflow-hidden border border-gray-800">
      {hasMovementEffect && (
        <style>{`
          @keyframes kb-movement {
            from { transform: var(--kb-from); }
            to { transform: var(--kb-to); }
          }
        `}</style>
      )}
      <div ref={containerRef} className="flex-1 flex items-center justify-center bg-black relative overflow-hidden">

        {/* ── Image layer (z-index 1) ── */}
        {displayImageUrl && (
          <img
            src={displayImageUrl}
            alt="Generated preview"
            className="h-full max-w-full object-contain absolute inset-0 m-auto"
            onError={handleImgError}
            style={{
              zIndex: 1,
              opacity: (showImage || showEmpty) ? 1 : 0,
              pointerEvents: 'none',
              ...(hasMovementEffect && showImage ? { ...imageMovementStyle, animationName: 'kb-movement' } : {}),
            }}
          />
        )}

        {/* ── Canvas display surface (z-index 2) ── */}
        <canvas
          ref={canvasRef}
          className="h-full max-w-full object-contain absolute inset-0 m-auto"
          style={{
            zIndex: 2,
            opacity: showVideo ? 1 : 0,
            pointerEvents: 'none',
          }}
        />

        {/* ── Hidden video decode sources (off-screen) ── */}
        <video
          ref={videoARef}
          style={{ position: 'absolute', top: -9999, left: -9999, width: 1, height: 1 }}
          muted
          playsInline
        />
        <video
          ref={videoBRef}
          style={{ position: 'absolute', top: -9999, left: -9999, width: 1, height: 1 }}
          muted
          playsInline
        />

        {/* ── Overlays (z-index 3) ── */}
        {sceneName && (
          <div className="absolute top-4 left-4 text-xs text-gray-200 bg-black/60 px-3 py-1.5 rounded font-medium" style={{ zIndex: 3 }}>
            {sceneName}
          </div>
        )}

        {showVideo && (
          <div className="absolute top-4 right-4 flex items-center gap-1.5 text-xs text-blue-300 bg-black/60 px-2.5 py-1.5 rounded font-medium" style={{ zIndex: 3 }}>
            <Film size={12} />
            Video
          </div>
        )}

        {showImage && (
          <div className="absolute bottom-4 left-4 flex items-center gap-2" style={{ zIndex: 3 }}>
            <div className="flex items-center gap-2 text-xs text-gray-300 bg-black/50 px-3 py-2 rounded">
              <Image size={14} />
              {hasMovementEffect
                ? `Image | ${displayScene?.parameters?.image_movement?.effect?.split('_').join(' ') || 'Movement'}`
                : frameLabel || 'Still Image'}
            </div>
            {isFFLF && (
              <div className="text-xs text-gray-400 bg-black/50 px-2 py-2 rounded">
                FF / LF
              </div>
            )}
          </div>
        )}

        {showEmpty && (
          <div className="text-center text-gray-400" style={{ zIndex: 3 }}>
            <p className="text-sm">No preview available</p>
            <p className="text-xs text-gray-500 mt-2">Generate an image and save it as preview</p>
          </div>
        )}

        {/* ── Subtitle overlay (z-index 4) ── */}
        {subtitlesEnabled && ((srtBlocks && srtBlocks.length > 0) || (words && words.length > 0)) && (
          <SubtitleOverlay words={words} srtBlocks={srtBlocks} style={subtitleStyle} />
        )}
        {subtitlesEnabled && (!words || words.length === 0) && (
          <div className="absolute bottom-8 left-1/2 -translate-x-1/2 max-w-[80%] text-center pointer-events-none" style={{ zIndex: 4 }}>
            <span className="inline-block px-3 py-1.5 rounded text-xs text-yellow-400 bg-black/75">
              Subtitles enabled — no word timestamps loaded. Run Whisper or upload SRT.
            </span>
          </div>
        )}
        {/* Debug: always-visible position indicator when subtitles enabled with words */}
        {subtitlesEnabled && ((srtBlocks && srtBlocks.length > 0) || (words && words.length > 0)) && (
          <div className="absolute top-4 right-4 text-[10px] text-gray-500 bg-black/40 px-2 py-1 rounded pointer-events-none" style={{ zIndex: 5 }}>
            SUB: {srtBlocks && srtBlocks.length > 0 ? `${srtBlocks.length} blocks` : `${words?.length || 0}w`} | pos: {playbackPosition.toFixed(1)}s
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Shows subtitles at the bottom of the preview.
 * Uses pre-built SRT blocks from the backend when available (simple time-range match).
 * Falls back to word-level grouping (~6 words) for Whisper-sourced words.
 */
function SubtitleOverlay({ words, srtBlocks, style }: { words?: WordTimestamp[]; srtBlocks?: SrtBlock[]; style?: SubtitleStyle }) {
  const playbackPosition = useAppStore((s) => s.playbackPosition);

  const lines = useMemo(() => {
    try {
      // Priority 1: Use pre-built SRT blocks from the backend — already grouped correctly
      if (srtBlocks && Array.isArray(srtBlocks) && srtBlocks.length > 0) {
        const result = srtBlocks.map(b => ({
          text: String(b.text || ''),
          start: Number(b.start) || 0,
          end: Number(b.end) || 0,
        })).filter(b => b.text.trim());
        if (result.length > 0) {
          console.debug(`[SubtitleOverlay] Using ${result.length} SRT blocks directly. First: "${result[0].text}" [${result[0].start.toFixed(2)}-${result[0].end.toFixed(2)}s]`);
        }
        return result;
      }

      // Priority 2: Fall back to word-level grouping for Whisper words
      if (!words || !Array.isArray(words) || words.length === 0) return [];

      const result: { text: string; start: number; end: number }[] = [];
      let currentWords: WordTimestamp[] = [];

      const flushLine = () => {
        if (currentWords.length === 0) return;
        const startVal = Number(currentWords[0]?.start) || 0;
        const endVal = Number(currentWords[currentWords.length - 1]?.end) || 0;
        result.push({
          text: currentWords.map(w => String(w?.word || '')).join(' '),
          start: startVal,
          end: endVal,
        });
        currentWords = [];
      };

      for (let i = 0; i < words.length; i++) {
        const w = words[i];
        if (!w) continue;
        if (currentWords.length > 0) {
          const prevEnd = Number(currentWords[currentWords.length - 1]?.end) || 0;
          const curStart = Number(w?.start) || 0;
          const gap = curStart - prevEnd;
          if (gap > 0.3 || currentWords.length >= 6) {
            flushLine();
          }
        }
        currentWords.push(w);
      }
      flushLine();

      if (result.length > 0) {
        console.debug(`[SubtitleOverlay] ${result.length} lines (word groups). First: "${result[0].text}" [${result[0].start.toFixed(2)}-${result[0].end.toFixed(2)}s]`);
      }
      return result;
    } catch (e) {
      console.error('[SubtitleOverlay] Error building subtitle lines:', e);
      return [];
    }
  }, [srtBlocks, words]);

  const currentLine = useMemo(() => {
    if (!lines || lines.length === 0) return null;
    // Find the line whose time range contains the playback position
    const exact = lines.find(l => l.start <= playbackPosition && l.end >= playbackPosition);
    if (exact) return exact;

    // Fallback 1: if we're within 0.5s before the next line starts, show it early
    const upcoming = lines.find(l => l.start > playbackPosition && l.start - playbackPosition <= 0.5);
    if (upcoming) return upcoming;

    // Fallback 2: show the most recently passed line for up to 1s after it ends
    // This keeps subtitles visible in gaps between lines
    const recent = [...lines]
      .filter(l => l.end < playbackPosition && playbackPosition - l.end <= 1.0)
      .sort((a, b) => b.end - a.end);
    if (recent.length > 0) return recent[0];

    // Fallback 3: if playback is before the first line, show nothing
    // If playback is after all lines, show the last line briefly
    if (lines.length > 0 && playbackPosition > 0) {
      const last = lines[lines.length - 1];
      if (playbackPosition >= last.start && playbackPosition <= last.end + 2.0) {
        return last;
      }
    }

    return null;
  }, [lines, playbackPosition]);

  if (!currentLine) return null;

  const font = style?.font || 'Arial';
  const size = style?.size || 24;
  const color = style?.color || '#FFFFFF';
  const position = style?.position || 'bottom';
  const outline = style?.outline ?? 2;
  const bold = style?.bold ?? false;

  // Scale font size relative to the preview container (base 24 ≈ 14px in preview)
  const scaledSize = Math.max(10, Math.round(size * 0.6));

  const positionClasses = position === 'top'
    ? 'absolute top-8 left-1/2 -translate-x-1/2'
    : position === 'center'
    ? 'absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2'
    : 'absolute bottom-8 left-1/2 -translate-x-1/2';

  return (
    <div
      className={`${positionClasses} max-w-[80%] text-center pointer-events-none`}
      style={{ zIndex: 4 }}
    >
      <span
        className="inline-block px-3 py-1.5 rounded font-medium"
        style={{
          fontFamily: font,
          fontSize: `${scaledSize}px`,
          fontWeight: bold ? 'bold' : 'normal',
          color: color,
          backgroundColor: 'rgba(0,0,0,0.75)',
          textShadow: outline > 0
            ? `${outline}px ${outline}px 0 #000, -${outline}px -${outline}px 0 #000, ${outline}px -${outline}px 0 #000, -${outline}px ${outline}px 0 #000`
            : 'none',
        }}
      >
        {currentLine.text}
      </span>
    </div>
  );
}
