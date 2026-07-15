import { useEffect, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  getProject, getScenes, getAssets, getConcept, getLyrics, getWorkflowConfigs,
} from '@/api/client';
import { useAppStore } from '@/store';
import type { Scene, WorkflowConfig } from '@/types/index';
import type { CharacterInfo } from '@/components/SceneEditor/ReferenceSelector';

/**
 * Loads everything a mobile project screen needs and hydrates the Zustand
 * store (so shared components like ReferenceSelector work on standalone
 * routes). React-Query caches by key, so calling this from several mobile
 * screens shares the same fetches. The global useJobEvents() SSE (mounted in
 * App.tsx) keeps store.jobs live everywhere.
 */
export function useProjectData(projectId: string) {
  const setProject = useAppStore((s) => s.setProject);
  const setAssets = useAppStore((s) => s.setAssets);
  const setScenes = useAppStore((s) => s.setScenes);

  const projectQ = useQuery({ queryKey: ['project', projectId], queryFn: async () => (await getProject(projectId)).data, enabled: !!projectId });
  const scenesQ = useQuery({ queryKey: ['scenes', projectId], queryFn: async () => (await getScenes(projectId)).data, enabled: !!projectId });
  const assetsQ = useQuery({ queryKey: ['assets', projectId], queryFn: async () => (await getAssets(projectId)).data, enabled: !!projectId });
  const conceptQ = useQuery({ queryKey: ['concept', projectId], queryFn: async () => (await getConcept(projectId)).data, enabled: !!projectId });
  const lyricsQ = useQuery({ queryKey: ['lyrics', projectId], queryFn: async () => (await getLyrics(projectId)).data, enabled: !!projectId });
  const workflowsQ = useQuery({ queryKey: ['workflows'], queryFn: async () => (await getWorkflowConfigs()).data });

  useEffect(() => { if (projectQ.data) setProject(projectQ.data); }, [projectQ.data, setProject]);
  useEffect(() => { if (assetsQ.data) setAssets(assetsQ.data); }, [assetsQ.data, setAssets]);
  useEffect(() => { if (scenesQ.data) setScenes(scenesQ.data); }, [scenesQ.data, setScenes]);

  const scenes: Scene[] = useMemo(
    () => [...(scenesQ.data || [])].sort((a, b) => a.order_index - b.order_index),
    [scenesQ.data],
  );
  const concept: any = conceptQ.data || null;
  const characters: CharacterInfo[] = concept?.characters || [];
  const imageWorkflows: WorkflowConfig[] = (workflowsQ.data || []).filter((w) => w.workflow_type === 'image');
  const words: any[] = (lyricsQ.data as any)?.words || [];

  return {
    project: projectQ.data,
    scenes,
    assets: assetsQ.data || [],
    concept,
    characters,
    imageWorkflows,
    words,
    defaultWidth: concept?.image_resolution_width || concept?.resolution_width || 1536,
    defaultHeight: concept?.image_resolution_height || concept?.resolution_height || 864,
    loading: projectQ.isLoading || scenesQ.isLoading,
    projectLoading: projectQ.isLoading,
  };
}

export const fileUrl = (p?: string | null) => (p ? `/api/files/${p}` : '');

export function deriveLyric(scene: Scene, words: any[]): string {
  const stored = scene.parameters?.lyrics;
  if (stored && String(stored).trim()) return String(stored).trim();
  if (!words?.length) return '';
  return words
    .filter((w) => typeof w.start === 'number' && w.start >= scene.start_time - 0.05 && w.start < scene.end_time)
    .map((w) => w.word)
    .join(' ')
    .replace(/\s+/g, ' ')
    .trim();
}
