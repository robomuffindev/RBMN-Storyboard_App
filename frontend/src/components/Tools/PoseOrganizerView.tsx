/**
 * Pose Organizer — scan a server folder path or an uploaded .zip of poses,
 * review auto-classified/auto-tagged/deduped candidates, then commit selected
 * ones into the Pose Library.
 */
import { useEffect, useRef, useState, useCallback } from 'react';
import { FolderInput, Upload, Check, Loader2, Sparkles } from 'lucide-react';
import { ImageLightbox } from '../CharacterStudio/p2Shared';
import { toolsApi, ScanCandidateT, ScanStatusT } from './toolsApi';
import SampleGenerateModal from './SampleGenerateModal';

export function PoseOrganizerView({ onCommitted }: { onCommitted?: () => void }) {
  const [folder, setFolder] = useState('');
  const [runVision, setRunVision] = useState(false);
  const [scanId, setScanId] = useState<string | null>(null);
  const [status, setStatus] = useState<ScanStatusT | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [category, setCategory] = useState('Imported');
  const [extraTags, setExtraTags] = useState('');
  const [lightbox, setLightbox] = useState<string | null>(null);
  const [committing, setCommitting] = useState(false);
  const zipRef = useRef<HTMLInputElement | null>(null);
  const imgRef = useRef<HTMLInputElement | null>(null);
  const [caps, setCaps] = useState<{ dwpose: boolean; klein: boolean } | null>(null);
  const [genOpen, setGenOpen] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [extractMsg, setExtractMsg] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const poll = useCallback(async (id: string) => {
    try {
      // Load ALL candidates (backend caps at MAX_SCAN) so commit/select-all
      // aren't limited to the first page.
      const s = await toolsApi.poseScanStatus(id, 0, 5000);
      setStatus(s);
      if (s.status !== 'scanning' && pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
        // auto-select all non-duplicate keypoint candidates
        setSelected(new Set(s.candidates.filter((c) => c.has_joints && !c.duplicate).map((c) => c.cand_id)));
      }
    } catch (e: any) {
      setError(e.message);
      // Stop polling on a hard failure (e.g. scan row gone) so we don't
      // hammer the endpoint forever.
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    }
  }, []);

  useEffect(() => {
    toolsApi.capabilities().then(setCaps).catch(() => setCaps({ dwpose: false, klein: false }));
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const extractImages = async (fileList: File[]) => {
    if (!fileList.length) return;
    setExtracting(true);
    setExtractMsg(null);
    try {
      const res = await toolsApi.poseExtract(fileList, { category: 'Extracted', detect_hands: true });
      setExtractMsg(`Extracted ${res.extracted} pose(s).${res.errors.length ? ` ${res.errors.length} skipped.` : ''}`);
      if (res.extracted > 0) onCommitted?.();
    } catch (e: any) {
      setExtractMsg(`Extract failed: ${e.message}`);
    } finally {
      setExtracting(false);
    }
  };

  const startScan = async (opts: { folder?: string; file?: File }) => {
    setBusy(true);
    setError(null);
    setStatus(null);
    setSelected(new Set());
    try {
      const res = await toolsApi.poseScan({ ...opts, run_vision: runVision });
      setScanId(res.scan_id);
      setStatus({ scan_id: res.scan_id, status: 'scanning', summary: {}, total: 0, candidates: [] });
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(() => poll(res.scan_id), 1500);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const toggle = (id: string) =>
    setSelected((prev) => {
      const n = new Set(prev);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });

  const commit = async () => {
    if (!scanId || !selected.size) return;
    setCommitting(true);
    setError(null);
    try {
      const res = await toolsApi.poseCommit(scanId, {
        cand_ids: Array.from(selected),
        category: category.trim() || 'Imported',
        extra_tags: extraTags.split(',').map((t) => t.trim()).filter(Boolean),
      });
      alert(`Added ${res.added} poses to the library.`);
      onCommitted?.();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setCommitting(false);
    }
  };

  const cands = status?.candidates || [];
  const scanning = status?.status === 'scanning';

  return (
    <div className="flex flex-col gap-4">
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 flex flex-col gap-3">
        <div className="text-sm text-gray-400">
          Point at a server folder of pose files (OpenPose keypoint JSON, paired sample images) or upload a
          <b> .zip</b> of thousands. Each pose is classified, converted to keypoints, auto-tagged, and deduped.
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <label className="flex flex-col gap-1 text-sm flex-1 min-w-[280px]">
            <span className="text-gray-400">Server folder path</span>
            <input
              value={folder}
              onChange={(e) => setFolder(e.target.value)}
              placeholder="e.g. D:\\poses\\openpose_pack"
              className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 text-sm"
            />
          </label>
          <button
            onClick={() => folder.trim() && startScan({ folder: folder.trim() })}
            disabled={busy || !folder.trim()}
            className="px-3 py-2 bg-teal-700 hover:bg-teal-600 disabled:opacity-50 rounded-md text-sm font-medium flex items-center gap-2"
          >
            <FolderInput size={14} /> Scan Folder
          </button>
          <input
            ref={zipRef}
            type="file"
            accept=".zip,application/zip"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) startScan({ file: f });
              e.target.value = '';
            }}
          />
          <button
            onClick={() => zipRef.current?.click()}
            disabled={busy}
            className="px-3 py-2 bg-gray-800 hover:bg-gray-700 border border-gray-700 disabled:opacity-50 rounded-md text-sm font-medium flex items-center gap-2"
          >
            <Upload size={14} /> Scan Zip
          </button>
          <label className="flex items-center gap-1.5 text-xs text-gray-400 ml-1" title="Describe each pose with the Ollama vision model (adds semantic tags — slower). Needs a vision model set in Settings.">
            <input type="checkbox" checked={runVision} onChange={(e) => setRunVision(e.target.checked)} />
            Vision scan (auto-tag poses)
          </label>
        </div>
        {error && <div className="text-sm text-red-400">{error}</div>}
      </div>

      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 flex flex-col gap-2">
        <div className="text-sm text-gray-300 font-medium">Extract poses from images (DWPose)</div>
        <div className="text-xs text-gray-500">
          Drop in photos, character art, or downloaded skeleton/mannequin renders — a GPU worker runs pose
          estimation and adds real editable keypoints to the library.
        </div>
        <div className="flex items-center gap-2">
          <input
            ref={imgRef}
            type="file"
            accept="image/*"
            multiple
            className="hidden"
            onChange={(e) => {
              const fs = Array.from(e.target.files || []);
              if (fs.length) extractImages(fs);
              e.target.value = '';
            }}
          />
          <button
            onClick={() => imgRef.current?.click()}
            disabled={extracting || (caps !== null && !caps.dwpose)}
            title={caps && !caps.dwpose ? 'No DWPose worker online — install comfyui_controlnet_aux on a GPU worker' : ''}
            className="px-3 py-2 bg-gray-800 hover:bg-gray-700 border border-gray-700 disabled:opacity-50 rounded-md text-sm font-medium flex items-center gap-2"
          >
            {extracting ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
            Extract from Images
          </button>
          <button
            onClick={() => setGenOpen(true)}
            className="px-3 py-2 bg-indigo-600 hover:bg-indigo-500 rounded-md text-sm font-medium flex items-center gap-2"
            title="Generate pose candidates with your own models, then extract keypoints"
          >
            <Sparkles size={14} /> Generate Sample
          </button>
          {caps && !caps.dwpose && (
            <span className="text-xs text-amber-400">Needs a DWPose worker (comfyui_controlnet_aux).</span>
          )}
          {extractMsg && <span className="text-xs text-gray-400">{extractMsg}</span>}
        </div>
      </div>

      {status && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 flex flex-col gap-3">
          <div className="flex items-center gap-3 text-sm flex-wrap">
            {scanning ? (
              <span className="flex items-center gap-2 text-teal-300">
                <Loader2 size={15} className="animate-spin" /> Scanning…
              </span>
            ) : (
              <span className="text-emerald-400 flex items-center gap-1">
                <Check size={15} /> {status.total} candidates
              </span>
            )}
            {Object.entries(status.summary || {}).map(([k, v]) => (
              <span key={k} className="text-xs text-gray-400 bg-gray-800/60 rounded-full px-2 py-0.5">
                {k}: {v}
              </span>
            ))}
          </div>

          {!scanning && cands.length > 0 && (
            <>
              <div className="flex flex-wrap items-end gap-2">
                <label className="flex flex-col gap-1 text-sm">
                  <span className="text-gray-400">Category</span>
                  <input value={category} onChange={(e) => setCategory(e.target.value)} className="bg-gray-800 border border-gray-700 rounded-md px-3 py-1.5 text-sm" />
                </label>
                <label className="flex flex-col gap-1 text-sm flex-1 min-w-[200px]">
                  <span className="text-gray-400">Extra tags (comma-separated)</span>
                  <input value={extraTags} onChange={(e) => setExtraTags(e.target.value)} placeholder="combat, dynamic" className="bg-gray-800 border border-gray-700 rounded-md px-3 py-1.5 text-sm" />
                </label>
                <button onClick={() => setSelected(new Set(cands.filter((c) => c.has_joints && !c.duplicate).map((c) => c.cand_id)))} className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-md text-sm">
                  Select all (non-dup)
                </button>
                <button onClick={() => setSelected(new Set())} className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-md text-sm">
                  Clear
                </button>
                <button
                  onClick={commit}
                  disabled={committing || !selected.size}
                  className="px-4 py-1.5 bg-teal-700 hover:bg-teal-600 disabled:opacity-50 rounded-md text-sm font-medium flex items-center gap-2 ml-auto"
                >
                  {committing && <Loader2 size={14} className="animate-spin" />}
                  Add {selected.size} to Library
                </button>
              </div>

              <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8 gap-2 max-h-[60vh] overflow-y-auto">
                {cands.map((c) => (
                  <PoseCandidateCard
                    key={c.cand_id}
                    scanId={scanId!}
                    c={c}
                    selected={selected.has(c.cand_id)}
                    onToggle={() => toggle(c.cand_id)}
                    onView={(url) => setLightbox(url)}
                  />
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {lightbox && <ImageLightbox url={lightbox} onClose={() => setLightbox(null)} />}
      {genOpen && <SampleGenerateModal kind="pose" dwposeAvailable={!!caps?.dwpose} onClose={() => setGenOpen(false)} onCommitted={() => { setGenOpen(false); onCommitted?.(); }} />}
    </div>
  );
}

function PoseCandidateCard({
  scanId,
  c,
  selected,
  onToggle,
  onView,
}: {
  scanId: string;
  c: ScanCandidateT;
  selected: boolean;
  onToggle: () => void;
  onView: (url: string) => void;
}) {
  const url = c.thumb ? toolsApi.poseScanThumbUrl(scanId, c.thumb) : null;
  return (
    <div
      onClick={onToggle}
      className={`relative bg-gray-800 border rounded-md overflow-hidden cursor-pointer ${
        selected ? 'border-teal-500 ring-1 ring-teal-500/50' : 'border-gray-700 hover:border-gray-600'
      }`}
      title={c.name}
    >
      <div className="aspect-[2/3] flex items-center justify-center bg-gray-900">
        {url ? (
          <img src={url} alt={c.name} className="w-full h-full object-contain" />
        ) : (
          <span className="text-gray-600 text-[10px]">no preview</span>
        )}
      </div>
      {url && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onView(url);
          }}
          className="absolute top-1 right-1 bg-black/60 hover:bg-black/80 rounded px-1 text-[10px] text-gray-200"
        >
          view
        </button>
      )}
      {c.duplicate && <span className="absolute top-1 left-1 bg-amber-900/80 text-amber-200 rounded px-1 text-[9px]">dup</span>}
      {!c.has_joints && <span className="absolute bottom-1 left-1 bg-gray-900/80 text-gray-400 rounded px-1 text-[9px]">img-only</span>}
      {selected && <span className="absolute bottom-1 right-1 bg-teal-600 rounded-full p-0.5"><Check size={10} /></span>}
    </div>
  );
}
