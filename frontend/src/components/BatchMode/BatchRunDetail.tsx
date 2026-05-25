import { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  Play,
  Pause,
  CheckCircle,
  XCircle,
  AlertTriangle,
  Clock,
  Trash2,
  Loader2,
  ChevronDown,
  ChevronRight,
  ChevronLeftIcon,
  ChevronRightIcon,
  ExternalLink,
  Image as ImageIcon,
  Circle,
  Settings,
  X,
} from 'lucide-react';
import {
  getPersistentBatchRun,
  resumePersistentBatchRun,
  deletePersistentBatchRun,
  cancelSequentialAutoGen,
} from '@/api/client';
import type { PersistentBatchRunDetail as BatchRunDetailType, BatchRunStepEntry } from '@/types/index';

/* ─── Helpers ─── */

function formatElapsed(ms: number): string {
  if (ms < 1000) return '< 1s';
  const totalSec = Math.floor(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function formatRelativeTime(iso: string): string {
  // Backend sends UTC timestamps without 'Z' suffix — append it so JS parses as UTC
  const normalized = iso.endsWith('Z') ? iso : iso + 'Z';
  const diff = Date.now() - new Date(normalized).getTime();
  const sec = Math.max(0, Math.floor(diff / 1000));
  if (sec < 5) return 'just now';
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  return `${hr}h ago`;
}

function formatDate(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function statusBadgeClasses(status: string): string {
  switch (status) {
    case 'running': return 'bg-blue-500/20 text-blue-400 border-blue-500/40';
    case 'completed': case 'done': return 'bg-green-500/20 text-green-400 border-green-500/40';
    case 'failed': return 'bg-red-500/20 text-red-400 border-red-500/40';
    case 'cancelled': return 'bg-yellow-500/20 text-yellow-400 border-yellow-500/40';
    case 'paused': return 'bg-orange-500/20 text-orange-400 border-orange-500/40';
    default: return 'bg-gray-500/20 text-gray-400 border-gray-500/40';
  }
}

function progressBarColor(status: string): string {
  switch (status) {
    case 'completed': case 'done': return 'bg-green-500';
    case 'failed': return 'bg-red-500';
    case 'cancelled': return 'bg-yellow-500';
    default: return 'bg-blue-500';
  }
}

function scenePillBorder(status: string): string {
  switch (status) {
    case 'completed': case 'done': return 'border-green-500/60 bg-green-500/10 text-green-300';
    case 'failed': return 'border-red-500/60 bg-red-500/10 text-red-300';
    case 'running': return 'border-blue-500/60 bg-blue-500/10 text-blue-300';
    case 'skipped': return 'border-gray-600 bg-gray-800 text-gray-500';
    default: return 'border-gray-700 bg-gray-800/50 text-gray-500';
  }
}

/* ─── Feed Entry Row ─── */

function FeedEntry({ entry, onImageClick }: { entry: BatchRunStepEntry; onImageClick?: () => void }) {
  const dotColor = (() => {
    switch (entry.type) {
      case 'scene_start': return 'text-blue-400';
      case 'scene_complete': return 'text-green-400';
      case 'scene_failed': return 'text-red-400';
      default: return 'text-gray-500';
    }
  })();

  const icon = (() => {
    switch (entry.type) {
      case 'scene_start': return <Circle size={10} className={`${dotColor} fill-current`} />;
      case 'scene_complete': return <CheckCircle size={14} className="text-green-400" />;
      case 'scene_failed': return <XCircle size={14} className="text-red-400" />;
      default: return <Circle size={6} className="text-gray-600 fill-current" />;
    }
  })();

  return (
    <div className="flex items-start gap-3 py-2 px-3 hover:bg-gray-800/30 transition-colors group">
      <div className="mt-1 shrink-0 w-4 flex items-center justify-center">{icon}</div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          {entry.scene_name && (
            <span className="text-xs font-medium text-purple-400 bg-purple-500/10 px-1.5 py-0.5 rounded">
              {entry.scene_name}
            </span>
          )}
          <span className="text-sm text-gray-300">{entry.step}</span>
          {entry.worker_url && (
            <span className="text-[10px] font-mono text-cyan-400/70 bg-cyan-500/10 px-1.5 py-0.5 rounded" title={entry.worker_url}>
              {(() => {
                try {
                  const u = new URL(entry.worker_url);
                  return u.hostname + (u.port ? `:${u.port}` : '');
                } catch {
                  return entry.worker_url;
                }
              })()}
            </span>
          )}
        </div>
        {entry.type === 'scene_complete' && entry.asset_url && (
          entry.asset_url.match(/\.(mp4|webm)(\?|$)|\/video\//i) ? (
            <div
              className="relative w-8 h-8 rounded overflow-hidden mt-1.5 border border-gray-700 cursor-pointer hover:border-purple-500 hover:ring-1 hover:ring-purple-500/50 transition-all shrink-0"
              onClick={onImageClick}
            >
              <video
                src={entry.asset_url}
                muted
                playsInline
                preload="metadata"
                className="w-full h-full object-cover"
              />
              <div className="absolute inset-0 flex items-center justify-center bg-black/30">
                <Play size={10} className="text-white fill-current" />
              </div>
            </div>
          ) : (
            <img
              src={entry.asset_url}
              alt=""
              className="w-8 h-8 rounded object-cover mt-1.5 border border-gray-700 cursor-pointer hover:border-purple-500 hover:ring-1 hover:ring-purple-500/50 transition-all shrink-0"
              onClick={onImageClick}
            />
          )
        )}
      </div>
      <span className="text-xs text-gray-600 shrink-0 mt-0.5" title={formatRelativeTime(entry.timestamp)}>
        {(() => {
          const normalized = entry.timestamp.endsWith('Z') ? entry.timestamp : entry.timestamp + 'Z';
          return new Date(normalized).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        })()}
      </span>
    </div>
  );
}

/* ─── Lightbox Overlay ─── */

interface LightboxImage {
  url: string;
  sceneName?: string;
}

function Lightbox({
  images,
  currentIndex,
  onClose,
  onPrev,
  onNext,
}: {
  images: LightboxImage[];
  currentIndex: number;
  onClose: () => void;
  onPrev: () => void;
  onNext: () => void;
}) {
  const current = images[currentIndex];
  if (!current) return null;

  const isVideo = /\.(mp4|webm)(\?|$)|\/video\//i.test(current.url);

  // Keyboard navigation
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      else if (e.key === 'ArrowLeft') onPrev();
      else if (e.key === 'ArrowRight') onNext();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose, onPrev, onNext]);

  return (
    <div
      className="fixed inset-0 z-[9999] bg-black/90 backdrop-blur-sm flex items-center justify-center"
      onClick={onClose}
    >
      {/* Close button */}
      <button
        onClick={onClose}
        className="absolute top-4 right-4 p-2 bg-gray-800/80 hover:bg-gray-700 rounded-full transition-colors z-10"
      >
        <X size={20} className="text-gray-300" />
      </button>

      {/* Counter */}
      <div className="absolute top-4 left-1/2 -translate-x-1/2 text-sm text-gray-400 bg-gray-900/80 px-3 py-1 rounded-full z-10">
        {currentIndex + 1} / {images.length}
      </div>

      {/* Scene name */}
      {current.sceneName && (
        <div className="absolute bottom-6 left-1/2 -translate-x-1/2 text-sm font-medium text-purple-400 bg-purple-500/10 border border-purple-500/30 px-3 py-1 rounded-full z-10">
          {current.sceneName}
        </div>
      )}

      {/* Prev button */}
      {images.length > 1 && (
        <button
          onClick={(e) => { e.stopPropagation(); onPrev(); }}
          className="absolute left-3 top-1/2 -translate-y-1/2 p-3 bg-gray-800/80 hover:bg-gray-700 rounded-full transition-colors z-10"
        >
          <ChevronLeftIcon size={24} className="text-gray-300" />
        </button>
      )}

      {/* Next button */}
      {images.length > 1 && (
        <button
          onClick={(e) => { e.stopPropagation(); onNext(); }}
          className="absolute right-3 top-1/2 -translate-y-1/2 p-3 bg-gray-800/80 hover:bg-gray-700 rounded-full transition-colors z-10"
        >
          <ChevronRightIcon size={24} className="text-gray-300" />
        </button>
      )}

      {/* Content */}
      <div
        className="max-w-[90vw] max-h-[85vh] flex items-center justify-center"
        onClick={(e) => e.stopPropagation()}
      >
        {isVideo ? (
          <video
            key={current.url}
            src={current.url}
            loop
            muted
            playsInline
            controls
            className="max-w-full max-h-[85vh] object-contain rounded-lg"
          />
        ) : (
          <img
            src={current.url}
            alt={current.sceneName || 'Preview'}
            className="max-w-full max-h-[85vh] object-contain rounded-lg"
          />
        )}
      </div>
    </div>
  );
}

/* ─── Main Component ─── */

export default function BatchRunDetail() {
  const navigate = useNavigate();
  const { batchRunId } = useParams<{ batchRunId: string }>();
  const [run, setRun] = useState<BatchRunDetailType | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showErrors, setShowErrors] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [lightboxIndex, setLightboxIndex] = useState(0);
  const [liveElapsedMs, setLiveElapsedMs] = useState(0);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const feedEndRef = useRef<HTMLDivElement>(null);
  const feedContainerRef = useRef<HTMLDivElement>(null);
  const prevLogLenRef = useRef(0);

  /** Compute elapsed from started_at so it's always smooth and monotonic */
  const computeElapsedFromStart = useCallback((data: BatchRunDetailType) => {
    if (data.status === 'running' && data.started_at) {
      const normalized = data.started_at.endsWith('Z') ? data.started_at : data.started_at + 'Z';
      return Math.max(0, Date.now() - new Date(normalized).getTime());
    }
    return data.elapsed_ms;
  }, []);

  const fetchRun = useCallback(async () => {
    if (!batchRunId) return;
    try {
      const res = await getPersistentBatchRun(batchRunId);
      setRun(res.data);
      setLiveElapsedMs(computeElapsedFromStart(res.data));
    } catch (err: any) {
      setError(err?.response?.data?.detail || 'Failed to load batch run');
    } finally {
      setLoading(false);
    }
  }, [batchRunId, computeElapsedFromStart]);

  // Auto-scroll feed when new entries arrive
  useEffect(() => {
    if (!run) return;
    const logLen = run.step_log?.length ?? 0;
    if (logLen > prevLogLenRef.current) {
      prevLogLenRef.current = logLen;
      requestAnimationFrame(() => {
        feedEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
      });
    }
  }, [run?.step_log?.length]);

  // Polling — always poll while running/pending, stop when terminal
  useEffect(() => {
    fetchRun();
    pollRef.current = setInterval(fetchRun, 3000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [fetchRun]);

  // Stop polling only when we've confirmed a terminal state
  useEffect(() => {
    if (!run || run.status === 'running' || run.status === 'pending') return;
    // Give one extra poll to catch final data, then stop
    const stopTimer = setTimeout(() => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    }, 5000);
    return () => clearTimeout(stopTimer);
  }, [run?.status]);

  // Client-side tick: update elapsed every second while running
  useEffect(() => {
    if (run?.status === 'running' && run?.started_at) {
      const normalizedStart = run.started_at.endsWith('Z') ? run.started_at : run.started_at + 'Z';
      const startMs = new Date(normalizedStart).getTime();
      tickRef.current = setInterval(() => {
        setLiveElapsedMs(Math.max(0, Date.now() - startMs));
      }, 1000);
    } else {
      if (tickRef.current) { clearInterval(tickRef.current); tickRef.current = null; }
      // Use server value for terminal states
      if (run) setLiveElapsedMs(run.elapsed_ms);
    }
    return () => { if (tickRef.current) clearInterval(tickRef.current); };
  }, [run?.status, run?.started_at]);

  // ── Derived data (must be before early returns to keep hook order stable) ──
  const pct = run && run.total_scenes > 0
    ? Math.round((run.completed_scenes / run.total_scenes) * 100)
    : 0;

  const sceneEntries = Object.entries(run?.scene_results || {}).sort(
    (a, b) => (a[1]?.order ?? 0) - (b[1]?.order ?? 0)
  );

  const canResume = run?.status === 'failed' || run?.status === 'cancelled' || run?.status === 'paused';
  const isRunning = run?.status === 'running';
  const isTerminal = run ? !isRunning && run.status !== 'pending' : false;
  const stepLog = run?.step_log || [];

  // Collect all asset images/videos from step log for lightbox navigation
  const lightboxImages: LightboxImage[] = stepLog
    .filter((e) => e.type === 'scene_complete' && e.asset_url)
    .map((e) => ({ url: e.asset_url!, sceneName: e.scene_name }));

  const openLightbox = useCallback((url: string) => {
    const idx = lightboxImages.findIndex((img) => img.url === url);
    setLightboxIndex(idx >= 0 ? idx : 0);
    setLightboxOpen(true);
  }, [lightboxImages]);

  const lightboxPrev = useCallback(() => {
    setLightboxIndex((i) => (i > 0 ? i - 1 : lightboxImages.length - 1));
  }, [lightboxImages.length]);

  const lightboxNext = useCallback(() => {
    setLightboxIndex((i) => (i < lightboxImages.length - 1 ? i + 1 : 0));
  }, [lightboxImages.length]);

  const handleResume = async () => {
    if (!batchRunId) return;
    setResuming(true);
    try {
      await resumePersistentBatchRun(batchRunId);
      if (!pollRef.current) {
        pollRef.current = setInterval(fetchRun, 3000);
      }
      await fetchRun();
    } catch (err: any) {
      alert(err?.response?.data?.detail || 'Failed to resume');
    } finally {
      setResuming(false);
    }
  };

  const handleCancel = async () => {
    if (!run) return;
    try {
      await cancelSequentialAutoGen(run.project_id);
      await fetchRun();
    } catch (err) {
      console.error('Failed to cancel:', err);
    }
  };

  const handleDelete = async () => {
    if (!batchRunId) return;
    if (!confirm('Delete this batch run record?')) return;
    try {
      await deletePersistentBatchRun(batchRunId);
      navigate('/batches');
    } catch (err: any) {
      alert(err?.response?.data?.detail || 'Failed to delete');
    }
  };

  /* ─── Loading / Error states ─── */

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-950 text-gray-100 flex items-center justify-center">
        <Loader2 size={40} className="animate-spin text-gray-500" />
      </div>
    );
  }

  if (error || !run) {
    return (
      <div className="min-h-screen bg-gray-950 text-gray-100 p-8">
        <div className="max-w-3xl mx-auto">
          <button onClick={() => navigate('/batches')} className="mb-4 px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded text-sm flex items-center gap-2">
            <ArrowLeft size={18} /> Back to Batches
          </button>
          <div className="bg-red-900/30 border border-red-700 rounded-lg p-6 text-center">
            <XCircle size={40} className="mx-auto mb-3 text-red-400" />
            <p className="text-red-300">{error || 'Batch run not found'}</p>
          </div>
        </div>
      </div>
    );
  }

  /* ─── Render ─── */

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      {/* Top Bar */}
      <div className="sticky top-0 z-40 bg-gray-950/95 backdrop-blur border-b border-gray-800">
        <div className="max-w-7xl mx-auto px-4 py-3 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <button
              onClick={() => navigate('/batches')}
              className="p-2 bg-gray-800 hover:bg-gray-700 rounded-lg transition-colors shrink-0"
              title="Back to Batches"
            >
              <ArrowLeft size={18} />
            </button>
            <div className="min-w-0">
              <h1 className="text-lg md:text-xl font-bold truncate">{run.project_name}</h1>
              <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                <span className={`text-xs font-semibold uppercase px-2 py-0.5 rounded-full border ${statusBadgeClasses(run.status)}`}>
                  {run.status}
                </span>
                <span className="text-xs text-gray-500 capitalize">{run.mode.replace(/_/g, ' ')}</span>
                <span className="text-xs text-gray-600">|</span>
                <span className="text-xs text-gray-400 flex items-center gap-1">
                  <Clock size={12} />
                  {formatElapsed(liveElapsedMs)}
                </span>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {isRunning && (
              <button
                onClick={handleCancel}
                className="px-3 py-1.5 bg-yellow-600/20 hover:bg-yellow-600/30 border border-yellow-600/40 text-yellow-400 rounded-lg text-sm font-medium transition-colors flex items-center gap-1.5"
              >
                <Pause size={14} /> Cancel
              </button>
            )}
            {canResume && (
              <button
                onClick={handleResume}
                disabled={resuming}
                className="px-3 py-1.5 bg-green-600/20 hover:bg-green-600/30 border border-green-600/40 text-green-400 rounded-lg text-sm font-medium transition-colors flex items-center gap-1.5 disabled:opacity-50"
              >
                {resuming ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                Resume
              </button>
            )}
            <button
              onClick={() => navigate(`/project/${run.project_id}`)}
              className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-lg text-sm font-medium transition-colors flex items-center gap-1.5"
            >
              <ExternalLink size={14} />
              <span className="hidden sm:inline">Open Project</span>
            </button>
            {isTerminal && (
              <button
                onClick={handleDelete}
                className="p-1.5 bg-gray-800 hover:bg-red-900/50 border border-gray-700 hover:border-red-700 rounded-lg transition-colors text-gray-500 hover:text-red-400"
                title="Delete run"
              >
                <Trash2 size={16} />
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-4 py-4">
        {/* Progress Bar */}
        <div className="mb-4">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-sm text-gray-400">Scene Progress</span>
            <span className="text-sm font-mono text-gray-300">
              {run.completed_scenes}/{run.total_scenes} scenes &middot; {pct}%
            </span>
          </div>
          <div className="h-3 bg-gray-800 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-700 ease-out ${progressBarColor(run.status)} ${isRunning ? 'animate-pulse' : ''}`}
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>

        {/* 2-column layout */}
        <div className="flex flex-col lg:flex-row gap-4" style={{ minHeight: 'calc(100vh - 200px)' }}>

          {/* Left: Live Activity Feed */}
          <div className="lg:w-[60%] flex flex-col">
            <div className="bg-gray-900 border border-gray-800 rounded-lg flex flex-col flex-1 overflow-hidden">
              <div className="px-4 py-2.5 border-b border-gray-800 flex items-center justify-between shrink-0">
                <div className="flex items-center gap-2">
                  {isRunning && (
                    <span className="relative flex h-2.5 w-2.5">
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75" />
                      <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-blue-500" />
                    </span>
                  )}
                  <span className="text-sm font-medium text-gray-300">Live Activity</span>
                </div>
                <span className="text-xs text-gray-600">{stepLog.length} entries</span>
              </div>

              <div
                ref={feedContainerRef}
                className="flex-1 overflow-y-auto min-h-0"
                style={{ maxHeight: 'calc(100vh - 280px)' }}
              >
                {stepLog.length === 0 && !isRunning ? (
                  <div className="flex items-center justify-center h-48 text-gray-600 text-sm">
                    Waiting for activity...
                  </div>
                ) : stepLog.length === 0 && isRunning ? (
                  <div className="flex flex-col items-center justify-center h-48 gap-3">
                    <Loader2 size={24} className="text-purple-400 animate-spin" />
                    <span className="text-gray-400 text-sm">
                      {run?.current_step ? `${run.current_step}...` : 'Starting up...'}
                    </span>
                  </div>
                ) : (
                  <div className="divide-y divide-gray-800/50">
                    {stepLog.map((entry, i) => (
                      <FeedEntry
                        key={`${entry.timestamp}-${i}`}
                        entry={entry}
                        onImageClick={entry.asset_url ? () => openLightbox(entry.asset_url!) : undefined}
                      />
                    ))}
                  </div>
                )}

                {/* Live "now happening" indicator */}
                {isRunning && run.current_step && (
                  <div className="flex items-center gap-3 py-3 px-3 bg-blue-500/5 border-t border-blue-500/20">
                    <span className="relative flex h-2.5 w-2.5 shrink-0">
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75" />
                      <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-blue-500" />
                    </span>
                    <div className="flex items-center gap-2 min-w-0 flex-wrap">
                      {run.current_scene_name && (
                        <span className="text-xs font-medium text-purple-400 bg-purple-500/10 px-1.5 py-0.5 rounded shrink-0">
                          {run.current_scene_name}
                        </span>
                      )}
                      <span className="text-sm text-blue-300 truncate">{run.current_step}</span>
                    </div>
                  </div>
                )}

                <div ref={feedEndRef} />
              </div>
            </div>
          </div>

          {/* Right: Preview + Stats */}
          <div className="lg:w-[40%] flex flex-col gap-4">
            {/* Preview Panel */}
            <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
              <div className="aspect-video bg-black flex items-center justify-center relative group/preview">
                {run.last_asset_url ? (
                  run.last_asset_url.match(/\.(mp4|webm)(\?|$)|\/video\//i) ? (
                    <>
                      <video
                        key={run.last_asset_url}
                        src={run.last_asset_url}
                        loop
                        muted
                        playsInline
                        controls
                        className="w-full h-full object-contain"
                      />
                      {/* Lightbox button overlay for videos */}
                      <button
                        onClick={() => openLightbox(run.last_asset_url!)}
                        className="absolute top-2 right-2 p-1.5 bg-gray-900/70 hover:bg-gray-800 rounded-lg transition-colors opacity-0 group-hover/preview:opacity-100 z-10"
                        title="Open in lightbox"
                      >
                        <ExternalLink size={14} className="text-gray-300" />
                      </button>
                    </>
                  ) : (
                    <img
                      src={run.last_asset_url}
                      alt="Latest preview"
                      className="w-full h-full object-contain cursor-pointer"
                      onClick={() => openLightbox(run.last_asset_url!)}
                    />
                  )
                ) : (
                  <div className="text-gray-700 flex flex-col items-center gap-2">
                    <ImageIcon size={36} />
                    <span className="text-xs">No preview yet</span>
                  </div>
                )}
              </div>
              {run.last_asset_scene_name && (
                <div className="px-3 py-2 border-t border-gray-800">
                  <span className="text-xs font-medium text-purple-400 bg-purple-500/10 px-2 py-0.5 rounded">
                    {run.last_asset_scene_name}
                  </span>
                </div>
              )}
            </div>

            {/* Scene Progress Grid */}
            <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
              <div className="px-4 py-2.5 border-b border-gray-800 text-sm font-medium text-gray-300">
                Scenes
              </div>
              <div className="p-3">
                {sceneEntries.length === 0 ? (
                  <p className="text-center text-gray-600 text-sm py-4">No scenes recorded yet</p>
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                    {sceneEntries.map(([sceneId, data]) => (
                      <span
                        key={sceneId}
                        className={`text-xs px-2 py-1 rounded-full border truncate max-w-[120px] ${scenePillBorder(data.status || 'pending')}`}
                        title={`${data.scene_name || data.name || sceneId}: ${data.status || 'pending'}`}
                      >
                        {data.scene_name || data.name || sceneId}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* Error Count Badge + Expandable Log */}
            {run.error_log && run.error_log.length > 0 && (
              <div className="bg-gray-900 border border-red-900/40 rounded-lg overflow-hidden">
                <button
                  onClick={() => setShowErrors(!showErrors)}
                  className="w-full px-4 py-2.5 flex items-center justify-between hover:bg-gray-800/50 transition-colors"
                >
                  <div className="flex items-center gap-2 text-sm font-medium text-red-400">
                    <AlertTriangle size={14} />
                    <span>{run.error_log.length} Error{run.error_log.length !== 1 ? 's' : ''}</span>
                  </div>
                  {showErrors ? <ChevronDown size={14} className="text-gray-500" /> : <ChevronRight size={14} className="text-gray-500" />}
                </button>
                {showErrors && (
                  <div className="border-t border-gray-800 max-h-60 overflow-y-auto">
                    {run.error_log.map((entry, i) => (
                      <div key={i} className="px-4 py-2.5 border-b border-gray-800/50 text-sm">
                        <div className="flex items-center gap-2 mb-1">
                          {entry.scene_name && (
                            <span className="text-gray-300 font-medium text-xs">{entry.scene_name}</span>
                          )}
                          {entry.step && (
                            <span className="text-gray-500 text-xs">({entry.step})</span>
                          )}
                          {entry.timestamp && (
                            <span className="text-gray-600 text-xs ml-auto">{formatRelativeTime(entry.timestamp)}</span>
                          )}
                        </div>
                        <p className="text-red-300/80 font-mono text-xs break-all">{entry.error}</p>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Collapsible Run Settings */}
            {run.run_settings && Object.keys(run.run_settings).length > 0 && (
              <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
                <button
                  onClick={() => setShowSettings(!showSettings)}
                  className="w-full px-4 py-2.5 flex items-center justify-between hover:bg-gray-800/50 transition-colors"
                >
                  <div className="flex items-center gap-2 text-sm text-gray-400">
                    <Settings size={14} />
                    <span>Run Settings</span>
                  </div>
                  {showSettings ? <ChevronDown size={14} className="text-gray-500" /> : <ChevronRight size={14} className="text-gray-500" />}
                </button>
                {showSettings && (
                  <div className="border-t border-gray-800 p-4">
                    <pre className="text-xs text-gray-500 font-mono whitespace-pre-wrap break-all">
                      {JSON.stringify(run.run_settings, null, 2)}
                    </pre>
                  </div>
                )}
              </div>
            )}

            {/* Timestamps */}
            <div className="text-xs text-gray-600 flex flex-wrap gap-x-4 gap-y-1 px-1">
              {run.started_at && <span>Started: {formatDate(run.started_at)}</span>}
              {run.completed_at && <span>Finished: {formatDate(run.completed_at)}</span>}
            </div>
          </div>
        </div>
      </div>

      {/* Lightbox overlay */}
      {lightboxOpen && lightboxImages.length > 0 && (
        <Lightbox
          images={lightboxImages}
          currentIndex={lightboxIndex}
          onClose={() => setLightboxOpen(false)}
          onPrev={lightboxPrev}
          onNext={lightboxNext}
        />
      )}
    </div>
  );
}
