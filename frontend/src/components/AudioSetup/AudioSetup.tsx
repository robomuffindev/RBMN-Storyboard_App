import { useState, useRef, useEffect, useCallback } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Upload, Music, Loader, CheckCircle, AlertCircle, Play, Pause, Mic, Drum, Guitar, Waves, FileText, Sparkles, Scissors, MessageSquare } from 'lucide-react';
import { analyzeAudio, uploadAsset, getSections, getLyrics, saveLyricsText, getAssetFileUrl, createScenesFromSections, sliceSceneAudio, rerunWhisper, suggestTimeline, getScenes, uploadSrt, updateProject } from '@/api/client';
import { useAppStore } from '@/store';

interface AudioSetupProps {
  projectId: string;
  projectMode?: string; // 'music_video' | 'narration_images' | 'narration_video'
}

type AnalysisStage = 'idle' | 'uploading' | 'separating_stems' | 'transcribing' | 'detecting_sections' | 'complete' | 'error';

// Music-video labels (default).  Narration projects skip Demucs and
// the "song sections" concept, so we override the relevant labels via
// a mode-aware getter below.  Keeping a Record here so the union type
// stays exhaustive at compile time.
const stageLabelsMusic: Record<AnalysisStage, string> = {
  idle: 'Ready to analyze',
  uploading: 'Uploading audio file...',
  separating_stems: 'Separating stems with Demucs (vocals, drums, bass, other)...',
  transcribing: 'Transcribing lyrics with WhisperX...',
  detecting_sections: 'Detecting song sections...',
  complete: 'Analysis complete!',
  error: 'Analysis failed',
};

// Narration-mode overrides — Demucs is skipped (audio is already speech)
// and we transcribe a script rather than song lyrics.  Empty stage =
// the music label is used.
const stageLabelsNarration: Partial<Record<AnalysisStage, string>> = {
  separating_stems: 'Preparing audio (stem separation skipped for narration)...',
  transcribing: 'Transcribing narration with WhisperX...',
  detecting_sections: 'Detecting narration segments...',
};

function stageLabelFor(stage: AnalysisStage, isNarration: boolean): string {
  if (isNarration && stage in stageLabelsNarration) {
    return stageLabelsNarration[stage]!;
  }
  return stageLabelsMusic[stage];
}

/**
 * Known section tags to KEEP when cleaning lyrics for Whisper.
 * Everything else in brackets gets stripped.
 */
const SECTION_TAG_PATTERNS = [
  /^verse/i,
  /^chorus/i,
  /^pre[- ]?chorus/i,
  /^post[- ]?chorus/i,
  /^bridge/i,
  /^intro/i,
  /^outro/i,
  /^hook/i,
  /^refrain/i,
  /^interlude/i,
  /^break/i,
  /^coda/i,
  /^tag/i,
  /^final\s+(chorus|verse)/i,
];

/**
 * Clean lyrics for Whisper by removing non-section bracket tags.
 * Keeps tags like [Verse 1], [Chorus], [Bridge], [Intro], [Outro], etc.
 * Removes tags like [guitar solo], [harmonica lead], [slow fiddle], [wind], [more intense], etc.
 * Also removes lines that become empty after tag removal.
 */
function cleanLyricsForWhisper(text: string): string {
  const lines = text.split('\n');
  const cleaned: string[] = [];

  for (const line of lines) {
    // Check if the entire line is just a bracket tag
    const fullTagMatch = line.trim().match(/^\[(.+)\]$/);
    if (fullTagMatch) {
      const tagContent = fullTagMatch[1].trim();
      const isSection = SECTION_TAG_PATTERNS.some(p => p.test(tagContent));
      if (isSection) {
        cleaned.push(line);
      }
      // Otherwise skip the entire line (it's a non-section tag like [guitar solo])
      continue;
    }

    // For lines with inline bracket tags, remove non-section tags
    const processed = line.replace(/\[([^\]]+)\]/g, (_match, content) => {
      const isSection = SECTION_TAG_PATTERNS.some(p => p.test(content.trim()));
      return isSection ? `[${content}]` : '';
    }).trim();

    // Only keep non-empty lines
    if (processed) {
      cleaned.push(processed);
    }
  }

  return cleaned.join('\n');
}

