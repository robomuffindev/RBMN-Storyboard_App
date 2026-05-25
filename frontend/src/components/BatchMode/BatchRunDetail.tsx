import { useState, useEffect, useRef } from 'react';
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
  ExternalLink,
  Image as ImageIcon,
  Video,
} from 'lucide-react';
import {
  getPersistentBatchRun,
  resumePersistentBatchRun,
  deletePersistentBatchRun,
  cancelSequentialAutoGen,
} from '@/api/client';
import type { PersistentBatchRunDetail as BatchRunDetailType } from '@/types/index';

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

function formatDate(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function statusColor(status: string): string {
  switch (status) {
    case 'running': return 'text-blue-400';
    case 'completed': return 'text-green-400';
    case 'failed': return 'text-red-400';
    case 'cancelled': return 'text-yellow-400';
    case 'paused': return 'text-orange-400';
    case 'done': return 'text-green-400';
    case 'skipped': return 'text-gray-500';
    default: return 'text-gray-400';
  }
}

function StatusIcon({ status, size = 16 }: { status: string; size?: number }) {
  switch (status) {
    case 'running':
      return <Loader2 size={size} className="text-blue-400 animate-spin" />;
    case 'completed':
    case 'done':
      return <CheckCircle size={size} className="text-green-400" />;
    case 'failed':
      return <XCircle size={size} className="text-red-400" />;
    case 'cancelled':
      return <Pause size={size} className="text-yellow-400" />;
    case 'paused':
      return <Pause size={size} className="text-orange-400" />;
    case 'skipped':
      return <ChevronRight size={size} className="text-gray-500" />;
    default:
      return <Clock size={size} className="text-gray-400" />;
  }
}

export default function BatchRunDetail() {
  const navigate = useNavigate();
  const { batchRunId } = useParams<{ batchRunId: string }>();
  const [run, setRun] = useState<BatchRunDetailType | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showErrors, setShowErrors] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchRun = async () => {
    if (!batchRunId) return;
    try {
      const res = await getPersistentBatchRun(batchRunId);
      setRun(res.data);
      // Update preview from last asset
      if (res.data.last_asset_url) {
        setPreviewUrl(res.data.last_asset_url);
      }
    } catch (err: any) {
      setError(err?.response?.data?.detail || 'Failed to load batch run');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchRun();
    pollRef.current = setInterval(fetchRun, 3000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [batchRunId]);

  // Stop polling when run is terminal
  useEffect(() => {
    if (run && run.status !== 'running' && run.status !== 'pending') {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    }
  }, [run?.status]);

  const handleResume = async () => {
    if (!batchRunId) return;
    setResuming(true);
    try {
      await resumePersistentBatchRun(batchRunId);
      // Start polling again
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

  const pct = run.total_scenes > 0
    ? Math.round((run.completed_scenes / run.total_scenes) * 100)
    : 0;

  const sceneEntries = Object.entries(run.scene_results || {}).sort(
    (a, b) => (a[1]?.order ?? 0) - (b[1]?.order ?? 0)
  );

  const canResume = run.status === 'failed' || run.status === 'cancelled' || run.status === 'paused';

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-4 md:p-8">
      <div className="max-w-5xl mx-auto">
        {/* Header */}
        <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center mb-6 gap-4">
          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate('/batches')}
              className="px-3 py-2 bg-gray-800 hover:bg-gray-700 rounded text-sm font-medium transition-colors flex items-center gap-2"
            >
              <ArrowLeft size={18} />
              <span className="hidden sm:inline">Batches</span>
            </button>
            <div>
              <h1 className="text-xl md:text-3xl font-bold">{run.project_name}</h1>
              <div className="flex items-center gap-2 mt-1">
                <StatusIcon status={run.status} />
                <span className={`text-sm font-medium capitalize ${statusColor(run.status)}`}>
                  {run.status}
                </span>
                <span className="text-gray-600 text-sm">|</span>
                <span className="text-gray-400 text-sm capitalize">{run.mode.replace(/_/g, ' ')}</span>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {run.status === 'running' && (
              <button
                onClick={handleCancel}
                className="px-3 py-2 bg-yellow-700 hover:bg-yellow-600 rounded text-sm font-medium transition-colors flex items-center gap-2"
              >
                <Pause size={16} />
                Cancel
              </button>
            )}
            {canResume && (
              <button
                onClick={handleResume}
                disabled={resuming}
                className="px-3 py-2 bg-green-700 hover:bg-green-600 rounded text-sm font-medium transition-colors flex items-center gap-2 disabled:opacity-50"
              >
                {resuming ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}
                Resume
              </button>
            )}
            <button
              onClick={() => navigate(`/project/${run.project_id}`)}
              className="px-3 py-2 bg-gray-800 hover:bg-gray-700 rounded text-sm font-medium transition-colors flex items-center gap-2"
            >
              <ExternalLink size={16} />
              <span className="hidden sm:inline">Open Project</span>
            </button>
            {run.status !== 'running' && (
              <button
                onClick={handleDelete}
                className="px-3 py-2 bg-gray-800 hover:bg-red-900 rounded text-sm font-medium transition-colors text-gray-400 hover:text-red-400 flex items-center gap-2"
              >
                <Trash2 size={16} />
              </button>
            )}
          </div>
        </div>

        {/* Progress section */}
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 md:p-6 mb-6">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm text-gray-400">Progress</span>
            <span className="text-sm font-mono text-gray-300">
              {run.completed_scenes}/{run.total_scenes} scenes ({pct}%)
            </span>
          </div>
          <div className="h-3 bg-gray-800 rounded-full overflow-hidden mb-3">
            <div
              className={`h-full rounded-full transition-all duration-700 ${
                run.status === 'completed' ? 'bg-green-500' :
                run.status === 'failed' ? 'bg-red-500' :
                run.status === 'cancelled' ? 'bg-yellow-500' :
                'bg-blue-500'
              }`}
              style={{ width: `${pct}%` }}
            />
          </div>
          <div className="flex flex-wrap gap-4 text-sm text-gray-400">
            {run.current_step && run.status === 'running' && (
              <div className="flex items-center gap-2">
                <Loader2 size={14} className="animate-spin text-blue-400" />
                <span>{run.current_scene_name && `${run.current_scene_name} — `}{run.current_step}</span>
              </div>
            )}
            <div className="flex items-center gap-1">
              <Clock size={14} />
              <span>{formatElapsed(run.elapsed_ms)}</span>
            </div>
            {run.started_at && (
              <span>Started: {formatDate(run.started_at)}</span>
            )}
            {run.completed_at && (
              <span>Finished: {formatDate(run.completed_at)}</span>
            )}
          </div>
        </div>

        {/* Preview + Scene list side by side on desktop */}
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-6 mb-6">
          {/* Preview panel */}
          <div className="lg:col-span-2">
            <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
              <div className="px-4 py-3 border-b border-gray-800 text-sm font-medium text-gray-300">
                Latest Preview
              </div>
              <div className="aspect-video bg-black flex items-center justify-center">
                {previewUrl ? (
                  previewUrl.match(/\.(mp4|webm)$/i) ? (
                    <video
                      key={previewUrl}
                      src={previewUrl}
                      autoPlay
                      loop
                      muted
                      playsInline
                      className="w-full h-full object-contain"
                    />
                  ) : (
                    <img
                      src={previewUrl}
                      alt="Preview"
                      className="w-full h-full object-contain"
                    />
                  )
                ) : (
                  <div className="text-gray-600 flex flex-col items-center gap-2">
                    <ImageIcon size={40} />
                    <span className="text-sm">No preview yet</span>
                  </div>
                )}
              </div>
              {run.last_asset_scene_name && (
                <div className="px-4 py-2 border-t border-gray-800 text-xs text-gray-500">
                  {run.last_asset_scene_name}
                </div>
              )}
            </div>
          </div>

          {/* Scene checklist */}
          <div className="lg:col-span-3">
            <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
              <div className="px-4 py-3 border-b border-gray-800 text-sm font-medium text-gray-300">
                Scene Progress
              </div>
              <div className="max-h-96 overflow-y-auto">
                {sceneEntries.length === 0 ? (
                  <div className="p-6 text-center text-gray-500 text-sm">
                    No scene results recorded yet.
                  </div>
                ) : (
                  <div className="divide-y divide-gray-800">
                    {sceneEntries.map(([sceneId, data]) => (
                      <div key={sceneId} className="flex items-center gap-3 px-4 py-2.5 text-sm">
                        <StatusIcon status={data.status || 'pending'} size={14} />
                        <span className="flex-1 truncate">{data.name || sceneId}</span>
                        {data.image_path && (
                          <span className="text-gray-600" title="Image done">
                            <ImageIcon size={14} className="text-green-600" />
                          </span>
                        )}
                        {data.video_path && (
                          <span className="text-gray-600" title="Video done">
                            <Video size={14} className="text-green-600" />
                          </span>
                        )}
                        <span className={`text-xs capitalize ${statusColor(data.status || 'pending')}`}>
                          {data.status || 'pending'}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Error log (collapsible) */}
        {run.error_log && run.error_log.length > 0 && (
          <div className="bg-gray-900 border border-red-900/40 rounded-lg overflow-hidden mb-6">
            <button
              onClick={() => setShowErrors(!showErrors)}
              className="w-full px-4 py-3 flex items-center justify-between hover:bg-gray-800/50 transition-colors"
            >
              <div className="flex items-center gap-2 text-sm font-medium text-red-400">
                <AlertTriangle size={16} />
                Errors ({run.error_log.length})
              </div>
              {showErrors ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
            </button>
            {showErrors && (
              <div className="border-t border-gray-800 max-h-80 overflow-y-auto">
                {run.error_log.map((entry, i) => (
                  <div key={i} className="px-4 py-3 border-b border-gray-800/50 text-sm">
                    <div className="flex items-center gap-2 mb-1">
                      {entry.scene_name && (
                        <span className="text-gray-300 font-medium">{entry.scene_name}</span>
                      )}
                      {entry.step && (
                        <span className="text-gray-500">({entry.step})</span>
                      )}
                      {entry.timestamp && (
                        <span className="text-gray-600 text-xs ml-auto">{formatDate(entry.timestamp)}</span>
                      )}
                    </div>
                    <p className="text-red-300/80 font-mono text-xs break-all">{entry.error}</p>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Run settings (collapsible) */}
        {run.run_settings && Object.keys(run.run_settings).length > 0 && (
          <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden mb-6">
            <button
              onClick={() => setShowSettings(!showSettings)}
              className="w-full px-4 py-3 flex items-center justify-between hover:bg-gray-800/50 transition-colors"
            >
              <span className="text-sm font-medium text-gray-300">Run Settings</span>
              {showSettings ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
            </button>
            {showSettings && (
              <div className="border-t border-gray-800 p-4">
                <pre className="text-xs text-gray-400 font-mono whitespace-pre-wrap break-all">
                  {JSON.stringify(run.run_settings, null, 2)}
                </pre>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
