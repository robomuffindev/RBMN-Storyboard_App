import { useRef, useState, useCallback, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Trash2, Volume2, Repeat, Music, ChevronDown, ChevronUp, ListOrdered, Loader2 } from 'lucide-react';
import { getBackingTracks, uploadBackingTrack, updateBackingTrack, deleteBackingTrack, updateProject } from '@/api/client';
import type { BackingTrack } from '@/types';
import { useAppStore } from '@/store';

interface BackingTrackTimelineProps {
  projectId: string;
  totalDuration: number;
  expanded?: boolean; // When true, fills the full parent height (tabbed mode)
}

export default function BackingTrackTimeline({ projectId, totalDuration, expanded }: BackingTrackTimelineProps) {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [editingTrack, setEditingTrack] = useState<string | null>(null);
  const [showMixer, setShowMixer] = useState(false);
  const [batchUploading, setBatchUploading] = useState(false);
  const [batchProgress, setBatchProgress] = useState({ current: 0, total: 0 });

  const currentProject = useAppStore((s) => s.currentProject);
  const projSettings = currentProject?.settings || {};

  // Audio mixer settings from project.settings
  // Also sync to Zustand store for live playback control
  const setStoreNarrationVolume = useAppStore((s) => s.setNarrationVolume);
  const setStoreBackingMasterVolume = useAppStore((s) => s.setBackingMasterVolume);
  const [loopBacking, setLoopBacking] = useState(projSettings.backing_track_loop ?? false);
  const [narrationVolume, setNarrationVolume] = useState(projSettings.narration_volume ?? 1.0);
  const [backingVolume, setBackingVolume] = useState(projSettings.backing_volume ?? 1.0);
  const [mainFadeIn, setMainFadeIn] = useState(projSettings.backing_main_fade_in ?? 0.0);
  const [mainFadeOut, setMainFadeOut] = useState(projSettings.backing_main_fade_out ?? 0.0);
  const [mainFadeInEnabled, setMainFadeInEnabled] = useState((projSettings.backing_main_fade_in ?? 0) > 0);
  const [mainFadeOutEnabled, setMainFadeOutEnabled] = useState((projSettings.backing_main_fade_out ?? 0) > 0);
  const [normalizeBacking, setNormalizeBacking] = useState(projSettings.normalize_backing ?? false);

  // Sync from project settings when they change
  useEffect(() => {
    const s = currentProject?.settings || {};
    setLoopBacking(s.backing_track_loop ?? false);
    const nv = s.narration_volume ?? 1.0;
    const bv = s.backing_volume ?? 1.0;
    setNarrationVolume(nv);
    setBackingVolume(bv);
    setStoreNarrationVolume(nv);
    setStoreBackingMasterVolume(bv);
    setMainFadeIn(s.backing_main_fade_in ?? 0.0);
    setMainFadeOut(s.backing_main_fade_out ?? 0.0);
    setMainFadeInEnabled((s.backing_main_fade_in ?? 0) > 0);
    setMainFadeOutEnabled((s.backing_main_fade_out ?? 0) > 0);
    setNormalizeBacking(s.normalize_backing ?? false);
  }, [currentProject?.id, currentProject?.settings]); // eslint-disable-line react-hooks/exhaustive-deps

  // Debounced save to project.settings
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saveSettings = useCallback((updates: Record<string, any>) => {
    if (!projectId) return;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(async () => {
      try {
        const merged = {
          ...(currentProject?.settings || {}),
          ...updates,
        };
        await updateProject(projectId, { settings: merged });
      } catch {
        // Silently fail — settings are non-critical
      }
    }, 500);
  }, [projectId, currentProject?.settings]);

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

  // Batch upload: upload files sequentially, then arrange them end-to-end
  const [uploadStatus, setUploadStatus] = useState<string | null>(null);

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;

    // IMPORTANT: capture files BEFORE clearing the input — clearing resets the FileList
    const fileList = Array.from(files);
    e.target.value = '';

    // Single file — simple path
    if (fileList.length === 1) {
      uploadMutation.mutate(fileList[0]);
      return;
    }
    setBatchUploading(true);
    setBatchProgress({ current: 0, total: fileList.length });
    setUploadStatus(`Starting upload of ${fileList.length} tracks...`);

    // Get the current end position of existing tracks (new tracks will chain after)
    const existingEnd = tracks.length > 0
      ? Math.max(...tracks.map(t => t.end_time))
      : 0;

    const newTrackIds: string[] = [];
    const newTrackDurations: number[] = [];
    let failCount = 0;

    for (let i = 0; i < fileList.length; i++) {
      setBatchProgress({ current: i + 1, total: fileList.length });
      setUploadStatus(`Uploading "${fileList[i].name}" (${i + 1}/${fileList.length})...`);
      try {
        const res = await uploadBackingTrack(projectId, fileList[i]);
        const track = res.data;
        newTrackIds.push(track.id);
        // Duration = end_time - start_time as returned by backend (end_time = audio duration, start_time = 0)
        newTrackDurations.push((track.end_time || 0) - (track.start_time || 0));
      } catch (err) {
        failCount++;
        console.error(`Failed to upload backing track "${fileList[i].name}":`, err);
      }
    }

    // Auto-arrange: position new tracks end-to-end, starting from existingEnd
    if (newTrackIds.length > 0) {
      setUploadStatus(`Arranging ${newTrackIds.length} tracks...`);
      let cursor = existingEnd;
      for (let i = 0; i < newTrackIds.length; i++) {
        const dur = newTrackDurations[i];
        try {
          await updateBackingTrack(projectId, newTrackIds[i], {
            start_time: cursor,
            end_time: cursor + dur,
            order_index: tracks.length + i,
          });
        } catch (err) {
          console.error(`Failed to arrange track ${newTrackIds[i]}:`, err);
        }
        cursor += dur;
      }
    }

    setBatchUploading(false);
    setBatchProgress({ current: 0, total: 0 });
    setUploadStatus(
      failCount > 0
        ? `Done — ${newTrackIds.length} uploaded, ${failCount} failed`
        : `Done — ${newTrackIds.length} tracks added`
    );
    // Clear the status message after a few seconds
    setTimeout(() => setUploadStatus(null), 4000);
    queryClient.invalidateQueries({ queryKey: ['backingTracks', projectId] });
  };

  // Arrange all tracks sequentially (end-to-end by order_index)
  const handleArrangeSequential = async () => {
    if (tracks.length < 2) return;

    const sorted = [...tracks].sort((a, b) => a.order_index - b.order_index);
    let cursor = 0;

    for (const track of sorted) {
      const dur = track.end_time - track.start_time;
      try {
        await updateBackingTrack(projectId, track.id, {
          start_time: cursor,
          end_time: cursor + dur,
        });
      } catch (err) {
        console.error(`Failed to arrange track ${track.id}:`, err);
      }
      cursor += dur;
    }

    queryClient.invalidateQueries({ queryKey: ['backingTracks', projectId] });
  };

  return (
    <div className={`bg-gray-900 px-3 py-2 flex flex-col ${expanded ? 'h-full overflow-hidden' : 'border-t border-gray-800'}`}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium text-gray-400">Backing Tracks</span>
        <div className="flex items-center gap-2">
          {/* Loop toggle */}
          <button
            onClick={() => {
              const next = !loopBacking;
              setLoopBacking(next);
              saveSettings({ backing_track_loop: next });
            }}
            className={`flex items-center gap-1 text-xs transition-colors ${
              loopBacking ? 'text-purple-400 hover:text-purple-300' : 'text-gray-500 hover:text-gray-400'
            }`}
            title={loopBacking ? 'Loop backing tracks: ON' : 'Loop backing tracks: OFF'}
          >
            <Repeat size={12} />
            <span className="hidden sm:inline">Loop</span>
          </button>

          {/* Arrange sequentially */}
          {tracks.length >= 2 && (
            <button
              onClick={handleArrangeSequential}
              className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-300 transition-colors"
              title="Arrange all tracks end-to-end in order"
            >
              <ListOrdered size={12} />
              <span className="hidden sm:inline">Arrange</span>
            </button>
          )}

          {/* Mixer toggle */}
          <button
            onClick={() => setShowMixer(!showMixer)}
            className={`flex items-center gap-1 text-xs transition-colors ${
              showMixer ? 'text-blue-400 hover:text-blue-300' : 'text-gray-500 hover:text-gray-400'
            }`}
            title="Audio Mixer"
          >
            <Music size={12} />
            <span className="hidden sm:inline">Mixer</span>
            {showMixer ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
          </button>

          {/* Add Tracks (supports multi-select) */}
          <button
            onClick={() => fileInputRef.current?.click()}
            className="flex items-center gap-1 text-xs text-blue-400 hover:text-blue-300 transition-colors"
            disabled={uploadMutation.isPending || batchUploading}
          >
            {batchUploading ? (
              <>
                <Loader2 size={12} className="animate-spin" />
                <span>{batchProgress.current}/{batchProgress.total}</span>
              </>
            ) : uploadMutation.isPending ? (
              <>
                <Loader2 size={12} className="animate-spin" />
                <span>Uploading...</span>
              </>
            ) : (
              <>
                <Plus size={12} />
                <span>Add Tracks</span>
              </>
            )}
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept="audio/*"
            multiple
            onChange={handleFileUpload}
            className="hidden"
          />
        </div>
      </div>

      {/* Upload Status Bar */}
      {uploadStatus && (
        <div className={`mb-2 px-2 py-1.5 rounded text-xs flex items-center gap-2 ${
          uploadStatus.startsWith('Done')
            ? (uploadStatus.includes('failed') ? 'bg-yellow-900/40 text-yellow-300 border border-yellow-700/50' : 'bg-green-900/40 text-green-300 border border-green-700/50')
            : 'bg-blue-900/40 text-blue-300 border border-blue-700/50'
        }`}>
          {!uploadStatus.startsWith('Done') && <Loader2 size={12} className="animate-spin flex-shrink-0" />}
          <span className="truncate">{uploadStatus}</span>
          {batchUploading && batchProgress.total > 0 && (
            <div className="ml-auto flex-shrink-0 w-24 h-1.5 bg-gray-700 rounded-full overflow-hidden">
              <div
                className="h-full bg-blue-500 rounded-full transition-all duration-300"
                style={{ width: `${(batchProgress.current / batchProgress.total) * 100}%` }}
              />
            </div>
          )}
        </div>
      )}

      {/* Audio Mixer Panel */}
      {showMixer && (
        <div className="mb-2 p-2 bg-gray-800 rounded border border-gray-700 space-y-2">
          <div className="text-xs font-medium text-gray-300 mb-1">Audio Mixer</div>

          {/* Narration Volume */}
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-gray-400 w-16 flex-shrink-0">Narration</span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={narrationVolume}
              onChange={(e) => {
                const v = parseFloat(e.target.value);
                setNarrationVolume(v);
                setStoreNarrationVolume(v);
                saveSettings({ narration_volume: v });
              }}
              className="flex-1 h-1 accent-blue-500"
            />
            <span className="text-[10px] text-gray-500 w-10 text-right">{Math.round(narrationVolume * 100)}%</span>
          </div>

          {/* Backing Volume */}
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-gray-400 w-16 flex-shrink-0">Backing</span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={backingVolume}
              onChange={(e) => {
                const v = parseFloat(e.target.value);
                setBackingVolume(v);
                setStoreBackingMasterVolume(v);
                saveSettings({ backing_volume: v });
              }}
              className="flex-1 h-1 accent-purple-500"
            />
            <span className="text-[10px] text-gray-500 w-10 text-right">{Math.round(backingVolume * 100)}%</span>
          </div>

          {/* Separator */}
          <div className="border-t border-gray-700 pt-2 mt-1">
            <div className="text-[10px] text-gray-500 mb-1">Main Fade In/Out (first/last backing track)</div>
          </div>

          {/* Main Fade In */}
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={mainFadeInEnabled}
              onChange={(e) => {
                const checked = e.target.checked;
                setMainFadeInEnabled(checked);
                if (checked) {
                  const defaultVal = 2.0;
                  setMainFadeIn(defaultVal);
                  saveSettings({ backing_main_fade_in: defaultVal });
                } else {
                  setMainFadeIn(0);
                  saveSettings({ backing_main_fade_in: 0 });
                }
              }}
              className="rounded border-gray-600 bg-gray-900 text-purple-500 focus:ring-purple-500"
            />
            <span className="text-[10px] text-gray-400 w-14 flex-shrink-0">Fade In</span>
            {mainFadeInEnabled && (
              <>
                <input
                  type="number"
                  step={0.5}
                  min={0.5}
                  max={30}
                  value={mainFadeIn}
                  onChange={(e) => {
                    const v = parseFloat(e.target.value) || 0;
                    setMainFadeIn(v);
                    saveSettings({ backing_main_fade_in: v });
                  }}
                  className="w-16 px-1.5 py-0.5 text-xs bg-gray-900 border border-gray-700 rounded text-gray-200"
                />
                <span className="text-[10px] text-gray-500">sec</span>
              </>
            )}
          </div>

          {/* Main Fade Out */}
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={mainFadeOutEnabled}
              onChange={(e) => {
                const checked = e.target.checked;
                setMainFadeOutEnabled(checked);
                if (checked) {
                  const defaultVal = 3.0;
                  setMainFadeOut(defaultVal);
                  saveSettings({ backing_main_fade_out: defaultVal });
                } else {
                  setMainFadeOut(0);
                  saveSettings({ backing_main_fade_out: 0 });
                }
              }}
              className="rounded border-gray-600 bg-gray-900 text-purple-500 focus:ring-purple-500"
            />
            <span className="text-[10px] text-gray-400 w-14 flex-shrink-0">Fade Out</span>
            {mainFadeOutEnabled && (
              <>
                <input
                  type="number"
                  step={0.5}
                  min={0.5}
                  max={30}
                  value={mainFadeOut}
                  onChange={(e) => {
                    const v = parseFloat(e.target.value) || 0;
                    setMainFadeOut(v);
                    saveSettings({ backing_main_fade_out: v });
                  }}
                  className="w-16 px-1.5 py-0.5 text-xs bg-gray-900 border border-gray-700 rounded text-gray-200"
                />
                <span className="text-[10px] text-gray-500">sec</span>
              </>
            )}
          </div>

          {/* Normalize Backing Tracks */}
          <div className="flex items-center gap-2 pt-1 border-t border-gray-700">
            <input
              type="checkbox"
              checked={normalizeBacking}
              onChange={(e) => {
                const v = e.target.checked;
                setNormalizeBacking(v);
                saveSettings({ normalize_backing: v });
              }}
              className="rounded border-gray-600 bg-gray-900 text-purple-500 focus:ring-purple-500"
            />
            <span className="text-[10px] text-gray-400">Normalize backing track loudness</span>
          </div>
        </div>
      )}

      <div className={expanded ? 'flex-1 overflow-y-auto min-h-0' : ''}>
      {tracks.length === 0 ? (
        <div className="text-xs text-gray-600 text-center py-3">
          No backing tracks. Click &quot;Add Tracks&quot; to upload background music (multi-select supported).
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
    </div>
  );
}
