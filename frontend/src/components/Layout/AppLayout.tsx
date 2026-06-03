import { useEffect, useState, useCallback, useRef, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Settings, Download, ChevronLeft, Grid3x3, Music, Plus, Play, Pause, GripHorizontal, Lightbulb, GitBranch, Wand2, MonitorPlay, MoreVertical, Pencil, Layers, ListOrdered, PanelLeft, Minimize2, Loader2, CheckCircle, XCircle, Sparkles, Captions, ChevronDown, ChevronUp, Film } from 'lucide-react';
import { getProject, getScenes, getSections, getAssets, exportVideo, getExportStatus, cancelExport, resumeExport, scanExport, recoverExport, createScenesFromSections, createScene, updateScene, deleteScene, startSequentialAutoGen, cancelSequentialAutoGen, generateVideoFlow, renderPreview, getPreviewStatus, getLyrics, updateProject, getSequentialAutoGenStatus, rerunWhisper, getBackingTracks, listExports, deleteExportFile } from '@/api/client';
import type { ExportFileInfo } from '@/api/client';
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
import BackingTrackTimeline from '@/components/BackingTrackTimeline/BackingTrackTimeline';
import AssetGeneratorModal from '@/components/AssetGenerator/AssetGeneratorModal';
import ErrorBoundary from '@/components/ErrorBoundary';
import { useBackingTrackPlayer } from '@/hooks/useBackingTrackPlayer';
import type { BackingTrackData } from '@/hooks/useBackingTrackPlayer';
import { parseBackendDate } from '@/utils/time';
import ChapterOverlay from '@/components/Chapters/ChapterOverlay';
// ChapterBreadcrumb is rolled into ChapterScopeBanner now
// ChapterTree is now wrapped inside ChapterDirectionPanel
import ChapterPicker from '@/components/Chapters/ChapterPicker';
import ChapterScopeBanner from '@/components/Chapters/ChapterScopeBanner';
import ChapterDirectionPanel from '@/components/Chapters/ChapterDirectionPanel';

const EMPTY_ARRAY: never[] = [];

