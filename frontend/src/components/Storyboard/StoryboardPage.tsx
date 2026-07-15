import { useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft, ZoomIn, ZoomOut, Maximize2, Loader2, Clapperboard,
} from 'lucide-react';
import {
  getProject, getScenes, getAssets, getConcept, getLyrics, getWorkflowConfigs,
} from '@/api/client';
import { useAppStore } from '@/store';
import type { Scene, WorkflowConfig } from '@/types/index';
import type { CharacterInfo } from '@/components/SceneEditor/ReferenceSelector';
import { useZoomPan } from './useZoomPan';
import SceneCard from './SceneCard';
import StoryboardSceneModal from './StoryboardSceneModal';

const fileUrl = (p?: string) => (p ? `/api/files/${p}` : '');

function deriveLyric(scene: Scene, words: any[]): string {
  const stored = scene.parameters?.lyrics;
  if (stored && String(stored).trim()) return String(stored).trim();
  if (!words?.length) return '';
  const parts = words
    .filter((w) => typeof w.start === 'number' && w.start >= scene.start_time - 0.05 && w.start < scene.end_time)
    .map((w) => w.word);
  return parts.join(' ').replace(/\s+/g, ' ').trim();
}

export default function StoryboardPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const projectId = id!;

  const setAssets = useAppStore((s) => s.setAssets);
  const setProject = useAppStore((s) => s.setProject);

  const projectQ = useQuery({ queryKey: ['project', projectId], queryFn: async () => (await getProject(projectId)).data });
  const scenesQ = useQuery({ queryKey: ['scenes', projectId], queryFn: async () => (await getScenes(projectId)).data });
  const assetsQ = useQuery({ queryKey: ['assets', projectId], queryFn: async () => (await getAssets(projectId)).data });
  const conceptQ = useQuery({ queryKey: ['concept', projectId], queryFn: async () => (await getConcept(projectId)).data });
  const lyricsQ = useQuery({ queryKey: ['lyrics', projectId], queryFn: async () => (await getLyrics(projectId)).data });
  const workflowsQ = useQuery({ queryKey: ['workflows'], queryFn: async () => (await getWorkflowConfigs()).data });

  // Hydrate the store so shared components (ReferenceSelector) can read assets.
  useEffect(() => { if (assetsQ.data) setAssets(assetsQ.data); }, [assetsQ.data, setAssets]);
  useEffect(() => { if (projectQ.data) setProject(projectQ.data); }, [projectQ.data, setProject]);

  const scenes: Scene[] = useMemo(
    () => [...(scenesQ.data || [])].sort((a, b) => a.order_index - b.order_index),
    [scenesQ.data],
  );
  const characters: CharacterInfo[] = (conceptQ.data as any)?.characters || [];
  const defaultWidth = (conceptQ.data as any)?.resolution_width || 1536;
  const defaultHeight = (conceptQ.data as any)?.resolution_height || 864;
  const imageWorkflows: WorkflowConfig[] = (workflowsQ.data || []).filter((w) => w.workflow_type === 'image');
  const words: any[] = (lyricsQ.data as any)?.words || [];

  // ── Live job status ───────────────────────────────────────────────
  // useJobEvents() (mounted globally in App.tsx) already streams job events
  // into store.jobs, so we derive running scenes from there instead of opening
  // our own SSE. When a job for this project finishes we refresh scenes +
  // versions so thumbnails update live.
  const jobs = useAppStore((s) => s.jobs);
  const runningScenes = useMemo(() => {
    const set = new Set<string>();
    for (const j of jobs) if ((j.status === 'pending' || j.status === 'running') && j.scene_id) set.add(j.scene_id);
    return set;
  }, [jobs]);
  const doneCount = useMemo(
    () => jobs.filter((j) => j.project_id === projectId && (j.status === 'done' || j.status === 'failed')).length,
    [jobs, projectId],
  );
  useEffect(() => {
    if (doneCount > 0) {
      qc.invalidateQueries({ queryKey: ['scenes', projectId] });
      qc.invalidateQueries({ queryKey: ['scene-versions'] });
    }
  }, [doneCount, qc, projectId]);

  // ── Zoom / pan ────────────────────────────────────────────────────
  const zp = useZoomPan();

  // ── Audio playback (one at a time) ────────────────────────────────
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [playingId, setPlayingId] = useState<string | null>(null);
  const toggleAudio = (sceneId: string, url: string | null) => {
    if (!url) return;
    if (playingId === sceneId) {
      audioRef.current?.pause();
      setPlayingId(null);
      return;
    }
    if (!audioRef.current) audioRef.current = new Audio();
    audioRef.current.src = url;
    audioRef.current.onended = () => setPlayingId(null);
    audioRef.current.play().then(() => setPlayingId(sceneId)).catch(() => setPlayingId(null));
  };
  useEffect(() => () => { audioRef.current?.pause(); }, []);

  // ── Modal ─────────────────────────────────────────────────────────
  const [open, setOpen] = useState<{ sceneId: string; frame: 'first' | 'last' } | null>(null);
  const openScene = open ? scenes.find((s) => s.id === open.sceneId) : undefined;

  const frameCounts = (scene: Scene) => {
    const hist = scene.generation_history || [];
    const imgs = hist.filter((v) => v.job_type !== 'video' && v.output_path);
    const ff = imgs.filter((v) => !v.parameters?.frame_type || v.parameters?.frame_type === 'first').length;
    const lf = imgs.filter((v) => v.parameters?.frame_type === 'last').length;
    return { ff, lf };
  };

  const loading = scenesQ.isLoading || projectQ.isLoading;

  return (
    <div className="fixed inset-0 z-40 bg-gray-950 flex flex-col">
      {/* Top bar */}
      <div className="flex items-center gap-3 px-4 py-2.5 border-b border-gray-800 bg-gray-900 flex-shrink-0">
        <button
          onClick={() => navigate(`/project/${projectId}`)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-gray-800 text-gray-200 text-sm hover:bg-gray-700"
        >
          <ArrowLeft className="w-4 h-4" /> Back
        </button>
        <div className="flex items-center gap-2 text-gray-100">
          <Clapperboard className="w-5 h-5 text-indigo-400" />
          <span className="font-semibold">{projectQ.data?.name || 'Project'}</span>
          <span className="text-gray-500">· Storyboard</span>
        </div>
        <span className="text-xs text-gray-500 hidden md:inline">
          {scenes.length} scenes · scroll to zoom, drag to pan
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          <button onClick={() => zp.zoomBy(1 / 1.2)} className="p-1.5 rounded bg-gray-800 text-gray-300 hover:bg-gray-700" title="Zoom out"><ZoomOut className="w-4 h-4" /></button>
          <span className="text-xs text-gray-400 w-10 text-center">{Math.round(zp.state.k * 100)}%</span>
          <button onClick={() => zp.zoomBy(1.2)} className="p-1.5 rounded bg-gray-800 text-gray-300 hover:bg-gray-700" title="Zoom in"><ZoomIn className="w-4 h-4" /></button>
          <button onClick={zp.reset} className="p-1.5 rounded bg-gray-800 text-gray-300 hover:bg-gray-700" title="Reset view"><Maximize2 className="w-4 h-4" /></button>
        </div>
      </div>

      {/* Canvas */}
      <div
        ref={zp.viewportRef}
        className="relative flex-1 overflow-hidden cursor-grab active:cursor-grabbing bg-[radial-gradient(circle,rgba(255,255,255,0.05)_1px,transparent_1px)] [background-size:24px_24px]"
        {...zp.bind}
      >
        {loading ? (
          <div className="absolute inset-0 flex items-center justify-center text-gray-400">
            <Loader2 className="w-6 h-6 animate-spin mr-2" /> Loading storyboard…
          </div>
        ) : scenes.length === 0 ? (
          <div className="absolute inset-0 flex items-center justify-center text-gray-500">
            No scenes yet. Build your timeline first.
          </div>
        ) : (
          <div
            className="absolute top-0 left-0 flex items-start gap-6 p-2 select-none"
            style={{ transform: zp.transform, transformOrigin: '0 0' }}
          >
            {scenes.map((scene, i) => {
              const { ff, lf } = frameCounts(scene);
              const audioUrl = scene.parameters?.audio_clip_path ? fileUrl(scene.parameters.audio_clip_path) : null;
              return (
                <div key={scene.id} className="flex items-center gap-6">
                  <SceneCard
                    scene={scene}
                    index={i}
                    ffUrl={fileUrl(scene.parameters?.chosen_image_path) || null}
                    lfUrl={fileUrl(scene.parameters?.chosen_last_frame_path) || null}
                    ffCount={ff}
                    lfCount={lf}
                    lyric={deriveLyric(scene, words)}
                    audioUrl={audioUrl}
                    playing={playingId === scene.id}
                    busy={runningScenes.has(scene.id)}
                    onOpen={(frame) => setOpen({ sceneId: scene.id, frame })}
                    onToggleAudio={() => toggleAudio(scene.id, audioUrl)}
                  />
                  {i < scenes.length - 1 && (
                    <div className="text-gray-700 text-2xl flex-shrink-0" aria-hidden>→</div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {open && openScene && (
        <StoryboardSceneModal
          projectId={projectId}
          scene={openScene}
          initialFrame={open.frame}
          characters={characters}
          defaultWidth={defaultWidth}
          defaultHeight={defaultHeight}
          imageWorkflows={imageWorkflows}
          onClose={() => setOpen(null)}
          onChanged={() => qc.invalidateQueries({ queryKey: ['scenes', projectId] })}
        />
      )}
    </div>
  );
}
