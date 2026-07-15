import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Film, Users, Activity, Clapperboard, Wand2, Loader2, ChevronRight, StopCircle,
} from 'lucide-react';
import { getSequentialAutoGenStatus, startSequentialAutoGen, cancelSequentialAutoGen } from '@/api/client';
import MobileShell from './MobileShell';
import MobileSheet from './MobileSheet';
import { useProjectData } from './useProjectData';

const AUTO_GEN_MODES: { value: string; label: string; hint: string }[] = [
  { value: 'missing_images_independent', label: 'Missing Images', hint: 'Only scenes without an image (independent)' },
  { value: 'all_images', label: 'All Images', hint: 'Every scene, previous scene as reference' },
  { value: 'all_video_single', label: 'Full — Single Image', hint: 'Image + video, no last frame' },
  { value: 'all_video_fflf', label: 'Full — FF/LF Chaining', hint: 'First+last frame chained video' },
  { value: 'all_video_fflf_keyframes', label: 'Full — FF/LF Keyframes', hint: 'Independent first+last keyframes' },
  { value: 'all_video_v2v', label: 'Full — V2V Extend', hint: 'Video-to-video extension' },
  { value: 'missing_videos_single', label: 'Missing Videos', hint: 'Only scenes without a video' },
];

const isRunning = (s?: string) => s === 'running' || s === 'pending';

export default function MobileProject() {
  const { id } = useParams<{ id: string }>();
  const projectId = id!;
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { project, scenes, characters, assets } = useProjectData(projectId);
  const [sheet, setSheet] = useState(false);
  const [busy, setBusy] = useState(false);

  const statusQ = useQuery({
    queryKey: ['autogen-status', projectId],
    queryFn: async () => (await getSequentialAutoGenStatus(projectId)).data,
    refetchInterval: (q) => (isRunning((q.state.data as any)?.status) ? 3000 : false),
  });
  const st = statusQ.data;
  const running = isRunning(st?.status);
  const pct = st && st.total_scenes ? Math.round((st.completed_scenes / st.total_scenes) * 100) : 0;

  const start = async (mode: string) => {
    setBusy(true);
    try {
      await startSequentialAutoGen(projectId, mode);
      setSheet(false);
      qc.invalidateQueries({ queryKey: ['autogen-status', projectId] });
    } finally { setBusy(false); }
  };
  const cancel = async () => {
    setBusy(true);
    try { await cancelSequentialAutoGen(projectId); qc.invalidateQueries({ queryKey: ['autogen-status', projectId] }); }
    finally { setBusy(false); }
  };

  const tiles = [
    { label: 'Scenes', icon: Film, count: scenes.length, to: `/mobile/p/${projectId}/scenes` },
    { label: 'Cast', icon: Users, count: characters.length, to: `/mobile/p/${projectId}/characters` },
    { label: 'Queue', icon: Activity, count: undefined, to: `/mobile/p/${projectId}/queue` },
    { label: 'Storyboard', icon: Clapperboard, count: undefined, to: `/project/${projectId}/storyboard` },
  ];

  return (
    <MobileShell projectId={projectId} title={project?.name || 'Project'} subtitle={`${scenes.length} scenes · ${assets.length} assets`} active="overview">
      <div className="p-3 space-y-3">
        {/* Auto-gen status / action */}
        <div className="rounded-xl bg-gray-900 border border-gray-800 p-4">
          {running ? (
            <>
              <div className="flex items-center gap-2 mb-2">
                <Loader2 className="w-4 h-4 animate-spin text-indigo-400" />
                <span className="font-semibold text-sm">Auto-Generating…</span>
                <span className="ml-auto text-xs text-gray-400">{st?.completed_scenes}/{st?.total_scenes}</span>
              </div>
              <div className="h-2 rounded-full bg-gray-800 overflow-hidden">
                <div className="h-full bg-indigo-500 transition-all" style={{ width: `${pct}%` }} />
              </div>
              <div className="text-xs text-gray-400 mt-2 truncate">
                {st?.current_scene_name ? `${st.current_scene_name}` : ''}{st?.current_step ? ` — ${st.current_step}` : ''}
              </div>
              <div className="flex gap-2 mt-3">
                {st?.batch_run_id && (
                  <button onClick={() => navigate(`/mobile/batch/${st.batch_run_id}`)} className="flex-1 py-2.5 rounded-lg bg-gray-800 active:bg-gray-700 text-sm font-medium">
                    View details
                  </button>
                )}
                <button onClick={cancel} disabled={busy} className="flex-1 py-2.5 rounded-lg bg-red-600/90 active:bg-red-600 text-sm font-medium flex items-center justify-center gap-1.5">
                  <StopCircle className="w-4 h-4" /> Stop
                </button>
              </div>
            </>
          ) : (
            <button onClick={() => setSheet(true)} className="w-full py-3.5 rounded-lg bg-indigo-600 active:bg-indigo-700 font-semibold flex items-center justify-center gap-2">
              <Wand2 className="w-5 h-5" /> Auto-Generate
            </button>
          )}
          {st?.status === 'failed' && st?.error && (
            <div className="text-xs text-red-400 mt-2">{st.error}</div>
          )}
        </div>

        {/* Nav tiles */}
        <div className="grid grid-cols-2 gap-3">
          {tiles.map((t) => {
            const Icon = t.icon;
            return (
              <button key={t.label} onClick={() => navigate(t.to)} className="rounded-xl bg-gray-900 border border-gray-800 active:bg-gray-800 p-4 text-left">
                <Icon className="w-6 h-6 text-indigo-400 mb-2" />
                <div className="flex items-center">
                  <span className="font-semibold">{t.label}</span>
                  {typeof t.count === 'number' && <span className="ml-auto text-sm text-gray-500">{t.count}</span>}
                  {typeof t.count !== 'number' && <ChevronRight className="ml-auto w-4 h-4 text-gray-600" />}
                </div>
              </button>
            );
          })}
        </div>
      </div>

      <MobileSheet open={sheet} title="Auto-Generate mode" onClose={() => setSheet(false)}>
        <div className="space-y-2">
          {AUTO_GEN_MODES.map((m) => (
            <button key={m.value} onClick={() => start(m.value)} disabled={busy}
              className="w-full text-left p-3 rounded-lg bg-gray-800 active:bg-gray-700 disabled:opacity-50">
              <div className="font-medium text-sm">{m.label}</div>
              <div className="text-xs text-gray-400">{m.hint}</div>
            </button>
          ))}
        </div>
      </MobileSheet>
    </MobileShell>
  );
}
