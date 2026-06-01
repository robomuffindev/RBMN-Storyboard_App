/**
 * ChapterPicker — scope selector used in the Export modal.
 *
 *   Scope:
 *     ○ Entire video (default)
 *     ○ Single chapter   [dropdown of chapters]
 *     ○ Multiple chapters
 *        [+ Add chapter]
 *        [Chapter 1] [×]
 *        [Chapter 3] [×]
 *
 * Flattens nested chapters so sub-chapters are individually selectable.
 * Sorted by start_time so the dropdown lists them in playback order.
 */
import { useMemo, useState } from 'react';
import type { ChapterSelection, ChapterTreeNode } from '../../types';

interface ChapterPickerProps {
  /** Tree of chapters (with children). */
  chapters: ChapterTreeNode[];
  /** Current selection — controlled. */
  value: ChapterSelection;
  /** Called whenever the selection changes. */
  onChange: (next: ChapterSelection) => void;
}

interface FlatChapter {
  id: string;
  name: string;
  short_code: string;
  depth: number;
  start_time: number;
}

function flatten(chapters: ChapterTreeNode[]): FlatChapter[] {
  const out: FlatChapter[] = [];
  const walk = (nodes: ChapterTreeNode[]) => {
    for (const n of nodes) {
      out.push({
        id: n.id,
        name: n.name,
        short_code: n.short_code,
        depth: n.depth,
        start_time: n.start_time,
      });
      if (n.children?.length) walk(n.children);
    }
  };
  walk(chapters);
  out.sort((a, b) => a.start_time - b.start_time);
  return out;
}

export default function ChapterPicker({ chapters, value, onChange }: ChapterPickerProps) {
  const flat = useMemo(() => flatten(chapters), [chapters]);
  const byId = useMemo(() => {
    const m = new Map<string, FlatChapter>();
    for (const c of flat) m.set(c.id, c);
    return m;
  }, [flat]);

  const [picking, setPicking] = useState(false);

  const setMode = (mode: ChapterSelection['mode']) => {
    if (mode === 'all') {
      onChange({ mode: 'all', chapter_ids: [] });
    } else if (mode === 'single') {
      const first = flat[0]?.id ? [flat[0].id] : [];
      onChange({ mode: 'single', chapter_ids: first });
    } else {
      onChange({ mode: 'multiple', chapter_ids: value.chapter_ids });
    }
  };

  const removeChapter = (id: string) => {
    onChange({
      ...value,
      chapter_ids: value.chapter_ids.filter((x) => x !== id),
    });
  };

  const addChapter = (id: string) => {
    if (!value.chapter_ids.includes(id)) {
      onChange({ ...value, chapter_ids: [...value.chapter_ids, id] });
    }
    setPicking(false);
  };

  return (
    <div className="bg-gray-800/40 border border-gray-700/60 rounded-lg p-3 space-y-2.5">
      <div className="text-sm font-medium text-gray-300">Scope</div>

      <label className="flex items-start gap-2 cursor-pointer">
        <input
          type="radio"
          checked={value.mode === 'all'}
          onChange={() => setMode('all')}
          className="w-4 h-4 mt-0.5 accent-purple-500"
        />
        <div className="flex-1">
          <div className="text-sm text-gray-200">Entire video</div>
          <div className="text-xs text-gray-500">Default — render every scene.</div>
        </div>
      </label>

      <label className="flex items-start gap-2 cursor-pointer">
        <input
          type="radio"
          checked={value.mode === 'single'}
          onChange={() => setMode('single')}
          className="w-4 h-4 mt-0.5 accent-purple-500"
        />
        <div className="flex-1">
          <div className="text-sm text-gray-200">Single chapter</div>
          {value.mode === 'single' && (
            <select
              className="mt-1 bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs text-gray-200 w-full"
              value={value.chapter_ids[0] ?? ''}
              onChange={(e) => onChange({ mode: 'single', chapter_ids: [e.target.value] })}
            >
              {flat.map((c) => (
                <option key={c.id} value={c.id}>
                  {' '.repeat(c.depth * 2)}{c.name} · {c.short_code}
                </option>
              ))}
            </select>
          )}
        </div>
      </label>

      <label className="flex items-start gap-2 cursor-pointer">
        <input
          type="radio"
          checked={value.mode === 'multiple'}
          onChange={() => setMode('multiple')}
          className="w-4 h-4 mt-0.5 accent-purple-500"
        />
        <div className="flex-1">
          <div className="text-sm text-gray-200">Multiple chapters</div>
          {value.mode === 'multiple' && (
            <div className="mt-1 space-y-1">
              <div className="flex flex-wrap gap-1">
                {value.chapter_ids.map((cid) => {
                  const ch = byId.get(cid);
                  if (!ch) return null;
                  return (
                    <span
                      key={cid}
                      className="inline-flex items-center gap-1 bg-purple-700/60 text-xs px-2 py-0.5 rounded"
                    >
                      {ch.name}
                      <button
                        type="button"
                        onClick={() => removeChapter(cid)}
                        className="text-purple-200 hover:text-white"
                      >
                        ×
                      </button>
                    </span>
                  );
                })}
              </div>
              {!picking ? (
                <button
                  type="button"
                  onClick={() => setPicking(true)}
                  className="text-xs text-purple-300 hover:text-purple-200"
                >
                  + Add chapter
                </button>
              ) : (
                <select
                  autoFocus
                  className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs text-gray-200 w-full"
                  defaultValue=""
                  onChange={(e) => e.target.value && addChapter(e.target.value)}
                  onBlur={() => setPicking(false)}
                >
                  <option value="" disabled>Pick a chapter…</option>
                  {flat
                    .filter((c) => !value.chapter_ids.includes(c.id))
                    .map((c) => (
                      <option key={c.id} value={c.id}>
                        {' '.repeat(c.depth * 2)}{c.name} · {c.short_code}
                      </option>
                    ))}
                </select>
              )}
              <div className="text-[10px] text-gray-500">
                Chapters render in timeline order regardless of selection order.
              </div>
            </div>
          )}
        </div>
      </label>
    </div>
  );
}
