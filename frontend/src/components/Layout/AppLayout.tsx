import { useEffect, useState, useCallback, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Settings, Download, ChevronLeft, Grid3x3, Music, Plus, Play, Pause, GripHorizontal, Lightbulb, GitBranch, Wand2, MonitorPlay, MoreVertical, Pencil, Layers, ListOrdered, PanelLeft } from 'lucide-react';
import { getProject, getScenes, getSections, getAssets, exportVideo, getExportStatus, createScenesFromSections, createScene, updateScene, deleteScene, startSequentialAutoGen, generateVideoFlow, renderPreview, getPreviewStatus, getLyrics, updateProject, getSequentialAutoGenStatus } from '@/api/client';
import { useAppStore } from '@/store';
import type { Scene } from '@/types/index';
import Timeline from '@/components/Timeline/Timeline';
import SceneEditor from '@/components/SceneEditor/SceneEditor';
import AssetManager from '@/components/AssetManager/AssetManager';
import AudioSetup from '@/components/AudioSetup/AudioSetup';
import GenerationPanel from '@/components/GenerationPanel/GenerationPanel';
import VideoPreview from '@/components/VideoPreview/VideoPreview';
import ConceptPanel from '@/components/ConceptPanel/ConceptPanel';
import VideoFlowPanel from '@/components/VideoFlowPanel/VideoFlowPanel';
import AutoGenStatusBar from '@/components/BatchMode/AutoGenStatusBar';

const EMPTY_ARRAY: never[] = [];

