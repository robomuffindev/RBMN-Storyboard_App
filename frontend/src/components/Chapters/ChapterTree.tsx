/**
 * ChapterTree — sidebar tree of chapters with click-to-drill.
 *
 * Renders nested chapters with expand/collapse, scene counts, and color
 * dots.  The active chapter (when the user is drilled in) is highlighted.
 *
 * Each node also exposes a small "..." menu for split / merge / rename.
 * For Phase 1 we only wire RENAME via a single-line input; split / merge
 * are stubs that fire `onAction` so the parent can show a modal.
 */
import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { ChapterTreeNode } from '../../types';

interface ChapterTreeProps {
  projectId: string;
  chapters: ChapterTreeNode[];
  activeChapterShortCode?: string | null;
  onAction?: (chapter: ChapterTreeNode, action: 'rename' | 'split' | 'merge' | 'recolor') => void;
}

function ChapterRow({
  ch,
  projectId,
  activeShortCode,
  onAction,
  depth = 0,
}: {
  ch: ChapterTreeNode;
  projectId: string;
  activeShortCode?: string | null;
  onAction?: ChapterTreeProps['onAction'];
  depth?: number;
}) {
  const [open, setOpen] = useState(true);
  const [menuOpen, setMenuOpen] = useState(false);
  const hasChildren = (ch.children?.length ?? 0) > 0;
  const isActive = ch.short_code === activeShortCode;

  return (
    <div className="text-sm">
      <div
        className={`flex items-center gap-1 px-1 py-1 rounded group hover:bg-gray-800/60 ${
          isActive ? 'bg-purple-900/40' : ''
        }`}
        style={{ paddingLeft: `${depth * 12 + 4}px` }}
      >
        {hasChildren ? (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="w-4 h-4 flex items-center justify-center text-gray-500 hover:text-gray-300"
          >
            <svg
              className={`w-3 h-3 transition-transform ${open ? 'rotate-90' : ''}`}
              fill="none" stroke="currentColor" viewBox="0 0 24 24"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
          </button>
        ) : (
          <span className="w-4 h-4" />
        )}
        <span
          className="w-2.5 h-2.5 rounded-sm flex-shrink-0"
          style={{ backgroundColor: ch.color }}
          title={ch.short_code}
        />
        <Link
          to={`/project/${projectId}/c/${ch.short_code}`}
          className={`flex-1 truncate ${isActive ? 'text-purple-200 font-medium' : 'text-gray-200'} hover:text-purple-300`}
          title={`${ch.name} (${ch.short_code})`}
        >
          {ch.name}
        </Link>
        <span className="text-[10px] text-gray-500 px-1">{ch.scene_count ?? 0}</span>
        {onAction && (
          <div className="relative">
            <button
              type="button"
              onClick={(e) => { e.preventDefault(); setMenuOpen((v) => !v); }}
              className="w-5 h-5 flex items-center justify-center text-gray-500 hover:text-gray-300 opacity-0 group-hover:opacity-100"
              aria-label="Chapter actions"
            >
              ⋯
            </button>
            {menuOpen && (
              <div
                className="absolute right-0 top-full mt-1 min-w-[140px] bg-gray-900 border border-gray-700 rounded shadow-lg z-50"
                onMouseLeave={() => setMenuOpen(false)}
              >
                <button
                  type="button"
                  onClick={() => { setMenuOpen(false); onAction(ch, 'rename'); }}
                  className="block w-full text-left px-3 py-1.5 text-xs hover:bg-gray-800"
                >
                  Rename
                </button>
                <button
                  type="button"
                  onClick={() => { setMenuOpen(false); onAction(ch, 'recolor'); }}
                  className="block w-full text-left px-3 py-1.5 text-xs hover:bg-gray-800"
                >
                  Recolor
                </button>
                <button
                  type="button"
                  onClick={() => { setMenuOpen(false); onAction(ch, 'split'); }}
                  className="block w-full text-left px-3 py-1.5 text-xs hover:bg-gray-800"
                >
                  Split…
                </button>
                <button
                  type="button"
                  onClick={() => { setMenuOpen(false); onAction(ch, 'merge'); }}
                  className="block w-full text-left px-3 py-1.5 text-xs hover:bg-gray-800"
                >
                  Merge with next
                </button>
              </div>
            )}
          </div>
        )}
      </div>
      {open && hasChildren && (
        <div>
          {ch.children!.map((child) => (
            <ChapterRow
              key={child.id}
              ch={child}
              projectId={projectId}
              activeShortCode={activeShortCode}
              onAction={onAction}
              depth={depth + 1}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default function ChapterTree({
  projectId,
  chapters,
  activeChapterShortCode,
  onAction,
}: ChapterTreeProps) {
  if (!chapters.length) {
    return (
      <div className="text-xs text-gray-500 italic px-2 py-3">
        No chapters yet. Click "Re-parse" or add <code>#</code> headers
        to your script.
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-0.5">
      {chapters.map((c) => (
        <ChapterRow
          key={c.id}
          ch={c}
          projectId={projectId}
          activeShortCode={activeChapterShortCode}
          onAction={onAction}
        />
      ))}
    </div>
  );
}
