import { useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { ImageOff, Play, Pause, Loader2, Layers } from 'lucide-react';
import { handleImgError } from '@/utils/brokenImage';
import { useAppStore } from '@/store';
import type { Scene } from '@/types/index';
import MobileShell from './MobileShell';
import StoryboardSceneModal from '@/components/Storyboard/StoryboardSceneModal';
import { useProjectData, fileUrl, deriveLyric } from './useProjectData';

function Thumb({ url, label, count, onClick }: { url: string; label: string; count: number; onClick: () => void }) {
  return (
    <button onClick={onClick} className="relative flex-1 aspect-video rounded-lg overflow-hidden border border-gray-700 bg-gray-950 active:border-indigo-400">
      {url ? (
        <img src={url} onError={handleImgError} className="w-full h-full object-cover" alt={label} />
      ) : (
        <div className="w-full h-full flex flex-col items-center justify-center text-gray-600">
          <ImageOff className="w-5 h-5 mb-1" /><span className="text-[10px] uppercase">{label}</span>
        </div>
      )}
      <span className="absolute top-1 left-1 px-1.5 py-0.5 rounded bg-black/60 text-[9px] uppercase text-gray-200">{label}</span>
      {count > 1 && <span className="absolute top-1 right-1 px-1 py-0.5 rounded bg-black/60 text-[9px] text-gray-200 flex items-center gap-0.5"><Layers className="w-2.5 h-2.5" />{count}</span>}
    </button>
  );
}

export default function MobileScenes() {
  const { id } = useParams<{ id: string }>();
  const projectId = id!;
  const { project, scenes, characters, imageWorkflows, words, defaultWidth, defaultHeight } = useProjectData(projectId);
  const jobs = useAppStore((s) => s.jobs);

  const runningScenes = useMemo(() => {
    const set = new Set<string>();
    for (const j of jobs) if ((j.status === 'pending' || j.status === 'running') && j.scene_id) set.add(j.scene_id);
    return set;
  }, [jobs]);

  // Refresh scene thumbnails when a job for this project completes (useJobEvents
  // updates store.jobs but doesn't invalidate the ['scenes'] query).
  const qc = useQueryClient();
  const doneCount = useMemo(
    () => jobs.filter((j) => j.project_id === projectId && (j.status === 'done' || j.status === 'failed')).length,
    [jobs, projectId],
  );
  useEffect(() => { if (doneCount > 0) qc.invalidateQueries({ queryKey: ['scenes', projectId] }); }, [doneCount, qc, projectId]);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [playingId, setPlayingId] = useState<string | null>(null);
  useEffect(() => () => { audioRef.current?.pause(); }, []);
  const toggleAudio = (sceneId: string, url: string | null) => {
    if (!url) return;
    if (playingId === sceneId) { audioRef.current?.pause(); setPlayingId(null); return; }
    if (!audioRef.current) audioRef.current = new Audio();
    audioRef.current.src = url;
    audioRef.current.onended = () => setPlayingId(null);
    audioRef.current.play().then(() => setPlayingId(sceneId)).catch(() => setPlayingId(null));
  };

  const [open, setOpen] = useState<{ sceneId: string; frame: 'first' | 'last' } | null>(null);
  const openScene = open ? scenes.find((s) => s.id === open.sceneId) : undefined;

  const counts = (scene: Scene) => {
    const h = (scene.generation_history || []).filter((v) => v.job_type !== 'video' && v.output_path);
    return {
      ff: h.filter((v) => !v.parameters?.frame_type || v.parameters?.frame_type === 'first').length,
      lf: h.filter((v) => v.parameters?.frame_type === 'last').length,
    };
  };

  return (
    <MobileShell projectId={projectId} title={project?.name || 'Project'} subtitle={`${scenes.length} scenes`} active="scenes">
      <div className="p-3 space-y-3">
        {scenes.length === 0 && <div className="text-center text-gray-500 py-16">No scenes yet.</div>}
        {scenes.map((scene, i) => {
          const { ff, lf } = counts(scene);
          const audioUrl = scene.parameters?.audio_clip_path ? fileUrl(scene.parameters.audio_clip_path) : null;
          const lyric = deriveLyric(scene, words);
          return (
            <div key={scene.id} className="rounded-xl bg-gray-900 border border-gray-800 p-3">
              <div className="flex items-center gap-2 mb-2">
                <span className="w-6 h-6 rounded-full bg-indigo-600 text-white text-xs font-bold flex items-center justify-center flex-shrink-0">{i + 1}</span>
                <span className="font-semibold text-sm truncate">{scene.name || `Scene ${i + 1}`}</span>
                {runningScenes.has(scene.id) && <Loader2 className="w-4 h-4 animate-spin text-amber-400 ml-auto flex-shrink-0" />}
              </div>
              <div className="flex gap-2">
                <Thumb url={fileUrl(scene.parameters?.chosen_image_path)} label="First" count={ff} onClick={() => setOpen({ sceneId: scene.id, frame: 'first' })} />
                <Thumb url={fileUrl(scene.parameters?.chosen_last_frame_path)} label="Last" count={lf} onClick={() => setOpen({ sceneId: scene.id, frame: 'last' })} />
              </div>
              {lyric && <div className="mt-2 text-xs text-gray-400 line-clamp-2">{lyric}</div>}
              <button
                onClick={() => toggleAudio(scene.id, audioUrl)}
                disabled={!audioUrl}
                className={`mt-2 flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium ${audioUrl ? (playingId === scene.id ? 'bg-indigo-600 text-white' : 'bg-gray-800 text-gray-200 active:bg-gray-700') : 'bg-gray-800/50 text-gray-600'}`}
              >
                {playingId === scene.id ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
                {playingId === scene.id ? 'Playing' : 'Audio'}
              </button>
            </div>
          );
        })}
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
          onChanged={() => {}}
        />
      )}
    </MobileShell>
  );
}
