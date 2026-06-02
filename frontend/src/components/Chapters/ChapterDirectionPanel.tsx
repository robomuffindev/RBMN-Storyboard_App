/**
 * ChapterDirectionPanel — the rich Chapters tab content.
 *
 * Replaces the plain ChapterTree on the Chapters tab.  Every chapter
 * gets its own card with:
 *   - color stripe + name + shortcode + scene count
 *   - inline editable description (textarea)
 *   - ✨ Generate description (LLM call, single chapter)
 *   - 🎬 Generate Story Flow (per-scene flow ideas, scoped to this chapter)
 *   - "Open chapter" link → drilldown view
 *
 * Plus two top-level batch buttons:
 *   - "Generate ALL chapter descriptions" — calls /generate-description
 *     for every chapter sequentially with progress
 *   - "Re-parse chapters" (existing)
 */
import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import type { ChapterTreeNode } from '../../types';
import {
  generateChapterDescription,
  generateVideoFlow,
  updateChapter,
} from '@/api/client';

interface ChapterDirectionPanelProps {
  projectId: string;
  chapters: ChapterTreeNode[];
  /** Called after any save/generate so the parent can refetch the tree. */
  onChange?: () => void;
  /** Optional: triggered by parent's Re-parse button. */
  onReparse?: (forceAuto: boolean) => Promise<void>;
  reparseBusy?: boolean;
}

function flattenInOrder(chapters: ChapterTreeNode[]): ChapterTreeNode[] {
  const out: ChapterTreeNode[] = [];
  const walk = (nodes: ChapterTreeNode[]) => {
    for (const n of nodes) {
      out.push(n);
      if (n.children?.length) walk(n.children);
    }
  };
  walk(chapters);
  out.sort((a, b) => a.start_time - b.start_time);
  return out;
}

