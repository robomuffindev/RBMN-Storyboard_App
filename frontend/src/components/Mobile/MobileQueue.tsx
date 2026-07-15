import { useEffect, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Loader2, XCircle, RefreshCw, Trash2, CheckCircle, Clock, Layers, ChevronRight,
} from 'lucide-react';
import {
  getProject, getJobs, cancelJob, retryJob, deleteJob,
  getSequentialAutoGenStatus, listPersistentBatchRuns,
} from '@/api/client';
import { useAppStore } from '@/store';
import type { Job, PersistentBatchRunSummary } from '@/types/index';
import MobileShell from './MobileShell';

const isRunning = (s?: string) => s === 'running' || s === 'pending';

export default function MobileQueue() {
  const { id } = useParams<{ id: string }>();
  const projectId = id!;
  const navigate = useNavigate();
  const setJobs = useAppStore((s) => s.setJobs);
  const jobs = useAppStore((s) => s.jobs);

  const projectQ = useQuery({ queryKey: ['project', projectId], queryFn: async () => (await getProject(projectId)).data });

  const refresh = async () => {
    try { setJobs((await getJobs(projectId)).data); } catch { /* ignore */ }
  };
  useEffect(() => { refresh(); /* seed; SSE keeps it live */ // eslint-disable-next-line
  }, [projectId]);

  const statusQ = useQuery({
    queryKey: ['autogen-status', projectId],
    queryFn: async () => (await getSequentialAutoGenStatus(projectId)).data,
    refetchInterval: (q) => (isRunning((q.state.data as any)?.status) ? 3000 : false),
  });
  const st = statusQ.data;
  const agPct = st && st.total_scenes ? Math.round((st.completed_scenes / st.total_scenes) * 100) : 0;

  const batchesQ = useQuery({
    queryKey: ['batch-runs', projectId],
    queryFn: async () => (await listPersistentBatchRuns(projectId)).data,
    refetchInterval: 5000,
  });

  const projJobs = useMemo(
    () => jobs.filter((j) => j.project_id === projectId)
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()),
    [jobs, projectId],
  );
  const processing = projJobs.filter((j) => j.status === 'pending' || j.status === 'running');
  const failed = projJobs.filter((j) => j.status === 'failed' || j.status === 'retrying' || j.status === 'cancelled');
  const done = projJobs.filter((j) => j.status === 'done').slice(0, 12);

  const act = async (fn: () => Promise<any>) => { await fn(); await refresh(); };

  return (
    <MobileShell
      projectId={projectId} title={projectQ.data?.name || 'Project'} subtitle="Generation queue" active="queue"
      right={<button onClick={refresh} className="p-2 rounded-lg active:bg-gray-800 text-gray-300"><RefreshCw className="w-5 h-5" /></button>}
    >
      <div className="p-3 space-y-4">
        {/* Auto-gen status */}
        {st && isRunning(st.status) && (
          <div className="rounded-xl bg-indigo-950/40 border border-indigo-800/50 p-3">
            <div className="flex items-center gap-2 mb-1.5">
              <Loader2 className="w-4 h-4 animate-spin text-indigo-400" />
              <span className="text-sm font-semibold">Auto-Gen</span>
              <span className="ml-auto text-xs text-gray-400">{st.completed_scenes}/{st.total_scenes}</span>
            </div>
            <div className="h-1.5 rounded-full bg-gray-800 overflow-hidden">
              <div className="h-full bg-indigo-500" style={{ width: `${agPct}%` }} />
            </div>
            {st.batch_run_id && (
              <button onClick={() => navigate(`/mobile/batch/${st.batch_run_id}`)} className="mt-2 text-xs text-indigo-300 flex items-center gap-1">
                View live details <ChevronRight className="w-3 h-3" />
              </button>
            )}
          </div>
        )}

        {/* Batch runs */}
        {(batchesQ.data || []).length > 0 && (
          <section>
            <h3 className="text-xs font-semibold text-gray-400 uppercase mb-2 flex items-center gap-1.5"><Layers className="w-3.5 h-3.5" /> Batch runs</h3>
            <div className="space-y-2">
              {(batchesQ.data as PersistentBatchRunSummary[]).slice(0, 8).map((b) => {
                const pct = b.total_scenes ? Math.round((b.completed_scenes / b.total_scenes) * 100) : 0;
                return (
                  <button key={b.id} onClick={() => navigate(`/mobile/batch/${b.id}`)} className="w-full text-left rounded-xl bg-gray-900 border border-gray-800 active:bg-gray-800 p-3">
                    <div className="flex items-center gap-2">
                      <StatusDot status={b.status} />
                      <span className="text-sm font-medium capitalize">{b.status}</span>
                      <span className="ml-auto text-xs text-gray-500">{b.completed_scenes}/{b.total_scenes}</span>
                      <ChevronRight className="w-4 h-4 text-gray-600" />
                    </div>
                    <div className="h-1.5 rounded-full bg-gray-800 overflow-hidden mt-2">
                      <div className={`h-full ${b.status === 'failed' ? 'bg-red-500' : 'bg-indigo-500'}`} style={{ width: `${pct}%` }} />
                    </div>
                    {b.current_scene_name && <div className="text-[11px] text-gray-500 mt-1 truncate">{b.current_scene_name}{b.current_step ? ` — ${b.current_step}` : ''}</div>}
                  </button>
                );
              })}
            </div>
          </section>
        )}

        {/* Jobs */}
        <JobGroup title="Processing" icon={<Loader2 className="w-3.5 h-3.5 animate-spin" />} jobs={processing} onCancel={(j) => act(() => cancelJob(j.id))} />
        <JobGroup title="Failed" icon={<XCircle className="w-3.5 h-3.5 text-red-400" />} jobs={failed} onRetry={(j) => act(() => retryJob(j.id))} onDelete={(j) => act(() => deleteJob(j.id))} />
        <JobGroup title="Completed" icon={<CheckCircle className="w-3.5 h-3.5 text-emerald-400" />} jobs={done} onDelete={(j) => act(() => deleteJob(j.id))} />

        {projJobs.length === 0 && (
          <div className="text-center text-gray-500 py-16">
            <Clock className="w-8 h-8 mx-auto mb-2 opacity-50" /> No jobs yet.
          </div>
        )}
      </div>
    </MobileShell>
  );
}

