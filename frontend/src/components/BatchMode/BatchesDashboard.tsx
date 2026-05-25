import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ArrowLeft,
  Settings,
  Play,
  Pause,
  CheckCircle,
  XCircle,
  AlertTriangle,
  Clock,
  Trash2,
  RefreshCw,
  Eye,
  Loader2,
} from 'lucide-react';
import { listPersistentBatchRuns, deletePersistentBatchRun } from '@/api/client';
import type { PersistentBatchRunSummary } from '@/types/index';

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
  return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function statusColor(status: string): string {
  switch (status) {
    case 'running': return 'text-blue-400';
    case 'completed': return 'text-green-400';
    case 'failed': return 'text-red-400';
    case 'cancelled': return 'text-yellow-400';
    case 'paused': return 'text-orange-400';
    default: return 'text-gray-400';
  }
}

function statusBorder(status: string): string {
  switch (status) {
    case 'running': return 'border-blue-600';
    case 'completed': return 'border-green-600/40';
    case 'failed': return 'border-red-600/40';
    case 'cancelled': return 'border-yellow-600/40';
    case 'paused': return 'border-orange-600/40';
    default: return 'border-gray-700';
  }
}

function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case 'running':
      return <Loader2 size={18} className="text-blue-400 animate-spin" />;
    case 'completed':
      return <CheckCircle size={18} className="text-green-400" />;
    case 'failed':
      return <XCircle size={18} className="text-red-400" />;
    case 'cancelled':
      return <Pause size={18} className="text-yellow-400" />;
    case 'paused':
      return <Pause size={18} className="text-orange-400" />;
    default:
      return <Clock size={18} className="text-gray-400" />;
  }
}