export default function ChapterDirectionPanel({
  projectId,
  chapters,
  onChange,
  onReparse,
  reparseBusy = false,
}: ChapterDirectionPanelProps) {
  const flat = useMemo(() => flattenInOrder(chapters), [chapters]);
  const [batchRunning, setBatchRunning] = useState(false);
  const [batchProgress, setBatchProgress] = useState<{ done: number; total: number; current: string } | null>(null);
  const [batchError, setBatchError] = useState<string | null>(null);

  // ── Generate ALL chapter descriptions (sequential, with progress) ──
  const handleGenerateAll = async () => {
    if (batchRunning || flat.length === 0) return;
    if (!window.confirm(
      `This will use the LLM to generate descriptions for all ${flat.length} chapters. ` +
      `Existing descriptions will be overwritten.\n\nContinue?`,
    )) return;
    setBatchRunning(true);
    setBatchError(null);
    setBatchProgress({ done: 0, total: flat.length, current: '' });
    for (let i = 0; i < flat.length; i++) {
      const ch = flat[i];
      setBatchProgress({ done: i, total: flat.length, current: ch.name });
      try {
        await generateChapterDescription(projectId, ch.id, true);
      } catch (e: any) {
        const msg = e?.response?.data?.detail || e?.message || String(e);
        setBatchError(`${ch.short_code}: ${msg}`);
        // Continue with next chapter rather than aborting
      }
    }
    setBatchProgress({ done: flat.length, total: flat.length, current: '' });
    setBatchRunning(false);
    onChange?.();
  };

  return (
    <div className="h-full flex flex-col overflow-y-auto">
      {/* Top toolbar */}
      <div className="sticky top-0 z-10 flex items-center justify-between gap-2 px-2 py-2 bg-gray-900/95 border-b border-gray-800">
        <div className="text-xs text-gray-400 font-medium">
          Chapters
          <span className="ml-2 text-gray-500">({flat.length})</span>
        </div>
        <div className="flex items-center gap-1 flex-wrap">
          <button
            type="button"
            onClick={handleGenerateAll}
            disabled={batchRunning || flat.length === 0}
            title="Use the LLM to generate a description for every chapter (sequential, takes a few seconds per chapter)"
            className="text-[10px] px-2 py-0.5 rounded bg-purple-600 hover:bg-purple-700 disabled:opacity-50 text-white"
          >
            {batchRunning ? `Generating ${(batchProgress?.done ?? 0)}/${batchProgress?.total ?? 0}…` : '✨ Generate ALL'}
          </button>
          {onReparse && (
            <button
              type="button"
              onClick={() => onReparse(false)}
              disabled={reparseBusy}
              title="Re-derive chapters from the script (preserves manual rename / color / description)"
              className="text-[10px] px-2 py-0.5 rounded bg-gray-700/60 hover:bg-gray-700 disabled:opacity-50"
            >
              {reparseBusy ? 'Re-parsing…' : 'Re-parse'}
            </button>
          )}
        </div>
      </div>

      {batchProgress && batchRunning && (
        <div className="mx-2 mt-2 text-[11px] text-purple-200 bg-purple-900/30 border border-purple-700/40 rounded px-2 py-1.5">
          Generating: <span className="font-medium">{batchProgress.current}</span>
          <div className="mt-1 h-1 bg-gray-800 rounded overflow-hidden">
            <div
              className="h-full bg-purple-500 transition-all"
              style={{ width: `${(batchProgress.done / Math.max(batchProgress.total, 1)) * 100}%` }}
            />
          </div>
        </div>
      )}
      {batchError && (
        <div className="mx-2 mt-2 text-[11px] text-red-300 bg-red-900/30 border border-red-700/40 rounded px-2 py-1.5">
          Last error: {batchError}
        </div>
      )}

      {flat.length === 0 ? (
        <div className="text-xs text-gray-500 italic px-3 py-6">
          No chapters yet. Click "Re-parse" or add <code>#</code> headers to your script.
        </div>
      ) : (
        <div className="flex flex-col gap-2 p-2">
          {flat.map((ch) => (
            <ChapterCard
              key={ch.id}
              chapter={ch}
              projectId={projectId}
              onChange={onChange}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/** Per-chapter card with description + generate + flow buttons. */
function ChapterCard({
  chapter,
  projectId,
  onChange,
}: {
  chapter: ChapterTreeNode;
  projectId: string;
  onChange?: () => void;
}) {
  const [description, setDescription] = useState(chapter.description || '');
  const [dirty, setDirty] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [generatingFlow, setGeneratingFlow] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);

  // Re-hydrate when the underlying chapter changes from the server
  useEffect(() => {
    setDescription(chapter.description || '');
    setDirty(false);
  }, [chapter.id, chapter.description]);

  const handleSave = async () => {
    setError(null);
    setOkMsg(null);
    try {
      await updateChapter(projectId, chapter.id, { description });
      setDirty(false);
      onChange?.();
      setOkMsg('Saved');
      setTimeout(() => setOkMsg(null), 1500);
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || String(e));
    }
  };

  const handleGenerate = async () => {
    setGenerating(true);
    setError(null);
    setOkMsg(null);
    try {
      const { data } = await generateChapterDescription(projectId, chapter.id, true);
      setDescription((data as any).description || '');
      setDirty(false);
      onChange?.();
      setOkMsg('Generated');
      setTimeout(() => setOkMsg(null), 1500);
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || String(e));
    } finally {
      setGenerating(false);
    }
  };

  const handleGenerateFlow = async () => {
    setGeneratingFlow(true);
    setError(null);
    setOkMsg(null);
    try {
      const { data } = await generateVideoFlow(projectId, chapter.id);
      const filled = (data?.ideas || []).filter((x: any) => x?.flow_idea?.trim()).length;
      setOkMsg(`Story flow generated for ${filled} scene(s)`);
      setTimeout(() => setOkMsg(null), 2500);
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || String(e));
    } finally {
      setGeneratingFlow(false);
    }
  };

  return (
    <div
      className="rounded-lg border border-gray-800 bg-gray-900/50 overflow-hidden"
      style={{ borderLeftWidth: 4, borderLeftColor: chapter.color }}
    >
      <div className="flex items-center justify-between gap-2 px-3 py-2 border-b border-gray-800">
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <Link
            to={`/project/${projectId}/c/${chapter.short_code}`}
            className="text-sm font-medium text-gray-100 hover:text-purple-300 truncate"
            title={`Open chapter ${chapter.name}`}
          >
            {chapter.name}
          </Link>
          <span className="text-[10px] font-mono text-gray-500 flex-shrink-0">
            {chapter.short_code}
          </span>
          <span className="text-[10px] text-gray-500 flex-shrink-0">
            · {chapter.scene_count ?? 0} scenes · {chapter.start_time.toFixed(0)}s–{chapter.end_time.toFixed(0)}s
          </span>
        </div>
        <Link
          to={`/project/${projectId}/c/${chapter.short_code}`}
          className="text-[10px] px-2 py-0.5 rounded bg-gray-800 hover:bg-gray-700 text-gray-200 flex-shrink-0"
        >
          Open →
        </Link>
      </div>

      <div className="px-3 py-2 space-y-2">
        <textarea
          value={description}
          onChange={(e) => { setDescription(e.target.value); setDirty(true); }}
          placeholder="Chapter concept — what happens here?  Drives scene prompt generation."
          rows={3}
          className="w-full bg-gray-800/60 border border-gray-700 rounded text-xs p-2 resize-y min-h-[3.5rem]"
        />
        {chapter.character_focus && chapter.character_focus.length > 0 && (
          <div className="flex flex-wrap gap-1 text-[10px]">
            <span className="text-gray-500 uppercase tracking-wide mr-1">Cast:</span>
            {chapter.character_focus.map((c: string) => (
              <span key={c} className="px-1.5 py-0.5 rounded bg-purple-900/40 text-purple-200">
                {c}
              </span>
            ))}
          </div>
        )}
        {chapter.style_notes && (
          <div className="text-[10px] text-gray-400 italic">
            <span className="uppercase not-italic tracking-wide mr-1">Style:</span>
            {chapter.style_notes}
          </div>
        )}

        {error && (
          <div className="text-[11px] text-red-300 bg-red-900/30 border border-red-700/40 rounded px-2 py-1">
            {error}
          </div>
        )}
        {okMsg && (
          <div className="text-[11px] text-emerald-300 bg-emerald-900/30 border border-emerald-700/40 rounded px-2 py-1">
            {okMsg}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-1">
          <button
            type="button"
            onClick={handleGenerate}
            disabled={generating}
            title="Use the LLM to summarize this chapter's narration into a description + character cast + style notes"
            className="text-[10px] px-2 py-0.5 rounded bg-purple-600 hover:bg-purple-700 disabled:opacity-50 text-white"
          >
            {generating ? 'Generating…' : '✨ Generate description'}
          </button>
          <button
            type="button"
            onClick={handleGenerateFlow}
            disabled={generatingFlow}
            title="Use the LLM to generate per-scene Story Flow ideas for every scene in THIS chapter only (uses chapter description as creative direction)"
            className="text-[10px] px-2 py-0.5 rounded bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white"
          >
            {generatingFlow ? 'Generating flow…' : '🎬 Generate Story Flow'}
          </button>
          {dirty && (
            <button
              type="button"
              onClick={handleSave}
              className="text-[10px] px-2 py-0.5 rounded bg-emerald-600 hover:bg-emerald-700 text-white"
            >
              Save
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
