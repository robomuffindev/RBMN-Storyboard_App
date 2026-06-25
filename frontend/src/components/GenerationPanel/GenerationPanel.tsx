import { useState, useEffect } from 'react';
import { useAppStore } from '@/store';
import { useMutation } from '@tanstack/react-query';
import { cancelJob, retryJob, deleteJob } from '@/api/client';
import { X, RotateCcw, Trash2, Loader, CheckCircle, AlertCircle, Server, Film, Clock } from 'lucide-react';
import type { Job } from '@/types/index';
import { parseBackendMs, parseBackendDate } from '@/utils/time';

/** Live elapsed timer that ticks every second */
function ElapsedTimer({ startedAt, completedAt }: { startedAt?: string; completedAt?: string }) {
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    if (completedAt) return; // Static — no ticking needed
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [completedAt]);

  if (!startedAt) return null;

  const start = parseBackendMs(startedAt) ?? 0;
  const end = completedAt ? (parseBackendMs(completedAt) ?? now) : now;
  const secs = Math.max(0, Math.floor((end - start) / 1000));

  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  let label: string;
  if (h > 0) label = `${h}h ${m}m ${s}s`;
  else if (m > 0) label = `${m}m ${s}s`;
  else label = `${s}s`;

  return (
    <span className="inline-flex items-center gap-1 text-xs text-gray-400">
      <Clock size={11} className="text-blue-400" />
      {completedAt ? label : <span className="text-blue-400">{label}</span>}
    </span>
  );
}

