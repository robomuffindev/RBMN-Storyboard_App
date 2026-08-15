/**
 * 🎬 Video Lab — top-level page (v1.277.7).
 *
 * Moved out of the character studio by request: video generation isn't a
 * character-creation step, it CONSUMES characters (via the 📚 picker). The
 * panel itself is unchanged and still lives in components/VNCCSNative/ —
 * this page just gives it a front door on the home screen.
 */
import { useNavigate } from 'react-router-dom';

import VideoLabPanel from '@/components/VNCCSNative/VideoLabPanel';

export default function VideoLabPage() {
  const navigate = useNavigate();
  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <div className="flex items-center gap-3 px-5 py-3 border-b border-gray-800">
        <button className="text-gray-400 hover:text-gray-200 text-sm"
                onClick={() => navigate('/')}>← Home</button>
        <h1 className="text-xl font-bold">🎬 Video Lab</h1>
        <span className="text-xs text-gray-500 hidden md:inline">
          MiniMax H3 — t2v · i2v · first+last · last-frame · references→video · LTX 2.3 upscale
        </span>
      </div>
      <div className="p-4">
        <VideoLabPanel />
      </div>
    </div>
  );
}
