/**
 * Character Studio P2 — EMOTIONS tab.
 *
 * Emotion picker sourced from GET /catalogs (emotions dict, grouped by
 * category when the catalog provides one, else a flat list), a "source"
 * select (base or a completed costume sprite — shots aren't documented as a
 * valid `source` value in the P2 contract, so this only offers base/costume
 * per section 3 of the doc), an optional costume selector, multi-select
 * emotions, and a "Generate Emotions" action.
 *
 * Results grid reads character.manifest.emotions (via status), showing the
 * full render + face-crop thumbnail when available.
 */
import { useEffect, useState, useCallback } from 'react';
import { Smile } from 'lucide-react';
import { CostumeT, EmotionCatalogEntryT, EmotionEntryT, EmotionsCatalogT, EngineT, p2Api } from './characterStudioP2Api';
import { Spinner, ErrorText, StatusChip, assetUrl } from './p2Shared';

function normalizeCatalog(raw: EmotionsCatalogT | EmotionCatalogEntryT[] | undefined): {
  key: string;
  entries: EmotionCatalogEntryT[];
}[] {
  if (!raw) return [];
  if (Array.isArray(raw)) {
    return [{ key: 'All', entries: raw }];
  }
  return Object.entries(raw).map(([key, entries]) => ({ key, entries: entries || [] }));
}

