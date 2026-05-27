import { useRef, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Trash2, Volume2 } from 'lucide-react';
import { getBackingTracks, uploadBackingTrack, updateBackingTrack, deleteBackingTrack } from '@/api/client';
import type { BackingTrack } from '@/types';

interface BackingTrackTimelineProps {
  projectId: string;
  totalDuration: number;
}

export default function BackingTrackTimeline({ projectId, totalDuration }: BackingTrackTimelineProps) {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [editingTrack, setEditingTrack] = useState<string | null>(null);

  const { data: tracksData } = useQuery({
    queryKey: ['backingTracks', projectId],
    queryFn: () => getBackingTracks(projectId),
    enabled: !!projectId,
  });

  const tracks: BackingTrack[] = tracksData?.data || [];

  const uploadMutation = useMutation({
    mutationFn: (file: File) => uploadBackingTrack(projectId, file),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['backingTracks', projectId] }),
  });

  const updateMutation = useMutation({
    mutationFn: ({ trackId, data }: { trackId: string; data: Partial<BackingTrack> }) =>
      updateBackingTrack(projectId, trackId, data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['backingTracks', projectId] }),
  });

  const deleteMutation = useMutation({
    mutationFn: (trackId: string) => deleteBackingTrack(projectId, trackId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['backingTracks', projectId] }),
  });

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      uploadMutation.mutate(file);
      e.target.value = '';
    }
  };

  return (
    <div className="bg-gray-900 border-t border-gray-800 px-3 py-2">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium text-gray-400">Backing Tracks</span>
        <button
          onClick={() => fileInputRef.current?.click()}
          className="flex items-center gap-1 text-xs text-blue-400 hover:text-blue-300 transition-colors"
          disabled={uploadMutation.isPending}
        >
          <Plus size={12} />
          {uploadMutation.isPending ? 'Uploading...' : 'Add Track'}
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept="audio/*"
          onChange={handleFileUpload}
          className="hidden"
        />
      </div>

      {tracks.length === 0 ? (
        <div className="text-xs text-gray-600 text-center py-3">
          No backing tracks. Click &quot;Add Track&quot; to upload background music.
        </div>
      ) : (
        <div className="space-y-1">
          {tracks.map((track) => (
            <div key={track.id} className="flex items-center gap-2 group">
              {/* Track label */}
              <div className="w-24 flex-shrink-0 text-xs text-gray-400 truncate" title={track.filename}>
                {track.filename}
              </div>

              {/* Track bar */}
              <div className="flex-1 relative h-6 bg-gray-800 rounded overflow-hidden">
                <div
                  className="absolute top-0 h-full bg-purple-700/60 rounded border border-purple-500/40 cursor-pointer hover:bg-purple-700/80 transition-colors"
                  style={{
                    left: `${(track.start_time / Math.max(totalDuration, 1)) * 100}%`,
                    width: `${((track.end_time - track.start_time) / Math.max(totalDuration, 1)) * 100}%`,
                  }}
                  onClick={() => setEditingTrack(editingTrack === track.id ? null : track.id)}
                  title={`${track.filename} (${track.start_time.toFixed(1)}s - ${track.end_time.toFixed(1)}s)`}
                />
              </div>

              {/* Volume control */}
              <div className="flex items-center gap-1 flex-shrink-0">
                <Volume2 size={12} className="text-gray-500" />
                <input
                  type="range"
                  min={-20}
                  max={6}
                  step={1}
                  value={track.volume_db}
                  onChange={(e) =>
                    updateMutation.mutate({
                      trackId: track.id,
                      data: { volume_db: parseInt(e.target.value) },
                    })
                  }
                  className="w-16 h-1 accent-purple-500"
                  title={`${track.volume_db} dB`}
                />
                <span className="text-[10px] text-gray-500 w-8 text-right">{track.volume_db}dB</span>
              </div>

              {/* Delete */}
              <button
                onClick={() => {
                  if (confirm(`Delete backing track "${track.filename}"?`)) {
                    deleteMutation.mutate(track.id);
                  }
                }}
                className="text-gray-600 hover:text-red-400 transition-colors opacity-0 group-hover:opacity-100"
              >
                <Trash2 size={12} />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Inline editor for selected track */}
      {editingTrack && tracks.find(t => t.id === editingTrack) && (() => {
        const track = tracks.find(t => t.id === editingTrack)!;
        return (
          <div className="mt-2 p-2 bg-gray-800 rounded border border-gray-700 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-gray-300">{track.filename}</span>
              <button onClick={() => setEditingTrack(null)} className="text-xs text-gray-500 hover:text-gray-300">Close</button>
            </div>
            <div className="grid grid-cols-4 gap-2">
              <div>
                <label className="block text-[10px] text-gray-500 mb-0.5">Start (s)</label>
                <input
                  type="number"
                  step={0.1}
                  value={track.start_time}
                  onChange={(e) => updateMutation.mutate({ trackId: track.id, data: { start_time: parseFloat(e.target.value) || 0 } })}
                  className="w-full px-1.5 py-0.5 text-xs bg-gray-900 border border-gray-700 rounded text-gray-200"
                />
              </div>
              <div>
                <label className="block text-[10px] text-gray-500 mb-0.5">End (s)</label>
                <input
                  type="number"
                  step={0.1}
                  value={track.end_time}
                  onChange={(e) => updateMutation.mutate({ trackId: track.id, data: { end_time: parseFloat(e.target.value) || 0 } })}
                  className="w-full px-1.5 py-0.5 text-xs bg-gray-900 border border-gray-700 rounded text-gray-200"
                />
              </div>
              <div>
                <label className="block text-[10px] text-gray-500 mb-0.5">Fade In (s)</label>
                <input
                  type="number"
                  step={0.1}
                  min={0}
                  value={track.fade_in_sec}
                  onChange={(e) => updateMutation.mutate({ trackId: track.id, data: { fade_in_sec: parseFloat(e.target.value) || 0 } })}
                  className="w-full px-1.5 py-0.5 text-xs bg-gray-900 border border-gray-700 rounded text-gray-200"
                />
              </div>
              <div>
                <label className="block text-[10px] text-gray-500 mb-0.5">Fade Out (s)</label>
                <input
                  type="number"
                  step={0.1}
                  min={0}
                  value={track.fade_out_sec}
                  onChange={(e) => updateMutation.mutate({ trackId: track.id, data: { fade_out_sec: parseFloat(e.target.value) || 0 } })}
                  className="w-full px-1.5 py-0.5 text-xs bg-gray-900 border border-gray-700 rounded text-gray-200"
                />
              </div>
            </div>
          </div>
        );
      })()}
    </div>
  );
}
