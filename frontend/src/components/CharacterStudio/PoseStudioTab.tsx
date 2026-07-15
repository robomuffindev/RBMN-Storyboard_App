/**
 * Character Studio P2 — POSES tab.
 *
 * Grid of bundled pose presets (GET /pose-presets), multi-select, and a
 * "Generate Pose Set" action (POST /characters/{id}/poses/generate).
 * Results are read from character.manifest.pose_sets via the status object
 * the parent already polls (CharacterStatusT extended with pose_sets).
 */
import { useEffect, useState, useCallback, useRef } from 'react';
import { PersonStanding, Pencil, Trash2, Upload } from 'lucide-react';
import { EngineT, p2Api, PosePresetT, PoseSetEntryT } from './characterStudioP2Api';
import { Spinner, ErrorText, StatusChip, assetUrl, ImageLightbox } from './p2Shared';
import { PoseEditorModal } from './PoseEditorModal';
import StudioLibraryPicker from './StudioLibraryPicker';
import { toolsApi } from '../Tools/toolsApi';

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
  const [catFilter, setCatFilter] = useState<string>('all');
  const [importing, setImporting] = useState(false);
  const [importMsg, setImportMsg] = useState<string | null>(null);
  const importRef = useRef<HTMLInputElement | null>(null);
  const openposeRef = useRef<HTMLInputElement | null>(null);
  const pngRef = useRef<HTMLInputElement | null>(null);
  const [lightboxUrl, setLightboxUrl] = useState<string | null>(null);
  const [libOpen, setLibOpen] = useState(false);

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

  // Import a VNCCS-style poseset JSON ({canvas, poses:[...]}) or a flat list.
  const importPoseFile = async (file: File) => {
    setImporting(true);
    setImportMsg(null);
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      const category = (file.name.replace(/\.[^.]+$/, '') || 'Imported').slice(0, 40);
      const payload = Array.isArray(data)
        ? { poses: data, category }
        : Array.isArray(data?.poses) && !data?.canvas
        ? { poses: data.poses, category }
        : { poseset: data, category };
      const res = await p2Api.importPoses(payload);
      setImportMsg(`Imported ${res.imported} pose${res.imported === 1 ? '' : 's'} into "${category}".`);
      await loadPresets();
    } catch (e: any) {
      setImportMsg(`Import failed: ${e.message}`);
    } finally {
      setImporting(false);
    }
  };

  // Import OpenPose keypoint files (single .json, an array, or a .zip of many).
  const importPngFile = async (file: File) => {
    setImporting(true); setImportMsg(null); setGenError(null);
    try {
      const res = await p2Api.importPoseImages(file, 'OpenPose PNG');
      setImportMsg(`Imported ${res.imported} PNG pose(s).`);
      await loadPresets();
    } catch (e: any) {
      setGenError(e?.message || 'PNG import failed');
    } finally { setImporting(false); }
  };

  const importOpenposeFile = async (file: File) => {
    setImporting(true);
    setImportMsg(null);
    try {
      const category = (file.name.replace(/\.[^.]+$/, '') || 'OpenPose').slice(0, 40);
      const res = await p2Api.importOpenpose(file, category);
      setImportMsg(
        `Imported ${res.imported} OpenPose pose${res.imported === 1 ? '' : 's'} into "${res.category}".`
      );
      await loadPresets();
    } catch (e: any) {
      setImportMsg(`OpenPose import failed: ${e.message}`);
    } finally {
      setImporting(false);
    }
  };

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

  const presetCategories = Array.from(
    new Set(presets.map((p) => p.category || 'Basic'))
  ).sort();
  const visiblePresets =
    catFilter === 'all' ? presets : presets.filter((p) => (p.category || 'Basic') === catFilter);

  return (
    <div className="flex flex-col gap-4">
      {!hasBase && (
        <div className="text-sm text-amber-300 bg-amber-950/20 border border-amber-900/40 rounded-md px-3 py-2">
          Generate a base render first (Sheet tab) — pose generation requires it.
        </div>
      )}

      {engine !== 'qwen' && (
        <div className="text-xs text-amber-300 bg-amber-950/20 border border-amber-900/40 rounded-md px-3 py-2">
          On <strong>Klein</strong>, pose transfer uses the <strong>RefControl Pose LoRA</strong> — set it in
          Settings (<code>cs_klein_pose_lora</code>) and install <code>refcontrol_v2_poses.safetensors</code> on a
          Klein worker. For the strongest, most reliable pose control, <strong>Qwen (VNCCS)</strong> with the
          PoseStudio LoRA is recommended. Without a pose LoRA configured, the engine mostly reproduces the base pose.
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
        <input
          ref={importRef}
          type="file"
          accept="application/json,.json"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) importPoseFile(f);
            e.target.value = '';
          }}
        />
        <button
          onClick={() => importRef.current?.click()}
          disabled={importing}
          title="Import a VNCCS poseset JSON (or a list of poses) as a categorized custom set"
          className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 disabled:opacity-50 rounded-md text-sm font-medium flex items-center gap-2"
        >
          {importing ? <Spinner size={13} /> : <Upload size={13} />}
          Import Poses
        </button>
        <input
          ref={openposeRef}
          type="file"
          accept="application/json,.json,application/zip,.zip"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) importOpenposeFile(f);
            e.target.value = '';
          }}
        />
        <button
          onClick={() => openposeRef.current?.click()}
          disabled={importing}
          title="Import OpenPose keypoint files (BODY_25 / COCO-18) — a single .json, an array, or a .zip of thousands"
          className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 disabled:opacity-50 rounded-md text-sm font-medium flex items-center gap-2"
        >
          {importing ? <Spinner size={13} /> : <Upload size={13} />}
          Import OpenPose
        </button>
        <input
          ref={pngRef}
          type="file"
          accept="image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp,application/zip,.zip"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) importPngFile(f);
            e.target.value = '';
          }}
        />
        <button
          onClick={() => pngRef.current?.click()}
          disabled={importing}
          title="Import PNG/JPG OpenPose skeleton images (a single image or a .zip of thousands) — used directly as the pose control, no conversion"
          className="px-3 py-1.5 bg-indigo-700 hover:bg-indigo-600 border border-indigo-600 disabled:opacity-50 rounded-md text-sm font-medium flex items-center gap-2"
        >
          {importing ? <Spinner size={13} /> : <Upload size={13} />}
          Import PNG Poses
        </button>
        <button
          onClick={() => setLibOpen(true)}
          title="Pick poses from your Tools → Pose Library"
          className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-md text-sm font-medium flex items-center gap-2"
        >
          <PersonStanding size={13} /> Pose Library
        </button>
        {presetCategories.length > 1 && (
          <select
            value={catFilter}
            onChange={(e) => setCatFilter(e.target.value)}
            className="ml-auto bg-gray-800 border border-gray-700 rounded-md px-3 py-1.5 text-sm text-gray-200"
            title="Filter poses by category"
          >
            <option value="all">All categories ({presets.length})</option>
            {presetCategories.map((cat) => (
              <option key={cat} value={cat}>
                {cat} ({presets.filter((p) => (p.category || 'Basic') === cat).length})
              </option>
            ))}
          </select>
        )}
      </div>
      {importMsg && <div className="text-xs text-gray-400">{importMsg}</div>}

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
          {visiblePresets.map((preset) => {
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
                    <img
                      src={resultUrl}
                      alt={preset.name}
                      onClick={(e) => {
                        e.stopPropagation();
                        setLightboxUrl(resultUrl);
                      }}
                      title="Click to enlarge"
                      className="w-full h-full object-cover cursor-zoom-in"
                    />
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
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      setLightboxUrl(resultUrl || p2Api.posePresetThumbnailUrl(preset.id));
                    }}
                    title="View pose"
                    className="absolute bottom-1.5 left-1.5 z-10 bg-black/60 hover:bg-black/80 rounded px-1.5 py-0.5 text-[10px] text-gray-200"
                  >
                    view
                  </button>
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

      {lightboxUrl && <ImageLightbox url={lightboxUrl} onClose={() => setLightboxUrl(null)} />}
      {libOpen && (
        <StudioLibraryPicker
          kind="pose"
          onClose={() => setLibOpen(false)}
          onConfirm={async ({ poseIds }) => {
            if (poseIds && poseIds.length) {
              await toolsApi.poseToPresets(poseIds);
              await loadPresets();
            }
          }}
        />
      )}
    </div>
  );
}