export function EmotionsTab({
  characterId,
  studioProjectId,
  emotions,
  costumes,
  engine,
  hasBase,
  onGenerated,
}: {
  characterId: string;
  studioProjectId: string | null | undefined;
  emotions: Record<string, EmotionEntryT> | undefined;
  costumes: Record<string, CostumeT> | undefined;
  engine: EngineT;
  hasBase: boolean;
  onGenerated: () => void;
}) {
  const [groups, setGroups] = useState<{ key: string; entries: EmotionCatalogEntryT[] }[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [source, setSource] = useState<'base' | 'costume'>('base');
  // Emotion-specific engine override; '' = follow the page-level selector.
  // 'facedetailer' = VNCCS's face-crop re-render (needs Impact-Pack on the
  // VNCCS worker) — best for small faces in full-body sprites.
  const [emotionEngine, setEmotionEngine] = useState<'' | 'qwen' | 'klein' | 'facedetailer'>('');
  const [costumeId, setCostumeId] = useState<string>('');
  const [busy, setBusy] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);
  const [lastErrors, setLastErrors] = useState<string[]>([]);

  const loadCatalog = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const res = await p2Api.getCatalogsRaw();
      setGroups(normalizeCatalog(res?.emotions));
    } catch (e: any) {
      setLoadError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadCatalog();
  }, [loadCatalog]);

  const costumeList = Object.values(costumes || {});

  const toggle = (safeName: string) => {
    setSelected((prev) => {
      const n = new Set(prev);
      if (n.has(safeName)) n.delete(safeName);
      else n.add(safeName);
      return n;
    });
  };

  const generate = async () => {
    if (!selected.size) return;
    if (source === 'costume' && !costumeId) {
      setGenError('Select a costume to use as the emotion source.');
      return;
    }
    setBusy(true);
    setGenError(null);
    setLastErrors([]);
    try {
      const res = await p2Api.generateEmotions(characterId, {
        emotions: Array.from(selected),
        costume_id: source === 'costume' ? costumeId : null,
        source: 'base',
        engine: (emotionEngine || engine) as EngineT,
      });
      if (res?.errors?.length) setLastErrors(res.errors);
      setSelected(new Set());
      onGenerated();
    } catch (e: any) {
      setGenError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      {!hasBase && (
        <div className="text-sm text-amber-300 bg-amber-950/20 border border-amber-900/40 rounded-md px-3 py-2">
          Generate a base render first (Sheet tab) — emotion generation requires it.
        </div>
      )}

      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 flex flex-wrap items-end gap-4">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-gray-400">Source</span>
          <select
            value={source}
            onChange={(e) => setSource(e.target.value as 'base' | 'costume')}
            className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 focus:outline-none focus:border-purple-600"
          >
            <option value="base">Base render</option>
            <option value="costume">Costume sprite</option>
          </select>
        </label>

        {source === 'costume' && (
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-gray-400">Costume</span>
            <select
              value={costumeId}
              onChange={(e) => setCostumeId(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 focus:outline-none focus:border-purple-600 min-w-[160px]"
            >
              <option value="">(select)</option>
              {costumeList.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
        )}

        <select

          value={emotionEngine}

          onChange={(e) => setEmotionEngine(e.target.value as '' | 'qwen' | 'klein' | 'facedetailer')}

          className="bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm"

          title="Emotion engine — FaceDetailer re-renders just the face at high res (needs Impact-Pack on the VNCCS worker); best for small faces in full-body sprites"

        >

          <option value="">Engine: follow page</option>

          <option value="qwen">Engine: Qwen (whole-image edit)</option>

          <option value="klein">Engine: Klein (face-mask inpaint)</option>

          <option value="facedetailer">Engine: FaceDetailer (face-crop re-render)</option>

        </select>

        <button
          onClick={generate}
          disabled={busy || !selected.size || !hasBase}
          className="px-3 py-1.5 bg-indigo-700 hover:bg-indigo-600 disabled:opacity-50 rounded-md text-sm font-medium flex items-center gap-2"
        >
          {busy && <Spinner size={13} />}
          <Smile size={14} />
          Generate Emotions ({selected.size})
        </button>
      </div>

      <ErrorText msg={loadError} />
      <ErrorText msg={genError} />
      {!!lastErrors.length && (
        <div className="flex flex-col gap-1">
          {lastErrors.map((e, i) => (
            <ErrorText key={i} msg={e} />
          ))}
        </div>
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-gray-400">
          <Spinner size={14} /> Loading emotion catalog...
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          {groups.map((g) => (
            <div key={g.key} className="flex flex-col gap-2">
              {groups.length > 1 && (
                <div className="text-xs uppercase tracking-wide text-gray-500">{g.key}</div>
              )}
              <div className="flex flex-wrap gap-2">
                {g.entries.map((e) => {
                  const isSelected = selected.has(e.safe_name);
                  return (
                    <button
                      key={e.safe_name}
                      onClick={() => toggle(e.safe_name)}
                      title={e.description || e.natural_prompt || e.key}
                      className={`px-2.5 py-1 rounded-full text-xs font-medium border transition-colors ${
                        isSelected
                          ? 'bg-purple-900/50 border-purple-600 text-purple-200'
                          : 'bg-gray-800 border-gray-700 text-gray-300 hover:border-gray-600'
                      }`}
                    >
                      {e.key}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
          {!groups.length && (
            <div className="text-center text-sm text-gray-500 py-6">No emotion catalog available.</div>
          )}
        </div>
      )}

      <div className="border-t border-gray-800 pt-4">
        <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide mb-3">Generated Emotions</h3>
        {!emotions || !Object.keys(emotions).length ? (
          <div className="text-sm text-gray-500 bg-gray-900 border border-gray-800 rounded-lg p-6 text-center">
            No emotions generated yet.
          </div>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3">
            {Object.entries(emotions).map(([key, entry]) => {
              const fullUrl = entry.asset_id ? assetUrl(studioProjectId, entry.asset_id) : null;
              const faceUrl = entry.face_crop_asset_id ? assetUrl(studioProjectId, entry.face_crop_asset_id) : null;
              return (
                <div key={key} className="bg-gray-900 border border-gray-800 rounded-lg p-2 flex flex-col gap-2">
                  <div className="flex gap-1.5">
                    <div className="flex-1 aspect-square bg-gray-800 border border-gray-700 rounded overflow-hidden flex items-center justify-center">
                      {fullUrl ? (
                        <img src={fullUrl} alt={key} className="w-full h-full object-cover" />
                      ) : (
                        <Smile size={20} className="text-gray-600" />
                      )}
                    </div>
                    {faceUrl && (
                      <div className="w-1/3 aspect-square bg-gray-800 border border-gray-700 rounded overflow-hidden flex items-center justify-center shrink-0">
                        <img src={faceUrl} alt={`${key} face`} className="w-full h-full object-cover" />
                      </div>
                    )}
                  </div>
                  <div className="flex items-center justify-between gap-1">
                    <span className="text-xs text-gray-300 truncate" title={key}>
                      {key}
                    </span>
                    <span title={entry.error || ''}>
                      <StatusChip status={entry.status} />
                    </span>
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
