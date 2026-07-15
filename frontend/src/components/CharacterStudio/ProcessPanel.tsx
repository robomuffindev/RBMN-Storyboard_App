/**
 * Character Studio P2 — PROCESS controls (cutout / upscale).
 *
 * Rendered as a footer panel inside the Renders tab. Lets the user pick
 * which image refs to process (base / shot ids / costume:<id> / emotion:<key>),
 * which steps to run (cutout / upscale), and an engine, then calls
 * POST /characters/{id}/process. Cutout can return inline_results
 * synchronously (CPU fallback) in addition to async jobs — both are surfaced.
 */
import { useState } from 'react';
import { Scissors, ZoomIn } from 'lucide-react';
import {
  CostumeT,
  EmotionEntryT,
  EngineT,
  p2Api,
  ProcessedEntryT,
  UpscaleModeT,
} from './characterStudioP2Api';
import { Spinner, ErrorText, StatusChip, assetUrl, ImageLightbox } from './p2Shared';

// Local shape for shot_plan entries passed in from Phase 1 (avoids importing
// Phase 1's private ShotPlanItemT type — duplicated minimally here).
interface ShotPlanLikeT {
  id: string;
  label: string;
}

export function ProcessPanel({
  characterId,
  studioProjectId,
  shotPlan,
  costumes,
  emotions,
  processed,
  engine,
  hasBase,
  onProcessed,
}: {
  characterId: string;
  studioProjectId: string | null | undefined;
  shotPlan: ShotPlanLikeT[];
  costumes: Record<string, CostumeT> | undefined;
  emotions: Record<string, EmotionEntryT> | undefined;
  processed: Record<string, ProcessedEntryT> | undefined;
  engine: EngineT;
  hasBase: boolean;
  onProcessed: () => void;
}) {
  const [selectedRefs, setSelectedRefs] = useState<Set<string>>(new Set());
  const [lightboxUrl, setLightboxUrl] = useState<string | null>(null);
  const [cutout, setCutout] = useState(true);
  const [upscale, setUpscale] = useState(false);
  const [upscaleMode, setUpscaleMode] = useState<UpscaleModeT>('auto');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastErrors, setLastErrors] = useState<string[]>([]);

  const refOptions: { ref: string; label: string }[] = [
    { ref: 'base', label: 'Base render' },
    ...shotPlan.map((s) => ({ ref: s.id, label: s.label })),
    ...Object.values(costumes || {}).map((c) => ({ ref: `costume:${c.id}`, label: `Costume: ${c.name}` })),
    ...Object.keys(emotions || {}).map((key) => ({ ref: `emotion:${key}`, label: `Emotion: ${key}` })),
  ];

  const toggle = (ref: string) => {
    setSelectedRefs((prev) => {
      const n = new Set(prev);
      if (n.has(ref)) n.delete(ref);
      else n.add(ref);
      return n;
    });
  };

  const run = async () => {
    if (!selectedRefs.size) return;
    if (!cutout && !upscale) {
      setError('Select at least one step (cutout or upscale).');
      return;
    }
    setBusy(true);
    setError(null);
    setLastErrors([]);
    try {
      const res = await p2Api.processImages(characterId, {
        image_refs: Array.from(selectedRefs),
        steps: { cutout, upscale },
        upscale_mode: upscale ? upscaleMode : undefined,
        engine,
      });
      if (res?.errors?.length) setLastErrors(res.errors);
      onProcessed();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 flex flex-col gap-3 mt-4">
      <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">Process (cutout / upscale)</h3>

      {!hasBase && (
        <div className="text-sm text-amber-300 bg-amber-950/20 border border-amber-900/40 rounded-md px-3 py-2">
          Generate a base render first (Sheet tab) before processing images.
        </div>
      )}

      <div className="flex flex-wrap gap-1.5">
        {refOptions.map((o) => {
          const isSelected = selectedRefs.has(o.ref);
          const entry = processed?.[o.ref];
          return (
            <button
              key={o.ref}
              onClick={() => toggle(o.ref)}
              title={o.ref}
              className={`px-2.5 py-1 rounded-full text-xs font-medium border transition-colors flex items-center gap-1 ${
                isSelected
                  ? 'bg-purple-900/50 border-purple-600 text-purple-200'
                  : 'bg-gray-800 border-gray-700 text-gray-300 hover:border-gray-600'
              }`}
            >
              {o.label}
              {entry?.cutout && <StatusChip status={entry.cutout.status} />}
              {entry?.upscale && <StatusChip status={entry.upscale.status} />}
            </button>
          );
        })}
      </div>

      <div className="flex flex-wrap items-center gap-4">
        <label className="flex items-center gap-2 text-sm text-gray-300">
          <input type="checkbox" checked={cutout} onChange={(e) => setCutout(e.target.checked)} />
          <Scissors size={13} /> Cutout
        </label>
        <label className="flex items-center gap-2 text-sm text-gray-300">
          <input type="checkbox" checked={upscale} onChange={(e) => setUpscale(e.target.checked)} />
          <ZoomIn size={13} /> Upscale
        </label>
        {upscale && (
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

        <button
          onClick={run}
          disabled={busy || !selectedRefs.size || !hasBase}
          className="ml-auto px-3 py-1.5 bg-indigo-700 hover:bg-indigo-600 disabled:opacity-50 rounded-md text-sm font-medium flex items-center gap-2"
        >
          {busy && <Spinner size={13} />}
          Process Selected ({selectedRefs.size})
        </button>
      </div>

      <ErrorText msg={error} />
      {!!lastErrors.length && (
        <div className="flex flex-col gap-1">
          {lastErrors.map((e, i) => (
            <ErrorText key={i} msg={e} />
          ))}
        </div>
      )}

      {/* Show processed results with thumbnails when available */}
      {!!processed && Object.keys(processed).length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3 mt-2">
          {Object.entries(processed).map(([ref, steps]) =>
            Object.entries(steps).map(([stepName, res]) => {
              const url = res.asset_id ? assetUrl(studioProjectId, res.asset_id) : null;
              return (
                <div key={`${ref}:${stepName}`} className="bg-gray-800/60 border border-gray-700 rounded-lg p-2 flex flex-col gap-1.5">
                  <div className="aspect-square bg-gray-800 border border-gray-700 rounded overflow-hidden flex items-center justify-center">
                    {url ? (
                      <img
                        src={url}
                        alt={`${ref} ${stepName}`}
                        onClick={() => setLightboxUrl(url)}
                        title="Click to enlarge"
                        className="w-full h-full object-cover cursor-pointer"
                      />
                    ) : (
                      <span className="text-gray-600 text-xs">—</span>
                    )}
                  </div>
                  <div className="text-[10px] text-gray-400 truncate" title={`${ref} · ${stepName}`}>
                    {ref} · {stepName}
                  </div>
                  <span title={res.error || res.note || ''}>
                    <StatusChip status={res.status} />
                  </span>
                </div>
              );
            })
          )}
        </div>
      )}

      {lightboxUrl && <ImageLightbox url={lightboxUrl} onClose={() => setLightboxUrl(null)} />}
    </div>
  );
}
