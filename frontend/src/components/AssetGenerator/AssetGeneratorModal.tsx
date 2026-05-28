import { useState, useEffect, useRef, useCallback } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  X, Image, Film, Wand2, Download, Plus, Upload, Loader2, ChevronDown, Clock,
} from 'lucide-react';
import {
  enhancePrompt,
  generateAsset,
  uploadAsset,
  getSettings,
  getScenes,
  getAssets,
  getAssetFileUrl,
  assignAssetToScene,
} from '@/api/client';
import type { Scene, Asset, AppSettings } from '@/types/index';

interface AssetGeneratorModalProps {
  projectId: string;
  onClose: () => void;
}

type TabType = 'image' | 'video';
type VideoMode = 'ff_lf' | 'i2v' | 'v2v';

const IMAGE_RESOLUTIONS = [
  { label: '1024 x 576 (16:9)', w: 1024, h: 576 },
  { label: '1280 x 720 (16:9)', w: 1280, h: 720 },
  { label: '1024 x 1024 (1:1)', w: 1024, h: 1024 },
  { label: '576 x 1024 (9:16)', w: 576, h: 1024 },
  { label: '768 x 768 (1:1)', w: 768, h: 768 },
  { label: '1920 x 1080 (16:9)', w: 1920, h: 1080 },
];

const VIDEO_RESOLUTIONS = [
  { label: '1024 x 576 (16:9)', w: 1024, h: 576 },
  { label: '1280 x 720 (16:9)', w: 1280, h: 720 },
  { label: '576 x 1024 (9:16)', w: 576, h: 1024 },
  { label: '768 x 512 (3:2)', w: 768, h: 512 },
];

const FPS_OPTIONS = [24, 30];

