import { useState, useEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Plus, Trash2, Save, Zap, User, ImageIcon, Monitor, Pencil, Music, Sparkles, Users, X } from 'lucide-react';
import { getConcept, saveConcept, uploadAsset, getLyrics, baseOnLyrics, autogenerateCharacters } from '@/api/client';
import { handleImgError } from '@/utils/brokenImage';
import CharacterCreatorModal from './CharacterCreatorModal';
import { GlobalCharacterLibraryModal } from './GlobalCharacterLibraryModal';
import { useAppStore } from '@/store';

interface Character {
  name: string;
  description: string;
  image_path: string | null;
  // Persisted across save/close — see backend CharacterModel.
  // Lets the Character Edit modal hydrate the prompt + reference
  // images on reopen so users can tweak and regenerate without
  // re-typing everything.
  last_prompt?: string;
  reference_images?: Array<{ asset_id: string; image_path: string; description: string }>;
}

// ── Resolution presets compatible with FLUX.2 Klein 9B & LTX 2.3 ────
interface ResolutionPreset {
  label: string;
  width: number;
  height: number;
  aspect: string;
}

const RESOLUTION_PRESETS: ResolutionPreset[] = [
  // Landscape
  { label: '1536 × 864',  width: 1536, height: 864,  aspect: '16:9' },
  { label: '1344 × 768',  width: 1344, height: 768,  aspect: '16:9' },
  { label: '1280 × 720',  width: 1280, height: 720,  aspect: '16:9' },
  { label: '1152 × 896',  width: 1152, height: 896,  aspect: '9:7' },
  { label: '1216 × 832',  width: 1216, height: 832,  aspect: '3:2' },
  { label: '1344 × 896',  width: 1344, height: 896,  aspect: '3:2' },
  { label: '1024 × 1024', width: 1024, height: 1024, aspect: '1:1' },
  // Portrait
  { label: '864 × 1536',  width: 864,  height: 1536, aspect: '9:16' },
  { label: '768 × 1344',  width: 768,  height: 1344, aspect: '9:16' },
  { label: '720 × 1280',  width: 720,  height: 1280, aspect: '9:16' },
  { label: '896 × 1152',  width: 896,  height: 1152, aspect: '7:9' },
  { label: '832 × 1216',  width: 832,  height: 1216, aspect: '2:3' },
  { label: '896 × 1344',  width: 896,  height: 1344, aspect: '2:3' },
];

/** Find matching preset key or 'custom'. */
function findPresetKey(w: number, h: number): string {
  const match = RESOLUTION_PRESETS.find((p) => p.width === w && p.height === h);
  return match ? `${match.width}x${match.height}` : 'custom';
}

interface ConceptPanelProps {
  projectId: string;
}