export default function AppLayout() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [selectedPanel, setSelectedPanel] = useState<'audio' | 'scenes' | 'assets' | 'concept' | 'flow'>('audio');
  const [exportOpen, setExportOpen] = useState(false);
  const [autoGenOpen, setAutoGenOpen] = useState(false);
  const [editorCollapsed, setEditorCollapsed] = useState(false);
  const [previewRendering, setPreviewRendering] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const previewPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [timelineHeight, setTimelineHeight] = useState(256); // default h-64 = 256px
  const [toolsMenuOpen, setToolsMenuOpen] = useState(false);
  const toolsMenuRef = useRef<HTMLDivElement>(null);
  const [renameModalOpen, setRenameModalOpen] = useState(false);
  const [newProjectName, setNewProjectName] = useState('');
  const isDraggingTimeline = useRef(false);
  const dragStartY = useRef(0);
  const dragStartHeight = useRef(0);
  // Mobile panel visibility
  const [mobilePanel, setMobilePanel] = useState<'editor' | 'left' | 'queue'>('editor');

  // Auto-gen status bar state
  const [autoGenStatus, setAutoGenStatus] = useState<string>('idle');
  const [autoGenMode, setAutoGenMode] = useState<string>('');
  const [autoGenCompleted, setAutoGenCompleted] = useState(0);
  const [autoGenTotal, setAutoGenTotal] = useState(0);
  const [autoGenStep, setAutoGenStep] = useState<string | null>(null);
  const [autoGenSceneName, setAutoGenSceneName] = useState<string | null>(null);
  const [autoGenBatchRunId, setAutoGenBatchRunId] = useState<string | null>(null);
  const [autoGenMinimized, setAutoGenMinimized] = useState(false);
  const [autoGenDismissed, setAutoGenDismissed] = useState(false);
  const autoGenPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Timeline resize drag handlers
  const handleTimelineDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isDraggingTimeline.current = true;
    dragStartY.current = e.clientY;
    dragStartHeight.current = timelineHeight;

    const handleMouseMove = (ev: MouseEvent) => {
      if (!isDraggingTimeline.current) return;
      const delta = dragStartY.current - ev.clientY; // dragging up = increase height
      const newHeight = Math.min(Math.max(dragStartHeight.current + delta, 120), 500);
      setTimelineHeight(newHeight);
    };

    const handleMouseUp = () => {
      isDraggingTimeline.current = false;
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
  }, [timelineHeight]);

  const { setProject, setScenes, setSections, setAssets } =
    useAppStore();

  const { data: project } = useQuery({
    queryKey: ['project', id],
    queryFn: async () => {
      const response = await getProject(id!);
      return response.data;
    },
    enabled: !!id,
  });

  const { data: scenes } = useQuery({
    queryKey: ['scenes', id],
    queryFn: async () => {
      const response = await getScenes(id!);
      const data = response.data;
      return Array.isArray(data) ? data : [];
    },
    enabled: !!id,
  });

  const { data: sections } = useQuery({
    queryKey: ['sections', id],
    queryFn: async () => {
      const response = await getSections(id!);
      const data = response.data;
      return Array.isArray(data) ? data : [];
    },
    enabled: !!id,
  });

  const { data: assets } = useQuery({
    queryKey: ['assets', id],
    queryFn: async () => {
      const response = await getAssets(id!);
      const data = response.data;
      return Array.isArray(data) ? data : [];
    },
    enabled: !!id,
  });

  // Use stable references for arrays to avoid infinite re-render loops
  const stableScenes = scenes ?? EMPTY_ARRAY;
  const stableSections = sections ?? EMPTY_ARRAY;
  const stableAssets = assets ?? EMPTY_ARRAY;

  const createScenesMutation = useMutation({
    mutationFn: async () => {
      if (!id) return;
      await createScenesFromSections(id);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['scenes', id] });
    },
  });

  // Rename project (label only)
  const renameMutation = useMutation({
    mutationFn: async (name: string) => {
      if (!id) return;
      await updateProject(id, { name });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['project', id] });
      setRenameModalOpen(false);
    },
  });

  // Add a blank scene
  const addSceneMutation = useMutation({
    mutationFn: async () => {
      if (!id) return;
      const existingScenes = stableScenes as Scene[];
      const lastEnd = existingScenes.length > 0
        ? Math.max(...existingScenes.map(s => s.end_time))
        : 0;
      await createScene(id, {
        name: `Scene ${existingScenes.length + 1}`,
        start_time: lastEnd,
        end_time: lastEnd + 10,
        prompt: '',
        negative_prompt: '',
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['scenes', id] });
    },
  });

  // Split a scene at a given time into two scenes
  const handleSplitScene = useCallback(async (sceneId: string, splitTime: number) => {
    if (!id) return;
    const scene = (stableScenes as Scene[]).find(s => s.id === sceneId);
    if (!scene) return;

    try {
      // Update the original scene to end at the split point
      await updateScene(id, sceneId, { end_time: splitTime });

      // Create a new scene from the split point to the original end
      const originalEnd = scene.end_time;
      await createScene(id, {
        name: `${scene.name} (split)`,
        start_time: splitTime,
        end_time: originalEnd,
        prompt: scene.prompt || '',
        negative_prompt: scene.negative_prompt || '',
      });

      queryClient.invalidateQueries({ queryKey: ['scenes', id] });
    } catch (err) {
      console.error('Failed to split scene:', err);
    }
  }, [id, stableScenes, queryClient]);

  // Drag a scene boundary to resize two adjacent scenes
  const handleBoundaryDrag = useCallback(async (leftSceneId: string, rightSceneId: string, newTime: number) => {
    if (!id) return;
    try {
      await Promise.all([
        updateScene(id, leftSceneId, { end_time: newTime }),
        updateScene(id, rightSceneId, { start_time: newTime }),
      ]);
      // Update local store immediately so UI feels snappy
      const store = useAppStore.getState();
      store.updateSceneInStore(leftSceneId, { end_time: newTime });
      store.updateSceneInStore(rightSceneId, { start_time: newTime });
      queryClient.invalidateQueries({ queryKey: ['scenes', id] });
    } catch (err) {
      console.error('Failed to drag scene boundary:', err);
    }
  }, [id, queryClient]);

  // Delete a scene and redistribute its time to the adjacent scene
  const handleDeleteScene = useCallback(async () => {
    if (!id) return;
    const store = useAppStore.getState();
    const scene = store.activeScene;
    if (!scene) return;

    if (!window.confirm(`Delete scene "${scene.name || 'Untitled'}"? This is permanent and cannot be undone.`)) return;

    const sortedScenes = [...(stableScenes as Scene[])].sort((a, b) => a.order_index - b.order_index);
    const idx = sortedScenes.findIndex(s => s.id === scene.id);
    if (idx === -1) return;

    try {
      // Expand the adjacent scene to fill the gap
      if (sortedScenes.length > 1) {
        if (idx > 0) {
          // Expand the previous scene's end_time to cover deleted scene
          const prev = sortedScenes[idx - 1];
          await updateScene(id, prev.id, { end_time: scene.end_time });
          store.updateSceneInStore(prev.id, { end_time: scene.end_time });
        } else {
          // Deleting first scene — expand the next scene's start_time
          const next = sortedScenes[idx + 1];
          await updateScene(id, next.id, { start_time: scene.start_time });
          store.updateSceneInStore(next.id, { start_time: scene.start_time });
        }
      }

      // Delete the scene
      await deleteScene(id, scene.id);
      store.removeScene(scene.id);

      // Select the nearest scene
      const remaining = sortedScenes.filter(s => s.id !== scene.id);
      if (remaining.length > 0) {
        const newActive = remaining[Math.min(idx, remaining.length - 1)];
        store.setActiveScene(newActive);
      }

      queryClient.invalidateQueries({ queryKey: ['scenes', id] });
    } catch (err) {
      console.error('Failed to delete scene:', err);
    }
  }, [id, stableScenes, queryClient]);

  // Render Preview handler
  const handleRenderPreview = useCallback(async () => {
    if (!id || previewRendering) return;
    setPreviewRendering(true);
    setPreviewUrl(null);
    try {
      await renderPreview(id);
      // Poll for completion every 2 seconds
      if (previewPollRef.current) clearInterval(previewPollRef.current);
      previewPollRef.current = setInterval(async () => {
        try {
          const res = await getPreviewStatus(id);
          const data = res.data;
          if (data.status === 'done' && data.preview_path) {
            setPreviewRendering(false);
            // Serve via the files endpoint with cache bust
            setPreviewUrl(`/api/files/${data.preview_path}?t=${Date.now()}`);
            if (previewPollRef.current) clearInterval(previewPollRef.current);
            previewPollRef.current = null;
          } else if (data.status === 'failed') {
            setPreviewRendering(false);
            console.error('Preview render failed:', data.error);
            alert(`Preview render failed: ${data.error || 'Unknown error'}`);
            if (previewPollRef.current) clearInterval(previewPollRef.current);
            previewPollRef.current = null;
          }
        } catch {
          // Ignore polling errors
        }
      }, 2000);
    } catch (err) {
      console.error('Failed to start preview render:', err);
      setPreviewRendering(false);
    }
  }, [id, previewRendering]);

  // Clean up preview polling on unmount
  useEffect(() => {
    return () => {
      if (previewPollRef.current) clearInterval(previewPollRef.current);
    };
  }, []);

  // Auto-gen status polling — starts after auto-gen is triggered
  const startAutoGenPolling = useCallback(() => {
    if (autoGenPollRef.current) clearInterval(autoGenPollRef.current);
    setAutoGenDismissed(false);

    const poll = async () => {
      if (!id) return;
      try {
        const res = await getSequentialAutoGenStatus(id);
        const d = res.data;
        setAutoGenStatus(d.status);
        setAutoGenMode(d.mode || '');
        setAutoGenCompleted(d.completed_scenes);
        setAutoGenTotal(d.total_scenes);
        setAutoGenStep(d.current_step || null);
        setAutoGenSceneName(d.current_scene_name || null);
        setAutoGenBatchRunId(d.batch_run_id || null);

        // Stop polling when terminal
        if (d.status !== 'running' && d.status !== 'pending') {
          if (autoGenPollRef.current) {
            clearInterval(autoGenPollRef.current);
            autoGenPollRef.current = null;
          }
        }
      } catch {
        // ignore transient errors
      }
    };

    poll(); // immediate first check
    autoGenPollRef.current = setInterval(poll, 3000);
  }, [id]);

  // On mount, check if auto-gen is already running for this project
  useEffect(() => {
    if (!id) return;
    const checkInitial = async () => {
      try {
        const res = await getSequentialAutoGenStatus(id);
        if (res.data.status === 'running') {
          startAutoGenPolling();
        }
      } catch { /* ignore */ }
    };
    checkInitial();
    return () => {
      if (autoGenPollRef.current) clearInterval(autoGenPollRef.current);
    };
  }, [id, startAutoGenPolling]);

  const handleAutoGenDismiss = useCallback(() => {
    setAutoGenDismissed(true);
    if (autoGenPollRef.current) {
      clearInterval(autoGenPollRef.current);
      autoGenPollRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (project) {
      setProject(project);
      // Restore scenes_locked state from project settings (persisted across restarts)
      const locked = project.settings?.scenes_locked ?? false;
      useAppStore.getState().setScenesLocked(locked);
    }
  }, [project, setProject]);

  useEffect(() => {
    setScenes(stableScenes as Scene[]);
  }, [stableScenes, setScenes]);

  useEffect(() => {
    setSections(stableSections as any[]);
  }, [stableSections, setSections]);

  useEffect(() => {
    setAssets(stableAssets as any[]);
  }, [stableAssets, setAssets]);

  // Close tools menu on click outside
  useEffect(() => {
    if (!toolsMenuOpen) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (toolsMenuRef.current && !toolsMenuRef.current.contains(e.target as Node)) {
        setToolsMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [toolsMenuOpen]);

  // Export Timeline/Lyrics Data handler
  const handleExportTimelineData = async () => {
    setToolsMenuOpen(false);
    if (!id) return;
    try {
      const sceneData = scenes ?? [];
      let lyricsData: any = null;
      try {
        const resp = await getLyrics(id);
        lyricsData = resp.data;
      } catch { /* no lyrics available */ }

      const words = lyricsData?.words ?? [];

      const exportData = sceneData.map((scene: any, idx: number) => {
        // Prefer stored lyrics from scene parameters (set by suggest_timeline)
        // These are the user's real lyrics, not Whisper's garbled transcription
        let lyrics = scene.parameters?.lyrics || '';

        // Fallback: reconstruct from Whisper word timestamps
        if (!lyrics && words.length > 0) {
          const sceneWords = words.filter((w: any) => {
            const wordStart = w.start_time ?? w.start ?? 0;
            const wordEnd = w.end_time ?? w.end ?? wordStart;
            return wordStart >= scene.start_time && wordEnd <= scene.end_time;
          });
          lyrics = sceneWords.map((w: any) => w.word ?? w.text ?? '').join(' ').trim();
        }

        return {
          scene_number: idx + 1,
          start_time: scene.start_time,
          end_time: scene.end_time,
          duration: Math.round((scene.end_time - scene.start_time) * 1000) / 1000,
          lyrics: lyrics || '(instrumental)',
        };
      });

      const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `timeline-lyrics-${id}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error('Failed to export timeline data:', err);
    }
  };

  // Download Whisper Transcription handler
  const handleDownloadWhisper = async () => {
    setToolsMenuOpen(false);
    if (!id) return;
    try {
      const resp = await getLyrics(id);
      const blob = new Blob([JSON.stringify(resp.data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `whisper-transcription-${id}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error('Failed to download whisper transcription:', err);
    }
  };

  if (!project) return (
    <div className="h-screen bg-gray-950 text-gray-100 flex items-center justify-center">
      <div className="text-gray-400">Loading project...</div>
    </div>
  );

  const getModeLabel = (mode: string) => {
    switch (mode) {
      case 'music_video':
        return 'Music Video';
      case 'narration_images':
        return 'Narration (Images)';
      case 'narration_video':
        return 'Narration (Video)';
      default:
        return mode;
    }
  };

  return (
    <div className="h-screen bg-gray-950 text-gray-100 flex flex-col overflow-hidden app-root">
      {/* Toolbar */}
      <div className="app-toolbar bg-gray-900 border-b border-gray-800 px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-4 min-w-0">
          <button
            onClick={() => navigate('/')}
            className="px-3 py-2 text-gray-400 hover:text-gray-100 transition-colors flex items-center gap-2 text-sm font-medium flex-shrink-0"
          >
            <ChevronLeft size={20} />
            <span className="hidden sm:inline">Back</span>
          </button>
          <h1 className="text-2xl font-bold truncate">{project.name}</h1>
          <span className="px-3 py-1 bg-gray-800 rounded-full text-sm text-gray-300 hidden md:inline-block flex-shrink-0">
            {getModeLabel(project.mode)}
          </span>
        </div>

        <div className="flex items-center gap-2 md:gap-4 flex-shrink-0">
          <div className="hidden md:flex gap-2 border border-gray-700 rounded-md p-1 bg-gray-800">
            <button
              className="px-3 py-1 rounded flex items-center gap-1 text-sm font-medium bg-blue-600 text-white"
            >
              <Grid3x3 size={16} />
              Scenes
            </button>
          </div>

          {stableSections.length > 0 && stableScenes.length === 0 && (
            <button
              onClick={() => createScenesMutation.mutate()}
              className="px-4 py-2 bg-green-600 hover:bg-green-700 rounded text-sm font-medium transition-colors"
              disabled={createScenesMutation.isPending}
            >
              {createScenesMutation.isPending ? 'Creating...' : 'Create Scenes'}
            </button>
          )}

          {(stableScenes as Scene[]).length > 0 && (
            <button
              onClick={() => setAutoGenOpen(true)}
              className="px-3 md:px-4 py-2 bg-purple-600 hover:bg-purple-700 rounded text-sm font-medium transition-colors flex items-center gap-2"
            >
              <Wand2 size={18} />
              <span className="hidden sm:inline">Auto Generate</span>
            </button>
          )}

          {(stableScenes as Scene[]).length > 0 && (
            <button
              onClick={handleRenderPreview}
              disabled={previewRendering}
              className={`hidden md:flex px-4 py-2 rounded text-sm font-medium transition-colors items-center gap-2 ${
                previewRendering
                  ? 'bg-gray-600 text-gray-300 cursor-wait'
                  : previewUrl
                    ? 'bg-green-600 hover:bg-green-700'
                    : 'bg-teal-600 hover:bg-teal-700'
              }`}
            >
              <MonitorPlay size={18} />
              {previewRendering ? 'Rendering...' : previewUrl ? 'Re-render Preview' : 'Render Preview'}
            </button>
          )}

          <button
            onClick={() => setExportOpen(true)}
            className="px-3 md:px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded text-sm font-medium transition-colors flex items-center gap-2"
          >
            <Download size={20} />
            <span className="hidden sm:inline">Export</span>
          </button>

          {/* Tools/Debug Menu */}
          <div className="relative" ref={toolsMenuRef}>
            <button
              onClick={() => setToolsMenuOpen(!toolsMenuOpen)}
              className="px-3 py-2 text-gray-400 hover:text-gray-100 transition-colors flex items-center"
              title="Tools"
            >
              <MoreVertical size={20} />
            </button>
            {toolsMenuOpen && (
              <div className="absolute right-0 top-full mt-1 w-64 bg-gray-800 border border-gray-700 rounded-lg shadow-xl z-50 overflow-hidden">
                <button
                  onClick={handleExportTimelineData}
                  className="w-full px-4 py-3 text-sm text-gray-200 hover:bg-gray-700 text-left transition-colors"
                >
                  Export Timeline/Lyrics Data
                </button>
                <button
                  onClick={handleDownloadWhisper}
                  className="w-full px-4 py-3 text-sm text-gray-200 hover:bg-gray-700 text-left transition-colors border-t border-gray-700"
                >
                  Download Whisper Transcription
                </button>
                <button
                  onClick={() => {
                    setNewProjectName(project.name);
                    setRenameModalOpen(true);
                    setToolsMenuOpen(false);
                  }}
                  className="w-full px-4 py-3 text-sm text-gray-200 hover:bg-gray-700 text-left transition-colors border-t border-gray-700 flex items-center gap-2"
                >
                  <Pencil size={14} />
                  Edit Project Name
                </button>
              </div>
            )}
          </div>

          <button
            onClick={() => navigate('/settings')}
            className="px-3 py-2 text-gray-400 hover:text-gray-100 transition-colors flex items-center gap-2"
          >
            <Settings size={20} />
          </button>
        </div>
      </div>

      {/* Rename Project Modal */}
      {renameModalOpen && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-gray-900 border border-gray-800 rounded-lg w-full max-w-md p-6">
            <h2 className="text-xl font-bold mb-4">Edit Project Name</h2>
            <p className="text-sm text-gray-400 mb-4">
              This changes the display name only. Project files and directories are not affected.
            </p>
            <input
              type="text"
              value={newProjectName}
              onChange={(e) => setNewProjectName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && newProjectName.trim()) {
                  renameMutation.mutate(newProjectName.trim());
                }
              }}
              className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-gray-100 focus:outline-none focus:border-blue-500 mb-4"
              autoFocus
            />
            <div className="flex justify-end gap-3">
              <button
                onClick={() => setRenameModalOpen(false)}
                className="px-4 py-2 text-sm text-gray-400 hover:text-gray-200 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  if (newProjectName.trim()) {
                    renameMutation.mutate(newProjectName.trim());
                  }
                }}
                disabled={!newProjectName.trim() || renameMutation.isPending}
                className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {renameMutation.isPending ? 'Saving...' : 'Save'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Main Content */}
      <div className="app-main-content flex-1 flex overflow-hidden gap-4 p-4">
        {/* Left Panel - Scene/Asset Management */}
        <div className={`app-left-panel w-72 bg-gray-900 border border-gray-800 rounded-lg flex flex-col overflow-hidden ${mobilePanel !== 'left' ? 'mobile-hidden' : ''}`}>
          {/* Row 1: Audio / Scenes / Assets */}
          <div className="flex gap-1 p-2 pb-1 border-b-0 border-gray-800">
            <button
              onClick={() => setSelectedPanel('audio')}
              className={`flex-1 px-2 py-1 rounded text-xs font-medium transition-colors flex items-center justify-center gap-1 ${
                selectedPanel === 'audio'
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:text-white'
              }`}
            >
              <Music size={12} />
              Audio
            </button>
            <button
              onClick={() => setSelectedPanel('scenes')}
              className={`flex-1 px-2 py-1 rounded text-xs font-medium transition-colors ${
                selectedPanel === 'scenes'
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:text-white'
              }`}
            >
              Scenes
            </button>
            <button
              onClick={() => setSelectedPanel('assets')}
              className={`flex-1 px-2 py-1 rounded text-xs font-medium transition-colors ${
                selectedPanel === 'assets'
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:text-white'
              }`}
            >
              Assets
            </button>
          </div>
          {/* Row 2: Concept / Video Flow */}
          <div className="flex gap-1 px-2 pb-2 pt-1 border-b border-gray-800">
            <button
              onClick={() => setSelectedPanel('concept')}
              className={`flex-1 px-2 py-1 rounded text-xs font-medium transition-colors flex items-center justify-center gap-1 ${
                selectedPanel === 'concept'
                  ? 'bg-purple-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:text-white'
              }`}
            >
              <Lightbulb size={12} />
              Concept
            </button>
            <button
              onClick={() => setSelectedPanel('flow')}
              className={`flex-1 px-2 py-1 rounded text-xs font-medium transition-colors flex items-center justify-center gap-1 ${
                selectedPanel === 'flow'
                  ? 'bg-purple-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:text-white'
              }`}
            >
              <GitBranch size={12} />
              Video Flow
            </button>
          </div>

          <div className="flex-1 overflow-hidden">
            {selectedPanel === 'audio' ? (
              <AudioSetup projectId={id!} projectMode={project.mode} />
            ) : selectedPanel === 'scenes' ? (
              <SceneList
                scenes={stableScenes as Scene[]}
                onAddScene={() => addSceneMutation.mutate()}
                isAdding={addSceneMutation.isPending}
              />
            ) : selectedPanel === 'concept' ? (
              <ConceptPanel projectId={id!} />
            ) : selectedPanel === 'flow' ? (
              <VideoFlowPanel projectId={id!} />
            ) : (
              <AssetManager />
            )}
          </div>
        </div>

        {/* Middle & Right Panel - Editor & Preview */}
        <div className={`app-center-panel flex-1 flex flex-col overflow-hidden gap-4 ${mobilePanel !== 'editor' ? 'mobile-hidden' : ''}`}>
          {/* Preview — visible only when editor is collapsed */}
          {editorCollapsed && (
            <div className="flex-1 min-h-0 bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
              <VideoPreview assembledPreviewUrl={previewUrl} onExitPreview={() => setPreviewUrl(null)} />
            </div>
          )}

          {/* Scene Editor — takes full space when expanded, just tab bar when collapsed */}
          <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden"
               style={{ flex: editorCollapsed ? '0 0 auto' : '1 1 0%', minHeight: editorCollapsed ? undefined : 0 }}>
            <SceneEditor collapsed={editorCollapsed} onToggleCollapse={() => setEditorCollapsed(c => !c)} />
          </div>
        </div>

        {/* Generation Panel */}
        <div className={`app-right-panel w-80 bg-gray-900 border border-gray-800 rounded-lg flex flex-col overflow-hidden ${mobilePanel !== 'queue' ? 'mobile-hidden' : ''}`}>
          <GenerationPanel />
        </div>
      </div>

      {/* Timeline - Bottom Panel (resizable) */}
      <div
        className="app-timeline bg-gray-900 border-t border-gray-800 flex flex-col overflow-hidden flex-shrink-0"
        style={{ height: `${timelineHeight}px` }}
      >
        {/* Drag handle */}
        <div
          onMouseDown={handleTimelineDragStart}
          className="h-2 flex items-center justify-center cursor-ns-resize hover:bg-gray-700/50 transition-colors flex-shrink-0 group"
        >
          <GripHorizontal size={14} className="text-gray-600 group-hover:text-gray-400" />
        </div>
        <div className="flex-1 overflow-hidden">
          <Timeline onSplitScene={handleSplitScene} onBoundaryDrag={handleBoundaryDrag} onDeleteScene={handleDeleteScene} />
        </div>
      </div>

      {/* Export Modal */}
      {exportOpen && <ExportModal projectId={id!} onClose={() => setExportOpen(false)} />}

      {/* Auto Generate Modal */}
      {autoGenOpen && <AutoGenerateModal projectId={id!} onClose={() => setAutoGenOpen(false)} onStarted={startAutoGenPolling} />}

      {/* Auto-gen Status Bar */}
      {autoGenStatus !== 'idle' && !autoGenDismissed && (
        <AutoGenStatusBar
          projectId={id!}
          batchRunId={autoGenBatchRunId}
          status={autoGenStatus}
          mode={autoGenMode}
          completedScenes={autoGenCompleted}
          totalScenes={autoGenTotal}
          currentStep={autoGenStep}
          currentSceneName={autoGenSceneName}
          onNavigateToDetail={() => {
            if (autoGenBatchRunId) navigate(`/batches/${autoGenBatchRunId}`);
          }}
          onDismiss={handleAutoGenDismiss}
          minimized={autoGenMinimized}
          onToggleMinimize={() => setAutoGenMinimized(m => !m)}
        />
      )}

      {/* Mobile Bottom Navigation */}
      <div className="mobile-nav">
        <button
          onClick={() => setMobilePanel('left')}
          className={`flex flex-col items-center gap-0.5 px-3 py-1 rounded text-xs transition-colors ${
            mobilePanel === 'left' ? 'text-blue-400' : 'text-gray-500'
          }`}
        >
          <PanelLeft size={18} />
          <span>Panels</span>
        </button>
        <button
          onClick={() => setMobilePanel('editor')}
          className={`flex flex-col items-center gap-0.5 px-3 py-1 rounded text-xs transition-colors ${
            mobilePanel === 'editor' ? 'text-blue-400' : 'text-gray-500'
          }`}
        >
          <Layers size={18} />
          <span>Editor</span>
        </button>
        <button
          onClick={() => setMobilePanel('queue')}
          className={`flex flex-col items-center gap-0.5 px-3 py-1 rounded text-xs transition-colors ${
            mobilePanel === 'queue' ? 'text-blue-400' : 'text-gray-500'
          }`}
        >
          <ListOrdered size={18} />
          <span>Queue</span>
        </button>
      </div>
    </div>
  );
}

