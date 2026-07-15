import { Play, Pause, ImageOff, Loader2, Layers } from 'lucide-react';
import { handleImgError } from '@/utils/brokenImage';
import type { Scene } from '@/types/index';

interface SlotProps {
  label: string;
  url: string | null;
  count: number;
  onOpen: () => void;
}

function FrameSlot({ label, url, count, onOpen }: SlotProps) {
  return (
    <button
      data-sb-interactive
      onClick={onOpen}
      className="group relative flex-1 aspect-video rounded-md overflow-hidden border border-gray-700 bg-gray-900 hover:border-indigo-400 transition-colors"
      title={`Open ${label}`}
    >
      {url ? (
        <img
          src={url}
          onError={handleImgError}
          className="w-full h-full object-cover"
          alt={label}
          draggable={false}
        />
      ) : (
        <div className="w-full h-full flex flex-col items-center justify-center text-gray-600">
          <ImageOff className="w-5 h-5 mb-1" />
          <span className="text-[10px] uppercase tracking-wide">{label}</span>
        </div>
      )}
      <div className="absolute top-1 left-1 px-1.5 py-0.5 rounded bg-black/60 text-[9px] font-medium uppercase tracking-wide text-gray-200">
        {label}
      </div>
      {count > 1 && (
        <div className="absolute top-1 right-1 px-1.5 py-0.5 rounded bg-black/60 text-[9px] text-gray-200 flex items-center gap-0.5">
          <Layers className="w-2.5 h-2.5" />
          {count}
        </div>
      )}
      <div className="absolute inset-0 bg-indigo-500/0 group-hover:bg-indigo-500/10 transition-colors" />
    </button>
  );
}

interface SceneCardProps {
  scene: Scene;
  index: number;
  ffUrl: string | null;
  lfUrl: string | null;
  ffCount: number;
  lfCount: number;
  lyric: string;
  audioUrl: string | null;
  playing: boolean;
  busy: boolean;
  onOpen: (frame: 'first' | 'last') => void;
  onToggleAudio: () => void;
}

export default function SceneCard({
  scene,
  index,
  ffUrl,
  lfUrl,
  ffCount,
  lfCount,
  lyric,
  audioUrl,
  playing,
  busy,
  onOpen,
  onToggleAudio,
}: SceneCardProps) {
  return (
    <div className="relative w-[320px] flex-shrink-0 rounded-lg border border-gray-700 bg-gray-800/90 shadow-lg backdrop-blur-sm">
      {busy && (
        <div className="absolute -top-2 -right-2 z-10 flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-500 text-black text-[10px] font-semibold shadow">
          <Loader2 className="w-3 h-3 animate-spin" />
          Rendering
        </div>
      )}

      <div className="flex items-center gap-2 px-3 pt-2 pb-1">
        <span className="flex items-center justify-center w-5 h-5 rounded-full bg-indigo-600 text-white text-[11px] font-bold flex-shrink-0">
          {index + 1}
        </span>
        <span className="text-sm font-semibold text-gray-100 truncate" title={scene.name}>
          {scene.name || `Scene ${index + 1}`}
        </span>
        <span className="ml-auto text-[10px] text-gray-500 flex-shrink-0">
          {scene.start_time.toFixed(1)}s–{scene.end_time.toFixed(1)}s
        </span>
      </div>

      <div className="flex gap-2 px-3">
        <FrameSlot label="First" url={ffUrl} count={ffCount} onOpen={() => onOpen('first')} />
        <FrameSlot label="Last" url={lfUrl} count={lfCount} onOpen={() => onOpen('last')} />
      </div>

      <div className="px-3 pt-2">
        <div className="h-16 overflow-y-auto rounded bg-gray-900/70 px-2 py-1.5 text-[12px] leading-snug text-gray-300 whitespace-pre-wrap">
          {lyric || <span className="text-gray-600 italic">No lyric / narration text for this scene.</span>}
        </div>
      </div>

      <div className="flex items-center gap-2 px-3 py-2">
        <button
          data-sb-interactive
          onClick={onToggleAudio}
          disabled={!audioUrl}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
            audioUrl
              ? playing
                ? 'bg-indigo-600 text-white hover:bg-indigo-500'
                : 'bg-gray-700 text-gray-100 hover:bg-gray-600'
              : 'bg-gray-800 text-gray-600 cursor-not-allowed'
          }`}
          title={audioUrl ? 'Play scene audio' : 'No sliced audio for this scene (run Slice Audio in Timeline)'}
        >
          {playing ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
          {playing ? 'Playing' : 'Audio'}
        </button>
      </div>
    </div>
  );
}
