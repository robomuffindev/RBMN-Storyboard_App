/**
 * ChapterOverlay — colored bars row showing chapter spans on the timeline.
 *
 * Renders two overlay rows when sub-chapters exist:
 *   Row 0: top-level chapters (depth 0) — full opacity
 *   Row 1: sub-chapters (depth 1+)      — slightly transparent, nested
 *
 * Clicking a bar navigates to the chapter drill-down view.
 *
 * Width math: each bar's width = ((chapter.end_time - chapter.start_time) / totalDuration) * 100%.
 * Position : ((chapter.start_time) / totalDuration) * 100%.
 */
import { useNavigate } from 'react-router-dom';
import type { ChapterTreeNode } from '../../types';

interface ChapterOverlayProps {
  /** Tree of chapters — top-level first, with nested children. */
  chapters: ChapterTreeNode[];
  /** Total duration of the project audio in seconds. Used to scale widths. */
  totalDuration: number;
  /** Project ID — used for chapter drill-down navigation. */
  projectId: string;
  /** When set, highlight this chapter (you're currently drilled into it). */
  activeChapterShortCode?: string | null;
  /** Optional click handler — when provided, replaces the default navigate behavior. */
  onChapterClick?: (chapter: ChapterTreeNode) => void;
  /** Optional zoom level (1 = fit; >1 = zoomed in). Width is multiplied by this. */
  zoom?: number;
}

/** Flatten the tree into one list per depth row. */
function flattenByDepth(chapters: ChapterTreeNode[]): Map<number, ChapterTreeNode[]> {
  const out = new Map<number, ChapterTreeNode[]>();
  const walk = (nodes: ChapterTreeNode[]) => {
    for (const n of nodes) {
      const list = out.get(n.depth) ?? [];
      list.push(n);
      out.set(n.depth, list);
      if (n.children?.length) walk(n.children);
    }
  };
  walk(chapters);
  return out;
}

export default function ChapterOverlay({
  chapters,
  totalDuration,
  projectId,
  activeChapterShortCode,
  onChapterClick,
  zoom = 1,
}: ChapterOverlayProps) {
  const navigate = useNavigate();

  if (!chapters.length || totalDuration <= 0) return null;

  const byDepth = flattenByDepth(chapters);
  const depths = Array.from(byDepth.keys()).sort((a, b) => a - b);

  // Number top-level chapters in playback order so the overlay reads
  // "1 · Chapter Name" / "2 · Chapter Name" instead of just the name.
  // Sub-chapters get their parent's label with a letter suffix at
  // depth 1 (1a, 1b), then numeric at depth 2 (1a1, 1a2), then letters
  // again at depth 3 (1a1a) — recursive so the labeling holds at any
  // future tree depth.  Suffixes wrap at 26 (a..z) by falling back to
  // 1-indexed numbers for the 27th sibling onward.
  const topLevel = [...(byDepth.get(0) ?? [])].sort((a, b) => a.start_time - b.start_time);
  const chapterNumber = new Map<string, string>();
  const subSuffix = (i: number) =>
    i < 26 ? String.fromCharCode(97 + i) : String(i + 1);
  const assignNumbers = (nodes: ChapterTreeNode[], prefix: string, depth: number) => {
    const sorted = [...nodes].sort((a, b) => a.start_time - b.start_time);
    sorted.forEach((ch, i) => {
      // depth 0: "1", "2", ...
      // depth 1: "1a", "1b", ... (letters)
      // depth 2: "1a1", "1a2", ... (numbers)
      // depth 3: "1a1a", ... (letters) — alternates
      const segment = depth === 0
        ? String(i + 1)
        : depth % 2 === 1
          ? subSuffix(i)
          : String(i + 1);
      const label = prefix + segment;
      chapterNumber.set(ch.id, label);
      if (ch.children?.length) {
        assignNumbers(ch.children, label, depth + 1);
      }
    });
  };
  assignNumbers(topLevel, '', 0);

  const handleClick = (ch: ChapterTreeNode) => {
    if (onChapterClick) {
      onChapterClick(ch);
    } else {
      navigate(`/project/${projectId}/c/${ch.short_code}`);
    }
  };

  return (
    <div className="w-full" data-testid="chapter-overlay">
      {depths.map((d) => (
        <div
          key={d}
          className="relative h-5 mb-0.5"
          style={{ width: `${zoom * 100}%` }}
        >
          {(byDepth.get(d) ?? []).map((ch) => {
            const widthPct = ((ch.end_time - ch.start_time) / totalDuration) * 100;
            const leftPct = (ch.start_time / totalDuration) * 100;
            const isActive = ch.short_code === activeChapterShortCode;
            const opacity = d === 0 ? 1.0 : 0.65;
            return (
              <button
                key={ch.id}
                onClick={() => handleClick(ch)}
                title={`${ch.name} (${ch.short_code}) · ${ch.scene_count ?? 0} scenes · ${ch.start_time.toFixed(1)}s - ${ch.end_time.toFixed(1)}s`}
                className={`absolute h-full rounded-sm text-xs text-white px-2 truncate text-left hover:brightness-125 transition-all overflow-hidden ${
                  isActive ? 'ring-2 ring-white' : ''
                }`}
                style={{
                  left: `${leftPct}%`,
                  width: `${Math.max(widthPct, 0.4)}%`,
                  backgroundColor: ch.color,
                  opacity,
                  borderLeft: d > 0 ? '2px solid rgba(255,255,255,0.4)' : 'none',
                }}
              >
                <span className="text-[10px] font-medium truncate">
                  {chapterNumber.get(ch.id) && (
                    <span className="font-mono mr-1 opacity-80">
                      {chapterNumber.get(ch.id)} ·
                    </span>
                  )}
                  {ch.name}
                </span>
              </button>
            );
          })}
        </div>
      ))}
    </div>
  );
}
