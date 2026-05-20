import { useNavigate } from 'react-router-dom';
import { FolderOpen, Settings } from 'lucide-react';

export default function HomePage() {
  const navigate = useNavigate();

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-8">
      {/* Settings button in top-right corner */}
      <div className="absolute top-8 right-8">
        <button
          onClick={() => navigate('/settings')}
          className="px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded text-sm font-medium transition-colors flex items-center gap-2"
        >
          <Settings size={20} />
          Settings
        </button>
      </div>

      {/* Main content centered */}
      <div className="max-w-6xl mx-auto flex flex-col items-center justify-center min-h-screen">
        {/* Title and tagline */}
        <div className="text-center mb-16">
          <h1 className="text-6xl font-bold mb-4">Robomuffin Idea Factory</h1>
          <p className="text-xl text-gray-400">AI-Powered Creative Studio</p>
        </div>

        {/* Section cards grid */}
        <div className="w-full max-w-3xl">
          <div className="grid grid-cols-1 gap-6">
            {/* Projects Card */}
            <div
              onClick={() => navigate('/projects')}
              className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden hover:border-blue-600 hover:shadow-lg transition-all cursor-pointer group"
            >
              <div className="h-32 bg-gradient-to-br from-blue-900/20 to-gray-900 flex items-center justify-center">
                <FolderOpen
                  size={48}
                  className="text-blue-400 group-hover:text-blue-300 transition-colors"
                />
              </div>
              <div className="p-8">
                <h2 className="text-2xl font-bold mb-2">Projects</h2>
                <p className="text-gray-400">
                  Create and manage music video & narration projects
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