export default function ConceptPanel({ projectId }: ConceptPanelProps) {
  const [songTitle, setSongTitle] = useState('');
  const [conceptText, setConceptText] = useState('');
  const [styleText, setStyleText] = useState('');
  const [characters, setCharacters] = useState<Character[]>([]);
  const [resWidth, setResWidth] = useState(1536);
  const [resHeight, setResHeight] = useState(864);
  const [resPresetKey, setResPresetKey] = useState('1536x864');
  // Per-job-type overrides (1.8.x). 0 means "use the unified default
  // above" so the backend falls through to resolution_width / _height.
  // Allows users to render images at higher resolution for cleaner
  // Klein composites in Pass 2 while keeping LTX video at a sensible
  // size, since most workflows upscale video after generation anyway.
  const [imgResWidth, setImgResWidth] = useState(0);
  const [imgResHeight, setImgResHeight] = useState(0);
  const [vidResWidth, setVidResWidth] = useState(0);
  const [vidResHeight, setVidResHeight] = useState(0);
  const [projectFps, setProjectFps] = useState(24);
  const [imageDirection, setImageDirection] = useState('');
  const [customImageDirection, setCustomImageDirection] = useState('');
  const [globalSeedEnabled, setGlobalSeedEnabled] = useState(false);
  const [globalSeed, setGlobalSeed] = useState(0);
  const [useTransitionLora, setUseTransitionLora] = useState(false);
  const [transitionLoraStrength, setTransitionLoraStrength] = useState(1.0);
  const [randomKenBurns, setRandomKenBurns] = useState(false);
  const [kenBurnsAllowedEffects, setKenBurnsAllowedEffects] = useState<string[]>([]);
  const [globalColorOverride, setGlobalColorOverride] = useState('');
  const [customColorPalette, setCustomColorPalette] = useState('');
  // FFmpeg post-process color filter applied to every generated image
  // AFTER the model produces it.  Independent of globalColorOverride
  // (which only influences the LLM prompt).  Values: "" / "bw" /
  // "grayscale" / "sepia".  Per-scene image_color_filter overrides.
  const [globalImageColorFilter, setGlobalImageColorFilter] = useState('');
  // Model-generated audio (LTX 2.3 AV-native).  When ON, every I2V video
  // render in this project uses the AV-native workflow — no per-scene
  // opt-in required.  Mixer slider controls level for the resulting
  // model-audio channel layered with narration + backing tracks.
  const [enableModelAudio, setEnableModelAudio] = useState(false);
  const [modelAudioVolume, setModelAudioVolume] = useState(1.0);
  const [dirty, setDirty] = useState(false);
  const [creatorOpen, setCreatorOpen] = useState<{ index: number; character: Character } | null>(null);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [lightboxImage, setLightboxImage] = useState<{ src: string; name: string } | null>(null);
  const queryClient = useQueryClient();

  const currentProject = useAppStore((s) => s.currentProject);
  const isNarration = currentProject?.mode === 'narration_images' || currentProject?.mode === 'narration_video';
  const isNarrationImages = currentProject?.mode === 'narration_images';

  // Fetch concept data
  const { data: conceptData } = useQuery({
    queryKey: ['concept', projectId],
    queryFn: async () => {
      const response = await getConcept(projectId);
      return response.data;
    },
    enabled: !!projectId,
    staleTime: 30_000,
  });

  // Fetch lyrics for Whisper display and Base on Lyrics
  // Always refetch on mount to pick up Whisper results from audio processing
  const { data: lyricsData } = useQuery({
    queryKey: ['lyrics', projectId],
    queryFn: async () => {
      const response = await getLyrics(projectId);
      return response.data;
    },
    enabled: !!projectId,
    staleTime: 10_000,  // Short stale time — lyrics change after audio processing
    refetchOnMount: 'always',  // Always refetch when panel mounts (user may have just processed audio)
  });

  // Whisper detected lyrics = the full_text (from Whisper transcription)
  // User-provided lyrics = initial_text (entered on Audio tab)
  const whisperLyrics = lyricsData?.text || '';
  const userProvidedLyrics = lyricsData?.initial_text || '';

  // Base on Lyrics mutation
  const baseOnLyricsMutation = useMutation({
    mutationFn: async () => {
      const response = await baseOnLyrics(projectId, {
        song_title: songTitle,
        concept_text: conceptText,
        style_text: styleText,
      });
      return response.data;
    },
    onSuccess: async (data) => {
      const newTitle = (data.song_title && !songTitle.trim()) ? data.song_title : songTitle;
      const newConcept = (data.concept_text && !conceptText.trim()) ? data.concept_text : conceptText;
      const newStyle = (data.style_text && !styleText.trim()) ? data.style_text : styleText;
      setSongTitle(newTitle);
      setConceptText(newConcept);
      setStyleText(newStyle);
      // Auto-save immediately since this used LLM tokens
      await saveConcept(projectId, {
        song_title: newTitle,
        concept_text: newConcept,
        style_text: newStyle,
        characters,
        resolution_width: resWidth,
        resolution_height: resHeight,
        image_resolution_width: imgResWidth,
        image_resolution_height: imgResHeight,
        video_resolution_width: vidResWidth,
        video_resolution_height: vidResHeight,
        project_fps: projectFps,
        image_direction: imageDirection,
        custom_image_direction: customImageDirection,
        global_seed_enabled: globalSeedEnabled,
        global_seed: globalSeed,
        use_transition_lora: useTransitionLora,
        transition_lora_strength: transitionLoraStrength,
        random_ken_burns: randomKenBurns,
        ken_burns_allowed_effects: kenBurnsAllowedEffects,
        global_color_override: globalColorOverride,
        custom_color_palette: customColorPalette,
        global_image_color_filter: globalImageColorFilter,
        enable_model_audio: enableModelAudio,
        model_audio_volume: modelAudioVolume,
      } as any);
      setDirty(false);
      queryClient.invalidateQueries({ queryKey: ['concept', projectId] });
      // ALSO invalidate the project query so AppLayout.currentProject.settings
      // picks up the freshly saved global_color_override (and any other
      // settings the Scene Editor / dispatcher read from project.settings).
      // Without this, saves persist server-side but the SceneEditor's
      // "use project Default Color Palette" inheritance message keeps
      // showing the stale "(no project default set)" until full page reload.
      queryClient.invalidateQueries({ queryKey: ['project', projectId] });
    },
  });

  // Autogenerate Characters mutation
  const autoCharsMutation = useMutation({
    mutationFn: async () => {
      const response = await autogenerateCharacters(projectId);
      return response.data;
    },
    onSuccess: () => {
      // Refresh concept data to pick up the new characters
      queryClient.invalidateQueries({ queryKey: ['concept', projectId] });
      // ALSO invalidate the project query so AppLayout.currentProject.settings
      // picks up the freshly saved global_color_override (and any other
      // settings the Scene Editor / dispatcher read from project.settings).
      // Without this, saves persist server-side but the SceneEditor's
      // "use project Default Color Palette" inheritance message keeps
      // showing the stale "(no project default set)" until full page reload.
      queryClient.invalidateQueries({ queryKey: ['project', projectId] });
    },
  });

  // Sync from server
  useEffect(() => {
    // Re-hydrate from server ONLY when the user has no unsaved edits.
    // Otherwise a background refetch (triggered by, e.g., importing a
    // library character or any other invalidation of ['concept', projectId])
    // would silently overwrite the user's in-progress text and unsaved
    // toggle changes.  When dirty===true we leave local state alone; the
    // next save will round-trip with the user's current values intact.
    if (conceptData && !dirty) {
      setSongTitle(conceptData.song_title || '');
      setConceptText(conceptData.concept_text || '');
      setStyleText(conceptData.style_text || '');
      setCharacters(conceptData.characters || []);
      const w = conceptData.resolution_width || 1536;
      const h = conceptData.resolution_height || 864;
      setResWidth(w);
      setResHeight(h);
      setResPresetKey(findPresetKey(w, h));
      setImgResWidth(conceptData.image_resolution_width || 0);
      setImgResHeight(conceptData.image_resolution_height || 0);
      setVidResWidth(conceptData.video_resolution_width || 0);
      setVidResHeight(conceptData.video_resolution_height || 0);
      setProjectFps(conceptData.project_fps || 24);
      setImageDirection(conceptData.image_direction || '');
      setCustomImageDirection(conceptData.custom_image_direction || '');
      setGlobalSeedEnabled(conceptData.global_seed_enabled || false);
      setGlobalSeed(conceptData.global_seed || 0);
      setUseTransitionLora(conceptData.use_transition_lora || false);
      setTransitionLoraStrength(conceptData.transition_lora_strength ?? 1.0);
      setRandomKenBurns(conceptData.random_ken_burns || false);
      setKenBurnsAllowedEffects(conceptData.ken_burns_allowed_effects || []);
      setGlobalColorOverride(conceptData.global_color_override || '');
      setGlobalImageColorFilter(((conceptData as any).global_image_color_filter || '') as string);
      setEnableModelAudio(Boolean((conceptData as any).enable_model_audio));
      setModelAudioVolume(typeof (conceptData as any).model_audio_volume === 'number' ? (conceptData as any).model_audio_volume : 1.0);
      setCustomColorPalette(conceptData.custom_color_palette || '');
      setDirty(false);
    }
  }, [conceptData, dirty]);

  // Save mutation
  const saveMutation = useMutation({
    mutationFn: async () => {
      await saveConcept(projectId, {
        song_title: songTitle,
        concept_text: conceptText,
        style_text: styleText,
        characters,
        resolution_width: resWidth,
        resolution_height: resHeight,
        image_resolution_width: imgResWidth,
        image_resolution_height: imgResHeight,
        video_resolution_width: vidResWidth,
        video_resolution_height: vidResHeight,
        project_fps: projectFps,
        image_direction: imageDirection,
        custom_image_direction: customImageDirection,
        global_seed_enabled: globalSeedEnabled,
        global_seed: globalSeed,
        use_transition_lora: useTransitionLora,
        transition_lora_strength: transitionLoraStrength,
        random_ken_burns: randomKenBurns,
        ken_burns_allowed_effects: kenBurnsAllowedEffects,
        global_color_override: globalColorOverride,
        custom_color_palette: customColorPalette,
        global_image_color_filter: globalImageColorFilter,
        enable_model_audio: enableModelAudio,
        model_audio_volume: modelAudioVolume,
      } as any);
    },
    onSuccess: () => {
      setDirty(false);
      queryClient.invalidateQueries({ queryKey: ['concept', projectId] });
      // ALSO invalidate the project query so AppLayout.currentProject.settings
      // picks up the freshly saved global_color_override (and any other
      // settings the Scene Editor / dispatcher read from project.settings).
      // Without this, saves persist server-side but the SceneEditor's
      // "use project Default Color Palette" inheritance message keeps
      // showing the stale "(no project default set)" until full page reload.
      queryClient.invalidateQueries({ queryKey: ['project', projectId] });
    },
  });

  const markDirty = useCallback(() => setDirty(true), []);

  const addCharacter = () => {
    setCharacters([...characters, { name: '', description: '', image_path: null }]);
    markDirty();
  };

  const removeCharacter = (index: number) => {
    const charName = characters[index]?.name || 'this character';
    if (!window.confirm(`Remove ${charName}? This will delete the character and all its data. This is permanent.`)) return;
    setCharacters(characters.filter((_, i) => i !== index));
    markDirty();
  };

  const updateCharacter = (index: number, field: keyof Character, value: string | null) => {
    const updated = [...characters];
    updated[index] = { ...updated[index], [field]: value };
    setCharacters(updated);
    markDirty();
  };

  const handleCreatorSave = useCallback((index: number, updatedChar: Character) => {
    if (index < 0) {
      // Creating new character — add to list
      const newChars = [...characters, updatedChar];
      setCharacters(newChars);
      // Auto-save the concept so the backend has the new character
      saveConcept(projectId, {
        song_title: songTitle,
        concept_text: conceptText,
        style_text: styleText,
        characters: newChars,
        resolution_width: resWidth,
        resolution_height: resHeight,
        image_resolution_width: imgResWidth,
        image_resolution_height: imgResHeight,
        video_resolution_width: vidResWidth,
        video_resolution_height: vidResHeight,
        project_fps: projectFps,
        image_direction: imageDirection,
        custom_image_direction: customImageDirection,
        global_seed_enabled: globalSeedEnabled,
        global_seed: globalSeed,
        use_transition_lora: useTransitionLora,
        transition_lora_strength: transitionLoraStrength,
        random_ken_burns: randomKenBurns,
        ken_burns_allowed_effects: kenBurnsAllowedEffects,
        global_color_override: globalColorOverride,
        custom_color_palette: customColorPalette,
        global_image_color_filter: globalImageColorFilter,
        enable_model_audio: enableModelAudio,
        model_audio_volume: modelAudioVolume,
      }).then(() => {
        queryClient.invalidateQueries({ queryKey: ['concept', projectId] });
        // Refresh project query too so currentProject.settings picks up
        // the saved global_color_override (Scene Editor inherits from it).
        queryClient.invalidateQueries({ queryKey: ['project', projectId] });
      });
    } else {
      // Editing existing character
      const updated = [...characters];
      updated[index] = updatedChar;
      setCharacters(updated);
      saveConcept(projectId, {
        song_title: songTitle,
        concept_text: conceptText,
        style_text: styleText,
        characters: updated,
        resolution_width: resWidth,
        resolution_height: resHeight,
        image_resolution_width: imgResWidth,
        image_resolution_height: imgResHeight,
        video_resolution_width: vidResWidth,
        video_resolution_height: vidResHeight,
        project_fps: projectFps,
        image_direction: imageDirection,
        custom_image_direction: customImageDirection,
        global_seed_enabled: globalSeedEnabled,
        global_seed: globalSeed,
        use_transition_lora: useTransitionLora,
        transition_lora_strength: transitionLoraStrength,
        random_ken_burns: randomKenBurns,
        ken_burns_allowed_effects: kenBurnsAllowedEffects,
        global_color_override: globalColorOverride,
        custom_color_palette: customColorPalette,
        global_image_color_filter: globalImageColorFilter,
        enable_model_audio: enableModelAudio,
        model_audio_volume: modelAudioVolume,
      }).then(() => {
        queryClient.invalidateQueries({ queryKey: ['concept', projectId] });
        // Refresh project query too so currentProject.settings picks up
        // the saved global_color_override (Scene Editor inherits from it).
        queryClient.invalidateQueries({ queryKey: ['project', projectId] });
      });
    }
  }, [characters, conceptText, styleText, resWidth, resHeight, projectFps, imageDirection, customImageDirection, globalSeedEnabled, globalSeed, useTransitionLora, transitionLoraStrength, randomKenBurns, kenBurnsAllowedEffects, globalColorOverride, customColorPalette, globalImageColorFilter, projectId, queryClient]);

  const handleImageUpload = async (index: number, file: File) => {
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('asset_type', 'character');
      const response = await uploadAsset(projectId, formData);
      const asset = response.data;
      updateCharacter(index, 'image_path', asset.rel_path);
    } catch (err) {
      console.error('Failed to upload character image:', err);
    }
  };

  return (
    <div className="h-full flex flex-col overflow-hidden">
      <div className="p-3 border-b border-gray-800 flex items-center justify-between flex-shrink-0">
        <span className="text-xs font-medium text-gray-300">{isNarration ? 'Narration Concept' : 'Video Concept'}</span>
        <button
          onClick={() => saveMutation.mutate()}
          disabled={!dirty || saveMutation.isPending}
          className={`flex items-center gap-1 px-2.5 py-1 rounded text-xs font-medium transition-colors ${
            dirty
              ? 'bg-green-600 hover:bg-green-700 text-white'
              : 'bg-gray-800 text-gray-500 cursor-not-allowed'
          }`}
        >
          <Save size={12} />
          {saveMutation.isPending ? 'Saving...' : 'Save'}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        {/* Song Title */}
        <div>
          <label className="flex items-center gap-1.5 text-xs font-medium text-gray-400 mb-1">
            <Music size={12} />
            {isNarration ? 'Project Title' : 'Song Title'}
            <span className="text-gray-600 font-normal">(optional)</span>
          </label>
          <input
            type="text"
            value={songTitle}
            onChange={(e) => { setSongTitle(e.target.value); markDirty(); }}
            placeholder={isNarration ? 'Enter narration project title...' : 'Enter song or video title...'}
            className="w-full px-2.5 py-2 bg-gray-800 border border-gray-700 rounded text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500"
          />
        </div>

        {/* Base on Lyrics Button */}
        <div>
          <button
            onClick={() => baseOnLyricsMutation.mutate()}
            disabled={baseOnLyricsMutation.isPending || (!whisperLyrics && !userProvidedLyrics)}
            className={`w-full flex items-center justify-center gap-2 px-3 py-2 rounded text-sm font-medium transition-colors ${
              (!whisperLyrics && !userProvidedLyrics)
                ? 'bg-gray-800 text-gray-600 cursor-not-allowed'
                : 'bg-purple-600/80 hover:bg-purple-600 text-white'
            }`}
            title={
              (!whisperLyrics && !userProvidedLyrics)
                ? `No ${isNarration ? 'script' : 'lyrics'} available — add ${isNarration ? 'your script' : 'lyrics'} on the Audio tab or process audio first`
                : `Use ${isNarration ? 'script' : 'lyrics'} to generate missing concept fields via LLM`
            }
          >
            <Sparkles size={14} />
            {baseOnLyricsMutation.isPending ? 'Generating...' : isNarration ? 'Base on Script' : 'Base on Lyrics'}
          </button>
          {baseOnLyricsMutation.isError && (
            <p className="text-[10px] text-red-400 mt-1">
              {(baseOnLyricsMutation.error as any)?.response?.data?.detail || `Failed to generate from ${isNarration ? 'script' : 'lyrics'}`}
            </p>
          )}
          <p className="text-[10px] text-gray-600 mt-1">
            {(!whisperLyrics && !userProvidedLyrics)
              ? `No ${isNarration ? 'script' : 'lyrics'} detected yet. ${isNarration ? 'Add your script on the Audio tab first.' : 'Process audio on the Audio tab first.'}`
              : `Uses ${userProvidedLyrics ? 'user-provided' : 'Whisper-detected'} ${isNarration ? 'script' : 'lyrics'} to fill empty fields above/below.`
            }
          </p>
        </div>

        {/* Overall Concept */}
        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1">{isNarration ? 'Narration Concept' : 'Overall Concept'}</label>
          <textarea
            value={conceptText}
            onChange={(e) => { setConceptText(e.target.value); markDirty(); }}
            placeholder={isNarration ? 'Describe the visual theme and mood for this narration...' : 'Describe the overall concept for this video...'}
            className="w-full px-2.5 py-2 bg-gray-800 border border-gray-700 rounded text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 h-20 resize-none"
          />
        </div>

        {/* Overall Style */}
        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1">Visual Style</label>
          <textarea
            value={styleText}
            onChange={(e) => { setStyleText(e.target.value); markDirty(); }}
            placeholder="Describe the visual style — colors, mood, aesthetic..."
            className="w-full px-2.5 py-2 bg-gray-800 border border-gray-700 rounded text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 h-16 resize-none"
          />
        </div>

        {/* Image Direction */}
        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1">Image Direction</label>
          <select
            value={imageDirection}
            onChange={(e) => { setImageDirection(e.target.value); if (e.target.value !== 'custom') setCustomImageDirection(''); markDirty(); }}
            className="w-full px-2.5 py-2 bg-gray-800 border border-gray-700 rounded text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          >
            <option value="">None</option>
            <option value="photorealistic">Photorealistic</option>
            <option value="cinematic">Cinematic</option>
            <option value="cartoon">Cartoon</option>
            <option value="anime">Anime</option>
            <option value="sketch">Sketch</option>
            <option value="watercolor">Watercolor</option>
            <option value="oil_painting">Oil Painting</option>
            <option value="3d_render">3D Render</option>
            <option value="comic_book">Comic Book</option>
            <option value="pixel_art">Pixel Art</option>
            <option value="abstract">Abstract</option>
            <option value="surreal">Surreal</option>
            <option value="custom">Custom</option>
          </select>
          {imageDirection === 'custom' && (
            <input
              type="text"
              value={customImageDirection}
              onChange={(e) => { setCustomImageDirection(e.target.value); markDirty(); }}
              placeholder="Enter your custom image direction..."
              className="w-full mt-1.5 px-2.5 py-2 bg-gray-800 border border-gray-700 rounded text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500"
            />
          )}
        </div>

        {/* Global Color Override */}
        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1">Default Color Palette</label>
          <select
            value={globalColorOverride}
            onChange={(e) => { setGlobalColorOverride(e.target.value); if (e.target.value !== 'custom') setCustomColorPalette(''); markDirty(); }}
            className="w-full px-2.5 py-2 bg-gray-800 border border-gray-700 rounded text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          >
            <option value="">Full Color (Default)</option>
            <option value="black_and_white">Black & White / Noir</option>
            <option value="high_contrast_bw">High Contrast B&W</option>
            <option value="sepia">Sepia Tone</option>
            <option value="monochrome_blue">Monochrome Blue</option>
            <option value="monochrome_red">Monochrome Red</option>
            <option value="desaturated">Desaturated / Muted</option>
            <option value="vintage_film">Vintage Film</option>
            <option value="neon_cyberpunk">Neon Cyberpunk</option>
            <option value="custom">Custom Palette...</option>
          </select>
          {globalColorOverride === 'custom' && (
            <textarea
              value={customColorPalette}
              onChange={(e) => { setCustomColorPalette(e.target.value); markDirty(); }}
              placeholder="Describe your color palette (e.g., 'warm golden tones, amber highlights, deep burgundy shadows')..."
              rows={2}
              className="w-full mt-1.5 px-2.5 py-2 bg-gray-800 border border-gray-700 rounded text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 resize-none"
            />
          )}
          {globalColorOverride && globalColorOverride !== 'full_color' && (
            <p className="text-[10px] text-amber-400/70 mt-1">Default for all scenes — override per-scene in Image tab</p>
          )}
        </div>

        {/* FFmpeg Post-Process Color Filter — applies a deterministic
            pixel transform to every generated image AFTER the model
            renders it.  Independent of Default Color Palette (which
            only steers the LLM prompt).  Off by default. */}
        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1">
            Force Color Filter on Generated Images (FFmpeg)
          </label>
          <select
            value={globalImageColorFilter || ''}
            onChange={(e) => { setGlobalImageColorFilter(e.target.value); markDirty(); }}
            className="w-full px-2.5 py-2 bg-gray-800 border border-gray-700 rounded text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          >
            <option value="">Off — keep model's color output</option>
            <option value="bw">Black &amp; White (high contrast)</option>
            <option value="grayscale">Grayscale (desaturated)</option>
            <option value="sepia">Sepia Tone</option>
          </select>
          <p className="text-[10px] text-gray-500 mt-1">
            Runs FFmpeg over every newly-generated image. Per-scene override available on each Image tab.
          </p>
        </div>

        {/* Model-Generated Audio (LTX 2.3 AV-native) — master toggle.
            When ON, every I2V video render in the project uses AV-native
            regardless of any per-scene toggle.  When OFF, scenes can opt in
            individually via the Video tab checkbox. */}
        <div className="bg-gray-800/40 border border-gray-700/60 rounded p-3">
          <div className="flex items-center gap-2 mb-1">
            <input
              type="checkbox"
              id="enable_model_audio"
              checked={enableModelAudio}
              onChange={(e) => { setEnableModelAudio(e.target.checked); markDirty(); }}
              className="rounded border-gray-700 text-purple-500 focus:ring-purple-500"
            />
            <label htmlFor="enable_model_audio" className="text-xs font-medium text-gray-200">
              Enable Model-Generated Audio (LTX 2.3 AV-native)
            </label>
          </div>
          <p className="text-[10px] text-gray-500 mb-1">
            When on, <strong className="text-purple-200">every</strong> I2V video render in this project will use the AV-native LTX 2.3 workflow —
            no per-scene opt-in required. The model generates its own speech / SFX / ambient and the result
            is layered with your narration and backing tracks at the volume below.
          </p>
          <p className="text-[10px] text-gray-500 mb-2">
            (You can still flip the per-scene "Let model generate own audio" checkbox on the Video tab to
            opt a single scene in when this master toggle is off.)
          </p>
          {enableModelAudio && (
            <div>
              <label className="block text-[10px] text-gray-400 mb-1">
                Model Audio Mixer Volume: <span className="text-purple-300">{modelAudioVolume.toFixed(2)}×</span>
              </label>
              <input
                type="range"
                min="0"
                max="2"
                step="0.05"
                value={modelAudioVolume}
                onChange={(e) => { setModelAudioVolume(parseFloat(e.target.value)); markDirty(); }}
                className="w-full accent-purple-500"
              />
              <p className="text-[10px] text-gray-600 mt-1">
                Applied to the model-audio channel only — narration / backing tracks unchanged.
                Set to 0 to mute the model audio without disabling generation.
              </p>
            </div>
          )}
        </div>


        {/* Global Seed Control */}
        <div>
          <div className="flex items-center gap-2 mb-2">
            <input
              type="checkbox"
              id="global_seed_enabled"
              checked={globalSeedEnabled}
              onChange={(e) => { setGlobalSeedEnabled(e.target.checked); markDirty(); }}
              className="rounded border-gray-700 text-blue-500 focus:ring-blue-500"
            />
            <label htmlFor="global_seed_enabled" className="text-xs font-medium text-gray-400">
              Use Global Seed
            </label>
          </div>
          {globalSeedEnabled && (
            <div>
              <label className="block text-[10px] text-gray-500 mb-1">Seed Value</label>
              <input
                type="number"
                value={globalSeed}
                onChange={(e) => { setGlobalSeed(parseInt(e.target.value) || 0); markDirty(); }}
                min="0"
                max={2 ** 32 - 1}
                className="w-full px-2.5 py-2 bg-gray-800 border border-gray-700 rounded text-sm text-gray-100 focus:outline-none focus:border-blue-500"
                placeholder="0 for random"
              />
              <p className="text-[10px] text-gray-600 mt-1">
                Applied to all image and video generation unless overridden per-scene.
              </p>
            </div>
          )}
        </div>

        {/* Desired Resolution */}
        <div>
          <label className="flex items-center gap-1.5 text-xs font-medium text-gray-400 mb-1">
            <Monitor size={12} />
            Desired Resolution
          </label>
          <select
            value={resPresetKey}
            onChange={(e) => {
              const key = e.target.value;
              setResPresetKey(key);
              if (key !== 'custom') {
                const preset = RESOLUTION_PRESETS.find((p) => `${p.width}x${p.height}` === key);
                if (preset) {
                  setResWidth(preset.width);
                  setResHeight(preset.height);
                }
              }
              markDirty();
            }}
            className="w-full px-2.5 py-2 bg-gray-800 border border-gray-700 rounded text-sm text-gray-100 focus:outline-none focus:border-blue-500 mb-2"
          >
            <optgroup label="Landscape">
              {RESOLUTION_PRESETS.filter((p) => p.width > p.height).map((p) => (
                <option key={`${p.width}x${p.height}`} value={`${p.width}x${p.height}`}>
                  {p.label} — {p.aspect}
                </option>
              ))}
            </optgroup>
            <optgroup label="Square">
              {RESOLUTION_PRESETS.filter((p) => p.width === p.height).map((p) => (
                <option key={`${p.width}x${p.height}`} value={`${p.width}x${p.height}`}>
                  {p.label} — {p.aspect}
                </option>
              ))}
            </optgroup>
            <optgroup label="Portrait">
              {RESOLUTION_PRESETS.filter((p) => p.width < p.height).map((p) => (
                <option key={`${p.width}x${p.height}`} value={`${p.width}x${p.height}`}>
                  {p.label} — {p.aspect}
                </option>
              ))}
            </optgroup>
            <optgroup label="Other">
              <option value="custom">Custom</option>
            </optgroup>
          </select>

          {resPresetKey === 'custom' && (
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="block text-[10px] text-gray-500 mb-0.5">Width</label>
                <input
                  type="number"
                  value={resWidth}
                  onChange={(e) => { setResWidth(parseInt(e.target.value) || 512); markDirty(); }}
                  min="256"
                  max="4096"
                  step="64"
                  className="w-full px-2 py-1.5 bg-gray-900 border border-gray-700 rounded text-xs text-gray-100 focus:outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-[10px] text-gray-500 mb-0.5">Height</label>
                <input
                  type="number"
                  value={resHeight}
                  onChange={(e) => { setResHeight(parseInt(e.target.value) || 512); markDirty(); }}
                  min="256"
                  max="4096"
                  step="64"
                  className="w-full px-2 py-1.5 bg-gray-900 border border-gray-700 rounded text-xs text-gray-100 focus:outline-none focus:border-blue-500"
                />
              </div>
            </div>
          )}

          <div className="text-[10px] text-gray-600 mt-1">
            Used as default for all image &amp; video generation. Override per-type below or per-scene in editor tabs.
          </div>

          {/* Per-type overrides — separates Klein/Z-Image image jobs from
              LTX video jobs since they typically benefit from different
              sizes (larger images for cleaner character composites,
              smaller videos that are upscaled afterward). */}
          <div className="mt-3 grid grid-cols-2 gap-3">
            <div className="border border-gray-800 rounded p-2">
              <div className="text-[11px] font-semibold text-purple-300 mb-1">
                Image Generation Size
              </div>
              <div className="text-[9px] text-gray-500 mb-1.5 leading-tight">
                Klein / Z-Image. Leave at 0 to use the unified resolution above.
              </div>
              <div className="grid grid-cols-2 gap-1.5">
                <div>
                  <label className="block text-[9px] text-gray-500 mb-0.5">Width</label>
                  <input
                    type="number"
                    value={imgResWidth}
                    onChange={(e) => { setImgResWidth(parseInt(e.target.value) || 0); markDirty(); }}
                    min="0" max="4096" step="64"
                    placeholder={String(resWidth)}
                    className="w-full px-2 py-1 bg-gray-900 border border-gray-700 rounded text-xs text-gray-100 focus:outline-none focus:border-purple-500"
                  />
                </div>
                <div>
                  <label className="block text-[9px] text-gray-500 mb-0.5">Height</label>
                  <input
                    type="number"
                    value={imgResHeight}
                    onChange={(e) => { setImgResHeight(parseInt(e.target.value) || 0); markDirty(); }}
                    min="0" max="4096" step="64"
                    placeholder={String(resHeight)}
                    className="w-full px-2 py-1 bg-gray-900 border border-gray-700 rounded text-xs text-gray-100 focus:outline-none focus:border-purple-500"
                  />
                </div>
              </div>
            </div>
            <div className="border border-gray-800 rounded p-2">
              <div className="text-[11px] font-semibold text-blue-300 mb-1">
                Video Generation Size
              </div>
              <div className="text-[9px] text-gray-500 mb-1.5 leading-tight">
                LTX 2.3. Leave at 0 to use the unified resolution above.
              </div>
              <div className="grid grid-cols-2 gap-1.5">
                <div>
                  <label className="block text-[9px] text-gray-500 mb-0.5">Width</label>
                  <input
                    type="number"
                    value={vidResWidth}
                    onChange={(e) => { setVidResWidth(parseInt(e.target.value) || 0); markDirty(); }}
                    min="0" max="4096" step="64"
                    placeholder={String(resWidth)}
                    className="w-full px-2 py-1 bg-gray-900 border border-gray-700 rounded text-xs text-gray-100 focus:outline-none focus:border-blue-500"
                  />
                </div>
                <div>
                  <label className="block text-[9px] text-gray-500 mb-0.5">Height</label>
                  <input
                    type="number"
                    value={vidResHeight}
                    onChange={(e) => { setVidResHeight(parseInt(e.target.value) || 0); markDirty(); }}
                    min="0" max="4096" step="64"
                    placeholder={String(resHeight)}
                    className="w-full px-2 py-1 bg-gray-900 border border-gray-700 rounded text-xs text-gray-100 focus:outline-none focus:border-blue-500"
                  />
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Project FPS */}
        <div>
          <label className="flex items-center gap-1.5 text-xs font-medium text-gray-400 mb-1">
            Project FPS
          </label>
          <select
            value={projectFps}
            onChange={(e) => { setProjectFps(parseInt(e.target.value)); markDirty(); }}
            className="w-full px-2.5 py-2 bg-gray-800 border border-gray-700 rounded text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          >
            <option value={24}>24 fps (Film / LTX default)</option>
            <option value={25}>25 fps (PAL)</option>
            <option value={30}>30 fps (NTSC)</option>
            <option value={60}>60 fps</option>
          </select>
          <div className="text-[10px] text-gray-600 mt-1">
            Global framerate for all video generation, export, and assembly. All FFmpeg operations use this value.
          </div>
        </div>

        {/* AI Transition Clips (Transition LoRA) */}
        <div>
          <div className="flex items-center gap-2 mb-2">
            <input
              type="checkbox"
              id="use_transition_lora"
              checked={useTransitionLora}
              onChange={(e) => { setUseTransitionLora(e.target.checked); markDirty(); }}
              className="rounded border-gray-700 text-purple-500 focus:ring-purple-500"
            />
            <label htmlFor="use_transition_lora" className="text-xs font-medium text-gray-400">
              Use AI Transitions (Transition LoRA)
            </label>
          </div>
          {useTransitionLora && (
            <div>
              <label className="block text-[10px] text-gray-500 mb-1">LoRA Strength</label>
              <input
                type="number"
                value={transitionLoraStrength}
                onChange={(e) => { setTransitionLoraStrength(parseFloat(e.target.value) || 1.0); markDirty(); }}
                min="0.1"
                max="2.0"
                step="0.1"
                className="w-full px-2.5 py-2 bg-gray-800 border border-gray-700 rounded text-sm text-gray-100 focus:outline-none focus:border-blue-500"
              />
              <p className="text-[10px] text-gray-600 mt-1">
                Auto-generates short AI transition clips between scene pairs during V2V auto-gen. Inserted between scenes in export.
              </p>
            </div>
          )}
        </div>

        {/* Random Ken Burns Effects (narration_images only) */}
        {isNarrationImages && (
          <div>
            <div className="flex items-center gap-2 mb-2">
              <input
                type="checkbox"
                id="random_ken_burns"
                checked={randomKenBurns}
                onChange={(e) => { setRandomKenBurns(e.target.checked); markDirty(); }}
                className="rounded border-gray-700 text-purple-500 focus:ring-purple-500"
              />
              <label htmlFor="random_ken_burns" className="text-xs font-medium text-gray-400">
                Randomize Ken Burns Effects
              </label>
            </div>
            {randomKenBurns && (
              <div className="ml-5 space-y-1.5">
                <label className="block text-[10px] text-gray-500 mb-1.5">Only Use These Ken Burns Effects</label>
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
                        if (kenBurnsAllowedEffects.length === 0) {
                          // First click: switching from "all" to explicit selection — select all EXCEPT this one
                          const allEffects = ['zoom_in_center','zoom_out_center','zoom_in_top_left','zoom_in_top_right','zoom_in_bottom_left','zoom_in_bottom_right','pan_left','pan_right','pan_up','pan_down','pan_left_to_right','pan_right_to_left','zoom_in_pan_left','zoom_in_pan_right','zoom_out_pan_left','zoom_out_pan_right'];
                          if (e.target.checked) {
                            next = allEffects;
                          } else {
                            next = allEffects.filter(v => v !== value);
                          }
                        } else {
                          if (e.target.checked) {
                            next = [...kenBurnsAllowedEffects, value];
                          } else {
                            next = kenBurnsAllowedEffects.filter(v => v !== value);
                          }
                        }
                        // If all are selected, clear to [] (meaning "all")
                        if (next.length >= 16) next = [];
                        setKenBurnsAllowedEffects(next);
                        markDirty();
                      }}
                      className="rounded border-gray-700 text-blue-500 focus:ring-blue-500"
                    />
                    <span className="text-[11px] text-gray-300">{label}</span>
                  </label>
                ))}
                <p className="text-[10px] text-gray-600 mt-2">
                  {kenBurnsAllowedEffects.length === 0
                    ? 'All effects enabled — uncheck any to limit the selection.'
                    : `${kenBurnsAllowedEffects.length} of 16 effects enabled.`}
                </p>
              </div>
            )}
          </div>
        )}

        {/* Autogenerate Characters */}
        <div>
          <button
            onClick={() => {
              if (characters.length > 0) {
                if (!window.confirm('This will replace your current characters with LLM-generated ones and queue image generation for each. Continue?')) return;
              }
              autoCharsMutation.mutate();
            }}
            disabled={autoCharsMutation.isPending}
            className={`w-full flex items-center justify-center gap-2 px-3 py-2 rounded text-sm font-medium transition-colors ${
              autoCharsMutation.isPending
                ? 'bg-gray-800 text-gray-500 cursor-wait'
                : 'bg-indigo-600/80 hover:bg-indigo-600 text-white'
            }`}
            title="Use LLM to generate characters from your concept, lyrics, and style"
          >
            <Users size={14} />
            {autoCharsMutation.isPending ? 'Generating Characters...' : 'Autogenerate Characters'}
          </button>
          {autoCharsMutation.isError && (
            <p className="text-[10px] text-red-400 mt-1">
              {(autoCharsMutation.error as any)?.response?.data?.detail || 'Failed to autogenerate characters'}
            </p>
          )}
          {autoCharsMutation.isSuccess && (
            <p className="text-[10px] text-green-400 mt-1">
              {(autoCharsMutation.data as any)?.message || 'Characters generated!'}
            </p>
          )}
          <p className="text-[10px] text-gray-600 mt-1">
            Analyzes your concept, lyrics, and style to create characters and generate their images.
          </p>
        </div>

        {/* Characters */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <label className="text-xs font-medium text-gray-400">Characters</label>
            <div className="flex gap-1.5">
              <button
                onClick={() => {
                  // Save concept first, then open creator in "new" mode
                  const newChar = { name: '', description: '', image_path: null };
                  const newIndex = characters.length;
                  // Add the character first so it has an index on the backend
                  const newChars = [...characters, newChar];
                  setCharacters(newChars);
                  saveConcept(projectId, {
                    concept_text: conceptText,
                    style_text: styleText,
                    characters: newChars,
                    resolution_width: resWidth,
                    resolution_height: resHeight,
                    image_resolution_width: imgResWidth,
                    image_resolution_height: imgResHeight,
                    video_resolution_width: vidResWidth,
                    video_resolution_height: vidResHeight,
                    project_fps: projectFps,
                    image_direction: imageDirection,
                    custom_image_direction: customImageDirection,
                    global_seed_enabled: globalSeedEnabled,
                    global_seed: globalSeed,
                    use_transition_lora: useTransitionLora,
                    transition_lora_strength: transitionLoraStrength,
                    random_ken_burns: randomKenBurns,
                    ken_burns_allowed_effects: kenBurnsAllowedEffects,
        global_color_override: globalColorOverride,
        custom_color_palette: customColorPalette,
        global_image_color_filter: globalImageColorFilter,
        enable_model_audio: enableModelAudio,
        model_audio_volume: modelAudioVolume,
                  }).then(() => {
                    queryClient.invalidateQueries({ queryKey: ['concept', projectId] });
      // ALSO invalidate the project query so AppLayout.currentProject.settings
      // picks up the freshly saved global_color_override (and any other
      // settings the Scene Editor / dispatcher read from project.settings).
      // Without this, saves persist server-side but the SceneEditor's
      // "use project Default Color Palette" inheritance message keeps
      // showing the stale "(no project default set)" until full page reload.
      queryClient.invalidateQueries({ queryKey: ['project', projectId] });
                    setCreatorOpen({ index: newIndex, character: newChar });
                  });
                }}
                className="flex items-center gap-1 px-2 py-1 bg-purple-600/80 hover:bg-purple-600 rounded text-xs text-white transition-colors"
              >
                <Plus size={12} />
                Create
              </button>
              <button
                onClick={addCharacter}
                className="flex items-center gap-1 px-2 py-1 bg-gray-800 hover:bg-gray-700 rounded text-xs text-gray-300 transition-colors"
              >
                <Plus size={12} />
                Add
              </button>
              <button
                onClick={() => setLibraryOpen(true)}
                className="flex items-center gap-1 px-2 py-1 bg-purple-800/40 hover:bg-purple-700/50 border border-purple-700/50 rounded text-xs text-purple-200 transition-colors"
                title="Browse the global character library and import saved characters into this project"
              >
                🎭 Library
              </button>
              <button
                onClick={() => setLibraryOpen(true)}
                className="flex items-center gap-1 px-2 py-1 bg-purple-800/40 hover:bg-purple-700/50 border border-purple-700/50 rounded text-xs text-purple-200 transition-colors"
                title="Browse the global character library and import saved characters into this project"
              >
                🎭 Library
              </button>
            </div>
          </div>

          {characters.length === 0 && (
            <div className="text-center py-4 text-gray-500 text-xs">
              No characters yet. Add one to get started.
            </div>
          )}

          <div className="space-y-3">
            {characters.map((char, i) => (
              <div key={i} className="p-2.5 bg-gray-800/60 border border-gray-700 rounded space-y-2">
                <div className="flex items-center gap-2">
                  <User size={14} className="text-gray-500 flex-shrink-0" />
                  <input
                    value={char.name}
                    onChange={(e) => { updateCharacter(i, 'name', e.target.value); }}
                    placeholder="Character name"
                    className="flex-1 px-2 py-1 bg-gray-900 border border-gray-700 rounded text-xs text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500"
                  />
                  <button
                    onClick={() => setCreatorOpen({ index: i, character: char })}
                    className="p-1 text-blue-400 hover:text-blue-300 transition-colors"
                    title="Edit character"
                  >
                    <Pencil size={14} />
                  </button>
                  <button
                    onClick={() => removeCharacter(i)}
                    className="p-1 text-red-400 hover:text-red-300 transition-colors"
                    title="Remove character"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>

                <textarea
                  value={char.description}
                  onChange={(e) => { updateCharacter(i, 'description', e.target.value); }}
                  placeholder="Describe this character's appearance, clothing, features..."
                  className="w-full px-2 py-1.5 bg-gray-900 border border-gray-700 rounded text-xs text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 h-14 resize-none"
                />

                {/* Character image */}
                <div className="flex items-center gap-2">
                  {char.image_path ? (
                    <img
                      src={`/api/files/${char.image_path}`}
                      alt={char.name || 'Character'}
                      className="w-12 h-12 object-cover rounded border border-gray-600 cursor-pointer hover:border-blue-500 transition-colors"
                      onClick={() => setLightboxImage({ src: `/api/files/${char.image_path}`, name: char.name || 'Character' })}
                      title="Click to enlarge"
                      onError={handleImgError}
                    />
                  ) : (
                    <div className="w-12 h-12 bg-gray-900 rounded border border-gray-700 flex items-center justify-center">
                      <ImageIcon size={16} className="text-gray-600" />
                    </div>
                  )}
                  <div className="flex-1 flex flex-col gap-1">
                    <label className="flex items-center gap-1 px-2 py-1 bg-gray-900 hover:bg-gray-800 rounded text-[10px] text-gray-400 cursor-pointer transition-colors border border-gray-700 text-center justify-center">
                      <ImageIcon size={10} />
                      Upload Image
                      <input
                        type="file"
                        accept="image/*"
                        className="hidden"
                        onChange={(e) => {
                          const file = e.target.files?.[0];
                          if (file) handleImageUpload(i, file);
                        }}
                      />
                    </label>
                    <button
                      onClick={() => setCreatorOpen({ index: i, character: char })}
                      className="flex items-center gap-1 px-2 py-1 bg-purple-600/80 hover:bg-purple-600 rounded text-[10px] text-white transition-colors justify-center"
                    >
                      <Zap size={10} />
                      Generate / Edit
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Detected Lyrics (read-only) */}
        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1">
            {whisperLyrics && userProvidedLyrics && whisperLyrics === userProvidedLyrics
              ? 'Lyrics (from your input)'
              : whisperLyrics
                ? 'Whisper Detected Lyrics'
                : 'Detected Lyrics'}
          </label>
          <textarea
            value={whisperLyrics || '(No lyrics detected yet — process audio on the Audio tab)'}
            readOnly
            className="w-full px-2.5 py-2 bg-gray-900/60 border border-gray-700/50 rounded text-xs text-gray-400 h-32 resize-none cursor-default focus:outline-none"
          />
        </div>
      </div>

      {/* Character Creator/Editor Modal */}
      {creatorOpen && (
        <CharacterCreatorModal
          projectId={projectId}
          characterIndex={creatorOpen.index}
          character={creatorOpen.character}
          onClose={() => setCreatorOpen(null)}
          onSave={handleCreatorSave}
        />
      )}

      {/* Global Character Library — browse + import */}
      {libraryOpen && (
        <GlobalCharacterLibraryModal
          projectId={projectId}
          onClose={() => setLibraryOpen(false)}
        />
      )}

      {/* Global Character Library — browse + import */}
      {libraryOpen && (
        <GlobalCharacterLibraryModal
          projectId={projectId}
          onClose={() => setLibraryOpen(false)}
        />
      )}

      {/* Character Image Lightbox */}
      {lightboxImage && createPortal(
        <div
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 9999,
            backgroundColor: 'rgba(0, 0, 0, 0.85)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            cursor: 'pointer',
          }}
          onClick={() => setLightboxImage(null)}
        >
          <button
            onClick={() => setLightboxImage(null)}
            style={{
              position: 'absolute',
              top: 16,
              right: 16,
              background: 'rgba(0,0,0,0.6)',
              border: 'none',
              borderRadius: 8,
              padding: 8,
              cursor: 'pointer',
              color: 'white',
              zIndex: 10000,
            }}
          >
            <X size={24} />
          </button>
          <div
            style={{ textAlign: 'center', maxWidth: '90vw', maxHeight: '90vh' }}
            onClick={(e) => e.stopPropagation()}
          >
            <img
              src={lightboxImage.src}
              alt={lightboxImage.name}
              onError={handleImgError}
              style={{
                maxWidth: '90vw',
                maxHeight: '80vh',
                objectFit: 'contain',
                borderRadius: 8,
                boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
              }}
            />
            <p style={{ color: '#ccc', marginTop: 12, fontSize: 14 }}>
              {lightboxImage.name}
            </p>
          </div>
        </div>,
        document.body
      )}
    </div>
  );
}
