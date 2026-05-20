import { useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useAppStore } from '@/store';
import { getScene, getScenes } from '@/api/client';

export const useJobEvents = () => {
  const retryCountRef = useRef(0);
  const eventSourceRef = useRef<EventSource | null>(null);
  const queryClient = useQueryClient();

  useEffect(() => {
    let cancelled = false;
    let retryTimeout: ReturnType<typeof setTimeout>;
    let hasConnectedBefore = false;

    const connect = () => {
      if (cancelled) return;

      const eventSource = new EventSource('/api/jobs/stream');
      eventSourceRef.current = eventSource;

      eventSource.onopen = () => {
        const wasReconnect = hasConnectedBefore;
        hasConnectedBefore = true;
        retryCountRef.current = 0;

        // On reconnect, refresh all scene data to catch any missed completions
        if (wasReconnect) {
          const store = useAppStore.getState();
          const projectId = store.currentProject?.id;
          if (projectId) {
            getScenes(projectId).then((res) => {
              const s = useAppStore.getState();
              s.setScenes(res.data);
            }).catch(() => {/* ignore */});
          }
        }
      };

      eventSource.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.event === 'stream_ready') return;
          // Accept events that have either job.id or job_id
          const jobId = data.job?.id || data.job_id;
          if (jobId) {
            handleEvent(data, jobId);
          }
        } catch {
          // ignore parse errors
        }
      };

      eventSource.onerror = () => {
        eventSource.close();
        eventSourceRef.current = null;
        if (cancelled) return;

        // Exponential backoff: 1s, 2s, 4s, 8s, max 30s
        const delay = Math.min(1000 * Math.pow(2, retryCountRef.current), 30000);
        retryCountRef.current++;
        retryTimeout = setTimeout(connect, delay);
      };
    };

    const handleEvent = (data: any, jobId: string) => {
      // Use getState() to avoid stale closures — never depend on component state
      const store = useAppStore.getState();

      // Resolve job_type from SSE event (top-level or nested in job object)
      const resolvedJobType = data.job_type || data.job?.job_type || 'image';

      // Ensure job exists in store (it may arrive via SSE before onSuccess runs)
      const existingJob = store.jobs.find(j => j.id === jobId);
      if (!existingJob) {
        store.addJob({
          id: jobId,
          project_id: data.project_id || '',
          scene_id: data.scene_id,
          job_type: resolvedJobType,
          status: 'pending',
          priority: 0,
          parameters: {},
          created_at: new Date().toISOString(),
          retry_count: 0,
        });
      } else if (resolvedJobType !== 'image' && existingJob.job_type !== resolvedJobType) {
        // Update job_type if SSE provides a more specific type than the default
        store.updateJob(jobId, { job_type: resolvedJobType });
      }

      switch (data.event || data.type) {
        case 'job_started':
          store.updateJob(jobId, {
            status: 'running',
            started_at: data.job?.started_at || new Date().toISOString(),
          });
          break;
        case 'job_worker_assigned':
          store.updateJob(jobId, {
            worker_url: data.worker_url,
            scene_id: data.scene_id,
          });
          break;
        case 'job_progress':
          store.updateJob(jobId, {
            status: 'running',
            progress: data.progress,
            current_node: data.node,
          });
          break;
        case 'job_completed':
          store.updateJob(jobId, {
            status: 'done',
            progress: 100,
            result: data.job?.result,
            completed_at: data.job?.completed_at || new Date().toISOString(),
          });
          // Refetch scene to pick up auto-set chosen_image_path from backend
          {
            const sceneId = data.scene_id || data.job?.scene_id;
            const projectId = data.project_id || data.job?.project_id;
            if (sceneId && projectId) {
              getScene(projectId, sceneId).then((res) => {
                const s = useAppStore.getState();
                const scene = res.data;
                // Update ALL scene fields including start_time/end_time
                // (V2V trim-A adjusts scene boundaries)
                s.updateSceneInStore(sceneId, {
                  parameters: scene.parameters,
                  start_time: scene.start_time,
                  end_time: scene.end_time,
                });
              }).catch(() => {/* ignore fetch errors */});

              // V2V trim-A also modifies scene A (previous scene) —
              // refetch it so the frontend has updated boundaries + video
              const v2vSceneAId = data.v2v_scene_a_id;
              if (v2vSceneAId) {
                getScene(projectId, v2vSceneAId).then((res) => {
                  const s = useAppStore.getState();
                  const sceneA = res.data;
                  s.updateSceneInStore(v2vSceneAId, {
                    parameters: sceneA.parameters,
                    start_time: sceneA.start_time,
                    end_time: sceneA.end_time,
                  });
                }).catch(() => {/* ignore fetch errors */});
              }
            }
            // Character gen jobs update project.settings — refresh concept data
            if (data.character_gen && projectId) {
              queryClient.invalidateQueries({ queryKey: ['concept', projectId] });
            }
          }
          break;
        case 'job_failed':
          store.updateJob(jobId, {
            status: 'failed',
            error: data.error,
            completed_at: data.job?.completed_at || new Date().toISOString(),
          });
          break;
        default:
          if (data.job && typeof data.job === 'object') {
            store.updateJob(jobId, data.job);
          }
      }
    };

    connect();

    return () => {
      cancelled = true;
      clearTimeout(retryTimeout);
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
    };
  // Empty dependency array — connect once, use getState() for all store access
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
};