function StatusDot({ status }: { status: string }) {
  const c = status === 'running' || status === 'pending' ? 'bg-indigo-400 animate-pulse'
    : status === 'completed' ? 'bg-emerald-400'
    : status === 'failed' ? 'bg-red-400'
    : status === 'paused' ? 'bg-amber-400' : 'bg-gray-500';
  return <span className={`w-2.5 h-2.5 rounded-full ${c}`} />;
}

function JobGroup({ title, icon, jobs, onCancel, onRetry, onDelete }: {
  title: string; icon: React.ReactNode; jobs: Job[];
  onCancel?: (j: Job) => void; onRetry?: (j: Job) => void; onDelete?: (j: Job) => void;
}) {
  if (jobs.length === 0) return null;
  return (
    <section>
      <h3 className="text-xs font-semibold text-gray-400 uppercase mb-2 flex items-center gap-1.5">{icon} {title} ({jobs.length})</h3>
      <div className="space-y-2">
        {jobs.map((j) => (
          <div key={j.id} className="rounded-xl bg-gray-900 border border-gray-800 p-3">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium truncate">{j.scene_name || j.job_type}</span>
              <span className="ml-auto text-[10px] uppercase text-gray-500">{j.job_type}</span>
            </div>
            {(j.status === 'running' || j.status === 'pending') && (
              <div className="mt-2">
                <div className="h-1.5 rounded-full bg-gray-800 overflow-hidden">
                  <div className="h-full bg-indigo-500 transition-all" style={{ width: `${Math.max(4, j.progress || 0)}%` }} />
                </div>
                {j.current_node && <div className="text-[10px] text-gray-500 mt-1 truncate">{j.current_node}</div>}
              </div>
            )}
            {j.error && <div className="text-[11px] text-red-400 mt-1 line-clamp-2">{j.error}</div>}
            <div className="flex gap-2 mt-2">
              {onCancel && (j.status === 'running' || j.status === 'pending') && (
                <button onClick={() => onCancel(j)} className="flex-1 py-2 rounded-lg bg-gray-800 active:bg-gray-700 text-xs font-medium">Cancel</button>
              )}
              {onRetry && <button onClick={() => onRetry(j)} className="flex-1 py-2 rounded-lg bg-indigo-600 active:bg-indigo-700 text-xs font-medium">Retry</button>}
              {onDelete && <button onClick={() => onDelete(j)} className="px-3 py-2 rounded-lg bg-gray-800 active:bg-gray-700 text-gray-400"><Trash2 className="w-4 h-4" /></button>}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
