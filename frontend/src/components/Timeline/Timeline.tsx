import { useState, useCallback, useRef, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { useAppStore } from '@/store';
import WaveformDisplay from '@/components/Timeline/WaveformDisplay';
import TimelineOverlay from '@/components/Timeline/TimelineOverlay';
import { suggestTimeline, sliceSceneAudio, getScenes, startSequentialAutoGen, getSequentialAutoGenStatus, cancelSequentialAutoGen, cleanupScenes, updateProject, retrimAllScenes } from '@/api/client';
import { Play, Pause, ZoomIn, ZoomOut, Scissors, SkipBack, SkipForward, RotateCcw, RefreshCw, Trash2, Wand2, Lock, Unlock, Music, Zap, X } from 'lucide-react';

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
  const [showAutoGenModal, setShowAutoGenModal] = useState(false);
  const {
    playbackPosition, isPlaying, setPlaybackPosition, togglePlay,
    viewMode, activeScene, sections, scenes, currentProject,
    scenesLocked, setScenesLocked,
  } = useAppStore();

  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  };

  // Items to display on the timeline depend on viewMode
  const timelineItems = viewMode === 'sections'
    ? sections.map(s => ({ id: s.id, label: s.label, start_time: s.start_time, end_time: s.end_time, type: 'section' as const }))
    : scenes.map(s => ({ id: s.id, label: s.name || `Scene ${s.order_index + 1}`, start_time: s.start_time, end_time: s.end_time, type: 'scene' as const }));

  const effectiveDuration = duration > 0
    ? duration
    : Math.max(...[...sections, ...scenes].map(s => s.end_time || 0), 1);

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
    if (!onSplitScene || viewMode !== 'scenes') return;
    const sceneAtPlayhead = scenes.find(
      s => s.start_time <= playbackPosition && s.end_time > playbackPosition
    );
    if (sceneAtPlayhead && playbackPosition > sceneAtPlayhead.start_time + 0.5 && playbackPosition < sceneAtPlayhead.end_time - 0.5) {
      onSplitScene(sceneAtPlayhead.id, playbackPosition);
    }
  };

  const canSplit = viewMode === 'scenes' && !!onSplitScene && scenes.some(
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
      <div className="flex items-center justify-between mb-2 flex-shrink-0">
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

        <div className="flex items-center gap-2">
          {/* Split Button */}
          {viewMode === 'scenes' && onSplitScene && (
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
          {viewMode === 'scenes' && currentProject && scenes.length > 0 && (
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
          {viewMode === 'scenes' && currentProject && scenes.length > 0 && (
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
          {viewMode === 'scenes' && currentProject && scenes.length > 0 && (
            <button
              onClick={() => setShowAutoGenModal(true)}
              className="flex items-center gap-1 px-2.5 py-1 rounded text-xs font-medium transition-colors bg-purple-600/80 hover:bg-purple-600 text-white"
              title="Auto-generate missing scene content (images or videos)"
            >
              <Zap size={14} />
              Auto Gen
            </button>
          )}

          {/* Delete Scene Button */}
          {viewMode === 'scenes' && onDeleteScene && activeScene && scenes.length > 1 && (
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
          {viewMode === 'scenes' && (
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
          {viewMode === 'scenes' && currentProject && (
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
          {viewMode === 'scenes' && currentProject && scenes.length > 0 && (
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
              onClick={() => setZoom(Math.max(zoom - 0.25, 0.5))}
              className="p-1 text-gray-400 hover:text-white transition-colors"
              title="Zoom out"
            >
              <ZoomOut size={16} />
            </button>
            <span className="text-xs text-gray-500 w-8 text-center">{Math.round(zoom * 100)}%</span>
            <button
              onClick={() => setZoom(Math.min(zoom + 0.25, 4))}
              className="p-1 text-gray-400 hover:text-white transition-colors"
              title="Zoom in"
            >
              <ZoomIn size={16} />
            </button>
          </div>
        </div>
      </div>

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
          />
        </div>

        {/* Section/Scene Overlay Layer */}
        <TimelineOverlay
          items={timelineItems}
          duration={effectiveDuration}
          activeItemId={activeItemId}
          onItemClick={(item) => {
            const store = useAppStore.getState();
            if (item.type === 'section') {
              const section = sections.find(s => s.id === item.id);
              if (section) store.setActiveScene(section as any);
            } else {
              const scene = scenes.find(s => s.id === item.id);
              if (scene) store.setActiveScene(scene);
            }
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

      {/* Auto Gen Modal */}
      {showAutoGenModal && currentProject && (
        <AutoGenModal
          projectId={currentProject.id}
          onClose={() => setShowAutoGenModal(false)}
        />
      )}
    </div>
  );
}


/** Auto-generation modal with sequential processing modes */
function AutoGenModal({ projectId, onClose }: { projectId: string; onClose: () => void }) {
  const [overrideFullSet, setOverrideFullSet] = useState(false);
  const [vocalsOnlyAudio, setVocalsOnlyAudio] = useState(false);
  const [skipAudioMux, setSkipAudioMux] = useState(false);
  const [twoPass, setTwoPass] = useState(false);
  const [status, setStatus] = useState<{
    status: string;
    mode?: string;
    total_scenes: number;
    completed_scenes: number;
    current_scene_name?: string;
    current_step?: string;
    error?: string;
  } | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [startTime, setStartTime] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const isRunning = status?.status === 'running';
  const isDone = status?.status === 'done';
  const isFailed = status?.status === 'failed';
  const isCancelled = status?.status === 'cancelled';

  // Elapsed time timer — ticks every second while running
  useEffect(() => {
    if (isRunning && !startTime) {
      setStartTime(Date.now());
    }
    if (isRunning) {
      timerRef.current = setInterval(() => {
        if (startTime) setElapsed(Math.floor((Date.now() - startTime) / 1000));
      }, 1000);
    } else if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [isRunning, startTime]);

  const formatElapsed = (secs: number) => {
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    const s = secs % 60;
    if (h > 0) return `${h}h ${m}m ${s}s`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
  };

  // Poll for status updates
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const startPolling = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const res = await getSequentialAutoGenStatus(projectId);
        setStatus(res.data);
        if (res.data.status !== 'running') {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          // Refresh scenes in store after completion
          if (res.data.status === 'done') {
            try {
              const scenesRes = await getScenes(projectId);
              useAppStore.getState().setScenes(scenesRes.data);
            } catch { /* ignore */ }
          }
        }
      } catch { /* ignore */ }
    }, 2000);
  }, [projectId]);

  const handleStart = useCallback(async (mode: string) => {
    try {
      const res = await startSequentialAutoGen(projectId, mode, overrideFullSet, vocalsOnlyAudio, skipAudioMux, twoPass);
      setStatus(res.data);
      startPolling();
    } catch (err: any) {
      setStatus({
        status: 'failed',
        total_scenes: 0,
        completed_scenes: 0,
        error: err?.response?.data?.detail || 'Failed to start',
      });
    }
  }, [projectId, startPolling, overrideFullSet, vocalsOnlyAudio, skipAudioMux, twoPass]);

  const handleCancel = useCallback(async () => {
    try {
      const res = await cancelSequentialAutoGen(projectId);
      setStatus(res.data as any);
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = null;
    } catch { /* ignore */ }
  }, [projectId]);

  const progressPct = status && status.total_scenes > 0
    ? Math.round((status.completed_scenes / status.total_scenes) * 100)
    : 0;

  return createPortal(
    <div
      style={{ position: 'fixed', inset: 0, zIndex: 9999, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.6)' }}
      onClick={(e) => { if (e.target === e.currentTarget && !isRunning) onClose(); }}
    >
      <div style={{ background: '#1a1a2e', borderRadius: 12, padding: 24, width: 520, maxHeight: '80vh', overflow: 'auto', border: '1px solid #333', boxShadow: '0 20px 60px rgba(0,0,0,0.5)' }}>
        {/* Header */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
          <h2 style={{ color: '#e0e0e0', fontSize: 18, fontWeight: 600, margin: 0 }}>
            {overrideFullSet ? 'Auto Generate — Full Set' : 'Auto Generate Missing Scenes'}
          </h2>
          {!isRunning && (
            <button onClick={onClose} style={{ background: 'none', border: 'none', color: '#888', cursor: 'pointer', padding: 4 }}>
              <X size={20} />
            </button>
          )}
        </div>

        {/* Override toggle — only show when not running */}
        {!isRunning && !isDone && !isFailed && !isCancelled && (
          <label
            style={{
              display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer',
              padding: '10px 12px', marginBottom: 8, borderRadius: 8,
              background: overrideFullSet ? 'rgba(245, 158, 11, 0.15)' : 'rgba(255,255,255,0.04)',
              border: overrideFullSet ? '1px solid rgba(245, 158, 11, 0.4)' : '1px solid rgba(255,255,255,0.08)',
              transition: 'all 0.2s',
            }}
          >
            <input
              type="checkbox"
              checked={overrideFullSet}
              onChange={(e) => setOverrideFullSet(e.target.checked)}
              style={{ accentColor: '#f59e0b', width: 16, height: 16, cursor: 'pointer' }}
            />
            <div>
              <div style={{ color: overrideFullSet ? '#f59e0b' : '#ccc', fontSize: 13, fontWeight: 600 }}>
                Override — Create Full Set
              </div>
              <div style={{ color: '#888', fontSize: 11, lineHeight: 1.4, marginTop: 2 }}>
                Regenerate all scenes regardless of existing images/videos. Previous generations remain in the gallery.
              </div>
            </div>
          </label>
        )}

        {/* Vocals-only audio toggle — only show when not running */}
        {!isRunning && !isDone && !isFailed && !isCancelled && (
          <label
            style={{
              display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer',
              padding: '10px 12px', marginBottom: 8, borderRadius: 8,
              background: vocalsOnlyAudio ? 'rgba(139, 92, 246, 0.15)' : 'rgba(255,255,255,0.04)',
              border: vocalsOnlyAudio ? '1px solid rgba(139, 92, 246, 0.4)' : '1px solid rgba(255,255,255,0.08)',
              transition: 'all 0.2s',
            }}
          >
            <input
              type="checkbox"
              checked={vocalsOnlyAudio}
              onChange={(e) => setVocalsOnlyAudio(e.target.checked)}
              style={{ accentColor: '#8b5cf6', width: 16, height: 16, cursor: 'pointer' }}
            />
            <div>
              <div style={{ color: vocalsOnlyAudio ? '#8b5cf6' : '#ccc', fontSize: 13, fontWeight: 600 }}>
                Only Send Vocal Stems to Video Gen
              </div>
              <div style={{ color: '#888', fontSize: 11, lineHeight: 1.4, marginTop: 2 }}>
                Sends only the vocal stem audio to the video model for better lip-sync detection.
                Overrides per-scene stem selections when enabled.
              </div>
            </div>
          </label>
        )}

        {/* Skip audio mux toggle — only show when not running */}
        {!isRunning && !isDone && !isFailed && !isCancelled && (
          <label
            style={{
              display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer',
              padding: '10px 12px', marginBottom: 8, borderRadius: 8,
              background: skipAudioMux ? 'rgba(99, 102, 241, 0.15)' : 'rgba(255,255,255,0.04)',
              border: skipAudioMux ? '1px solid rgba(99, 102, 241, 0.4)' : '1px solid rgba(255,255,255,0.08)',
              transition: 'all 0.2s',
            }}
          >
            <input
              type="checkbox"
              checked={skipAudioMux}
              onChange={(e) => setSkipAudioMux(e.target.checked)}
              style={{ accentColor: '#6366f1', width: 16, height: 16, cursor: 'pointer' }}
            />
            <div>
              <div style={{ color: skipAudioMux ? '#6366f1' : '#ccc', fontSize: 13, fontWeight: 600 }}>
                Keep Model Audio (skip mux)
              </div>
              <div style={{ color: '#888', fontSize: 11, lineHeight: 1.4, marginTop: 2 }}>
                Preserves LTX model-generated audio instead of replacing with original music.
                Useful for testing lip-sync and audio generation quality.
              </div>
            </div>
          </label>
        )}

        {/* Two-pass generation toggle — only show when not running */}
        {!isRunning && !isDone && !isFailed && !isCancelled && (
          <label
            style={{
              display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer',
              padding: '10px 12px', marginBottom: 8, borderRadius: 8,
              background: twoPass ? 'rgba(59, 130, 246, 0.15)' : 'rgba(255,255,255,0.04)',
              border: twoPass ? '1px solid rgba(59, 130, 246, 0.4)' : '1px solid rgba(255,255,255,0.08)',
              transition: 'all 0.2s',
            }}
          >
            <input
              type="checkbox"
              checked={twoPass}
              onChange={(e) => setTwoPass(e.target.checked)}
              style={{ accentColor: '#3b82f6', width: 16, height: 16, cursor: 'pointer' }}
            />
            <div>
              <div style={{ color: twoPass ? '#3b82f6' : '#ccc', fontSize: 13, fontWeight: 600 }}>
                Two-Pass Image Generation
              </div>
              <div style={{ color: '#888', fontSize: 11, lineHeight: 1.4, marginTop: 2 }}>
                Pass 1 generates scene composition without character refs, Pass 2 composites characters into the scene.
                Only applies to image-generation modes with character references selected.
              </div>
            </div>
          </label>
        )}

        {/* Mode buttons — only show when not running */}
        {!isRunning && !isDone && !isFailed && !isCancelled && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <button
              onClick={() => handleStart('all_video_fflf')}
              style={{ background: '#7c3aed', color: 'white', border: 'none', borderRadius: 8, padding: '14px 16px', cursor: 'pointer', textAlign: 'left', transition: 'background 0.2s' }}
              onMouseEnter={e => (e.currentTarget.style.background = '#6d28d9')}
              onMouseLeave={e => (e.currentTarget.style.background = '#7c3aed')}
            >
              <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4 }}>
                {overrideFullSet ? 'Generate All' : 'Generate All Missing'} — Video (Use Previous Frame)
              </div>
              <div style={{ fontSize: 11, opacity: 0.8, lineHeight: 1.4 }}>
                Generates videos sequentially. Each scene uses the previous video&apos;s last frame as its first frame.
                Scene 1 generates a first-frame image{overrideFullSet ? '' : ' if missing'}. All prompts auto-enhanced via LLM.
              </div>
            </button>

            <button
              onClick={() => handleStart('all_video_v2v')}
              style={{ background: '#9333ea', color: 'white', border: 'none', borderRadius: 8, padding: '14px 16px', cursor: 'pointer', textAlign: 'left', transition: 'background 0.2s' }}
              onMouseEnter={e => (e.currentTarget.style.background = '#7e22ce')}
              onMouseLeave={e => (e.currentTarget.style.background = '#9333ea')}
            >
              <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4 }}>
                {overrideFullSet ? 'Generate All' : 'Generate All Missing'} — Video (V2V Extend)
              </div>
              <div style={{ fontSize: 11, opacity: 0.8, lineHeight: 1.4 }}>
                Scene 1 generates normally (I2V). Scenes 2+ use V2V extending — feeds the previous
                video as latent conditioning for seamless transitions. No single-frame bottleneck.
              </div>
            </button>

            <button
              onClick={() => handleStart('all_video_single')}
              style={{ background: '#2563eb', color: 'white', border: 'none', borderRadius: 8, padding: '14px 16px', cursor: 'pointer', textAlign: 'left', transition: 'background 0.2s' }}
              onMouseEnter={e => (e.currentTarget.style.background = '#1d4ed8')}
              onMouseLeave={e => (e.currentTarget.style.background = '#2563eb')}
            >
              <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4 }}>
                {overrideFullSet ? 'Generate All' : 'Generate All Missing'} — Video (Single Frame, No Last Frame)
              </div>
              <div style={{ fontSize: 11, opacity: 0.8, lineHeight: 1.4 }}>
                Generates a first-frame image per scene{overrideFullSet ? '' : ' (if missing)'}, then a single-image video for each.
                No last-frame referencing between scenes. All prompts auto-enhanced via LLM.
              </div>
            </button>

            <button
              onClick={() => handleStart('missing_videos_single')}
              style={{ background: '#0891b2', color: 'white', border: 'none', borderRadius: 8, padding: '14px 16px', cursor: 'pointer', textAlign: 'left', transition: 'background 0.2s' }}
              onMouseEnter={e => (e.currentTarget.style.background = '#0e7490')}
              onMouseLeave={e => (e.currentTarget.style.background = '#0891b2')}
            >
              <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4 }}>
                {overrideFullSet ? 'Generate All' : 'Generate All Missing'} — Videos (Single Frame Input)
              </div>
              <div style={{ fontSize: 11, opacity: 0.8, lineHeight: 1.4 }}>
                Generates videos for scenes that don&apos;t have one yet, using the existing first-frame image.
                Only generates a first-frame image if the scene has none at all. Video prompts auto-enhanced via LLM.
              </div>
            </button>

            <button
              onClick={() => handleStart('all_images')}
              style={{ background: '#059669', color: 'white', border: 'none', borderRadius: 8, padding: '14px 16px', cursor: 'pointer', textAlign: 'left', transition: 'background 0.2s' }}
              onMouseEnter={e => (e.currentTarget.style.background = '#047857')}
              onMouseLeave={e => (e.currentTarget.style.background = '#059669')}
            >
              <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4 }}>
                {overrideFullSet ? 'Generate All' : 'Generate All Missing'} — Images Only
              </div>
              <div style={{ fontSize: 11, opacity: 0.8, lineHeight: 1.4 }}>
                Generates first-frame images for {overrideFullSet ? 'all scenes' : 'all scenes that don\'t have one yet'}. Sets scenes to image mode.
                Creates a still-image version you can add movement and transitions to later.
              </div>
            </button>

            <button
              onClick={() => handleStart('missing_images_independent')}
              style={{ background: '#d97706', color: 'white', border: 'none', borderRadius: 8, padding: '14px 16px', cursor: 'pointer', textAlign: 'left', transition: 'background 0.2s' }}
              onMouseEnter={e => (e.currentTarget.style.background = '#b45309')}
              onMouseLeave={e => (e.currentTarget.style.background = '#d97706')}
            >
              <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4 }}>
                {overrideFullSet ? 'Generate All' : 'Generate All Missing'} — Images (Independent)
              </div>
              <div style={{ fontSize: 11, opacity: 0.8, lineHeight: 1.4 }}>
                Generates first-frame images for {overrideFullSet ? 'all scenes' : 'scenes missing them'}, but ignores the previous scene&apos;s
                image as reference. Each scene generates independently — only character refs are used.
                Useful when the previous scene image overpowers the current scene&apos;s content.
              </div>
            </button>
          </div>
        )}

        {/* Progress display — shown when running */}
        {isRunning && status && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {/* Mode label */}
            <div style={{ color: '#a78bfa', fontSize: 13, fontWeight: 500 }}>
              {status.mode === 'all_video_fflf' && 'Video (Use Previous Frame)'}
              {status.mode === 'all_video_v2v' && 'Video (V2V Extend)'}
              {status.mode === 'all_video_single' && 'Video (Single Frame)'}
              {status.mode === 'missing_videos_single' && 'Missing Videos (Single Frame Input)'}
              {status.mode === 'all_images' && 'Images Only'}
              {status.mode === 'missing_images_independent' && 'Images (Independent)'}
            </div>

            {/* Progress bar */}
            <div style={{ background: '#2a2a3e', borderRadius: 6, height: 24, overflow: 'hidden', position: 'relative' }}>
              <div
                style={{
                  background: 'linear-gradient(90deg, #7c3aed, #a78bfa)',
                  height: '100%',
                  width: `${progressPct}%`,
                  transition: 'width 0.5s ease',
                  borderRadius: 6,
                }}
              />
              <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, color: 'white', fontWeight: 600 }}>
                {status.completed_scenes} / {status.total_scenes} scenes ({progressPct}%)
              </div>
            </div>

            {/* Current scene + step */}
            <div style={{ color: '#ccc', fontSize: 13 }}>
              {status.current_scene_name && (
                <div style={{ marginBottom: 4 }}>
                  <span style={{ color: '#888' }}>Current: </span>
                  <span style={{ fontWeight: 500 }}>{status.current_scene_name}</span>
                </div>
              )}
              {status.current_step && (
                <div>
                  <span style={{ color: '#888' }}>Step: </span>
                  <span style={{ color: '#a78bfa' }}>{status.current_step}</span>
                </div>
              )}
              {elapsed > 0 && (
                <div style={{ marginTop: 4 }}>
                  <span style={{ color: '#888' }}>Time Elapsed: </span>
                  <span style={{ color: '#60a5fa', fontWeight: 500 }}>{formatElapsed(elapsed)}</span>
                </div>
              )}
            </div>

            {/* Cancel button */}
            <button
              onClick={handleCancel}
              style={{ background: '#dc2626', color: 'white', border: 'none', borderRadius: 8, padding: '10px 16px', cursor: 'pointer', fontWeight: 500, fontSize: 13 }}
            >
              Cancel
            </button>
          </div>
        )}

        {/* Completion / Error display */}
        {(isDone || isFailed || isCancelled) && status && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div style={{
              padding: '16px',
              borderRadius: 8,
              background: isDone ? '#064e3b' : isCancelled ? '#78350f' : '#7f1d1d',
              color: isDone ? '#6ee7b7' : isCancelled ? '#fbbf24' : '#fca5a5',
              fontSize: 14,
              fontWeight: 500,
            }}>
              {isDone && `Completed! All ${status.total_scenes} scenes processed in ${formatElapsed(elapsed)}.`}
              {isCancelled && `Cancelled after ${status.completed_scenes} of ${status.total_scenes} scenes (${formatElapsed(elapsed)}).`}
              {isFailed && `Failed: ${status.error || 'Unknown error'}`}
              {isFailed && status.completed_scenes > 0 && (
                <div style={{ fontSize: 12, marginTop: 6, opacity: 0.8 }}>
                  {status.completed_scenes} of {status.total_scenes} scenes were completed before the failure.
                </div>
              )}
            </div>

            <button
              onClick={onClose}
              style={{ background: '#374151', color: 'white', border: 'none', borderRadius: 8, padding: '10px 16px', cursor: 'pointer', fontWeight: 500, fontSize: 13 }}
            >
              Close
            </button>
          </div>
        )}
      </div>
    </div>,
    document.body
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
