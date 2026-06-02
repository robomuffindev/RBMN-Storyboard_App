/**
 * ChapterScopeBanner — the header that appears at the top of the
 * project view when the user has drilled into a chapter.
 *
 * Shows:
 *   - Color swatch + chapter name + shortcode
 *   - prev / next chapter navigation
 *   - "Back to project" link
 *   - Editable description (with "Generate with LLM" button)
 *   - Character focus chips
 *   - Style notes inline editor
 *   - Scene count / time range
 *
 * Edits persist via PATCH /api/projects/:pid/chapters/:cid.
 * "Generate with LLM" calls POST /generate-description.
 *
 * Designed to be the always-visible context inside the chapter view,
 * so the user knows what they're working in and why.
 */
import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import type { ChapterTreeNode } from '../../types';
import { generateChapterDescription, updateChapter } from '@/api/client';
import { useAppStore } from '@/store';

interface ChapterScopeBannerProps {
  projectId: string;
  projectName?: string;
  chapter: ChapterTreeNode;
  /** Flat ordered list of all chapters in playback order (for prev/next nav). */
  flatChapters: ChapterTreeNode[];
  /** Called after any successful edit so the parent can refetch the tree. */
  onChange?: () => void;
}

export default function ChapterScopeBanner({
  projectId,
  projectName = 'Project',
  chapter,
  flatChapters,
  onChange,
}: ChapterScopeBannerProps) {
  const navigate = useNavigate();
  const [description, setDescription] = useState(chapter.description || '');
  const [styleNotes, setStyleNotes] = useState(chapter.style_notes || '');
  const [charFocusInput, setCharFocusInput] = useState('');
  const [characterFocus, setCharacterFocus] = useState<string[]>(chapter.character_focus || []);
  const [editing, setEditing] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Re-hydrate when the underlying chapter changes
  useEffect(() => {
    setDescription(chapter.description || '');
    setStyleNotes(chapter.style_notes || '');
    setCharacterFocus(chapter.character_focus || []);
    setEditing(false);
  }, [chapter.id]);

  const idx = flatChapters.findIndex(c => c.id === chapter.id);
  const prev = idx > 0 ? flatChapters[idx - 1] : null;
  const next = idx >= 0 && idx < flatChapters.length - 1 ? flatChapters[idx + 1] : null;

  const handleSave = async () => {
    setSaveError(null);
    try {
      await updateChapter(projectId, chapter.id, {
        description,
        style_notes: styleNotes,
        character_focus: characterFocus,
      });
      setEditing(false);
      onChange?.();
    } catch (e: any) {
      setSaveError(e?.response?.data?.detail || e?.message || String(e));
    }
  };

  const handleGenerate = async () => {
    setGenerating(true);
    setSaveError(null);
    try {
      const { data } = await generateChapterDescription(projectId, chapter.id, true);
      setDescription((data as any).description || '');
      setStyleNotes((data as any).style_notes || '');
      setCharacterFocus((data as any).character_focus || []);
      onChange?.();
    } catch (e: any) {
      setSaveError(e?.response?.data?.detail || e?.message || String(e));
    } finally {
      setGenerating(false);
    }
  };

  const addCharacter = () => {
    const v = charFocusInput.trim();
    if (v && !characterFocus.includes(v)) {
      setCharacterFocus([...characterFocus, v]);
    }
    setCharFocusInput('');
  };

  const removeCharacter = (name: string) => {
    setCharacterFocus(characterFocus.filter(c => c !== name));
  };

  return (
    <div
      className="border-b border-gray-800 bg-gray-900/50 px-3 py-2"
      style={{ borderLeftWidth: 4, borderLeftColor: chapter.color }}
    >
      {/* Top row: breadcrumb + prev/next + edit toggle */}
      <div className="flex items-center justify-between gap-2 mb-1">
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <Link
            to={`/project/${projectId}`}
            className="text-xs text-purple-300 hover:text-purple-200 flex-shrink-0"
            title="Back to full project"
          >
            ← {projectName}
          </Link>
          <span className="text-gray-600">/</span>
          <span
            className="w-2.5 h-2.5 rounded-sm flex-shrink-0"
            style={{ backgroundColor: chapter.color }}
          />
          <span className="font-semibold text-gray-100 truncate">{chapter.name}</span>
          <span className="text-[10px] font-mono text-gray-500 flex-shrink-0">
            {chapter.short_code}
          </span>
          <span className="text-[10px] text-gray-500 flex-shrink-0">
            · {chapter.scene_count ?? 0} scenes
          </span>
          <span className="text-[10px] text-gray-500 flex-shrink-0">
            · {chapter.start_time.toFixed(1)}s–{chapter.end_time.toFixed(1)}s
          </span>
        </div>
        <div className="flex items-center gap-1 flex-shrink-0">
          <button
            type="button"
            onClick={() => prev && navigate(`/project/${projectId}/c/${prev.short_code}`)}
            disabled={!prev}
            title={prev ? `Previous: ${prev.name}` : 'Already first chapter'}
            className="text-xs px-2 py-0.5 rounded bg-gray-800 hover:bg-gray-700 disabled:opacity-30"
          >
            ← Prev
          </button>
          <button
            type="button"
            onClick={() => next && navigate(`/project/${projectId}/c/${next.short_code}`)}
            disabled={!next}
            title={next ? `Next: ${next.name}` : 'Already last chapter'}
            className="text-xs px-2 py-0.5 rounded bg-gray-800 hover:bg-gray-700 disabled:opacity-30"
          >
            Next →
          </button>
          <button
            type="button"
            onClick={() => setEditing(v => !v)}
            className="text-xs px-2 py-0.5 rounded bg-gray-800 hover:bg-gray-700"
          >
            {editing ? 'Done' : 'Edit'}
          </button>
          {/* Chapter-scoped Auto Gen — opens the same Auto-Gen modal the
              header button opens, but because chapterScope is set in
              Zustand the kickoff payload includes chapter_id so the
              backend only processes this chapter's scenes. */}
          <button
            type="button"
            onClick={() => useAppStore.getState().setAutoGenOpen(true)}
            title={`Run Auto Gen on this chapter only (${chapter.scene_count ?? 0} scenes)`}
            className="text-xs px-2 py-0.5 rounded bg-purple-600 hover:bg-purple-700 text-white"
          >
            ✨ Auto Gen this chapter
          </button>
        </div>
      </div>

      {/* Description (read-only by default, editable on click "Edit") */}
      {editing ? (
        <div className="space-y-1.5 mt-1">
          <div className="flex items-start gap-2">
            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="Chapter description — what happens in this chapter? Drives scene prompt generation."
              className="flex-1 bg-gray-800 border border-gray-700 rounded text-xs p-1.5 resize-y min-h-[3rem]"
              rows={3}
            />
            <button
              type="button"
              onClick={handleGenerate}
              disabled={generating}
              title="Use the LLM to summarize this chapter's narration into a description + character cast + style notes"
              className="text-xs px-2 py-1 rounded bg-purple-600 hover:bg-purple-700 disabled:opacity-50 whitespace-nowrap"
            >
              {generating ? 'Generating…' : '✨ Generate'}
            </button>
          </div>
          <div className="flex flex-wrap gap-1 items-center">
            <span className="text-[10px] text-gray-500 uppercase tracking-wide mr-1">Characters:</span>
            {characterFocus.map(c => (
              <span
                key={c}
                className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-purple-700/40 text-purple-100"
              >
                {c}
                <button
                  type="button"
                  onClick={() => removeCharacter(c)}
                  className="text-purple-200 hover:text-white"
                >
                  ×
                </button>
              </span>
            ))}
            <input
              type="text"
              value={charFocusInput}
              onChange={e => setCharFocusInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addCharacter(); } }}
              placeholder="+ add"
              className="bg-gray-800 border border-gray-700 rounded text-[10px] px-1.5 py-0.5 w-24"
            />
          </div>
          <input
            type="text"
            value={styleNotes}
            onChange={e => setStyleNotes(e.target.value)}
            placeholder="Style notes — visual tone / mood for this chapter"
            className="w-full bg-gray-800 border border-gray-700 rounded text-xs p-1.5"
          />
          {saveError && (
            <div className="text-[11px] text-red-300 bg-red-900/30 border border-red-700/40 rounded px-2 py-1">
              {saveError}
            </div>
          )}
          <div className="flex justify-end gap-1">
            <button
              type="button"
              onClick={() => setEditing(false)}
              className="text-xs px-2 py-0.5 rounded bg-gray-800 hover:bg-gray-700"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleSave}
              className="text-xs px-2 py-0.5 rounded bg-purple-600 hover:bg-purple-700"
            >
              Save
            </button>
          </div>
        </div>
      ) : (
        <div className="mt-1">
          {description ? (
            <p className="text-xs text-gray-300 leading-snug whitespace-pre-wrap">{description}</p>
          ) : (
            <p className="text-xs text-gray-500 italic">
              No description yet. Click <span className="text-purple-300">Edit</span> to write one, or use <span className="text-purple-300">Generate</span> to let the LLM read this chapter's narration and propose one.
            </p>
          )}
          {(characterFocus.length > 0 || styleNotes) && (
            <div className="mt-1 flex flex-wrap gap-2 items-center text-[10px] text-gray-500">
              {characterFocus.length > 0 && (
                <span>
                  <span className="uppercase tracking-wide mr-1">Cast:</span>
                  {characterFocus.map(c => (
                    <span key={c} className="ml-1 inline-block px-1.5 py-0 rounded bg-purple-900/40 text-purple-200">
                      {c}
                    </span>
                  ))}
                </span>
              )}
              {styleNotes && (
                <span className="italic">
                  <span className="uppercase not-italic tracking-wide mr-1">Style:</span>
                  {styleNotes}
                </span>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
