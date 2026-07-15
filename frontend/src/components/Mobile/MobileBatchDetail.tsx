import { useNavigate, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, Loader2, Play, StopCircle } from 'lucide-react';
import { getPersistentBatchRun, resumePersistentBatchRun, cancelSequentialAutoGen } from '@/api/client';
import { handleImgError } from '@/utils/brokenImage';
import { useState } from 'react';

const isRunning = (s?: string) => s === 'running' || s === 'pending';

export default function MobileBatchDetail() {
  const { batchRunId } = useParams<{ batchRunId: string }>();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);

  const q = useQuery({
    queryKey: ['batch-run', batchRunId],
    queryFn: async () => (await getPersistentBatchRun(batchRunId!)).data,
    refetchInterval: (query) => (isRunning((query.state.data as any)?.status) ? 3000 : false),
  });
  const run = q.data;
  const pct = run && run.total_scenes ? Math.round((run.completed_scenes / run.total_scenes) * 100) : 0;
  const steps = [...(run?.step_log || [])].slice(-25).reverse();
  const activeJobs = run?.active_jobs || [];

  const resume = async () => { setBusy(true); try { await resumePersistentBatchRun(batchRunId!); q.refetch(); } finally { setBusy(false); } };
  const cancel = async () => { if (!run) return; setBusy(true); try { await cancelSequentialAutoGen(run.project_id); q.refetch(); } finally { setBusy(false); } };

  return (
    <div className="fixed inset-0 z-40 flex flex-col bg-gray-950 text-gray-100">
      <header className="flex items-center gap-2 px-3 h-14 border-b border-gray-800 bg-gray-900 flex-shrink-0">
        <button onClick={() => navigate(-1)} className="p-2 -ml-1 rounded-lg active:bg-gray-800 text-gray-300"><ArrowLeft className="w-6 h-6" /></button>
        <div className="min-w-0 flex-1">
          <div className="font-semibold truncate leading-tight">{run?.project_name || 'Batch run'}</div>
          <div className="text-[11px] text-gray-500 capitalize">{run?.status || '…'}{run?.mode ? ` · ${run.mode}` : ''}</div>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto p-3 space-y-4 pb-[env(safe-area-inset-bottom)]">
        {!run ? (
          <div className="flex items-center justify-center py-20 text-gray-400"><Loader2 className="w-6 h-6 animate-spin mr-2" /> Loading…</div>
        ) : (
          <>
            {/* Progress */}
            <div className="rounded-xl bg-gray-900 border border-gray-800 p-4">
              <div className="flex items-center gap-2 mb-2">
                <span className="text-sm font-semibold">{run.completed_scenes}/{run.total_scenes} scenes</span>
                <span className="ml-auto text-xs text-gray-400">{pct}%</span>
              </div>
              <div className="h-2 rounded-full bg-gray-800 overflow-hidden">
                <div className={`h-full ${run.status === 'failed' ? 'bg-red-500' : 'bg-indigo-500'}`} style={{ width: `${pct}%` }} />
              </div>
              {run.current_scene_name && <div className="text-xs text-gray-400 mt-2 truncate">{run.current_scene_name}{run.current_step ? ` — ${run.current_step}` : ''}</div>}
              {run.error_count > 0 && <div className="text-xs text-red-400 mt-1">{run.error_count} error{run.error_count !== 1 ? 's' : ''}</div>}
              <div className="flex gap-2 mt-3">
                {isRunning(run.status) ? (
                  <button onClick={cancel} disabled={busy} className="flex-1 py-2.5 rounded-lg bg-red-600/90 active:bg-red-600 text-sm font-medium flex items-center justify-center gap-1.5"><StopCircle className="w-4 h-4" /> Stop</button>
                ) : (run.status === 'paused' || run.status === 'failed' || run.status === 'cancelled') ? (
                  <button onClick={resume} disabled={busy} className="flex-1 py-2.5 rounded-lg bg-indigo-600 active:bg-indigo-700 text-sm font-medium flex items-center justify-center gap-1.5"><Play className="w-4 h-4" /> Resume</button>
                ) : null}
              </div>
            </div>

            {/* Live preview */}
            {run.last_asset_url && (
              <div className="rounded-xl overflow-hidden border border-gray-800 bg-black">
                <img src={run.last_asset_url} onError={handleImgError} className="w-full object-contain max-h-64" alt="latest" />
                {run.last_asset_scene_name && <div className="text-xs text-gray-400 px-3 py-1.5">{run.last_asset_scene_name}</div>}
              </div>
            )}

            {/* Active jobs */}
            {activeJobs.length > 0 && (
              <section>
                <h3 className="text-xs font-semibold text-gray-400 uppercase mb-2">Rendering now</h3>
                <div className="space-y-2">
                  {activeJobs.map((j: any) => (
                    <div key={j.job_id} className="rounded-lg bg-gray-900 border border-gray-800 p-3">
                      <div className="flex items-center gap-2 text-sm">
                        <Loader2 className="w-3.5 h-3.5 animate-spin text-indigo-400" />
                        <span className="truncate">{j.scene_name || j.job_type}</span>
                        <span className="ml-auto text-xs text-gray-500">{Math.round(j.progress_percent || 0)}%</span>
                      </div>
                      <div className="h-1.5 rounded-full bg-gray-800 overflow-hidden mt-1.5">
                        <div className="h-full bg-indigo-500 transition-all" style={{ width: `${Math.max(4, j.progress_percent || 0)}%` }} />
                      </div>
                      {(j.current_node || j.two_pass_phase) && <div className="text-[10px] text-gray-500 mt-1 truncate">{j.two_pass_phase ? `${j.two_pass_phase} · ` : ''}{j.current_node}</div>}
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Activity feed */}
            {steps.length > 0 && (
              <section>
                <h3 className="text-xs font-semibold text-gray-400 uppercase mb-2">Activity</h3>
                <div className="space-y-1.5">
                  {steps.map((s: any, i: number) => (
                    <div key={i} className="flex items-start gap-2 text-xs">
                      <span className={`mt-1 w-1.5 h-1.5 rounded-full flex-shrink-0 ${s.type === 'error' ? 'bg-red-400' : 'bg-indigo-400'}`} />
                      <span className="text-gray-300 min-w-0">
                        <span className="text-gray-500">{s.scene_name ? `${s.scene_name}: ` : ''}</span>{s.step}
                      </span>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Errors */}
            {(run.error_log || []).length > 0 && (
              <section>
                <h3 className="text-xs font-semibold text-red-400 uppercase mb-2">Errors</h3>
                <div className="space-y-1.5">
                  {run.error_log.map((e: any, i: number) => (
                    <div key={i} className="text-xs text-red-300 bg-red-950/30 rounded-lg px-2.5 py-1.5">
                      <span className="text-red-400/70">{e.scene_name || e.step || 'error'}: </span>{e.error}
                    </div>
                  ))}
                </div>
              </section>
            )}
          </>
        )}
      </main>
    </div>
  );
}
