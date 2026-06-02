import { useState, useCallback, useRef } from 'react';
import { useAppStore } from '@/store';
import WaveformDisplay from '@/components/Timeline/WaveformDisplay';
import TimelineOverlay from '@/components/Timeline/TimelineOverlay';
import { suggestTimeline, sliceSceneAudio, getScenes, cleanupScenes, updateProject, retrimAllScenes } from '@/api/client';
import { Play, Pause, ZoomIn, ZoomOut, Scissors, SkipBack, SkipForward, RotateCcw, RefreshCw, Trash2, Wand2, Lock, Unlock, Music, Zap } from 'lucide-react';

interface TimelineProps {
  onSplitScene?: (sceneId: string, splitTime: number) => void;
  onBoundaryDrag?: (leftSceneId: string, rightSceneId: string, newTime: number) => void;
  onDeleteScene?: () => void;
}

export default function Timeline({ onSplitScene, onBoundaryDrag, onDeleteScene }: TimelineProps) {
  const [duration, setDuration] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [isSuggesting, setIsSuggesting] = useState(false);
  const [isSlicingAudio, setIsSlicingAudio] = useState(false);
  const [isCleaning, setIsCleaning] = useState(false);
  const [isRetrimming, setIsRetrimming] = useState(false);
  const playbackPosition = useAppStore(s => s.playbackPosition);
  const isPlaying = useAppStore(s => s.isPlaying);
  const setPlaybackPosition = useAppStore(s => s.setPlaybackPosition);
  const togglePlay = useAppStore(s => s.togglePlay);
  const activeScene = useAppStore(s => s.activeScene);
  const allScenes = useAppStore(s => s.scenes);
  const chapterScope = useAppStore(s => s.chapterScope);
  // When drilled into a chapter, filter the visible scene list down to
  // that chapter's scenes only.  Main project view → unchanged.
  const scenes = chapterScope
    ? allScenes.filter((s: any) => chapterScope.sceneIds.has(String(s.id)))
    : allScenes;
  const currentProject = useAppStore(s => s.currentProject);
  const scenesLocked = useAppStore(s => s.scenesLocked);
  const setScenesLocked = useAppStore(s => s.setScenesLocked);
  const narrationVolume = useAppStore(s => s.narrationVolume);
  const setAutoGenOpen = useAppStore(s => s.setAutoGenOpen);

  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  };

  // Always show scenes on the timeline
  const timelineItems = scenes.map(s => ({ id: s.id, label: s.name || `Scene ${s.order_index + 1}`, start_time: s.start_time, end_time: s.end_time, type: 'scene' as const }));

  const effectiveDuration = duration > 0
    ? duration
    : Math.max(...scenes.map(s => s.end_time || 0), 1);

  // Find active item on the timeline
  const activeItemId = activeScene?.id || null;

  // Skip to previous/next section or scene boundary
  const skipToPrev = () => {
    const boundaries = timelineItems
      .map(i => i.start_time)
      .filter(t => t < playbackPosition - 0.5)
      .sort((a, b) => b - a);
    if (boundaries.length > 0) {
      setPlaybackPosition(boundaries[0]);
    } else {
      setPlaybackPosition(0);
    }
  };

  const skipToNext = () => {
    const boundaries = timelineItems
      .map(i => i.start_time)
      .filter(t => t > playbackPosition + 0.5)
      .sort((a, b) => a - b);
    if (boundaries.length > 0) {
      setPlaybackPosition(boundaries[0]);
    } else {
      setPlaybackPosition(effectiveDuration);
    }
  };

  // Split: find the scene under the playhead and split it
  const handleSplit = () => {
    if (!onSplitScene) return;
    const sceneAtPlayhead = scenes.find(
      s => s.start_time <= playbackPosition && s.end_time > playbackPosition
    );
    if (sceneAtPlayhead && playbackPosition > sceneAtPlayhead.start_time + 0.5 && playbackPosition < sceneAtPlayhead.end_time - 0.5) {
      onSplitScene(sceneAtPlayhead.id, playbackPosition);
    }
  };

  const canSplit = !!onSplitScene && scenes.some(
    s => s.start_time < playbackPosition - 0.5 && s.end_time > playbackPosition + 0.5
  );

  // Suggest Fresh Timeline handler
  const handleSuggestTimeline = async () => {
    if (!currentProject) return;
    if (scenes.length > 0) {
      if (!window.confirm(
        'This will replace ALL existing scenes with an LLM-generated timeline based on your lyrics, sections, and timing data. Any existing scene work (prompts, images) will be lost.\n\nContinue?'
      )) return;
    }
    setIsSuggesting(true);
    try {
      await suggestTimeline(currentProject.id);
      // Refresh scenes in store
      const res = await getScenes(currentProject.id);
      const store = useAppStore.getState();
      store.setScenes(res.data);
      if (res.data.length > 0) {
        store.setActiveScene(res.data[0]);
      }
      // Broadcast so any mounted chapter-aware component (AppLayout's
      // Chapters tab, the timeline overlay, the export-modal picker)
      // refetches the freshly-built chapter tree.
      window.dispatchEvent(new CustomEvent('rbmn:chapters:invalidate', {
        detail: { projectId: currentProject.id },
      }));
    } catch (err: any) {
      const detail = err?.response?.data?.detail || 'Failed to suggest timeline';
      alert(`Error: ${detail}`);
    } finally {
      setIsSuggesting(false);
    }
  };

  // Regenerate Audio Segments handler
  const handleRegenerateAudioSegments = async () => {
    if (!currentProject) return;
    if (scenes.length === 0) return;
    setIsSlicingAudio(true);
    try {
      const res = await sliceSceneAudio(currentProject.id);
      const msg = res.data?.message || `Sliced audio for ${res.data?.sliced_count || 0} scenes`;
      alert(msg);
    } catch (err: any) {
      const detail = err?.response?.data?.detail || 'Failed to regenerate audio segments';
      alert(`Error: ${detail}`);
    } finally {
      setIsSlicingAudio(false);
    }
  };

  // Cleanup orphaned scenes handler
  const handleRetrimAll = async () => {
    if (!currentProject) return;
    if (!window.confirm(
      'Re-run post-processing (trim, color correction, audio mux, last-frame extraction) on all scenes with videos. This does NOT regenerate videos. Continue?'
    )) return;
    setIsRetrimming(true);
    try {
      const res = await retrimAllScenes(currentProject.id);
      const data = res.data;
      // Refresh scenes in store
      const scenesRes = await getScenes(currentProject.id);
      const store = useAppStore.getState();
      store.setScenes(scenesRes.data);
      alert(`Retrim complete: ${data.processed} processed, ${data.skipped} skipped, ${data.errors} errors`);
    } catch (err: any) {
      const detail = err?.response?.data?.detail || 'Retrim failed';
      alert(`Error: ${detail}`);
    } finally {
      setIsRetrimming(false);
    }
  };

  const handleCleanupScenes = async () => {
    if (!currentProject) return;
    if (!window.confirm(
      'This will remove duplicate/orphaned scenes and re-index the remaining ones. Continue?'
    )) return;
    setIsCleaning(true);
    try {
      const res = await cleanupScenes(currentProject.id);
      const msg = res.data?.message || `Cleaned up scenes`;
      // Refresh scenes in store
      const scenesRes = await getScenes(currentProject.id);
      const store = useAppStore.getState();
      store.setScenes(scenesRes.data);
      if (scenesRes.data.length > 0 && !scenesRes.data.find(s => s.id === activeScene?.id)) {
        store.setActiveScene(scenesRes.data[0]);
      }
      alert(msg);
    } catch (err: any) {
      const detail = err?.response?.data?.detail || 'Failed to clean up scenes';
      alert(`Error: ${detail}`);
    } finally {
      setIsCleaning(false);
    }
  };

  return (
    <div className="h-full bg-gray-900 p-3 flex flex-col overflow-hidden">
      {/* Transport Controls */}
      <div className="flex items-center justify-between mb-2 flex-shrink-0 flex-wrap gap-y-1">
        <div className="flex items-center gap-2">
          <button onClick={skipToPrev} className="p-1.5 text-gray-400 hover:text-white transition-colors" title="Previous section">
            <SkipBack size={16} />
          </button>
          <button
            onClick={togglePlay}
            className="p-2 bg-blue-600 hover:bg-blue-700 rounded-full text-white transition-colors"
            title={isPlaying ? 'Pause' : 'Play'}
          >
            {isPlaying ? <Pause size={18} /> : <Play size={18} className="ml-0.5" />}
          </button>
          <button onClick={skipToNext} className="p-1.5 text-gray-400 hover:text-white transition-colors" title="Next section">
            <SkipForward size={16} />
          </button>
          <button
            onClick={() => { setPlaybackPosition(0); if (!isPlaying) togglePlay(); }}
            className="p-1.5 text-gray-400 hover:text-white transition-colors"
            title="Play from Start"
          >
            <RotateCcw size={16} />
          </button>

          <div className="flex items-center gap-1.5 text-xs font-mono text-gray-300 ml-2">
            <span>{formatTime(playbackPosition)}</span>
            <span className="text-gray-500">/</span>
            <span className="text-gray-500">{formatTime(effectiveDuration)}</span>
          </div>
        </div>

        <div className="timeline-toolbar flex items-center gap-2">
          {/* Split Button */}
          {onSplitScene && (
            <button
              onClick={handleSplit}
              disabled={!canSplit}
              className={`flex items-center gap-1 px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                canSplit
                  ? 'bg-orange-600 hover:bg-orange-700 text-white'
                  : 'bg-gray-800 text-gray-500 cursor-not-allowed'
              }`}
              title="Split scene at playhead"
            >
              <Scissors size={14} />
              Split
            </button>
          )}

          {/* Regenerate Audio Segments */}
          {currentProject && scenes.length > 0 && (
            <button
              onClick={handleRegenerateAudioSegments}
              disabled={isSlicingAudio}
              className={`flex items-center gap-1 px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                isSlicingAudio
                  ? 'bg-gray-800 text-gray-500 cursor-not-allowed'
                  : 'bg-cyan-600/80 hover:bg-cyan-600 text-white'
              }`}
              title="Re-slice the master audio into per-scene clips based on current timeline positions"
            >
              <Music size={14} />
              {isSlicingAudio ? 'Slicing...' : 'Regen Audio Segments'}
            </button>
          )}

          {/* Retrim All Button */}
          {currentProject && scenes.length > 0 && (
            <button
              onClick={handleRetrimAll}
              disabled={isRetrimming}
              className={`flex items-center gap-1 px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                isRetrimming
                  ? 'bg-gray-800 text-gray-500 cursor-not-allowed'
                  : 'bg-amber-600/80 hover:bg-amber-600 text-white'
              }`}
              title="Re-run post-processing (trim, color correct, audio mux, last-frame extract) on all scene videos without regenerating"
            >
              <RefreshCw size={14} className={isRetrimming ? 'animate-spin' : ''} />
              {isRetrimming ? 'Retrimming...' : 'Retrim All'}
            </button>
          )}

          {/* Auto Gen Button */}
          {currentProject && scenes.length > 0 && (
            <button
              onClick={() => setAutoGenOpen(true)}
              className="flex items-center gap-1 px-2.5 py-1 rounded text-xs font-medium transition-colors bg-purple-600/80 hover:bg-purple-600 text-white"
              title="Auto-generate missing scene content (images or videos)"
            >
              <Zap size={14} />
              Auto Gen
            </button>
          )}

          {/* Delete Scene Button */}
          {onDeleteScene && activeScene && scenes.length > 1 && (
            <button
              onClick={onDeleteScene}
              className="flex items-center gap-1 px-2.5 py-1 rounded text-xs font-medium transition-colors bg-red-600/80 hover:bg-red-600 text-white"
              title={`Delete "${activeScene.name || 'Scene'}"`}
            >
              <Trash2 size={14} />
              Delete Scene
            </button>
          )}

          {/* Lock Scenes Toggle */}
          {(
            <button
              onClick={() => {
                const newLocked = !scenesLocked;
                setScenesLocked(newLocked);
                // Persist to project settings so it survives app restarts
                if (currentProject) {
                  const newSettings = { ...(currentProject.settings || {}), scenes_locked: newLocked };
                  updateProject(currentProject.id, { settings: newSettings }).catch(() => {});
                }
              }}
              className={`flex items-center gap-1 px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                scenesLocked
                  ? 'bg-yellow-600/80 hover:bg-yellow-600 text-white'
                  : 'bg-gray-800 hover:bg-gray-700 text-gray-400'
              }`}
              title={scenesLocked
                ? 'Scenes are locked — audio reprocessing will not modify scene boundaries'
                : 'Click to lock scenes — prevents audio reprocessing from changing scene boundaries'
              }
            >
              {scenesLocked ? <Lock size={14} /> : <Unlock size={14} />}
              {scenesLocked ? 'Scenes Locked' : 'Lock Scenes'}
            </button>
          )}

          {/* Suggest Fresh Timeline */}
          {currentProject && (
            <button
              onClick={handleSuggestTimeline}
              disabled={isSuggesting || scenesLocked}
              className={`flex items-center gap-1 px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                isSuggesting || scenesLocked
                  ? 'bg-gray-800 text-gray-500 cursor-not-allowed'
                  : 'bg-emerald-600/80 hover:bg-emerald-600 text-white'
              }`}
              title={scenesLocked
                ? 'Unlock scenes first to regenerate timeline'
                : 'Use LLM to generate an optimal scene timeline from lyrics and timing data'
              }
            >
              <Wand2 size={14} />
              {isSuggesting ? 'Analyzing...' : 'Suggest Fresh Timeline'}
            </button>
          )}

          {/* Clean Up Scenes — only show when there are scenes */}
          {currentProject && scenes.length > 0 && (
            <button
              onClick={handleCleanupScenes}
              disabled={isCleaning}
              className={`flex items-center gap-1 px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                isCleaning
                  ? 'bg-gray-800 text-gray-500 cursor-not-allowed'
                  : 'bg-orange-600/60 hover:bg-orange-600 text-white'
              }`}
              title="Remove duplicate/orphaned scenes and re-index"
            >
              <RotateCcw size={14} />
              {isCleaning ? 'Cleaning...' : 'Clean Up Scenes'}
            </button>
          )}

          {/* Zoom */}
          <div className="flex items-center gap-1 ml-2">
            <button
              onClick={() => {
                const step = zoom > 4 ? 1 : zoom > 2 ? 0.5 : 0.25;
                setZoom(Math.max(+(zoom - step).toFixed(2), 0.5));
              }}
              className="p-1 text-gray-400 hover:text-white transition-colors"
              title="Zoom out"
            >
              <ZoomOut size={16} />
            </button>
            <span className="text-xs text-gray-500 w-10 text-center">{Math.round(zoom * 100)}%</span>
            <button
              onClick={() => {
                const step = zoom >= 4 ? 1 : zoom >= 2 ? 0.5 : 0.25;
                setZoom(Math.min(+(zoom + step).toFixed(2), 16));
              }}
              className="p-1 text-gray-400 hover:text-white transition-colors"
              title="Zoom in"
            >
              <ZoomIn size={16} />
            </button>
          </div>
        </div>
      </div>

      {/* Scrollable zoom container — wraps ruler + waveform so they scroll together */}
      <div className="flex-1 flex flex-col overflow-x-auto overflow-y-hidden min-h-0">
        <div style={{ width: `${zoom * 100}%`, minWidth: '100%' }} className="flex flex-col h-full">

          {/* Time Ruler — click or drag to seek */}
          <TimeRuler
            duration={effectiveDuration}
            zoom={zoom}
            playbackPosition={playbackPosition}
            onSeek={setPlaybackPosition}
          />

          {/* Waveform + Overlay */}
          <div
            className="flex-1 relative bg-gray-950 rounded-b border border-t-0 border-gray-800 overflow-hidden min-h-0"
          >
            {/* Waveform Layer */}
            <div className="absolute inset-0">
              <WaveformDisplay
                zoom={zoom}
                duration={duration}
                setDuration={setDuration}
                playbackPosition={playbackPosition}
                setPlaybackPosition={setPlaybackPosition}
                isPlaying={isPlaying}
                volume={narrationVolume}
              />
            </div>

            {/* Section/Scene Overlay Layer */}
            <TimelineOverlay
              items={timelineItems}
              duration={effectiveDuration}
              activeItemId={activeItemId}
              onItemClick={(item) => {
                const store = useAppStore.getState();
                const scene = scenes.find(s => s.id === item.id);
                if (scene) store.setActiveScene(scene);
              }}
              onSeek={(time) => setPlaybackPosition(time)}
              onBoundaryDrag={onBoundaryDrag}
            />

            {/* Playhead */}
            {effectiveDuration > 0 && (
              <div
                className="absolute top-0 bottom-0 w-px bg-white z-30 pointer-events-none"
                style={{ left: `${(playbackPosition / effectiveDuration) * 100}%` }}
              >
                <div className="absolute -top-0.5 -left-1.5 w-3 h-2 bg-white" style={{ clipPath: 'polygon(0 0, 100% 0, 50% 100%)' }} />
              </div>
            )}
          </div>

        </div>
      </div>

      {/* Auto Gen Modal is rendered by AppLayout — opening it via the
          store ensures the minimize-to-status-bar path is wired up. */}
    </div>
  );
}


