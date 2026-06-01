/**
 * ChapterBreadcrumb — shows the project → chapter hierarchy at the top
 * of the chapter drill-down view.
 *
 *     [← Project] / Chapter 1 / Sub-chapter 1.2
 *
 * The project link returns to the main timeline.  Each chapter link
 * (when nested) jumps to that ancestor's drill-down.
 */
import { Link } from 'react-router-dom';
import type { Chapter } from '../../types';

interface ChapterBreadcrumbProps {
  projectId: string;
  projectName?: string;
  /** Ordered from root → leaf.  Last element is the currently-active chapter. */
  ancestry: Chapter[];
}

export default function ChapterBreadcrumb({
  projectId,
  projectName = 'Project',
  ancestry,
}: ChapterBreadcrumbProps) {
  if (!ancestry.length) {
    return (
      <div className="flex items-center gap-2 text-sm text-gray-400">
        <Link
          to={`/projects/${projectId}`}
          className="text-purple-300 hover:text-purple-200 transition-colors"
        >
          ← {projectName}
        </Link>
      </div>
    );
  }

  return (
    <nav
      className="flex items-center flex-wrap gap-1 text-sm text-gray-400"
      aria-label="Chapter breadcrumb"
    >
      <Link
        to={`/projects/${projectId}`}
        className="text-purple-300 hover:text-purple-200 transition-colors"
      >
        ← {projectName}
      </Link>
      {ancestry.map((ch, i) => {
        const isLast = i === ancestry.length - 1;
        return (
          <span key={ch.id} className="flex items-center gap-1">
            <span className="text-gray-600">/</span>
            {isLast ? (
              <span className="text-gray-200 font-medium">{ch.name}</span>
            ) : (
              <Link
                to={`/projects/${projectId}/c/${ch.short_code}`}
                className="text-purple-300 hover:text-purple-200 transition-colors"
              >
                {ch.name}
              </Link>
            )}
            <span className="text-[10px] text-gray-600 font-mono ml-1">
              {ch.short_code}
            </span>
          </span>
        );
      })}
    </nav>
  );
}