function SceneList({ scenes, onAddScene, isAdding }: { scenes: Scene[]; onAddScene: () => void; isAdding: boolean }) {
  const { activeScene, setActiveScene, setPlaybackPosition, setIsPlaying, isPlaying, playbackPosition } = useAppStore();
  const [playingSceneId, setPlayingSceneId] = useState<string | null>(null);

  // Stop per-scene playback when playback position passes the scene end
  useEffect(() => {
    if (playingSceneId) {
      const scene = scenes.find(s => s.id === playingSceneId);
      if (scene && playbackPosition >= scene.end_time - 0.1) {
        setIsPlaying(false);
        setPlayingSceneId(null);
      }
    }
  }, [playbackPosition, playingSceneId, scenes, setIsPlaying]);

  const handlePlayScene = (e: React.MouseEvent, scene: Scene) => {
    e.stopPropagation();
    if (playingSceneId === scene.id && isPlaying) {
      // Pause
      setIsPlaying(false);
      setPlayingSceneId(null);
    } else {
      // Seek to start and play
      setPlaybackPosition(scene.start_time);
      setIsPlaying(true);
      setPlayingSceneId(scene.id);
    }
  };

  const formatTime = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, '0')}`;
  };

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Add Scene button */}
      <div className="p-2 border-b border-gray-800 flex-shrink-0">
        <button
          onClick={onAddScene}
          disabled={isAdding}
          className="w-full flex items-center justify-center gap-1.5 px-3 py-1.5 bg-green-600 hover:bg-green-700 disabled:bg-gray-700 disabled:text-gray-500 rounded text-xs font-medium transition-colors"
        >
          <Plus size={14} />
          {isAdding ? 'Adding...' : 'Add Scene'}
        </button>
      </div>

      {/* Scene list */}
      {scenes.length === 0 ? (
        <div className="p-4 text-center text-gray-400 text-sm">
          <p>No scenes yet</p>
          <p className="text-xs mt-1 text-gray-500">Process audio first, or add a scene manually</p>
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {scenes.map((scene) => {
            const isActive = activeScene?.id === scene.id;
            const isScenePlaying = playingSceneId === scene.id && isPlaying;

            return (
              <div
                key={scene.id}
                onClick={() => setActiveScene(scene)}
                className={`p-2 rounded cursor-pointer transition-colors group ${
                  isActive
                    ? 'bg-blue-900/40 border border-blue-700'
                    : 'bg-gray-800 hover:bg-gray-750 border border-transparent'
                }`}
              >
                <div className="flex items-center gap-2">
                  {/* Play button */}
                  <button
                    onClick={(e) => handlePlayScene(e, scene)}
                    className="p-1 rounded-full bg-gray-700 hover:bg-gray-600 transition-colors flex-shrink-0"
                    title={isScenePlaying ? 'Pause' : `Play ${scene.name}`}
                  >
                    {isScenePlaying ? <Pause size={10} /> : <Play size={10} className="ml-px" />}
                  </button>

                  <div className="min-w-0 flex-1">
                    <div className="text-xs font-medium truncate">{scene.name || `Scene ${scene.order_index + 1}`}</div>
                    <div className="text-[10px] text-gray-400">
                      {formatTime(scene.start_time)} — {formatTime(scene.end_time)}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function ExportModal({ projectId, onClose }: { projectId: string; onClose: () => void }) {
  const currentProject = useAppStore((s) => s.currentProject);
  const projSettings = currentProject?.settings || {};
  const [format, setFormat] = useState<'mp4' | 'webm'>('mp4');
  const [width, setWidth] = useState(projSettings.resolution_width || 1536);
  const [height, setHeight] = useState(projSettings.resolution_height || 864);
  const [fps, setFps] = useState(projSettings.project_fps || 24);
  const [quality, setQuality] = useState<'draft' | 'standard' | 'high'>('standard');
  const [transitionType, setTransitionType] = useState('crossfade');  // default from AppSettings
  const [transitionDuration, setTransitionDuration] = useState(0.5);
  const [colorCorrection, setColorCorrection] = useState(false);
  const [phase, setPhase] = useState<'config' | 'exporting' | 'done' | 'failed'>('config');
  const [progressPercent, setProgressPercent] = useState(0);
  const [currentStep, setCurrentStep] = useState('');
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const exportMutation = useMutation({
    mutationFn: async () => {
      await exportVideo(projectId, {
        format, width, height, fps, quality,
        transition_type: transitionType,
        transition_duration: transitionDuration,
        color_match_clips: colorCorrection,
      });
    },
    onSuccess: () => {
      // Start polling for progress
      setPhase('exporting');
      setCurrentStep('Starting export...');
      setProgressPercent(0);

      pollRef.current = setInterval(async () => {
        try {
          const res = await getExportStatus(projectId);
          const data = res.data;
          setProgressPercent(data.progress_percent);
          setCurrentStep(data.current_step);

          if (data.status === 'done') {
            if (pollRef.current) clearInterval(pollRef.current);
            pollRef.current = null;
            setPhase('done');
            setDownloadUrl(data.download_url || null);
          } else if (data.status === 'failed') {
            if (pollRef.current) clearInterval(pollRef.current);
            pollRef.current = null;
            setPhase('failed');
            setExportError(data.error || 'Export failed');
          }
        } catch {
          // Ignore transient poll errors
        }
      }, 1500);
    },
    onError: (err: any) => {
      setPhase('failed');
      setExportError(err?.message || 'Failed to start export');
    },
  });

  const handleExport = () => {
    setPhase('exporting');
    setCurrentStep('Initializing...');
    setProgressPercent(0);
    exportMutation.mutate();
  };

  const handleDownload = () => {
    if (downloadUrl) {
      window.open(downloadUrl, '_blank');
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-gray-900 border border-gray-800 rounded-lg w-full max-w-md p-6">
        <h2 className="text-2xl font-bold mb-6">
          {phase === 'done' ? 'Export Complete' : phase === 'failed' ? 'Export Failed' : 'Export Video'}
        </h2>

        {/* Config phase — show settings */}
        {phase === 'config' && (
          <>
            <div className="space-y-4 mb-6">
              <div>
                <label className="block text-sm font-medium mb-2">Format</label>
                <select
                  value={format}
                  onChange={(e) => setFormat(e.target.value as 'mp4' | 'webm')}
                  className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 focus:outline-none focus:border-blue-500"
                >
                  <option value="mp4">MP4</option>
                  <option value="webm">WebM</option>
                </select>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm font-medium mb-2">Width</label>
                  <input
                    type="number"
                    value={width}
                    onChange={(e) => setWidth(parseInt(e.target.value))}
                    className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 focus:outline-none focus:border-blue-500"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium mb-2">Height</label>
                  <input
                    type="number"
                    value={height}
                    onChange={(e) => setHeight(parseInt(e.target.value))}
                    className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 focus:outline-none focus:border-blue-500"
                  />
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium mb-2">FPS</label>
                <input
                  type="number"
                  value={fps}
                  onChange={(e) => setFps(parseInt(e.target.value))}
                  className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 focus:outline-none focus:border-blue-500"
                />
              </div>

              <div>
                <label className="block text-sm font-medium mb-2">Quality</label>
                <select
                  value={quality}
                  onChange={(e) => setQuality(e.target.value as 'draft' | 'standard' | 'high')}
                  className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 focus:outline-none focus:border-blue-500"
                >
                  <option value="draft">Draft</option>
                  <option value="standard">Standard</option>
                  <option value="high">High</option>
                </select>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm font-medium mb-2">Transitions</label>
                  <select
                    value={transitionType}
                    onChange={(e) => setTransitionType(e.target.value)}
                    className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 focus:outline-none focus:border-blue-500"
                  >
                    <option value="none">None (Hard Cut)</option>
                    <option value="crossfade">Crossfade</option>
                    <option value="dissolve">Dissolve</option>
                  </select>
                </div>
                {transitionType !== 'none' && (
                  <div>
                    <label className="block text-sm font-medium mb-2">Duration (s)</label>
                    <input
                      type="number"
                      value={transitionDuration}
                      step={0.1}
                      min={0.1}
                      max={2.0}
                      onChange={(e) => setTransitionDuration(parseFloat(e.target.value) || 0.5)}
                      className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 focus:outline-none focus:border-blue-500"
                    />
                  </div>
                )}
              </div>

              <div className="flex items-center gap-3">
                <input
                  type="checkbox"
                  id="colorCorrection"
                  checked={colorCorrection}
                  onChange={(e) => setColorCorrection(e.target.checked)}
                  className="w-4 h-4 rounded border-gray-600 bg-gray-800 text-blue-500 focus:ring-blue-500"
                />
                <label htmlFor="colorCorrection" className="text-sm font-medium">
                  Color Correction (match colors between adjacent clips)
                </label>
              </div>
            </div>

            <div className="flex gap-4">
              <button
                onClick={onClose}
                className="flex-1 px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded font-medium transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleExport}
                className="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded font-medium transition-colors"
              >
                Export
              </button>
            </div>
          </>
        )}

        {/* Exporting phase — show progress */}
        {phase === 'exporting' && (
          <div className="space-y-4">
            <div className="text-center mb-4">
              <div className="inline-flex items-center gap-2 text-blue-400 text-sm font-medium">
                <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
                Exporting...
              </div>
            </div>

            <div className="w-full bg-gray-700 rounded-full h-3 overflow-hidden">
              <div
                className="bg-blue-500 h-full transition-all duration-500 ease-out"
                style={{ width: `${progressPercent}%` }}
              />
            </div>

            <div className="flex justify-between text-xs text-gray-400">
              <span>{currentStep}</span>
              <span>{progressPercent}%</span>
            </div>
          </div>
        )}

        {/* Done phase — show download button */}
        {phase === 'done' && (
          <div className="space-y-4">
            <div className="text-center mb-2">
              <div className="inline-flex items-center justify-center w-12 h-12 rounded-full bg-green-500/20 mb-3">
                <svg className="w-6 h-6 text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
              </div>
              <p className="text-sm text-gray-300">Your video has been exported successfully.</p>
            </div>

            <div className="w-full bg-gray-700 rounded-full h-3 overflow-hidden">
              <div className="bg-green-500 h-full w-full" />
            </div>

            <div className="flex gap-4">
              <button
                onClick={onClose}
                className="flex-1 px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded font-medium transition-colors"
              >
                Close
              </button>
              {downloadUrl && (
                <button
                  onClick={handleDownload}
                  className="flex-1 px-4 py-2 bg-green-600 hover:bg-green-700 rounded font-medium transition-colors flex items-center justify-center gap-2"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                  </svg>
                  Download
                </button>
              )}
            </div>
          </div>
        )}

        {/* Failed phase — show error */}
        {phase === 'failed' && (
          <div className="space-y-4">
            <div className="text-center mb-2">
              <div className="inline-flex items-center justify-center w-12 h-12 rounded-full bg-red-500/20 mb-3">
                <svg className="w-6 h-6 text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </div>
              <p className="text-sm text-red-400">{exportError || 'Export failed'}</p>
            </div>

            <div className="flex gap-4">
              <button
                onClick={onClose}
                className="flex-1 px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded font-medium transition-colors"
              >
                Close
              </button>
              <button
                onClick={() => { setPhase('config'); setExportError(null); }}
                className="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded font-medium transition-colors"
              >
                Try Again
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}


type AutoGenMode = 'all_images' | 'empty_only' | 'enhanced_all' | 'enhanced_missing';

const AUTO_GEN_OPTIONS: { value: AutoGenMode; label: string; description: string }[] = [
  {
    value: 'enhanced_all',
    label: 'Full Pipeline (All Scenes)',
    description:
      'Generate video flow, LLM-enhance prompts, then queue first frames, last frames, and videos for every scene.',
  },
  {
    value: 'enhanced_missing',
    label: 'Full Pipeline (Missing Only)',
    description:
      'LLM-enhance and generate only what\'s missing per scene — skips scenes that already have images/videos.',
  },
  {
    value: 'all_images',
    label: 'Images Only (All Scenes)',
    description: 'Queue first-frame image generation for every scene in order.',
  },
  {
    value: 'empty_only',
    label: 'Images Only (Empty Scenes)',
    description: 'Only generate images for scenes that don\'t have a chosen preview yet.',
  },
];

function AutoGenerateModal({ projectId, onClose, onStarted }: { projectId: string; onClose: () => void; onStarted: () => void }) {
  const navigate = useNavigate();
  const [mode, setMode] = useState<AutoGenMode>('enhanced_all');
  const [isRunning, setIsRunning] = useState(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Advanced options
  const [twoPass, setTwoPass] = useState(true);
  const [useStoryFlow, setUseStoryFlow] = useState(true);
  const [lipsyncEnabled, setLipsyncEnabled] = useState(true);
  const [vocalsOnlyForLipsync, setVocalsOnlyForLipsync] = useState(false);
  const [skipAudioMux, setSkipAudioMux] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const handleQueue = async () => {
    setIsRunning(true);
    setError(null);
    setStatusMsg(null);

    try {
      // For enhanced_all mode, generate video flow first
      if (mode === 'enhanced_all') {
        setStatusMsg('Generating video flow ideas...');
        try {
          await generateVideoFlow(projectId);
        } catch (e) {
          console.warn('Video flow generation failed, continuing with existing flow data:', e);
        }
      }

      setStatusMsg(
        mode.startsWith('enhanced')
          ? 'Enhancing prompts and queuing jobs...'
          : 'Queuing generation jobs...'
      );

      await startSequentialAutoGen(
        projectId,
        mode,
        false, // overrideFullSet
        false, // vocalsOnlyAudio
        skipAudioMux,
        twoPass,
        useStoryFlow,
        lipsyncEnabled,
        vocalsOnlyForLipsync,
      );

      setStatusMsg('Auto-gen started! Check the status bar below or view batch details.');
      onStarted(); // start polling in AppLayout

      // Close after a brief pause
      setTimeout(() => onClose(), 1200);
    } catch (e: any) {
      const detail = e?.response?.data?.detail || e?.message || 'Auto-generation failed';
      setError(detail);
      setIsRunning(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-[9999] p-4">
      <div className="bg-gray-900 border border-gray-700 rounded-xl w-full max-w-lg max-h-[90vh] overflow-y-auto p-6">
        <h2 className="text-xl font-bold mb-1 text-gray-100">Auto Generate</h2>
        <p className="text-sm text-gray-400 mb-4">
          Select a mode, configure options, then start. Progress will show in a status bar below.
        </p>

        {/* Mode selection */}
        <div className="flex flex-col gap-2 mb-4">
          {AUTO_GEN_OPTIONS.map((opt) => (
            <label
              key={opt.value}
              className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-all ${
                mode === opt.value
                  ? 'bg-purple-900/30 border-purple-500/50'
                  : 'bg-gray-800 border-gray-700 hover:border-gray-600'
              } ${isRunning ? 'opacity-60 cursor-default' : ''}`}
            >
              <input
                type="radio"
                name="auto_gen_mode"
                value={opt.value}
                checked={mode === opt.value}
                onChange={() => setMode(opt.value)}
                disabled={isRunning}
                className="mt-0.5 accent-purple-500"
              />
              <div>
                <div className="font-semibold text-sm text-gray-200">{opt.label}</div>
                <div className="text-xs text-gray-500 mt-0.5 leading-relaxed">{opt.description}</div>
              </div>
            </label>
          ))}
        </div>

        {/* Advanced Options Toggle */}
        <button
          onClick={() => setShowAdvanced(a => !a)}
          className="text-xs text-purple-400 hover:text-purple-300 mb-3 flex items-center gap-1 transition-colors"
        >
          {showAdvanced ? '▼' : '▶'} Advanced Options
        </button>

        {showAdvanced && (
          <div className="mb-4 p-3 bg-gray-800/50 border border-gray-700 rounded-lg space-y-2.5">
            <label className="flex items-center gap-2.5 cursor-pointer">
              <input type="checkbox" checked={twoPass} onChange={e => setTwoPass(e.target.checked)} disabled={isRunning}
                className="w-4 h-4 rounded border-gray-600 bg-gray-700 accent-purple-500" />
              <div>
                <span className="text-sm text-gray-200">Two-Pass Image Generation</span>
                <p className="text-xs text-gray-500">Pass 1: scene composition, Pass 2: character compositing</p>
              </div>
            </label>

            <label className="flex items-center gap-2.5 cursor-pointer">
              <input type="checkbox" checked={useStoryFlow} onChange={e => setUseStoryFlow(e.target.checked)} disabled={isRunning}
                className="w-4 h-4 rounded border-gray-600 bg-gray-700 accent-purple-500" />
              <div>
                <span className="text-sm text-gray-200">Use Story Flow</span>
                <p className="text-xs text-gray-500">Incorporate video flow ideas into prompt generation</p>
              </div>
            </label>

            <label className="flex items-center gap-2.5 cursor-pointer">
              <input type="checkbox" checked={lipsyncEnabled} onChange={e => setLipsyncEnabled(e.target.checked)} disabled={isRunning}
                className="w-4 h-4 rounded border-gray-600 bg-gray-700 accent-purple-500" />
              <div>
                <span className="text-sm text-gray-200">Lipsync-Aware Prompts</span>
                <p className="text-xs text-gray-500">Tell the LLM to include singing/speaking cues</p>
              </div>
            </label>

            {lipsyncEnabled && (
              <label className="flex items-center gap-2.5 cursor-pointer ml-6">
                <input type="checkbox" checked={vocalsOnlyForLipsync} onChange={e => setVocalsOnlyForLipsync(e.target.checked)} disabled={isRunning}
                  className="w-4 h-4 rounded border-gray-600 bg-gray-700 accent-purple-500" />
                <div>
                  <span className="text-sm text-gray-200">Vocals-Only Audio for Lipsync</span>
                  <p className="text-xs text-gray-500">Use isolated vocal stem instead of full mix</p>
                </div>
              </label>
            )}

            <label className="flex items-center gap-2.5 cursor-pointer">
              <input type="checkbox" checked={skipAudioMux} onChange={e => setSkipAudioMux(e.target.checked)} disabled={isRunning}
                className="w-4 h-4 rounded border-gray-600 bg-gray-700 accent-purple-500" />
              <div>
                <span className="text-sm text-gray-200">Skip Audio Mux</span>
                <p className="text-xs text-gray-500">Don't embed audio in generated video files</p>
              </div>
            </label>
          </div>
        )}

        {/* Status / Error messages */}
        {statusMsg && (
          <div className={`p-2.5 mb-3 rounded text-sm ${error ? 'bg-red-950 border border-red-900 text-red-300' : 'bg-green-950 border border-green-900 text-green-300'}`}>
            {statusMsg}
          </div>
        )}
        {error && (
          <div className="p-2.5 mb-3 rounded text-sm bg-red-950 border border-red-900 text-red-300">
            {error}
          </div>
        )}

        {/* Actions */}
        <div className="flex gap-3">
          <button
            onClick={onClose}
            disabled={isRunning}
            className="flex-1 px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Cancel
          </button>
          <button
            onClick={handleQueue}
            disabled={isRunning}
            className="flex-1 px-4 py-2 bg-purple-600 hover:bg-purple-700 rounded-lg font-semibold text-white transition-colors disabled:opacity-70 disabled:cursor-not-allowed"
          >
            {isRunning ? 'Starting...' : 'Start Auto Gen'}
          </button>
        </div>

        {/* View Batches link */}
        <div className="mt-3 text-center">
          <button
            onClick={() => { onClose(); navigate('/batches'); }}
            className="text-xs text-gray-500 hover:text-purple-400 transition-colors"
          >
            View all batch runs →
          </button>
        </div>
      </div>
    </div>
  );
}