export default function AppLayout() {
  const { id, chapterShortCode } = useParams<{ id: string; chapterShortCode?: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [selectedPanel, setSelectedPanel] = useState<'audio' | 'scenes' | 'assets' | 'concept' | 'flow' | 'chapters'>('audio');
  const [exportOpen, setExportOpen] = useState(false);
  const [exportGalleryOpen, setExportGalleryOpen] = useState(false);
  // autoGenOpen lives in the Zustand store so both the header button
  // (here) and the Timeline toolbar button open the SAME modal (the one
  // wired to AutoGenStatusBar for minimize-to-bottom-of-screen).
  const autoGenOpen = useAppStore((s) => s.autoGenOpen);
  const setAutoGenOpen = useAppStore((s) => s.setAutoGenOpen);
  const [assetGenOpen, setAssetGenOpen] = useState(false);
  const [editorCollapsed, setEditorCollapsed] = useState(false);
  const [previewRendering, setPreviewRendering] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const previewPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [timelineHeight, setTimelineHeight] = useState(256); // default h-64 = 256px
  const [activeTimeline, setActiveTimeline] = useState<'scenes' | 'backing'>('scenes');
  const [toolsMenuOpen, setToolsMenuOpen] = useState(false);
  const toolsMenuRef = useRef<HTMLDivElement>(null);
  const [renameModalOpen, setRenameModalOpen] = useState(false);
  const [newProjectName, setNewProjectName] = useState('');
  const isDraggingTimeline = useRef(false);
  const dragStartY = useRef(0);
  const dragStartHeight = useRef(0);
  // Mobile panel visibility
  const [mobilePanel, setMobilePanel] = useState<'editor' | 'left' | 'queue'>('editor');

  // ── Chapter awareness ─────────────────────────────────────────────
  // When the URL is /project/:id/c/:shortcode the layout enters
  // "chapter focus" mode: timeline, scene list, and auto-gen scope are
  // filtered to that chapter (or its subtree).  See BLUEPRINT_CHAPTERS_v1.md.
  const [chapterTree, setChapterTree] = useState<import('@/types').ChapterTreeNode[]>([]);
  const [chapterRebuilding, setChapterRebuilding] = useState<boolean>(false);

  const reloadChapters = useCallback(async () => {
    if (!id) {
      console.info('[chapters] reloadChapters: no project id yet');
      return;
    }
    console.info('[chapters] reloadChapters: GET /api/projects/' + id + '/chapters/');
    try {
      const mod = await import('@/api/client');
      const resp = await mod.getChapters(id);
      const payload = resp.data;
      const count = payload?.chapter_count ?? (payload?.chapters?.length ?? 0);
      console.info('[chapters] reloadChapters OK:', count, 'chapter(s), top-level:', payload?.chapters?.length ?? 0);
      setChapterTree(payload?.chapters || []);
    } catch (err: any) {
      const msg = err?.response?.status
        ? `HTTP ${err.response.status}: ${err.response.data?.detail || err.message}`
        : (err?.message || String(err));
      console.warn('[chapters] reloadChapters FAILED:', msg, err);
    }
  }, [id]);

  useEffect(() => {
    console.info('[chapters] mount/id-change effect — id =', id);
    reloadChapters();
  }, [reloadChapters]);

  // ── Cross-component chapter refresh bus ──────────────────────────
  // Suggest Timeline (and other actions that change the chapter tree)
  // dispatch `rbmn:chapters:invalidate` on window so this listener
  // refetches.  No prop-drilling required.
  useEffect(() => {
    if (!id) return;
    const handler = (ev: Event) => {
      const det = (ev as CustomEvent).detail as { projectId?: string } | undefined;
      // If a project id is provided, only refetch when it matches ours.
      if (det?.projectId && det.projectId !== id) return;
      reloadChapters();
    };
    window.addEventListener('rbmn:chapters:invalidate', handler);
    return () => window.removeEventListener('rbmn:chapters:invalidate', handler);
  }, [id, reloadChapters]);

  // Flatten chapter tree → map by short_code (for lookup)
  const chapterByShortCode = useMemo(() => {
    const m = new Map<string, import('@/types').ChapterTreeNode>();
    const walk = (nodes: import('@/types').ChapterTreeNode[]) => {
      for (const n of nodes) {
        m.set(n.short_code, n);
        if (n.children?.length) walk(n.children);
      }
    };
    walk(chapterTree);
    return m;
  }, [chapterTree]);

  // Active chapter (current drill-down target, if any)
  const activeChapter = chapterShortCode
    ? chapterByShortCode.get(chapterShortCode) ?? null
    : null;

  // (chapter scene-id filter will land in Phase 1.5 once Timeline /
  //  SceneList accept a scope prop — see BLUEPRINT_CHAPTERS_v1.md §6.4)

  // activeChapterAncestry is now rendered inside ChapterScopeBanner

  const handleReparseChapters = useCallback(async (forceAuto: boolean = false) => {
    if (!id) return;
    setChapterRebuilding(true);
    try {
      const { data } = await import('@/api/client').then(m => m.reparseChapters(id, forceAuto));
      setChapterTree(data.chapters || []);
    } catch (err) {
      console.error('[chapters] reparse failed:', err);
    } finally {
      setChapterRebuilding(false);
    }
  }, [id]);

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

  const setProject = useAppStore(s => s.setProject);
  const setScenes = useAppStore(s => s.setScenes);
  const setSections = useAppStore(s => s.setSections);
  const setAssets = useAppStore(s => s.setAssets);

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
  // Fetch lyrics for subtitle preview (narration modes)
  const isNarrationProject = project?.mode === 'narration_images' || project?.mode === 'narration_video';
  const { data: lyricsData } = useQuery({
    queryKey: ['lyrics', id],
    queryFn: async () => {
      try {
        const resp = await getLyrics(id!);
        // Backend returns start_time/end_time but frontend WordTimestamp uses start/end
        // Normalize here so SubtitleOverlay and all consumers get the right fields
        const data = resp.data as any;
        if (data?.words && Array.isArray(data.words)) {
          data.words = data.words.map((w: any) => ({
            word: String(w?.word || ''),
            start: Number(w?.start ?? w?.start_time ?? 0) || 0,
            end: Number(w?.end ?? w?.end_time ?? 0) || 0,
            score: w?.score,
            block: w?.block,  // SRT block index for proper subtitle grouping
          }));
          // Log srt_blocks if present
          if (data.srt_blocks && Array.isArray(data.srt_blocks) && data.srt_blocks.length > 0) {
            console.debug(`[Lyrics] ${data.srt_blocks.length} SRT blocks. First: "${data.srt_blocks[0]?.text}" [${data.srt_blocks[0]?.start}-${data.srt_blocks[0]?.end}]`);
          }
          console.debug(`[Lyrics] Loaded ${data.words.length} words. First 3:`, data.words.slice(0, 3));
        } else {
          console.warn('[Lyrics] No words in lyrics response:', data);
          if (data) data.words = [];
        }
        return data;
      } catch (e) {
        console.error('[Lyrics] Failed to load lyrics:', e);
        return { text: '', words: [], initial_text: '', srt_blocks: [] };
      }
    },
    enabled: !!id && isNarrationProject,
  });
  const subtitleWords = lyricsData?.words ?? [];
  const srtBlocks = lyricsData?.srt_blocks ?? [];

  // Backing tracks mini-preview data (lightweight query for the inactive strip)
  const { data: backingTracksRaw } = useQuery({
    queryKey: ['backingTracks', id],
    queryFn: () => getBackingTracks(id!),
    enabled: !!id && isNarrationProject,
  });
  const backingTracksForMini: any[] = backingTracksRaw?.data || [];

  // ── Backing track browser playback via Web Audio API ──
  // Map raw API data to the shape expected by the player hook
  const backingTrackPlayerData: BackingTrackData[] = useMemo(() => (backingTracksForMini || []).map((t: any) => ({
    id: String(t.id),
    rel_path: String(t.rel_path || ''),
    start_time: Number(t.start_time) || 0,
    end_time: Number(t.end_time) || 0,
    trim_start: Number(t.trim_start) || 0,
    trim_end: Number(t.trim_end) || 0,
    volume_db: Number(t.volume_db) || 0,
    fade_in_sec: Number(t.fade_in_sec) || 0,
    fade_out_sec: Number(t.fade_out_sec) || 0,
  })), [backingTracksForMini]);
  useBackingTrackPlayer(isNarrationProject ? backingTrackPlayerData : EMPTY_ARRAY, id || '');

  // Subtitle preview settings — persisted to project.settings
  const [previewSubEnabled, setPreviewSubEnabled] = useState(false);
  const [previewSubFont, setPreviewSubFont] = useState('Arial');
  const [previewSubSize, setPreviewSubSize] = useState(24);
  const [previewSubColor, setPreviewSubColor] = useState('#FFFFFF');
  const [previewSubPosition, setPreviewSubPosition] = useState('bottom');
  const [previewSubOutline, setPreviewSubOutline] = useState(2);
  const [previewSubBold, setPreviewSubBold] = useState(false);
  const [subSettingsOpen, setSubSettingsOpen] = useState(false);
  const [subSettingsDirty, setSubSettingsDirty] = useState(false);
  const [subSettingsSaved, setSubSettingsSaved] = useState(false);

  // Load subtitle settings from project.settings when project loads
  // Track whether we've initialized from this project to avoid overwriting user edits on every refetch
  const subSettingsInitRef = useRef<string | null>(null);
  useEffect(() => {
    if (project?.settings && project?.id && subSettingsInitRef.current !== project.id) {
      subSettingsInitRef.current = project.id;
      const s = project.settings;
      if (s.subtitle_enabled !== undefined) setPreviewSubEnabled(s.subtitle_enabled);
      if (s.subtitle_font) setPreviewSubFont(s.subtitle_font);
      if (s.subtitle_size !== undefined) setPreviewSubSize(s.subtitle_size);
      if (s.subtitle_color) setPreviewSubColor(s.subtitle_color);
      if (s.subtitle_position) setPreviewSubPosition(s.subtitle_position);
      if (s.subtitle_outline !== undefined) setPreviewSubOutline(s.subtitle_outline);
      if (s.subtitle_bold !== undefined) setPreviewSubBold(s.subtitle_bold);
    }
  }, [project?.id, project?.settings]); // eslint-disable-line react-hooks/exhaustive-deps

  // Save subtitle settings to project.settings with debounce
  // Use a ref to always access the latest settings without stale closures
  const latestSettingsRef = useRef<Record<string, any>>(project?.settings || {});
  useEffect(() => { latestSettingsRef.current = project?.settings || {}; }, [project?.settings]);

  const subSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saveSubtitleSettings = useCallback((updates: Record<string, any>) => {
    if (!id) return;
    if (subSaveTimer.current) clearTimeout(subSaveTimer.current);
    subSaveTimer.current = setTimeout(async () => {
      try {
        const merged = {
          ...latestSettingsRef.current,
          ...updates,
        };
        await updateProject(id, { settings: merged });
        // Update the ref immediately so subsequent saves don't use stale data
        latestSettingsRef.current = merged;
        // Invalidate the project query so React Query cache stays fresh
        queryClient.invalidateQueries({ queryKey: ['project', id] });
      } catch (e) {
        // Silently fail — settings are non-critical
      }
    }, 500);
  }, [id, queryClient]); // eslint-disable-line react-hooks/exhaustive-deps

  // Initialize Zustand store with saved mixer volumes from project settings.
  // This ensures playback hooks (WaveSurfer + Web Audio) use the correct volumes
  // even if BackingTrackTimeline hasn't mounted yet (e.g. user is on Scene tab).
  useEffect(() => {
    if (project?.settings) {
      const s = project.settings;
      const nv = s.narration_volume ?? 1.0;
      const bv = s.backing_volume ?? 1.0;
      useAppStore.getState().setNarrationVolume(nv);
      useAppStore.getState().setBackingMasterVolume(bv);
    }
  }, [project?.id, project?.settings]);

  // Memoize subtitle style to prevent unnecessary VideoPreview re-renders
  const memoizedSubtitleStyle = useMemo(() => previewSubEnabled ? {
    font: previewSubFont,
    size: previewSubSize,
    color: previewSubColor,
    position: previewSubPosition,
    outline: previewSubOutline,
    bold: previewSubBold,
  } : undefined, [previewSubEnabled, previewSubFont, previewSubSize, previewSubColor, previewSubPosition, previewSubOutline, previewSubBold]);

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

  // ── Push chapter scope into the Zustand store ─────────────────────
  // Timeline / SceneList read store.chapterScope to filter what they
  // render.  Null when on the main project URL.
  const setChapterScopeAction = useAppStore((s) => s.setChapterScope);
  useEffect(() => {
    if (!activeChapter || !id) {
      setChapterScopeAction(null);
      return;
    }
    // Collect every scene id belonging to this chapter or any descendant
    const sceneIds = new Set<string>();
    const walk = (n: import('@/types').ChapterTreeNode) => {
      n.scene_ids?.forEach((sid) => sceneIds.add(sid));
      n.children?.forEach(walk);
    };
    walk(activeChapter);
    setChapterScopeAction({
      chapterId: activeChapter.id,
      shortCode: activeChapter.short_code,
      name: activeChapter.name,
      color: activeChapter.color,
      sceneIds,
      startTime: activeChapter.start_time,
      endTime: activeChapter.end_time,
    });
    return () => setChapterScopeAction(null);
  }, [activeChapter, id, setChapterScopeAction]);

  useEffect(() => {
    setSections(stableSections as any[]);
  }, [stableSections, setSections]);

  useEffect(() => {
    setAssets(stableAssets as any[]);
  }, [stableAssets, setAssets]);

  // Auto-select scene 1 when scenes load and none is active
  useEffect(() => {
    const store = useAppStore.getState();
    if (stableScenes.length > 0 && !store.activeScene) {
      // Sort by scene_index to get the first scene
      const sorted = [...stableScenes].sort((a: any, b: any) => (a.scene_index ?? 0) - (b.scene_index ?? 0));
      store.setActiveScene(sorted[0] as Scene);
    }
  }, [stableScenes]);

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
            autoGenStatus === 'running' ? (
              <button
                onClick={() => setAutoGenOpen(true)}
                className="px-3 md:px-4 py-2 bg-purple-600/80 hover:bg-purple-600 rounded text-sm font-medium transition-colors flex items-center gap-2 border border-purple-500/40"
              >
                <Loader2 size={16} className="animate-spin" />
                <span className="hidden sm:inline font-mono text-xs">
                  {autoGenTotal > 0 ? `${autoGenCompleted}/${autoGenTotal}` : 'Running...'}
                </span>
                <span className="sm:hidden font-mono text-xs">
                  {autoGenTotal > 0 ? `${Math.round((autoGenCompleted / autoGenTotal) * 100)}%` : '...'}
                </span>
              </button>
            ) : (
              <button
                onClick={() => setAutoGenOpen(true)}
                className="px-3 md:px-4 py-2 bg-purple-600 hover:bg-purple-700 rounded text-sm font-medium transition-colors flex items-center gap-2"
              >
                <Wand2 size={18} />
                <span className="hidden sm:inline">Auto Generate</span>
              </button>
            )
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
            onClick={() => setAssetGenOpen(true)}
            className="px-3 md:px-4 py-2 bg-amber-600 hover:bg-amber-700 rounded text-sm font-medium transition-colors flex items-center gap-2"
          >
            <Sparkles size={18} />
            <span className="hidden sm:inline">Asset Generator</span>
          </button>

          <button
            onClick={() => setExportGalleryOpen(true)}
            className="px-3 md:px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm font-medium transition-colors flex items-center gap-2"
            title="View past exports"
          >
            <Film size={18} />
            <span className="hidden sm:inline">Gallery</span>
          </button>

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
          {/* Row 2: Concept / Story Flow (narration) or Video Flow (music) */}
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
              {isNarrationProject ? 'Story Flow' : 'Video Flow'}
            </button>
            <button
              onClick={() => { setSelectedPanel('chapters'); reloadChapters(); }}
              className={`flex-1 px-2 py-1 rounded text-xs font-medium transition-colors flex items-center justify-center gap-1 ${
                selectedPanel === 'chapters'
                  ? 'bg-purple-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:text-white'
              }`}
              title="Chapter umbrella — work in smaller batches"
            >
              <Layers size={12} />
              Chapters {chapterTree.length > 0 && (<span className="text-[10px] opacity-70 ml-0.5">({chapterTree.length})</span>)}
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
            ) : selectedPanel === 'chapters' ? (
              <ChapterDirectionPanel
                projectId={id!}
                chapters={chapterTree}
                onChange={reloadChapters}
                onReparse={handleReparseChapters}
                reparseBusy={chapterRebuilding}
              />
            ) : (
              <AssetManager />
            )}
          </div>
        </div>

        {/* Middle & Right Panel - Editor & Preview */}
        <div className={`app-center-panel flex-1 flex flex-col overflow-hidden gap-4 ${mobilePanel !== 'editor' ? 'mobile-hidden' : ''}`}>
          {/* Preview — visible only when editor is collapsed */}
          {editorCollapsed && (
            <div className="flex-1 min-h-0 flex flex-col bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
              <div className="flex-1 min-h-0">
                <ErrorBoundary>
                  <VideoPreview
                    assembledPreviewUrl={previewUrl}
                    onExitPreview={() => setPreviewUrl(null)}
                    words={isNarrationProject ? subtitleWords : undefined}
                    srtBlocks={isNarrationProject ? srtBlocks : undefined}
                    subtitlesEnabled={isNarrationProject && previewSubEnabled}
                    subtitleStyle={memoizedSubtitleStyle}
                  />
                </ErrorBoundary>
              </div>

              {/* Subtitle settings bar — narration projects only */}
              {isNarrationProject && (
                <div className="flex-shrink-0 border-t border-gray-800">
                  <div className="flex items-center gap-2 px-3 py-1.5">
                    <button
                      onClick={() => {
                        const next = !previewSubEnabled;
                        setPreviewSubEnabled(next);
                        saveSubtitleSettings({ subtitle_enabled: next });
                      }}
                      className={`flex items-center gap-1.5 px-2 py-1 rounded text-xs font-medium transition-colors ${previewSubEnabled ? 'bg-blue-600/30 text-blue-300 border border-blue-500/50' : 'bg-gray-800 text-gray-400 border border-gray-700 hover:text-gray-300'}`}
                      title="Toggle subtitle preview"
                    >
                      <Captions size={14} />
                      Subtitles
                    </button>

                    {previewSubEnabled && subtitleWords.length === 0 && (
                      <span className="text-[10px] text-yellow-400 ml-1">
                        No word timestamps found —
                        <button
                          onClick={async () => {
                            if (!id) return;
                            try {
                              await rerunWhisper(id);
                              queryClient.invalidateQueries({ queryKey: ['lyrics', id] });
                            } catch (e) {
                              console.error('Whisper failed:', e);
                            }
                          }}
                          className="underline hover:text-yellow-200 ml-1"
                        >
                          Run Whisper
                        </button>
                        {' '}or upload an SRT
                      </span>
                    )}

                    {previewSubEnabled && subtitleWords.length > 0 && (
                      <>
                        <button
                          onClick={() => setSubSettingsOpen(!subSettingsOpen)}
                          className="flex items-center gap-1 px-2 py-1 rounded text-xs text-gray-400 hover:text-gray-200 bg-gray-800 border border-gray-700 transition-colors"
                        >
                          Style
                          {subSettingsOpen ? <ChevronDown size={12} /> : <ChevronUp size={12} />}
                        </button>

                        <span className="text-[10px] text-gray-500 ml-auto">
                          {previewSubFont} {previewSubSize}px | {previewSubPosition}
                        </span>
                      </>
                    )}
                  </div>

                  {/* Expanded subtitle style settings */}
                  {previewSubEnabled && subSettingsOpen && (
                    <div className="px-3 pb-2 space-y-2 border-t border-gray-800/50">
                      <div className="grid grid-cols-6 gap-2 pt-2">
                        <div>
                          <label className="block text-[10px] text-gray-500 mb-0.5">Font</label>
                          <select
                            value={previewSubFont}
                            onChange={(e) => { setPreviewSubFont(e.target.value); setSubSettingsDirty(true); }}
                            className="w-full px-1.5 py-1 bg-gray-800 border border-gray-700 rounded text-xs text-gray-100"
                          >
                            <option value="Arial">Arial</option>
                            <option value="Helvetica">Helvetica</option>
                            <option value="Times New Roman">Times New Roman</option>
                            <option value="Courier New">Courier New</option>
                            <option value="Impact">Impact</option>
                          </select>
                        </div>
                        <div>
                          <label className="block text-[10px] text-gray-500 mb-0.5">Size</label>
                          <input
                            type="number"
                            value={previewSubSize}
                            min={12} max={72}
                            onChange={(e) => { const v = parseInt(e.target.value) || 24; setPreviewSubSize(v); setSubSettingsDirty(true); }}
                            className="w-full px-1.5 py-1 bg-gray-800 border border-gray-700 rounded text-xs text-gray-100"
                          />
                        </div>
                        <div>
                          <label className="block text-[10px] text-gray-500 mb-0.5">Color</label>
                          <input
                            type="color"
                            value={previewSubColor}
                            onChange={(e) => { setPreviewSubColor(e.target.value); setSubSettingsDirty(true); }}
                            className="w-full h-[26px] bg-gray-800 border border-gray-700 rounded cursor-pointer"
                          />
                        </div>
                        <div>
                          <label className="block text-[10px] text-gray-500 mb-0.5">Position</label>
                          <select
                            value={previewSubPosition}
                            onChange={(e) => { setPreviewSubPosition(e.target.value); setSubSettingsDirty(true); }}
                            className="w-full px-1.5 py-1 bg-gray-800 border border-gray-700 rounded text-xs text-gray-100"
                          >
                            <option value="bottom">Bottom</option>
                            <option value="top">Top</option>
                            <option value="center">Center</option>
                          </select>
                        </div>
                        <div>
                          <label className="block text-[10px] text-gray-500 mb-0.5">Outline</label>
                          <input
                            type="number"
                            value={previewSubOutline}
                            min={0} max={8}
                            onChange={(e) => { const v = parseInt(e.target.value) || 0; setPreviewSubOutline(v); setSubSettingsDirty(true); }}
                            className="w-full px-1.5 py-1 bg-gray-800 border border-gray-700 rounded text-xs text-gray-100"
                          />
                        </div>
                        <div>
                          <label className="block text-[10px] text-gray-500 mb-0.5">Bold</label>
                          <button
                            onClick={() => { setPreviewSubBold(b => !b); setSubSettingsDirty(true); }}
                            className={`w-full px-1.5 py-1 rounded text-xs font-bold transition-colors ${
                              previewSubBold
                                ? 'bg-blue-600 text-white'
                                : 'bg-gray-800 border border-gray-700 text-gray-400'
                            }`}
                          >
                            B
                          </button>
                        </div>
                      </div>
                      {/* Save button */}
                      <div className="flex items-center gap-2 pt-1">
                        <button
                          onClick={() => {
                            saveSubtitleSettings({
                              subtitle_font: previewSubFont,
                              subtitle_size: previewSubSize,
                              subtitle_color: previewSubColor,
                              subtitle_position: previewSubPosition,
                              subtitle_outline: previewSubOutline,
                              subtitle_bold: previewSubBold,
                            });
                            setSubSettingsDirty(false);
                            setSubSettingsSaved(true);
                            setTimeout(() => setSubSettingsSaved(false), 2000);
                          }}
                          disabled={!subSettingsDirty}
                          className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                            subSettingsDirty
                              ? 'bg-blue-600 hover:bg-blue-500 text-white'
                              : 'bg-gray-800 text-gray-500 cursor-not-allowed'
                          }`}
                        >
                          Save
                        </button>
                        {subSettingsSaved && (
                          <span className="text-[10px] text-green-400 animate-pulse">Saved!</span>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              )}
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

        {/* Tab bar — only show when backing tracks are available */}
        {project && project.mode !== 'music_video' && id && (
          <div className="flex items-center gap-0 border-b border-gray-800 flex-shrink-0">
            <button
              onClick={() => setActiveTimeline('scenes')}
              className={`flex items-center gap-1.5 px-3 py-1 text-xs font-medium transition-colors border-b-2 ${
                activeTimeline === 'scenes'
                  ? 'text-blue-400 border-blue-400 bg-gray-800/50'
                  : 'text-gray-500 border-transparent hover:text-gray-300 hover:bg-gray-800/30'
              }`}
            >
              <Layers size={11} />
              Scene Timeline
            </button>
            <button
              onClick={() => setActiveTimeline('backing')}
              className={`flex items-center gap-1.5 px-3 py-1 text-xs font-medium transition-colors border-b-2 ${
                activeTimeline === 'backing'
                  ? 'text-purple-400 border-purple-400 bg-gray-800/50'
                  : 'text-gray-500 border-transparent hover:text-gray-300 hover:bg-gray-800/30'
              }`}
            >
              <Music size={11} />
              Backing Tracks
            </button>
          </div>
        )}

        {/* Chapter scope banner — name, color, description, prev/next nav.
            Only shows when drilled into a chapter (URL /c/<short_code>). */}
        {activeChapter && id && (
          <ChapterScopeBanner
            projectId={id}
            projectName={project?.name}
            chapter={activeChapter}
            flatChapters={(() => {
              // Flatten tree in playback order for prev/next
              const out: import('@/types').ChapterTreeNode[] = [];
              const walk = (nodes: import('@/types').ChapterTreeNode[]) => {
                for (const n of nodes) {
                  out.push(n);
                  if (n.children?.length) walk(n.children);
                }
              };
              walk(chapterTree);
              out.sort((a, b) => a.start_time - b.start_time);
              return out;
            })()}
            onChange={reloadChapters}
          />
        )}
        {/* Chapter overlay — colored bars row above the waveform */}
        {project && id && chapterTree.length > 0 && (
          <div className="px-3 pt-1 pb-0.5 bg-gray-900/50 border-b border-gray-800/60">
            <ChapterOverlay
              chapters={chapterTree}
              totalDuration={(scenes && scenes.length > 0)
                ? Math.max(...scenes.map((s: any) => s.end_time || 0))
                : 0}
              projectId={id}
              activeChapterShortCode={chapterShortCode || null}
            />
          </div>
        )}
        {/* Both timelines stay mounted (to preserve WaveSurfer playback state); CSS hides the inactive one */}
        <div className={`flex-1 overflow-hidden ${activeTimeline !== 'scenes' && project && project.mode !== 'music_video' && id ? 'hidden' : ''}`}>
          <Timeline onSplitScene={handleSplitScene} onBoundaryDrag={handleBoundaryDrag} onDeleteScene={handleDeleteScene} />
        </div>
        {project && project.mode !== 'music_video' && id && (
          <div className={`flex-1 overflow-hidden ${activeTimeline !== 'backing' ? 'hidden' : ''}`}>
            <BackingTrackTimeline
              projectId={id}
              totalDuration={stableScenes.length > 0 ? Math.max(...stableScenes.map((s: any) => s.end_time || 0)) : 0}
              expanded
            />
          </div>
        )}

        {/* Mini-preview strip of the INACTIVE timeline */}
        {project && project.mode !== 'music_video' && id && (
          <div
            onClick={() => setActiveTimeline(activeTimeline === 'scenes' ? 'backing' : 'scenes')}
            className={`flex-shrink-0 h-6 border-t cursor-pointer transition-colors flex items-center gap-2 px-3 ${
              activeTimeline === 'scenes'
                ? 'border-purple-800/50 bg-purple-950/30 hover:bg-purple-900/40'
                : 'border-blue-800/50 bg-blue-950/30 hover:bg-blue-900/40'
            }`}
            title={activeTimeline === 'scenes' ? 'Click to switch to Backing Tracks' : 'Click to switch to Scene Timeline'}
          >
            {activeTimeline === 'scenes' ? (
              <>
                <Music size={9} className="text-purple-500 flex-shrink-0" />
                <span className="text-[9px] text-purple-500/80 flex-shrink-0">Backing</span>
                {/* Mini backing track bars */}
                <div className="flex-1 relative h-3 bg-gray-800/60 rounded overflow-hidden">
                  {(() => {
                    const maxT = stableScenes.length > 0 ? Math.max(...stableScenes.map((s: any) => s.end_time || 0)) : 1;
                    return (backingTracksForMini || []).map((t: any) => (
                      <div
                        key={t.id}
                        className="absolute top-0 h-full bg-purple-600/50 rounded-sm"
                        style={{
                          left: `${(t.start_time / Math.max(maxT, 1)) * 100}%`,
                          width: `${Math.max(((t.end_time - t.start_time) / Math.max(maxT, 1)) * 100, 0.5)}%`,
                        }}
                      />
                    ));
                  })()}
                </div>
              </>
            ) : (
              <>
                <Layers size={9} className="text-blue-500 flex-shrink-0" />
                <span className="text-[9px] text-blue-500/80 flex-shrink-0">Scenes</span>
                {/* Mini scene bars */}
                <div className="flex-1 relative h-3 bg-gray-800/60 rounded overflow-hidden">
                  {stableScenes.map((s: any, i: number) => {
                    const maxT = stableScenes.length > 0 ? Math.max(...stableScenes.map((sc: any) => sc.end_time || 0)) : 1;
                    const colors = ['bg-blue-600/50', 'bg-cyan-600/50', 'bg-teal-600/50', 'bg-indigo-600/50'];
                    return (
                      <div
                        key={s.id}
                        className={`absolute top-0 h-full ${colors[i % colors.length]} rounded-sm`}
                        style={{
                          left: `${((s.start_time || 0) / Math.max(maxT, 1)) * 100}%`,
                          width: `${Math.max((((s.end_time || 0) - (s.start_time || 0)) / Math.max(maxT, 1)) * 100, 0.5)}%`,
                        }}
                      />
                    );
                  })}
                </div>
              </>
            )}
          </div>
        )}
      </div>

      {/* Export Modal */}
      {exportOpen && <ExportModal projectId={id!} onClose={() => setExportOpen(false)} />}

      {/* Export Gallery Modal */}
      {exportGalleryOpen && <ExportGalleryModal projectId={id!} onClose={() => setExportGalleryOpen(false)} />}

      {/* Auto Generate Modal */}
      {autoGenOpen && <AutoGenerateModal
        projectId={id!}
        onClose={() => setAutoGenOpen(false)}
        onMinimize={() => setAutoGenOpen(false)}
        onStarted={startAutoGenPolling}
        autoGenStatus={autoGenStatus}
        autoGenMode={autoGenMode}
        autoGenCompleted={autoGenCompleted}
        autoGenTotal={autoGenTotal}
        autoGenStep={autoGenStep}
        autoGenSceneName={autoGenSceneName}
        autoGenBatchRunId={autoGenBatchRunId}
      />}

      {/* Asset Generator Modal */}
      {assetGenOpen && <AssetGeneratorModal projectId={id!} onClose={() => setAssetGenOpen(false)} />}

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
  const activeScene = useAppStore(s => s.activeScene);
  const setActiveScene = useAppStore(s => s.setActiveScene);
  const setPlaybackPosition = useAppStore(s => s.setPlaybackPosition);
  const setIsPlaying = useAppStore(s => s.setIsPlaying);
  const isPlaying = useAppStore(s => s.isPlaying);
  const playbackPosition = useAppStore(s => s.playbackPosition);
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
  // Ken Burns settings (initialized from project.settings)
  const [randomKenBurns, setRandomKenBurns] = useState(projSettings.random_ken_burns || false);
  const [kenBurnsAllowedEffects, setKenBurnsAllowedEffects] = useState<string[]>(projSettings.ken_burns_allowed_effects || []);
  // Narration-only settings
  const isNarration = currentProject?.mode === 'narration_images' || currentProject?.mode === 'narration_video';
  const [subtitlesEnabled, setSubtitlesEnabled] = useState(projSettings.subtitle_enabled ?? false);
  const [subtitleFont, setSubtitleFont] = useState(projSettings.subtitle_font || 'Arial');
  const [subtitleSize, setSubtitleSize] = useState(projSettings.subtitle_size || 24);
  const [subtitleColor, setSubtitleColor] = useState(projSettings.subtitle_color || '#FFFFFF');
  const [subtitlePosition, setSubtitlePosition] = useState(projSettings.subtitle_position || 'bottom');
  const [subtitleOutline, setSubtitleOutline] = useState(projSettings.subtitle_outline ?? 2);
  const [subtitleBold, setSubtitleBold] = useState(projSettings.subtitle_bold ?? false);
  const [normalizeAudio, setNormalizeAudio] = useState(projSettings.normalize_audio ?? false);
  // Backing track mix settings — synced from project.settings (set on timeline)
  const [backingTrackLoop, setBackingTrackLoop] = useState(projSettings.backing_track_loop ?? false);
  const [narrationVolume, setNarrationVolume] = useState(projSettings.narration_volume ?? 1.0);
  const [backingMasterVolume, setBackingMasterVolume] = useState(projSettings.backing_volume ?? 1.0);
  const [backingMainFadeIn, setBackingMainFadeIn] = useState(projSettings.backing_main_fade_in ?? 0.0);
  const [backingMainFadeOut, setBackingMainFadeOut] = useState(projSettings.backing_main_fade_out ?? 0.0);
  const [normalizeBacking, setNormalizeBacking] = useState(projSettings.normalize_backing ?? false);
  // ── Re-export controls ──
  const [audioOnlyRemix, setAudioOnlyRemix] = useState(false);
  const [forceRecreate, setForceRecreate] = useState(false);
  const [exportStems, setExportStems] = useState(false);
  const [stemsOnly, setStemsOnly] = useState(false);
  // Chapter scope — defaults to 'all'.  When set to single/multiple
  // the export pipeline filters scenes, slices audio, and shifts
  // backing tracks + subtitle word timestamps.
  // ── Chapter-scope-aware default for the export modal ───────────────
  // When the user opens Export from inside a chapter view, default the
  // scope to "single chapter" pre-selecting THIS chapter.  No mode →
  // sentinel from Zustand store.
  const _chapterScopeFromStore = useAppStore((s) => s.chapterScope);
  const [chapterSelection, setChapterSelection] = useState<import('@/types').ChapterSelection>(
    _chapterScopeFromStore
      ? { mode: 'single', chapter_ids: [_chapterScopeFromStore.chapterId] }
      : { mode: 'all', chapter_ids: [] }
  );
  const [chapterTreeForPicker, setChapterTreeForPicker] = useState<import('@/types').ChapterTreeNode[]>([]);
  const [reExportOpen, setReExportOpen] = useState(false);
  const [phase, setPhase] = useState<'config' | 'exporting' | 'done' | 'failed' | 'cancelled'>('config');
  const [progressPercent, setProgressPercent] = useState(0);
  const [currentStep, setCurrentStep] = useState('');
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exportPhase, setExportPhase] = useState<string | null>(null);
  const [totalChunks, setTotalChunks] = useState(0);
  const [currentChunk, setCurrentChunk] = useState(0);
  const [chunks, setChunks] = useState<Array<{ index: number; path: string; download_url: string; scenes: string; size_mb: number }>>([]);
  const [stems, setStems] = useState<Array<{ filename: string; size_mb: number; download_url: string }>>([]);
  const [lightboxChunkUrl, setLightboxChunkUrl] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  // Recovery scan state
  const [scanResult, setScanResult] = useState<{ recoverable: boolean; clip_count: number; chunk_count: number; total_clips_size_mb: number; total_chunks_size_mb: number; has_manifest: boolean; chunks: Array<{ index: number; download_url: string; scenes: string; size_mb: number }> } | null>(null);
  const [recovering, setRecovering] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  // Scan for recoverable export artifacts on modal open
  useEffect(() => {
    let cancelled = false;
    const doScan = async () => {
      try {
        const res = await scanExport(projectId);
        if (!cancelled) setScanResult(res.data as any);
      } catch {
        // Ignore — feature just won't show
      }
    };
    doScan();
    return () => { cancelled = true; };
  }, [projectId]);

  // Load chapter tree for the scope picker
  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    (async () => {
      try {
        const m = await import('@/api/client');
        const { data } = await m.getChapters(projectId);
        if (!cancelled) setChapterTreeForPicker(data.chapters || []);
      } catch (e) {
        console.warn('[export-modal] failed to load chapter tree:', e);
      }
    })();
    return () => { cancelled = true; };
  }, [projectId]);

  const startPolling = () => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const res = await getExportStatus(projectId);
        const data = res.data;
        setProgressPercent(data.progress_percent);
        setCurrentStep(data.current_step);
        if (data.phase) setExportPhase(data.phase);
        if (data.total_chunks) setTotalChunks(data.total_chunks);
        if (data.current_chunk) setCurrentChunk(data.current_chunk);
        if (data.chunks && data.chunks.length > 0) setChunks(data.chunks);
        if (data.stems && data.stems.length > 0) setStems(data.stems);

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
        } else if (data.status === 'cancelled') {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          setPhase('cancelled');
          setCancelling(false);
        }
      } catch {
        // Ignore transient poll errors
      }
    }, 1500);
  };

  const exportMutation = useMutation({
    mutationFn: async () => {
      await exportVideo(projectId, {
        format, width, height, fps, quality,
        transition_type: transitionType,
        transition_duration: transitionDuration,
        color_match_clips: colorCorrection,
        random_ken_burns: randomKenBurns,
        ken_burns_allowed_effects: kenBurnsAllowedEffects.length > 0 ? kenBurnsAllowedEffects : null,
        ...(isNarration ? {
          subtitles_enabled: subtitlesEnabled,
          subtitle_font: subtitleFont,
          subtitle_size: subtitleSize,
          subtitle_color: subtitleColor,
          subtitle_position: subtitlePosition,
          subtitle_outline: subtitleOutline,
          subtitle_bold: subtitleBold,
          normalize_audio: normalizeAudio,
          backing_track_loop: backingTrackLoop,
          narration_volume: narrationVolume,
          backing_volume: backingMasterVolume,
          backing_main_fade_in: backingMainFadeIn,
          backing_main_fade_out: backingMainFadeOut,
          normalize_backing: normalizeBacking,
          // Re-export controls (narration-only; music_video ignores these for now)
          audio_only_remix: audioOnlyRemix,
          force_recreate: forceRecreate,
          export_stems: exportStems,
          stems_only: stemsOnly,
          chapter_selection: chapterSelection.mode === 'all' ? null : chapterSelection,
        } : {
          // For music mode we still support force_recreate + stems_only + chapter scope
          force_recreate: forceRecreate,
          stems_only: stemsOnly,
          chapter_selection: chapterSelection.mode === 'all' ? null : chapterSelection,
        }),
      });
    },
    onSuccess: () => {
      setPhase('exporting');
      setCurrentStep('Starting export...');
      setProgressPercent(0);
      setChunks([]);
      setCancelling(false);
      startPolling();
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
    setChunks([]);
    setStems([]);
    setExportPhase(null);
    setTotalChunks(0);
    setCurrentChunk(0);
    exportMutation.mutate();
  };

  const handleCancel = async () => {
    setCancelling(true);
    try {
      await cancelExport(projectId);
    } catch {
      setCancelling(false);
    }
  };

  const handleResume = async () => {
    setPhase('exporting');
    setCurrentStep('Resuming export...');
    setProgressPercent(0);
    setExportError(null);
    setCancelling(false);
    try {
      await resumeExport(projectId);
      startPolling();
    } catch (err: any) {
      setPhase('failed');
      setExportError(err?.response?.data?.detail || err?.message || 'Failed to resume export');
    }
  };

  const handleRecover = async () => {
    setRecovering(true);
    setPhase('exporting');
    setCurrentStep('Recovering export from disk...');
    setProgressPercent(0);
    setExportError(null);
    setCancelling(false);
    // Pre-populate chunks from scan
    if (scanResult?.chunks) setChunks(scanResult.chunks as any);
    try {
      await recoverExport(projectId);
      startPolling();
    } catch (err: any) {
      setPhase('failed');
      setExportError(err?.response?.data?.detail || err?.message || 'Failed to recover export');
    } finally {
      setRecovering(false);
    }
  };

  const handleDownload = () => {
    if (downloadUrl) {
      window.open(downloadUrl, '_blank');
    }
  };

  const phaseLabel = (p: string | null): string => {
    switch (p) {
      case 'clips': return 'Rendering clips';
      case 'chunks': return 'Merging chunks';
      case 'final': return 'Final assembly';
      case 'post': return 'Post-processing';
      default: return '';
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-gray-900 border border-gray-800 rounded-lg w-full max-w-md p-6 max-h-[85vh] flex flex-col">
        <h2 className="text-2xl font-bold mb-6 flex-shrink-0">
          {phase === 'done' ? 'Export Complete' : phase === 'failed' ? 'Export Failed' : 'Export Video'}
        </h2>

        {/* Config phase — show settings */}
        {phase === 'config' && (
          <div className="flex flex-col min-h-0 flex-1">
            <div className="space-y-4 mb-6 overflow-y-auto pr-1 min-h-0 flex-1">
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
                    onChange={(e) => setWidth(parseInt(e.target.value) || 1280)}
                    className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 focus:outline-none focus:border-blue-500"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium mb-2">Height</label>
                  <input
                    type="number"
                    value={height}
                    onChange={(e) => setHeight(parseInt(e.target.value) || 720)}
                    className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 focus:outline-none focus:border-blue-500"
                  />
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium mb-2">FPS</label>
                <input
                  type="number"
                  value={fps}
                  onChange={(e) => setFps(parseInt(e.target.value) || 24)}
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
                    <option value="none">None (Use Per Scene Preference)</option>
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

              {/* Ken Burns — useful for narration_images but available for all modes */}
              <div className="border-t border-gray-700 pt-3 mt-2">
                <div className="flex items-center gap-3 mb-2">
                  <input
                    type="checkbox"
                    id="exportRandomKenBurns"
                    checked={randomKenBurns}
                    onChange={(e) => setRandomKenBurns(e.target.checked)}
                    className="w-4 h-4 rounded border-gray-600 bg-gray-800 text-purple-500 focus:ring-purple-500"
                  />
                  <label htmlFor="exportRandomKenBurns" className="text-sm font-medium">
                    Randomize Ken Burns Effects
                  </label>
                </div>
                {randomKenBurns && (
                  <div className="ml-7 space-y-1 max-h-48 overflow-y-auto">
                    <p className="text-xs text-gray-500 mb-1">Only use these effects:</p>
                    {[
                      { value: 'zoom_in_center', label: 'Zoom In (Center)' },
                      { value: 'zoom_out_center', label: 'Zoom Out (Center)' },
                      { value: 'zoom_in_top_left', label: 'Zoom In (Top Left)' },
                      { value: 'zoom_in_top_right', label: 'Zoom In (Top Right)' },
                      { value: 'zoom_in_bottom_left', label: 'Zoom In (Bottom Left)' },
                      { value: 'zoom_in_bottom_right', label: 'Zoom In (Bottom Right)' },
                      { value: 'pan_left', label: 'Pan Left' },
                      { value: 'pan_right', label: 'Pan Right' },
                      { value: 'pan_up', label: 'Pan Up' },
                      { value: 'pan_down', label: 'Pan Down' },
                      { value: 'pan_left_to_right', label: 'Pan Left to Right' },
                      { value: 'pan_right_to_left', label: 'Pan Right to Left' },
                      { value: 'zoom_in_pan_left', label: 'Zoom In + Pan Left' },
                      { value: 'zoom_in_pan_right', label: 'Zoom In + Pan Right' },
                      { value: 'zoom_out_pan_left', label: 'Zoom Out + Pan Left' },
                      { value: 'zoom_out_pan_right', label: 'Zoom Out + Pan Right' },
                    ].map(({ value, label }) => (
                      <label key={value} className="flex items-center gap-2 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={kenBurnsAllowedEffects.length === 0 || kenBurnsAllowedEffects.includes(value)}
                          onChange={(e) => {
                            let next: string[];
                            const allEffects = ['zoom_in_center','zoom_out_center','zoom_in_top_left','zoom_in_top_right','zoom_in_bottom_left','zoom_in_bottom_right','pan_left','pan_right','pan_up','pan_down','pan_left_to_right','pan_right_to_left','zoom_in_pan_left','zoom_in_pan_right','zoom_out_pan_left','zoom_out_pan_right'];
                            if (kenBurnsAllowedEffects.length === 0) {
                              next = e.target.checked ? allEffects : allEffects.filter(v => v !== value);
                            } else {
                              next = e.target.checked
                                ? [...kenBurnsAllowedEffects, value]
                                : kenBurnsAllowedEffects.filter(v => v !== value);
                            }
                            if (next.length >= 16) next = [];
                            setKenBurnsAllowedEffects(next);
                          }}
                          className="rounded border-gray-600 bg-gray-800 text-blue-500 focus:ring-blue-500"
                        />
                        <span className="text-xs text-gray-300">{label}</span>
                      </label>
                    ))}
                    <p className="text-[10px] text-gray-500 mt-1">
                      {kenBurnsAllowedEffects.length === 0
                        ? 'All 16 effects enabled.'
                        : `${kenBurnsAllowedEffects.length} of 16 effects enabled.`}
                    </p>
                  </div>
                )}
              </div>

              {/* Narration-only: Subtitle & Normalize controls */}
              {isNarration && (
                <>
                  <div className="border-t border-gray-700 pt-3 mt-2">
                    <div className="flex items-center gap-3 mb-3">
                      <input
                        type="checkbox"
                        id="subtitlesEnabled"
                        checked={subtitlesEnabled}
                        onChange={(e) => setSubtitlesEnabled(e.target.checked)}
                        className="w-4 h-4 rounded border-gray-600 bg-gray-800 text-blue-500 focus:ring-blue-500"
                      />
                      <label htmlFor="subtitlesEnabled" className="text-sm font-medium">
                        Burn-in Subtitles
                      </label>
                    </div>

                    {subtitlesEnabled && (
                      <div className="space-y-3 pl-7">
                        <div className="grid grid-cols-2 gap-3">
                          <div>
                            <label className="block text-xs text-gray-400 mb-1">Font</label>
                            <select
                              value={subtitleFont}
                              onChange={(e) => setSubtitleFont(e.target.value)}
                              className="w-full px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-sm text-gray-100"
                            >
                              <option value="Arial">Arial</option>
                              <option value="Helvetica">Helvetica</option>
                              <option value="Times New Roman">Times New Roman</option>
                              <option value="Courier New">Courier New</option>
                              <option value="Impact">Impact</option>
                            </select>
                          </div>
                          <div>
                            <label className="block text-xs text-gray-400 mb-1">Size</label>
                            <input
                              type="number"
                              value={subtitleSize}
                              min={12}
                              max={72}
                              onChange={(e) => setSubtitleSize(parseInt(e.target.value) || 24)}
                              className="w-full px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-sm text-gray-100"
                            />
                          </div>
                        </div>
                        <div className="grid grid-cols-3 gap-3">
                          <div>
                            <label className="block text-xs text-gray-400 mb-1">Color</label>
                            <input
                              type="color"
                              value={subtitleColor}
                              onChange={(e) => setSubtitleColor(e.target.value)}
                              className="w-full h-8 bg-gray-800 border border-gray-700 rounded cursor-pointer"
                            />
                          </div>
                          <div>
                            <label className="block text-xs text-gray-400 mb-1">Position</label>
                            <select
                              value={subtitlePosition}
                              onChange={(e) => setSubtitlePosition(e.target.value)}
                              className="w-full px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-sm text-gray-100"
                            >
                              <option value="bottom">Bottom</option>
                              <option value="top">Top</option>
                              <option value="center">Center</option>
                            </select>
                          </div>
                          <div>
                            <label className="block text-xs text-gray-400 mb-1">Outline</label>
                            <input
                              type="number"
                              value={subtitleOutline}
                              min={0}
                              max={8}
                              onChange={(e) => setSubtitleOutline(parseInt(e.target.value) || 0)}
                              className="w-full px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-sm text-gray-100"
                            />
                          </div>
                          <div>
                            <label className="block text-xs text-gray-400 mb-1">Bold</label>
                            <button
                              onClick={() => setSubtitleBold((b: boolean) => !b)}
                              className={`w-full px-2 py-1.5 rounded text-sm font-bold transition-colors ${
                                subtitleBold
                                  ? 'bg-blue-600 text-white'
                                  : 'bg-gray-800 border border-gray-700 text-gray-400'
                              }`}
                            >
                              B
                            </button>
                          </div>
                        </div>
                      </div>
                    )}
                  </div>

                  <div className="flex items-center gap-3">
                    <input
                      type="checkbox"
                      id="normalizeAudio"
                      checked={normalizeAudio}
                      onChange={(e) => setNormalizeAudio(e.target.checked)}
                      className="w-4 h-4 rounded border-gray-600 bg-gray-800 text-blue-500 focus:ring-blue-500"
                    />
                    <label htmlFor="normalizeAudio" className="text-sm font-medium">
                      Normalize Audio (loudness normalization)
                    </label>
                  </div>

                  {/* Backing Track Mix Settings */}
                  <div className="border-t border-gray-700 pt-3 mt-2">
                    <div className="text-sm font-medium mb-2">Backing Track Mix</div>

                    <div className="space-y-2">
                      <div className="flex items-center gap-3">
                        <input
                          type="checkbox"
                          id="exportBackingLoop"
                          checked={backingTrackLoop}
                          onChange={(e) => setBackingTrackLoop(e.target.checked)}
                          className="w-4 h-4 rounded border-gray-600 bg-gray-800 text-purple-500 focus:ring-purple-500"
                        />
                        <label htmlFor="exportBackingLoop" className="text-xs text-gray-300">
                          Repeat backing tracks until video ends
                        </label>
                      </div>

                      <div className="flex items-center gap-3">
                        <input
                          type="checkbox"
                          id="exportNormBacking"
                          checked={normalizeBacking}
                          onChange={(e) => setNormalizeBacking(e.target.checked)}
                          className="w-4 h-4 rounded border-gray-600 bg-gray-800 text-purple-500 focus:ring-purple-500"
                        />
                        <label htmlFor="exportNormBacking" className="text-xs text-gray-300">
                          Normalize backing track loudness
                        </label>
                      </div>

                      <div className="grid grid-cols-2 gap-3">
                        <div>
                          <label className="block text-xs text-gray-400 mb-1">Narration Volume</label>
                          <div className="flex items-center gap-2">
                            <input
                              type="range"
                              min={0}
                              max={1}
                              step={0.01}
                              value={narrationVolume}
                              onChange={(e) => setNarrationVolume(parseFloat(e.target.value))}
                              className="flex-1 h-1 accent-blue-500"
                            />
                            <span className="text-[10px] text-gray-500 w-8">{Math.round(narrationVolume * 100)}%</span>
                          </div>
                        </div>
                        <div>
                          <label className="block text-xs text-gray-400 mb-1">Backing Volume</label>
                          <div className="flex items-center gap-2">
                            <input
                              type="range"
                              min={0}
                              max={1}
                              step={0.01}
                              value={backingMasterVolume}
                              onChange={(e) => setBackingMasterVolume(parseFloat(e.target.value))}
                              className="flex-1 h-1 accent-purple-500"
                            />
                            <span className="text-[10px] text-gray-500 w-8">{Math.round(backingMasterVolume * 100)}%</span>
                          </div>
                        </div>
                      </div>

                      <div className="grid grid-cols-2 gap-3">
                        <div>
                          <label className="block text-xs text-gray-400 mb-1">Main Fade In (s)</label>
                          <input
                            type="number"
                            step={0.5}
                            min={0}
                            max={30}
                            value={backingMainFadeIn}
                            onChange={(e) => setBackingMainFadeIn(parseFloat(e.target.value) || 0)}
                            className="w-full px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-sm text-gray-100"
                          />
                        </div>
                        <div>
                          <label className="block text-xs text-gray-400 mb-1">Main Fade Out (s)</label>
                          <input
                            type="number"
                            step={0.5}
                            min={0}
                            max={30}
                            value={backingMainFadeOut}
                            onChange={(e) => setBackingMainFadeOut(parseFloat(e.target.value) || 0)}
                            className="w-full px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-sm text-gray-100"
                          />
                        </div>
                      </div>
                    </div>
                  </div>
                </>
              )}
            </div>

            {/* Chapter scope — Entire video / Single / Multiple */}
            {chapterTreeForPicker.length > 0 && (
              <div className="mb-3 flex-shrink-0">
                <ChapterPicker
                  chapters={chapterTreeForPicker}
                  value={chapterSelection}
                  onChange={setChapterSelection}
                />
              </div>
            )}

            {/* Re-export options — collapsible accordion, closed by default so main export controls stay visible */}
            <div className="bg-gray-800/40 border border-gray-700/60 rounded-lg mb-3 flex-shrink-0 overflow-hidden">
              <button
                type="button"
                onClick={() => setReExportOpen((v) => !v)}
                className="w-full flex items-center justify-between gap-2 px-3 py-2 hover:bg-gray-800/60 transition-colors"
                aria-expanded={reExportOpen}
              >
                <div className="flex items-center gap-2 text-sm font-medium text-gray-300">
                  <svg
                    className={`w-4 h-4 transition-transform ${reExportOpen ? 'rotate-90' : ''}`}
                    fill="none" viewBox="0 0 24 24" stroke="currentColor"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                  </svg>
                  <span>Re-export options</span>
                  {(audioOnlyRemix || forceRecreate || exportStems || stemsOnly) && (
                    <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-blue-600/30 text-blue-200 border border-blue-500/40">
                      {[
                        audioOnlyRemix && 'audio-only',
                        forceRecreate && 'force recreate',
                        exportStems && 'stems',
                        stemsOnly && 'stems only',
                      ].filter(Boolean).join(' · ')}
                    </span>
                  )}
                </div>
                <span className="text-xs text-gray-500">{reExportOpen ? 'hide' : 'show'}</span>
              </button>
              {reExportOpen && (
              <div className="space-y-2.5 px-3 pb-3 border-t border-gray-700/50 pt-3">
                {isNarration && (
                  <>
                    <label className="flex items-start gap-2 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={audioOnlyRemix}
                        onChange={(e) => setAudioOnlyRemix(e.target.checked)}
                        disabled={forceRecreate}
                        className="w-4 h-4 mt-0.5 rounded accent-blue-500"
                      />
                      <div className="flex-1">
                        <div className="text-sm text-gray-200">
                          Audio-only re-mix <span className="text-xs text-blue-300">(reuse cached video)</span>
                        </div>
                        <div className="text-xs text-gray-500 leading-snug">
                          Skip clip rendering and chunk merging. Reuses the silent video from a previous successful export and applies the new audio mix on top. Use this after adjusting narration/backing volumes, fades, or normalization. Requires a prior export with matching video settings.
                        </div>
                      </div>
                    </label>
                    <label className="flex items-start gap-2 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={exportStems}
                        onChange={(e) => setExportStems(e.target.checked)}
                        className="w-4 h-4 mt-0.5 rounded accent-emerald-500"
                      />
                      <div className="flex-1">
                        <div className="text-sm text-gray-200">
                          Export audio stems <span className="text-xs text-emerald-300">(for DAW remix)</span>
                        </div>
                        <div className="text-xs text-gray-500 leading-snug">
                          Also writes <code className="text-emerald-400">narration.wav</code> and <code className="text-emerald-400">backing_mix.wav</code> to a <code>stems/</code> folder alongside the main MP4. Mix outside the app without re-rendering.
                        </div>
                      </div>
                    </label>
                  </>
                )}
                <label className="flex items-start gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={forceRecreate}
                    onChange={(e) => {
                      setForceRecreate(e.target.checked);
                      if (e.target.checked) setAudioOnlyRemix(false);
                    }}
                    className="w-4 h-4 mt-0.5 rounded accent-amber-500"
                  />
                  <div className="flex-1">
                    <div className="text-sm text-gray-200">
                      Force full recreate <span className="text-xs text-amber-300">(ignore cache)</span>
                    </div>
                    <div className="text-xs text-gray-500 leading-snug">
                      Wipe the export cache and re-render everything from scratch. Use this if cached artifacts are stale or you want to start completely fresh.
                    </div>
                  </div>
                </label>
                {isNarration && (
                  <label className="flex items-start gap-2 cursor-pointer pt-1 border-t border-gray-700/50">
                    <input
                      type="checkbox"
                      checked={stemsOnly}
                      onChange={(e) => setStemsOnly(e.target.checked)}
                      className="w-4 h-4 mt-0.5 rounded accent-purple-500"
                    />
                    <div className="flex-1">
                      <div className="text-sm text-gray-200">
                        Stems only <span className="text-xs text-purple-300">(skip video entirely)</span>
                      </div>
                      <div className="text-xs text-gray-500 leading-snug">
                        Skip all video rendering and produce ONLY the audio stems in <code>{'{output_dir}'}/stems/</code>: <code className="text-purple-300">narration.wav</code>, <code className="text-purple-300">backing_mix.wav</code>, and one WAV per backing track (<code className="text-purple-300">backing_01_<i>name</i>.wav</code>, ...). Use this when you already have the exported video and just want to grab stems for outside-the-app mixing.
                      </div>
                    </div>
                  </label>
                )}
              </div>
              )}
            </div>

            {/* Recovery banner — shown when recoverable artifacts are found */}
            {scanResult?.recoverable && (
              <div className="bg-amber-900/30 border border-amber-700/50 rounded-lg p-3 mb-3 flex-shrink-0">
                <div className="flex items-start gap-2">
                  <svg className="w-5 h-5 text-amber-400 flex-shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-amber-300">Previous export found on disk</div>
                    <div className="text-xs text-amber-400/80 mt-1">
                      {scanResult.clip_count > 0 && <span>{scanResult.clip_count} clips ({scanResult.total_clips_size_mb} MB)</span>}
                      {scanResult.clip_count > 0 && scanResult.chunk_count > 0 && <span> + </span>}
                      {scanResult.chunk_count > 0 && <span>{scanResult.chunk_count} chunks ({scanResult.total_chunks_size_mb} MB)</span>}
                      {scanResult.has_manifest && <span className="ml-1 text-green-400">&bull; manifest found</span>}
                    </div>
                    <button
                      onClick={handleRecover}
                      disabled={recovering}
                      className="mt-2 px-3 py-1.5 bg-amber-600 hover:bg-amber-700 disabled:opacity-50 rounded text-xs font-medium transition-colors"
                    >
                      {recovering ? 'Recovering...' : 'Recover & Resume Export'}
                    </button>
                  </div>
                </div>
              </div>
            )}

            <div className="flex gap-4 flex-shrink-0 pt-4 border-t border-gray-800">
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
          </div>
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
                {cancelling ? 'Cancelling...' : 'Exporting...'}
              </div>
              {exportPhase && (
                <div className="text-xs text-gray-500 mt-1">{phaseLabel(exportPhase)}</div>
              )}
              {totalChunks > 0 && (
                <div className="text-xs text-gray-500 mt-0.5">
                  Chunk {currentChunk} / {totalChunks}
                </div>
              )}
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

            {/* Chunk gallery */}
            {chunks.length > 0 && (
              <div className="border-t border-gray-700 pt-3 mt-2">
                <div className="text-xs text-gray-400 mb-2 font-medium">Completed Chunks ({chunks.length})</div>
                <div className="grid grid-cols-2 gap-2 max-h-48 overflow-y-auto">
                  {chunks.map((c) => (
                    <div key={c.index} className="bg-gray-800 border border-gray-700 rounded overflow-hidden hover:border-blue-500 transition-colors group">
                      <div className="relative cursor-pointer" onClick={() => setLightboxChunkUrl(c.download_url)}>
                        <video src={c.download_url} muted preload="metadata" className="w-full h-20 object-cover bg-gray-900" />
                        <div className="absolute inset-0 flex items-center justify-center bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity">
                          <svg className="w-8 h-8 text-white/90" fill="currentColor" viewBox="0 0 24 24"><path d="M8 5v14l11-7z" /></svg>
                        </div>
                      </div>
                      <div className="px-2 py-1.5 flex items-center justify-between">
                        <div>
                          <div className="text-xs font-medium text-gray-300">Part {c.index + 1}</div>
                          <div className="text-[10px] text-gray-500">Scenes {c.scenes} · {c.size_mb}MB</div>
                        </div>
                        <a href={c.download_url} download onClick={(e) => e.stopPropagation()} className="p-1 text-gray-500 hover:text-green-400 transition-colors" title="Download chunk">
                          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" /></svg>
                        </a>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="flex justify-center pt-2">
              <button
                onClick={handleCancel}
                disabled={cancelling}
                className="px-4 py-2 bg-red-600/80 hover:bg-red-600 disabled:bg-gray-700 disabled:text-gray-500 rounded font-medium text-sm transition-colors"
              >
                {cancelling ? 'Cancelling...' : 'Cancel Export'}
              </button>
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
              <p className="text-sm text-gray-300">
                {stems.length > 0 && chunks.length === 0
                  ? "Your audio stems have been exported successfully."
                  : "Your video has been exported successfully."}
              </p>
            </div>

            <div className="w-full bg-gray-700 rounded-full h-3 overflow-hidden">
              <div className="bg-green-500 h-full w-full" />
            </div>

            {/* Stems gallery on done */}
            {stems.length > 0 && (
              <div className="border-t border-gray-700 pt-3">
                <div className="text-xs text-gray-400 mb-2 font-medium">
                  Audio Stems ({stems.length}) · click to download
                </div>
                <div className="space-y-1.5 max-h-72 overflow-y-auto">
                  {stems.map((stem) => (
                    <a
                      key={stem.filename}
                      href={stem.download_url}
                      download
                      className="flex items-center justify-between gap-3 px-3 py-2 bg-gray-800 border border-gray-700 hover:border-purple-500 rounded transition-colors group"
                    >
                      <div className="flex items-center gap-2 min-w-0 flex-1">
                        <svg className="w-4 h-4 text-purple-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M9 19V6l12-3v13M9 19c0 1.657-1.343 3-3 3s-3-1.343-3-3 1.343-3 3-3 3 1.343 3 3zm12-3c0 1.657-1.343 3-3 3s-3-1.343-3-3 1.343-3 3-3 3 1.343 3 3z" />
                        </svg>
                        <span className="text-xs font-mono text-gray-200 truncate">{stem.filename}</span>
                      </div>
                      <div className="flex items-center gap-2 flex-shrink-0">
                        <span className="text-[10px] text-gray-500">{stem.size_mb} MB</span>
                        <svg className="w-4 h-4 text-gray-500 group-hover:text-purple-400 transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                        </svg>
                      </div>
                    </a>
                  ))}
                </div>
              </div>
            )}

            {/* Chunk gallery on done */}
            {chunks.length > 0 && (
              <div className="border-t border-gray-700 pt-3">
                <div className="text-xs text-gray-400 mb-2 font-medium">Individual Parts ({chunks.length})</div>
                <div className="grid grid-cols-2 gap-2 max-h-48 overflow-y-auto">
                  {chunks.map((c) => (
                    <div key={c.index} className="bg-gray-800 border border-gray-700 rounded overflow-hidden hover:border-green-500 transition-colors group">
                      <div className="relative cursor-pointer" onClick={() => setLightboxChunkUrl(c.download_url)}>
                        <video src={c.download_url} muted preload="metadata" className="w-full h-20 object-cover bg-gray-900" />
                        <div className="absolute inset-0 flex items-center justify-center bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity">
                          <svg className="w-8 h-8 text-white/90" fill="currentColor" viewBox="0 0 24 24"><path d="M8 5v14l11-7z" /></svg>
                        </div>
                      </div>
                      <div className="px-2 py-1.5 flex items-center justify-between">
                        <div>
                          <div className="text-xs font-medium text-gray-300">Part {c.index + 1}</div>
                          <div className="text-[10px] text-gray-500">Scenes {c.scenes} · {c.size_mb}MB</div>
                        </div>
                        <a href={c.download_url} download onClick={(e) => e.stopPropagation()} className="p-1 text-gray-500 hover:text-green-400 transition-colors" title="Download chunk">
                          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" /></svg>
                        </a>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="flex gap-4">
              <button
                onClick={onClose}
                className="flex-1 px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded font-medium transition-colors"
              >
                Close
              </button>
              {/* Hide the single big Download button on a stems-only export — the
                  per-stem cards above are the right action.  Show it only when
                  we have a real video to download (chunks present, or stems
                  list empty so the user hasn't done a stems-only export). */}
              {downloadUrl && !(stems.length > 0 && chunks.length === 0) && (
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

        {/* Failed phase — show error with resume option */}
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

            {/* Chunk gallery on failure */}
            {chunks.length > 0 && (
              <div className="border-t border-gray-700 pt-3">
                <div className="text-xs text-gray-400 mb-2 font-medium">Completed Parts ({chunks.length} before failure)</div>
                <div className="grid grid-cols-2 gap-2 max-h-48 overflow-y-auto">
                  {chunks.map((c) => (
                    <div key={c.index} className="bg-gray-800 border border-gray-700 rounded overflow-hidden hover:border-orange-500 transition-colors group">
                      <div className="relative cursor-pointer" onClick={() => setLightboxChunkUrl(c.download_url)}>
                        <video src={c.download_url} muted preload="metadata" className="w-full h-20 object-cover bg-gray-900" />
                        <div className="absolute inset-0 flex items-center justify-center bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity">
                          <svg className="w-8 h-8 text-white/90" fill="currentColor" viewBox="0 0 24 24"><path d="M8 5v14l11-7z" /></svg>
                        </div>
                      </div>
                      <div className="px-2 py-1.5 flex items-center justify-between">
                        <div>
                          <div className="text-xs font-medium text-gray-300">Part {c.index + 1}</div>
                          <div className="text-[10px] text-gray-500">Scenes {c.scenes} · {c.size_mb}MB</div>
                        </div>
                        <a href={c.download_url} download onClick={(e) => e.stopPropagation()} className="p-1 text-gray-500 hover:text-green-400 transition-colors" title="Download chunk">
                          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" /></svg>
                        </a>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="flex gap-3">
              <button
                onClick={onClose}
                className="flex-1 px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded font-medium transition-colors"
              >
                Close
              </button>
              <button
                onClick={handleResume}
                className="flex-1 px-4 py-2 bg-orange-600 hover:bg-orange-700 rounded font-medium transition-colors flex items-center justify-center gap-2"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
                Resume
              </button>
              <button
                onClick={() => { setPhase('config'); setExportError(null); setChunks([]); }}
                className="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded font-medium transition-colors"
              >
                Restart
              </button>
            </div>
          </div>
        )}

        {/* Cancelled phase */}
        {phase === 'cancelled' && (
          <div className="space-y-4">
            <div className="text-center mb-2">
              <div className="inline-flex items-center justify-center w-12 h-12 rounded-full bg-yellow-500/20 mb-3">
                <svg className="w-6 h-6 text-yellow-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
              </div>
              <p className="text-sm text-yellow-400">Export was cancelled</p>
              {progressPercent > 0 && (
                <p className="text-xs text-gray-500 mt-1">Stopped at {progressPercent}%</p>
              )}
            </div>

            {/* Chunk gallery on cancel */}
            {chunks.length > 0 && (
              <div className="border-t border-gray-700 pt-3">
                <div className="text-xs text-gray-400 mb-2 font-medium">Completed Parts ({chunks.length})</div>
                <div className="grid grid-cols-2 gap-2 max-h-48 overflow-y-auto">
                  {chunks.map((c) => (
                    <div key={c.index} className="bg-gray-800 border border-gray-700 rounded overflow-hidden hover:border-yellow-500 transition-colors group">
                      <div className="relative cursor-pointer" onClick={() => setLightboxChunkUrl(c.download_url)}>
                        <video src={c.download_url} muted preload="metadata" className="w-full h-20 object-cover bg-gray-900" />
                        <div className="absolute inset-0 flex items-center justify-center bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity">
                          <svg className="w-8 h-8 text-white/90" fill="currentColor" viewBox="0 0 24 24"><path d="M8 5v14l11-7z" /></svg>
                        </div>
                      </div>
                      <div className="px-2 py-1.5 flex items-center justify-between">
                        <div>
                          <div className="text-xs font-medium text-gray-300">Part {c.index + 1}</div>
                          <div className="text-[10px] text-gray-500">Scenes {c.scenes} · {c.size_mb}MB</div>
                        </div>
                        <a href={c.download_url} download onClick={(e) => e.stopPropagation()} className="p-1 text-gray-500 hover:text-green-400 transition-colors" title="Download chunk">
                          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" /></svg>
                        </a>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="flex gap-3">
              <button
                onClick={onClose}
                className="flex-1 px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded font-medium transition-colors"
              >
                Close
              </button>
              <button
                onClick={handleResume}
                className="flex-1 px-4 py-2 bg-orange-600 hover:bg-orange-700 rounded font-medium transition-colors flex items-center justify-center gap-2"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
                Resume
              </button>
              <button
                onClick={() => { setPhase('config'); setExportError(null); setChunks([]); }}
                className="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded font-medium transition-colors"
              >
                Restart
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Chunk video lightbox with download */}
      {lightboxChunkUrl && (
        <div
          className="fixed inset-0 bg-black/80 flex items-center justify-center z-[60]"
          onClick={() => setLightboxChunkUrl(null)}
        >
          <div
            className="relative w-full max-w-3xl mx-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm text-gray-300 font-medium">Chunk Preview</span>
              <div className="flex items-center gap-3">
                <a
                  href={lightboxChunkUrl}
                  download
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-green-600 hover:bg-green-700 rounded text-sm font-medium transition-colors"
                  onClick={(e) => e.stopPropagation()}
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                  </svg>
                  Download
                </a>
                <button
                  onClick={() => setLightboxChunkUrl(null)}
                  className="text-gray-400 hover:text-white text-sm font-medium"
                >
                  Close
                </button>
              </div>
            </div>
            <video
              src={lightboxChunkUrl}
              controls
              autoPlay
              className="w-full rounded-lg shadow-2xl"
            />
          </div>
        </div>
      )}
    </div>
  );
}


function ExportGalleryModal({ projectId, onClose }: { projectId: string; onClose: () => void }) {
  const [exports, setExports] = useState<ExportFileInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [playingUrl, setPlayingUrl] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  const fetchExports = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const res = await listExports(projectId);
      setExports(res.data);
    } catch (err: any) {
      setError(err?.message || 'Failed to load exports');
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    fetchExports();
  }, [fetchExports]);

  const handleDelete = async (filename: string) => {
    try {
      setDeleting(filename);
      await deleteExportFile(projectId, filename);
      setExports((prev) => prev.filter((e) => e.filename !== filename));
      setConfirmDelete(null);
      if (playingUrl?.includes(filename)) setPlayingUrl(null);
    } catch (err: any) {
      setError(err?.message || 'Failed to delete export');
    } finally {
      setDeleting(null);
    }
  };

  const formatDate = (iso: string) => {
    try {
      const d = parseBackendDate(iso);
      if (!d) return iso;
      return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) +
        ' ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    } catch { return iso; }
  };

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-gray-900 border border-gray-800 rounded-xl w-full max-w-3xl mx-4 max-h-[85vh] flex flex-col shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800">
          <div className="flex items-center gap-3">
            <Film size={20} className="text-blue-400" />
            <h2 className="text-lg font-semibold text-white">Export Gallery</h2>
            <span className="text-xs text-gray-500">{exports.length} export{exports.length !== 1 ? 's' : ''}</span>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-white transition-colors text-xl leading-none">&times;</button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-6">
          {loading ? (
            <div className="flex items-center justify-center py-16 text-gray-400">
              <Loader2 className="animate-spin mr-3" size={20} />
              Loading exports…
            </div>
          ) : error ? (
            <div className="text-center py-16">
              <p className="text-red-400 mb-3">{error}</p>
              <button onClick={fetchExports} className="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm transition-colors">
                Retry
              </button>
            </div>
          ) : exports.length === 0 ? (
            <div className="text-center py-16 text-gray-500">
              <Film size={40} className="mx-auto mb-3 opacity-40" />
              <p>No exports yet</p>
              <p className="text-sm mt-1">Exported videos will appear here</p>
            </div>
          ) : (
            <div className="space-y-3">
              {exports.map((exp) => (
                <div
                  key={exp.filename}
                  className="bg-gray-800/60 border border-gray-700/50 rounded-lg p-4 hover:border-gray-600 transition-colors"
                >
                  <div className="flex items-center justify-between gap-4">
                    {/* Info */}
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-white truncate" title={exp.filename}>
                        {exp.filename}
                      </p>
                      <div className="flex items-center gap-3 mt-1 text-xs text-gray-400">
                        <span>{exp.size_mb.toFixed(1)} MB</span>
                        <span className="text-gray-600">•</span>
                        <span>{formatDate(exp.created_at)}</span>
                      </div>
                    </div>

                    {/* Actions */}
                    <div className="flex items-center gap-2 shrink-0">
                      <button
                        onClick={() => setPlayingUrl(playingUrl === exp.download_url ? null : exp.download_url)}
                        className="p-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-white transition-colors"
                        title="Watch"
                      >
                        {playingUrl === exp.download_url ? <Pause size={16} /> : <Play size={16} />}
                      </button>
                      <a
                        href={exp.download_url}
                        download
                        className="p-2 bg-green-600 hover:bg-green-700 rounded-lg text-white transition-colors"
                        title="Download"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <Download size={16} />
                      </a>
                      {confirmDelete === exp.filename ? (
                        <div className="flex items-center gap-1">
                          <button
                            onClick={() => handleDelete(exp.filename)}
                            disabled={deleting === exp.filename}
                            className="px-2 py-1 bg-red-600 hover:bg-red-700 rounded text-xs font-medium transition-colors disabled:opacity-50"
                          >
                            {deleting === exp.filename ? '…' : 'Yes'}
                          </button>
                          <button
                            onClick={() => setConfirmDelete(null)}
                            className="px-2 py-1 bg-gray-600 hover:bg-gray-500 rounded text-xs font-medium transition-colors"
                          >
                            No
                          </button>
                        </div>
                      ) : (
                        <button
                          onClick={() => setConfirmDelete(exp.filename)}
                          className="p-2 bg-gray-700 hover:bg-red-600 rounded-lg text-gray-300 hover:text-white transition-colors"
                          title="Delete"
                        >
                          <XCircle size={16} />
                        </button>
                      )}
                    </div>
                  </div>

                  {/* Inline video player */}
                  {playingUrl === exp.download_url && (
                    <div className="mt-3">
                      <video
                        src={exp.download_url}
                        controls
                        autoPlay
                        className="w-full rounded-lg shadow-lg"
                        style={{ maxHeight: '400px' }}
                      />
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}


// These are the modes the backend `_run_sequential_auto_gen` actually handles.
// Don't add modes here without a matching branch in the dispatcher — they'll
// silently no-op and the run will "complete" with nothing generated.
type AutoGenMode =
  | 'all_images'
  | 'missing_images_independent'
  | 'all_video_single'
  | 'missing_videos_single'
  | 'all_video_fflf'
  | 'all_video_v2v';

const AUTO_GEN_OPTIONS: { value: AutoGenMode; label: string; description: string }[] = [
  {
    value: 'all_video_fflf',
    label: 'Full Pipeline \u2014 FF/LF Chaining',
    description:
      "Enhance prompts and generate first-frame image + video for every scene. Uses the previous scene's last frame as this scene's first frame for visual continuity.",
  },
  {
    value: 'all_video_v2v',
    label: 'Full Pipeline \u2014 V2V Extend',
    description:
      "Like FF/LF but uses the previous scene's video tail directly as conditioning, producing smoother transitions between cuts. Generates one scene at a time.",
  },
  {
    value: 'all_video_single',
    label: 'Full Pipeline \u2014 Single Image (no Last Frame)',
    description:
      'Enhance prompts and generate first-frame image + video for every scene independently. No previous-frame continuity. Runs in parallel across workers.',
  },
  {
    value: 'missing_videos_single',
    label: 'Missing Videos Only (Single Image)',
    description:
      'Only scenes that lack a chosen video \u2014 keep existing images, generate videos via I2V. Runs in parallel.',
  },
  {
    value: 'all_images',
    label: 'All Images \u2014 Use Previous Scene as Reference',
    description:
      'Generate first-frame images for every scene. Each scene uses the previous scene as an extra reference for style continuity. No video generation.',
  },
  {
    value: 'missing_images_independent',
    label: 'Missing Images \u2014 Independent',
    description:
      "Only scenes that lack a chosen image \u2014 generate independently (no previous-scene reference). Runs in parallel.",
  },
];

function AutoGenerateModal({ projectId, onClose, onMinimize, onStarted, autoGenStatus, autoGenMode, autoGenCompleted, autoGenTotal, autoGenStep, autoGenSceneName, autoGenBatchRunId }: {
  projectId: string;
  onClose: () => void;
  onMinimize: () => void;
  onStarted: () => void;
  autoGenStatus: string;
  autoGenMode: string;
  autoGenCompleted: number;
  autoGenTotal: number;
  autoGenStep: string | null;
  autoGenSceneName: string | null;
  autoGenBatchRunId: string | null;
}) {
  const navigate = useNavigate();
  const currentProject = useAppStore((s) => s.currentProject);
  const isNarration = currentProject?.mode === 'narration_images' || currentProject?.mode === 'narration_video';
  const [mode, setMode] = useState<AutoGenMode>('all_video_fflf');
  const [isStarting, setIsStarting] = useState(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [startTime, setStartTime] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);

  // Advanced options
  const [overrideFullSet, setOverrideFullSet] = useState(false);
  const [twoPass, setTwoPass] = useState(true);
  const [useStoryFlow, setUseStoryFlow] = useState(true);
  const [lipsyncEnabled, setLipsyncEnabled] = useState(true);
  const [vocalsOnlyForLipsync, setVocalsOnlyForLipsync] = useState(false);
  const [skipAudioMux, setSkipAudioMux] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const isBackendRunning = autoGenStatus === 'running';
  const isBackendDone = autoGenStatus === 'done' || autoGenStatus === 'completed';
  const isBackendFailed = autoGenStatus === 'failed';
  const isBackendCancelled = autoGenStatus === 'cancelled';
  const isBackendTerminal = isBackendDone || isBackendFailed || isBackendCancelled;
  const progressPct = autoGenTotal > 0 ? Math.round((autoGenCompleted / autoGenTotal) * 100) : 0;

  // Reset elapsed timer when a new batch run starts
  useEffect(() => {
    if (autoGenBatchRunId) {
      setElapsed(0);
      setStartTime(Date.now());
    }
  }, [autoGenBatchRunId]);

  // Stop the timer and clear startTime when backend reaches a terminal state
  useEffect(() => {
    if (isBackendTerminal && startTime) {
      setStartTime(null);
    }
  }, [isBackendTerminal, startTime]);

  // Elapsed time timer
  useEffect(() => {
    if (isBackendRunning && !startTime) {
      setStartTime(Date.now());
    }
    if (isBackendRunning && startTime) {
      const timer = setInterval(() => {
        setElapsed(Math.floor((Date.now() - startTime) / 1000));
      }, 1000);
      return () => clearInterval(timer);
    }
    return undefined;
  }, [isBackendRunning, startTime]);

  const formatElapsed = (secs: number) => {
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    const s = secs % 60;
    if (h > 0) return `${h}h ${m}m ${s}s`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
  };

  const handleQueue = async () => {
    setIsStarting(true);
    setError(null);
    setStatusMsg(null);

    try {
      // Forward chapter scope from Zustand FIRST so we can also pass it
      // through to the pre-step story-flow call (otherwise that call
      // generates ideas for all 328 scenes when the user only asked for
      // 23 in this chapter).
      const _zScope = useAppStore.getState().chapterScope;
      const _scopeForRun = _zScope?.chapterId ?? undefined;

      // Generate video flow first if useStoryFlow is on and we're producing
      // video — but ONLY if there's actually work to do.  When every scene
      // in scope already has a flow_idea, regenerating is pure waste and
      // overwrites edits the user may have made.
      if (useStoryFlow && (mode.includes('video') || mode === 'all_images')) {
        const _allScenes = useAppStore.getState().scenes || [];
        const _inScope: any[] = _zScope
          ? _allScenes.filter((s: any) => _zScope.sceneIds.has(String(s.id)))
          : _allScenes;
        const _missingFlow = _inScope.some((s: any) => {
          const flow = ((s.parameters || {}).flow_idea || '').trim();
          return !flow;
        });
        if (_missingFlow) {
          const _label = _zScope ? `chapter "${_zScope.name}"` : 'project';
          setStatusMsg(
            (isNarration ? 'Generating story flow ideas' : 'Generating video flow ideas')
            + ` for ${_label}...`
          );
          try {
            await generateVideoFlow(projectId, _scopeForRun);
          } catch (e) {
            console.warn('Story/video flow generation failed, continuing with existing flow data:', e);
          }
        } else {
          console.info(
            `[AutoGen] Skipping flow gen — all ${_inScope.length} ${_zScope ? 'chapter ' : ''}scenes already have flow_idea`
          );
        }
      }

      setStatusMsg('Queuing generation jobs...');
      await startSequentialAutoGen(
        projectId,
        mode,
        overrideFullSet,
        false, // vocalsOnlyAudio
        skipAudioMux,
        twoPass,
        useStoryFlow,
        lipsyncEnabled,
        vocalsOnlyForLipsync,
        _scopeForRun,
      );

      setStatusMsg(null);
      setElapsed(0);
      setStartTime(Date.now());
      setIsStarting(false);
      onStarted(); // start polling in AppLayout
    } catch (e: any) {
      const detail = e?.response?.data?.detail || e?.message || 'Auto-generation failed';
      setError(detail);
      setIsStarting(false);
    }
  };

  const handleCancel = async () => {
    try {
      await cancelSequentialAutoGen(projectId);
    } catch { /* ignore */ }
  };

  const getModeLabel = (m: string) => {
    switch (m) {
      case 'all_video_fflf': return 'Full Pipeline \u2014 FF/LF Chaining';
      case 'all_video_v2v': return 'Full Pipeline \u2014 V2V Extend';
      case 'all_video_single': return 'Full Pipeline \u2014 Single Image';
      case 'missing_videos_single': return 'Missing Videos (Single Image)';
      case 'all_images': return 'All Images (Prev Scene Ref)';
      case 'missing_images_independent': return 'Missing Images (Independent)';
      default: return m.replace(/_/g, ' ');
    }
  };

  // Show progress view when backend is running or terminal
  const showProgress = isBackendRunning || isBackendTerminal;

  return (
    <div
      className="fixed inset-0 bg-black/60 flex items-center justify-center z-[9999] p-4"
      onClick={(e) => { if (e.target === e.currentTarget) onMinimize(); }}
    >
      <div className="bg-gray-900 border border-gray-700 rounded-xl w-full max-w-lg max-h-[90vh] overflow-y-auto p-6">
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xl font-bold text-gray-100">Auto Generate</h2>
          <div className="flex items-center gap-1">
            {(isBackendRunning || isStarting) && (
              <button
                onClick={onMinimize}
                className="p-1.5 hover:bg-gray-800 rounded-lg transition-colors text-gray-500 hover:text-gray-300"
                title="Minimize — keep running in background"
              >
                <Minimize2 size={16} />
              </button>
            )}
            {!isStarting && (
              <button
                onClick={onClose}
                className="p-1.5 hover:bg-gray-800 rounded-lg transition-colors text-gray-500 hover:text-gray-300"
                title="Close"
              >
                <XCircle size={16} />
              </button>
            )}
          </div>
        </div>

        {/* ─── Progress View (when running or terminal) ─── */}
        {showProgress && (
          <div className="flex flex-col gap-4">
            {/* Mode label */}
            <div className="flex items-center gap-2">
              {isBackendRunning && <Loader2 size={16} className="text-purple-400 animate-spin" />}
              {isBackendDone && <CheckCircle size={16} className="text-green-400" />}
              {isBackendFailed && <XCircle size={16} className="text-red-400" />}
              {isBackendCancelled && <XCircle size={16} className="text-yellow-400" />}
              <span className="text-sm font-medium text-purple-300">
                {getModeLabel(autoGenMode)}
              </span>
            </div>

            {/* Progress bar */}
            <div className="bg-gray-800 rounded-lg h-7 overflow-hidden relative">
              <div
                className="h-full rounded-lg transition-all duration-700 ease-out"
                style={{
                  width: `${progressPct}%`,
                  background: isBackendDone ? 'linear-gradient(90deg, #059669, #34d399)' :
                              isBackendFailed ? 'linear-gradient(90deg, #dc2626, #f87171)' :
                              isBackendCancelled ? 'linear-gradient(90deg, #d97706, #fbbf24)' :
                              'linear-gradient(90deg, #7c3aed, #a78bfa)',
                }}
              />
              <div className="absolute inset-0 flex items-center justify-center text-xs font-semibold text-white">
                {autoGenCompleted} / {autoGenTotal} scenes ({progressPct}%)
              </div>
            </div>

            {/* Current scene + step */}
            {isBackendRunning && (
              <div className="text-sm space-y-1">
                {autoGenSceneName && (
                  <div>
                    <span className="text-gray-500">Current: </span>
                    <span className="text-gray-200 font-medium">{autoGenSceneName}</span>
                  </div>
                )}
                {autoGenStep && (
                  <div>
                    <span className="text-gray-500">Step: </span>
                    <span className="text-purple-400">{autoGenStep}</span>
                  </div>
                )}
                {elapsed > 0 && (
                  <div>
                    <span className="text-gray-500">Time: </span>
                    <span className="text-blue-400 font-medium">{formatElapsed(elapsed)}</span>
                  </div>
                )}
              </div>
            )}

            {/* Terminal status message */}
            {isBackendTerminal && (
              <div className={`p-3 rounded-lg text-sm font-medium ${
                isBackendDone ? 'bg-green-950 border border-green-900 text-green-300' :
                isBackendCancelled ? 'bg-yellow-950 border border-yellow-900 text-yellow-300' :
                'bg-red-950 border border-red-900 text-red-300'
              }`}>
                {isBackendDone && `Completed! All ${autoGenCompleted} scenes processed${elapsed > 0 ? ` in ${formatElapsed(elapsed)}` : ''}.`}
                {isBackendCancelled && `Cancelled after ${autoGenCompleted} of ${autoGenTotal} scenes.`}
                {isBackendFailed && `Failed after ${autoGenCompleted} of ${autoGenTotal} scenes.`}
              </div>
            )}

            {/* Action buttons */}
            <div className="flex gap-3">
              {isBackendRunning && (
                <>
                  <button
                    onClick={onMinimize}
                    className="flex-1 px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg font-medium transition-colors text-sm"
                  >
                    Minimize
                  </button>
                  <button
                    onClick={handleCancel}
                    className="flex-1 px-4 py-2 bg-red-600 hover:bg-red-700 rounded-lg font-medium text-white transition-colors text-sm"
                  >
                    Cancel
                  </button>
                </>
              )}
              {isBackendTerminal && (
                <button
                  onClick={onClose}
                  className="flex-1 px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg font-medium transition-colors text-sm"
                >
                  Close
                </button>
              )}
              {autoGenBatchRunId && (
                <button
                  onClick={() => { onClose(); navigate(`/batches/${autoGenBatchRunId}`); }}
                  className="flex-1 px-4 py-2 bg-purple-600/20 hover:bg-purple-600/30 border border-purple-500/30 rounded-lg font-medium text-purple-300 transition-colors text-sm"
                >
                  View Details
                </button>
              )}
            </div>
          </div>
        )}

        {/* ─── Config View (before starting) ─── */}
        {!showProgress && (
          <>
            <p className="text-sm text-gray-400 mb-4">
              Select a mode, configure options, then start. You can minimize this window while it runs.
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
                  } ${isStarting ? 'opacity-60 cursor-default' : ''}`}
                >
                  <input
                    type="radio"
                    name="auto_gen_mode"
                    value={opt.value}
                    checked={mode === opt.value}
                    onChange={() => setMode(opt.value)}
                    disabled={isStarting}
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
                  <input type="checkbox" checked={overrideFullSet} onChange={e => setOverrideFullSet(e.target.checked)} disabled={isStarting}
                    className="w-4 h-4 rounded border-gray-600 bg-gray-700 accent-amber-500" />
                  <div>
                    <span className="text-sm text-gray-200">Override \u2014 Regenerate Full Set</span>
                    <p className="text-xs text-gray-500">For "Missing" modes: regenerate every scene instead of only the missing ones. Prior versions remain in the gallery.</p>
                  </div>
                </label>

                <label className="flex items-center gap-2.5 cursor-pointer">
                  <input type="checkbox" checked={twoPass} onChange={e => setTwoPass(e.target.checked)} disabled={isStarting}
                    className="w-4 h-4 rounded border-gray-600 bg-gray-700 accent-purple-500" />
                  <div>
                    <span className="text-sm text-gray-200">Two-Pass Image Generation</span>
                    <p className="text-xs text-gray-500">Pass 1: scene composition, Pass 2: character compositing</p>
                  </div>
                </label>

                <label className="flex items-center gap-2.5 cursor-pointer">
                  <input type="checkbox" checked={useStoryFlow} onChange={e => setUseStoryFlow(e.target.checked)} disabled={isStarting}
                    className="w-4 h-4 rounded border-gray-600 bg-gray-700 accent-purple-500" />
                  <div>
                    <span className="text-sm text-gray-200">Use Story Flow</span>
                    <p className="text-xs text-gray-500">Incorporate {isNarration ? 'story' : 'video'} flow ideas into prompt generation</p>
                  </div>
                </label>

                <label className="flex items-center gap-2.5 cursor-pointer">
                  <input type="checkbox" checked={lipsyncEnabled} onChange={e => setLipsyncEnabled(e.target.checked)} disabled={isStarting}
                    className="w-4 h-4 rounded border-gray-600 bg-gray-700 accent-purple-500" />
                  <div>
                    <span className="text-sm text-gray-200">Lipsync-Aware Prompts</span>
                    <p className="text-xs text-gray-500">Tell the LLM to include singing/speaking cues</p>
                  </div>
                </label>

                {lipsyncEnabled && (
                  <label className="flex items-center gap-2.5 cursor-pointer ml-6">
                    <input type="checkbox" checked={vocalsOnlyForLipsync} onChange={e => setVocalsOnlyForLipsync(e.target.checked)} disabled={isStarting}
                      className="w-4 h-4 rounded border-gray-600 bg-gray-700 accent-purple-500" />
                    <div>
                      <span className="text-sm text-gray-200">Vocals-Only Audio for Lipsync</span>
                      <p className="text-xs text-gray-500">Use isolated vocal stem instead of full mix</p>
                    </div>
                  </label>
                )}

                <label className="flex items-center gap-2.5 cursor-pointer">
                  <input type="checkbox" checked={skipAudioMux} onChange={e => setSkipAudioMux(e.target.checked)} disabled={isStarting}
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
                disabled={isStarting}
                className="flex-1 px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Cancel
              </button>
              <button
                onClick={handleQueue}
                disabled={isStarting}
                className="flex-1 px-4 py-2 bg-purple-600 hover:bg-purple-700 rounded-lg font-semibold text-white transition-colors disabled:opacity-70 disabled:cursor-not-allowed"
              >
                {isStarting ? 'Starting...' : 'Start Auto Gen'}
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
          </>
        )}
      </div>
    </div>
  );
}
