import { useState, useEffect, useCallback } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Save, Sparkles } from 'lucide-react';
import { getVideoFlow, generateVideoFlow, updateSceneFlow, getScenes } from '@/api/client';
import { useAppStore } from '@/store';
import FlowGenerationStatus from './FlowGenerationStatus';

interface FlowIdea {
  scene_id: string;
  flow_idea: string;
}

interface VideoFlowPanelProps {
  projectId: string;
}

export default function VideoFlowPanel({ projectId }: VideoFlowPanelProps) {
  const [ideas, setIdeas] = useState<FlowIdea[]>([]);
  const [editedIds, setEditedIds] = useState<Set<string>>(new Set());
  const [showFlowStatus, setShowFlowStatus] = useState(false);
  const scenes = useAppStore(s => s.scenes);
  const setActiveScene = useAppStore(s => s.setActiveScene);
  const currentProject = useAppStore(s => s.currentProject);
  const queryClient = useQueryClient();
  const isNarration = currentProject?.mode === 'narration_images' || currentProject?.mode === 'narration_video';

  // Fetch existing flow
  const { data: flowData, isLoading } = useQuery({
    queryKey: ['video-flow', projectId],
    queryFn: async () => {
      const response = await getVideoFlow(projectId);
      return response.data;
    },
    enabled: !!projectId,
    staleTime: 30_000,
  });

  useEffect(() => {
    if (flowData?.ideas) {
      setIdeas(flowData.ideas);
      setEditedIds(new Set());
    }
  }, [flowData]);

  // Generate flow mutation — refresh scenes first to ensure store is in sync with DB
  const generateMutation = useMutation({
    mutationFn: async () => {
      // Refresh scenes from DB before generating to ensure accurate count
      try {
        const scenesRes = await getScenes(projectId);
        useAppStore.getState().setScenes(scenesRes.data);
      } catch { /* continue with existing scenes */ }
      const response = await generateVideoFlow(projectId);
      return response.data;
    },
    onSuccess: (data) => {
      if (data?.ideas) {
        setIdeas(data.ideas);
        setEditedIds(new Set());
        queryClient.invalidateQueries({ queryKey: ['video-flow', projectId] });
        // Also refresh scenes since flow_idea is stored in scene.parameters
        queryClient.invalidateQueries({ queryKey: ['scenes', projectId] });
      }
    },
  });

  // Save a single scene's flow idea
  const saveMutation = useMutation({
    mutationFn: async (idea: FlowIdea) => {
      await updateSceneFlow(projectId, idea.scene_id, idea.flow_idea);
      return idea.scene_id;
    },
    onSuccess: (sceneId) => {
      setEditedIds((prev) => {
        const next = new Set(prev);
        next.delete(sceneId);
        return next;
      });
      queryClient.invalidateQueries({ queryKey: ['scenes', projectId] });
    },
  });

  const updateIdea = useCallback((sceneId: string, text: string) => {
    setIdeas((prev) =>
      prev.map((idea) =>
        idea.scene_id === sceneId ? { ...idea, flow_idea: text } : idea
      )
    );
    setEditedIds((prev) => new Set(prev).add(sceneId));
  }, []);

  // Save all edited ideas
  const saveAllEdited = async () => {
    const toSave = ideas.filter((idea) => editedIds.has(idea.scene_id));
    for (const idea of toSave) {
      await saveMutation.mutateAsync(idea);
    }
  };

  // Find scene name by ID
  const getSceneName = (sceneId: string) => {
    const scene = scenes.find((s) => s.id === sceneId);
    return scene ? (scene.name || `Scene ${scene.order_index + 1}`) : `Scene`;
  };

  const getSceneIndex = (sceneId: string) => {
    const scene = scenes.find((s) => s.id === sceneId);
    return scene ? scene.order_index + 1 : 0;
  };

  const hasAnyFlow = ideas.some((idea) => idea.flow_idea);

  return (
    <div className="h-full flex flex-col overflow-hidden">
      <div className="p-3 border-b border-gray-800 flex items-center justify-between flex-shrink-0">
        <span className="text-xs font-medium text-gray-300">{isNarration ? 'Story Flow' : 'Video Flow'}</span>
        {editedIds.size > 0 && (
          <button
            onClick={saveAllEdited}
            disabled={saveMutation.isPending}
            className="flex items-center gap-1 px-2.5 py-1 bg-green-600 hover:bg-green-700 rounded text-xs font-medium text-white transition-colors"
          >
            <Save size={12} />
            {saveMutation.isPending ? 'Saving...' : `Save (${editedIds.size})`}
          </button>
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {/* Generate Flow button */}
        <button
          onClick={() => { setShowFlowStatus(true); generateMutation.mutate(); }}
          disabled={generateMutation.isPending || scenes.length === 0}
          className="w-full flex items-center justify-center gap-2 px-3 py-2.5 bg-purple-600 hover:bg-purple-700 rounded text-sm font-medium text-white transition-colors disabled:opacity-50"
        >
          <Sparkles size={16} />
          {generateMutation.isPending
            ? 'Generating Flow...'
            : hasAnyFlow
              ? 'Regenerate Flow'
              : 'Generate Flow'
          }
        </button>

        {/* Inline hint while generating */}
        {generateMutation.isPending && (
          <div className="text-center text-xs text-gray-400 py-1">
            Check the status window for progress...
          </div>
        )}

        {scenes.length === 0 && (
          <div className="text-center py-6 text-gray-500 text-xs">
            No scenes created yet. Process audio and create scenes first.
          </div>
        )}

        {/* Scene flow idea cards */}
        {ideas.length > 0 && (
          <div className="space-y-2">
            {ideas.map((idea) => {
              const isEdited = editedIds.has(idea.scene_id);
              return (
                <div
                  key={idea.scene_id}
                  className={`p-2.5 rounded border transition-colors ${
                    isEdited
                      ? 'bg-gray-800/80 border-yellow-700/50'
                      : 'bg-gray-800/40 border-gray-700/50'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1.5">
                    <button
                      onClick={() => {
                        const scene = scenes.find((s) => s.id === idea.scene_id);
                        if (scene) setActiveScene(scene);
                      }}
                      className="text-xs font-medium text-blue-400 hover:text-blue-300 transition-colors"
                    >
                      {getSceneIndex(idea.scene_id)}. {getSceneName(idea.scene_id)}
                    </button>
                    {isEdited && (
                      <button
                        onClick={() => saveMutation.mutate(idea)}
                        disabled={saveMutation.isPending}
                        className="p-1 text-green-400 hover:text-green-300 transition-colors"
                        title="Save this scene's idea"
                      >
                        <Save size={12} />
                      </button>
                    )}
                  </div>
                  <textarea
                    value={idea.flow_idea}
                    onChange={(e) => updateIdea(idea.scene_id, e.target.value)}
                    placeholder="Scene idea will appear here after generation..."
                    className="w-full px-2 py-1.5 bg-gray-900 border border-gray-700 rounded text-xs text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500 h-16 resize-none"
                  />
                </div>
              );
            })}
          </div>
        )}

        {/* Show placeholder cards for scenes without flow data */}
        {ideas.length === 0 && scenes.length > 0 && !isLoading && (
          <div className="space-y-2">
            {scenes
              .slice()
              .sort((a, b) => a.order_index - b.order_index)
              .map((scene) => (
                <div
                  key={scene.id}
                  className="p-2.5 bg-gray-800/30 border border-gray-700/30 rounded"
                >
                  <div className="text-xs font-medium text-gray-500 mb-1">
                    {scene.order_index + 1}. {scene.name || `Scene ${scene.order_index + 1}`}
                  </div>
                  <div className="text-[10px] text-gray-600 italic">
                    No flow idea yet — click "Generate Flow" above
                  </div>
                </div>
              ))}
          </div>
        )}
      </div>

      {/* Floating status window for flow generation progress */}
      {showFlowStatus && (
        <FlowGenerationStatus
          projectId={projectId}
          isGenerating={generateMutation.isPending}
          isNarration={isNarration}
          onDismiss={() => setShowFlowStatus(false)}
        />
      )}
    </div>
  );
}
