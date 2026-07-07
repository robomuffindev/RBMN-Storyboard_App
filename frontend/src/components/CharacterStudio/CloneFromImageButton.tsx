/**
 * Character Studio — Clone-from-reference-image button.
 *
 * NVCCS "clone character" style: upload a reference character image, have the
 * Ollama vision model describe it, then extract a full character tag sheet
 * (sex/age/race/skin/body/face/hair/eyes/additional_details) from that
 * description via POST /wizards/clone. The parsed info is handed back through
 * onApply so the caller can merge it into the editable Character Info fields.
 *
 * Requires Ollama Vision (Settings → Vision model) + an Ollama text model
 * (Settings → LLM). Talks to the Ollama HTTP API directly, not ComfyUI.
 */
import { useState, useEffect, useRef } from 'react';
import { ImagePlus } from 'lucide-react';
import { p2Api, WizardCharacterInfoT } from './characterStudioP2Api';
import { Spinner, ErrorText } from './p2Shared';

export function CloneFromImageButton({
  studioProjectId,
  onApply,
  style,
}: {
  studioProjectId: string | null | undefined;
  onApply: (info: WizardCharacterInfoT, description?: string) => void;
  style?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (busy) {
      setElapsed(0);
      timerRef.current = setInterval(() => setElapsed((e) => e + 1), 1000);
    } else if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [busy]);

  const run = async (file: File) => {
    if (!studioProjectId) {
      setError('Studio project not ready — try again in a moment.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const fd = new FormData();
      fd.append('asset_type', 'reference');
      fd.append('file', file);
      const res = await fetch(`/api/projects/${studioProjectId}/assets/upload`, {
        method: 'POST',
        body: fd,
      });
      if (!res.ok) throw new Error(`Upload failed (${res.status})`);
      const asset = await res.json();
      const out = await p2Api.wizardClone(asset.id, style);
      onApply(out.character_info || {}, out.vision_description);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const stageMsg =
    elapsed < 2
      ? 'Uploading reference…'
      : elapsed < 8
      ? 'Vision model is analyzing the image…'
      : 'Still working — vision models can be slow…';

  return (
    <div className="flex flex-col gap-1 items-end">
      <input
        ref={fileRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) run(f);
          e.target.value = '';
        }}
      />
      <button
        type="button"
        onClick={() => fileRef.current?.click()}
        disabled={busy}
        title="Analyze a reference character image and auto-fill the tag sheet"
        className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 disabled:opacity-50 rounded-md text-sm font-medium flex items-center gap-2 text-gray-300"
      >
        {busy ? <Spinner size={14} /> : <ImagePlus size={14} className="text-cyan-400" />}
        Clone from image
      </button>
      {busy && (
        <span className="text-xs text-gray-400 flex items-center gap-1.5">
          <Spinner size={11} />
          {stageMsg} <span className="text-gray-600">({elapsed}s)</span>
        </span>
      )}
      <ErrorText msg={error} />
    </div>
  );
}
