import { useNavigate } from 'react-router-dom';
import { FolderOpen, Settings, Activity, Users, Wrench, Smartphone, Globe2, Clapperboard, Music } from 'lucide-react';

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
        <div className="w-full max-w-5xl">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {/* Story / World Builder Card */}
            <div
              onClick={() => navigate('/worlds')}
              className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden hover:border-amber-600 hover:shadow-lg transition-all cursor-pointer group"
            >
              <div className="h-32 bg-gradient-to-br from-amber-900/20 to-gray-900 flex items-center justify-center">
                <Globe2
                  size={48}
                  className="text-amber-400 group-hover:text-amber-300 transition-colors"
                />
              </div>
              <div className="p-8">
                <h2 className="text-2xl font-bold mb-2">Story / World Builder</h2>
                <p className="text-gray-400">
                  Build worlds, stories & full casts with LLM help — then batch-generate the characters
                </p>
              </div>
            </div>

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

            {/* Batch Runs Card */}
            <div
              onClick={() => navigate('/batches')}
              className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden hover:border-purple-600 hover:shadow-lg transition-all cursor-pointer group"
            >
              <div className="h-32 bg-gradient-to-br from-purple-900/20 to-gray-900 flex items-center justify-center">
                <Activity
                  size={48}
                  className="text-purple-400 group-hover:text-purple-300 transition-colors"
                />
              </div>
              <div className="p-8">
                <h2 className="text-2xl font-bold mb-2">Batch Runs</h2>
                <p className="text-gray-400">
                  Monitor auto-generation progress, errors & resume runs
                </p>
              </div>
            </div>

            {/* Character Studio Card */}
            <div
              onClick={() => navigate('/studio')}
              className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden hover:border-indigo-600 hover:shadow-lg transition-all cursor-pointer group"
            >
              <div className="h-32 bg-gradient-to-br from-indigo-900/20 to-gray-900 flex items-center justify-center">
                <Users
                  size={48}
                  className="text-indigo-400 group-hover:text-indigo-300 transition-colors"
                />
              </div>
              <div className="p-8">
                <h2 className="text-2xl font-bold mb-2">Character Studio</h2>
                <p className="text-gray-400">
                  Create reusable characters, organize them by story, and build LoRA training datasets
                </p>
              </div>
            </div>

            {/* Video Lab Card */}
            <div
              onClick={() => navigate('/video-lab')}
              className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden hover:border-rose-600 hover:shadow-lg transition-all cursor-pointer group"
            >
              <div className="h-32 bg-gradient-to-br from-rose-900/20 to-gray-900 flex items-center justify-center">
                <Clapperboard
                  size={48}
                  className="text-rose-400 group-hover:text-rose-300 transition-colors"
                />
              </div>
              <div className="p-8">
                <h2 className="text-2xl font-bold mb-2">Video Lab</h2>
                <p className="text-gray-400">
                  MiniMax H3 video generation — text, image & reference modes, with LTX upscaling
                </p>
              </div>
            </div>

            {/* Audio Lab Card */}
            <div
              onClick={() => navigate('/audio-lab')}
              className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden hover:border-emerald-600 hover:shadow-lg transition-all cursor-pointer group"
            >
              <div className="h-32 bg-gradient-to-br from-emerald-900/20 to-gray-900 flex items-center justify-center">
                <Music
                  size={48}
                  className="text-emerald-400 group-hover:text-emerald-300 transition-colors"
                />
              </div>
              <div className="p-8">
                <h2 className="text-2xl font-bold mb-2">Audio Lab</h2>
                <p className="text-gray-400">
                  Local music generation (ACE-Step) & narration with voice cloning (F5-TTS)
                </p>
              </div>
            </div>

            {/* Tools Card */}
            <div
              onClick={() => navigate('/tools')}
              className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden hover:border-teal-600 hover:shadow-lg transition-all cursor-pointer group"
            >
              <div className="h-32 bg-gradient-to-br from-teal-900/20 to-gray-900 flex items-center justify-center">
                <Wrench
                  size={48}
                  className="text-teal-400 group-hover:text-teal-300 transition-colors"
                />
              </div>
              <div className="p-8">
                <h2 className="text-2xl font-bold mb-2">Tools</h2>
                <p className="text-gray-400">
                  Pose &amp; Expression libraries — organize, tag, and build reusable pose/expression packs
                </p>
              </div>
            </div>

            {/* Mobile Mode Card */}
            <div
              onClick={() => navigate('/mobile')}
              className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden hover:border-indigo-600 hover:shadow-lg transition-all cursor-pointer group"
            >
              <div className="h-32 bg-gradient-to-br from-indigo-900/20 to-gray-900 flex items-center justify-center">
                <Smartphone
                  size={48}
                  className="text-indigo-400 group-hover:text-indigo-300 transition-colors"
                />
              </div>
              <div className="p-8">
                <h2 className="text-2xl font-bold mb-2">Mobile Mode</h2>
                <p className="text-gray-400">
                  Touch-optimized view for phone &amp; tablet — work on scenes, cast, and batch status over your network
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
