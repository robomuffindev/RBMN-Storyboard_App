import { useState, useEffect, useRef } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Plus, Settings, Trash2, ArrowLeft, Layers } from 'lucide-react';
import { getProjects, createProject, deleteProject, startBatchRun, getBatchStatus, cancelBatch } from '@/api/client';
import BatchItemAddModal from '@/components/BatchMode/BatchItemAddModal';
import BatchQueuePanel from '@/components/BatchMode/BatchQueuePanel';
import type { Project, ProjectMode, BatchItemConfig, BatchRunStatus } from '@/types/index';

export default function ProjectList() {
  const navigate = useNavigate();
  const [showNewProjectModal, setShowNewProjectModal] = useState(false);
  const [newProjectName, setNewProjectName] = useState('');
  const [newProjectMode, setNewProjectMode] = useState<ProjectMode>('music_video');
  const [isCreating, setIsCreating] = useState(false);

  // Batch mode state
  const [batchMode, setBatchMode] = useState(false);
  const [batchItems, setBatchItems] = useState<BatchItemConfig[]>([]);
  const [showAddItemModal, setShowAddItemModal] = useState(false);
  const [batchRunStatus, setBatchRunStatus] = useState<BatchRunStatus | null>(null);
  const [isRunningBatch, setIsRunningBatch] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const { data: projects = [], refetch } = useQuery({
    queryKey: ['projects'],
    queryFn: async () => {
      const response = await getProjects();
      // Axios wraps in response.data; ensure we always return an array
      const data = response.data;
      return Array.isArray(data) ? data : [];
    },
  });

  const deleteProjectMutation = useMutation({
    mutationFn: async (projectId: string) => {
      await deleteProject(projectId);
    },
    onSuccess: () => {
      refetch();
    },
  });

  const handleCreateProject = async () => {
    if (!newProjectName.trim()) return;

    setIsCreating(true);
    try {
      const response = await createProject({
        name: newProjectName,
        mode: newProjectMode,
      });
      setNewProjectName('');
      setShowNewProjectModal(false);
      refetch();
      navigate(`/project/${response.data.id}`);
    } catch (error) {
      console.error('Failed to create project:', error);
    } finally {
      setIsCreating(false);
    }
  };

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const handleAddBatchItem = (item: BatchItemConfig) => {
    setBatchItems((prev) => [...prev, item]);
  };

  const handleUpdateBatchItem = (index: number, item: BatchItemConfig) => {
    setBatchItems((prev) => prev.map((it, i) => (i === index ? item : it)));
  };

  const handleRemoveBatchItem = (index: number) => {
    setBatchItems((prev) => prev.filter((_, i) => i !== index));
  };

  const handleRunBatch = async () => {
    if (batchItems.length === 0) return;
    setIsRunningBatch(true);

    try {
      const payload = batchItems.map(({ id, audioFile, ...rest }) => rest);
      const response = await startBatchRun(payload);
      const status = response.data;
      setBatchRunStatus(status);

      // Start polling
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        try {
          const pollResponse = await getBatchStatus(status.batch_id);
          const updated = pollResponse.data;
          setBatchRunStatus(updated);

          if (updated.status === 'done' || updated.status === 'failed' || updated.status === 'cancelled') {
            if (pollRef.current) clearInterval(pollRef.current);
            pollRef.current = null;
            setIsRunningBatch(false);
            refetch(); // Refresh project list since batch creates projects
          }
        } catch {
          // Polling error — keep trying
        }
      }, 3000);
    } catch (error) {
      console.error('Failed to start batch run:', error);
      setIsRunningBatch(false);
    }
  };

  const handleCancelBatch = async () => {
    if (!batchRunStatus) return;
    try {
      await cancelBatch(batchRunStatus.batch_id);
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = null;
      setIsRunningBatch(false);
      setBatchRunStatus((prev) => prev ? { ...prev, status: 'cancelled' } : null);
    } catch (error) {
      console.error('Failed to cancel batch:', error);
    }
  };

  const handleCloseBatchMode = () => {
    if (isRunningBatch) return;
    setBatchMode(false);
    setBatchRunStatus(null);
  };

  const getModeLabel = (mode: ProjectMode) => {
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
    <div className="min-h-screen bg-gray-950 text-gray-100 p-8">
      <div className="max-w-7xl mx-auto">
        <div className="flex justify-between items-center mb-8">
          <div className="flex items-center gap-4">
            <button
              onClick={() => navigate('/')}
              className="px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded text-sm font-medium transition-colors flex items-center gap-2"
            >
              <ArrowLeft size={20} />
              Back
            </button>
            <h1 className="text-4xl font-bold">Robomuffin Idea Factory</h1>
          </div>
          <button
            onClick={() => navigate('/settings')}
            className="px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded text-sm font-medium transition-colors flex items-center gap-2"
          >
            <Settings size={20} />
            Settings
          </button>
        </div>

        <div className="mb-8 flex gap-3">
          <button
            onClick={() => setShowNewProjectModal(true)}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded text-sm font-medium transition-colors flex items-center gap-2"
          >
            <Plus size={20} />
            New Project
          </button>
          <button
            onClick={() => setBatchMode(true)}
            className="px-4 py-2 bg-purple-600 hover:bg-purple-700 rounded text-sm font-medium transition-colors flex items-center gap-2"
          >
            <Layers size={20} />
            Batch Mode
          </button>
        </div>

        {batchMode ? (
          <>
            <BatchQueuePanel
              items={batchItems}
              onUpdateItem={handleUpdateBatchItem}
              onRemoveItem={handleRemoveBatchItem}
              onAddMore={() => setShowAddItemModal(true)}
              onRunBatch={handleRunBatch}
              onClose={handleCloseBatchMode}
              batchStatus={batchRunStatus}
              isRunning={isRunningBatch}
              onCancel={handleCancelBatch}
            />
            {showAddItemModal && (
              <BatchItemAddModal
                onAdd={handleAddBatchItem}
                onClose={() => setShowAddItemModal(false)}
              />
            )}
          </>
        ) : projects.length === 0 ? (
          <div className="bg-gray-900 border border-gray-800 rounded-lg text-center py-12 px-6">
            <p className="text-gray-400 mb-4">No projects yet. Create one to get started!</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {projects.map((project: Project) => (
              <div
                key={project.id}
                className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden hover:border-gray-700 transition-colors group cursor-pointer"
                onClick={() => navigate(`/project/${project.id}`)}
              >
                <div className="h-40 bg-gradient-to-br from-gray-800 to-gray-900 flex items-center justify-center">
                  <div className="text-gray-500 text-sm font-medium">{project.name}</div>
                </div>
                <div className="p-4">
                  <h3 className="text-lg font-semibold mb-2">{project.name}</h3>
                  <p className="text-gray-400 text-sm mb-3">
                    Mode: <span className="text-gray-200 font-medium">{getModeLabel(project.mode)}</span>
                  </p>
                  <p className="text-gray-500 text-xs mb-4">
                    Created {new Date(project.created_at).toLocaleDateString()}
                  </p>
                  <div className="flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        if (confirm('Delete this project? This cannot be undone.')) {
                          deleteProjectMutation.mutate(project.id);
                        }
                      }}
                      className="flex-1 px-3 py-2 bg-red-900 hover:bg-red-800 rounded text-sm font-medium transition-colors flex items-center justify-center gap-2"
                      disabled={deleteProjectMutation.isPending}
                    >
                      <Trash2 size={14} />
                      Delete
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}

        {showNewProjectModal && (
          <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
            <div className="bg-gray-900 border border-gray-800 rounded-lg w-full max-w-md p-6">
              <h2 className="text-2xl font-bold mb-6">Create New Project</h2>

              <div className="mb-4">
                <label className="block text-sm font-medium mb-2">Project Name</label>
                <input
                  type="text"
                  value={newProjectName}
                  onChange={(e) => setNewProjectName(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleCreateProject()}
                  placeholder="My awesome storyboard"
                  className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500"
                  autoFocus
                />
              </div>

              <div className="mb-6">
                <label className="block text-sm font-medium mb-3">Project Mode</label>
                <div className="space-y-2">
                  {(['music_video', 'narration_images', 'narration_video'] as ProjectMode[]).map((mode) => (
                    <label key={mode} className="flex items-center gap-3 p-3 border border-gray-700 rounded cursor-pointer hover:bg-gray-800 transition-colors">
                      <input
                        type="radio"
                        name="mode"
                        value={mode}
                        checked={newProjectMode === mode}
                        onChange={() => setNewProjectMode(mode)}
                        className="w-4 h-4"
                      />
                      <span>{getModeLabel(mode)}</span>
                    </label>
                  ))}
                </div>
              </div>

              <div className="flex gap-4">
                <button
                  onClick={() => setShowNewProjectModal(false)}
                  className="flex-1 px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded font-medium transition-colors"
                  disabled={isCreating}
                >
                  Cancel
                </button>
                <button
                  onClick={handleCreateProject}
                  className="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded font-medium transition-colors disabled:opacity-50"
                  disabled={!newProjectName.trim() || isCreating}
                >
                  {isCreating ? 'Creating...' : 'Create'}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
