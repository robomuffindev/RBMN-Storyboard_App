/**
 * Character Studio P2 — GENERATE ALL modal + progress panel.
 *
 * Opens from a header button in CharacterDetail. Lets the user toggle which
 * stages to include (shots / costumes / emotions / cutout / upscale) and
 * pick an engine, then POSTs /characters/{id}/generate-all. The backend
 * runs the pipeline as a background task and immediately returns
 * {ok, status: "running"} — progress is read from
 * character.manifest.generate_all via the same /status poll the parent
 * already performs (CharacterDetail passes the latest GenerateAllStateT in
 * as a prop and this component polls the parent's onPoll callback every 5s
 * while running).
 *
 * Terminal-state ambiguity: the doc's manifest shape comment shows
 * "status": "running|done|failed" but the prose right above says "done" or
 * "failed" as example status literals in prior sections use "error" too
 * (e.g. StatusChip's shared vocabulary). We treat any of
 * done/failed/error/completed as terminal (see isTerminalGenerateAllStatus
 * in characterStudioP2Api.ts) to be defensive against minor wording drift.
 */
import { useEffect, useRef, useState } from 'react';
import { X, Sparkles } from 'lucide-react';
import { CostumeT, EngineT, EmotionCatalogEntryT, EmotionsCatalogT, GenerateAllIncludeT, GenerateAllStateT, p2Api, UpscaleModeT } from './characterStudioP2Api';
import { isTerminalGenerateAllStatus } from './characterStudioP2Api';
import { Spinner, ErrorText } from './p2Shared';

function flattenEmotionKeys(raw: EmotionsCatalogT | EmotionCatalogEntryT[] | undefined): EmotionCatalogEntryT[] {
  if (!raw) return [];
  if (Array.isArray(raw)) return raw;
  return Object.values(raw).flat();
}