export default function AudioSetup({ projectId, projectMode }: AudioSetupProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const srtInputRef = useRef<HTMLInputElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const queryClient = useQueryClient();
  const assets = useAppStore(s => s.assets);
  const scenes = useAppStore(s => s.scenes);
  const currentProject = useAppStore(s => s.currentProject);

  const [stage, setStage] = useState<AnalysisStage>('idle');
  const [errorMessage, setErrorMessage] = useState('');
  const [isPlaying, setIsPlaying] = useState(false);
  const [initialText, setInitialText] = useState('');
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [isResplitting, setIsResplitting] = useState(false);
  const [isUploadingSrt, setIsUploadingSrt] = useState(false);

  // Determine label based on project mode
  const isNarration = projectMode === 'narration_images' || projectMode === 'narration_video';
  const textLabel = isNarration ? 'Script' : 'Lyrics';
  const textPlaceholder = isNarration
    ? 'Paste your narration script here (optional). This helps WhisperX produce more accurate transcription...'
    : 'Paste your song lyrics here (optional). This helps WhisperX produce more accurate transcription...';

  // Find existing music asset
  const musicAsset = (assets || []).find(a => a.asset_type === 'music');

  // Fetch sections for this project
  const { data: sections = [] } = useQuery({
    queryKey: ['sections', projectId],
    queryFn: async () => {
      const response = await getSections(projectId);
      return Array.isArray(response.data) ? response.data : [];
    },
  });

  // Fetch lyrics (always, so we can restore initial_text)
  const { data: lyricsData } = useQuery({
    queryKey: ['lyrics', projectId],
    queryFn: async () => {
      const response = await getLyrics(projectId);
      return response.data;
    },
  });

  // Pre-populate lyrics/script from saved initial_text
  useEffect(() => {
    if (lyricsData?.initial_text && !initialText) {
      setInitialText(lyricsData.initial_text);
    }
  }, [lyricsData?.initial_text]);

  // Debounced auto-save for script/lyrics text (1.5s after last change)
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastSavedRef = useRef<string>('');
  const pendingTextRef = useRef<string>('');
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved'>('idle');

  // Track what was loaded from server so we don't save on initial populate
  useEffect(() => {
    if (lyricsData?.initial_text) {
      lastSavedRef.current = lyricsData.initial_text;
      pendingTextRef.current = lyricsData.initial_text;
    }
  }, [lyricsData?.initial_text]);

  const doSave = useCallback(async (text: string) => {
    if (text !== lastSavedRef.current) {
      setSaveStatus('saving');
      try {
        await saveLyricsText(projectId, text);
        lastSavedRef.current = text;
        queryClient.invalidateQueries({ queryKey: ['lyrics', projectId] });
        setSaveStatus('saved');
        setTimeout(() => setSaveStatus('idle'), 2000);
      } catch (err) {
        console.error('Auto-save lyrics failed:', err);
        setSaveStatus('idle');
      }
    }
  }, [projectId, queryClient]);

  const debouncedSave = useCallback((text: string) => {
    pendingTextRef.current = text;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => doSave(text), 1500);
  }, [doSave]);

  // Flush pending save on unmount (tab switch, navigation)
  useEffect(() => {
    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
      // Fire-and-forget save of any unsaved text
      const pending = pendingTextRef.current;
      if (pending && pending !== lastSavedRef.current) {
        saveLyricsText(projectId, pending).catch(() => {});
      }
    };
  }, [projectId]);

  // Upload-only mutation — just saves the file as an asset, no analysis
  const uploadMutation = useMutation({
    mutationFn: async (file: File) => {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('asset_type', 'music');
      const response = await uploadAsset(projectId, formData);
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['assets', projectId] });
      setPendingFile(null);
    },
    onError: (error: any) => {
      setErrorMessage(error?.response?.data?.detail || error?.message || 'Upload failed');
      setStage('error');
    },
  });

  // Analysis mutation — processes an already-uploaded asset
  const analyzeMutation = useMutation({
    mutationFn: async (assetId: string) => {
      setStage('separating_stems');
      setErrorMessage('');

      const formData = new FormData();
      formData.append('asset_id', assetId);

      // Clean lyrics before sending to Whisper
      const rawText = initialText.trim();
      if (rawText) {
        const cleanedText = cleanLyricsForWhisper(rawText);
        formData.append('initial_text', cleanedText);
      }

      const response = await analyzeAudio(projectId, formData);
      return response.data;
    },
    onSuccess: async () => {
      setStage('complete');
      queryClient.invalidateQueries({ queryKey: ['assets', projectId] });
      queryClient.invalidateQueries({ queryKey: ['sections', projectId] });
      queryClient.invalidateQueries({ queryKey: ['lyrics', projectId] });

      // Auto-create scenes from the detected sections (unless scenes are locked)
      const { scenesLocked } = useAppStore.getState();
      if (!scenesLocked) {
        try {
          await createScenesFromSections(projectId);
        } catch (e) {
          console.warn('Auto-create scenes failed (may already exist):', e);
        }

        // Auto-trigger "Suggest Fresh Timeline" to create optimal scene boundaries
        try {
          await suggestTimeline(projectId);
          const res = await getScenes(projectId);
          const store = useAppStore.getState();
          store.setScenes(res.data);
          if (res.data.length > 0) {
            store.setActiveScene(res.data[0]);
          }
          // Trigger chapter tree refetch in the AppLayout
          window.dispatchEvent(new CustomEvent('rbmn:chapters:invalidate', {
            detail: { projectId },
          }));
        } catch (e) {
          console.warn('Auto-suggest timeline failed:', e);
        }
      } else {
        console.info('Scenes locked — skipping auto-create from sections');
      }
      queryClient.invalidateQueries({ queryKey: ['scenes', projectId] });
    },
    onError: (error: any) => {
      setStage('error');
      setErrorMessage(error?.response?.data?.detail || error?.message || 'Analysis failed');
    },
  });

  const handleFileSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    if (!file) return;

    // Validate it's an audio file
    if (!file.type.startsWith('audio/') && !file.name.match(/\.(mp3|wav|flac|ogg|m4a|aac|wma)$/i)) {
      setErrorMessage('Please select an audio file (MP3, WAV, FLAC, etc.)');
      setStage('error');
      return;
    }

    setStage('idle');
    setErrorMessage('');
    setPendingFile(file);
    uploadMutation.mutate(file);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const handleProcess = () => {
    if (!musicAsset) return;
    analyzeMutation.mutate(musicAsset.id);
  };

  const togglePlayback = () => {
    if (!audioRef.current) return;
    if (isPlaying) {
      audioRef.current.pause();
    } else {
      audioRef.current.play();
    }
    setIsPlaying(!isPlaying);
  };

  const audioUrl = musicAsset ? getAssetFileUrl(projectId, musicAsset.id) : '';
  const hasBeenAnalyzed = sections.length > 0;
  const isAnalyzing = analyzeMutation.isPending;
  const isUploading = uploadMutation.isPending;
  const canProcess = !!musicAsset && !isAnalyzing && !isUploading;

  const handleResplitSceneAudio = async () => {
    if (!projectId || scenes.length === 0) return;
    setIsResplitting(true);
    try {
      const res = await sliceSceneAudio(projectId);
      const msg = res.data?.message || `Re-split audio for ${res.data?.sliced_count || 0} scenes`;
      alert(msg);
    } catch (err: any) {
      const detail = err?.response?.data?.detail || 'Failed to re-split scene audio';
      alert(`Error: ${detail}`);
    } finally {
      setIsResplitting(false);
    }
  };

  const sectionColors: Record<string, string> = {
    intro: 'bg-purple-600',
    verse: 'bg-blue-600',
    chorus: 'bg-green-600',
    bridge: 'bg-yellow-600',
    outro: 'bg-red-600',
    other: 'bg-gray-600',
  };

  return (
    <div className="h-full flex flex-col overflow-hidden">
      <div className="p-4 border-b border-gray-800">
        <h3 className="font-semibold text-sm flex items-center gap-2">
          <Music size={16} />
          Audio Setup
        </h3>
        <p className="text-xs text-gray-400 mt-1">
          Upload your {isNarration ? 'narration audio' : 'song'}, add {textLabel.toLowerCase()}, then process
        </p>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* Step 1: Lyrics / Script Input */}
        <div className="bg-gray-800 rounded-lg p-4">
          <h4 className="text-sm font-medium mb-2 flex items-center gap-2">
            <FileText size={14} className="text-gray-400" />
            <span className="text-blue-400 text-xs font-bold mr-1">1</span>
            {textLabel} <span className="text-xs text-gray-500 font-normal">(optional)</span>
          </h4>
          <p className="text-xs text-gray-500 mb-2">
            Providing {textLabel.toLowerCase()} helps WhisperX produce more accurate transcription. Non-section tags like [guitar solo] will be automatically cleaned.
          </p>
          <textarea
            value={initialText}
            onChange={(e) => {
              const val = e.target.value;
              setInitialText(val);
              debouncedSave(val);
            }}
            onPaste={(e) => {
              // Let React handle the paste via onChange — just ensure we
              // trigger an immediate save after a short delay for paste events
              setTimeout(() => {
                const el = e.target as HTMLTextAreaElement;
                debouncedSave(el.value);
              }, 50);
            }}
            placeholder={textPlaceholder}
            className="w-full h-32 px-3 py-2 bg-gray-900 border border-gray-700 rounded text-xs text-gray-200 resize-y focus:outline-none focus:border-blue-500 placeholder-gray-600 whitespace-pre-wrap"
            disabled={isAnalyzing}
          />
          <div className="flex items-center justify-between mt-1">
            {initialText.trim() ? (
              <p className="text-xs text-gray-500">
                {initialText.split('\n').filter(l => l.trim()).length} lines, {initialText.split('\n').filter(l => /^\[.+\]$/.test(l.trim())).length} tags — non-section tags will be stripped before processing
              </p>
            ) : <span />}
            {saveStatus === 'saving' && (
              <span className="text-xs text-yellow-400 flex items-center gap-1">
                <Loader size={10} className="animate-spin" /> Saving...
              </span>
            )}
            {saveStatus === 'saved' && (
              <span className="text-xs text-green-400 flex items-center gap-1">
                <CheckCircle size={10} /> Saved
              </span>
            )}
          </div>
          {lyricsData?.text && !initialText && (
            <button
              onClick={() => setInitialText(lyricsData.text)}
              className="mt-2 text-xs text-blue-400 hover:text-blue-300 transition-colors"
            >
              Load transcribed {textLabel.toLowerCase()} into editor
            </button>
          )}
        </div>

        {/* Step 2: Upload Audio */}
        <div className="bg-gray-800 rounded-lg p-4">
          <h4 className="text-sm font-medium mb-2 flex items-center gap-2">
            <Music size={14} className="text-gray-400" />
            <span className="text-blue-400 text-xs font-bold mr-1">2</span>
            Audio File
          </h4>

          {!musicAsset && !isUploading ? (
            <div className="border-2 border-dashed border-gray-700 rounded-lg p-6 text-center">
              <Music size={32} className="mx-auto text-gray-500 mb-3" />
              <p className="text-xs text-gray-300 mb-3">
                Upload your {isNarration ? 'narration audio' : 'music'} file
              </p>
              <button
                onClick={() => fileInputRef.current?.click()}
                className="px-5 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-xs font-medium transition-colors inline-flex items-center gap-2"
              >
                <Upload size={14} />
                Select Audio File
              </button>
              <p className="text-xs text-gray-500 mt-2">
                MP3, WAV, FLAC, OGG, M4A
              </p>
            </div>
          ) : isUploading ? (
            <div className="flex items-center gap-3 p-3 bg-gray-900 rounded">
              <Loader size={16} className="animate-spin text-blue-400" />
              <span className="text-xs text-gray-300">Uploading {pendingFile?.name || 'audio'}...</span>
            </div>
          ) : musicAsset ? (
            <div>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3 min-w-0">
                  <Music size={18} className="text-blue-400 flex-shrink-0" />
                  <div className="min-w-0">
                    <p className="text-sm font-medium truncate">{musicAsset.filename}</p>
                    <p className="text-xs text-gray-400">
                      {((musicAsset.file_size || 0) / (1024 * 1024)).toFixed(1)} MB
                    </p>
                  </div>
                </div>
                <div className="flex gap-2 flex-shrink-0">
                  {audioUrl && (
                    <button
                      onClick={togglePlayback}
                      className="p-2 bg-gray-700 hover:bg-gray-600 rounded-full transition-colors"
                    >
                      {isPlaying ? <Pause size={14} /> : <Play size={14} />}
                    </button>
                  )}
                </div>
              </div>
              {audioUrl && (
                <audio
                  ref={audioRef}
                  src={audioUrl}
                  onEnded={() => setIsPlaying(false)}
                  onPause={() => setIsPlaying(false)}
                  onPlay={() => setIsPlaying(true)}
                  preload="metadata"
                />
              )}
              {!isAnalyzing && (
                <button
                  onClick={() => fileInputRef.current?.click()}
                  className="mt-2 w-full px-3 py-1.5 bg-gray-700 hover:bg-gray-600 rounded text-xs font-medium transition-colors"
                >
                  Replace Audio
                </button>
              )}
            </div>
          ) : null}

          <input
            ref={fileInputRef}
            type="file"
            accept="audio/*,.mp3,.wav,.flac,.ogg,.m4a,.aac"
            onChange={handleFileSelect}
            className="hidden"
          />
        </div>

        {/* Step 3: Process Button */}
        {!hasBeenAnalyzed && !isAnalyzing && (
          <button
            onClick={handleProcess}
            disabled={!canProcess}
            className={`w-full py-3 rounded-lg text-sm font-semibold transition-colors flex items-center justify-center gap-2 ${
              canProcess
                ? 'bg-green-600 hover:bg-green-700 text-white'
                : 'bg-gray-700 text-gray-500 cursor-not-allowed'
            }`}
          >
            <Sparkles size={16} />
            Process Audio
          </button>
        )}

        {/* Re-process button (if already analyzed) */}
        {hasBeenAnalyzed && !isAnalyzing && (
          <button
            onClick={handleProcess}
            disabled={!canProcess}
            className="w-full py-2 rounded-lg text-xs font-medium transition-colors flex items-center justify-center gap-2 bg-gray-700 hover:bg-gray-600 text-gray-300"
          >
            <Sparkles size={14} />
            Re-process Audio
          </button>
        )}

        {/* Re-Split Scene Audio button */}
        {hasBeenAnalyzed && !isAnalyzing && scenes.length > 0 && (
          <button
            onClick={handleResplitSceneAudio}
            disabled={isResplitting}
            className="w-full py-2 rounded-lg text-xs font-medium transition-colors flex items-center justify-center gap-2 bg-gray-700 hover:bg-gray-600 text-gray-300 disabled:opacity-50"
          >
            {isResplitting ? <Loader size={14} className="animate-spin" /> : <Scissors size={14} />}
            {isResplitting ? 'Re-Splitting...' : 'Re-Split Scene Audio'}
          </button>
        )}

        {/* Upload SRT — narration modes only */}
        {isNarration && (
          <>
            {/* Narration-timing source chip — surfaces whether scene
                boundaries will be driven by an authoritative SRT (e.g.
                ElevenLabs export) or a probabilistic Whisper pass.
                Updates in real time as the user uploads / re-uploads
                because the parent query key invalidates after every
                analyze / upload_srt round-trip. */}
            {(() => {
              const _src = ((lyricsData as any)?.source || '') as string;
              const _cues = Number((lyricsData as any)?.cue_count || 0);
              const _wordCount = Array.isArray((lyricsData as any)?.words)
                ? (lyricsData as any).words.length
                : 0;
              if (_src === 'srt') {
                return (
                  <div
                    className="w-full px-3 py-2 rounded-lg text-xs flex items-center gap-2 bg-emerald-900/40 border border-emerald-700/60 text-emerald-200"
                    title={
                      `SRT-derived narration timing — ${_cues} cue(s), ` +
                      `${_wordCount} word(s).  Scene boundaries will snap to ` +
                      `cue starts/ends.  Re-running Whisper will NOT overwrite ` +
                      `these cues; re-upload the SRT to refresh.`
                    }
                  >
                    <MessageSquare size={14} />
                    <span className="font-medium">SRT loaded</span>
                    <span className="text-emerald-300/80 ml-auto">
                      {_cues} cue{_cues === 1 ? '' : 's'} · {_wordCount} word{_wordCount === 1 ? '' : 's'}
                    </span>
                  </div>
                );
              }
              if (_src === 'whisper') {
                return (
                  <div
                    className="w-full px-3 py-2 rounded-lg text-xs flex items-center gap-2 bg-amber-900/30 border border-amber-700/40 text-amber-200"
                    title={
                      `Whisper-derived narration timing — ${_wordCount} word(s). ` +
                      `Upload an SRT to use authoritative cue boundaries instead ` +
                      `(better alignment + survives audio re-analysis).`
                    }
                  >
                    <MessageSquare size={14} />
                    <span className="font-medium">Whisper timing</span>
                    <span className="text-amber-300/80 ml-auto">
                      {_wordCount} word{_wordCount === 1 ? '' : 's'}
                    </span>
                  </div>
                );
              }
              return (
                <div
                  className="w-full px-3 py-2 rounded-lg text-xs flex items-center gap-2 bg-gray-800/60 border border-gray-700/60 text-gray-400"
                  title="No narration timing yet.  Run audio analysis or upload an SRT."
                >
                  <MessageSquare size={14} />
                  <span>No SRT or Whisper transcription</span>
                </div>
              );
            })()}

            <button
              onClick={() => srtInputRef.current?.click()}
              disabled={isUploadingSrt}
              className="w-full py-2 rounded-lg text-xs font-medium transition-colors flex items-center justify-center gap-2 bg-indigo-700 hover:bg-indigo-600 text-white disabled:opacity-50"
            >
              {isUploadingSrt ? <Loader size={14} className="animate-spin" /> : <MessageSquare size={14} />}
              {isUploadingSrt ? 'Uploading SRT...' : 'Upload SRT Subtitles'}
            </button>

            {/* Disable Whisper toggle — only meaningful when an SRT is
                loaded.  Persisted on project.settings.disable_whisper so
                the next Re-process Audio honors it.  Backend gracefully
                falls back to running Whisper if the project is somehow
                missing SRT cues when the flag is on. */}
            {(() => {
              const _src = ((lyricsData as any)?.source || '') as string;
              const _isSrtLoaded = _src === 'srt';
              const _disabled = !_isSrtLoaded;
              const _checked = Boolean(
                (currentProject?.settings as any)?.disable_whisper
              );
              const onToggle = async () => {
                if (_disabled || !currentProject) return;
                const newVal = !_checked;
                const nextSettings = {
                  ...(currentProject.settings || {}),
                  disable_whisper: newVal,
                };
                try {
                  await updateProject(currentProject.id, {
                    settings: nextSettings,
                  } as any);
                  // Local store sync so the checkbox flips immediately
                  useAppStore.getState().setProject({
                    ...currentProject,
                    settings: nextSettings,
                  } as any);
                  queryClient.invalidateQueries({
                    queryKey: ['project', currentProject.id],
                  });
                } catch (err) {
                  console.error('Failed to toggle disable_whisper:', err);
                  alert('Could not save the Disable Whisper toggle.');
                }
              };
              return (
                <label
                  className={`w-full flex items-start gap-2 px-3 py-2 rounded-lg text-xs ${
                    _disabled
                      ? 'bg-gray-800/40 border border-gray-700/40 text-gray-500 cursor-not-allowed'
                      : 'bg-gray-800 border border-gray-700 text-gray-200 cursor-pointer hover:bg-gray-750'
                  }`}
                  title={
                    _disabled
                      ? 'Upload an SRT first — Whisper can only be skipped when SRT cues are available as the timing source.'
                      : _checked
                      ? 'Whisper transcription is currently SKIPPED.  Re-process Audio will use the SRT cues directly as narration timing.'
                      : 'Enable to skip Whisper transcription entirely on the next Re-process Audio.  Faster + cleaner alignment when you have an authoritative SRT (ElevenLabs etc.).'
                  }
                >
                  <input
                    type="checkbox"
                    checked={_checked && !_disabled}
                    disabled={_disabled}
                    onChange={onToggle}
                    className="mt-0.5 w-3.5 h-3.5 accent-emerald-500"
                  />
                  <div className="flex-1">
                    <div className="font-medium">
                      Disable Whisper Detection
                      <span className="ml-1 text-gray-500">(SRT required)</span>
                    </div>
                    <div className="text-[10px] mt-0.5 leading-tight">
                      {_disabled
                        ? 'Upload an SRT to enable.  When on, the next Re-process Audio skips Whisper and uses SRT cues directly.'
                        : _checked
                        ? '✓ Whisper will be skipped.  SRT cues will be the sole narration timing source.'
                        : 'When on, Re-process Audio skips Whisper entirely.  SRT cues become the only timing source — faster and more precise than probabilistic transcription.'}
                    </div>
                  </div>
                </label>
              );
            })()}
            <input
              ref={srtInputRef}
              type="file"
              accept=".srt"
              onChange={async (e) => {
                const file = e.currentTarget.files?.[0];
                if (!file) return;
                setIsUploadingSrt(true);
                try {
                  const srtResp = await uploadSrt(projectId, file);
                  // Use the response data directly to populate lyrics cache
                  // This avoids race conditions with refetch returning stale data
                  const srtData = srtResp.data as any;
                  if (srtData?.words && Array.isArray(srtData.words)) {
                    // Normalize word keys (backend returns start_time/end_time)
                    srtData.words = srtData.words.map((w: any) => ({
                      word: String(w?.word || ''),
                      start: Number(w?.start ?? w?.start_time ?? 0) || 0,
                      end: Number(w?.end ?? w?.end_time ?? 0) || 0,
                      score: w?.score,
                      block: w?.block,
                    }));
                  }
                  queryClient.setQueryData(['lyrics', projectId], srtData);
                  console.debug(`[SRT Upload] Set lyrics cache: ${srtData?.words?.length} words, ${srtData?.srt_blocks?.length} srt_blocks`);
                } catch (err: any) {
                  const detail = err?.response?.data?.detail || 'Failed to upload SRT';
                  alert(`Error: ${detail}`);
                } finally {
                  setIsUploadingSrt(false);
                  if (srtInputRef.current) srtInputRef.current.value = '';
                }
              }}
              className="hidden"
            />
          </>
        )}

        {/* Re-run Whisper button */}
        {hasBeenAnalyzed && !isAnalyzing && (
          <button
            onClick={async () => {
              if (!projectId) return;
              try {
                setStage('transcribing');
                await rerunWhisper(projectId);
                queryClient.invalidateQueries({ queryKey: ['lyrics', projectId] });
                setStage('complete');
              } catch (err: any) {
                setStage('error');
                alert(`Whisper failed: ${err?.response?.data?.detail || err.message}`);
              }
            }}
            disabled={stage === 'transcribing'}
            className="w-full py-2 rounded-lg text-xs font-medium transition-colors flex items-center justify-center gap-2 bg-indigo-700 hover:bg-indigo-600 text-gray-200 disabled:opacity-50"
          >
            {stage === 'transcribing' ? <Loader size={14} className="animate-spin" /> : <MessageSquare size={14} />}
            {stage === 'transcribing' ? 'Running Whisper...' : 'Re-run Whisper (Lyrics Only)'}
          </button>
        )}

        {/* Analysis Progress */}
        {isAnalyzing && (
          <div className="bg-gray-800 rounded-lg p-4">
            <div className="flex items-center gap-3 mb-3">
              <Loader size={18} className="animate-spin text-blue-400" />
              <p className="text-sm font-medium">Processing Audio...</p>
            </div>

            <div className="space-y-2">
              {(['separating_stems', 'transcribing', 'detecting_sections'] as AnalysisStage[]).map((s) => {
                const stageOrder = ['separating_stems', 'transcribing', 'detecting_sections', 'complete'];
                const isDone = stageOrder.indexOf(stage) > stageOrder.indexOf(s);
                const isCurrent = stage === s;
                return (
                  <div key={s} className={`flex items-center gap-2 text-xs ${isCurrent ? 'text-blue-400' : isDone ? 'text-green-400' : 'text-gray-500'}`}>
                    {isDone ? <CheckCircle size={14} /> : isCurrent ? <Loader size={14} className="animate-spin" /> : <div className="w-3.5 h-3.5 rounded-full border border-gray-600" />}
                    {stageLabelFor(s, isNarration)}
                  </div>
                );
              })}
            </div>

            {initialText.trim() && (
              <p className="text-xs text-green-400/70 mt-3">
                ✓ {textLabel} provided — Whisper will use them for improved accuracy
              </p>
            )}

            <p className="text-xs text-gray-400 mt-2">
              This may take a few minutes depending on the song length and your hardware.
            </p>
          </div>
        )}

        {/* Error */}
        {stage === 'error' && (
          <div className="bg-red-900/30 border border-red-800 rounded-lg p-4 flex items-start gap-3">
            <AlertCircle size={18} className="text-red-400 flex-shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-medium text-red-300">Failed</p>
              <p className="text-xs text-red-400 mt-1">{errorMessage}</p>
            </div>
          </div>
        )}

        {/* Analysis Results */}
        {hasBeenAnalyzed && !isAnalyzing && (
          <>
            {/* Sections */}
            <div className="bg-gray-800 rounded-lg p-4">
              <h4 className="text-sm font-medium mb-3 flex items-center gap-2">
                <CheckCircle size={14} className="text-green-400" />
                Song Sections ({sections.length})
              </h4>
              <div className="space-y-1">
                {sections.map((section: any) => (
                  <div
                    key={section.id}
                    className="flex items-center gap-2 text-xs"
                  >
                    <span className={`w-2 h-2 rounded-full ${sectionColors[section.label] || 'bg-gray-600'}`} />
                    <span className="capitalize font-medium w-16">{section.label}</span>
                    <span className="text-gray-400">
                      {section.start_time.toFixed(1)}s — {section.end_time.toFixed(1)}s
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* Stems */}
            <div className="bg-gray-800 rounded-lg p-4">
              <h4 className="text-sm font-medium mb-3 flex items-center gap-2">
                <CheckCircle size={14} className="text-green-400" />
                Audio Stems
              </h4>
              <div className="grid grid-cols-2 gap-2">
                {[
                  { name: 'Vocals', icon: Mic },
                  { name: 'Drums', icon: Drum },
                  { name: 'Bass', icon: Guitar },
                  { name: 'Other', icon: Waves },
                ].map(({ name, icon: Icon }) => (
                  <div key={name} className="flex items-center gap-2 p-2 bg-gray-700 rounded text-xs">
                    <Icon size={14} className="text-gray-400" />
                    <span>{name}</span>
                    <CheckCircle size={12} className="text-green-400 ml-auto" />
                  </div>
                ))}
              </div>
            </div>

            {/* Lyrics preview */}
            {lyricsData?.text && (
              <div className="bg-gray-800 rounded-lg p-4">
                <h4 className="text-sm font-medium mb-3 flex items-center gap-2">
                  <CheckCircle size={14} className="text-green-400" />
                  Transcribed {textLabel}
                </h4>
                <p className="text-xs text-gray-300 leading-relaxed line-clamp-6 whitespace-pre-wrap">
                  {lyricsData.text}
                </p>
              </div>
            )}

            <div className="bg-blue-900/20 border border-blue-800 rounded-lg p-4">
              <p className="text-xs text-blue-300">
                Audio analysis complete. Scenes have been automatically generated from detected sections and a fresh timeline has been suggested.
              </p>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
