import { useEffect, useState, useCallback, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Settings, Download, ChevronLeft, Eye, Grid3x3, Music, Plus, Play, Pause, GripHorizontal, Lightbulb, GitBranch, Wand2, MonitorPlay } from 'lucide-react';
import { getProject, getScenes, getSections, getAssets, exportVideo, getExportStatus, createScenesFromSections, createScene, updateScene, deleteScene, autoGenerate, generateVideoFlow, renderPreview, getPreviewStatus } from '@/api/client';
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
  const isDraggingTimeline = useRef(false);
  const dragStartY = useRef(0);
  const dragStartHeight = useRef(0);

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

  const { setProject, setScenes, setSections, setAssets, viewMode, setViewMode } =
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
    <div className="h-screen bg-gray-950 text-gray-100 flex flex-col overflow-hidden">
      {/* Toolbar */}
      <div className="bg-gray-900 border-b border-gray-800 px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate('/')}
            className="px-3 py-2 text-gray-400 hover:text-gray-100 transition-colors flex items-center gap-2 text-sm font-medium"
          >
            <ChevronLeft size={20} />
            Back
          </button>
          <h1 className="text-2xl font-bold">{project.name}</h1>
          <span className="px-3 py-1 bg-gray-800 rounded-full text-sm text-gray-300">
            {getModeLabel(project.mode)}
          </span>
        </div>

        <div className="flex items-center gap-4">
          <div className="flex gap-2 border border-gray-700 rounded-md p-1 bg-gray-800">
            <button
              onClick={() => setViewMode('scenes')}
              className={`px-3 py-1 rounded flex items-center gap-1 text-sm font-medium transition-colors ${
                viewMode === 'scenes'
                  ? 'bg-blue-600 text-white'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              <Grid3x3 size={16} />
              Scenes
            </button>
            <button
              onClick={() => setViewMode('sections')}
              className={`px-3 py-1 rounded flex items-center gap-1 text-sm font-medium transition-colors ${
                viewMode === 'sections'
                  ? 'bg-blue-600 text-white'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              <Eye size={16} />
              Sections
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

          {viewMode === 'scenes' && (stableScenes as Scene[]).length > 0 && (
            <button
              onClick={() => setAutoGenOpen(true)}
              className="px-4 py-2 bg-purple-600 hover:bg-purple-700 rounded text-sm font-medium transition-colors flex items-center gap-2"
            >
              <Wand2 size={18} />
              Auto Generate
            </button>
          )}

          {viewMode === 'scenes' && (stableScenes as Scene[]).length > 0 && (
            <button
              onClick={handleRenderPreview}
              disabled={previewRendering}
              className={`px-4 py-2 rounded text-sm font-medium transition-colors flex items-center gap-2 ${
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
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded text-sm font-medium transition-colors flex items-center gap-2"
          >
            <Download size={20} />
            Export
          </button>

          <button
            onClick={() => navigate('/settings')}
            className="px-3 py-2 text-gray-400 hover:text-gray-100 transition-colors flex items-center gap-2"
          >
            <Settings size={20} />
          </button>
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 flex overflow-hidden gap-4 p-4">
        {/* Left Panel - Scene/Asset Management */}
        <div className="w-72 bg-gray-900 border border-gray-800 rounded-lg flex flex-col overflow-hidden">
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
        <div className="flex-1 flex flex-col overflow-hidden gap-4">
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
        <div className="w-80 bg-gray-900 border border-gray-800 rounded-lg flex flex-col overflow-hidden">
          <GenerationPanel />
        </div>
      </div>

      {/* Timeline - Bottom Panel (resizable) */}
      <div
        className="bg-gray-900 border-t border-gray-800 flex flex-col overflow-hidden flex-shrink-0"
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
      {autoGenOpen && <AutoGenerateModal projectId={id!} onClose={() => setAutoGenOpen(false)} />}
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
    value: 'all_images',
    label: 'Generate All Scene Images',
    description: 'Queue first-frame image generation for every scene in order.',
  },
  {
    value: 'empty_only',
    label: 'Generate Only Empty Scenes',
    description: 'Only generate images for scenes that don\'t have a chosen preview yet.',
  },
  {
    value: 'enhanced_all',
    label: 'Enhanced Generate All',
    description:
      'Generate video flow ideas, LLM-enhance all prompts, then queue first frames, last frames, and videos for every scene. Full pipeline.',
  },
  {
    value: 'enhanced_missing',
    label: 'Enhanced Generate Only Missing',
    description:
      'LLM-enhance and generate only what\'s missing per scene — e.g. a missing last frame or video — using existing scene data for context.',
  },
];

function AutoGenerateModal({ projectId, onClose }: { projectId: string; onClose: () => void }) {
  const [mode, setMode] = useState<AutoGenMode>('all_images');
  const [isRunning, setIsRunning] = useState(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

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
          ? 'Enhancing prompts and queuing jobs (this may take a minute)...'
          : 'Queuing generation jobs...'
      );

      const response = await autoGenerate(projectId, mode);
      const { jobs_created, enhanced_count, skipped_count } = response.data;

      const parts: string[] = [`${jobs_created} jobs queued`];
      if (enhanced_count > 0) parts.push(`${enhanced_count} prompts enhanced`);
      if (skipped_count > 0) parts.push(`${skipped_count} scenes skipped (already complete)`);

      setStatusMsg(parts.join(', '));

      // Close after a brief pause so user sees the result
      setTimeout(() => onClose(), 1500);
    } catch (e: any) {
      const detail = e?.response?.data?.detail || e?.message || 'Auto-generation failed';
      setError(detail);
      setIsRunning(false);
    }
  };

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 9999 }}>
      <div style={{ background: '#111827', border: '1px solid #374151', borderRadius: '0.75rem', width: '100%', maxWidth: '520px', padding: '1.5rem' }}>
        <h2 style={{ fontSize: '1.5rem', fontWeight: 700, marginBottom: '0.5rem', color: '#f3f4f6' }}>
          Auto Generate
        </h2>
        <p style={{ fontSize: '0.875rem', color: '#9ca3af', marginBottom: '1.25rem' }}>
          Select a generation mode and click Queue to populate the generation queue.
        </p>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', marginBottom: '1.5rem' }}>
          {AUTO_GEN_OPTIONS.map((opt) => (
            <label
              key={opt.value}
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: '0.75rem',
                padding: '0.75rem 1rem',
                background: mode === opt.value ? '#1e1b4b' : '#1f2937',
                border: `1px solid ${mode === opt.value ? '#6366f1' : '#374151'}`,
                borderRadius: '0.5rem',
                cursor: isRunning ? 'default' : 'pointer',
                transition: 'all 150ms',
              }}
            >
              <input
                type="radio"
                name="auto_gen_mode"
                value={opt.value}
                checked={mode === opt.value}
                onChange={() => setMode(opt.value)}
                disabled={isRunning}
                style={{ marginTop: '0.2rem', accentColor: '#818cf8' }}
              />
              <div>
                <div style={{ fontWeight: 600, color: '#e5e7eb', fontSize: '0.9rem' }}>{opt.label}</div>
                <div style={{ color: '#9ca3af', fontSize: '0.8rem', marginTop: '0.2rem', lineHeight: 1.4 }}>
                  {opt.description}
                </div>
              </div>
            </label>
          ))}
        </div>

        {statusMsg && (
          <div style={{
            padding: '0.5rem 0.75rem',
            marginBottom: '1rem',
            borderRadius: '0.375rem',
            fontSize: '0.85rem',
            background: error ? '#450a0a' : '#052e16',
            border: `1px solid ${error ? '#7f1d1d' : '#14532d'}`,
            color: error ? '#fca5a5' : '#86efac',
          }}>
            {statusMsg}
          </div>
        )}
        {error && (
          <div style={{
            padding: '0.5rem 0.75rem',
            marginBottom: '1rem',
            borderRadius: '0.375rem',
            fontSize: '0.85rem',
            background: '#450a0a',
            border: '1px solid #7f1d1d',
            color: '#fca5a5',
          }}>
            {error}
          </div>
        )}

        <div style={{ display: 'flex', gap: '0.75rem' }}>
          <button
            onClick={onClose}
            disabled={isRunning}
            style={{
              flex: 1,
              padding: '0.5rem 1rem',
              background: '#1f2937',
              border: 'none',
              borderRadius: '0.375rem',
              color: '#e5e7eb',
              fontWeight: 500,
              cursor: isRunning ? 'not-allowed' : 'pointer',
              opacity: isRunning ? 0.5 : 1,
            }}
          >
            Cancel
          </button>
          <button
            onClick={handleQueue}
            disabled={isRunning}
            style={{
              flex: 1,
              padding: '0.5rem 1rem',
              background: isRunning ? '#6b21a8' : '#7c3aed',
              border: 'none',
              borderRadius: '0.375rem',
              color: '#ffffff',
              fontWeight: 600,
              cursor: isRunning ? 'not-allowed' : 'pointer',
              opacity: isRunning ? 0.7 : 1,
            }}
          >
            {isRunning ? 'Working...' : 'Queue'}
          </button>
        </div>
      </div>
    </div>
  );
}