export default function BatchesDashboard() {
  const navigate = useNavigate();
  const [runs, setRuns] = useState<PersistentBatchRunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<'all' | 'running' | 'completed' | 'failed'>('all');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchRuns = async () => {
    try {
      const res = await listPersistentBatchRuns();
      setRuns(res.data);
    } catch (err) {
      console.error('Failed to fetch batch runs:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchRuns();
    // Poll every 5s while any run is active
    pollRef.current = setInterval(fetchRuns, 5000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const handleDelete = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm('Delete this batch run record?')) return;
    try {
      await deletePersistentBatchRun(id);
      setRuns((prev) => prev.filter((r) => r.id !== id));
    } catch (err) {
      console.error('Failed to delete batch run:', err);
    }
  };

  const filtered = runs.filter((r) => {
    if (filter === 'all') return true;
    if (filter === 'running') return r.status === 'running' || r.status === 'paused';
    if (filter === 'completed') return r.status === 'completed';
    if (filter === 'failed') return r.status === 'failed' || r.status === 'cancelled';
    return true;
  });

  const runningCount = runs.filter((r) => r.status === 'running').length;
  const completedCount = runs.filter((r) => r.status === 'completed').length;
  const failedCount = runs.filter((r) => r.status === 'failed' || r.status === 'cancelled').length;

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-4 md:p-8">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center mb-6 gap-4">
          <div className="flex items-center gap-4">
            <button
              onClick={() => navigate('/')}
              className="px-3 py-2 bg-gray-800 hover:bg-gray-700 rounded text-sm font-medium transition-colors flex items-center gap-2"
            >
              <ArrowLeft size={18} />
              <span className="hidden sm:inline">Back</span>
            </button>
            <h1 className="text-2xl md:text-4xl font-bold">Batch Runs</h1>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={fetchRuns}
              className="px-3 py-2 bg-gray-800 hover:bg-gray-700 rounded text-sm font-medium transition-colors flex items-center gap-2"
            >
              <RefreshCw size={16} />
              <span className="hidden sm:inline">Refresh</span>
            </button>
            <button
              onClick={() => navigate('/settings')}
              className="px-3 py-2 bg-gray-800 hover:bg-gray-700 rounded text-sm font-medium transition-colors flex items-center gap-2"
            >
              <Settings size={16} />
              <span className="hidden sm:inline">Settings</span>
            </button>
          </div>
        </div>

        {/* Summary stats */}
        <div className="grid grid-cols-3 gap-3 md:gap-6 mb-6">
          <button
            onClick={() => setFilter(filter === 'running' ? 'all' : 'running')}
            className={`p-3 md:p-4 rounded-lg border transition-colors ${
              filter === 'running' ? 'bg-blue-900/30 border-blue-600' : 'bg-gray-900 border-gray-800 hover:border-gray-700'
            }`}
          >
            <div className="text-2xl md:text-3xl font-bold text-blue-400">{runningCount}</div>
            <div className="text-xs md:text-sm text-gray-400 mt-1">Active</div>
          </button>
          <button
            onClick={() => setFilter(filter === 'completed' ? 'all' : 'completed')}
            className={`p-3 md:p-4 rounded-lg border transition-colors ${
              filter === 'completed' ? 'bg-green-900/30 border-green-600' : 'bg-gray-900 border-gray-800 hover:border-gray-700'
            }`}
          >
            <div className="text-2xl md:text-3xl font-bold text-green-400">{completedCount}</div>
            <div className="text-xs md:text-sm text-gray-400 mt-1">Completed</div>
          </button>
          <button
            onClick={() => setFilter(filter === 'failed' ? 'all' : 'failed')}
            className={`p-3 md:p-4 rounded-lg border transition-colors ${
              filter === 'failed' ? 'bg-red-900/30 border-red-600' : 'bg-gray-900 border-gray-800 hover:border-gray-700'
            }`}
          >
            <div className="text-2xl md:text-3xl font-bold text-red-400">{failedCount}</div>
            <div className="text-xs md:text-sm text-gray-400 mt-1">Failed</div>
          </button>
        </div>

        {/* Filter label */}
        {filter !== 'all' && (
          <div className="mb-4 flex items-center gap-2">
            <span className="text-sm text-gray-400">Showing:</span>
            <span className="text-sm font-medium capitalize">{filter}</span>
            <button
              onClick={() => setFilter('all')}
              className="text-xs text-gray-500 hover:text-gray-300 underline ml-2"
            >
              Clear filter
            </button>
          </div>
        )}

        {/* Batch run cards */}
        {loading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 size={32} className="animate-spin text-gray-500" />
          </div>
        ) : filtered.length === 0 ? (
          <div className="bg-gray-900 border border-gray-800 rounded-lg text-center py-12 px-6">
            <p className="text-gray-400">
              {runs.length === 0
                ? 'No batch runs yet. Start an Auto Gen from a project to create one.'
                : 'No runs match this filter.'}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {filtered.map((run) => {
              const pct = run.total_scenes > 0
                ? Math.round((run.completed_scenes / run.total_scenes) * 100)
                : 0;

              return (
                <div
                  key={run.id}
                  onClick={() => navigate(`/batches/${run.id}`)}
                  className={`bg-gray-900 border rounded-lg overflow-hidden hover:shadow-lg transition-all cursor-pointer group ${statusBorder(run.status)}`}
                >
                  {/* Thumbnail / last asset preview */}
                  <div className="h-32 md:h-40 bg-gradient-to-br from-gray-800 to-gray-900 relative overflow-hidden">
                    {run.last_asset_url ? (
                      <img
                        src={run.last_asset_url}
                        alt="Last generated"
                        className="w-full h-full object-cover opacity-60 group-hover:opacity-80 transition-opacity"
                      />
                    ) : (
                      <div className="w-full h-full flex items-center justify-center">
                        <Play size={40} className="text-gray-700" />
                      </div>
                    )}

                    {/* Progress overlay */}
                    {run.status === 'running' && (
                      <div className="absolute bottom-0 left-0 right-0 h-1.5 bg-gray-700">
                        <div
                          className="h-full bg-blue-500 transition-all duration-500"
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                    )}

                    {/* Status badge */}
                    <div className="absolute top-2 right-2">
                      <div className={`flex items-center gap-1.5 px-2 py-1 rounded-full bg-gray-900/80 backdrop-blur-sm text-xs font-medium ${statusColor(run.status)}`}>
                        <StatusIcon status={run.status} />
                        <span className="capitalize">{run.status}</span>
                      </div>
                    </div>

                    {/* Last scene name */}
                    {run.last_asset_scene_name && run.last_asset_url && (
                      <div className="absolute bottom-2 left-2 text-xs text-white/70 bg-black/50 rounded px-1.5 py-0.5">
                        {run.last_asset_scene_name}
                      </div>
                    )}
                  </div>

                  {/* Card body */}
                  <div className="p-4">
                    <h3 className="text-lg font-semibold mb-1 truncate">{run.project_name}</h3>
                    <div className="flex items-center gap-3 text-sm text-gray-400 mb-3">
                      <span className="capitalize">{run.mode.replace(/_/g, ' ')}</span>
                      <span className="text-gray-600">|</span>
                      <span>{run.completed_scenes}/{run.total_scenes} scenes</span>
                    </div>

                    {/* Progress bar for non-running too */}
                    <div className="h-1.5 bg-gray-800 rounded-full mb-3 overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all duration-500 ${
                          run.status === 'completed' ? 'bg-green-500' :
                          run.status === 'failed' ? 'bg-red-500' :
                          run.status === 'cancelled' ? 'bg-yellow-500' :
                          'bg-blue-500'
                        }`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>

                    {/* Current step / info line */}
                    <div className="text-xs text-gray-500 mb-3 h-4 truncate">
                      {run.status === 'running' && run.current_step
                        ? `${run.current_scene_name || ''} — ${run.current_step}`
                        : run.status === 'completed'
                          ? `Completed in ${formatElapsed(run.elapsed_ms)}`
                          : run.status === 'failed' && run.error_count > 0
                            ? `${run.error_count} error${run.error_count > 1 ? 's' : ''}`
                            : formatDate(run.started_at)
                      }
                    </div>

                    {/* Footer: error badge + actions */}
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        {run.error_count > 0 && (
                          <span className="flex items-center gap-1 text-xs text-red-400 bg-red-900/30 px-2 py-0.5 rounded-full">
                            <AlertTriangle size={12} />
                            {run.error_count}
                          </span>
                        )}
                        <span className="text-xs text-gray-600">{formatDate(run.created_at)}</span>
                      </div>
                      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            navigate(`/batches/${run.id}`);
                          }}
                          className="p-1.5 hover:bg-gray-700 rounded transition-colors"
                          title="View details"
                        >
                          <Eye size={14} />
                        </button>
                        {run.status !== 'running' && (
                          <button
                            onClick={(e) => handleDelete(run.id, e)}
                            className="p-1.5 hover:bg-red-900 rounded transition-colors text-gray-400 hover:text-red-400"
                            title="Delete"
                          >
                            <Trash2 size={14} />
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