export default function GenerationPanel() {
  const { jobs, scenes } = useAppStore();
  const setActiveScene = useAppStore((s) => s.setActiveScene);
  const setPlaybackPosition = useAppStore((s) => s.setPlaybackPosition);
  const setIsPlaying = useAppStore((s) => s.setIsPlaying);

  // Helper to resolve scene_id to the full Scene object so we can both
  // display its name AND navigate to it in the timeline on click.
  const getSceneForJob = (sceneId?: string) => {
    if (!sceneId) return null;
    return scenes.find((s) => s.id === sceneId) || null;
  };

  // Click handler: select scene + seek playhead so the SceneEditor's
  // tabbed panel switches to show this scene's info (same pattern Story
  // Flow uses for its scene title clicks — see AppLayout.tsx
  // `goToSceneInTimeline`).
  const goToScene = (scene: { id: string; start_time: number } | null) => {
    if (!scene) return;
    setActiveScene(scene as any);
    setPlaybackPosition(scene.start_time);
    setIsPlaying(false);
  };

  // Helper to extract short IP/host from worker URL
  const getWorkerLabel = (workerUrl?: string) => {
    if (!workerUrl) return null;
    try {
      const url = new URL(workerUrl);
      return url.hostname + (url.port ? ':' + url.port : '');
    } catch {
      return workerUrl;
    }
  };

  // Resolve the dispatched workflow_type to a human-friendly model badge.
  // The workflow_type is the ground truth of what actually went out to
  // ComfyUI (after Pass-1 Z-Image redirects, etc.) — so this badge
  // reflects what the worker is REALLY running, not just what the user
  // configured in Settings.
  const getModelBadge = (job: Job): { label: string; cls: string } | null => {
    const wf = (job.parameters?.workflow_type as string | undefined) || '';
    if (!wf) return null;
    // Z-Image Turbo (always wins for Pass 1 and for T2I when configured)
    if (wf === 'z_image_turbo') {
      return { label: 'Z-Image Turbo', cls: 'bg-emerald-700 text-emerald-50' };
    }
    if (wf === 'krea2_turbo' || wf === 'krea2_t2i') {
      return { label: 'Krea 2 Turbo', cls: 'bg-fuchsia-700 text-fuchsia-50' };
    }
    // Klein 9B — reference-conditioned or T2I fallback
    if (wf.startsWith('klein_')) {
      // Show the ref count for refs workflows so a `klein_5ref` job is
      // visibly distinct from a `klein_t2i` job at a glance.
      const refMatch = /^klein_(\d)ref$/.exec(wf);
      if (refMatch) {
        return { label: `Klein 9B · ${refMatch[1]}REF`, cls: 'bg-amber-700 text-amber-50' };
      }
      if (wf === 'klein_t2i') {
        return { label: 'Klein 9B · T2I', cls: 'bg-amber-700 text-amber-50' };
      }
      return { label: 'Klein 9B', cls: 'bg-amber-700 text-amber-50' };
    }
    // LTX video family
    if (wf.startsWith('ltx_')) {
      // ltx_fflf / ltx_i2v / ltx_v2v_extend / ltx_v2v_pass{1,2} / ltx_transition / ltx_seq_*
      const tail = wf.replace(/^ltx_/, '').toUpperCase().replace(/_/g, ' ');
      return { label: `LTX 2.3 · ${tail}`, cls: 'bg-purple-700 text-purple-50' };
    }
    // Unknown — show raw so it's debuggable
    return { label: wf, cls: 'bg-gray-700 text-gray-200' };
  };

  const cancelMutation = useMutation({
    mutationFn: async (jobId: string) => {
      await cancelJob(jobId);
      return jobId;
    },
    onSuccess: (jobId: string) => {
      useAppStore.getState().updateJob(jobId, { status: 'failed', error: 'Cancelled by user' });
    },
  });

  const retryMutation = useMutation({
    mutationFn: async (jobId: string) => {
      const response = await retryJob(jobId);
      return { jobId, data: response.data };
    },
    onSuccess: ({ jobId }) => {
      useAppStore.getState().updateJob(jobId, { status: 'pending', error: undefined, progress: 0 });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (jobId: string) => {
      await deleteJob(jobId);
      return jobId;
    },
    onSuccess: (jobId: string) => {
      useAppStore.getState().removeJob(jobId);
    },
  });

  const safeJobs = jobs || [];
  const activeJobs = safeJobs.filter(
    (job) => job.status === 'pending' || job.status === 'running'
  );

  const completedJobs = safeJobs.filter((job) => job.status === 'done');
  const failedJobs = safeJobs.filter((job) => ['failed', 'retrying', 'cancelled'].includes(job.status));

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'running':
      case 'pending':
        return <Loader className="animate-spin text-blue-500" size={18} />;
      case 'done':
        return <CheckCircle className="text-green-500" size={18} />;
      case 'failed':
      case 'retrying':
      case 'cancelled':
        return <AlertCircle className="text-red-500" size={18} />;
      default:
        return null;
    }
  };

  const getStatusLabel = (status: string) => {
    switch (status) {
      case 'done':
        return 'Completed';
      case 'retrying':
        return 'Retrying';
      case 'cancelled':
        return 'Cancelled';
      default:
        return status.charAt(0).toUpperCase() + status.slice(1);
    }
  };

  const JobCard = ({ job }: { job: Job }) => {
    const sceneObj = getSceneForJob(job.scene_id);
    // Prefer the backend-resolved snapshot (`job.scene_name`) so the
    // chip remains visible even when the scene was deleted or the user
    // navigated to a different project (the local `scenes` array won't
    // contain the row in that case).  Fall back to the live lookup so
    // older job records — created before the backend was teaching the
    // /jobs response to carry scene_name — still display.
    const sceneName = job.scene_name || (sceneObj ? sceneObj.name : null);
    const workerLabel = getWorkerLabel(job.worker_url);
    const twoPassPhase = job.parameters?.two_pass_phase;
    const modelBadge = getModelBadge(job);

    return (
    <div className="p-3 bg-gray-800 rounded mb-2 border border-gray-700">
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2 flex-1 min-w-0">
          {getStatusIcon(job.status)}
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium truncate capitalize flex items-center gap-2 flex-wrap">
              {job.job_type} Generation
              {twoPassPhase && (
                <span
                  className="text-[10px] font-semibold bg-blue-600 text-white px-2 py-0.5 rounded whitespace-nowrap"
                  title={twoPassPhase === 'base'
                    ? 'Pass 1 — scene composition only (Z-Image Turbo)'
                    : 'Pass 2 — character compositing on top of Pass 1 (Klein 9B)'}
                >
                  {twoPassPhase === 'base' ? 'Pass 1/2' : 'Pass 2/2'}
                </span>
              )}
              {modelBadge && (
                <span
                  className={`text-[10px] font-semibold px-2 py-0.5 rounded whitespace-nowrap font-mono ${modelBadge.cls}`}
                  title={`Workflow type: ${job.parameters?.workflow_type}`}
                >
                  {modelBadge.label}
                </span>
              )}
            </p>
            <p className="text-xs text-gray-400">{getStatusLabel(job.status)}</p>
          </div>
        </div>
        {(job.status === 'running' || job.status === 'pending') && (
          <button
            onClick={() => cancelMutation.mutate(job.id)}
            className="text-gray-400 hover:text-red-500 transition-colors flex-shrink-0"
            disabled={cancelMutation.isPending}
            title="Cancel job"
          >
            <X size={16} />
          </button>
        )}
        {['failed', 'retrying', 'cancelled'].includes(job.status) && (
          <div className="flex items-center gap-1 flex-shrink-0">
            <button
              onClick={() => retryMutation.mutate(job.id)}
              className="text-gray-400 hover:text-blue-500 transition-colors"
              disabled={retryMutation.isPending}
              title="Retry job with same settings"
            >
              <RotateCcw size={16} />
            </button>
            <button
              onClick={() => deleteMutation.mutate(job.id)}
              className="text-gray-400 hover:text-red-500 transition-colors"
              disabled={deleteMutation.isPending}
              title="Delete job"
            >
              <Trash2 size={14} />
            </button>
          </div>
        )}
        {job.status === 'done' && (
          <button
            onClick={() => deleteMutation.mutate(job.id)}
            className="text-gray-400 hover:text-red-500 transition-colors flex-shrink-0"
            disabled={deleteMutation.isPending}
            title="Delete job"
          >
            <Trash2 size={14} />
          </button>
        )}
      </div>

      {/* Scene and Worker info */}
      {(sceneName || workerLabel) && (
        <div className="flex flex-wrap gap-x-3 gap-y-1 mb-2">
          {sceneName && (
            sceneObj ? (
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); goToScene(sceneObj); }}
                className="inline-flex items-center gap-1 text-xs text-gray-400 hover:text-purple-300 hover:underline focus:outline-none focus:ring-1 focus:ring-purple-500 rounded"
                title={`Jump to ${sceneName} in the timeline`}
              >
                <Film size={12} className="text-purple-400 flex-shrink-0" />
                <span className="truncate max-w-[120px]">{sceneName}</span>
              </button>
            ) : (
              <span className="inline-flex items-center gap-1 text-xs text-gray-500" title={sceneName}>
                <Film size={12} className="text-purple-400 flex-shrink-0" />
                <span className="truncate max-w-[120px]">{sceneName}</span>
              </span>
            )
          )}
          {workerLabel && (
            <span className="inline-flex items-center gap-1 text-xs text-gray-400">
              <Server size={12} className="text-cyan-400 flex-shrink-0" />
              <span className="truncate max-w-[120px]" title={job.worker_url}>{workerLabel}</span>
            </span>
          )}
        </div>
      )}

      {(job.status === 'running' || job.status === 'pending') && (
        <div className="space-y-1">
          <div className="w-full bg-gray-700 rounded-full h-2 overflow-hidden">
            <div
              className="bg-blue-500 h-full transition-all"
              style={{ width: `${job.progress || 0}%` }}
            />
          </div>
          {job.current_node && (
            <p className="text-xs text-gray-400 truncate">{job.current_node}</p>
          )}
        </div>
      )}

      {job.error && (
        <p className="text-xs text-red-400 mt-2 truncate" title={job.error}>{job.error}</p>
      )}

      {job.started_at && (
        <div className="flex items-center justify-between mt-2">
          <span className="text-xs text-gray-500">
            {parseBackendDate(job.started_at)?.toLocaleTimeString() ?? ''}
          </span>
          <ElapsedTimer
            startedAt={job.started_at}
            completedAt={job.completed_at}
          />
        </div>
      )}
    </div>
  );
  };

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Header */}
      <div className="p-4 border-b border-gray-800">
        <h3 className="font-semibold text-sm">Generation Queue</h3>
        <p className="text-xs text-gray-400 mt-1">{safeJobs.length} total jobs</p>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* Active Jobs */}
        {activeJobs.length > 0 && (
          <div>
            <p className="text-xs font-medium text-gray-400 mb-2 uppercase tracking-wider">
              Processing ({activeJobs.length})
            </p>
            {activeJobs.map((job) => (
              <JobCard key={job.id} job={job} />
            ))}
          </div>
        )}

        {/* Completed Jobs */}
        {completedJobs.length > 0 && (
          <div>
            <p className="text-xs font-medium text-gray-400 mb-2 uppercase tracking-wider">
              Completed ({completedJobs.length})
            </p>
            <div className="space-y-2">
              {completedJobs.slice(0, 3).map((job) => (
                <JobCard key={job.id} job={job} />
              ))}
              {completedJobs.length > 3 && (
                <p className="text-xs text-gray-500 text-center py-2">
                  +{completedJobs.length - 3} more
                </p>
              )}
            </div>
          </div>
        )}

        {/* Failed Jobs */}
        {failedJobs.length > 0 && (
          <div>
            <p className="text-xs font-medium text-gray-400 mb-2 uppercase tracking-wider">
              Failed ({failedJobs.length})
            </p>
            <div className="space-y-2">
              {failedJobs.slice(0, 3).map((job) => (
                <JobCard key={job.id} job={job} />
              ))}
              {failedJobs.length > 3 && (
                <p className="text-xs text-gray-500 text-center py-2">
                  +{failedJobs.length - 3} more
                </p>
              )}
            </div>
          </div>
        )}

        {/* Empty State */}
        {safeJobs.length === 0 && (
          <div className="text-center text-gray-400 py-8">
            <p className="text-sm">No generation jobs yet</p>
            <p className="text-xs text-gray-500 mt-2">
              Generate images or videos to see progress here
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
