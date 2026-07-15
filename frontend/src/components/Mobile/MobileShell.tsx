import { useNavigate } from 'react-router-dom';
import { ArrowLeft, LayoutGrid, Film, Users, Activity } from 'lucide-react';

export type MobileTab = 'overview' | 'scenes' | 'characters' | 'queue';

interface Props {
  projectId: string;
  title: string;
  subtitle?: string;
  active: MobileTab;
  right?: React.ReactNode;
  children: React.ReactNode;
}

const TABS: { key: MobileTab; label: string; icon: typeof Film; path: (id: string) => string }[] = [
  { key: 'overview', label: 'Overview', icon: LayoutGrid, path: (id) => `/mobile/p/${id}` },
  { key: 'scenes', label: 'Scenes', icon: Film, path: (id) => `/mobile/p/${id}/scenes` },
  { key: 'characters', label: 'Cast', icon: Users, path: (id) => `/mobile/p/${id}/characters` },
  { key: 'queue', label: 'Queue', icon: Activity, path: (id) => `/mobile/p/${id}/queue` },
];

export default function MobileShell({ projectId, title, subtitle, active, right, children }: Props) {
  const navigate = useNavigate();
  return (
    <div className="fixed inset-0 z-40 flex flex-col bg-gray-950 text-gray-100">
      {/* Header */}
      <header className="flex items-center gap-2 px-3 h-14 border-b border-gray-800 bg-gray-900 flex-shrink-0">
        <button
          onClick={() => navigate('/mobile')}
          className="p-2 -ml-1 rounded-lg active:bg-gray-800 text-gray-300"
          aria-label="Back to projects"
        >
          <ArrowLeft className="w-6 h-6" />
        </button>
        <div className="min-w-0 flex-1">
          <div className="font-semibold truncate leading-tight">{title}</div>
          {subtitle && <div className="text-[11px] text-gray-500 truncate">{subtitle}</div>}
        </div>
        {right}
      </header>

      {/* Content */}
      <main className="flex-1 overflow-y-auto overscroll-contain pb-2">{children}</main>

      {/* Bottom tab bar */}
      <nav className="flex-shrink-0 grid grid-cols-4 border-t border-gray-800 bg-gray-900 pb-[env(safe-area-inset-bottom)]">
        {TABS.map((t) => {
          const Icon = t.icon;
          const on = active === t.key;
          return (
            <button
              key={t.key}
              onClick={() => navigate(t.path(projectId))}
              className={`flex flex-col items-center justify-center gap-0.5 py-2.5 min-h-[56px] transition-colors ${
                on ? 'text-indigo-400' : 'text-gray-500 active:text-gray-300'
              }`}
            >
              <Icon className="w-5 h-5" />
              <span className="text-[10px] font-medium">{t.label}</span>
            </button>
          );
        })}
      </nav>
    </div>
  );
}
