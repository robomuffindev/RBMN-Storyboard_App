import { useState, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  getSettings,
  updateSettings,
  testComfyUI,
  testWhisper,
  testLLM,
  testOllamaSingle,
  getWorkflowConfigs,
  deleteWorkflow,
  introspectWorkflow,
  exportSettings,
  importSettings,
  getBuiltinPrompt,
  testRunPod,
  getRunPodStatus,
  startRunPodPod,
  stopRunPodPod,
  purgeJobs,
  browseDirectory,
  changeProjectDir,
  getGpuStatus,
  redetectGpu,
  refreshOllamaModels,
} from '@/api/client';
import { ChevronLeft, Check, X, Loader, Upload, Trash2, Download, FolderInput, FolderOpen, BookOpen, Cloud, Play, Square, Plus, RefreshCw, AlertTriangle, Cpu, Monitor, Zap } from 'lucide-react';
import type { AppSettings, SystemPromptOverrideEntry, RunPodPodConfig, RunPodPodStatus, GpuStatus } from '@/types/index';

export default function SettingsPage() {
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [settings, setSettings] = useState<AppSettings>({
    id: 0,
    comfyui_urls: [],
    whisper_mode: 'local',
    whisper_model: 'base',
    whisper_language: 'English',
    openai_api_key: '',
    openai_model: '',
    anthropic_api_key: '',
    anthropic_model: '',
    gemini_api_key: '',
    gemini_model: '',
    image_model_type: 'flux2_klein_dev_9b',
    video_model_type: 'ltx_2.3',
    ltx_model_gguf: 'ltx-2.3-22b-dev-Q8_0.gguf',
    default_llm_provider: '',
    image_prompt_guidance: {},
    video_prompt_guidance: {},
    video_fps: 24,
    video_max_duration: 15,
    video_min_duration: 5,
    video_tail: 0,
    color_correction_enabled: false,
    restrict_explicit_content: false,
    network_access: false,
    app_port: 8899,
    global_negative_prompt: '',
    director_guide_strength: 0.5,
    director_audio_guidance: 0.001,
    director_stitch: false,
    director_auto_image_desc: true,
    global_video_negative_prompt: '',
    export_transition_type: 'crossfade',
    export_transition_duration: 0.5,
    export_color_match_clips: false,
    export_lfff_trim_enabled: true,
    single_image_generator: 'z_image_turbo',
    use_distilled_lora: true,
    distilled_lora_name: 'ltx-2.3-22b-distilled-lora-384-1.1.safetensors',
    runpod_enabled: false,
    runpod_api_key: '',
    runpod_idle_timeout: 30,
    runpod_pods: [],
    gpu_acceleration_enabled: true,
    ollama_base_url: '',
    ollama_urls: [],
    ollama_model: '',
    ollama_available_models: [],
  });
  const [customImageModel, setCustomImageModel] = useState('');
  // GPU status state
  const [gpuStatus, setGpuStatus] = useState<GpuStatus | null>(null);
  const [gpuLoading, setGpuLoading] = useState(false);
  const [gpuRedetecting, setGpuRedetecting] = useState(false);
  const [customSingleImageModel, setCustomSingleImageModel] = useState('');
  const [customVideoModel, setCustomVideoModel] = useState('');
  const [promptGuidanceModal, setPromptGuidanceModal] = useState<{ type: 'image' | 'video'; modelName: string } | null>(null);
  const [promptGuidanceText, setPromptGuidanceText] = useState('');
  const guidanceFileInputRef = useRef<HTMLInputElement>(null);

  const [newComfyUIUrl, setNewComfyUIUrl] = useState('');
  const [testResults, setTestResults] = useState<Record<string, boolean | null>>({});
  const [introspectingWorkflow, setIntrospectingWorkflow] = useState(false);
  const importFileRef = useRef<HTMLInputElement>(null);
  const [importExportStatus, setImportExportStatus] = useState<{ type: 'success' | 'error'; message: string } | null>(null);
  const [builtinImagePrompt, setBuiltinImagePrompt] = useState('');
  const [builtinVideoPrompt, setBuiltinVideoPrompt] = useState('');
  // RunPod state
  const [runpodPodStatuses, setRunpodPodStatuses] = useState<RunPodPodStatus[]>([]);
  const [runpodTestResult, setRunpodTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const [runpodTesting, setRunpodTesting] = useState(false);
  const [runpodRefreshing, setRunpodRefreshing] = useState(false);
  // Project directory state
  const [projectDirInput, setProjectDirInput] = useState('');
  const [projectDirChanged, setProjectDirChanged] = useState(false);
  const [showMoveDialog, setShowMoveDialog] = useState(false);
  const [projectDirStatus, setProjectDirStatus] = useState<{ type: 'success' | 'error' | 'loading'; message: string } | null>(null);
  // Ollama state
  const [newOllamaUrl, setNewOllamaUrl] = useState('');
  const [ollamaRefreshing, setOllamaRefreshing] = useState(false);
  const [ollamaRefreshMsg, setOllamaRefreshMsg] = useState<string | null>(null);
  const [ollamaServerTests, setOllamaServerTests] = useState<Record<string, 'testing' | 'ok' | 'fail'>>({});

  const { data: savedSettings } = useQuery({
    queryKey: ['settings'],
    queryFn: async () => {
      const response = await getSettings();
      return response.data;
    },
  });

  const { data: workflows = [], refetch: refetchWorkflows } = useQuery({
    queryKey: ['workflows'],
    queryFn: async () => {
      const response = await getWorkflowConfigs();
      return response.data;
    },
  });

  // Known preset keys for image and video models
  const IMAGE_MODEL_PRESETS = ['flux2_klein_dev_9b', 'flux1_dev', 'z_image', 'qwen_edit'];
  const SINGLE_IMAGE_PRESETS = ['z_image_turbo', 'flux2_klein_dev_9b'];
  const VIDEO_MODEL_PRESETS = ['ltx_2.3', 'wan_2.2'];

  useEffect(() => {
    if (savedSettings) {
      setSettings({
        ...savedSettings,
        default_llm_provider: savedSettings.default_llm_provider || '',
        image_prompt_guidance: savedSettings.image_prompt_guidance || {},
        video_prompt_guidance: savedSettings.video_prompt_guidance || {},
        video_fps: savedSettings.video_fps || 24,
        video_max_duration: savedSettings.video_max_duration || 15,
        video_min_duration: savedSettings.video_min_duration ?? 5,
        video_tail: savedSettings.video_tail || 0,
        color_correction_enabled: savedSettings.color_correction_enabled === true,
        restrict_explicit_content: savedSettings.restrict_explicit_content === true,
        network_access: savedSettings.network_access === true,
        app_port: savedSettings.app_port || 8899,
        global_negative_prompt: savedSettings.global_negative_prompt || '',
        director_guide_strength: savedSettings.director_guide_strength ?? 0.5,
        director_audio_guidance: savedSettings.director_audio_guidance ?? 0.001,
        director_stitch: savedSettings.director_stitch ?? false,
        director_auto_image_desc: savedSettings.director_auto_image_desc ?? true,
        global_video_negative_prompt: savedSettings.global_video_negative_prompt || '',
        export_transition_type: savedSettings.export_transition_type || 'crossfade',
        export_transition_duration: savedSettings.export_transition_duration ?? 0.5,
        export_color_match_clips: savedSettings.export_color_match_clips === true,
        export_lfff_trim_enabled: savedSettings.export_lfff_trim_enabled ?? true,
        single_image_generator: savedSettings.single_image_generator || 'z_image_turbo',
        use_distilled_lora: savedSettings.use_distilled_lora ?? true,
        distilled_lora_name: savedSettings.distilled_lora_name || 'ltx-2.3-22b-distilled-lora-384-1.1.safetensors',
        runpod_enabled: savedSettings.runpod_enabled || false,
        runpod_api_key: savedSettings.runpod_api_key || '',
        runpod_idle_timeout: savedSettings.runpod_idle_timeout || 30,
        runpod_pods: savedSettings.runpod_pods || [],
        gpu_acceleration_enabled: savedSettings.gpu_acceleration_enabled ?? true,
        ollama_base_url: savedSettings.ollama_base_url || '',
        ollama_urls: savedSettings.ollama_urls || [],
        ollama_model: savedSettings.ollama_model || '',
        ollama_available_models: savedSettings.ollama_available_models || [],
      });
      // Initialize project directory input
      setProjectDirInput(savedSettings.project_dir || '');
      setProjectDirChanged(false);
      // If saved value doesn't match a preset, mark it as custom
      const imgType = savedSettings.image_model_type || 'flux2_klein_dev_9b';
      if (!IMAGE_MODEL_PRESETS.includes(imgType)) {
        setCustomImageModel(imgType);
      }
      const singleImgType = savedSettings.single_image_generator || 'z_image_turbo';
      if (!SINGLE_IMAGE_PRESETS.includes(singleImgType)) {
        setCustomSingleImageModel(singleImgType);
      }
      const vidType = savedSettings.video_model_type || 'ltx_2.3';
      if (!VIDEO_MODEL_PRESETS.includes(vidType)) {
        setCustomVideoModel(vidType);
      }
    }
  }, [savedSettings]);

  // Fetch built-in prompts when model types change
  useEffect(() => {
    const imgModel = settings.image_model_type || 'flux2_klein_dev_9b';
    getBuiltinPrompt(imgModel, 'image')
      .then((res) => setBuiltinImagePrompt(res.data.prompt))
      .catch(() => setBuiltinImagePrompt(''));
  }, [settings.image_model_type]);

  useEffect(() => {
    const vidModel = settings.video_model_type || 'ltx_2.3';
    getBuiltinPrompt(vidModel, 'video')
      .then((res) => setBuiltinVideoPrompt(res.data.prompt))
      .catch(() => setBuiltinVideoPrompt(''));
  }, [settings.video_model_type]);

  // Fetch GPU status on mount
  useEffect(() => {
    setGpuLoading(true);
    getGpuStatus()
      .then((res) => setGpuStatus(res.data))
      .catch(() => setGpuStatus(null))
      .finally(() => setGpuLoading(false));
  }, []);

  const handleRedetectGpu = async () => {
    setGpuRedetecting(true);
    try {
      const res = await redetectGpu();
      setGpuStatus(res.data);
    } catch {
      // keep existing status
    } finally {
      setGpuRedetecting(false);
    }
  };

  // Helpers for system prompt override state
  const getImageOverride = (): SystemPromptOverrideEntry => {
    const model = settings.image_model_type || 'flux2_klein_dev_9b';
    return settings.image_system_prompt_overrides?.[model] || { text: '', enabled: false };
  };

  const getVideoOverride = (): SystemPromptOverrideEntry => {
    const model = settings.video_model_type || 'ltx_2.3';
    return settings.video_system_prompt_overrides?.[model] || { text: '', enabled: false };
  };

  const setImageOverride = (entry: SystemPromptOverrideEntry) => {
    const model = settings.image_model_type || 'flux2_klein_dev_9b';
    setSettings((prev) => ({
      ...prev,
      image_system_prompt_overrides: {
        ...(prev.image_system_prompt_overrides || {}),
        [model]: entry,
      },
    }));
  };

  const setVideoOverride = (entry: SystemPromptOverrideEntry) => {
    const model = settings.video_model_type || 'ltx_2.3';
    setSettings((prev) => ({
      ...prev,
      video_system_prompt_overrides: {
        ...(prev.video_system_prompt_overrides || {}),
        [model]: entry,
      },
    }));
  };

  // Prompt Guidance helpers
  const getImageGuidance = (): string => {
    const model = settings.image_model_type || 'flux2_klein_dev_9b';
    return settings.image_prompt_guidance?.[model] || '';
  };

  const getVideoGuidance = (): string => {
    const model = settings.video_model_type || 'ltx_2.3';
    return settings.video_prompt_guidance?.[model] || '';
  };

  const setImageGuidance = (text: string) => {
    const model = settings.image_model_type || 'flux2_klein_dev_9b';
    setSettings((prev) => ({
      ...prev,
      image_prompt_guidance: {
        ...(prev.image_prompt_guidance || {}),
        [model]: text,
      },
    }));
  };

  const setVideoGuidance = (text: string) => {
    const model = settings.video_model_type || 'ltx_2.3';
    setSettings((prev) => ({
      ...prev,
      video_prompt_guidance: {
        ...(prev.video_prompt_guidance || {}),
        [model]: text,
      },
    }));
  };

  const openPromptGuidanceModal = (type: 'image' | 'video') => {
    const modelName = type === 'image'
      ? (settings.image_model_type || 'flux2_klein_dev_9b')
      : (settings.video_model_type || 'ltx_2.3');
    const text = type === 'image' ? getImageGuidance() : getVideoGuidance();
    setPromptGuidanceText(text);
    setPromptGuidanceModal({ type, modelName });
  };

  const closePromptGuidanceModal = () => {
    setPromptGuidanceModal(null);
    setPromptGuidanceText('');
  };

  const savePromptGuidance = () => {
    if (promptGuidanceModal?.type === 'image') {
      setImageGuidance(promptGuidanceText);
    } else if (promptGuidanceModal?.type === 'video') {
      setVideoGuidance(promptGuidanceText);
    }
    closePromptGuidanceModal();
  };

  const handleGuidanceFileSelect = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    if (!file) return;

    try {
      const text = await file.text();
      setPromptGuidanceText(text);
    } catch (error) {
      console.error('Failed to read file:', error);
    }
  };

  const updateSettingsMutation = useMutation({
    mutationFn: async () => {
      const response = await updateSettings(settings);
      return response.data;
    },
    onSuccess: (data) => {
      setSettings(data);
    },
  });

  const testComfyMutation = useMutation({
    mutationFn: async (url: string) => {
      const response = await testComfyUI(url);
      return response.data.success;
    },
    onSuccess: (success, url) => {
      setTestResults((prev) => ({ ...prev, [url]: success }));
    },
  });

  const testWhisperMutation = useMutation({
    mutationFn: async () => {
      const response = await testWhisper();
      return response.data.success;
    },
    onSuccess: (success) => {
      setTestResults((prev) => ({ ...prev, whisper: success }));
    },
  });

  const testLLMMutation = useMutation({
    mutationFn: async (provider: string) => {
      if (provider === 'ollama') {
        // Ollama doesn't use api_key — just needs URLs configured
        if ((settings.ollama_urls || []).length === 0) return false;
        const response = await testLLM({ provider: 'ollama', api_key: '', model: settings.ollama_model || '' });
        return response.data.success;
      }
      const apiKey = settings[`${provider}_api_key` as keyof AppSettings] as string | undefined;
      const model = settings[`${provider}_model` as keyof AppSettings] as string | undefined;
      if (!apiKey || !model) return false;
      const response = await testLLM({ provider, api_key: apiKey, model });
      return response.data.success;
    },
    onSuccess: (success, provider) => {
      setTestResults((prev) => ({ ...prev, [provider]: success }));
    },
  });

  const uploadWorkflowMutation = useMutation({
    mutationFn: async (formData: FormData) => {
      setIntrospectingWorkflow(true);
      try {
        const introspectResponse = await introspectWorkflow(formData);
        // Show detected fields for review before uploading
        return introspectResponse.data;
      } finally {
        setIntrospectingWorkflow(false);
      }
    },
  });

  const deleteWorkflowMutation = useMutation({
    mutationFn: async (workflowId: string) => {
      await deleteWorkflow(workflowId);
      refetchWorkflows();
    },
  });

  const handleAddComfyUI = () => {
    if (newComfyUIUrl.trim()) {
      setSettings((prev) => ({
        ...prev,
        comfyui_urls: [...(prev.comfyui_urls || []), newComfyUIUrl],
      }));
      setNewComfyUIUrl('');
    }
  };

  const handleRemoveComfyUI = (index: number) => {
    setSettings((prev) => ({
      ...prev,
      comfyui_urls: (prev.comfyui_urls || []).filter((_, i) => i !== index),
    }));
  };

  const handleWorkflowFileSelect = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('workflow_json', file);

    try {
      await uploadWorkflowMutation.mutateAsync(formData);
      refetchWorkflows();
      if (fileInputRef.current) fileInputRef.current.value = '';
    } catch (error) {
      console.error('Failed to upload workflow:', error);
    }
  };

  const customWorkflows = workflows.filter(w => !w.is_default);
  const defaultWorkflows = workflows.filter(w => w.is_default);

  const handleExportSettings = async () => {
    setImportExportStatus(null);
    try {
      const response = await exportSettings();
      const data = response.data;
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const dateStamp = new Date().toISOString().slice(0, 19).replace(/[T:]/g, '_');
      const filename = `rbmn_settings_${dateStamp}.rbmn-settings.json`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      const exportedDate = data.exported_at
        ? new Date(data.exported_at).toLocaleString()
        : new Date().toLocaleString();
      setImportExportStatus({ type: 'success', message: `Settings exported (${exportedDate})` });
    } catch (error) {
      console.error('Export failed:', error);
      setImportExportStatus({ type: 'error', message: 'Failed to export settings' });
    }
  };

  const handleImportSettings = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    if (!file) return;
    setImportExportStatus(null);
    try {
      const response = await importSettings(file);
      setSettings(response.data);
      setImportExportStatus({ type: 'success', message: 'Settings imported successfully' });
    } catch (error: any) {
      const detail = error?.response?.data?.detail || 'Failed to import settings';
      console.error('Import failed:', error);
      setImportExportStatus({ type: 'error', message: detail });
    }
    if (importFileRef.current) importFileRef.current.value = '';
  };

  // ── RunPod Handlers ──────────────────────────────────────────────
  const handleTestRunPod = async () => {
    const key = settings.runpod_api_key;
    if (!key || key.startsWith('***')) {
      setRunpodTestResult({ success: false, message: 'Enter an API key first' });
      return;
    }
    setRunpodTesting(true);
    setRunpodTestResult(null);
    try {
      const res = await testRunPod(key);
      setRunpodTestResult(res.data);
    } catch {
      setRunpodTestResult({ success: false, message: 'Failed to connect' });
    } finally {
      setRunpodTesting(false);
    }
  };

  const handleRefreshRunPodStatuses = async () => {
    setRunpodRefreshing(true);
    try {
      const res = await getRunPodStatus();
      setRunpodPodStatuses(res.data.pods || []);
    } catch {
      // ignore
    } finally {
      setRunpodRefreshing(false);
    }
  };

  const handleStartPod = async (podId: string) => {
    try {
      const res = await startRunPodPod(podId);
      if (res.data.error) {
        alert(`Failed to start pod: ${res.data.error}`);
      }
      // Refresh after a brief delay for status to update
      setTimeout(handleRefreshRunPodStatuses, 2000);
    } catch (err: any) {
      const msg = err?.response?.data?.detail || err?.message || 'Unknown error';
      alert(`Failed to start pod: ${msg}`);
    }
  };

  const handleStopPod = async (podId: string) => {
    try {
      await stopRunPodPod(podId);
      setTimeout(handleRefreshRunPodStatuses, 2000);
    } catch (err) {
      console.error('Failed to stop pod:', err);
    }
  };

  const addRunPodPod = () => {
    const newPod: RunPodPodConfig = {
      pod_id: '',
      label: `Pod ${(settings.runpod_pods?.length || 0) + 1}`,
      service_type: 'image',
      gpu_type_id: '',
      gpu_count: 1,
      template_id: '',
      api_port: 8188,
      enabled: true,
    };
    setSettings(prev => ({
      ...prev,
      runpod_pods: [...(prev.runpod_pods || []), newPod],
    }));
  };

  const updateRunPodPod = (index: number, field: keyof RunPodPodConfig, value: any) => {
    setSettings(prev => {
      const pods = [...(prev.runpod_pods || [])];
      pods[index] = { ...pods[index], [field]: value };
      // Auto-set port based on service type
      if (field === 'service_type') {
        if (value === 'whisper') pods[index].api_port = 7860;
        else if (value === 'llm') pods[index].api_port = 8000;
        else pods[index].api_port = 8188;
      }
      return { ...prev, runpod_pods: pods };
    });
  };

  const removeRunPodPod = (index: number) => {
    setSettings(prev => ({
      ...prev,
      runpod_pods: (prev.runpod_pods || []).filter((_, i) => i !== index),
    }));
  };

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      {/* Header */}
      <div className="bg-gray-900 border-b border-gray-800 px-6 py-4">
        <button
          onClick={() => navigate('/')}
          className="flex items-center gap-2 text-sm font-medium text-gray-400 hover:text-gray-100 mb-4 transition-colors"
        >
          <ChevronLeft size={20} />
          Back
        </button>
        <h1 className="text-3xl font-bold">Settings</h1>
      </div>

      {/* Floating Save/Cancel Bar */}
      <div className="sticky top-0 z-30 bg-gray-900/95 backdrop-blur-sm border-b border-gray-700 shadow-lg">
        <div className="max-w-3xl mx-auto px-8 py-3 flex justify-end gap-3">
          <button
            onClick={() => navigate('/')}
            className="px-5 py-2 bg-gray-700 hover:bg-gray-600 rounded font-medium text-sm transition-colors flex items-center gap-2"
          >
            <X size={16} />
            Cancel
          </button>
          <button
            onClick={() => updateSettingsMutation.mutate()}
            disabled={updateSettingsMutation.isPending}
            className="px-5 py-2 bg-blue-600 hover:bg-blue-700 rounded font-medium text-sm transition-colors disabled:opacity-50 flex items-center gap-2"
          >
            {updateSettingsMutation.isPending ? (
              <Loader size={16} className="animate-spin" />
            ) : (
              <Check size={16} />
            )}
            {updateSettingsMutation.isPending ? 'Saving...' : 'Save Settings'}
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="max-w-3xl mx-auto p-8 space-y-8">
        {/* Project Directory */}
        <section className="bg-gray-900 border border-gray-800 rounded-lg p-6">
          <h2 className="text-xl font-semibold mb-2">Project Directory</h2>
          <p className="text-sm text-gray-400 mb-4">
            Set the folder where all project data (database, assets, cache, generated files) is stored.
            Changing this requires a restart to take full effect.
          </p>

          <div className="flex gap-2 mb-3">
            <input
              type="text"
              value={projectDirInput}
              onChange={(e) => {
                setProjectDirInput(e.target.value);
                setProjectDirChanged(e.target.value !== (settings.project_dir || ''));
                setProjectDirStatus(null);
              }}
              placeholder="e.g. C:\Users\You\RBMN-Projects"
              className="flex-1 px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 text-sm font-mono"
            />
            <button
              onClick={async () => {
                try {
                  const res = await browseDirectory();
                  if (res.data.success && res.data.path) {
                    setProjectDirInput(res.data.path);
                    setProjectDirChanged(res.data.path !== (settings.project_dir || ''));
                    setProjectDirStatus(null);
                  }
                } catch (e) {
                  console.error('Browse directory failed:', e);
                  setProjectDirStatus({ type: 'error', message: 'Failed to open folder picker' });
                }
              }}
              className="flex items-center gap-2 px-3 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm font-medium transition-colors"
              title="Browse for folder"
            >
              <FolderOpen size={16} />
              Browse
            </button>
          </div>

          {/* Current path display */}
          {settings.project_dir && (
            <p className="text-xs text-gray-500 mb-3">
              Current: <span className="font-mono text-gray-400">{settings.project_dir}</span>
            </p>
          )}

          {/* Apply button — only enabled when path has changed */}
          {projectDirChanged && projectDirInput.trim() && (
            <button
              onClick={() => setShowMoveDialog(true)}
              disabled={projectDirStatus?.type === 'loading'}
              className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded text-sm font-medium transition-colors disabled:opacity-50"
            >
              {projectDirStatus?.type === 'loading' ? (
                <Loader size={16} className="animate-spin" />
              ) : (
                <FolderInput size={16} />
              )}
              Apply New Directory
            </button>
          )}

          {/* Status message */}
          {projectDirStatus && projectDirStatus.type !== 'loading' && (
            <div
              className={`mt-3 px-3 py-2 rounded text-sm ${
                projectDirStatus.type === 'success'
                  ? 'bg-green-900/40 border border-green-700 text-green-300'
                  : 'bg-red-900/40 border border-red-700 text-red-300'
              }`}
            >
              {projectDirStatus.message}
            </div>
          )}
        </section>

        {/* ComfyUI Servers */}
        <section className="bg-gray-900 border border-gray-800 rounded-lg p-6">
          <h2 className="text-xl font-semibold mb-4">ComfyUI Servers</h2>
          <div className="space-y-3 mb-4">
            {(settings.comfyui_urls || []).map((url, index) => {
              const caps = (settings as any).comfyui_server_caps?.[url] || { image: true, video: true };
              const setCap = (field: string, val: boolean) => {
                const newCaps = { ...(settings as any).comfyui_server_caps || {} };
                newCaps[url] = { ...caps, [field]: val };
                setSettings((prev) => ({ ...prev, comfyui_server_caps: newCaps } as any));
              };
              return (
              <div key={index} className="p-3 bg-gray-800 rounded border border-gray-700">
                <div className="flex items-center gap-3">
                  <span className="flex-1 text-sm font-mono text-gray-300 truncate">{url}</span>
                  <button
                    onClick={() => testComfyMutation.mutate(url)}
                    className="px-3 py-1 text-xs font-medium transition-colors"
                    disabled={testComfyMutation.isPending}
                    title="Test connection"
                  >
                    {testComfyMutation.isPending && testComfyMutation.variables === url ? (
                      <Loader size={16} className="animate-spin text-blue-500" />
                    ) : testResults[url] === true ? (
                      <Check size={16} className="text-green-500" />
                    ) : testResults[url] === false ? (
                      <X size={16} className="text-red-500" />
                    ) : (
                      <span className="text-gray-400 hover:text-gray-200">Test</span>
                    )}
                  </button>
                  <button
                    onClick={() => handleRemoveComfyUI(index)}
                    className="text-gray-400 hover:text-red-500 transition-colors"
                    title="Remove server"
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
                <div className="flex items-center gap-4 mt-2 ml-1">
                  <label className="flex items-center gap-1.5 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={caps.image !== false}
                      onChange={(e) => setCap('image', e.target.checked)}
                      className="w-3.5 h-3.5 accent-emerald-500"
                    />
                    <span className="text-xs text-gray-400">Images (Klein)</span>
                  </label>
                  <label className="flex items-center gap-1.5 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={caps.video !== false}
                      onChange={(e) => setCap('video', e.target.checked)}
                      className="w-3.5 h-3.5 accent-purple-500"
                    />
                    <span className="text-xs text-gray-400">Video (LTX)</span>
                  </label>
                </div>
              </div>
              );
            })}
          </div>

          <div className="flex gap-2">
            <input
              type="url"
              value={newComfyUIUrl}
              onChange={(e) => setNewComfyUIUrl(e.target.value)}
              placeholder="http://localhost:8188"
              className="flex-1 px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 text-sm"
            />
            <button
              onClick={handleAddComfyUI}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded text-sm font-medium transition-colors"
            >
              Add Server
            </button>
          </div>
        </section>

        {/* Generation Models */}
        <section className="bg-gray-900 border border-gray-800 rounded-lg p-6">
          <h2 className="text-xl font-semibold mb-4">Generation Models</h2>
          <p className="text-sm text-gray-400 mb-5">
            Select the image and video models running on your ComfyUI instances. This information is passed to the LLM when enhancing prompts so it can optimize for each model's strengths and quirks.
          </p>

          <div className="space-y-5">
            {/* Image Model */}
            <div>
              <div className="flex items-end gap-2 mb-2">
                <div className="flex-1">
                  <label className="block text-sm font-medium mb-2">Edit Model (Reference-Based)</label>
                  <select
                    value={IMAGE_MODEL_PRESETS.includes(settings.image_model_type || '') ? settings.image_model_type : 'custom'}
                    onChange={(e) => {
                      const val = e.target.value;
                      if (val === 'custom') {
                        setSettings((prev) => ({ ...prev, image_model_type: customImageModel || 'custom' }));
                      } else {
                        setSettings((prev) => ({ ...prev, image_model_type: val }));
                        setCustomImageModel('');
                      }
                    }}
                    className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 focus:outline-none focus:border-blue-500 text-sm"
                  >
                    <option value="flux2_klein_dev_9b">FLUX.2 Klein Dev 9B</option>
                    <option value="flux1_dev">FLUX 1 Dev</option>
                    <option value="z_image">Z-Image</option>
                    <option value="qwen_edit">Qwen Edit</option>
                    <option value="custom">Custom...</option>
                  </select>
                </div>
                <button
                  onClick={() => openPromptGuidanceModal('image')}
                  className="px-3 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm transition-colors flex items-center gap-2"
                  title="Add or edit prompt guidance rules for this model"
                >
                  <BookOpen size={16} />
                  <span className="text-xs font-medium">Rules</span>
                </button>
              </div>
              {!IMAGE_MODEL_PRESETS.includes(settings.image_model_type || '') && (
                <input
                  type="text"
                  value={customImageModel || (settings.image_model_type === 'custom' ? '' : settings.image_model_type)}
                  onChange={(e) => {
                    setCustomImageModel(e.target.value);
                    setSettings((prev) => ({ ...prev, image_model_type: e.target.value || 'custom' }));
                  }}
                  placeholder="Enter custom image model name..."
                  className="w-full mt-2 px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 text-sm"
                />
              )}
            </div>

            {/* Image System Prompt Override */}
            <div className="mt-4 p-4 bg-gray-800 rounded border border-gray-700">
              <label className="flex items-center gap-2 cursor-pointer mb-2">
                <input
                  type="checkbox"
                  checked={getImageOverride().enabled}
                  onChange={(e) => setImageOverride({ ...getImageOverride(), enabled: e.target.checked })}
                  className="w-4 h-4 rounded"
                />
                <span className="text-sm font-medium">Image System Prompt Override</span>
              </label>
              <p className="text-xs text-gray-400 mb-2">
                Override the built-in system prompt used when enhancing image generation prompts for the selected model.
              </p>
              <textarea
                value={getImageOverride().text}
                onChange={(e) => setImageOverride({ ...getImageOverride(), text: e.target.value })}
                placeholder={builtinImagePrompt || 'Built-in system prompt will appear here...'}
                disabled={!getImageOverride().enabled}
                rows={6}
                className={`w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 text-xs font-mono resize-y ${
                  !getImageOverride().enabled ? 'opacity-50 cursor-not-allowed' : ''
                }`}
              />
              {getImageOverride().enabled && !getImageOverride().text.trim() && (
                <p className="text-xs text-amber-400 mt-1">When enabled but empty, the built-in prompt will be used.</p>
              )}
            </div>

            {/* Single Image Generator */}
            <div>
              <label className="block text-sm font-medium mb-2">Single Image Generator</label>
              <select
                value={SINGLE_IMAGE_PRESETS.includes(settings.single_image_generator || '') ? settings.single_image_generator : 'custom'}
                onChange={(e) => {
                  const val = e.target.value;
                  if (val === 'custom') {
                    setSettings((prev) => ({ ...prev, single_image_generator: customSingleImageModel || 'custom' }));
                  } else {
                    setSettings((prev) => ({ ...prev, single_image_generator: val }));
                    setCustomSingleImageModel('');
                  }
                }}
                className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 focus:outline-none focus:border-blue-500 text-sm"
              >
                <option value="z_image_turbo">Z-Image Turbo</option>
                <option value="flux2_klein_dev_9b">FLUX.2 Klein T2I (fallback)</option>
                <option value="custom">Custom...</option>
              </select>
              {!SINGLE_IMAGE_PRESETS.includes(settings.single_image_generator || '') && (
                <input
                  type="text"
                  value={customSingleImageModel || (settings.single_image_generator === 'custom' ? '' : settings.single_image_generator)}
                  onChange={(e) => {
                    setCustomSingleImageModel(e.target.value);
                    setSettings((prev) => ({ ...prev, single_image_generator: e.target.value || 'custom' }));
                  }}
                  placeholder="Enter custom single image model name..."
                  className="w-full mt-2 px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 text-sm"
                />
              )}
              <p className="text-xs text-gray-500 mt-1">
                Model used for text-to-image generation when no reference images are needed (e.g., first-pass scene images, character gen without refs). Z-Image Turbo generates images in 8 steps.
              </p>
            </div>

            {/* Video Model + FPS Row */}
            <div className="grid grid-cols-3 gap-4 items-start">
              <div className="col-span-2">
                <div className="flex items-end gap-2 mb-2">
                  <div className="flex-1">
                    <label className="block text-sm font-medium mb-2">Video Model</label>
                    <select
                      value={VIDEO_MODEL_PRESETS.includes(settings.video_model_type || '') ? settings.video_model_type : 'custom'}
                      onChange={(e) => {
                        const val = e.target.value;
                        if (val === 'custom') {
                          setSettings((prev) => ({ ...prev, video_model_type: customVideoModel || 'custom' }));
                        } else {
                          setSettings((prev) => ({ ...prev, video_model_type: val }));
                          setCustomVideoModel('');
                        }
                      }}
                      className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 focus:outline-none focus:border-blue-500 text-sm"
                    >
                      <option value="ltx_2.3">LTX 2.3</option>
                      <option value="wan_2.2">Wan 2.2</option>
                      <option value="custom">Custom...</option>
                    </select>
                  </div>
                  <button
                    onClick={() => openPromptGuidanceModal('video')}
                    className="px-3 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm transition-colors flex items-center gap-2"
                    title="Add or edit prompt guidance rules for this model"
                  >
                    <BookOpen size={16} />
                    <span className="text-xs font-medium">Rules</span>
                  </button>
                </div>
                {!VIDEO_MODEL_PRESETS.includes(settings.video_model_type || '') && (
                  <input
                    type="text"
                    value={customVideoModel || (settings.video_model_type === 'custom' ? '' : settings.video_model_type)}
                    onChange={(e) => {
                      setCustomVideoModel(e.target.value);
                      setSettings((prev) => ({ ...prev, video_model_type: e.target.value || 'custom' }));
                    }}
                    placeholder="Enter custom video model name..."
                    className="w-full mt-2 px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 text-sm"
                  />
                )}
              </div>

              {/* FPS Field */}
              <div>
                <label className="block text-sm font-medium mb-2">FPS</label>
                <input
                  type="number"
                  value={settings.video_fps || 24}
                  onChange={(e) => setSettings((prev) => ({ ...prev, video_fps: parseInt(e.target.value) || 24 }))}
                  min="12"
                  max="60"
                  step="1"
                  className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 focus:outline-none focus:border-blue-500 text-sm"
                />
              </div>
            </div>

            {/* Use Distilled LoRA */}
            <div className="p-4 bg-gray-800 rounded border border-gray-700">
              <label className="flex items-center gap-2 cursor-pointer mb-2">
                <input
                  type="checkbox"
                  checked={settings.use_distilled_lora ?? true}
                  onChange={(e) => setSettings((prev) => ({ ...prev, use_distilled_lora: e.target.checked }))}
                  className="w-4 h-4 rounded"
                />
                <span className="text-sm font-medium">Use LTX 2.3 Distilled LoRA (8-step generation)</span>
              </label>
              <p className="text-xs text-gray-400 mb-2">
                When enabled, video generation uses 8 steps instead of 20+.
              </p>
              {(settings.use_distilled_lora ?? true) && (
                <div className="mt-3">
                  <label className="block text-sm font-medium mb-2">Distilled LoRA Version</label>
                  <select
                    value={settings.distilled_lora_name || 'ltx-2.3-22b-distilled-lora-384-1.1.safetensors'}
                    onChange={(e) => setSettings((prev) => ({ ...prev, distilled_lora_name: e.target.value }))}
                    className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-gray-100 focus:outline-none focus:border-blue-500 text-sm"
                  >
                    <option value="ltx-2.3-22b-distilled-lora-384-1.1.safetensors">v1.1 (Recommended)</option>
                    <option value="ltx-2.3-22b-distilled-lora-384.safetensors">v1.0</option>
                  </select>
                  <p className="text-xs text-gray-400 mt-1">v1.1 has improved aesthetics and audio quality.</p>
                </div>
              )}
            </div>

            {/* LTX Model GGUF Selector */}
            <div>
              <label className="block text-sm font-medium mb-2">LTX Model (GGUF)</label>
              <select
                value={settings.ltx_model_gguf || 'ltx-2.3-22b-dev-Q8_0.gguf'}
                onChange={(e) => setSettings((prev) => ({ ...prev, ltx_model_gguf: e.target.value }))}
                className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 focus:outline-none focus:border-blue-500 text-sm"
              >
                <option value="ltx-2.3-22b-dev-Q8_0.gguf">Q8_0 (22B, highest quality)</option>
                <option value="ltx-2.3-22b-dev-Q6_K.gguf">Q6_K (22B, balanced)</option>
                <option value="ltx-2.3-22b-dev-Q5_K_S.gguf">Q5_K_S (22B, fastest / low VRAM)</option>
              </select>
              <p className="text-xs text-gray-500 mt-1">Controls which quantized model file is used in LTX video workflows</p>
            </div>

            {/* Max / Min Duration Fields */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium mb-2">Max Scene Duration (s)</label>
                <input
                  type="number"
                  value={settings.video_max_duration || 15}
                  onChange={(e) => setSettings((prev) => ({ ...prev, video_max_duration: parseInt(e.target.value) || 15 }))}
                  min="3"
                  max="60"
                  step="1"
                  className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 focus:outline-none focus:border-blue-500 text-sm"
                />
                <p className="text-xs text-gray-500 mt-1">Maximum seconds per scene / video generation</p>
              </div>
              <div>
                <label className="block text-sm font-medium mb-2">Min Scene Duration (s)</label>
                <input
                  type="number"
                  value={settings.video_min_duration ?? 5}
                  onChange={(e) => setSettings((prev) => ({ ...prev, video_min_duration: parseInt(e.target.value) || 3 }))}
                  min="2"
                  max="30"
                  step="1"
                  className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 focus:outline-none focus:border-blue-500 text-sm"
                />
                <p className="text-xs text-gray-500 mt-1">Minimum seconds per scene (tiny scenes get merged)</p>
              </div>
            </div>

            {/* Video Tail Field */}
            <div>
              <label className="block text-sm font-medium mb-2">Video Tail (extra seconds)</label>
              <select
                value={settings.video_tail || 0}
                onChange={(e) => setSettings((prev) => ({ ...prev, video_tail: parseInt(e.target.value) || 0 }))}
                className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 focus:outline-none focus:border-blue-500 text-sm"
              >
                <option value="0">None</option>
                <option value="1">1 second</option>
                <option value="2">2 seconds</option>
                <option value="3">3 seconds</option>
                <option value="4">4 seconds</option>
                <option value="5">5 seconds</option>
              </select>
              <p className="text-xs text-gray-500 mt-1">Adds extra duration to video generation, then auto-trims to exact scene length</p>
            </div>

            {/* Video System Prompt Override */}
            <div className="mt-4 p-4 bg-gray-800 rounded border border-gray-700">
              <label className="flex items-center gap-2 cursor-pointer mb-2">
                <input
                  type="checkbox"
                  checked={getVideoOverride().enabled}
                  onChange={(e) => setVideoOverride({ ...getVideoOverride(), enabled: e.target.checked })}
                  className="w-4 h-4 rounded"
                />
                <span className="text-sm font-medium">Video System Prompt Override</span>
              </label>
              <p className="text-xs text-gray-400 mb-2">
                Override the built-in system prompt used when enhancing video generation prompts for the selected model.
              </p>
              <textarea
                value={getVideoOverride().text}
                onChange={(e) => setVideoOverride({ ...getVideoOverride(), text: e.target.value })}
                placeholder={builtinVideoPrompt || 'Built-in system prompt will appear here...'}
                disabled={!getVideoOverride().enabled}
                rows={6}
                className={`w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 text-xs font-mono resize-y ${
                  !getVideoOverride().enabled ? 'opacity-50 cursor-not-allowed' : ''
                }`}
              />
              {getVideoOverride().enabled && !getVideoOverride().text.trim() && (
                <p className="text-xs text-amber-400 mt-1">When enabled but empty, the built-in prompt will be used.</p>
              )}
            </div>
          </div>
        </section>

        {/* Content Safety */}
        <section className="bg-gray-900 border border-gray-800 rounded-lg p-6">
          <h2 className="text-xl font-semibold mb-4">Content Safety</h2>
          <div className="space-y-3">
            <label className="flex items-center gap-3 cursor-pointer">
              <div
                onClick={() => setSettings((prev) => ({ ...prev, restrict_explicit_content: !prev.restrict_explicit_content }))}
                className={`relative w-11 h-6 rounded-full transition-colors ${
                  settings.restrict_explicit_content ? 'bg-blue-600' : 'bg-gray-700'
                }`}
              >
                <div className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform ${
                  settings.restrict_explicit_content ? 'translate-x-5' : ''
                }`} />
              </div>
              <span className="text-sm font-medium">Restrict Explicit Content</span>
            </label>
            <p className="text-xs text-gray-400">
              When enabled, appends SFW (safe for work) tags to all image and video generation prompts to discourage
              nudity and explicit content in outputs. This works across most models as a strong content restriction signal.
            </p>
          </div>

          {/* Global Negative Prompt */}
          <div className="mt-6 pt-6 border-t border-gray-800">
            <label className="block text-sm font-medium mb-2">Global Negative Prompt (Image Generation)</label>
            <textarea
              value={settings.global_negative_prompt || ''}
              onChange={(e) => setSettings((prev) => ({ ...prev, global_negative_prompt: e.target.value }))}
              placeholder="e.g. blurry, low quality, distorted, deformed, ugly, bad anatomy..."
              className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 h-20 text-sm"
            />
            <p className="text-xs text-gray-400 mt-1">
              Applied to all image generation workflows. Appended to the anti-text suffix on every image prompt.
              Per-scene negative prompts override this when set.
            </p>
          </div>
        </section>

        {/* LTXDirector Settings */}
        <section className="bg-gray-900 border border-gray-800 rounded-lg p-6">
          <h2 className="text-xl font-semibold mb-4">LTX Director (Video Generation)</h2>
          <p className="text-xs text-gray-400 mb-4">
            Controls how the LTXDirector node generates video. Director manages frame conditioning,
            audio-video sync, and prompt transitions between scenes.
          </p>

          {/* Guide Strength */}
          <div className="mb-4">
            <label className="block text-sm font-medium mb-1">
              Director Guide Strength: {(settings.director_guide_strength ?? 0.5).toFixed(2)}
            </label>
            <input
              type="range"
              min="0"
              max="1"
              step="0.05"
              value={settings.director_guide_strength ?? 0.5}
              onChange={(e) => setSettings((prev) => ({ ...prev, director_guide_strength: parseFloat(e.target.value) }))}
              className="w-full accent-blue-600"
            />
            <div className="flex justify-between text-xs text-gray-500 mt-1">
              <span>0.0 — Free motion</span>
              <span>0.5 — Official default</span>
              <span>1.0 — Rigid frame lock</span>
            </div>
            <p className="text-xs text-gray-400 mt-1">
              How strongly keyframe images constrain video generation. Lower values allow more natural motion;
              higher values lock the video tightly to reference frames. Official ComfyUI default is 0.5.
            </p>
          </div>

          {/* Audio Guidance */}
          <div className="mb-4">
            <label className="block text-sm font-medium mb-1">
              Audio Guidance: {(settings.director_audio_guidance ?? 0.001).toFixed(3)}
            </label>
            <input
              type="range"
              min="0.001"
              max="1"
              step="0.01"
              value={settings.director_audio_guidance ?? 0.001}
              onChange={(e) => setSettings((prev) => ({ ...prev, director_audio_guidance: parseFloat(e.target.value) }))}
              className="w-full accent-blue-600"
            />
            <div className="flex justify-between text-xs text-gray-500 mt-1">
              <span>0.001 — Off (prompt-only)</span>
              <span>0.3–0.7 — Moderate sync</span>
              <span>1.0 — Max audio influence</span>
            </div>
            <p className="text-xs text-gray-400 mt-1">
              How much audio influences video generation. At 0.001, audio has no effect on visuals.
              Raise this for audio-reactive video where motion syncs to beats and mood.
            </p>
          </div>

          {/* Stitch Mode */}
          <div className="mb-4">
            <label className="flex items-center gap-3 cursor-pointer">
              <div
                onClick={() => setSettings((prev) => ({ ...prev, director_stitch: !prev.director_stitch }))}
                className={`relative w-11 h-6 rounded-full transition-colors ${
                  settings.director_stitch ? 'bg-blue-600' : 'bg-gray-700'
                }`}
              >
                <div className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform ${
                  settings.director_stitch ? 'translate-x-5' : ''
                }`} />
              </div>
              <span className="text-sm font-medium">Stitch Mode (Smooth Prompt Transitions)</span>
            </label>
            <p className="text-xs text-gray-400 mt-1">
              When OFF (default), prompt segments have sharp cuts between them — ideal for beat-driven music videos.
              When ON, prompt attention softens at segment boundaries for cinematic cross-dissolve transitions.
            </p>
          </div>

          {/* Auto Image Description */}
          <div className="mb-4">
            <label className="flex items-center gap-3 cursor-pointer">
              <div
                onClick={() => setSettings((prev) => ({ ...prev, director_auto_image_desc: !(prev.director_auto_image_desc ?? true) }))}
                className={`relative w-11 h-6 rounded-full transition-colors ${
                  (settings.director_auto_image_desc ?? true) ? 'bg-blue-600' : 'bg-gray-700'
                }`}
              >
                <div className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform ${
                  (settings.director_auto_image_desc ?? true) ? 'translate-x-5' : ''
                }`} />
              </div>
              <span className="text-sm font-medium">Auto Image Description</span>
            </label>
            <p className="text-xs text-gray-400 mt-1">
              Automatically fills the Director's image_description field from the scene prompt, giving the model
              richer context about what reference images contain for better prompt-image alignment.
            </p>
          </div>

          {/* Video Negative Prompt */}
          <div className="mt-4 pt-4 border-t border-gray-800">
            <label className="block text-sm font-medium mb-2">Global Negative Prompt (Video Generation)</label>
            <textarea
              value={settings.global_video_negative_prompt || ''}
              onChange={(e) => setSettings((prev) => ({ ...prev, global_video_negative_prompt: e.target.value }))}
              placeholder="e.g. static, frozen, blurry, jittery, low quality, deformed..."
              className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 h-20 text-sm"
            />
            <p className="text-xs text-gray-400 mt-1">
              Applied to all LTX Director video workflows via the negative_prompt field.
              Tells the model what to avoid in generated video clips.
            </p>
          </div>
        </section>

        {/* Color Correction */}
        <section className="bg-gray-900 border border-gray-800 rounded-lg p-6">
          <h2 className="text-xl font-semibold mb-4">Color Correction</h2>
          <div className="space-y-3">
            <label className="flex items-center gap-3 cursor-pointer">
              <div
                onClick={() => setSettings((prev) => ({ ...prev, color_correction_enabled: !prev.color_correction_enabled }))}
                className={`relative w-11 h-6 rounded-full transition-colors ${
                  settings.color_correction_enabled !== false ? 'bg-blue-600' : 'bg-gray-700'
                }`}
              >
                <div className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform ${
                  settings.color_correction_enabled !== false ? 'translate-x-5' : ''
                }`} />
              </div>
              <span className="text-sm font-medium">Auto Color Correction</span>
            </label>
            <p className="text-xs text-gray-400">
              When enabled, generated videos are automatically color-corrected to match their input reference frame.
              This prevents brightness and color drift that AI video models commonly introduce. Uses per-channel
              gain matching between the reference image and the video's first frame, applied via FFmpeg in a single pass.
            </p>
            <p className="text-xs text-gray-500">
              You can also manually trigger color correction per-scene from the Tools tab in the Scene Editor.
            </p>
          </div>
        </section>

        {/* Network Access */}
        <section className="bg-gray-900 border border-gray-800 rounded-lg p-6">
          <h2 className="text-xl font-semibold mb-4">Network Access</h2>
          <div className="space-y-3">
            <label className="flex items-center gap-3 cursor-pointer">
              <div
                onClick={() => setSettings((prev) => ({ ...prev, network_access: !prev.network_access }))}
                className={`relative w-11 h-6 rounded-full transition-colors ${
                  settings.network_access ? 'bg-blue-600' : 'bg-gray-700'
                }`}
              >
                <div className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform ${
                  settings.network_access ? 'translate-x-5' : ''
                }`} />
              </div>
              <span className="text-sm font-medium">Allow LAN / WAN Access</span>
            </label>
            <p className="text-xs text-gray-400">
              When enabled, the server binds to all network interfaces (0.0.0.0) instead of localhost only.
              This allows other machines on your network to access the app by your IP address and port (default: 8899).
              Useful for checking generation progress from another device.
            </p>
            <div className="mt-4">
              <label className="block text-sm font-medium mb-1">Server Port</label>
              <div className="flex items-center gap-3">
                <input
                  type="number"
                  min={1024}
                  max={65535}
                  value={settings.app_port ?? 8899}
                  onChange={(e) => {
                    const val = parseInt(e.target.value, 10);
                    if (!isNaN(val)) {
                      setSettings((prev) => ({ ...prev, app_port: Math.max(1024, Math.min(65535, val)) }));
                    }
                  }}
                  className="w-28 px-3 py-1.5 bg-gray-800 border border-gray-700 rounded text-gray-100 text-sm"
                />
                <span className="text-xs text-gray-500">Default: 8899 (range: 1024-65535)</span>
              </div>
              <p className="text-xs text-gray-400 mt-1">
                The port number the app listens on. Other devices can reach the app at your-ip:port.
              </p>
            </div>
            <p className="text-xs text-yellow-500 font-medium">
              Requires app restart to take effect.
            </p>
          </div>
        </section>

        {/* Export Transitions */}
        <section className="bg-gray-900 border border-gray-800 rounded-lg p-6">
          <h2 className="text-xl font-semibold mb-4">Export Transitions</h2>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium mb-1">Transition Type</label>
              <select
                value={settings.export_transition_type || 'crossfade'}
                onChange={(e) => setSettings((prev) => ({ ...prev, export_transition_type: e.target.value }))}
                className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-white text-sm"
              >
                <option value="none">None (hard cut)</option>
                <option value="crossfade">Crossfade</option>
                <option value="dissolve">Dissolve</option>
                <option value="wipe_left">Wipe Left</option>
                <option value="wipe_right">Wipe Right</option>
                <option value="slide_left">Slide Left</option>
                <option value="slide_right">Slide Right</option>
              </select>
              <p className="text-xs text-gray-400 mt-1">
                Default transition applied between video clips during export. Crossfade is recommended to smooth visual jarring between AI-generated clips.
              </p>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Transition Duration (seconds)</label>
              <input
                type="number"
                min={0.1}
                max={3.0}
                step={0.1}
                value={settings.export_transition_duration ?? 0.5}
                onChange={(e) => setSettings((prev) => ({ ...prev, export_transition_duration: parseFloat(e.target.value) || 0.5 }))}
                className="w-32 px-3 py-2 bg-gray-800 border border-gray-700 rounded text-white text-sm"
                disabled={settings.export_transition_type === 'none'}
              />
              <p className="text-xs text-gray-400 mt-1">
                How long the transition lasts. Shorter (0.3s) for fast cuts, longer (1.0s) for smoother blending.
              </p>
            </div>
            <label className="flex items-center gap-3 cursor-pointer">
              <div
                onClick={() => setSettings((prev) => ({ ...prev, export_color_match_clips: !(prev.export_color_match_clips !== false) }))}
                className={`relative w-11 h-6 rounded-full transition-colors ${
                  settings.export_color_match_clips !== false ? 'bg-blue-600' : 'bg-gray-700'
                }`}
              >
                <div className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform ${
                  settings.export_color_match_clips !== false ? 'translate-x-5' : ''
                }`} />
              </div>
              <span className="text-sm font-medium">Adjacent Clip Color Matching</span>
            </label>
            <p className="text-xs text-gray-400">
              When enabled, the export pipeline compares the last frame of each clip with the first frame of the next
              clip and applies per-channel color correction to reduce the visual jarring from AI color drift between
              independently generated video segments.
            </p>
            <label className="flex items-center gap-3 cursor-pointer mt-3">
              <div
                onClick={() => setSettings((prev) => ({ ...prev, export_lfff_trim_enabled: !(prev.export_lfff_trim_enabled ?? true) }))}
                className={`relative w-11 h-6 rounded-full transition-colors ${
                  (settings.export_lfff_trim_enabled ?? true) ? 'bg-blue-600' : 'bg-gray-700'
                }`}
              >
                <div className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform ${
                  (settings.export_lfff_trim_enabled ?? true) ? 'translate-x-5' : ''
                }`} />
              </div>
              <span className="text-sm font-medium">Enable LFFF Scene Trim</span>
            </label>
            <p className="text-xs text-gray-400">
              When a scene uses the previous scene&apos;s last frame as its first frame, this trims the duplicate
              opening frame during export to prevent a 1-frame stutter at the cut point. Disable to keep all frames
              intact for testing.
            </p>
          </div>
        </section>

        {/* Whisper Settings */}
        <section className="bg-gray-900 border border-gray-800 rounded-lg p-6">
          <h2 className="text-xl font-semibold mb-4">Whisper (Speech-to-Text)</h2>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium mb-3">Mode</label>
              <div className="space-y-2">
                <label className="flex items-center gap-3 p-3 border border-gray-700 rounded cursor-pointer hover:bg-gray-800 transition-colors">
                  <input
                    type="radio"
                    name="whisper-mode"
                    checked={settings.whisper_mode === 'local'}
                    onChange={() => setSettings((prev) => ({ ...prev, whisper_mode: 'local' }))}
                    className="w-4 h-4"
                  />
                  <span className="text-sm">Local</span>
                </label>
                <label className="flex items-center gap-3 p-3 border border-gray-700 rounded cursor-pointer hover:bg-gray-800 transition-colors">
                  <input
                    type="radio"
                    name="whisper-mode"
                    checked={settings.whisper_mode === 'remote'}
                    onChange={() => setSettings((prev) => ({ ...prev, whisper_mode: 'remote' }))}
                    className="w-4 h-4"
                  />
                  <span className="text-sm">Remote API</span>
                </label>
                <label className="flex items-center gap-3 p-3 border border-gray-700 rounded cursor-pointer hover:bg-gray-800 transition-colors">
                  <input
                    type="radio"
                    name="whisper-mode"
                    checked={settings.whisper_mode === 'comfyui'}
                    onChange={() => setSettings((prev) => ({ ...prev, whisper_mode: 'comfyui' }))}
                    className="w-4 h-4"
                  />
                  <div>
                    <span className="text-sm">ComfyUI Workflow</span>
                    <span className="block text-xs text-gray-500 mt-0.5">Uses ComfyUI-Whisper extension on a ComfyUI server</span>
                  </div>
                </label>
              </div>
            </div>

            {settings.whisper_mode === 'remote' && (
              <div>
                <label className="block text-sm font-medium mb-2">Remote URL</label>
                <input
                  type="url"
                  value={settings.whisper_remote_url || ''}
                  onChange={(e) => setSettings((prev) => ({ ...prev, whisper_remote_url: e.target.value }))}
                  placeholder="https://api.openai.com/v1/audio/transcriptions"
                  className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 text-sm"
                />
              </div>
            )}

            {settings.whisper_mode === 'comfyui' && (
              <div>
                <label className="block text-sm font-medium mb-2">ComfyUI Server URL</label>
                <input
                  type="url"
                  value={settings.whisper_comfyui_url || ''}
                  onChange={(e) => setSettings((prev) => ({ ...prev, whisper_comfyui_url: e.target.value }))}
                  placeholder="http://192.168.1.100:8188"
                  className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 text-sm"
                />
                <p className="text-xs text-gray-500 mt-1">
                  Requires the ComfyUI-Whisper extension (yuvraj108c/ComfyUI-Whisper) installed on the server
                </p>
              </div>
            )}

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-sm font-medium mb-2">Model</label>
                <select
                  value={settings.whisper_model}
                  onChange={(e) => setSettings((prev) => ({ ...prev, whisper_model: e.target.value }))}
                  className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 focus:outline-none focus:border-blue-500 text-sm"
                >
                  <option value="tiny">Tiny</option>
                  <option value="base">Base</option>
                  <option value="small">Small</option>
                  <option value="medium">Medium</option>
                  <option value="large">Large</option>
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium mb-2">Language</label>
                <select
                  value={settings.whisper_language || 'English'}
                  onChange={(e) => setSettings((prev) => ({ ...prev, whisper_language: e.target.value }))}
                  className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 focus:outline-none focus:border-blue-500 text-sm"
                >
                  <option value="auto">Auto Detect</option>
                  <option value="English">English</option>
                  <option value="Spanish">Spanish</option>
                  <option value="French">French</option>
                  <option value="German">German</option>
                  <option value="Italian">Italian</option>
                  <option value="Portuguese">Portuguese</option>
                  <option value="Japanese">Japanese</option>
                  <option value="Korean">Korean</option>
                  <option value="Chinese">Chinese</option>
                  <option value="Russian">Russian</option>
                  <option value="Arabic">Arabic</option>
                  <option value="Hindi">Hindi</option>
                  <option value="Dutch">Dutch</option>
                  <option value="Swedish">Swedish</option>
                  <option value="Turkish">Turkish</option>
                  <option value="Polish">Polish</option>
                </select>
              </div>
            </div>

            <button
              onClick={() => testWhisperMutation.mutate()}
              className="w-full px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded text-sm font-medium transition-colors flex items-center justify-center gap-2"
            >
              {testWhisperMutation.isPending ? (
                <>
                  <Loader size={16} className="animate-spin" />
                  Testing...
                </>
              ) : testResults['whisper'] === true ? (
                <>
                  <Check size={16} className="text-green-500" />
                  Connected
                </>
              ) : testResults['whisper'] === false ? (
                <>
                  <X size={16} className="text-red-500" />
                  Connection Failed
                </>
              ) : (
                'Test Connection'
              )}
            </button>
          </div>
        </section>

        {/* LLM APIs */}
        <section className="bg-gray-900 border border-gray-800 rounded-lg p-6">
          <h2 className="text-xl font-semibold mb-4">LLM APIs</h2>
          <div className="space-y-6">
            {/* OpenAI */}
            <div className={`p-4 rounded border ${settings.default_llm_provider === 'openai' ? 'bg-gray-800 border-blue-500' : 'bg-gray-800 border-gray-700'}`}>
              <div className="flex items-center justify-between mb-3">
                <h3 className="font-medium">OpenAI</h3>
                <button
                  onClick={() => setSettings((prev) => ({ ...prev, default_llm_provider: prev.default_llm_provider === 'openai' ? '' : 'openai' }))}
                  className={`flex items-center gap-1.5 px-3 py-1 rounded text-xs font-medium transition-colors ${
                    settings.default_llm_provider === 'openai'
                      ? 'bg-blue-600 text-white'
                      : 'bg-gray-700 text-gray-400 hover:bg-gray-600 hover:text-gray-200'
                  }`}
                >
                  {settings.default_llm_provider === 'openai' && <Check size={12} />}
                  {settings.default_llm_provider === 'openai' ? 'Default' : 'Set as Default'}
                </button>
              </div>
              <div className="space-y-3">
                <div>
                  <label className="block text-sm font-medium mb-2">API Key</label>
                  <input
                    type="password"
                    value={settings.openai_api_key || ''}
                    onChange={(e) => setSettings((prev) => ({ ...prev, openai_api_key: e.target.value }))}
                    placeholder="sk-..."
                    className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 text-sm"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium mb-2">Model</label>
                  <select
                    value={settings.openai_model || ''}
                    onChange={(e) => setSettings((prev) => ({ ...prev, openai_model: e.target.value }))}
                    className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-gray-100 focus:outline-none focus:border-blue-500 text-sm"
                  >
                    <option value="">Select a model</option>
                    <option value="gpt-5.5">GPT-5.5</option>
                    <option value="gpt-5.4-mini">GPT-5.4 Mini</option>
                    <option value="gpt-5.4-nano">GPT-5.4 Nano</option>
                    <option value="gpt-4.1">GPT-4.1</option>
                    <option value="gpt-4.1-mini">GPT-4.1 Mini</option>
                    <option value="gpt-4.1-nano">GPT-4.1 Nano</option>
                    <option value="gpt-4o">GPT-4o</option>
                    <option value="gpt-4o-mini">GPT-4o Mini</option>
                    <option value="o3-mini">o3 Mini</option>
                  </select>
                </div>
                <button
                  onClick={() => testLLMMutation.mutate('openai')}
                  className="w-full px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm font-medium transition-colors flex items-center justify-center gap-2"
                  disabled={testLLMMutation.isPending || !settings.openai_api_key}
                >
                  {testLLMMutation.isPending && testLLMMutation.variables === 'openai' ? (
                    <>
                      <Loader size={14} className="animate-spin" />
                      Testing...
                    </>
                  ) : testResults['openai'] === true ? (
                    <>
                      <Check size={14} className="text-green-500" />
                      Connected
                    </>
                  ) : testResults['openai'] === false ? (
                    <>
                      <X size={14} className="text-red-500" />
                      Failed
                    </>
                  ) : (
                    'Test Connection'
                  )}
                </button>
              </div>
            </div>

            {/* Anthropic */}
            <div className={`p-4 rounded border ${settings.default_llm_provider === 'anthropic' ? 'bg-gray-800 border-blue-500' : 'bg-gray-800 border-gray-700'}`}>
              <div className="flex items-center justify-between mb-3">
                <h3 className="font-medium">Anthropic</h3>
                <button
                  onClick={() => setSettings((prev) => ({ ...prev, default_llm_provider: prev.default_llm_provider === 'anthropic' ? '' : 'anthropic' }))}
                  className={`flex items-center gap-1.5 px-3 py-1 rounded text-xs font-medium transition-colors ${
                    settings.default_llm_provider === 'anthropic'
                      ? 'bg-blue-600 text-white'
                      : 'bg-gray-700 text-gray-400 hover:bg-gray-600 hover:text-gray-200'
                  }`}
                >
                  {settings.default_llm_provider === 'anthropic' && <Check size={12} />}
                  {settings.default_llm_provider === 'anthropic' ? 'Default' : 'Set as Default'}
                </button>
              </div>
              <div className="space-y-3">
                <div>
                  <label className="block text-sm font-medium mb-2">API Key</label>
                  <input
                    type="password"
                    value={settings.anthropic_api_key || ''}
                    onChange={(e) => setSettings((prev) => ({ ...prev, anthropic_api_key: e.target.value }))}
                    placeholder="sk-ant-..."
                    className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 text-sm"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium mb-2">Model</label>
                  <select
                    value={settings.anthropic_model || ''}
                    onChange={(e) => setSettings((prev) => ({ ...prev, anthropic_model: e.target.value }))}
                    className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-gray-100 focus:outline-none focus:border-blue-500 text-sm"
                  >
                    <option value="">Select a model</option>
                    <option value="claude-opus-4-7">Claude Opus 4.7</option>
                    <option value="claude-opus-4-6">Claude Opus 4.6</option>
                    <option value="claude-sonnet-4-6">Claude Sonnet 4.6</option>
                    <option value="claude-haiku-4-5-20251001">Claude Haiku 4.5</option>
                    <option value="claude-sonnet-4-20250514">Claude Sonnet 4</option>
                    <option value="claude-opus-4-20250514">Claude Opus 4</option>
                    <option value="claude-3-5-sonnet-20241022">Claude 3.5 Sonnet</option>
                  </select>
                </div>
                <button
                  onClick={() => testLLMMutation.mutate('anthropic')}
                  className="w-full px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm font-medium transition-colors flex items-center justify-center gap-2"
                  disabled={testLLMMutation.isPending || !settings.anthropic_api_key}
                >
                  {testLLMMutation.isPending && testLLMMutation.variables === 'anthropic' ? (
                    <>
                      <Loader size={14} className="animate-spin" />
                      Testing...
                    </>
                  ) : testResults['anthropic'] === true ? (
                    <>
                      <Check size={14} className="text-green-500" />
                      Connected
                    </>
                  ) : testResults['anthropic'] === false ? (
                    <>
                      <X size={14} className="text-red-500" />
                      Failed
                    </>
                  ) : (
                    'Test Connection'
                  )}
                </button>
              </div>
            </div>

            {/* Google Gemini */}
            <div className={`p-4 rounded border ${settings.default_llm_provider === 'gemini' ? 'bg-gray-800 border-blue-500' : 'bg-gray-800 border-gray-700'}`}>
              <div className="flex items-center justify-between mb-3">
                <h3 className="font-medium">Google Gemini</h3>
                <button
                  onClick={() => setSettings((prev) => ({ ...prev, default_llm_provider: prev.default_llm_provider === 'gemini' ? '' : 'gemini' }))}
                  className={`flex items-center gap-1.5 px-3 py-1 rounded text-xs font-medium transition-colors ${
                    settings.default_llm_provider === 'gemini'
                      ? 'bg-blue-600 text-white'
                      : 'bg-gray-700 text-gray-400 hover:bg-gray-600 hover:text-gray-200'
                  }`}
                >
                  {settings.default_llm_provider === 'gemini' && <Check size={12} />}
                  {settings.default_llm_provider === 'gemini' ? 'Default' : 'Set as Default'}
                </button>
              </div>
              <div className="space-y-3">
                <div>
                  <label className="block text-sm font-medium mb-2">API Key</label>
                  <input
                    type="password"
                    value={settings.gemini_api_key || ''}
                    onChange={(e) => setSettings((prev) => ({ ...prev, gemini_api_key: e.target.value }))}
                    placeholder="AIza..."
                    className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 text-sm"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium mb-2">Model</label>
                  <select
                    value={settings.gemini_model || ''}
                    onChange={(e) => setSettings((prev) => ({ ...prev, gemini_model: e.target.value }))}
                    className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-gray-100 focus:outline-none focus:border-blue-500 text-sm"
                  >
                    <option value="">Select a model</option>
                    <option value="gemini-2.5-pro-preview-05-06">Gemini 2.5 Pro</option>
                    <option value="gemini-2.5-flash-preview-04-17">Gemini 2.5 Flash</option>
                    <option value="gemini-2.0-flash">Gemini 2.0 Flash</option>
                    <option value="gemini-2.0-flash-lite">Gemini 2.0 Flash Lite</option>
                  </select>
                </div>
                <button
                  onClick={() => testLLMMutation.mutate('gemini')}
                  className="w-full px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm font-medium transition-colors flex items-center justify-center gap-2"
                  disabled={testLLMMutation.isPending || !settings.gemini_api_key}
                >
                  {testLLMMutation.isPending && testLLMMutation.variables === 'gemini' ? (
                    <>
                      <Loader size={14} className="animate-spin" />
                      Testing...
                    </>
                  ) : testResults['gemini'] === true ? (
                    <>
                      <Check size={14} className="text-green-500" />
                      Connected
                    </>
                  ) : testResults['gemini'] === false ? (
                    <>
                      <X size={14} className="text-red-500" />
                      Failed
                    </>
                  ) : (
                    'Test Connection'
                  )}
                </button>
              </div>
            </div>

            {/* Ollama (Local LLM) — Multi-Server Pool */}
            <div className={`p-4 rounded border ${settings.default_llm_provider === 'ollama' ? 'bg-gray-800 border-blue-500' : 'bg-gray-800 border-gray-700'}`}>
              <div className="flex items-center justify-between mb-3">
                <h3 className="font-medium">Ollama (Local)</h3>
                <button
                  onClick={() => setSettings((prev) => ({ ...prev, default_llm_provider: prev.default_llm_provider === 'ollama' ? '' : 'ollama' }))}
                  className={`flex items-center gap-1.5 px-3 py-1 rounded text-xs font-medium transition-colors ${
                    settings.default_llm_provider === 'ollama'
                      ? 'bg-blue-600 text-white'
                      : 'bg-gray-700 text-gray-400 hover:bg-gray-600 hover:text-gray-200'
                  }`}
                >
                  {settings.default_llm_provider === 'ollama' && <Check size={12} />}
                  {settings.default_llm_provider === 'ollama' ? 'Default' : 'Set as Default'}
                </button>
              </div>
              <div className="space-y-3">
                {/* Server URL list */}
                <div>
                  <label className="block text-sm font-medium mb-2">
                    Servers {(settings.ollama_urls || []).length > 0 && <span className="text-gray-400 font-normal">({(settings.ollama_urls || []).length} server{(settings.ollama_urls || []).length !== 1 ? 's' : ''} — round-robin)</span>}
                  </label>
                  <div className="space-y-2 mb-2">
                    {(settings.ollama_urls || []).map((url, index) => (
                      <div key={index} className="flex gap-2 items-center">
                        <input
                          type="text"
                          value={url}
                          readOnly
                          className="flex-1 px-3 py-2 bg-gray-700 border border-gray-600 rounded text-gray-300 text-sm"
                        />
                        <button
                          onClick={async () => {
                            setOllamaServerTests((prev) => ({ ...prev, [url]: 'testing' }));
                            try {
                              const res = await testOllamaSingle(url);
                              setOllamaServerTests((prev) => ({ ...prev, [url]: res.data.success ? 'ok' : 'fail' }));
                            } catch {
                              setOllamaServerTests((prev) => ({ ...prev, [url]: 'fail' }));
                            }
                          }}
                          className="p-2 hover:bg-gray-700 rounded transition-colors"
                          title="Test this server"
                          disabled={ollamaServerTests[url] === 'testing'}
                        >
                          {ollamaServerTests[url] === 'testing' ? (
                            <Loader size={14} className="animate-spin text-gray-400" />
                          ) : ollamaServerTests[url] === 'ok' ? (
                            <Check size={14} className="text-green-500" />
                          ) : ollamaServerTests[url] === 'fail' ? (
                            <X size={14} className="text-red-500" />
                          ) : (
                            <Zap size={14} className="text-gray-400" />
                          )}
                        </button>
                        <button
                          onClick={() => {
                            setSettings((prev) => ({
                              ...prev,
                              ollama_urls: (prev.ollama_urls || []).filter((_, i) => i !== index),
                              // Keep legacy field in sync with first URL
                              ollama_base_url: (prev.ollama_urls || []).filter((_, i) => i !== index)[0] || '',
                            }));
                            // Clear test state for removed URL
                            setOllamaServerTests((prev) => {
                              const next = { ...prev };
                              delete next[url];
                              return next;
                            });
                          }}
                          className="p-2 text-red-400 hover:text-red-300 hover:bg-gray-700 rounded transition-colors"
                          title="Remove server"
                        >
                          <X size={14} />
                        </button>
                      </div>
                    ))}
                  </div>
                  <div className="flex gap-2">
                    <input
                      type="url"
                      value={newOllamaUrl}
                      onChange={(e) => setNewOllamaUrl(e.target.value)}
                      placeholder="http://localhost:11434"
                      className="flex-1 px-3 py-2 bg-gray-700 border border-gray-600 rounded text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 text-sm"
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && newOllamaUrl.trim()) {
                          setSettings((prev) => ({
                            ...prev,
                            ollama_urls: [...(prev.ollama_urls || []), newOllamaUrl.trim()],
                            ollama_base_url: (prev.ollama_urls || []).length === 0 ? newOllamaUrl.trim() : prev.ollama_base_url,
                          }));
                          setNewOllamaUrl('');
                        }
                      }}
                    />
                    <button
                      onClick={() => {
                        if (newOllamaUrl.trim()) {
                          setSettings((prev) => ({
                            ...prev,
                            ollama_urls: [...(prev.ollama_urls || []), newOllamaUrl.trim()],
                            ollama_base_url: (prev.ollama_urls || []).length === 0 ? newOllamaUrl.trim() : prev.ollama_base_url,
                          }));
                          setNewOllamaUrl('');
                        }
                      }}
                      className="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded text-sm font-medium transition-colors"
                    >
                      Add Server
                    </button>
                  </div>
                </div>
                {/* Model selector */}
                <div>
                  <label className="block text-sm font-medium mb-2">Model</label>
                  <div className="flex gap-2">
                    <select
                      value={settings.ollama_model || ''}
                      onChange={(e) => setSettings((prev) => ({ ...prev, ollama_model: e.target.value }))}
                      className="flex-1 px-3 py-2 bg-gray-700 border border-gray-600 rounded text-gray-100 focus:outline-none focus:border-blue-500 text-sm"
                    >
                      <option value="">Select a model</option>
                      {(settings.ollama_available_models || []).map((m) => (
                        <option key={m} value={m}>{m}</option>
                      ))}
                    </select>
                    <button
                      onClick={async () => {
                        setOllamaRefreshing(true);
                        setOllamaRefreshMsg(null);
                        try {
                          const res = await refreshOllamaModels();
                          const data = res.data;
                          if (data.success) {
                            setSettings((prev) => ({ ...prev, ollama_available_models: data.models }));
                            setOllamaRefreshMsg(`${data.models.length} models found`);
                          } else {
                            setOllamaRefreshMsg(data.message || 'Failed');
                          }
                        } catch (err: any) {
                          setOllamaRefreshMsg(err?.response?.data?.message || 'Connection failed');
                        } finally {
                          setOllamaRefreshing(false);
                        }
                      }}
                      className="px-3 py-2 bg-gray-600 hover:bg-gray-500 rounded text-sm transition-colors flex items-center gap-1"
                      disabled={ollamaRefreshing || (settings.ollama_urls || []).length === 0}
                      title="Refresh available models from all Ollama servers"
                    >
                      <RefreshCw size={14} className={ollamaRefreshing ? 'animate-spin' : ''} />
                    </button>
                  </div>
                  {ollamaRefreshMsg && (
                    <p className="text-xs mt-1 text-gray-400">{ollamaRefreshMsg}</p>
                  )}
                </div>
                <button
                  onClick={() => testLLMMutation.mutate('ollama')}
                  className="w-full px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm font-medium transition-colors flex items-center justify-center gap-2"
                  disabled={testLLMMutation.isPending || (settings.ollama_urls || []).length === 0}
                >
                  {testLLMMutation.isPending && testLLMMutation.variables === 'ollama' ? (
                    <>
                      <Loader size={14} className="animate-spin" />
                      Testing...
                    </>
                  ) : testResults['ollama'] === true ? (
                    <>
                      <Check size={14} className="text-green-500" />
                      Connected
                    </>
                  ) : testResults['ollama'] === false ? (
                    <>
                      <X size={14} className="text-red-500" />
                      Failed
                    </>
                  ) : (
                    'Test Connection'
                  )}
                </button>
                <p className="text-xs text-gray-500">
                  Free local LLM with round-robin dispatch across multiple servers.
                  Recommended: qwen3:14b for 12-16GB VRAM. Prompts are optimized for smaller models.
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* Workflow Management */}
        <section className="bg-gray-900 border border-gray-800 rounded-lg p-6">
          <h2 className="text-xl font-semibold mb-4">Workflow Management</h2>

          <div className="mb-6">
            <button
              onClick={() => fileInputRef.current?.click()}
              className="w-full px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded text-sm font-medium transition-colors flex items-center justify-center gap-2"
              disabled={introspectingWorkflow}
            >
              <Upload size={16} />
              {introspectingWorkflow ? 'Processing...' : 'Upload Workflow'}
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".json"
              onChange={handleWorkflowFileSelect}
              className="hidden"
            />
          </div>

          {defaultWorkflows.length > 0 && (
            <div className="mb-6">
              <h3 className="text-sm font-medium mb-3 text-gray-300">Default Workflows</h3>
              <div className="space-y-2">
                {defaultWorkflows.map((workflow) => (
                  <div key={workflow.id} className="p-3 bg-gray-800 rounded border border-gray-700 text-sm">
                    <div className="flex items-start justify-between">
                      <div>
                        <p className="font-medium">{workflow.name}</p>
                        <p className="text-xs text-gray-400 mt-1">{workflow.workflow_type} workflow</p>
                      </div>
                      <span className="px-2 py-1 bg-gray-700 rounded text-xs text-gray-300">
                        {(workflow.field_mappings || []).length} fields
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {customWorkflows.length > 0 && (
            <div>
              <h3 className="text-sm font-medium mb-3 text-gray-300">Custom Workflows</h3>
              <div className="space-y-2">
                {customWorkflows.map((workflow) => (
                  <div key={workflow.id} className="p-3 bg-gray-800 rounded border border-gray-700 text-sm">
                    <div className="flex items-start justify-between">
                      <div>
                        <p className="font-medium">{workflow.name}</p>
                        <p className="text-xs text-gray-400 mt-1">{workflow.workflow_type} workflow</p>
                      </div>
                      <button
                        onClick={() => {
                          if (confirm('Delete this workflow?')) {
                            deleteWorkflowMutation.mutate(workflow.id);
                          }
                        }}
                        className="text-gray-400 hover:text-red-500 transition-colors"
                        disabled={deleteWorkflowMutation.isPending}
                      >
                        <Trash2 size={16} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {workflows.length === 0 && (
            <div className="text-center text-gray-400 py-8">
              <p className="text-sm">No workflows available</p>
            </div>
          )}
        </section>

        {/* Save Button */}
        {/* RunPod Support */}
        <section className="bg-gray-900 border border-gray-800 rounded-lg p-6">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <Cloud size={22} className="text-purple-400" />
              <h2 className="text-xl font-semibold">RunPod Support</h2>
            </div>
            <label className="flex items-center gap-2 cursor-pointer">
              <span className="text-sm text-gray-400">{settings.runpod_enabled ? 'Enabled' : 'Disabled'}</span>
              <div
                className={`w-10 h-5 rounded-full transition-colors relative cursor-pointer ${
                  settings.runpod_enabled ? 'bg-purple-600' : 'bg-gray-700'
                }`}
                onClick={() => setSettings(prev => ({ ...prev, runpod_enabled: !prev.runpod_enabled }))}
              >
                <div
                  className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                    settings.runpod_enabled ? 'translate-x-5' : 'translate-x-0.5'
                  }`}
                />
              </div>
            </label>
          </div>

          {settings.runpod_enabled && (
            <div className="space-y-5">
              <p className="text-sm text-gray-400">
                Connect RunPod GPU pods for on-demand compute. Pods will auto-start when a job needs them and spin down after the idle timeout.
              </p>

              {/* API Key */}
              <div>
                <label className="block text-sm font-medium mb-1">RunPod API Key</label>
                <div className="flex gap-2">
                  <input
                    type="password"
                    value={settings.runpod_api_key || ''}
                    onChange={(e) => setSettings(prev => ({ ...prev, runpod_api_key: e.target.value }))}
                    placeholder="Enter your RunPod API key..."
                    className="flex-1 px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 placeholder-gray-500 focus:outline-none focus:border-purple-500"
                  />
                  <button
                    onClick={handleTestRunPod}
                    disabled={runpodTesting}
                    className="px-3 py-2 bg-purple-700 hover:bg-purple-600 rounded text-sm font-medium transition-colors disabled:opacity-50 flex items-center gap-1"
                  >
                    {runpodTesting ? <Loader size={14} className="animate-spin" /> : <Check size={14} />}
                    Test
                  </button>
                </div>
                {runpodTestResult && (
                  <div className={`mt-2 text-xs px-2 py-1 rounded ${runpodTestResult.success ? 'bg-green-900/40 text-green-300' : 'bg-red-900/40 text-red-300'}`}>
                    {runpodTestResult.message}
                  </div>
                )}
                <p className="text-[10px] text-gray-500 mt-1">
                  Find your API key at <span className="text-purple-400">runpod.io/console/user/settings</span>
                </p>
              </div>

              {/* Idle Timeout */}
              <div>
                <label className="block text-sm font-medium mb-1">Spin Down Timeout (minutes)</label>
                <input
                  type="number"
                  value={settings.runpod_idle_timeout || 30}
                  onChange={(e) => setSettings(prev => ({ ...prev, runpod_idle_timeout: Math.max(1, parseInt(e.target.value) || 30) }))}
                  min="1"
                  max="1440"
                  className="w-32 px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 focus:outline-none focus:border-purple-500"
                />
                <p className="text-[10px] text-gray-500 mt-1">
                  How long a pod can be idle before it is automatically stopped to save costs.
                </p>
              </div>

              {/* Pod List */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <label className="text-sm font-medium">Pods</label>
                  <div className="flex gap-2">
                    <button
                      onClick={handleRefreshRunPodStatuses}
                      disabled={runpodRefreshing}
                      className="flex items-center gap-1 px-2 py-1 bg-gray-700 hover:bg-gray-600 rounded text-xs transition-colors disabled:opacity-50"
                    >
                      <RefreshCw size={12} className={runpodRefreshing ? 'animate-spin' : ''} />
                      Refresh Status
                    </button>
                    <button
                      onClick={addRunPodPod}
                      className="flex items-center gap-1 px-2 py-1 bg-purple-700 hover:bg-purple-600 rounded text-xs transition-colors"
                    >
                      <Plus size={12} />
                      Add Pod
                    </button>
                  </div>
                </div>

                {(!settings.runpod_pods || settings.runpod_pods.length === 0) && (
                  <p className="text-sm text-gray-500 italic py-4 text-center border border-dashed border-gray-700 rounded">
                    No pods configured. Click "Add Pod" to add your RunPod pods.
                  </p>
                )}

                <div className="space-y-3">
                  {(settings.runpod_pods || []).map((pod, index) => {
                    const liveStatus = runpodPodStatuses.find(s => s.pod_id === pod.pod_id);
                    const stateColor = liveStatus?.state === 'running' ? 'text-green-400' :
                      liveStatus?.state === 'starting' ? 'text-yellow-400' :
                      liveStatus?.state === 'stopped' || liveStatus?.state === 'exited' ? 'text-gray-500' :
                      liveStatus?.state === 'error' ? 'text-red-400' : 'text-gray-600';

                    return (
                      <div key={index} className="bg-gray-800 border border-gray-700 rounded-lg p-4 space-y-3">
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2">
                            <div className={`w-2 h-2 rounded-full ${stateColor.replace('text-', 'bg-')}`} />
                            <span className="text-sm font-medium">{pod.label || `Pod ${index + 1}`}</span>
                            {liveStatus && (
                              <span className={`text-xs ${stateColor}`}>
                                {liveStatus.state}
                                {liveStatus.state === 'running' && liveStatus.cost_per_hr > 0 && (
                                  <span className="text-gray-500 ml-1">(${liveStatus.cost_per_hr.toFixed(2)}/hr)</span>
                                )}
                              </span>
                            )}
                          </div>
                          <div className="flex items-center gap-1">
                            {liveStatus?.state === 'stopped' || liveStatus?.state === 'exited' ? (
                              <button
                                onClick={() => handleStartPod(pod.pod_id)}
                                disabled={!pod.pod_id}
                                className="p-1 text-green-400 hover:bg-green-900/30 rounded transition-colors disabled:opacity-30"
                                title="Start pod"
                              >
                                <Play size={14} />
                              </button>
                            ) : liveStatus?.state === 'running' ? (
                              <button
                                onClick={() => handleStopPod(pod.pod_id)}
                                className="p-1 text-yellow-400 hover:bg-yellow-900/30 rounded transition-colors"
                                title="Stop pod"
                              >
                                <Square size={14} />
                              </button>
                            ) : null}
                            <button
                              onClick={() => removeRunPodPod(index)}
                              className="p-1 text-red-400 hover:bg-red-900/30 rounded transition-colors"
                              title="Remove pod"
                            >
                              <Trash2 size={14} />
                            </button>
                          </div>
                        </div>

                        <div className="grid grid-cols-2 gap-3">
                          <div>
                            <label className="block text-[10px] text-gray-500 mb-0.5">Label</label>
                            <input
                              type="text"
                              value={pod.label}
                              onChange={(e) => updateRunPodPod(index, 'label', e.target.value)}
                              placeholder="My GPU Pod"
                              className="w-full px-2 py-1.5 bg-gray-900 border border-gray-700 rounded text-sm text-gray-100 focus:outline-none focus:border-purple-500"
                            />
                          </div>
                          <div>
                            <label className="block text-[10px] text-gray-500 mb-0.5">Service Type</label>
                            <select
                              value={pod.service_type}
                              onChange={(e) => updateRunPodPod(index, 'service_type', e.target.value)}
                              className="w-full px-2 py-1.5 bg-gray-900 border border-gray-700 rounded text-sm text-gray-100 focus:outline-none focus:border-purple-500"
                            >
                              <option value="image">Image (ComfyUI)</option>
                              <option value="video">Video (ComfyUI)</option>
                              <option value="llm">LLM</option>
                              <option value="whisper">Whisper</option>
                            </select>
                          </div>
                          <div>
                            <label className="block text-[10px] text-gray-500 mb-0.5">Pod ID</label>
                            <input
                              type="text"
                              value={pod.pod_id}
                              onChange={(e) => updateRunPodPod(index, 'pod_id', e.target.value)}
                              placeholder="abc123def456"
                              className="w-full px-2 py-1.5 bg-gray-900 border border-gray-700 rounded text-sm text-gray-100 font-mono focus:outline-none focus:border-purple-500"
                            />
                          </div>
                          <div>
                            <label className="block text-[10px] text-gray-500 mb-0.5">API Port</label>
                            <input
                              type="number"
                              value={pod.api_port}
                              onChange={(e) => updateRunPodPod(index, 'api_port', parseInt(e.target.value) || 8188)}
                              className="w-full px-2 py-1.5 bg-gray-900 border border-gray-700 rounded text-sm text-gray-100 focus:outline-none focus:border-purple-500"
                            />
                          </div>
                          <div>
                            <label className="block text-[10px] text-gray-500 mb-0.5">GPU Count</label>
                            <input
                              type="number"
                              min={1}
                              max={8}
                              value={pod.gpu_count || 1}
                              onChange={(e) => updateRunPodPod(index, 'gpu_count', parseInt(e.target.value) || 1)}
                              className="w-full px-2 py-1.5 bg-gray-900 border border-gray-700 rounded text-sm text-gray-100 focus:outline-none focus:border-purple-500"
                            />
                          </div>
                          <div>
                            <label className="block text-[10px] text-gray-500 mb-0.5">GPU Type (optional)</label>
                            <input
                              type="text"
                              value={pod.gpu_type_id}
                              onChange={(e) => updateRunPodPod(index, 'gpu_type_id', e.target.value)}
                              placeholder="NVIDIA RTX 4090"
                              className="w-full px-2 py-1.5 bg-gray-900 border border-gray-700 rounded text-sm text-gray-100 focus:outline-none focus:border-purple-500"
                            />
                          </div>
                          <div className="flex items-end">
                            <label className="flex items-center gap-2 cursor-pointer pb-1.5">
                              <input
                                type="checkbox"
                                checked={pod.enabled}
                                onChange={(e) => updateRunPodPod(index, 'enabled', e.target.checked)}
                                className="w-4 h-4 accent-purple-500"
                              />
                              <span className="text-xs text-gray-400">Enabled</span>
                            </label>
                          </div>
                        {/* Show the constructed API URL when we have live status */}
                        {liveStatus?.url && (
                          <div className="mt-2 pt-2 border-t border-gray-700">
                            <span className="text-[10px] text-gray-500">API URL: </span>
                            <span className="text-[10px] text-blue-400 font-mono break-all">{liveStatus.url}</span>
                          </div>
                        )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          )}
        </section>

        {/* GPU Acceleration */}
        <section className="bg-gray-900 border border-gray-800 rounded-lg p-6">
          <h2 className="text-xl font-semibold mb-2 flex items-center gap-2">
            <Monitor size={20} />
            GPU Acceleration
          </h2>
          <p className="text-sm text-gray-400 mb-4">
            Auto-detected GPU capabilities for FFmpeg encoding/decoding and Demucs audio separation.
            Disable to force CPU-only processing.
          </p>

          <div className="flex items-center gap-3 mb-4">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={settings.gpu_acceleration_enabled ?? true}
                onChange={(e) => setSettings({ ...settings, gpu_acceleration_enabled: e.target.checked })}
                className="w-4 h-4 rounded border-gray-600 bg-gray-800 text-blue-500 focus:ring-blue-500"
              />
              <span className="text-sm font-medium">Enable GPU Acceleration</span>
            </label>
            <button
              onClick={handleRedetectGpu}
              disabled={gpuRedetecting}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-gray-700 hover:bg-gray-600 rounded text-sm transition-colors disabled:opacity-50"
            >
              <RefreshCw size={14} className={gpuRedetecting ? 'animate-spin' : ''} />
              Re-detect
            </button>
          </div>

          {gpuLoading ? (
            <div className="flex items-center gap-2 text-gray-400 text-sm">
              <Loader size={14} className="animate-spin" />
              Detecting GPU...
            </div>
          ) : gpuStatus ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* FFmpeg */}
              <div className="bg-gray-800/50 border border-gray-700 rounded-lg p-4">
                <h3 className="text-sm font-semibold mb-2 flex items-center gap-2">
                  <Cpu size={14} />
                  FFmpeg
                  {gpuStatus.ffmpeg.using_gpu ? (
                    <span className="ml-auto px-2 py-0.5 bg-green-900/50 text-green-400 text-xs rounded-full">GPU Active</span>
                  ) : (
                    <span className="ml-auto px-2 py-0.5 bg-yellow-900/50 text-yellow-400 text-xs rounded-full">CPU Only</span>
                  )}
                </h3>
                <div className="space-y-1 text-sm text-gray-300">
                  <div className="flex justify-between">
                    <span className="text-gray-400">GPU Type:</span>
                    <span>{gpuStatus.ffmpeg.gpu_type || 'None detected'}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-400">Encoder:</span>
                    <span className="font-mono text-xs">{gpuStatus.ffmpeg.encoder}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-400">Decoder:</span>
                    <span className="font-mono text-xs">{gpuStatus.ffmpeg.decoder}</span>
                  </div>
                </div>
              </div>

              {/* Demucs */}
              <div className="bg-gray-800/50 border border-gray-700 rounded-lg p-4">
                <h3 className="text-sm font-semibold mb-2 flex items-center gap-2">
                  <Cpu size={14} />
                  Demucs (Audio Separation)
                  {gpuStatus.demucs.using_gpu ? (
                    <span className="ml-auto px-2 py-0.5 bg-green-900/50 text-green-400 text-xs rounded-full">GPU Active</span>
                  ) : (
                    <span className="ml-auto px-2 py-0.5 bg-yellow-900/50 text-yellow-400 text-xs rounded-full">CPU Only</span>
                  )}
                </h3>
                <div className="space-y-1 text-sm text-gray-300">
                  <div className="flex justify-between">
                    <span className="text-gray-400">Device:</span>
                    <span>{gpuStatus.demucs.device}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-400">GPU Name:</span>
                    <span>{gpuStatus.demucs.gpu_name || 'None detected'}</span>
                  </div>
                  {!gpuStatus.demucs.using_gpu && (
                    <p className="text-xs text-gray-500 mt-2">
                      GPU acceleration requires NVIDIA CUDA or AMD ROCm (Linux only).
                    </p>
                  )}
                </div>
              </div>
            </div>
          ) : (
            <p className="text-sm text-gray-500">GPU status unavailable. Click Re-detect to check again.</p>
          )}
        </section>

        {/* Purge Generation Queue */}
        <section className="bg-gray-900 border border-red-900/50 rounded-lg p-6">
          <h2 className="text-xl font-semibold mb-2">Generation Queue</h2>
          <p className="text-sm text-gray-400 mb-4">
            Cancel all pending and running generation jobs. The queue is automatically cleared on app restart,
            but you can use this to stop everything immediately if jobs are stuck or unwanted.
          </p>
          <button
            onClick={async () => {
              if (!confirm('Cancel ALL pending and running generation jobs?')) return;
              try {
                const res = await purgeJobs();
                alert(res.data.message);
              } catch {
                alert('Failed to purge queue');
              }
            }}
            className="flex items-center gap-2 px-4 py-2 bg-red-700 hover:bg-red-600 rounded font-medium text-sm transition-colors"
          >
            <AlertTriangle size={16} />
            Purge All Queued Jobs
          </button>
        </section>

        {/* Import / Export */}
        <section className="bg-gray-900 border border-gray-800 rounded-lg p-6">
          <h2 className="text-xl font-semibold mb-2">Backup &amp; Restore</h2>
          <p className="text-sm text-gray-400 mb-4">
            Export your current settings to a file or import a previous backup. Exports include all API keys, server URLs, and model configurations.
          </p>

          <div className="flex gap-3">
            <button
              onClick={handleExportSettings}
              className="flex items-center gap-2 px-4 py-2 bg-emerald-700 hover:bg-emerald-600 rounded font-medium text-sm transition-colors"
            >
              <Download size={16} />
              Export Settings
            </button>

            <button
              onClick={() => importFileRef.current?.click()}
              className="flex items-center gap-2 px-4 py-2 bg-amber-700 hover:bg-amber-600 rounded font-medium text-sm transition-colors"
            >
              <FolderInput size={16} />
              Import Settings
            </button>
            <input
              ref={importFileRef}
              type="file"
              accept=".json,.rbmn-settings.json"
              onChange={handleImportSettings}
              className="hidden"
            />
          </div>

          {importExportStatus && (
            <div
              className={`mt-3 px-3 py-2 rounded text-sm ${
                importExportStatus.type === 'success'
                  ? 'bg-green-900/40 border border-green-700 text-green-300'
                  : 'bg-red-900/40 border border-red-700 text-red-300'
              }`}
            >
              {importExportStatus.message}
            </div>
          )}
        </section>

        <div className="flex gap-4">
          <button
            onClick={() => navigate('/')}
            className="flex-1 px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded font-medium transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={() => updateSettingsMutation.mutate()}
            disabled={updateSettingsMutation.isPending}
            className="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded font-medium transition-colors disabled:opacity-50"
          >
            {updateSettingsMutation.isPending ? 'Saving...' : 'Save Settings'}
          </button>
        </div>
      </div>

      {/* Move Project Directory Dialog */}
      {showMoveDialog && createPortal(
        <div
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: 'rgba(0, 0, 0, 0.5)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 9999,
          }}
          onClick={() => setShowMoveDialog(false)}
        >
          <div
            style={{
              backgroundColor: '#1f2937',
              borderRadius: '0.5rem',
              border: '1px solid #374151',
              padding: '1.5rem',
              maxWidth: '500px',
              width: '90%',
              boxShadow: '0 20px 25px -5px rgba(0, 0, 0, 0.3)',
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="text-lg font-semibold text-gray-100 mb-2">
              Change Project Directory
            </h2>
            <p className="text-sm text-gray-400 mb-2">
              New location:
            </p>
            <p className="text-sm font-mono text-blue-300 bg-gray-800 px-3 py-2 rounded mb-4 break-all">
              {projectDirInput}
            </p>
            <p className="text-sm text-gray-300 mb-6">
              Would you like to move your existing project data to the new folder?
            </p>

            <div className="flex flex-col gap-3">
              <button
                onClick={async () => {
                  setShowMoveDialog(false);
                  setProjectDirStatus({ type: 'loading', message: 'Moving data...' });
                  try {
                    const res = await changeProjectDir(projectDirInput, true);
                    setProjectDirStatus({ type: 'success', message: `${res.data.message}. Restart the app to use the new directory.` });
                    setSettings((prev) => ({ ...prev, project_dir: res.data.new_path }));
                    setProjectDirInput(res.data.new_path);
                    setProjectDirChanged(false);
                  } catch (e: any) {
                    const detail = e?.response?.data?.detail || 'Failed to change directory';
                    setProjectDirStatus({ type: 'error', message: detail });
                  }
                }}
                className="w-full px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded text-sm font-medium transition-colors text-left"
              >
                Yes, move existing data to new folder
                <span className="block text-xs text-blue-300 mt-0.5">Copies all projects, assets, and database</span>
              </button>

              <button
                onClick={async () => {
                  setShowMoveDialog(false);
                  setProjectDirStatus({ type: 'loading', message: 'Setting new directory...' });
                  try {
                    const res = await changeProjectDir(projectDirInput, false);
                    setProjectDirStatus({ type: 'success', message: `${res.data.message}. Restart the app to use the new directory.` });
                    setSettings((prev) => ({ ...prev, project_dir: res.data.new_path }));
                    setProjectDirInput(res.data.new_path);
                    setProjectDirChanged(false);
                  } catch (e: any) {
                    const detail = e?.response?.data?.detail || 'Failed to change directory';
                    setProjectDirStatus({ type: 'error', message: detail });
                  }
                }}
                className="w-full px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm font-medium transition-colors text-left"
              >
                No, just set the new path (start fresh)
                <span className="block text-xs text-gray-400 mt-0.5">Creates an empty directory at the new location</span>
              </button>

              <button
                onClick={() => setShowMoveDialog(false)}
                className="w-full px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded text-sm font-medium transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}

      {/* Prompt Guidance Modal */}
      {promptGuidanceModal && createPortal(
        <div
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: 'rgba(0, 0, 0, 0.5)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 9999,
          }}
          onClick={closePromptGuidanceModal}
        >
          <div
            style={{
              backgroundColor: '#1f2937',
              borderRadius: '0.5rem',
              border: '1px solid #374151',
              padding: '1.5rem',
              maxWidth: '600px',
              width: '90%',
              maxHeight: '80vh',
              overflowY: 'auto',
              boxShadow: '0 20px 25px -5px rgba(0, 0, 0, 0.3)',
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="text-lg font-semibold text-gray-100 mb-2">
              Prompt Guidance for {promptGuidanceModal.modelName}
            </h2>
            <p className="text-xs text-gray-400 mb-4">
              Add custom instructions or best practices for prompting this {promptGuidanceModal.type} model. This will be appended to the system prompt when enhancing prompts.
            </p>

            <div className="space-y-4">
              {/* Textarea */}
              <textarea
                value={promptGuidanceText}
                onChange={(e) => setPromptGuidanceText(e.target.value)}
                placeholder="Enter guidance text... (e.g., 'Always use detailed descriptions', 'Prefer warm color palettes')"
                rows={10}
                className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 text-sm font-mono resize-y"
              />

              {/* File Upload */}
              <div className="border-2 border-dashed border-gray-700 rounded p-4 text-center hover:border-gray-600 transition-colors">
                <input
                  ref={guidanceFileInputRef}
                  type="file"
                  accept=".txt,.md"
                  onChange={handleGuidanceFileSelect}
                  className="hidden"
                />
                <button
                  onClick={() => guidanceFileInputRef.current?.click()}
                  className="text-sm text-gray-400 hover:text-gray-300 transition-colors"
                >
                  <Upload size={16} className="inline mr-2" />
                  Upload .txt or .md file
                </button>
              </div>

              {/* Buttons */}
              <div className="flex gap-3 justify-end pt-2">
                <button
                  onClick={closePromptGuidanceModal}
                  className="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm font-medium transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={savePromptGuidance}
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded text-sm font-medium transition-colors"
                >
                  Save
                </button>
              </div>
            </div>
          </div>
        </div>,
        document.body
      )}
    </div>
  );
}
