/**
 * Character Studio P2 — tiny shared UI primitives reused across the new
 * Poses/Costumes/Emotions/Process/GenerateAll/Wizard components.
 *
 * These intentionally mirror the private helpers defined inline in
 * CharacterStudioPage.tsx (Spinner / ErrorText / StatusChip) so the P2 tabs
 * look identical to Phase 1, without modifying the Phase 1 file to export
 * them.
 */
import { Loader2, AlertTriangle } from 'lucide-react';

/**
 * Full-screen image lightbox reused across the studio tabs.
 *
 * v1.276.0 — this used to be a flat "big picture on a dark background": no
 * zoom, no pan, so you could see an image larger but not actually REVIEW it.
 * It now re-exports the shared viewer, which has cursor-anchored zoom, drag
 * pan, arrow-key gallery stepping and a download button. Every consumer of
 * this symbol (the poses / costumes / emotions / process / renders / dataset /
 * Klein 3.0 / pose-library tabs) gets that for free, with no call-site change:
 * the shared component accepts `url` as an alias for `src` precisely so this
 * swap could be a one-line re-export instead of a nine-file rename.
 */
export { default as ImageLightbox } from '../shared/ImageLightbox';

export function Spinner({ size = 16 }: { size?: number }) {
  return <Loader2 size={size} className="animate-spin text-purple-400" />;
}

export function ErrorText({ msg }: { msg: string | null | undefined }) {
  if (!msg) return null;
  return (
    <div className="flex items-start gap-2 text-sm text-red-400 bg-red-950/30 border border-red-900/50 rounded-md px-3 py-2">
      <AlertTriangle size={16} className="shrink-0 mt-0.5" />
      <span>{msg}</span>
    </div>
  );
}

const STATUS_CLASS_MAP: Record<string, string> = {
  pending: 'bg-gray-700 text-gray-300',
  running: 'bg-indigo-900/60 text-indigo-300',
  done: 'bg-emerald-900/60 text-emerald-300',
  ready: 'bg-emerald-900/60 text-emerald-300',
  failed: 'bg-red-900/60 text-red-300',
  error: 'bg-red-900/60 text-red-300',
  new: 'bg-gray-700 text-gray-300',
  captioning: 'bg-indigo-900/60 text-indigo-300',
  exported: 'bg-purple-900/60 text-purple-300',
};

export function StatusChip({ status }: { status: string | null | undefined }) {
  const s = status || 'pending';
  const cls = STATUS_CLASS_MAP[s] || 'bg-gray-700 text-gray-300';
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}>
      {(s === 'running' || s === 'captioning') && <Spinner size={11} />}
      {s}
    </span>
  );
}

export function assetUrl(
  studioProjectId: string | null | undefined,
  assetId: string | null | undefined
): string | null {
  if (!studioProjectId || !assetId) return null;
  return `/api/projects/${studioProjectId}/assets/${assetId}/file`;
}
