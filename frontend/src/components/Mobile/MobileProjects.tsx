import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ChevronRight, Loader2, Music, Smartphone, Monitor } from 'lucide-react';
import { getProjects } from '@/api/client';
import type { Project } from '@/types/index';

const MODE_LABEL: Record<string, string> = {
  music_video: 'Music Video',
  narration_images: 'Narration · Images',
  narration_video: 'Narration · Video',
  talkie: 'Talkie (Lip-Sync)',
};

export default function MobileProjects() {
  const navigate = useNavigate();
  const { data, isLoading } = useQuery({
    queryKey: ['projects'],
    queryFn: async () => (await getProjects()).data,
  });

  const projects: Project[] = [...(data || [])].sort(
    (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
  );

  return (
    <div className="fixed inset-0 z-40 flex flex-col bg-gray-950 text-gray-100">
      <header className="flex items-center gap-2 px-4 h-14 border-b border-gray-800 bg-gray-900 flex-shrink-0">
        <Smartphone className="w-5 h-5 text-indigo-400" />
        <span className="font-semibold">Mobile Studio</span>
        <button
          onClick={() => navigate('/')}
          className="ml-auto flex items-center gap-1 text-xs text-gray-400 active:text-gray-200 px-2 py-1 rounded-lg"
        >
          <Monitor className="w-4 h-4" /> Desktop
        </button>
      </header>

      <main className="flex-1 overflow-y-auto p-3 pb-[env(safe-area-inset-bottom)]">
        {isLoading ? (
          <div className="flex items-center justify-center py-20 text-gray-400">
            <Loader2 className="w-6 h-6 animate-spin mr-2" /> Loading…
          </div>
        ) : projects.length === 0 ? (
          <div className="text-center text-gray-500 py-20">No projects yet.</div>
        ) : (
          <ul className="space-y-2.5">
            {projects.map((p) => (
              <li key={p.id}>
                <button
                  onClick={() => navigate(`/mobile/p/${p.id}`)}
                  className="w-full flex items-center gap-3 p-4 rounded-xl bg-gray-900 border border-gray-800 active:bg-gray-800 text-left"
                >
                  <div className="w-11 h-11 rounded-lg bg-gradient-to-br from-indigo-700/40 to-gray-800 flex items-center justify-center flex-shrink-0">
                    <Music className="w-5 h-5 text-indigo-300" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="font-semibold truncate">{p.name}</div>
                    <div className="text-xs text-gray-500 mt-0.5">
                      {MODE_LABEL[p.mode] || p.mode}
                      {typeof p.scene_count === 'number' ? ` · ${p.scene_count} scenes` : ''}
                    </div>
                  </div>
                  <ChevronRight className="w-5 h-5 text-gray-600 flex-shrink-0" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </main>
    </div>
  );
}