export default function AssetGeneratorModal({ projectId, onClose }: AssetGeneratorModalProps) {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<TabType>('image');

  // Image form state
  const [imgPrompt, setImgPrompt] = useState('');
  const [imgNegPrompt, setImgNegPrompt] = useState('');
  const [imgModel, setImgModel] = useState('klein_t2i');
  const [imgWidth, setImgWidth] = useState(1024);
  const [imgHeight, setImgHeight] = useState(576);
  const [imgSeed, setImgSeed] = useState('');
  const [imgTwoPass, setImgTwoPass] = useState(false);
  const [refImages, setRefImages] = useState<(Asset | null)[]>([null, null, null, null]);

  // Video form state
  const [vidPrompt, setVidPrompt] = useState('');
  const [vidNegPrompt, setVidNegPrompt] = useState('');
  const [vidModel, setVidModel] = useState('ltx_fflf');
  const [vidMode, setVidMode] = useState<VideoMode>('i2v');
  const [vidWidth, setVidWidth] = useState(1024);
  const [vidHeight, setVidHeight] = useState(576);
  const [vidDuration, setVidDuration] = useState('3');
  const [vidFps, setVidFps] = useState(24);
  const [vidSeed, setVidSeed] = useState('');
  const [vidSkipAudio, setVidSkipAudio] = useState(true);
  const [vidFirstFrame, setVidFirstFrame] = useState<Asset | null>(null);
  const [vidLastFrame, setVidLastFrame] = useState<Asset | null>(null);

  // Results
  const [generatedAssets, setGeneratedAssets] = useState<Asset[]>([]);
  const [assignDropdown, setAssignDropdown] = useState<string | null>(null);

  // File input refs
  const refImageInputs = useRef<(HTMLInputElement | null)[]>([]);
  const vidFirstFrameInput = useRef<HTMLInputElement>(null);
  const vidLastFrameInput = useRef<HTMLInputElement>(null);

  // Query settings for defaults
  const { data: settingsData } = useQuery({
    queryKey: ['settings'],
    queryFn: () => getSettings().then(r => r.data),
  });

  // Query scenes for assign dropdown
  const { data: scenesData } = useQuery({
    queryKey: ['scenes', projectId],
    queryFn: () => getScenes(projectId).then(r => r.data),
  });

  // Query generated assets
  const { data: assetsData, refetch: refetchAssets } = useQuery({
    queryKey: ['assets', projectId, 'generated'],
    queryFn: () => getAssets(projectId, 'generated_image').then(r => r.data),
  });

  // Apply settings defaults
  useEffect(() => {
    if (settingsData) {
      const s = settingsData as AppSettings;
      if (s.image_model_type) {
        const model = s.image_model_type === 'zimage_turbo' ? 'zimage_turbo' : 'klein_t2i';
        setImgModel(model);
      }
      if (s.video_model_type) {
        setVidModel(s.video_model_type === 'ltx_sequencer' ? 'ltx_sequencer' : 'ltx_fflf');
      }
      if (s.video_fps) setVidFps(s.video_fps);
    }
  }, [settingsData]);

  // Merge fetched assets into results
  useEffect(() => {
    if (assetsData && assetsData.length > 0) {
      setGeneratedAssets(prev => {
        const existingIds = new Set(prev.map(a => a.id));
        const newAssets = assetsData.filter((a: Asset) => !existingIds.has(a.id));
        return newAssets.length > 0 ? [...prev, ...newAssets] : prev;
      });
    }
  }, [assetsData]);

  // Upload helper
  const handleFileUpload = useCallback(async (file: File): Promise<Asset | null> => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('asset_type', 'reference');
    try {
      const resp = await uploadAsset(projectId, formData);
      return resp.data;
    } catch {
      return null;
    }
  }, [projectId]);

  // Ref image upload handler
  const handleRefImageUpload = useCallback(async (index: number, file: File) => {
    const asset = await handleFileUpload(file);
    if (asset) {
      setRefImages(prev => {
        const next = [...prev];
        next[index] = asset;
        return next;
      });
    }
  }, [handleFileUpload]);

  // Enhance prompt mutation (image)
  const enhanceImgMutation = useMutation({
    mutationFn: async () => {
      const resp = await enhancePrompt(projectId, {
        prompt: imgPrompt,
        context: `Standalone image generation (not scene-specific). Model: ${imgModel}.`,
        is_video: false,
      });
      return resp.data.enhanced_prompt;
    },
    onSuccess: (enhanced) => setImgPrompt(enhanced),
  });

  // Enhance negative prompt mutation (image)
  const enhanceImgNegMutation = useMutation({
    mutationFn: async () => {
      const resp = await enhancePrompt(projectId, {
        prompt: imgNegPrompt,
        context: `NEGATIVE PROMPT: Generate a negative prompt for image generation. Model: ${imgModel}. Return only the negative prompt text.`,
        is_video: false,
      });
      return resp.data.enhanced_prompt;
    },
    onSuccess: (enhanced) => setImgNegPrompt(enhanced),
  });

  // Enhance prompt mutation (video)
  const enhanceVidMutation = useMutation({
    mutationFn: async () => {
      const resp = await enhancePrompt(projectId, {
        prompt: vidPrompt,
        context: `Standalone video generation (not scene-specific). Model: ${vidModel}. Mode: ${vidMode}.`,
        is_video: true,
      });
      return resp.data.enhanced_prompt;
    },
    onSuccess: (enhanced) => setVidPrompt(enhanced),
  });

  // Enhance negative prompt mutation (video)
  const enhanceVidNegMutation = useMutation({
    mutationFn: async () => {
      const resp = await enhancePrompt(projectId, {
        prompt: vidNegPrompt,
        context: `NEGATIVE PROMPT: Generate a negative prompt for video generation. Model: ${vidModel}. Return only the negative prompt text.`,
        is_video: true,
      });
      return resp.data.enhanced_prompt;
    },
    onSuccess: (enhanced) => setVidNegPrompt(enhanced),
  });

  // Generate image mutation
  const generateImgMutation = useMutation({
    mutationFn: async () => {
      const refAssetIds = refImages.filter(Boolean).map(a => a!.id);
      // Determine workflow type based on ref count for Klein
      let workflowType = imgModel;
      if (imgModel === 'klein_t2i' && refAssetIds.length > 0) {
        workflowType = `klein_${refAssetIds.length}ref`;
      }
      const resp = await generateAsset(projectId, {
        asset_type: 'image',
        workflow_type: workflowType,
        prompt: imgPrompt,
        negative_prompt: imgNegPrompt,
        width: imgWidth,
        height: imgHeight,
        seed: imgSeed ? parseInt(imgSeed, 10) : undefined,
        reference_asset_ids: refAssetIds.length > 0 ? refAssetIds : undefined,
        two_pass: imgTwoPass,
      });
      return resp.data;
    },
    onSuccess: () => {
      // Refresh assets after a delay to let the job complete
      setTimeout(() => {
        refetchAssets();
        queryClient.invalidateQueries({ queryKey: ['assets', projectId] });
      }, 3000);
    },
  });

  // Generate video mutation
  const generateVidMutation = useMutation({
    mutationFn: async () => {
      const workflowType = vidMode === 'ff_lf' ? 'ltx_fflf' : vidMode === 'v2v' ? 'ltx_v2v_extend' : 'ltx_i2v';
      const resp = await generateAsset(projectId, {
        asset_type: 'video',
        video_workflow_type: vidModel === 'ltx_sequencer' ? vidModel : workflowType,
        prompt: vidPrompt,
        negative_prompt: vidNegPrompt,
        width: vidWidth,
        height: vidHeight,
        duration: parseFloat(vidDuration) || 3,
        framerate: vidFps,
        seed: vidSeed ? parseInt(vidSeed, 10) : undefined,
        first_frame_asset_id: vidFirstFrame?.id,
        last_frame_asset_id: vidMode === 'ff_lf' ? vidLastFrame?.id : undefined,
        skip_audio_mux: vidSkipAudio,
      });
      return resp.data;
    },
    onSuccess: () => {
      setTimeout(() => {
        refetchAssets();
        queryClient.invalidateQueries({ queryKey: ['assets', projectId] });
      }, 3000);
    },
  });

  const getAssetThumbnailUrl = (asset: Asset) => {
    return getAssetFileUrl(projectId, asset.id);
  };

  const handleDownloadAsset = (asset: Asset) => {
    const url = getAssetFileUrl(projectId, asset.id);
    const a = document.createElement('a');
    a.href = url;
    a.download = asset.filename;
    a.click();
  };

  const scenes = (scenesData || []) as Scene[];

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-gray-900 border border-gray-700 rounded-xl w-full max-w-4xl max-h-[90vh] flex flex-col shadow-2xl"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800">
          <h2 className="text-xl font-bold text-gray-100">Asset Generator</h2>
          <button
            onClick={onClose}
            className="p-1 text-gray-400 hover:text-gray-100 transition-colors"
          >
            <X size={20} />
          </button>
        </div>

        {/* Tab selector */}
        <div className="flex gap-1 px-6 pt-4">
          <button
            onClick={() => setActiveTab('image')}
            className={`px-4 py-2 rounded-t text-sm font-medium flex items-center gap-2 transition-colors ${
              activeTab === 'image'
                ? 'bg-gray-800 text-white border border-gray-700 border-b-gray-900'
                : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            <Image size={16} />
            Image
          </button>
          <button
            onClick={() => setActiveTab('video')}
            className={`px-4 py-2 rounded-t text-sm font-medium flex items-center gap-2 transition-colors ${
              activeTab === 'video'
                ? 'bg-gray-800 text-white border border-gray-700 border-b-gray-900'
                : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            <Film size={16} />
            Video
          </button>
        </div>

        {/* Scrollable body */}
        <div className="flex-1 overflow-y-auto px-6 pb-6">
          {activeTab === 'image' ? (
            <ImageTabContent
              prompt={imgPrompt}
              setPrompt={setImgPrompt}
              negPrompt={imgNegPrompt}
              setNegPrompt={setImgNegPrompt}
              model={imgModel}
              setModel={setImgModel}
              width={imgWidth}
              height={imgHeight}
              setWidth={setImgWidth}
              setHeight={setImgHeight}
              seed={imgSeed}
              setSeed={setImgSeed}
              twoPass={imgTwoPass}
              setTwoPass={setImgTwoPass}
              refImages={refImages}
              setRefImages={setRefImages}
              refImageInputs={refImageInputs}
              onRefUpload={handleRefImageUpload}
              enhanceMutation={enhanceImgMutation}
              enhanceNegMutation={enhanceImgNegMutation}
              generateMutation={generateImgMutation}
              settings={settingsData as AppSettings | undefined}
            />
          ) : (
            <VideoTabContent
              prompt={vidPrompt}
              setPrompt={setVidPrompt}
              negPrompt={vidNegPrompt}
              setNegPrompt={setVidNegPrompt}
              model={vidModel}
              setModel={setVidModel}
              mode={vidMode}
              setMode={setVidMode}
              width={vidWidth}
              height={vidHeight}
              setWidth={setVidWidth}
              setHeight={setVidHeight}
              duration={vidDuration}
              setDuration={setVidDuration}
              fps={vidFps}
              setFps={setVidFps}
              seed={vidSeed}
              setSeed={setVidSeed}
              skipAudio={vidSkipAudio}
              setSkipAudio={setVidSkipAudio}
              firstFrame={vidFirstFrame}
              setFirstFrame={setVidFirstFrame}
              lastFrame={vidLastFrame}
              setLastFrame={setVidLastFrame}
              firstFrameInput={vidFirstFrameInput}
              lastFrameInput={vidLastFrameInput}
              enhanceMutation={enhanceVidMutation}
              enhanceNegMutation={enhanceVidNegMutation}
              generateMutation={generateVidMutation}
              handleFileUpload={handleFileUpload}
              projectId={projectId}
              generatedAssets={generatedAssets}
            />
          )}

          {/* Results gallery */}
          <ResultsGallery
            assets={generatedAssets}
            scenes={scenes}
            projectId={projectId}
            getAssetThumbnailUrl={getAssetThumbnailUrl}
            onDownload={handleDownloadAsset}
            assignDropdown={assignDropdown}
            setAssignDropdown={setAssignDropdown}
          />
        </div>
      </div>
    </div>
  );
}