export function GenerateAllModal({
  characterId,
  engine,
  costumes,
  generateAll,
  onClose,
  onStarted,
  onPoll,
}: {
  characterId: string;
  engine: EngineT;
  costumes: Record<string, CostumeT> | undefined;
  generateAll: GenerateAllStateT | undefined;
  onClose: () => void;
  onStarted: () => void;
  onPoll: () => void;
}) {
  const [includeShots, setIncludeShots] = useState(true);
  const [includeCostumeIds, setIncludeCostumeIds] = useState<Set<string>>(new Set());
  const [includeEmotions, setIncludeEmotions] = useState<Set<string>>(new Set());
  const [includeCutout, setIncludeCutout] = useState(false);
  const [includeUpscale, setIncludeUpscale] = useState(false);
  const [upscaleMode, setUpscaleMode] = useState<UpscaleModeT>('auto');
  const [modalEngine, setModalEngine] = useState<EngineT>(engine);
  const [emotionOptions, setEmotionOptions] = useState<EmotionCatalogEntryT[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    p2Api
      .getCatalogsRaw()
      .then((res) => setEmotionOptions(flattenEmotionKeys(res?.emotions)))
      .catch(() => setEmotionOptions([]));
  }, []);

  const running = generateAll?.status === 'running' || (generateAll?.status && !isTerminalGenerateAllStatus(generateAll.status));

  useEffect(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    if (running) {
      pollRef.current = setInterval(onPoll, 5000);
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [running, onPoll]);

  const toggleCostume = (id: string) => {
    setIncludeCostumeIds((prev) => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  };

  const toggleEmotion = (key: string) => {
    setIncludeEmotions((prev) => {
      const n = new Set(prev);
      if (n.has(key)) n.delete(key);
      else n.add(key);
      return n;
    });
  };

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const include: GenerateAllIncludeT = {
        shots: includeShots,
        costume_ids: Array.from(includeCostumeIds),
        emotions: Array.from(includeEmotions),
        cutout: includeCutout,
        upscale: includeUpscale,
        upscale_mode: includeUpscale ? upscaleMode : undefined,
      };
      await p2Api.generateAll(characterId, { engine: modalEngine, include });
      onStarted();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const costumeList = Object.values(costumes || {});

  return (
    <div className="fixed inset-0 bg-black/70 z-[9990] flex items-center justify-center p-4" onClick={onClose}>
      <div
        className="bg-gray-900 border border-gray-800 rounded-lg p-6 w-full max-w-lg flex flex-col gap-4 max-h-[85vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <Sparkles size={18} className="text-purple-400" />
            Generate All
          </h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300">
            <X size={18} />
          </button>
        </div>

        <label className="flex items-center gap-2 text-sm">
          <span className="text-gray-400">Engine</span>
          <select
            value={modalEngine}
            onChange={(e) => setModalEngine(e.target.value as EngineT)}
            className="bg-gray-800 border border-gray-700 rounded-md px-3 py-1.5 text-gray-100 focus:outline-none focus:border-purple-600 text-sm"
          >
            <option value="auto">auto</option>
            <option value="qwen">qwen (VNCCS worker)</option>
            <option value="klein">klein</option>
          </select>
        </label>

        <label className="flex items-center gap-2 text-sm text-gray-300">
          <input type="checkbox" checked={includeShots} onChange={(e) => setIncludeShots(e.target.checked)} />
          Shots (Phase 1 reference sheet)
        </label>

        <div className="flex flex-col gap-1.5">
          <span className="text-sm text-gray-400">Costumes</span>
          {costumeList.length ? (
            <div className="flex flex-wrap gap-1.5">
              {costumeList.map((c) => (
                <button
                  key={c.id}
                  onClick={() => toggleCostume(c.id)}
                  className={`px-2.5 py-1 rounded-full text-xs font-medium border transition-colors ${
                    includeCostumeIds.has(c.id)
                      ? 'bg-purple-900/50 border-purple-600 text-purple-200'
                      : 'bg-gray-800 border-gray-700 text-gray-300 hover:border-gray-600'
                  }`}
                >
                  {c.name}
                </button>
              ))}
            </div>
          ) : (
            <span className="text-xs text-gray-600">No costumes created yet.</span>
          )}
        </div>

        <div className="flex flex-col gap-1.5">
          <span className="text-sm text-gray-400">Emotions</span>
          {emotionOptions.length ? (
            <div className="flex flex-wrap gap-1.5 max-h-40 overflow-y-auto">
              {emotionOptions.map((e) => (
                <button
                  key={e.safe_name}
                  onClick={() => toggleEmotion(e.safe_name)}
                  className={`px-2.5 py-1 rounded-full text-xs font-medium border transition-colors ${
                    includeEmotions.has(e.safe_name)
                      ? 'bg-purple-900/50 border-purple-600 text-purple-200'
                      : 'bg-gray-800 border-gray-700 text-gray-300 hover:border-gray-600'
                  }`}
                >
                  {e.key}
                </button>
              ))}
            </div>
          ) : (
            <span className="text-xs text-gray-600">Loading emotion catalog...</span>
          )}
        </div>

        <div className="flex items-center gap-4 flex-wrap">
          <label className="flex items-center gap-2 text-sm text-gray-300">
            <input type="checkbox" checked={includeCutout} onChange={(e) => setIncludeCutout(e.target.checked)} />
            Cutout
          </label>
          <label className="flex items-center gap-2 text-sm text-gray-300">
            <input type="checkbox" checked={includeUpscale} onChange={(e) => setIncludeUpscale(e.target.checked)} />
            Upscale
          </label>
          {includeUpscale && (
            <label className="flex items-center gap-2 text-sm text-gray-300">
              <span className="text-gray-400">Mode</span>
              <select
                value={upscaleMode}
                onChange={(e) => setUpscaleMode(e.target.value as UpscaleModeT)}
                className="bg-gray-800 border border-gray-700 rounded-md px-2 py-1 text-gray-100 focus:outline-none focus:border-purple-600 text-xs"
              >
                <option value="auto">Auto</option>
                <option value="seedvr2">SeedVR2 (premium)</option>
                <option value="gan">GAN</option>
              </select>
            </label>
          )}
        </div>

        <ErrorText msg={error} />

        {generateAll && (
          <div className="bg-gray-800/60 border border-gray-700 rounded-md p-3 flex flex-col gap-2">
            <div className="flex items-center gap-2 text-sm">
              {running && <Spinner size={13} />}
              <span className="text-gray-300">
                Status: <span className="font-medium">{generateAll.status || 'unknown'}</span>
                {generateAll.stage ? ` — stage: ${generateAll.stage}` : ''}
              </span>
            </div>
            {!!generateAll.errors?.length && (
              <div className="flex flex-col gap-1 max-h-40 overflow-y-auto">
                {generateAll.errors.map((e, i) => (
                  <div key={i} className="text-xs text-amber-300 bg-amber-950/20 border border-amber-900/40 rounded px-2 py-1">
                    {e}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-400 hover:text-gray-200">
            Close
          </button>
          <button
            onClick={start}
            disabled={busy || !!running}
            className="px-4 py-2 bg-purple-700 hover:bg-purple-600 disabled:opacity-50 rounded-md text-sm font-medium flex items-center gap-2"
          >
            {busy && <Spinner size={14} />}
            Start
          </button>
        </div>
      </div>
    </div>
  );
}
