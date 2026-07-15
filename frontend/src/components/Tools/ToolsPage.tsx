/**
 * Tools section — container with tab nav for the Pose/Expression libraries
 * and organizers. Reachable from Home → Tools (/tools).
 */
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ChevronLeft, PersonStanding, Smile, FolderInput } from 'lucide-react';
import { PoseLibraryView } from './PoseLibraryView';
import { PoseOrganizerView } from './PoseOrganizerView';
import { ExpressionLibraryView } from './ExpressionLibraryView';

type ToolTab = 'pose-library' | 'pose-organizer' | 'expressions';

export default function ToolsPage() {
  const navigate = useNavigate();
  const [tab, setTab] = useState<ToolTab>('pose-library');

  const tabs: { key: ToolTab; label: string; icon: React.ReactNode }[] = [
    { key: 'pose-library', label: 'Pose Library', icon: <PersonStanding size={16} /> },
    { key: 'pose-organizer', label: 'Pose Organizer', icon: <FolderInput size={16} /> },
    { key: 'expressions', label: 'Expression Library', icon: <Smile size={16} /> },
  ];

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <div className="max-w-7xl mx-auto p-6 flex flex-col gap-5">
        <div className="flex items-center justify-between">
          <button onClick={() => navigate('/')} className="flex items-center gap-1 text-sm text-gray-400 hover:text-gray-200">
            <ChevronLeft size={16} /> Home
          </button>
          <h1 className="text-2xl font-bold">Tools</h1>
          <div className="w-16" />
        </div>

        <div className="flex gap-1 border-b border-gray-800">
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors flex items-center gap-2 ${
                tab === t.key ? 'border-teal-500 text-teal-300' : 'border-transparent text-gray-500 hover:text-gray-300'
              }`}
            >
              {t.icon}
              {t.label}
            </button>
          ))}
        </div>

        <div className="pt-1">
          {tab === 'pose-library' && <PoseLibraryView />}
          {tab === 'pose-organizer' && <PoseOrganizerView onCommitted={() => setTab('pose-library')} />}
          {tab === 'expressions' && <ExpressionLibraryView />}
        </div>
      </div>
    </div>
  );
}
