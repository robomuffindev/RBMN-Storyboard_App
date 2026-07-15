import { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  X, Trash2, Check, Loader2, Wand2, Sparkles, ChevronLeft, ChevronRight,
} from 'lucide-react';
import {
  getSceneVersions, deleteSceneVersion, updateScene, generateImage, enhancePrompt,
} from '@/api/client';
import ReferenceSelector, {
  autoWorkflowType, collectRefAssetIds,
} from '@/components/SceneEditor/ReferenceSelector';
import type { CharacterInfo, ReferenceState } from '@/components/SceneEditor/ReferenceSelector';
import { useAppStore } from '@/store';
import { handleImgError } from '@/utils/brokenImage';
import type { Scene, GenerationHistory, WorkflowConfig } from '@/types/index';

const EMPTY_REFS: ReferenceState = { characterIndices: [], extras: [] };
const fileUrl = (p?: string) => (p ? `/api/files/${p}` : '');

interface Props {
  projectId: string;
  scene: Scene;
  initialFrame: 'first' | 'last';
  characters: CharacterInfo[];
  defaultWidth: number;
  defaultHeight: number;
  imageWorkflows: WorkflowConfig[];
  onClose: () => void;
  onChanged: () => void;
}

export default function StoryboardSceneModal({
  projectId, scene, initialFrame, characters, defaultWidth, defaultHeight,
  imageWorkflows, onClose, onChanged,
}: Props) {
  const qc = useQueryClient();
  const assets = useAppStore((s) => s.assets);
  const [frame, setFrame] = useState<'first' | 'last'>(initialFrame);
  const params = scene.parameters || {};

  // Prompts (seeded from scene, editable)
  const [firstPrompt, setFirstPrompt] = useState<string>(scene.prompt || '');
  const [lastPrompt, setLastPrompt] = useState<string>(params.last_frame_prompt || scene.prompt || '');
  // References per frame
  const [firstRefs, setFirstRefs] = useState<ReferenceState>(params.image_refs_first || EMPTY_REFS);
  const [lastRefs, setLastRefs] = useState<ReferenceState>(params.image_refs_last || EMPTY_REFS);
  const [twoPass, setTwoPass] = useState<boolean>(!!params.two_pass_enabled);
  const [seed, setSeed] = useState<string>('');
  const [customWf, setCustomWf] = useState<string>('');
  const [selIdx, setSelIdx] = useState<number>(0);
  const [enhancing, setEnhancing] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);

  // Re-seed local state when the scene changes underneath us (after refetch).
  useEffect(() => {
    setFirstPrompt(scene.prompt || '');
    setLastPrompt(params.last_frame_prompt || scene.prompt || '');
    setFirstRefs(params.image_refs_first || EMPTY_REFS);
    setLastRefs(params.image_refs_last || EMPTY_REFS);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scene.id]);

  const versionsQ = useQuery({
    queryKey: ['scene-versions', projectId, scene.id],
    queryFn: async () => (await getSceneVersions(projectId, scene.id)).data,
    refetchInterval: generating ? 2500 : false,
  });
  const allVersions: GenerationHistory[] = versionsQ.data || [];

  const versions = useMemo(() => {
    const imgs = allVersions.filter((v) => v.job_type !== 'video' && v.output_path);
    if (frame === 'last') return imgs.filter((v) => v.parameters?.frame_type === 'last');
    return imgs.filter((v) => !v.parameters?.frame_type || v.parameters?.frame_type === 'first');
  }, [allVersions, frame]);

  const activePath: string | undefined =
    frame === 'first' ? params.chosen_image_path : params.chosen_last_frame_path;
  const activeIdx = versions.findIndex((v) => v.output_path === activePath);

  // Clear the generating flag once a new version lands.
  const versionCount = versions.length;
  const [genBaseline, setGenBaseline] = useState<number | null>(null);
  useEffect(() => {
    if (generating && genBaseline !== null && versionCount > genBaseline) {
      setGenerating(false);
      setGenBaseline(null);
      onChanged();
    }
  }, [versionCount, generating, genBaseline, onChanged]);

  // Safety net: if no new version lands (failed render) clear the in-flight
  // state so the Generate button doesn't stay disabled forever.
  useEffect(() => {
    if (!generating) return;
    const t = setTimeout(() => { setGenerating(false); setGenBaseline(null); }, 120000);
    return () => clearTimeout(t);
  }, [generating]);

  // When frame or versions change, default the preview to the active version.
  useEffect(() => {
    setSelIdx(activeIdx >= 0 ? activeIdx : 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frame, versionCount]);

  const refs = frame === 'first' ? firstRefs : lastRefs;
  const setRefs = frame === 'first' ? setFirstRefs : setLastRefs;
  const activePrompt = frame === 'first' ? firstPrompt : lastPrompt;
  const setActivePrompt = frame === 'first' ? setFirstPrompt : setLastPrompt;
  const autoWf = autoWorkflowType(refs, characters);

  const selVersion = versions[selIdx];
  const previewUrl = selVersion ? fileUrl(selVersion.output_path) : fileUrl(activePath);

  const setActive = useMutation({
    mutationFn: async (v: GenerationHistory) => {
      const key = frame === 'first' ? 'chosen_image_path' : 'chosen_last_frame_path';
      await updateScene(projectId, scene.id, {
        parameters: { ...(scene.parameters || {}), [key]: v.output_path },
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['scenes', projectId] });
      onChanged();
    },
  });

  const delVersion = useMutation({
    mutationFn: async (v: GenerationHistory) => { await deleteSceneVersion(projectId, scene.id, v.id); },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['scene-versions', projectId, scene.id] }),
  });

  const doEnhance = async () => {
    setEnhancing(true);
    try {
      const refIds = collectRefAssetIds(refs, characters, assets as any);
      const res = await enhancePrompt(projectId, {
        prompt: activePrompt,
        is_video: false,
        frame_type: frame,
        reference_asset_ids: refIds,
        scene_id: scene.id,
      });
      if (res.data?.enhanced_prompt) setActivePrompt(res.data.enhanced_prompt);
    } catch (e: any) {
      setGenError(e?.message || 'Enhance failed');
    } finally {
      setEnhancing(false);
    }
  };

  const doGenerate = async () => {
    setGenError(null);
    setGenerating(true);
    setGenBaseline(versionCount);
    try {
      const refIds = collectRefAssetIds(refs, characters, assets as any);
      const useCustom = !!customWf;
      const effectiveWf = useCustom ? customWf : autoWf;

      // Persist prompt + refs to the scene (parity with the Scene Editor).
      const paramUpdates: Record<string, any> = {
        ...(scene.parameters || {}),
        image_refs_first: frame === 'first' ? refs : firstRefs,
        image_refs_last: frame === 'last' ? refs : lastRefs,
        two_pass_enabled: twoPass,
        workflow_type: effectiveWf,
      };
      if (frame === 'last') paramUpdates.last_frame_prompt = activePrompt;
      const sceneUpdate: any = { parameters: paramUpdates };
      if (frame === 'first') sceneUpdate.prompt = activePrompt;
      await updateScene(projectId, scene.id, sceneUpdate);
      qc.invalidateQueries({ queryKey: ['scenes', projectId] });

      const w = Number(params.width) || defaultWidth;
      const h = Number(params.height) || defaultHeight;
      await generateImage(projectId, {
        scene_id: scene.id,
        ...(useCustom ? { workflow_config_id: customWf } : { workflow_type: effectiveWf || 'klein_t2i' }),
        prompt: activePrompt,
        width: w,
        height: h,
        seed: seed ? parseInt(seed) : undefined,
        reference_asset_ids: refIds,
        frame_type: frame,
        two_pass: twoPass,
      });
      // versions refetch (polling) will clear the generating flag on new version.
    } catch (e: any) {
      setGenError(e?.response?.data?.detail || e?.message || 'Generation failed');
      setGenerating(false);
      setGenBaseline(null);
    }
  };

  const FrameTab = ({ f, label }: { f: 'first' | 'last'; label: string }) => (
    <button
      onClick={() => setFrame(f)}
      disabled={generating}
      title={generating ? 'Finish the current render first' : ''}
      className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
        frame === f ? 'bg-indigo-600 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
      }`}
    >
      {label}
    </button>
  );

  return createPortal(
    <div className="fixed inset-0 z-[9996] bg-black/80 flex items-center justify-center p-4" onClick={onClose}>
      <div
        className="bg-gray-900 rounded-xl border border-gray-700 w-full max-w-6xl max-h-[92vh] flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-700">
          <h3 className="text-base font-semibold text-gray-100 truncate">{scene.name || 'Scene'}</h3>
          <div className="flex gap-1.5 ml-2">
            <FrameTab f="first" label="First Frame" />
            <FrameTab f="last" label="Last Frame" />
          </div>
          <button onClick={onClose} className="ml-auto p-1.5 rounded hover:bg-gray-700 text-gray-400">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="flex-1 min-h-0 grid grid-cols-1 md:grid-cols-[1.4fr_1fr]">
          {/* Left: preview + versions */}
          <div className="flex flex-col min-h-0 border-r border-gray-800">
            <div className="relative flex-1 min-h-[280px] bg-black flex items-center justify-center">
              {previewUrl ? (
                <img src={previewUrl} onError={handleImgError} className="max-h-full max-w-full object-contain" alt="preview" />
              ) : (
                <div className="text-gray-600 text-sm">No {frame} frame rendered yet.</div>
              )}
              {versions.length > 1 && (
                <>
                  <button
                    onClick={() => setSelIdx((i) => (i - 1 + versions.length) % versions.length)}
                    className="absolute left-2 top-1/2 -translate-y-1/2 p-1.5 rounded-full bg-black/60 text-white hover:bg-black/80"
                  ><ChevronLeft className="w-5 h-5" /></button>
                  <button
                    onClick={() => setSelIdx((i) => (i + 1) % versions.length)}
                    className="absolute right-2 top-1/2 -translate-y-1/2 p-1.5 rounded-full bg-black/60 text-white hover:bg-black/80"
                  ><ChevronRight className="w-5 h-5" /></button>
                </>
              )}
            </div>

            {/* Version strip */}
            <div className="p-2 border-t border-gray-800">
              <div className="flex items-center justify-between mb-1.5 px-1">
                <span className="text-xs text-gray-400">
                  {versions.length} version{versions.length !== 1 ? 's' : ''}
                </span>
                {selVersion && selVersion.output_path !== activePath && (
                  <button
                    onClick={() => setActive.mutate(selVersion)}
                    disabled={setActive.isPending}
                    className="flex items-center gap-1 px-2 py-1 rounded bg-emerald-600 text-white text-xs font-medium hover:bg-emerald-500"
                  ><Check className="w-3 h-3" /> Set Active</button>
                )}
              </div>
              <div className="flex gap-2 overflow-x-auto pb-1">
                {versions.map((v, i) => {
                  const isActive = v.output_path === activePath;
                  return (
                    <div key={v.id} className="relative flex-shrink-0 group">
                      <button
                        onClick={() => setSelIdx(i)}
                        className={`relative w-24 aspect-video rounded overflow-hidden border-2 ${
                          i === selIdx ? 'border-indigo-400' : 'border-transparent'
                        }`}
                      >
                        <img src={fileUrl(v.output_path)} onError={handleImgError} className="w-full h-full object-cover" alt="" />
                        {isActive && (
                          <span className="absolute bottom-0 inset-x-0 bg-emerald-600/90 text-white text-[9px] text-center py-0.5 flex items-center justify-center gap-0.5">
                            <Check className="w-2.5 h-2.5" /> Active
                          </span>
                        )}
                      </button>
                      <button
                        onClick={() => delVersion.mutate(v)}
                        className="absolute -top-1.5 -right-1.5 p-0.5 rounded-full bg-red-600 text-white opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity"
                        title="Delete version"
                      ><Trash2 className="w-3 h-3" /></button>
                    </div>
                  );
                })}
                {versions.length === 0 && (
                  <span className="text-xs text-gray-600 italic px-1 py-4">No renders yet — generate one on the right.</span>
                )}
              </div>
            </div>
          </div>

          {/* Right: regen form */}
          <div className="flex flex-col min-h-0 overflow-y-auto p-3 gap-3">
            <div>
              <label className="text-xs font-medium text-gray-400 mb-1 flex items-center justify-between">
                <span>{frame === 'first' ? 'First-frame' : 'Last-frame'} prompt</span>
                <button
                  onClick={doEnhance}
                  disabled={enhancing}
                  className="flex items-center gap-1 px-2 py-0.5 rounded bg-gray-700 text-gray-200 text-[11px] hover:bg-gray-600"
                >
                  {enhancing ? <Loader2 className="w-3 h-3 animate-spin" /> : <Wand2 className="w-3 h-3" />} Enhance
                </button>
              </label>
              <textarea
                value={activePrompt}
                onChange={(e) => setActivePrompt(e.target.value)}
                rows={4}
                className="w-full rounded-md bg-gray-800 border border-gray-700 px-2 py-1.5 text-sm text-gray-100 resize-none"
                placeholder="Describe the scene image…"
              />
            </div>

            <div>
              <label className="text-xs font-medium text-gray-400 mb-1 block">References</label>
              <ReferenceSelector
                characters={characters}
                value={refs}
                onChange={setRefs}
                frameType={frame}
                projectId={projectId}
              />
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-xs font-medium text-gray-400 mb-1 block">Workflow / model</label>
                <select
                  value={customWf}
                  onChange={(e) => setCustomWf(e.target.value)}
                  className="w-full rounded-md bg-gray-800 border border-gray-700 px-2 py-1.5 text-sm text-gray-100"
                >
                  <option value="">Auto ({autoWf})</option>
                  {imageWorkflows.map((w) => (
                    <option key={w.id} value={w.id}>{w.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-xs font-medium text-gray-400 mb-1 block">Seed (optional)</label>
                <input
                  value={seed}
                  onChange={(e) => setSeed(e.target.value.replace(/[^0-9]/g, ''))}
                  placeholder="random"
                  className="w-full rounded-md bg-gray-800 border border-gray-700 px-2 py-1.5 text-sm text-gray-100"
                />
              </div>
            </div>

            <label className="flex items-center gap-2 text-sm text-gray-300">
              <input type="checkbox" checked={twoPass} onChange={(e) => setTwoPass(e.target.checked)} />
              Two-pass refine
            </label>

            {genError && <div className="text-xs text-red-400 bg-red-950/40 rounded px-2 py-1">{genError}</div>}

            <button
              onClick={doGenerate}
              disabled={generating}
              className="mt-auto flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-indigo-600 text-white font-semibold hover:bg-indigo-500 disabled:opacity-60"
            >
              {generating ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
              {generating ? 'Rendering…' : `Generate ${frame === 'first' ? 'First' : 'Last'} Frame`}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
