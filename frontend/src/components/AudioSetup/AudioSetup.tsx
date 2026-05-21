import { useState, useRef, useEffect } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Upload, Music, Loader, CheckCircle, AlertCircle, Play, Pause, Mic, Drum, Guitar, Waves, FileText, Sparkles, Scissors, MessageSquare } from 'lucide-react';
import { analyzeAudio, uploadAsset, getSections, getLyrics, getAssetFileUrl, createScenesFromSections, sliceSceneAudio, rerunWhisper, suggestTimeline, getScenes } from '@/api/client';
import { useAppStore } from '@/store';

interface AudioSetupProps {
  projectId: string;
  projectMode?: string; // 'music_video' | 'narration_images' | 'narration_video'
}

type AnalysisStage = 'idle' | 'uploading' | 'separating_stems' | 'transcribing' | 'detecting_sections' | 'complete' | 'error';

const stageLabels: Record<AnalysisStage, string> = {
  idle: 'Ready to analyze',
  uploading: 'Uploading audio file...',
  separating_stems: 'Separating stems with Demucs (vocals, drums, bass, other)...',
  transcribing: 'Transcribing lyrics with WhisperX...',
  detecting_sections: 'Detecting song sections...',
  complete: 'Analysis complete!',
  error: 'Analysis failed',
};

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
  const audioRef = useRef<HTMLAudioElement>(null);
  const queryClient = useQueryClient();
  const { assets, scenes } = useAppStore();

  const [stage, setStage] = useState<AnalysisStage>('idle');
  const [errorMessage, setErrorMessage] = useState('');
  const [isPlaying, setIsPlaying] = useState(false);
  const [initialText, setInitialText] = useState('');
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [isResplitting, setIsResplitting] = useState(false);

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
            onChange={(e) => setInitialText(e.target.value)}
            placeholder={textPlaceholder}
            className="w-full h-32 px-3 py-2 bg-gray-900 border border-gray-700 rounded text-xs text-gray-200 resize-y focus:outline-none focus:border-blue-500 placeholder-gray-600"
            disabled={isAnalyzing}
          />
          {initialText.trim() && (
            <p className="text-xs text-gray-500 mt-1">
              {initialText.split('\n').filter(l => /^\[.+\]$/.test(l.trim())).length} tags found — non-section tags will be stripped before processing
            </p>
          )}
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
                    {stageLabels[s]}
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