/** Time ruler above the waveform — click or drag to seek */
function TimeRuler({
  duration,
  zoom,
  playbackPosition,
  onSeek,
}: {
  duration: number;
  zoom: number;
  playbackPosition: number;
  onSeek: (time: number) => void;
}) {
  const rulerRef = useRef<HTMLDivElement>(null);
  const isDragging = useRef(false);

  const positionFromEvent = useCallback(
    (clientX: number) => {
      if (!rulerRef.current || duration <= 0) return 0;
      const rect = rulerRef.current.getBoundingClientRect();
      const pct = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
      return pct * duration;
    },
    [duration]
  );

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      isDragging.current = true;
      onSeek(positionFromEvent(e.clientX));

      const handleMouseMove = (ev: MouseEvent) => {
        if (isDragging.current) onSeek(positionFromEvent(ev.clientX));
      };
      const handleMouseUp = () => {
        isDragging.current = false;
        window.removeEventListener('mousemove', handleMouseMove);
        window.removeEventListener('mouseup', handleMouseUp);
      };
      window.addEventListener('mousemove', handleMouseMove);
      window.addEventListener('mouseup', handleMouseUp);
    },
    [onSeek, positionFromEvent]
  );

  if (duration <= 0) return null;

  const formatRulerTime = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, '0')}`;
  };

  // Show a tick every ~10 seconds (adjusted by zoom)
  const interval = Math.max(5, Math.round(15 / zoom));
  const ticks: number[] = [];
  for (let t = 0; t <= duration; t += interval) {
    ticks.push(t);
  }

  const playheadPct = duration > 0 ? (playbackPosition / duration) * 100 : 0;

  return (
    <div
      ref={rulerRef}
      className="relative h-5 bg-gray-900 border border-b-0 border-gray-800 rounded-t overflow-hidden flex-shrink-0 cursor-pointer select-none"
      onMouseDown={handleMouseDown}
    >
      {ticks.map(t => (
        <div
          key={t}
          className="absolute top-0 h-full flex flex-col items-center pointer-events-none"
          style={{ left: `${(t / duration) * 100}%` }}
        >
          <div className="w-px h-2 bg-gray-600" />
          <span className="text-[9px] text-gray-500 font-mono leading-none mt-0.5">{formatRulerTime(t)}</span>
        </div>
      ))}

      {/* Playhead marker on ruler */}
      <div
        className="absolute top-0 bottom-0 w-px bg-white pointer-events-none z-10"
        style={{ left: `${playheadPct}%` }}
      />
    </div>
  );
}
