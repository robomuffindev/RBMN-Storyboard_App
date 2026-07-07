/**
 * Character Studio P2 — POSES tab.
 *
 * Grid of bundled pose presets (GET /pose-presets), multi-select, and a
 * "Generate Pose Set" action (POST /characters/{id}/poses/generate).
 * Results are read from character.manifest.pose_sets via the status object
 * the parent already polls (CharacterStatusT extended with pose_sets).
 */
import { useEffect, useState, useCallback } from 'react';
import { PersonStanding, Pencil, Trash2 } from 'lucide-react';
import { EngineT, p2Api, PosePresetT, PoseSetEntryT } from './characterStudioP2Api';
import { Spinner, ErrorText, StatusChip, assetUrl } from './p2Shared';
import { PoseEditorModal } from './PoseEditorModal';

export function PoseStudioTab({
  characterId,
  studioProjectId,
  poseSets,
  engine,
  hasBase,
  onGenerated,
}: {
  characterId: string;
  studioProjectId: string | null | undefined;
  poseSets: Record<string, PoseSetEntryT> | undefined;
  engine: EngineT;
  hasBase: boolean;
  onGenerated: () => void;
}) {
  const [presets, setPresets] = useState<PosePresetT[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);
  const [lastErrors, setLastErrors] = useState<string[]>([]);
  const [editorOpenFor, setEditorOpenFor] = useState<string | null | undefined>(undefined);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const loadPresets = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const res = await p2Api.listPosePresets();
      setPresets(res?.presets || []);
    } catch (e: any) {
      setLoadError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadPresets();
  }, [loadPresets]);

  const toggle = (id: string) => {
    setSelected((prev) => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  };

  const deleteCustomPreset = async (e: React.MouseEvent, presetId: string) => {
    e.stopPropagation();
    if (!window.confirm('Delete this custom pose preset?')) return;
    setDeletingId(presetId);
    setDeleteError(null);
    try {
      await p2Api.deleteCustomPosePreset(presetId);
      await loadPresets();
    } catch (err: any) {
      setDeleteError(err.message);
    } finally {
      setDeletingId(null);
    }
  };

  const generate = async () => {
    if (!selected.size) return;
    setBusy(true);
    setGenError(null);
    setLastErrors([]);
    try {
      const res = await p2Api.generatePoses(characterId, {
        preset_ids: Array.from(selected),
        engine,
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
          Generate a base render first (Sheet tab) — pose generation requires it.
        </div>
      )}

      <div className="flex items-center gap-3">
        <button
          onClick={generate}
          disabled={busy || !selected.size || !hasBase}
          className="px-3 py-1.5 bg-indigo-700 hover:bg-indigo-600 disabled:opacity-50 rounded-md text-sm font-medium flex items-center gap-2"
        >
          {busy && <Spinner size={13} />}
          <PersonStanding size={14} />
          Generate Pose Set ({selected.size})
        </button>
        <button
          onClick={() => setEditorOpenFor(null)}
          className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-md text-sm font-medium flex items-center gap-2"
        >
          <Pencil size={13} />
          Pose Editor
        </button>
      </div>

      <ErrorText msg={loadError} />
      <ErrorText msg={genError} />
      <ErrorText msg={deleteError} />
      {!!lastErrors.length && (
        <div className="flex flex-col gap-1">
          {lastErrors.map((e, i) => (
            <ErrorText key={i} msg={e} />
          ))}
        </div>
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-gray-400">
          <Spinner size={14} /> Loading pose presets...
        </div>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3">
          {presets.map((preset) => {
            const entry = poseSets?.[preset.id];
            const isSelected = selected.has(preset.id);
            const resultUrl = entry?.asset_id ? assetUrl(studioProjectId, entry.asset_id) : null;
            return (
              <div
                key={preset.id}
                onClick={() => toggle(preset.id)}
                className={`bg-gray-900 border rounded-lg p-2 flex flex-col gap-2 cursor-pointer transition-colors ${
                  isSelected ? 'border-purple-500 ring-1 ring-purple-500/50' : 'border-gray-800 hover:border-gray-700'
                }`}
              >
                <div className="aspect-square bg-gray-800 border border-gray-700 rounded overflow-hidden flex items-center justify-center relative">
                  {resultUrl ? (
                    <img src={resultUrl} alt={preset.name} className="w-full h-full object-cover" />
                  ) : (
                    <img
                      src={p2Api.posePresetThumbnailUrl(preset.id)}
                      alt={preset.name}
                      className="w-full h-full object-contain opacity-70"
                      onError={(e) => {
                        (e.target as HTMLImageElement).style.display = 'none';
                      }}
                    />
                  )}
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={() => toggle(preset.id)}
                    onClick={(e) => e.stopPropagation()}
                    className="absolute top-1.5 left-1.5"
                  />
                  {preset.custom && (
                    <span className="absolute top-1.5 right-1.5 px-1.5 py-0.5 rounded-full text-[9px] font-medium bg-purple-900/70 text-purple-200 uppercase tracking-wide">
                      custom
                    </span>
                  )}
                  <div className="absolute bottom-1.5 right-1.5 flex items-center gap-1">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setEditorOpenFor(preset.id);
                      }}
                      title="Edit in Pose Editor"
                      className="p-1 rounded bg-gray-950/70 text-gray-300 hover:text-purple-300"
                    >
                      <Pencil size={11} />
                    </button>
                    {preset.custom && (
                      <button
                        onClick={(e) => deleteCustomPreset(e, preset.id)}
                        disabled={deletingId === preset.id}
                        title="Delete custom pose"
                        className="p-1 rounded bg-gray-950/70 text-gray-300 hover:text-red-400 disabled:opacity-50"
                      >
                        {deletingId === preset.id ? <Spinner size={11} /> : <Trash2 size={11} />}
                      </button>
                    )}
                  </div>
                </div>
                <div className="text-xs text-gray-300 truncate" title={preset.name}>
                  {preset.name}
                </div>
                {entry && (
                  <div title={entry.error || ''}>
                    <StatusChip status={entry.status} />
                  </div>
                )}
              </div>
            );
          })}
          {!presets.length && (
            <div className="col-span-full text-center text-sm text-gray-500 py-6">No pose presets available.</div>
          )}
        </div>
      )}

      {editorOpenFor !== undefined && (
        <PoseEditorModal
          presets={presets}
          initialPresetId={editorOpenFor}
          onClose={() => setEditorOpenFor(undefined)}
          onSaved={() => {
            loadPresets();
          }}
        />
      )}
    </div>
  );
}