// ====== Image Tab ======

interface ImageTabProps {
  prompt: string;
  setPrompt: (v: string) => void;
  negPrompt: string;
  setNegPrompt: (v: string) => void;
  model: string;
  setModel: (v: string) => void;
  width: number;
  height: number;
  setWidth: (v: number) => void;
  setHeight: (v: number) => void;
  seed: string;
  setSeed: (v: string) => void;
  twoPass: boolean;
  setTwoPass: (v: boolean) => void;
  refImages: (Asset | null)[];
  setRefImages: (v: (Asset | null)[]) => void;
  refImageInputs: React.MutableRefObject<(HTMLInputElement | null)[]>;
  onRefUpload: (index: number, file: File) => void;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  enhanceMutation: any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  enhanceNegMutation: any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  generateMutation: any;
  settings?: AppSettings;
}

function ImageTabContent({
  prompt, setPrompt, negPrompt, setNegPrompt,
  model, setModel, width, height, setWidth, setHeight,
  seed, setSeed, twoPass, setTwoPass,
  refImages, setRefImages, refImageInputs, onRefUpload,
  enhanceMutation, enhanceNegMutation, generateMutation,
  settings: _settings,
}: ImageTabProps) {
  const handleResChange = (val: string) => {
    const [w, h] = val.split('x').map(Number);
    setWidth(w);
    setHeight(h);
  };

  return (
    <div className="space-y-4 pt-4">
      {/* Prompt */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="text-sm font-medium text-gray-300">Prompt</label>
          <button
            onClick={() => enhanceMutation.mutate()}
            disabled={enhanceMutation.isPending}
            className="px-3 py-1 text-xs bg-purple-600 hover:bg-purple-700 disabled:bg-gray-700 rounded flex items-center gap-1 transition-colors"
          >
            {enhanceMutation.isPending ? <Loader2 size={12} className="animate-spin" /> : <Wand2 size={12} />}
            {enhanceMutation.isPending ? 'Enhancing...' : 'Enhance'}
          </button>
        </div>
        <textarea
          value={prompt}
          onChange={e => setPrompt(e.target.value)}
          rows={4}
          className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 resize-none focus:outline-none focus:border-blue-500"
          placeholder="Describe the image you want to generate..."
        />
      </div>

      {/* Negative Prompt */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="text-sm font-medium text-gray-300">Negative Prompt</label>
          <button
            onClick={() => enhanceNegMutation.mutate()}
            disabled={enhanceNegMutation.isPending}
            className="px-3 py-1 text-xs bg-purple-600 hover:bg-purple-700 disabled:bg-gray-700 rounded flex items-center gap-1 transition-colors"
          >
            {enhanceNegMutation.isPending ? <Loader2 size={12} className="animate-spin" /> : <Wand2 size={12} />}
            {enhanceNegMutation.isPending ? 'Enhancing...' : 'Enhance'}
          </button>
        </div>
        <textarea
          value={negPrompt}
          onChange={e => setNegPrompt(e.target.value)}
          rows={2}
          className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 resize-none focus:outline-none focus:border-blue-500"
          placeholder="What to avoid in the image..."
        />
      </div>

      {/* Row: Model, Resolution, Seed */}
      <div className="grid grid-cols-3 gap-4">
        <div>
          <label className="text-sm font-medium text-gray-300 mb-1 block">Model</label>
          <select
            value={model}
            onChange={e => setModel(e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          >
            <option value="klein_t2i">Klein 9B</option>
            <option value="zimage_turbo">Z-Image Turbo</option>
          </select>
        </div>
        <div>
          <label className="text-sm font-medium text-gray-300 mb-1 block">Resolution</label>
          <select
            value={`${width}x${height}`}
            onChange={e => handleResChange(e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          >
            {IMAGE_RESOLUTIONS.map(r => (
              <option key={`${r.w}x${r.h}`} value={`${r.w}x${r.h}`}>{r.label}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-sm font-medium text-gray-300 mb-1 block">Seed (optional)</label>
          <input
            type="number"
            value={seed}
            onChange={e => setSeed(e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
            placeholder="Random"
          />
        </div>
      </div>

      {/* Reference Images */}
      {model !== 'zimage_turbo' && (
        <div>
          <label className="text-sm font-medium text-gray-300 mb-2 block">
            Reference Images (optional, up to 4 for Klein)
          </label>
          <div className="flex gap-3">
            {refImages.map((ref, i) => (
              <div key={i} className="relative w-20 h-20 border border-gray-700 rounded-lg overflow-hidden bg-gray-800 flex items-center justify-center">
                {ref ? (
                  <>
                    <img
                      src={getAssetFileUrl('', ref.id).replace('/api/projects//assets/', `/api/projects/${ref.project_id}/assets/`)}
                      alt={`Ref ${i + 1}`}
                      className="w-full h-full object-cover"
                    />
                    <button
                      onClick={() => {
                        const next = [...refImages];
                        next[i] = null;
                        setRefImages(next);
                      }}
                      className="absolute top-0 right-0 p-0.5 bg-red-600/80 rounded-bl text-white"
                    >
                      <X size={12} />
                    </button>
                  </>
                ) : (
                  <button
                    onClick={() => refImageInputs.current[i]?.click()}
                    className="w-full h-full flex flex-col items-center justify-center text-gray-500 hover:text-gray-300 transition-colors"
                  >
                    <Plus size={18} />
                    <span className="text-[10px] mt-0.5">Ref {i + 1}</span>
                  </button>
                )}
                <input
                  type="file"
                  accept="image/*"
                  className="hidden"
                  ref={el => { refImageInputs.current[i] = el; }}
                  onChange={e => {
                    const file = e.target.files?.[0];
                    if (file) onRefUpload(i, file);
                    e.target.value = '';
                  }}
                />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Two-pass + Generate */}
      <div className="flex items-center justify-between pt-2">
        <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer">
          <input
            type="checkbox"
            checked={twoPass}
            onChange={e => setTwoPass(e.target.checked)}
            className="rounded border-gray-600 bg-gray-800 text-blue-500 focus:ring-blue-500"
          />
          Two-Pass Generation
        </label>
        <button
          onClick={() => generateMutation.mutate()}
          disabled={generateMutation.isPending || !prompt}
          className="px-6 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 disabled:text-gray-500 rounded-lg text-sm font-medium transition-colors flex items-center gap-2"
        >
          {generateMutation.isPending ? <Loader2 size={16} className="animate-spin" /> : <Image size={16} />}
          {generateMutation.isPending ? 'Generating...' : 'Generate'}
        </button>
      </div>

      {generateMutation.isError && (
        <p className="text-sm text-red-400 mt-1">
          Error: {(generateMutation.error as Error)?.message || 'Generation failed'}
        </p>
      )}
    </div>
  );
}

// ====== Video Tab ======

interface VideoTabProps {
  prompt: string;
  setPrompt: (v: string) => void;
  negPrompt: string;
  setNegPrompt: (v: string) => void;
  model: string;
  setModel: (v: string) => void;
  mode: VideoMode;
  setMode: (v: VideoMode) => void;
  width: number;
  height: number;
  setWidth: (v: number) => void;
  setHeight: (v: number) => void;
  duration: string;
  setDuration: (v: string) => void;
  fps: number;
  setFps: (v: number) => void;
  seed: string;
  setSeed: (v: string) => void;
  skipAudio: boolean;
  setSkipAudio: (v: boolean) => void;
  firstFrame: Asset | null;
  setFirstFrame: (v: Asset | null) => void;
  lastFrame: Asset | null;
  setLastFrame: (v: Asset | null) => void;
  firstFrameInput: React.RefObject<HTMLInputElement | null>;
  lastFrameInput: React.RefObject<HTMLInputElement | null>;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  enhanceMutation: any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  enhanceNegMutation: any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  generateMutation: any;
  handleFileUpload: (file: File) => Promise<Asset | null>;
  projectId: string;
  generatedAssets: Asset[];
}

function VideoTabContent({
  prompt, setPrompt, negPrompt, setNegPrompt,
  model, setModel, mode, setMode,
  width, height, setWidth, setHeight,
  duration, setDuration, fps, setFps,
  seed, setSeed, skipAudio, setSkipAudio,
  firstFrame, setFirstFrame, lastFrame, setLastFrame,
  firstFrameInput, lastFrameInput,
  enhanceMutation, enhanceNegMutation, generateMutation,
  handleFileUpload, projectId, generatedAssets,
}: VideoTabProps) {
  const handleResChange = (val: string) => {
    const [w, h] = val.split('x').map(Number);
    setWidth(w);
    setHeight(h);
  };

  const [showFFPicker, setShowFFPicker] = useState(false);
  const [showLFPicker, setShowLFPicker] = useState(false);

  // Filter generated images for pickers
  const imageAssets = generatedAssets.filter(a =>
    a.asset_type === 'generated_image' || a.asset_type === 'reference'
  );

  const handleFrameUpload = async (
    _inputRef: React.RefObject<HTMLInputElement | null>,
    setFrame: (v: Asset | null) => void,
    file: File,
  ) => {
    const asset = await handleFileUpload(file);
    if (asset) setFrame(asset);
  };

  return (
    <div className="space-y-4 pt-4">
      {/* Prompt */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="text-sm font-medium text-gray-300">Prompt</label>
          <button
            onClick={() => enhanceMutation.mutate()}
            disabled={enhanceMutation.isPending}
            className="px-3 py-1 text-xs bg-purple-600 hover:bg-purple-700 disabled:bg-gray-700 rounded flex items-center gap-1 transition-colors"
          >
            {enhanceMutation.isPending ? <Loader2 size={12} className="animate-spin" /> : <Wand2 size={12} />}
            {enhanceMutation.isPending ? 'Enhancing...' : 'Enhance'}
          </button>
        </div>
        <textarea
          value={prompt}
          onChange={e => setPrompt(e.target.value)}
          rows={4}
          className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 resize-none focus:outline-none focus:border-blue-500"
          placeholder="Describe the video you want to generate..."
        />
      </div>

      {/* Negative Prompt */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="text-sm font-medium text-gray-300">Negative Prompt</label>
          <button
            onClick={() => enhanceNegMutation.mutate()}
            disabled={enhanceNegMutation.isPending}
            className="px-3 py-1 text-xs bg-purple-600 hover:bg-purple-700 disabled:bg-gray-700 rounded flex items-center gap-1 transition-colors"
          >
            {enhanceNegMutation.isPending ? <Loader2 size={12} className="animate-spin" /> : <Wand2 size={12} />}
            {enhanceNegMutation.isPending ? 'Enhancing...' : 'Enhance'}
          </button>
        </div>
        <textarea
          value={negPrompt}
          onChange={e => setNegPrompt(e.target.value)}
          rows={2}
          className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 resize-none focus:outline-none focus:border-blue-500"
          placeholder="What to avoid in the video..."
        />
      </div>

      {/* First Frame / Last Frame selectors */}
      <div className="grid grid-cols-2 gap-4">
        <FrameSelector
          label="First Frame"
          frame={firstFrame}
          setFrame={setFirstFrame}
          inputRef={firstFrameInput}
          onUpload={(f) => handleFrameUpload(firstFrameInput, setFirstFrame, f)}
          imageAssets={imageAssets}
          showPicker={showFFPicker}
          setShowPicker={setShowFFPicker}
          projectId={projectId}
        />
        {mode === 'ff_lf' && (
          <FrameSelector
            label="Last Frame"
            frame={lastFrame}
            setFrame={setLastFrame}
            inputRef={lastFrameInput}
            onUpload={(f) => handleFrameUpload(lastFrameInput, setLastFrame, f)}
            imageAssets={imageAssets}
            showPicker={showLFPicker}
            setShowPicker={setShowLFPicker}
            projectId={projectId}
          />
        )}
      </div>

      {/* Row: Model, Video Mode, Resolution */}
      <div className="grid grid-cols-3 gap-4">
        <div>
          <label className="text-sm font-medium text-gray-300 mb-1 block">Model</label>
          <select
            value={model}
            onChange={e => setModel(e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          >
            <option value="ltx_fflf">LTX 2.3</option>
            <option value="ltx_sequencer">LTX Sequencer</option>
          </select>
        </div>
        <div>
          <label className="text-sm font-medium text-gray-300 mb-1 block">Video Mode</label>
          <select
            value={mode}
            onChange={e => setMode(e.target.value as VideoMode)}
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          >
            <option value="ff_lf">FF/LF</option>
            <option value="i2v">I2V</option>
            <option value="v2v">V2V</option>
          </select>
        </div>
        <div>
          <label className="text-sm font-medium text-gray-300 mb-1 block">Resolution</label>
          <select
            value={`${width}x${height}`}
            onChange={e => handleResChange(e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          >
            {VIDEO_RESOLUTIONS.map(r => (
              <option key={`${r.w}x${r.h}`} value={`${r.w}x${r.h}`}>{r.label}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Row: Duration, FPS, Seed */}
      <div className="grid grid-cols-3 gap-4">
        <div>
          <label className="text-sm font-medium text-gray-300 mb-1 block">Duration (seconds)</label>
          <input
            type="number"
            value={duration}
            onChange={e => setDuration(e.target.value)}
            min={1}
            max={30}
            step={0.5}
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          />
        </div>
        <div>
          <label className="text-sm font-medium text-gray-300 mb-1 block">FPS</label>
          <select
            value={fps}
            onChange={e => setFps(parseInt(e.target.value, 10))}
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          >
            {FPS_OPTIONS.map(f => (
              <option key={f} value={f}>{f}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-sm font-medium text-gray-300 mb-1 block">Seed (optional)</label>
          <input
            type="number"
            value={seed}
            onChange={e => setSeed(e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
            placeholder="Random"
          />
        </div>
      </div>

      {/* Skip Audio + Generate */}
      <div className="flex items-center justify-between pt-2">
        <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer">
          <input
            type="checkbox"
            checked={skipAudio}
            onChange={e => setSkipAudio(e.target.checked)}
            className="rounded border-gray-600 bg-gray-800 text-blue-500 focus:ring-blue-500"
          />
          Skip Audio Mux
        </label>
        <button
          onClick={() => generateMutation.mutate()}
          disabled={generateMutation.isPending || !prompt}
          className="px-6 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 disabled:text-gray-500 rounded-lg text-sm font-medium transition-colors flex items-center gap-2"
        >
          {generateMutation.isPending ? <Loader2 size={16} className="animate-spin" /> : <Film size={16} />}
          {generateMutation.isPending ? 'Generating...' : 'Generate'}
        </button>
      </div>

      {generateMutation.isError && (
        <p className="text-sm text-red-400 mt-1">
          Error: {(generateMutation.error as Error)?.message || 'Generation failed'}
        </p>
      )}
    </div>
  );
}

// ====== Frame Selector ======

interface FrameSelectorProps {
  label: string;
  frame: Asset | null;
  setFrame: (v: Asset | null) => void;
  inputRef: React.RefObject<HTMLInputElement | null>;
  onUpload: (file: File) => void;
  imageAssets: Asset[];
  showPicker: boolean;
  setShowPicker: (v: boolean) => void;
  projectId: string;
}

function FrameSelector({
  label, frame, setFrame, inputRef, onUpload,
  imageAssets, showPicker, setShowPicker, projectId,
}: FrameSelectorProps) {
  return (
    <div>
      <label className="text-sm font-medium text-gray-300 mb-1 block">{label}</label>
      <div className="relative">
        {frame ? (
          <div className="flex items-center gap-2 bg-gray-800 border border-gray-700 rounded-lg p-2">
            <img
              src={getAssetFileUrl(projectId, frame.id)}
              alt={label}
              className="w-12 h-12 rounded object-cover"
            />
            <span className="text-xs text-gray-300 truncate flex-1">{frame.filename}</span>
            <button onClick={() => setFrame(null)} className="text-gray-500 hover:text-red-400">
              <X size={14} />
            </button>
          </div>
        ) : (
          <div className="flex gap-2">
            <button
              onClick={() => inputRef.current?.click()}
              className="flex-1 px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-400 hover:text-gray-200 flex items-center gap-2 transition-colors"
            >
              <Upload size={14} />
              Upload
            </button>
            {imageAssets.length > 0 && (
              <button
                onClick={() => setShowPicker(!showPicker)}
                className="px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-400 hover:text-gray-200 flex items-center gap-1 transition-colors"
              >
                <ChevronDown size={14} />
                Pick
              </button>
            )}
          </div>
        )}
        <input
          type="file"
          accept="image/*"
          className="hidden"
          ref={inputRef as React.RefObject<HTMLInputElement>}
          onChange={e => {
            const f = e.target.files?.[0];
            if (f) onUpload(f);
            e.target.value = '';
          }}
        />
        {showPicker && (
          <div className="absolute top-full left-0 mt-1 w-full max-h-40 overflow-y-auto bg-gray-800 border border-gray-700 rounded-lg shadow-xl z-10 p-2 grid grid-cols-4 gap-1">
            {imageAssets.map(a => (
              <button
                key={a.id}
                onClick={() => {
                  setFrame(a);
                  setShowPicker(false);
                }}
                className="w-full aspect-square rounded overflow-hidden border border-gray-600 hover:border-blue-500 transition-colors"
              >
                <img
                  src={getAssetFileUrl(projectId, a.id)}
                  alt={a.filename}
                  className="w-full h-full object-cover"
                />
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ====== Results Gallery ======

interface ResultsGalleryProps {
  assets: Asset[];
  scenes: Scene[];
  projectId: string;
  getAssetThumbnailUrl: (a: Asset) => string;
  onDownload: (a: Asset) => void;
  assignDropdown: string | null;
  setAssignDropdown: (v: string | null) => void;
}

function ResultsGallery({
  assets, scenes, projectId, getAssetThumbnailUrl, onDownload,
  assignDropdown, setAssignDropdown,
}: ResultsGalleryProps) {
  if (assets.length === 0) return null;

  return (
    <div className="mt-6 border-t border-gray-800 pt-4">
      <h3 className="text-sm font-medium text-gray-300 mb-3">Generated Assets ({assets.length})</h3>
      <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-5 gap-3">
        {assets.slice().reverse().map(asset => (
          <div key={asset.id} className="bg-gray-800 border border-gray-700 rounded-lg overflow-hidden group">
            <div className="aspect-video relative">
              {asset.asset_type === 'generated_video' ? (
                <video
                  src={getAssetThumbnailUrl(asset)}
                  className="w-full h-full object-cover"
                  muted
                  preload="metadata"
                />
              ) : (
                <img
                  src={getAssetThumbnailUrl(asset)}
                  alt={asset.filename}
                  className="w-full h-full object-cover"
                />
              )}
              {/* Overlay buttons */}
              <div className="absolute inset-0 bg-black/50 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center gap-2">
                <button
                  onClick={() => onDownload(asset)}
                  className="p-1.5 bg-gray-700/80 hover:bg-gray-600 rounded text-gray-200"
                  title="Download"
                >
                  <Download size={14} />
                </button>
                {scenes.length > 0 && (
                  <div className="relative">
                    <button
                      onClick={() => setAssignDropdown(assignDropdown === asset.id ? null : asset.id)}
                      className="p-1.5 bg-blue-600/80 hover:bg-blue-500 rounded text-white"
                      title="Assign to Scene"
                    >
                      <Plus size={14} />
                    </button>
                    {assignDropdown === asset.id && (
                      <AssignDropdown
                        asset={asset}
                        scenes={scenes}
                        projectId={projectId}
                        onClose={() => setAssignDropdown(null)}
                      />
                    )}
                  </div>
                )}
              </div>
            </div>
            <div className="p-1.5">
              <p className="text-[10px] text-gray-400 truncate" title={asset.meta?.prompt || asset.filename}>
                {asset.meta?.prompt || asset.filename}
              </p>
              <p className="text-[10px] text-gray-500 flex items-center gap-1">
                <Clock size={10} />
                {new Date(asset.created_at).toLocaleTimeString()}
              </p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ====== Assign Dropdown ======

interface AssignDropdownProps {
  asset: Asset;
  scenes: Scene[];
  projectId: string;
  onClose: () => void;
}

function AssignDropdown({ asset, scenes, projectId, onClose }: AssignDropdownProps) {
  const targets = asset.asset_type === 'generated_video'
    ? [{ value: 'video', label: 'Video' }]
    : [
        { value: 'first_frame', label: 'First Frame' },
        { value: 'last_frame', label: 'Last Frame' },
      ];

  const handleAssign = async (sceneId: string, target: string) => {
    try {
      await assignAssetToScene(projectId, sceneId, { asset_id: asset.id, target });
    } catch {
      // silent fail
    }
    onClose();
  };

  return (
    <div className="absolute bottom-full right-0 mb-1 w-48 max-h-48 overflow-y-auto bg-gray-800 border border-gray-700 rounded-lg shadow-xl z-20">
      <div className="p-1.5 text-[10px] text-gray-500 border-b border-gray-700">Assign to Scene</div>
      {scenes.map(scene => (
        <div key={scene.id}>
          {targets.map(t => (
            <button
              key={`${scene.id}-${t.value}`}
              onClick={() => handleAssign(scene.id, t.value)}
              className="w-full px-3 py-1.5 text-xs text-gray-300 hover:bg-gray-700 text-left truncate"
            >
              {scene.name} - {t.label}
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}
