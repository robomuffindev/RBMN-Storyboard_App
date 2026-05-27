import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useAppStore } from '@/store';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  generateImage,
  enhancePrompt,
  generateVideo,
  rerunPass2,
  setSceneStems,
  mixStems,
  getLyrics,
  getSceneVersions,
  getWorkflowConfigs,
  getConcept,
  updateScene,
  deleteSceneVersion,
  uploadSceneMedia,
  getSettings,
  getPrevSceneLastFrame,
  sliceSingleSceneAudio,
  colorCorrectSceneVideo,
  retrimScene,
} from '@/api/client';
import ReferenceSelector, {
  autoWorkflowType,
  collectRefAssetIds,
  buildRefDescriptions,
  type ReferenceState,
  type CharacterInfo,
} from './ReferenceSelector';
import {
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  ChevronDown,
  Wand2,
  Zap,
  Music,
  ImageIcon,
  Save,
  X,
  Check,
  Trash2,
  Film,
  Upload,
  Info,
  Copy,
  RotateCcw,
  Download,
  Link,
  Unlink,
  Palette,
  Eye,
  RefreshCw,
  Pencil,
} from 'lucide-react';

type Tab = 'image' | 'video' | 'movement' | 'transitions' | 'stems' | 'lyrics' | 'tools' | 'prompt';
type FrameSubTab = 'first' | 'last';

import { handleImgError } from '@/utils/brokenImage';

// ─── Lightbox Component ───────────────────────────────────────────────
function ImageLightbox({
  versions,
  initialIndex,
  onClose,
  onSave,
  onDelete,
}: {
  versions: any[];
  initialIndex: number;
  onClose: () => void;
  onSave: (version: any) => void;
  onDelete: (version: any, index: number) => void;
}) {
  const [index, setIndex] = useState(initialIndex);
  const version = versions[index];
  const imageUrl = version?.output_path ? `/api/files/${version.output_path}` : null;

  // Keep index in bounds if versions change (deletion)
  useEffect(() => {
    if (index >= versions.length && versions.length > 0) {
      setIndex(versions.length - 1);
    }
  }, [versions.length, index]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      if (e.key === 'ArrowLeft') setIndex((i) => Math.max(0, i - 1));
      if (e.key === 'ArrowRight') setIndex((i) => Math.min(versions.length - 1, i + 1));
    },
    [versions.length, onClose]
  );

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);

  if (versions.length === 0) return null;

  return (
    <div
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        zIndex: 9999,
        backgroundColor: 'rgba(0, 0, 0, 0.93)',
        display: 'flex',
        flexDirection: 'column',
      }}
      onClick={onClose}
    >
      <button
        onClick={(e) => { e.stopPropagation(); onClose(); }}
        style={{
          position: 'absolute', top: '16px', right: '16px', padding: '8px',
          backgroundColor: 'rgba(55, 65, 81, 0.9)', borderRadius: '9999px',
          border: 'none', color: '#9ca3af', cursor: 'pointer', zIndex: 10000,
        }}
      >
        <X size={24} />
      </button>

      <div
        style={{
          flex: '1 1 0%', display: 'flex', alignItems: 'center', justifyContent: 'center',
          width: '100%', padding: '16px 72px', position: 'relative', minHeight: 0, overflow: 'hidden',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <button
          onClick={() => setIndex((i) => Math.max(0, i - 1))}
          disabled={index === 0}
          style={{
            position: 'absolute', left: '16px', padding: '12px',
            backgroundColor: 'rgba(55, 65, 81, 0.9)', borderRadius: '9999px', border: 'none',
            color: index === 0 ? '#374151' : '#d1d5db', cursor: index === 0 ? 'default' : 'pointer', zIndex: 10,
          }}
        >
          <ChevronLeft size={28} />
        </button>

        {imageUrl ? (
          <img
            src={imageUrl}
            alt={`Generation ${index + 1}`}
            style={{ maxHeight: '100%', maxWidth: '100%', objectFit: 'contain', borderRadius: '8px' }}
            onError={handleImgError}
          />
        ) : (
          <div style={{ color: '#6b7280', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '8px' }}>
            <ImageIcon size={48} />
            <span>No image available</span>
          </div>
        )}

        <button
          onClick={() => setIndex((i) => Math.min(versions.length - 1, i + 1))}
          disabled={index === versions.length - 1}
          style={{
            position: 'absolute', right: '16px', padding: '12px',
            backgroundColor: 'rgba(55, 65, 81, 0.9)', borderRadius: '9999px', border: 'none',
            color: index === versions.length - 1 ? '#374151' : '#d1d5db',
            cursor: index === versions.length - 1 ? 'default' : 'pointer', zIndex: 10,
          }}
        >
          <ChevronRight size={28} />
        </button>
      </div>

      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          flexShrink: 0, width: '100%', padding: '16px 24px',
          backgroundColor: 'rgba(17, 24, 39, 0.95)', display: 'flex',
          alignItems: 'center', justifyContent: 'space-between', borderTop: '1px solid #374151',
        }}
      >
        <span style={{ fontSize: '14px', color: '#9ca3af' }}>
          {index + 1} / {versions.length}
          {version?.completed_at && (
            <span style={{ marginLeft: '12px', color: '#6b7280' }}>
              {new Date(version.completed_at).toLocaleString()}
            </span>
          )}
        </span>

        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <button
            onClick={() => onDelete(version, index)}
            style={{
              padding: '10px 20px', backgroundColor: '#dc2626', border: 'none', borderRadius: '6px',
              color: 'white', fontSize: '14px', fontWeight: 600, cursor: 'pointer',
              display: 'flex', alignItems: 'center', gap: '8px',
            }}
            onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = '#b91c1c')}
            onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = '#dc2626')}
          >
            <Trash2 size={18} />
            Delete
          </button>
          <button
            onClick={() => onSave(version)}
            style={{
              padding: '10px 24px', backgroundColor: '#16a34a', border: 'none', borderRadius: '6px',
              color: 'white', fontSize: '14px', fontWeight: 600, cursor: 'pointer',
              display: 'flex', alignItems: 'center', gap: '8px',
            }}
            onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = '#15803d')}
            onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = '#16a34a')}
          >
            <Check size={18} />
            Save as Preview
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Camera Action Presets ─────────────────────────────────────────────
const CAMERA_ACTIONS = [
  { value: 'none', label: 'None' },
  { value: 'pan_left', label: 'Pan Left' },
  { value: 'pan_right', label: 'Pan Right' },
  { value: 'tilt_up', label: 'Tilt Up' },
  { value: 'tilt_down', label: 'Tilt Down' },
  { value: 'dolly_in', label: 'Dolly In (Push In)' },
  { value: 'dolly_out', label: 'Dolly Out (Pull Out)' },
  { value: 'zoom_in', label: 'Zoom In' },
  { value: 'zoom_out', label: 'Zoom Out' },
  { value: 'tracking_shot', label: 'Tracking Shot' },
  { value: 'crane_up', label: 'Crane Up (Boom Up)' },
  { value: 'crane_down', label: 'Crane Down (Boom Down)' },
  { value: 'orbit', label: 'Orbit / Arc Shot' },
  { value: 'steadicam', label: 'Steadicam / Gimbal' },
  { value: 'handheld', label: 'Handheld' },
  { value: 'whip_pan', label: 'Whip Pan' },
  { value: 'dutch_angle', label: 'Dutch Angle / Tilt' },
  { value: 'rack_focus', label: 'Rack Focus' },
  { value: 'slow_push', label: 'Slow Push In' },
  { value: 'slow_pull', label: 'Slow Pull Out' },
  { value: 'parallax', label: 'Parallax' },
  { value: 'flyover', label: 'Flyover / Aerial' },
  { value: 'static', label: 'Static (Locked Off)' },
  { value: 'custom', label: 'Custom...' },
];

// ─── Generation Details Modal ──────────────────────────────────────────
function GenerationDetailsModal({
  version,
  onClose,
  onRerun,
  isRerunning,
}: {
  version: any;
  onClose: () => void;
  onRerun: (version: any) => void;
  isRerunning: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const params = version?.parameters || {};
  const isUserUpload = version?.prompt_id === 'user_upload';

  const handleCopyPrompt = () => {
    const prompt = params.prompt || '';
    navigator.clipboard.writeText(prompt).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Escape') onClose();
  };

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  return (
    <div
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        zIndex: 9999,
        backgroundColor: 'rgba(0, 0, 0, 0.85)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        style={{
          backgroundColor: '#111827',
          borderRadius: '12px',
          border: '1px solid #374151',
          maxWidth: '600px',
          width: '90%',
          maxHeight: '80vh',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '16px 20px', borderBottom: '1px solid #1f2937' }}>
          <h3 style={{ color: '#f3f4f6', fontSize: '16px', fontWeight: 600, margin: 0 }}>Generation Details</h3>
          <button onClick={onClose} style={{ color: '#9ca3af', background: 'none', border: 'none', cursor: 'pointer', padding: '4px' }}>
            <X size={20} />
          </button>
        </div>

        {/* Content */}
        <div style={{ padding: '20px', overflowY: 'auto', flex: 1 }}>
          {isUserUpload ? (
            <div style={{ color: '#9ca3af', fontSize: '14px', textAlign: 'center', padding: '20px 0' }}>
              This was a user upload — no generation parameters available.
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
              {/* Prompt */}
              {params.prompt && (
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '6px' }}>
                    <span style={{ color: '#9ca3af', fontSize: '11px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Prompt</span>
                    <button
                      onClick={handleCopyPrompt}
                      style={{ display: 'flex', alignItems: 'center', gap: '4px', color: copied ? '#34d399' : '#60a5fa', background: 'none', border: 'none', cursor: 'pointer', fontSize: '11px', fontWeight: 500 }}
                    >
                      <Copy size={12} /> {copied ? 'Copied!' : 'Copy'}
                    </button>
                  </div>
                  <div style={{ backgroundColor: '#1f2937', padding: '10px 12px', borderRadius: '8px', color: '#d1d5db', fontSize: '13px', lineHeight: 1.5, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                    {params.prompt}
                  </div>
                </div>
              )}

              {/* Two-column grid for parameters */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
                {params.workflow_type && (
                  <DetailItem label="Workflow" value={params.workflow_type} />
                )}
                {params.frame_type && (
                  <DetailItem label="Frame Type" value={params.frame_type === 'first' ? 'First Frame' : 'Last Frame'} />
                )}
                {params.width && params.height && (
                  <DetailItem label="Resolution" value={`${params.width} × ${params.height}`} />
                )}
                {params.seed != null && (
                  <DetailItem label="Seed" value={String(params.seed)} />
                )}
                {params.duration && (
                  <DetailItem label="Duration" value={`${params.duration}s`} />
                )}
                {params.framerate && (
                  <DetailItem label="Framerate" value={`${params.framerate} fps`} />
                )}
                {params.reference_asset_ids && params.reference_asset_ids.length > 0 && (
                  <DetailItem label="References" value={`${params.reference_asset_ids.length} image(s)`} />
                )}
                {version?.job_type && (
                  <DetailItem label="Type" value={version.job_type} />
                )}
              </div>

              {/* Timestamps */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
                {version?.created_at && (
                  <DetailItem label="Created" value={new Date(version.created_at).toLocaleString()} />
                )}
                {version?.completed_at && (
                  <DetailItem label="Completed" value={new Date(version.completed_at).toLocaleString()} />
                )}
              </div>

              {/* Negative prompt — show effective (global + scene merged) if available, else scene-level */}
              {(params.effective_negative_prompt || params.negative_prompt) && (
                <div>
                  <span style={{ color: '#9ca3af', fontSize: '11px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', display: 'block', marginBottom: '6px' }}>
                    Negative Prompt{params.effective_negative_prompt && !params.negative_prompt ? ' (Global)' : params.effective_negative_prompt && params.negative_prompt ? ' (Scene Override)' : ''}
                  </span>
                  <div style={{ backgroundColor: '#1f2937', padding: '10px 12px', borderRadius: '8px', color: '#d1d5db', fontSize: '13px', lineHeight: 1.5 }}>
                    {params.effective_negative_prompt || params.negative_prompt}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer actions */}
        {!isUserUpload && (
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', padding: '14px 20px', borderTop: '1px solid #1f2937' }}>
            <button
              onClick={handleCopyPrompt}
              disabled={!params.prompt}
              style={{
                display: 'flex', alignItems: 'center', gap: '6px',
                padding: '8px 16px', borderRadius: '6px', fontSize: '13px', fontWeight: 500,
                backgroundColor: '#374151', color: '#d1d5db', border: 'none', cursor: 'pointer',
                opacity: params.prompt ? 1 : 0.5,
              }}
            >
              <Copy size={14} /> Copy Prompt
            </button>
            <button
              onClick={() => onRerun(version)}
              disabled={isRerunning}
              style={{
                display: 'flex', alignItems: 'center', gap: '6px',
                padding: '8px 16px', borderRadius: '6px', fontSize: '13px', fontWeight: 500,
                backgroundColor: '#2563eb', color: '#ffffff', border: 'none', cursor: 'pointer',
                opacity: isRerunning ? 0.6 : 1,
              }}
            >
              <RotateCcw size={14} /> {isRerunning ? 'Submitting...' : 'Rerun Generation'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function DetailItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span style={{ color: '#6b7280', fontSize: '10px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', display: 'block', marginBottom: '2px' }}>{label}</span>
      <span style={{ color: '#e5e7eb', fontSize: '13px' }}>{value}</span>
    </div>
  );
}


// ─── Tools Tab Content ───────────────────────────────────────────────
function ToolsTabContent({ scene, projectId }: { scene: any; projectId: string }) {
  const [isSlicing, setIsSlicing] = useState(false);
  const [sliceMessage, setSliceMessage] = useState<string | null>(null);
  const [isColorCorrecting, setIsColorCorrecting] = useState(false);
  const [ccMessage, setCcMessage] = useState<string | null>(null);

  const audioClipPath = scene.parameters?.audio_clip_path;
  const hasAudioClip = !!audioClipPath;
  const hasTimingData = scene.start_time != null && scene.end_time != null;
  const sceneDuration = hasTimingData ? (scene.end_time - scene.start_time).toFixed(2) : null;
  const hasVideo = !!(scene.parameters?.chosen_video_path || scene.parameters?.generated_video_path);
  const hasRef = !!(scene.parameters?.chosen_image_path || scene.parameters?.use_prev_lf_as_ff);

  const handleRegenSceneAudio = async () => {
    if (!projectId || !scene.id) return;
    setIsSlicing(true);
    setSliceMessage(null);
    try {
      const res = await sliceSingleSceneAudio(projectId, scene.id);
      setSliceMessage(res.data?.message || 'Audio segment regenerated successfully');
    } catch (err: any) {
      const detail = err?.response?.data?.detail || 'Failed to regenerate scene audio';
      setSliceMessage(`Error: ${detail}`);
    } finally {
      setIsSlicing(false);
    }
  };

  const handleColorCorrect = async () => {
    if (!projectId || !scene.id) return;
    setIsColorCorrecting(true);
    setCcMessage(null);
    try {
      const res = await colorCorrectSceneVideo(projectId, scene.id);
      if (res.data?.corrected) {
        setCcMessage(res.data.message || 'Color correction applied');
      } else {
        setCcMessage(res.data?.message || 'No correction needed — colors within threshold');
      }
    } catch (err: any) {
      const detail = err?.response?.data?.detail || 'Color correction failed';
      setCcMessage(`Error: ${detail}`);
    } finally {
      setIsColorCorrecting(false);
    }
  };

  const handleDownloadSegment = () => {
    if (!audioClipPath) return;
    const url = `/api/files/${audioClipPath}`;
    const a = document.createElement('a');
    a.href = url;
    a.download = audioClipPath.split('/').pop() || 'scene_audio.wav';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  return (
    <div className="text-sm space-y-6">
      {/* Audio Section */}
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <Music size={16} className="text-cyan-400" />
          <h3 className="font-semibold text-gray-200">Audio</h3>
        </div>

        {/* Scene timing info */}
        {hasTimingData ? (
          <div className="p-3 bg-gray-800 rounded space-y-1">
            <div className="flex items-center justify-between text-xs">
              <span className="text-gray-400">Scene Position</span>
              <span className="text-gray-300 font-mono">
                {scene.start_time.toFixed(2)}s – {scene.end_time.toFixed(2)}s
              </span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-gray-400">Duration</span>
              <span className="text-gray-300 font-mono">{sceneDuration}s</span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-gray-400">Audio Clip</span>
              <span className={`font-mono ${hasAudioClip ? 'text-green-400' : 'text-amber-400'}`}>
                {hasAudioClip ? audioClipPath.split('/').pop() : 'Not generated'}
              </span>
            </div>
          </div>
        ) : (
          <div className="p-3 bg-gray-800/50 rounded text-center text-gray-500 text-xs">
            Scene has no timing data — set start/end times on the timeline first
          </div>
        )}

        {/* Action buttons */}
        <div className="flex gap-2">
          <button
            onClick={handleRegenSceneAudio}
            disabled={isSlicing || !hasTimingData}
            className={`flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded text-xs font-medium transition-colors ${
              isSlicing || !hasTimingData
                ? 'bg-gray-800 text-gray-500 cursor-not-allowed'
                : 'bg-cyan-600 hover:bg-cyan-700 text-white'
            }`}
            title="Re-slice this scene's audio segment from the master audio based on current timeline position"
          >
            <RotateCcw size={14} className={isSlicing ? 'animate-spin' : ''} />
            {isSlicing ? 'Regenerating...' : 'Re-generate Scene Audio'}
          </button>

          <button
            onClick={handleDownloadSegment}
            disabled={!hasAudioClip}
            className={`flex items-center justify-center gap-2 px-3 py-2 rounded text-xs font-medium transition-colors ${
              !hasAudioClip
                ? 'bg-gray-800 text-gray-500 cursor-not-allowed'
                : 'bg-gray-700 hover:bg-gray-600 text-white'
            }`}
            title={hasAudioClip ? 'Download this scene\'s audio segment' : 'Generate the audio segment first'}
          >
            <Download size={14} />
            Download Segment
          </button>
        </div>

        {/* Feedback message */}
        {sliceMessage && (
          <div className={`p-2 rounded text-xs ${
            sliceMessage.startsWith('Error') ? 'bg-red-900/30 text-red-300' : 'bg-green-900/30 text-green-300'
          }`}>
            {sliceMessage}
          </div>
        )}
      </div>

      {/* Color Correction Section */}
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <Palette size={16} className="text-purple-400" />
          <h3 className="font-semibold text-gray-200">Color Correction</h3>
        </div>

        <p className="text-xs text-gray-400">
          Corrects color drift between the reference first-frame image and the generated video.
          Uses the scene's chosen first frame (or previous scene's last frame) as the colour reference.
        </p>

        <button
          onClick={handleColorCorrect}
          disabled={isColorCorrecting || !hasVideo || !hasRef}
          className={`w-full flex items-center justify-center gap-2 px-3 py-2 rounded text-xs font-medium transition-colors ${
            isColorCorrecting || !hasVideo || !hasRef
              ? 'bg-gray-800 text-gray-500 cursor-not-allowed'
              : 'bg-purple-600 hover:bg-purple-700 text-white'
          }`}
          title={
            !hasVideo
              ? 'Generate a video first'
              : !hasRef
              ? 'No reference image available (set a first frame or enable LF-as-FF)'
              : 'Apply color correction to this scene\'s video'
          }
        >
          <Palette size={14} className={isColorCorrecting ? 'animate-pulse' : ''} />
          {isColorCorrecting ? 'Correcting...' : 'Color Correct Video'}
        </button>

        {!hasVideo && (
          <p className="text-xs text-gray-500">No video generated yet for this scene.</p>
        )}
        {hasVideo && !hasRef && (
          <p className="text-xs text-amber-400">No reference image available — set a first frame image or enable "Use LF of Previous."</p>
        )}

        {ccMessage && (
          <div className={`p-2 rounded text-xs ${
            ccMessage.startsWith('Error') ? 'bg-red-900/30 text-red-300' : 'bg-green-900/30 text-green-300'
          }`}>
            {ccMessage}
          </div>
        )}
      </div>
    </div>
  );
}


// ─── Main SceneEditor Component ───────────────────────────────────────
interface SceneEditorProps {
  collapsed?: boolean;
  onToggleCollapse?: () => void;
}

export default function SceneEditor({ collapsed = false, onToggleCollapse }: SceneEditorProps) {
  const [activeTab, setActiveTab] = useState<Tab>('image');
  const [frameSubTab, setFrameSubTab] = useState<FrameSubTab>('first');

  // Lyrics override state
  const [isEditingLyrics, setIsEditingLyrics] = useState(false);
  const [editedLyrics, setEditedLyrics] = useState('');
  const [isSavingLyrics, setIsSavingLyrics] = useState(false);

  // First frame state
  const [prompt, setPrompt] = useState('');
  const [negativePrompt, setNegativePrompt] = useState('');
  // Last frame state
  const [lastFramePrompt, setLastFramePrompt] = useState('');
  const [lastFrameNegPrompt, setLastFrameNegPrompt] = useState('');

  // Shared image settings
  const [imageWidth, setImageWidth] = useState(1536);
  const [imageHeight, setImageHeight] = useState(1024);
  const [imageSeed, setImageSeed] = useState('');
  const [imageSeedOverrideFirst, setImageSeedOverrideFirst] = useState(false);
  const [imageSeedFirst, setImageSeedFirst] = useState('');
  const [imageSeedOverrideLast, setImageSeedOverrideLast] = useState(false);
  const [imageSeedLast, setImageSeedLast] = useState('');
  // Image workflow is now auto-selected via autoWorkflowType() based on reference count
  const [overrideResolution, setOverrideResolution] = useState(false);

  // Video state
  const [videoPrompt, setVideoPrompt] = useState('');
  const [videoDuration, setVideoDuration] = useState(() => {
    // Default to scene length if available
    const scene = useAppStore.getState().activeScene;
    if (scene && scene.start_time != null && scene.end_time != null) {
      return parseFloat((scene.end_time - scene.start_time).toFixed(2));
    }
    return 8;
  });
  const [videoFramerate, setVideoFramerate] = useState(24);
  const [videoWorkflowType, setVideoWorkflowType] = useState<string>('ltx_i2v');
  const [cameraAction, setCameraAction] = useState('none');
  const [customCameraAction, setCustomCameraAction] = useState('');
  const [skipAudioMux, setSkipAudioMux] = useState(false);
  const [videoSeedOverride, setVideoSeedOverride] = useState(false);
  const [videoSeed, setVideoSeed] = useState('');

  // Gallery state
  const [imageHistoryIndex, setImageHistoryIndex] = useState(0);
  const [lastFrameHistoryIndex, setLastFrameHistoryIndex] = useState(0);
  const [allVersions, setAllVersions] = useState<any[]>([]);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [savingPreview, setSavingPreview] = useState(false);
  const [detailsVersion, setDetailsVersion] = useState<any>(null);
  const [isRerunning, setIsRerunning] = useState(false);
  const [twoPassBaseViewOpen, setTwoPassBaseViewOpen] = useState(false);
  const [rerunningPass2, setRerunningPass2] = useState(false);

  // Video gallery state
  const [videoHistoryIndex, setVideoHistoryIndex] = useState(0);
  const [savingVideoPreview, setSavingVideoPreview] = useState(false);

  // Upload refs & state
  const imageUploadRef = useRef<HTMLInputElement>(null);
  const videoUploadRef = useRef<HTMLInputElement>(null);
  const [uploadingImage, setUploadingImage] = useState(false);
  const [uploadingVideo, setUploadingVideo] = useState(false);
  const [isRetrimming, setIsRetrimming] = useState(false);

  // Stems state
  const [vocalsMix, setVocalsMix] = useState(true);
  const [drumsMix, setDrumsMix] = useState(true);
  const [bassMix, setBassMix] = useState(true);
  const [otherMix, setOtherMix] = useState(true);

  // Image Movement state
  const [movementEffect, setMovementEffect] = useState('none');
  const [movementIntensity, setMovementIntensity] = useState(50); // 0-100 percent
  const [movementEasing, setMovementEasing] = useState('ease_in_out');

  // Transitions state
  const [transitionIn, setTransitionIn] = useState('none');
  const [transitionOut, setTransitionOut] = useState('none');
  const [transitionInDuration, setTransitionInDuration] = useState(0.5);
  const [transitionOutDuration, setTransitionOutDuration] = useState(0.5);

  // Per-frame reference state (characters + extra images)
  const emptyRefs: ReferenceState = { characterIndices: [], extras: [] };
  const [firstFrameRefs, setFirstFrameRefs] = useState<ReferenceState>(emptyRefs);
  const [lastFrameRefs, setLastFrameRefs] = useState<ReferenceState>(emptyRefs);
  const activeRefs = frameSubTab === 'first' ? firstFrameRefs : lastFrameRefs;
  const setActiveRefs = frameSubTab === 'first' ? setFirstFrameRefs : setLastFrameRefs;

  const { activeScene, currentProject, assets, jobs, scenes } = useAppStore();

  // "Use Story Flow" — persisted in scene.parameters.use_story_flow
  const useStoryFlow = activeScene?.parameters?.use_story_flow || false;

  const handleSetUseStoryFlow = async (checked: boolean) => {
    if (!activeScene || !currentProject) return;
    const newParams = { ...activeScene.parameters, use_story_flow: checked };
    await updateScene(currentProject.id, activeScene.id, { parameters: newParams });
    useAppStore.getState().updateSceneInStore(activeScene.id, { parameters: newParams });
  };

  // Get the story flow idea for the current scene
  const sceneFlowIdea = activeScene?.parameters?.flow_idea || '';
  const queryClient = useQueryClient();

  // Fetch concept characters for reference selector
  const { data: conceptData } = useQuery({
    queryKey: ['concept', currentProject?.id],
    queryFn: async () => {
      if (!currentProject) return null;
      const response = await getConcept(currentProject.id);
      return response.data;
    },
    enabled: !!currentProject,
    staleTime: 60_000,
  });
  const conceptCharacters: CharacterInfo[] = conceptData?.characters || [];
  const projectWidth = conceptData?.resolution_width || 1536;
  const projectHeight = conceptData?.resolution_height || 864;
  const effectiveWidth = overrideResolution ? imageWidth : projectWidth;
  const effectiveHeight = overrideResolution ? imageHeight : projectHeight;

  // Fetch app settings for model type info
  const { data: appSettings } = useQuery({
    queryKey: ['settings'],
    queryFn: async () => {
      const response = await getSettings();
      return response.data;
    },
    staleTime: 120_000,
  });
  const imageModelType = appSettings?.image_model_type || 'flux2_klein_dev_9b';
  const videoModelType = appSettings?.video_model_type || 'ltx_2.3';

  // Sync video framerate from global settings
  useEffect(() => {
    if (appSettings?.video_fps && appSettings.video_fps > 0) {
      setVideoFramerate(appSettings.video_fps);
    }
  }, [appSettings?.video_fps]);

  // ─── Derived: split versions by frame type and job type ─────────────
  const imageVersions = useMemo(
    () => allVersions.filter((v: any) => v.job_type !== 'video' && v.output_path),
    [allVersions]
  );
  const videoVersions = useMemo(
    () => allVersions.filter((v: any) => v.job_type === 'video' && v.output_path),
    [allVersions]
  );
  const firstFrameVersions = useMemo(
    () => imageVersions.filter((v: any) => !v.parameters?.frame_type || v.parameters.frame_type === 'first'),
    [imageVersions]
  );
  const lastFrameVersions = useMemo(
    () => imageVersions.filter((v: any) => v.parameters?.frame_type === 'last'),
    [imageVersions]
  );

  // Which versions/index to use based on active sub-tab
  const activeVersions = frameSubTab === 'first' ? firstFrameVersions : lastFrameVersions;
  const activeHistoryIndex = frameSubTab === 'first' ? imageHistoryIndex : lastFrameHistoryIndex;
  const setActiveHistoryIndex = frameSubTab === 'first' ? setImageHistoryIndex : setLastFrameHistoryIndex;

  // Chosen paths
  const chosenFirstFramePath = activeScene?.parameters?.chosen_image_path;
  const chosenLastFramePath = activeScene?.parameters?.chosen_last_frame_path;
  const activeChosenPath = frameSubTab === 'first' ? chosenFirstFramePath : chosenLastFramePath;
  const chosenParamKey = frameSubTab === 'first' ? 'chosen_image_path' : 'chosen_last_frame_path';

  // Chosen video path
  const chosenVideoPath = activeScene?.parameters?.chosen_video_path;

  // Active prompt for current sub-tab
  const activePrompt = frameSubTab === 'first' ? prompt : lastFramePrompt;
  const setActivePrompt = frameSubTab === 'first' ? setPrompt : setLastFramePrompt;
  const activeNegPrompt = frameSubTab === 'first' ? negativePrompt : lastFrameNegPrompt;
  const setActiveNegPrompt = frameSubTab === 'first' ? setNegativePrompt : setLastFrameNegPrompt;

  // Video mode
  const videoMode = activeScene?.parameters?.video_mode || 'single';

  // Scene source type toggle
  const sceneSourceType = activeScene?.parameters?.scene_source_type || 'image';

  // Determine if this is the first scene (no previous video for "Use LF of Previous" option)
  const isFirstScene = useMemo(() => {
    if (!activeScene || !scenes || scenes.length === 0) return true;
    const sorted = [...scenes].sort((a, b) => a.order_index - b.order_index);
    return sorted[0]?.id === activeScene.id;
  }, [activeScene?.id, scenes]);

  // "Use LF of Previous Video For FF" setting (Video tab)
  const usePrevLfAsFf = activeScene?.parameters?.use_prev_lf_as_ff || false;

  const handleSetUsePrevLfAsFf = async (checked: boolean) => {
    if (!activeScene || !currentProject) return;
    const newParams = { ...activeScene.parameters, use_prev_lf_as_ff: checked };
    await updateScene(currentProject.id, activeScene.id, { parameters: newParams });
    useAppStore.getState().updateSceneInStore(activeScene.id, { parameters: newParams });
  };

  // "Use Last Frame of Previous Scene" setting (Image tab, First Frame sub-tab)
  const usePrevSceneLastFrame = activeScene?.parameters?.use_prev_scene_last_frame || false;
  const [prevSceneLastFramePath, setPrevSceneLastFramePath] = useState<string | null>(null);
  const [loadingPrevFrame, setLoadingPrevFrame] = useState(false);

  const handleSetUsePrevSceneLastFrame = async (checked: boolean) => {
    if (!activeScene || !currentProject) return;
    const newParams = { ...activeScene.parameters, use_prev_scene_last_frame: checked };
    await updateScene(currentProject.id, activeScene.id, { parameters: newParams });
    useAppStore.getState().updateSceneInStore(activeScene.id, { parameters: newParams });

    if (checked) {
      // Fetch and set the previous scene's last frame as this scene's first frame
      fetchPrevSceneLastFrame();
    } else {
      setPrevSceneLastFramePath(null);
    }
  };

  const fetchPrevSceneLastFrame = useCallback(async () => {
    if (!activeScene || !currentProject || isFirstScene) return;
    setLoadingPrevFrame(true);
    try {
      const response = await getPrevSceneLastFrame(currentProject.id, activeScene.id);
      const path = response.data.image_path;
      setPrevSceneLastFramePath(path);

      // Auto-set as chosen first frame if a path was found
      if (path) {
        const newParams = { ...activeScene.parameters, use_prev_scene_last_frame: true, chosen_image_path: path };
        await updateScene(currentProject.id, activeScene.id, { parameters: newParams });
        useAppStore.getState().updateSceneInStore(activeScene.id, { parameters: newParams });
      }
    } catch {
      setPrevSceneLastFramePath(null);
    } finally {
      setLoadingPrevFrame(false);
    }
  }, [activeScene?.id, currentProject?.id, isFirstScene]);

  // Re-fetch prev scene last frame when scene changes and toggle is on
  useEffect(() => {
    if (usePrevSceneLastFrame && !isFirstScene && activeScene && currentProject) {
      fetchPrevSceneLastFrame();
    } else {
      setPrevSceneLastFramePath(null);
    }
  }, [activeScene?.id, usePrevSceneLastFrame, isFirstScene]);

  // "Ignore Previous Scene Image as Reference" setting (Image tab, First Frame sub-tab)
  const ignorePrevSceneRef = activeScene?.parameters?.ignore_prev_scene_ref || false;

  const handleSetIgnorePrevSceneRef = async (checked: boolean) => {
    if (!activeScene || !currentProject) return;
    const newParams = { ...activeScene.parameters, ignore_prev_scene_ref: checked };
    await updateScene(currentProject.id, activeScene.id, { parameters: newParams });
    useAppStore.getState().updateSceneInStore(activeScene.id, { parameters: newParams });
  };

  // Two-pass generation — persisted in scene.parameters.two_pass_enabled
  const twoPass = activeScene?.parameters?.two_pass_enabled || false;

  const handleSetTwoPass = async (checked: boolean) => {
    if (!activeScene || !currentProject) return;
    const newParams = { ...activeScene.parameters, two_pass_enabled: checked };
    await updateScene(currentProject.id, activeScene.id, { parameters: newParams });
    useAppStore.getState().updateSceneInStore(activeScene.id, { parameters: newParams });
  };

  const handleCameraActionChange = async (value: string) => {
    setCameraAction(value);
    if (!activeScene || !currentProject) return;
    const newParams = { ...activeScene.parameters, camera_action: value };
    await updateScene(currentProject.id, activeScene.id, { parameters: newParams });
    useAppStore.getState().updateSceneInStore(activeScene.id, { parameters: newParams });
  };

  const handleCustomCameraActionChange = async (value: string) => {
    setCustomCameraAction(value);
    if (!activeScene || !currentProject) return;
    const newParams = { ...activeScene.parameters, custom_camera_action: value };
    await updateScene(currentProject.id, activeScene.id, { parameters: newParams });
    useAppStore.getState().updateSceneInStore(activeScene.id, { parameters: newParams });
  };

  const saveSceneSourceType = async (type: 'image' | 'video') => {
    if (!activeScene || !currentProject) return;
    const newParams = { ...activeScene.parameters, scene_source_type: type };
    await updateScene(currentProject.id, activeScene.id, { parameters: newParams });
    useAppStore.getState().updateSceneInStore(activeScene.id, { parameters: newParams });
  };

  const saveMovementSettings = async () => {
    if (!activeScene || !currentProject) return;
    const newParams = {
      ...activeScene.parameters,
      image_movement: {
        effect: movementEffect,
        intensity: movementIntensity,
        easing: movementEasing,
      },
    };
    await updateScene(currentProject.id, activeScene.id, { parameters: newParams });
    useAppStore.getState().updateSceneInStore(activeScene.id, { parameters: newParams });
  };

  const saveTransitionSettings = async () => {
    if (!activeScene || !currentProject) return;
    const newParams = {
      ...activeScene.parameters,
      transition_in: transitionIn !== 'none' ? { type: transitionIn, duration: transitionInDuration } : null,
      transition_out: transitionOut !== 'none' ? { type: transitionOut, duration: transitionOutDuration } : null,
    };
    await updateScene(currentProject.id, activeScene.id, { parameters: newParams });
    useAppStore.getState().updateSceneInStore(activeScene.id, { parameters: newParams });
  };

  const { data: workflows = [] } = useQuery({
    queryKey: ['workflows'],
    queryFn: async () => {
      const response = await getWorkflowConfigs();
      return response.data;
    },
  });

  // (Sections mode removed — always in scenes mode now)

  // Sync state when active scene changes
  useEffect(() => {
    if (activeScene) {
      setPrompt(activeScene.prompt || '');
      setNegativePrompt(activeScene.negative_prompt || '');
      setLastFramePrompt(activeScene.parameters?.last_frame_prompt || '');
      setLastFrameNegPrompt(activeScene.parameters?.last_frame_negative_prompt || '');
      // Default video prompt: use saved video_prompt, else fall back to image prompt
      setVideoPrompt(activeScene.parameters?.video_prompt || activeScene.prompt || '');
      // Default video duration to scene length
      if (activeScene.start_time != null && activeScene.end_time != null) {
        setVideoDuration(parseFloat((activeScene.end_time - activeScene.start_time).toFixed(2)));
      }
      // Restore reference state
      setFirstFrameRefs(activeScene.parameters?.image_refs_first || { characterIndices: [], extras: [] });
      setLastFrameRefs(activeScene.parameters?.image_refs_last || { characterIndices: [], extras: [] });
      if (activeScene.parameters) {
        setOverrideResolution(!!activeScene.parameters.override_resolution);
        setImageWidth(activeScene.parameters.width || 1536);
        setImageHeight(activeScene.parameters.height || 1024);
        setImageSeed(activeScene.parameters.seed?.toString() || '');
        // Load seed overrides
        setImageSeedOverrideFirst(!!activeScene.parameters.image_seed_override_first);
        setImageSeedFirst(activeScene.parameters.image_seed_first?.toString() || '');
        setImageSeedOverrideLast(!!activeScene.parameters.image_seed_override_last);
        setImageSeedLast(activeScene.parameters.image_seed_last?.toString() || '');
        setVideoSeedOverride(!!activeScene.parameters.video_seed_override);
        setVideoSeed(activeScene.parameters.video_seed?.toString() || '');
      } else {
        // New scene with no parameters — reset to defaults
        setOverrideResolution(false);
        setImageWidth(1536);
        setImageHeight(1024);
        setImageSeed('');
        setImageSeedOverrideFirst(false);
        setImageSeedFirst('');
        setImageSeedOverrideLast(false);
        setImageSeedLast('');
        setVideoSeedOverride(false);
        setVideoSeed('');
      }
      // Reset lyrics editing state on scene change
      setIsEditingLyrics(false);
      setEditedLyrics('');
      // Camera action
      setCameraAction(activeScene.parameters?.camera_action || 'none');
      setCustomCameraAction(activeScene.parameters?.custom_camera_action || '');
      // Default video workflow based on video mode
      // V2V Extend is invalid for the first scene — fall back to single
      const rawMode = activeScene.parameters?.video_mode || 'single';
      const mode = (rawMode === 'v2v_extend' && isFirstScene) ? 'single' : rawMode;
      setVideoWorkflowType(mode === 'ff_lf' ? 'ltx_fflf' : mode === 'v2v_extend' ? 'ltx_v2v_extend' : 'ltx_i2v');
      // Load image movement settings
      setMovementEffect(activeScene.parameters?.image_movement?.effect || 'none');
      setMovementIntensity(activeScene.parameters?.image_movement?.intensity ?? 50);
      setMovementEasing(activeScene.parameters?.image_movement?.easing || 'ease_in_out');
      // Load transition settings
      setTransitionIn(activeScene.parameters?.transition_in?.type || 'none');
      setTransitionOut(activeScene.parameters?.transition_out?.type || 'none');
      setTransitionInDuration(activeScene.parameters?.transition_in?.duration ?? 0.5);
      setTransitionOutDuration(activeScene.parameters?.transition_out?.duration ?? 0.5);
    } else {
      // No active scene (e.g. project switch) — clear all prompt state
      // so stale data from previous project doesn't leak through
      setPrompt('');
      setNegativePrompt('');
      setLastFramePrompt('');
      setLastFrameNegPrompt('');
      setVideoPrompt('');
      setFirstFrameRefs({ characterIndices: [], extras: [] });
      setLastFrameRefs({ characterIndices: [], extras: [] });
      setCameraAction('none');
      setCustomCameraAction('');
      setMovementEffect('none');
      setTransitionIn('none');
      setTransitionOut('none');
    }
  }, [activeScene?.id]);

  // Auto-refresh versions when a job for this scene completes
  useEffect(() => {
    if (!activeScene || !currentProject) return;
    const sceneJobs = jobs.filter(
      (j) => j.scene_id === activeScene.id && j.status === 'done'
    );
    if (sceneJobs.length > 0) {
      queryClient.invalidateQueries({
        queryKey: ['scene-versions', currentProject.id, activeScene.id],
      });
    }
  }, [jobs, activeScene?.id, currentProject?.id, queryClient]);

  // Fetch lyrics
  const { data: lyricsData } = useQuery({
    queryKey: ['lyrics', currentProject?.id],
    queryFn: async () => {
      if (!currentProject) return null;
      const response = await getLyrics(currentProject.id);
      return response.data;
    },
    enabled: !!currentProject,
    staleTime: 5 * 60_000,
  });

  const lyricsDebugInfo = (() => {
    if (!lyricsData) return { reason: 'no lyrics data loaded', wordsCount: 0, sampleWord: null, initialTextLength: 0 };
    const words = lyricsData.words || [];
    return {
      wordsCount: words.length,
      sampleWord: words.length > 0 ? words[0] : null,
      sceneStart: activeScene?.start_time,
      sceneEnd: activeScene?.end_time,
      textLength: lyricsData.text?.length || 0,
      initialTextLength: (lyricsData.initial_text || '').length,
    };
  })();

  const sceneLyrics = (() => {
    if (!lyricsData) return '';
    const fullText = lyricsData.text || lyricsData.initial_text || '';
    if (!activeScene) return fullText;

    // ── PREFER stored lyrics from scene parameters ───────────────────
    // The backend stores original user-typed lyrics per scene during
    // suggest_timeline, keyed as scene.parameters.lyrics. This is more
    // accurate than reconstructing from Whisper words (which are garbled).
    const storedLyrics = (activeScene as any).parameters?.lyrics;
    if (storedLyrics && typeof storedLyrics === 'string') {
      return storedLyrics;
    }

    // ── FALLBACK: reconstruct from Whisper word timestamps ───────────
    const words = lyricsData.words || [];
    const sceneStart = activeScene.start_time;
    const sceneEnd = activeScene.end_time;

    if (words.length > 0 && sceneStart != null && sceneEnd != null && sceneEnd > sceneStart) {
      // ── Phrase-aware assignment ──────────────────────────────────────
      // Group words into phrases using user's pasted lyrics lines, then
      // assign WHOLE phrases to scenes using >50% overlap. This prevents
      // a stray word like "Black" from being split from its phrase
      // "Black hat on a wooden chair".

      // Try initial_text first (user's pasted lyrics with line breaks),
      // then fall back to full_text (Whisper's transcription)
      const initialText = (lyricsData.initial_text || '').trim();
      const whisperText = (lyricsData.text || '').trim();
      const lyricsSource = initialText || whisperText;
      const phrases: Array<Array<any>> = [];

      const cleanWord = (w: string) => w.replace(/[^a-zA-Z0-9]/g, '').toLowerCase();
      const whisperCleaned = words.map((w: any) => cleanWord(w.word || ''));

      if (lyricsSource) {
        // Split into lines. initial_text has user line breaks;
        // whisperText may not, so also try splitting on punctuation/pauses
        let lines: string[];
        if (initialText) {
          lines = initialText.split('\n').map((l: string) => l.trim()).filter((l: string) => l.length > 0);
        } else {
          // Whisper text has no line breaks — detect phrase boundaries
          // via punctuation. Split on . ! ? … and also on long pauses
          // (we approximate by splitting on >=6 words as a fallback)
          lines = whisperText.split(/[.!?…]+/).map((l: string) => l.trim()).filter((l: string) => l.length > 0);
          // If that produced only one giant line, split into ~6-word chunks
          if (lines.length <= 1 && words.length > 8) {
            lines = [];
            const chunkSize = 6;
            const allWords = whisperText.split(/\s+/);
            for (let i = 0; i < allWords.length; i += chunkSize) {
              lines.push(allWords.slice(i, i + chunkSize).join(' '));
            }
          }
        }

        // Track which word indices are assigned to which group
        const wordToGroup: Map<number, number> = new Map();
        let wordIdx = 0;

        for (let lineIdx = 0; lineIdx < lines.length; lineIdx++) {
          const lineWords = lines[lineIdx].split(/\s+/);
          const expectedCount = lineWords.length;
          const firstWord = cleanWord(lineWords[0] || '');

          if (wordIdx >= words.length) break;

          // Find start of this line in Whisper words (small lookahead window)
          let bestStart = wordIdx;
          if (firstWord) {
            const searchEnd = Math.min(wordIdx + 5, words.length);
            for (let s = wordIdx; s < searchEnd; s++) {
              if (whisperCleaned[s] === firstWord) {
                bestStart = s;
                break;
              }
            }
          }

          // Assign expected word count, capped at next line's first word
          let groupEnd = bestStart + expectedCount;
          if (lineIdx + 1 < lines.length) {
            const nextLineWords = lines[lineIdx + 1].split(/\s+/);
            const nextFirst = cleanWord(nextLineWords[0] || '');
            if (nextFirst) {
              for (let s = Math.max(bestStart + 1, groupEnd - 2); s < Math.min(groupEnd + 5, words.length); s++) {
                if (whisperCleaned[s] === nextFirst) {
                  groupEnd = s;
                  break;
                }
              }
            }
          }
          groupEnd = Math.min(groupEnd, words.length);

          if (bestStart < groupEnd) {
            const gIdx = phrases.length;
            phrases.push(words.slice(bestStart, groupEnd));
            for (let wi = bestStart; wi < groupEnd; wi++) {
              wordToGroup.set(wi, gIdx);
            }
            wordIdx = groupEnd;
          } else {
            wordIdx = bestStart;
          }
        }

        // Remaining words go into last group
        if (wordIdx < words.length) {
          if (phrases.length > 0) {
            const lastIdx = phrases.length - 1;
            phrases[lastIdx] = [...phrases[lastIdx], ...words.slice(wordIdx)];
            for (let wi = wordIdx; wi < words.length; wi++) {
              wordToGroup.set(wi, lastIdx);
            }
          } else {
            phrases.push(words.slice(wordIdx));
            for (let wi = wordIdx; wi < words.length; wi++) {
              wordToGroup.set(wi, 0);
            }
          }
        }

        // ── WORD ANCHORING: merge orphaned words into nearest group ──
        const orphaned: number[] = [];
        for (let i = 0; i < words.length; i++) {
          if (!wordToGroup.has(i)) orphaned.push(i);
        }
        if (orphaned.length > 0 && phrases.length > 0) {
          console.log(`[WordAnchor] Found ${orphaned.length} orphaned word(s):`,
            orphaned.map(i => whisperCleaned[i]));
          for (const oi of orphaned) {
            // Find nearest group
            let bestGroup = 0;
            let bestDist = Infinity;
            for (let gi = 0; gi < phrases.length; gi++) {
              // Find min/max word indices in this group
              const groupWordIndices: number[] = [];
              wordToGroup.forEach((g, wi) => { if (g === gi) groupWordIndices.push(wi); });
              if (groupWordIndices.length > 0) {
                const gMin = Math.min(...groupWordIndices);
                const gMax = Math.max(...groupWordIndices);
                const dist = Math.min(Math.abs(oi - gMin), Math.abs(oi - gMax));
                if (dist < bestDist) {
                  bestDist = dist;
                  bestGroup = gi;
                }
              }
            }
            // Insert into the group at the right position (by timing)
            const wordObj = words[oi];
            const wordTime = wordObj.start_time ?? wordObj.start ?? 0;
            let inserted = false;
            for (let pos = 0; pos < phrases[bestGroup].length; pos++) {
              const gw = phrases[bestGroup][pos];
              if ((gw.start_time ?? gw.start ?? 0) > wordTime) {
                phrases[bestGroup].splice(pos, 0, wordObj);
                inserted = true;
                break;
              }
            }
            if (!inserted) phrases[bestGroup].push(wordObj);
            wordToGroup.set(oi, bestGroup);
            console.log(`[WordAnchor] Anchored '${whisperCleaned[oi]}' → group ${bestGroup}`);
          }
        }

        // ── Merge single-word groups into neighbors ─────────────────
        if (phrases.length > 1) {
          const mergedPhrases: Array<Array<any>> = [];
          for (let gi = 0; gi < phrases.length; gi++) {
            if (phrases[gi].length === 1 && phrases.length > 1) {
              const wText = (phrases[gi][0].word || '').trim();
              if (mergedPhrases.length > 0) {
                mergedPhrases[mergedPhrases.length - 1].push(...phrases[gi]);
                console.log(`[WordAnchor] Merged single-word '${wText}' into previous group`);
              } else if (gi + 1 < phrases.length) {
                phrases[gi + 1] = [...phrases[gi], ...phrases[gi + 1]];
                console.log(`[WordAnchor] Merged single-word '${wText}' into next group`);
              } else {
                mergedPhrases.push(phrases[gi]);
              }
            } else {
              mergedPhrases.push(phrases[gi]);
            }
          }
          phrases.length = 0;
          phrases.push(...mergedPhrases);
        }
      } else {
        // No lyrics text at all — group ALL words as one phrase so the
        // >50% overlap rule assigns them atomically (never orphans a word)
        phrases.push([...words]);
      }

      // Debug: log phrase grouping for first scene only (reduces console noise)
      if (activeScene.order_index === 0 && phrases.length > 0) {
        const firstPhraseWords = phrases[0].map((w: any) => w.word).join(' ');
        console.log(
          `[SceneLyrics] Scene 0 (${sceneStart.toFixed(1)}–${sceneEnd.toFixed(1)}s) | ` +
          `initial_text: ${initialText ? initialText.length + ' chars' : 'EMPTY'} | ` +
          `phrases: ${phrases.length} | first phrase: "${firstPhraseWords}" ` +
          `(${(phrases[0][0]?.start_time ?? phrases[0][0]?.start ?? 0).toFixed(2)}s–` +
          `${(phrases[0][phrases[0].length - 1]?.end_time ?? phrases[0][phrases[0].length - 1]?.end ?? 0).toFixed(2)}s)`
        );
      }

      // Assign whole phrases to this scene using >50% overlap rule
      const sceneWords: any[] = [];
      for (const phrase of phrases) {
        if (phrase.length === 0) continue;
        const phraseStart = phrase[0].start_time ?? phrase[0].start ?? 0;
        const phraseEnd = phrase[phrase.length - 1].end_time ?? phrase[phrase.length - 1].end ?? 0;
        const phraseDuration = Math.max(phraseEnd - phraseStart, 0.01);
        const overlapStart = Math.max(phraseStart, sceneStart);
        const overlapEnd = Math.min(phraseEnd, sceneEnd);
        const overlap = Math.max(0, overlapEnd - overlapStart);

        if (overlap >= phraseDuration * 0.5) {
          sceneWords.push(...phrase);
        }
      }

      const result = sceneWords.map((w: any) => w.word).join(' ').trim();
      return result || `(No lyrics detected in this section: ${sceneStart.toFixed(1)}s – ${sceneEnd.toFixed(1)}s)`;
    }

    // No word-level timestamps available — can't filter per-scene accurately.
    // Show a message instead of the misleading proportional distribution.
    if (fullText && sceneStart != null && sceneEnd != null) {
      return `(No word timestamps — reprocess audio to get per-scene lyrics)`;
    }

    return fullText;
  })();

  // Fetch scene versions (all — we split by frame_type client-side)
  const { data: versionsData } = useQuery({
    queryKey: ['scene-versions', currentProject?.id, activeScene?.id],
    queryFn: async () => {
      if (!currentProject || !activeScene) return [];
      try {
        const response = await getSceneVersions(currentProject.id, activeScene.id);
        return Array.isArray(response.data) ? response.data : [];
      } catch {
        return [];
      }
    },
    enabled: !!currentProject && !!activeScene && (activeTab === 'image' || activeTab === 'video'),
    staleTime: 30_000,
  });

  useEffect(() => {
    setAllVersions(versionsData || []);
  }, [versionsData]);

  // Sync first-frame gallery index to chosen image
  useEffect(() => {
    if (chosenFirstFramePath && firstFrameVersions.length > 0) {
      const idx = firstFrameVersions.findIndex((v: any) => v.output_path === chosenFirstFramePath);
      if (idx >= 0) { setImageHistoryIndex(idx); return; }
    }
    setImageHistoryIndex(0);
  }, [firstFrameVersions, chosenFirstFramePath]);

  // Sync last-frame gallery index to chosen image
  useEffect(() => {
    if (chosenLastFramePath && lastFrameVersions.length > 0) {
      const idx = lastFrameVersions.findIndex((v: any) => v.output_path === chosenLastFramePath);
      if (idx >= 0) { setLastFrameHistoryIndex(idx); return; }
    }
    setLastFrameHistoryIndex(0);
  }, [lastFrameVersions, chosenLastFramePath]);

  // ─── Save frame as preview ───────────────────────────────────────
  const handleSaveAsPreview = async (version: any) => {
    if (!activeScene || !currentProject || !version?.output_path) return;
    setSavingPreview(true);
    try {
      const newParams = {
        ...activeScene.parameters,
        [chosenParamKey]: version.output_path,
      };
      await updateScene(currentProject.id, activeScene.id, { parameters: newParams });
      useAppStore.getState().updateSceneInStore(activeScene.id, { parameters: newParams });
      setLightboxOpen(false);
    } catch (err) {
      console.error('Failed to save preview:', err);
    } finally {
      setSavingPreview(false);
    }
  };

  // ─── Rerun Pass 2 (character compositing) ────────────────────────
  const handleRerunPass2 = async () => {
    if (!activeScene || !currentProject) return;
    setRerunningPass2(true);
    try {
      await rerunPass2(currentProject.id, { scene_id: activeScene.id });
    } catch (err) {
      console.error('Failed to rerun pass 2:', err);
      alert('Failed to rerun Pass 2. Check that characters have images assigned.');
    } finally {
      setRerunningPass2(false);
    }
  };

  // ─── Delete version ──────────────────────────────────────────────
  const handleDeleteVersion = async (version: any, index: number) => {
    if (!activeScene || !currentProject || !version?.id) return;
    if (!window.confirm('Delete this generated image? This is permanent and cannot be undone.')) return;
    try {
      await deleteSceneVersion(currentProject.id, activeScene.id, version.id);
      const newAll = allVersions.filter((v: any) => v.id !== version.id);
      setAllVersions(newAll);

      // Adjust active index
      const newFiltered = frameSubTab === 'first'
        ? newAll.filter((v: any) => !v.parameters?.frame_type || v.parameters.frame_type === 'first')
        : newAll.filter((v: any) => v.parameters?.frame_type === 'last');

      if (newFiltered.length === 0) {
        setActiveHistoryIndex(0);
        setLightboxOpen(false);
      } else {
        setActiveHistoryIndex(Math.min(index, newFiltered.length - 1));
      }

      // If deleted version was the chosen one, auto-fallback to next available or clear
      if (version.output_path === activeChosenPath) {
        const fallbackVersion = newFiltered.length > 0
          ? newFiltered[Math.min(index, newFiltered.length - 1)]
          : null;
        const newChosenPath = fallbackVersion?.output_path ?? undefined;
        const newParams = { ...activeScene.parameters, [chosenParamKey]: newChosenPath };
        await updateScene(currentProject.id, activeScene.id, { parameters: newParams });
        useAppStore.getState().updateSceneInStore(activeScene.id, { parameters: newParams });
      }

      queryClient.invalidateQueries({
        queryKey: ['scene-versions', currentProject.id, activeScene.id],
      });
    } catch (err) {
      console.error('Failed to delete version:', err);
    }
  };

  // Sync video gallery index to chosen video
  useEffect(() => {
    if (chosenVideoPath && videoVersions.length > 0) {
      const idx = videoVersions.findIndex((v: any) => v.output_path === chosenVideoPath);
      if (idx >= 0) { setVideoHistoryIndex(idx); return; }
    }
    setVideoHistoryIndex(0);
  }, [videoVersions, chosenVideoPath]);

  // ─── Save video as active ──────────────────────────────────────
  const handleSaveVideoAsActive = async (version: any) => {
    if (!activeScene || !currentProject || !version?.output_path) return;
    setSavingVideoPreview(true);
    try {
      const newParams = {
        ...activeScene.parameters,
        chosen_video_path: version.output_path,
      };
      await updateScene(currentProject.id, activeScene.id, { parameters: newParams });
      useAppStore.getState().updateSceneInStore(activeScene.id, { parameters: newParams });
    } catch (err) {
      console.error('Failed to save active video:', err);
    } finally {
      setSavingVideoPreview(false);
    }
  };

  // ─── Delete video version ──────────────────────────────────────
  const handleDeleteVideoVersion = async (version: any, index: number) => {
    if (!activeScene || !currentProject || !version?.id) return;
    if (!window.confirm('Delete this generated video? This is permanent and cannot be undone.')) return;
    try {
      await deleteSceneVersion(currentProject.id, activeScene.id, version.id);
      const newAll = allVersions.filter((v: any) => v.id !== version.id);
      setAllVersions(newAll);

      const newVideoVers = newAll.filter((v: any) => v.job_type === 'video');
      if (newVideoVers.length === 0) {
        setVideoHistoryIndex(0);
      } else {
        setVideoHistoryIndex(Math.min(index, newVideoVers.length - 1));
      }

      // If deleted video was the chosen one, auto-fallback to next available or clear
      if (version.output_path === chosenVideoPath) {
        const fallbackVideo = newVideoVers.length > 0
          ? newVideoVers[Math.min(index, newVideoVers.length - 1)]
          : null;
        const newChosenVideo = fallbackVideo?.output_path ?? undefined;
        const newParams = { ...activeScene.parameters, chosen_video_path: newChosenVideo };
        await updateScene(currentProject.id, activeScene.id, { parameters: newParams });
        useAppStore.getState().updateSceneInStore(activeScene.id, { parameters: newParams });
      }

      queryClient.invalidateQueries({
        queryKey: ['scene-versions', currentProject.id, activeScene.id],
      });
    } catch (err) {
      console.error('Failed to delete video version:', err);
    }
  };

  // ─── Rerun generation from version details ──────────────────────────
  const handleRerunGeneration = async (version: any) => {
    if (!activeScene || !currentProject || !version?.parameters) return;
    setIsRerunning(true);
    try {
      const params = version.parameters;
      if (version.job_type === 'video') {
        await generateVideo(currentProject.id, {
          scene_id: activeScene.id,
          workflow_type: params.workflow_type,
          prompt: params.prompt || '',
          width: params.width || 1024,
          height: params.height || 576,
          duration: params.duration || 10,
          framerate: params.framerate || 24,
          seed: params.seed,
          first_frame_asset_id: params.first_frame_asset_id,
          last_frame_asset_id: params.last_frame_asset_id,
          // audio auto-resolved from scene's audio_clip_path by backend
        });
      } else {
        await generateImage(currentProject.id, {
          scene_id: activeScene.id,
          workflow_type: params.workflow_type,
          prompt: params.prompt || '',
          width: params.width || 1024,
          height: params.height || 576,
          seed: params.seed,
          reference_asset_ids: params.reference_asset_ids || [],
          frame_type: params.frame_type,
        });
      }
      setDetailsVersion(null);
    } catch (err) {
      console.error('Failed to rerun generation:', err);
    } finally {
      setIsRerunning(false);
    }
  };

  // ─── Upload image to scene gallery ─────────────────────────────────
  const handleImageUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    if (!file || !activeScene || !currentProject) return;
    setUploadingImage(true);
    try {
      await uploadSceneMedia(
        currentProject.id,
        activeScene.id,
        file,
        'image',
        frameSubTab,
      );
      // Refresh versions and update scene in store (backend auto-sets preview)
      queryClient.invalidateQueries({
        queryKey: ['scene-versions', currentProject.id, activeScene.id],
      });
      // Refresh scene data to pick up the new chosen_image_path
      const sceneResp = await import('@/api/client').then(m => m.getScenes(currentProject.id));
      const updatedScene = sceneResp.data.find((s: any) => s.id === activeScene.id);
      if (updatedScene) {
        useAppStore.getState().updateSceneInStore(activeScene.id, { parameters: updatedScene.parameters });
      }
    } catch (err) {
      console.error('Failed to upload image:', err);
    } finally {
      setUploadingImage(false);
      if (imageUploadRef.current) imageUploadRef.current.value = '';
    }
  };

  // ─── Retrim scene video (re-run post-processing pipeline) ──────────
  const handleRetrimScene = async () => {
    if (!currentProject || !activeScene) return;
    const chosenVideo = activeScene.parameters?.chosen_video_path;
    const untrimmedVideo = activeScene.parameters?.video_untrimmed_path;
    if (!chosenVideo && !untrimmedVideo) {
      alert('No video to retrim — generate or upload a video first.');
      return;
    }
    setIsRetrimming(true);
    try {
      const res = await retrimScene(currentProject.id, activeScene.id);
      if (res.data.success) {
        // Refresh scene data
        const scenesRes = await import('@/api/client').then(m => m.getScenes(currentProject.id));
        useAppStore.getState().setScenes(scenesRes.data);
        alert(`Retrim complete: ${res.data.message}`);
      } else {
        alert(`Retrim failed: ${res.data.message}`);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Unknown error';
      alert(`Retrim error: ${msg}`);
    } finally {
      setIsRetrimming(false);
    }
  };

  // ─── Upload video to scene gallery ─────────────────────────────────
  const handleVideoUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    if (!file || !activeScene || !currentProject) return;
    setUploadingVideo(true);
    try {
      await uploadSceneMedia(
        currentProject.id,
        activeScene.id,
        file,
        'video',
      );
      queryClient.invalidateQueries({
        queryKey: ['scene-versions', currentProject.id, activeScene.id],
      });
      const sceneResp = await import('@/api/client').then(m => m.getScenes(currentProject.id));
      const updatedScene = sceneResp.data.find((s: any) => s.id === activeScene.id);
      if (updatedScene) {
        useAppStore.getState().updateSceneInStore(activeScene.id, { parameters: updatedScene.parameters });
      }
    } catch (err) {
      console.error('Failed to upload video:', err);
    } finally {
      setUploadingVideo(false);
      if (videoUploadRef.current) videoUploadRef.current.value = '';
    }
  };

  // ─── Save video mode to scene parameters ─────────────────────────
  // Lipsync settings — derived from scene parameters, default ON
  const lipsyncEnabled = activeScene?.parameters?.lipsync_enabled !== false; // default true
  const vocalsOnlyForLipsync = activeScene?.parameters?.vocals_only_for_lipsync || false;

  const handleSetLipsync = async (enabled: boolean) => {
    if (!activeScene || !currentProject) return;
    const newParams = { ...activeScene.parameters, lipsync_enabled: enabled };
    await updateScene(currentProject.id, activeScene.id, { parameters: newParams });
    useAppStore.getState().updateSceneInStore(activeScene.id, { parameters: newParams });
  };

  const handleSetVocalsOnlyForLipsync = async (enabled: boolean) => {
    if (!activeScene || !currentProject) return;
    const newParams = { ...activeScene.parameters, vocals_only_for_lipsync: enabled };
    await updateScene(currentProject.id, activeScene.id, { parameters: newParams });
    useAppStore.getState().updateSceneInStore(activeScene.id, { parameters: newParams });
  };

  const handleSetVideoMode = async (mode: 'single' | 'ff_lf' | 'v2v_extend') => {
    if (!activeScene || !currentProject) return;
    const newParams = { ...activeScene.parameters, video_mode: mode };
    // Update UI state immediately so toggle feels responsive
    useAppStore.getState().updateSceneInStore(activeScene.id, { parameters: newParams });
    setVideoWorkflowType(mode === 'ff_lf' ? 'ltx_fflf' : mode === 'v2v_extend' ? 'ltx_v2v_extend' : 'ltx_i2v');
    // Persist to backend (non-blocking)
    try {
      await updateScene(currentProject.id, activeScene.id, { parameters: newParams });
    } catch (err) {
      console.error('Failed to save video mode:', err);
    }
  };

  // ─── Mutations ────────────────────────────────────────────────────
  const generateImageMutation = useMutation({
    mutationFn: async () => {
      if (!activeScene || !currentProject) return;

      // Auto-select characters mentioned in flow/prompt/lyrics
      const updatedRefs = autoSelectCharactersForScene();

      // Recompute ref asset IDs and workflow with the updated refs
      const updatedRefAssetIds = collectRefAssetIds(updatedRefs, conceptCharacters, safeAssets);
      const updatedWorkflowType = autoWorkflowType(updatedRefs, conceptCharacters);

      // Always use auto-selected workflow based on reference count
      const effectiveWorkflow = updatedWorkflowType;

      // Use the updated refs for saving
      const updatedFirstFrameRefs = frameSubTab === 'first' ? updatedRefs : firstFrameRefs;
      const updatedLastFrameRefs = frameSubTab === 'last' ? updatedRefs : lastFrameRefs;

      // Save prompts + reference state to scene
      const paramUpdates: Record<string, any> = {
        ...activeScene.parameters,
        override_resolution: overrideResolution,
        width: imageWidth,
        height: imageHeight,
        seed: imageSeed ? parseInt(imageSeed) : undefined,
        workflow_type: effectiveWorkflow,
        image_refs_first: updatedFirstFrameRefs,
        image_refs_last: updatedLastFrameRefs,
        // Per-frame seed overrides
        image_seed_override_first: imageSeedOverrideFirst,
        image_seed_first: imageSeedOverrideFirst && imageSeedFirst ? parseInt(imageSeedFirst) : undefined,
        image_seed_override_last: imageSeedOverrideLast,
        image_seed_last: imageSeedOverrideLast && imageSeedLast ? parseInt(imageSeedLast) : undefined,
      };
      if (frameSubTab === 'last') {
        paramUpdates.last_frame_prompt = lastFramePrompt;
        paramUpdates.last_frame_negative_prompt = lastFrameNegPrompt;
      }

      const sceneUpdate: any = { parameters: paramUpdates };
      if (frameSubTab === 'first') {
        sceneUpdate.prompt = prompt;
        sceneUpdate.negative_prompt = negativePrompt;
      }

      await updateScene(currentProject.id, activeScene.id, sceneUpdate);
      useAppStore.getState().updateSceneInStore(activeScene.id, sceneUpdate);

      const isUUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(effectiveWorkflow);
      const response = await generateImage(currentProject.id, {
        scene_id: activeScene.id,
        ...(isUUID
          ? { workflow_config_id: effectiveWorkflow }
          : { workflow_type: effectiveWorkflow || 'klein_t2i' }),
        prompt: activePrompt,
        width: effectiveWidth,
        height: effectiveHeight,
        seed: (() => {
          if (frameSubTab === 'first' && imageSeedOverrideFirst && imageSeedFirst) return parseInt(imageSeedFirst);
          if (frameSubTab === 'last' && imageSeedOverrideLast && imageSeedLast) return parseInt(imageSeedLast);
          if (imageSeed) return parseInt(imageSeed);
          return undefined;
        })(),
        reference_asset_ids: updatedRefAssetIds,
        // Tag the frame type so we can filter later
        frame_type: frameSubTab,
        two_pass: twoPass,
      });
      return response.data;
    },
    onSuccess: (data: any) => {
      if (data?.id) {
        useAppStore.getState().addJob({
          id: data.id,
          project_id: data.project_id,
          scene_id: data.scene_id,
          job_type: data.job_type || 'image',
          status: 'pending',
          priority: 0,
          parameters: { frame_type: frameSubTab },
          created_at: data.created_at || new Date().toISOString(),
          retry_count: 0,
        });
      }
    },
  });

  // ─── Shared context builder for enhance calls ─────────────────────
  const buildEnhanceContext = (extra: string) => {
    const parts: string[] = [extra];
    // Concept & style
    if (conceptData?.concept_text) {
      parts.push(`Video concept: ${conceptData.concept_text}`);
    }
    if (conceptData?.style_text) {
      parts.push(`Visual style: ${conceptData.style_text}`);
    }
    // Image direction
    if (conceptData?.image_direction && conceptData.image_direction !== 'none') {
      if (conceptData.image_direction === 'custom') {
        if (conceptData.custom_image_direction) {
          parts.push(`Image direction / art style: ${conceptData.custom_image_direction}`);
        }
      } else {
        const dirLabel = conceptData.image_direction.replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase());
        parts.push(`Image direction / art style: ${dirLabel}`);
      }
    }
    // Character descriptions
    if (conceptCharacters.length > 0) {
      const charBlock = conceptCharacters
        .map((c, i) => `Character ${i + 1}: "${c.name || 'Unnamed'}" — ${c.description || 'no description'}`)
        .join('. ');
      parts.push(`Characters: ${charBlock}`);
    }
    // Scene lyrics — PRIMARY creative driver
    if (sceneLyrics && !sceneLyrics.startsWith('(No lyrics')) {
      parts.push(`SCENE LYRICS (PRIMARY CREATIVE SOURCE — specific objects, people, actions, and settings mentioned here MUST appear visually in the image. The lyrics tell you WHAT to show): "${sceneLyrics}"`);
    }
    // Story flow — scene composition and framing
    if (useStoryFlow && sceneFlowIdea) {
      parts.push(`SCENE STORYBOARD (describes HOW to compose and frame the scene — use this alongside the lyrics to create a visually unique scene that depicts the lyrical content): ${sceneFlowIdea}`);
    }
    return parts.filter(Boolean).join(' | ');
  };

  /**
   * Auto-select characters that are mentioned in the scene's flow idea or
   * the current prompt text. Call before enhance/generate to ensure the
   * correct character reference images are sent.
   *
   * Returns the updated ReferenceState (also sets it in component state).
   */
  const autoSelectCharactersForScene = (): ReferenceState => {
    if (conceptCharacters.length === 0) return activeRefs;

    // Combine text sources to search for character mentions
    const searchText = [
      sceneFlowIdea,
      activePrompt,
      sceneLyrics,
    ].join(' ').toLowerCase();

    if (!searchText.trim()) return activeRefs;

    const maxRefs = frameSubTab === 'first' ? 4 : 3;
    const maxCharRefs = 2; // Klein works best with 1-2 character references
    const currentCharIndices = [...activeRefs.characterIndices];
    let changed = false;

    for (let idx = 0; idx < conceptCharacters.length; idx++) {
      if (currentCharIndices.includes(idx)) continue; // already selected
      const charName = conceptCharacters[idx].name?.toLowerCase().trim();
      if (!charName) continue;

      // Check if character name appears in the text (word boundary aware)
      // Split multi-word names and check if all parts appear
      const nameParts = charName.split(/\s+/).filter(p => p.length > 2);
      const mentioned = nameParts.length > 0 && nameParts.every(part => searchText.includes(part));

      if (mentioned) {
        // Check both total capacity and character limit
        if (currentCharIndices.length < maxCharRefs &&
            currentCharIndices.length + activeRefs.extras.length < maxRefs) {
          currentCharIndices.push(idx);
          changed = true;
        }
      }
    }

    if (changed) {
      const newRefs = { ...activeRefs, characterIndices: currentCharIndices };
      setActiveRefs(newRefs);
      return newRefs;
    }
    return activeRefs;
  };

  const enhancePromptMutation = useMutation({
    mutationFn: async () => {
      if (!currentProject) return;

      // Auto-select characters mentioned in flow/prompt/lyrics
      const updatedRefs = autoSelectCharactersForScene();
      const updatedRefDescriptions = buildRefDescriptions(updatedRefs, conceptCharacters);

      let base = `Image generation model: ${imageModelType}. Optimize the prompt for this specific model's strengths, requirements, and quirks. Scene timing: ${activeScene?.start_time}s to ${activeScene?.end_time}s. Frame: ${frameSubTab}.`;
      if (updatedRefDescriptions) {
        base += ` REFERENCE IMAGES: ${updatedRefDescriptions}. `
          + 'IMPORTANT: In the prompt, refer to each reference image by number using "Image 1", "Image 2", etc. '
          + 'Example: "The tall bearded man in Image 1 stands beside the woman in Image 2". '
          + 'This tells the model which reference image corresponds to which subject in the scene. '
          + 'Describe what each referenced subject is doing, wearing, and how they appear in the scene.';
      }
      // When "Use Last Frame of Previous Scene" is enabled, include previous scene's prompt for visual continuity
      // But skip this if "Ignore Previous Scene Image as Reference" is checked
      if (frameSubTab === 'first' && usePrevSceneLastFrame && !isFirstScene && !ignorePrevSceneRef) {
        const sorted = [...scenes].sort((a, b) => a.order_index - b.order_index);
        const currentIdx = sorted.findIndex(s => s.id === activeScene?.id);
        if (currentIdx > 0) {
          const prevScene = sorted[currentIdx - 1];
          const prevSourceType = prevScene.parameters?.scene_source_type || 'image';
          const prevPrompt = prevScene.prompt || '';
          const prevLastFramePrompt = prevScene.parameters?.last_frame_prompt || '';
          const prevVideoPrompt = prevScene.parameters?.video_prompt || '';
          const prevFlowIdea = prevScene.parameters?.flow_idea || '';
          // Use the most relevant prompt from the previous scene
          const prevContextPrompt = prevSourceType === 'video'
            ? (prevVideoPrompt || prevLastFramePrompt || prevPrompt)
            : (prevLastFramePrompt || prevPrompt);
          base += ` STYLE CONTINUITY FROM PREVIOUS SCENE: The previous scene's image is provided as a style reference. `
            + `Match the overall art style, color palette, lighting mood, and visual tone of the previous scene — `
            + `but the CONTENT of this new scene should be DIFFERENT and UNIQUE. `
            + `Do NOT recreate or closely copy the previous image's composition or subject placement. `
            + `Instead, use the previous scene only as a guide for consistent aesthetic style across the video. `
            + `Focus the prompt on what THIS scene depicts according to its own story flow, lyrics, and concept. `;
          if (prevContextPrompt) {
            base += `PREVIOUS SCENE (style reference only): "${prevContextPrompt}". `;
          }
          if (prevFlowIdea) {
            base += `THIS SCENE'S STORY FLOW: "${prevFlowIdea}". `;
          }
          base += `Generate a prompt for a NEW image that shares the visual style of the previous scene but depicts entirely new content for this scene.`;
        }
      }
      // When enhancing last frame, include the first frame prompt as context for visual continuity
      if (frameSubTab === 'last' && prompt) {
        base += ` FIRST FRAME PROMPT (the starting image this last frame must be visually continuous with): "${prompt}"`;
        // If the first frame is linked from a previous scene, note that for additional context
        if (usePrevSceneLastFrame && !isFirstScene) {
          base += ` NOTE: The first frame for this scene was carried over from the previous scene's last frame, so this last frame should transition from that visual context into the current scene's unique content.`;
        }
      }
      const response = await enhancePrompt(currentProject.id, {
        prompt: activePrompt,
        context: buildEnhanceContext(base),
        frame_type: frameSubTab,
      });
      setActivePrompt(response.data.enhanced_prompt);
      return response.data;
    },
  });

  const enhanceVideoPromptMutation = useMutation({
    mutationFn: async () => {
      if (!currentProject) return;
      const modeContext = videoMode === 'ff_lf'
        ? 'This video uses First Frame / Last Frame mode. The video transitions between two keyframe images.'
        : 'This video uses a single reference image as input.';
      const effectiveCameraAction = cameraAction === 'custom' ? customCameraAction : (cameraAction !== 'none' ? CAMERA_ACTIONS.find(a => a.value === cameraAction)?.label || cameraAction : '');
      const cameraContext = effectiveCameraAction ? ` Requested camera action: ${effectiveCameraAction}. Incorporate this camera movement naturally into the prompt.` : '';
      const sceneDuration = (activeScene?.end_time || 0) - (activeScene?.start_time || 0);
      let base = `Video generation model: ${videoModelType}. ${modeContext} Scene timing: ${activeScene?.start_time}s to ${activeScene?.end_time}s (duration: ${sceneDuration.toFixed(1)}s).${cameraContext}`;
      // If first frame is linked from previous scene, note it for video prompt context
      if (usePrevSceneLastFrame && !isFirstScene) {
        const sorted = [...scenes].sort((a, b) => a.order_index - b.order_index);
        const currentIdx = sorted.findIndex(s => s.id === activeScene?.id);
        if (currentIdx > 0) {
          const prevScene = sorted[currentIdx - 1];
          const prevPrompt = prevScene.parameters?.video_prompt || prevScene.parameters?.last_frame_prompt || prevScene.prompt || '';
          if (prevPrompt) {
            base += ` CONTINUITY: The starting frame of this video is the ending frame of the previous scene. Previous scene described: "${prevPrompt}". The video should visually continue from that context.`;
          }
        }
      }
      const response = await enhancePrompt(currentProject.id, {
        prompt: videoPrompt,
        context: buildEnhanceContext(base),
        is_video: true,
      });
      setVideoPrompt(response.data.enhanced_prompt);
      return response.data;
    },
  });

  const generateVideoMutation = useMutation({
    mutationFn: async () => {
      if (!activeScene || !currentProject) return;

      // Save video prompt + seed override to scene parameters for persistence
      const paramUpdates = {
        ...activeScene.parameters,
        video_prompt: videoPrompt,
        video_seed_override: videoSeedOverride,
        video_seed: videoSeedOverride && videoSeed ? parseInt(videoSeed) : undefined,
      };
      await updateScene(currentProject.id, activeScene.id, { parameters: paramUpdates });
      useAppStore.getState().updateSceneInStore(activeScene.id, { parameters: paramUpdates });

      // Look up asset IDs for first/last frame images and audio
      const storeAssets = useAppStore.getState().assets;
      const sceneParams = activeScene.parameters || {};

      // Find first frame asset by chosen_image_path
      const ffPath = sceneParams.chosen_image_path;
      const ffAsset = ffPath ? storeAssets.find((a: any) => a.rel_path === ffPath) : null;

      // Find last frame asset by chosen_last_frame_path (for FF/LF mode)
      const lfPath = sceneParams.chosen_last_frame_path;
      const lfAsset = lfPath ? storeAssets.find((a: any) => a.rel_path === lfPath) : null;

      // Audio: let backend auto-resolve from scene's audio_clip_path
      // (scene-specific segment takes priority over full song)

      // Determine workflow type based on video mode
      const vMode = sceneParams.video_mode || 'single';
      const isUUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(videoWorkflowType);
      let resolvedWorkflowType = videoWorkflowType || 'ltx_i2v';
      if (!isUUID && vMode === 'v2v_extend') {
        resolvedWorkflowType = 'ltx_v2v_extend';
      } else if (!isUUID && vMode === 'ff_lf' && ffAsset && lfAsset) {
        resolvedWorkflowType = 'ltx_fflf';
      }

      const response = await generateVideo(currentProject.id, {
        scene_id: activeScene.id,
        ...(isUUID
          ? { workflow_config_id: videoWorkflowType }
          : { workflow_type: resolvedWorkflowType }),
        prompt: videoPrompt,
        width: effectiveWidth,
        height: effectiveHeight,
        duration: videoDuration,
        framerate: videoFramerate,
        first_frame_asset_id: ffAsset?.id,
        last_frame_asset_id: lfAsset?.id,
        skip_audio_mux: skipAudioMux,
        seed: videoSeedOverride && videoSeed ? parseInt(videoSeed) : undefined,
        // audio_asset_id omitted — backend auto-resolves scene-specific audio clip
      });
      return response.data;
    },
    onSuccess: (data: any) => {
      if (data?.id) {
        useAppStore.getState().addJob({
          id: data.id,
          project_id: data.project_id,
          scene_id: data.scene_id,
          job_type: data.job_type || 'video',
          status: 'pending',
          priority: 0,
          parameters: {},
          created_at: data.created_at || new Date().toISOString(),
          retry_count: 0,
        });
      }
    },
  });

  const setStemsMutation = useMutation({
    mutationFn: async () => {
      if (!activeScene || !currentProject) return;
      await setSceneStems(currentProject.id, activeScene.id, {
        vocals: vocalsMix, drums: drumsMix, bass: bassMix, other: otherMix,
      });
    },
  });

  const mixStemsMutation = useMutation({
    mutationFn: async () => {
      if (!activeScene || !currentProject) return;
      const response = await mixStems(currentProject.id, activeScene.id);
      return response.data;
    },
  });

  if (!activeScene) {
    return (
      <div className="h-full flex items-center justify-center text-gray-400">
        <div className="text-center">
          <p className="text-sm">Select a scene to edit</p>
          <p className="text-xs text-gray-500 mt-2">Choose a scene from the left panel</p>
        </div>
      </div>
    );
  }

  const safeWorkflows = workflows || [];
  const safeAssets = assets || [];
  const videoWorkflows = safeWorkflows.filter(w => w.workflow_type === 'video');

  // Auto workflow type based on reference count
  const computedWorkflowType = autoWorkflowType(activeRefs, conceptCharacters);
  const refAssetIds = collectRefAssetIds(activeRefs, conceptCharacters, safeAssets);
  // Note: refDescriptions is computed inside enhance/generate mutations via autoSelectCharactersForScene()

  // Check if current version is saved
  const currentVersion = activeVersions[activeHistoryIndex];
  const isCurrentSaved = currentVersion?.output_path && activeChosenPath === currentVersion.output_path;

  // First frame thumbnail for the Last Frame sub-tab reference indicator
  const firstFrameUrl = chosenFirstFramePath ? `/api/files/${chosenFirstFramePath}` : null;

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Tabs + Scene Source toggle + Collapse toggle */}
      <div className="flex items-center gap-2 p-3 border-b border-gray-800 bg-gray-950">
        <div className="flex gap-2 flex-1 overflow-x-auto">
          {(['image', 'video', 'movement', 'transitions', 'stems', 'lyrics', 'tools', 'prompt'] as Tab[]).filter((tab) => {
            const mode = currentProject?.mode;
            if (mode === 'narration_images') {
              // Hide video, transitions, stems tabs
              return !['video', 'transitions', 'stems'].includes(tab);
            }
            if (mode === 'narration_video') {
              // Hide stems tab (no stem separation for narration)
              return tab !== 'stems';
            }
            return true; // music_video: show all
          }).map((tab) => {
            const isDisabled = false;
            const tabLabels: Record<Tab, string> = {
              image: 'Image',
              video: 'Video',
              movement: 'Movement',
              transitions: 'Transitions',
              stems: 'Stems',
              lyrics: 'Lyrics',
              tools: 'Tools',
              prompt: 'Prompt',
            };
            return (
              <button
                key={tab}
                onClick={() => !isDisabled && setActiveTab(tab)}
                disabled={isDisabled}
                className={`px-4 py-2 rounded text-sm font-medium transition-colors whitespace-nowrap ${
                  isDisabled
                    ? 'bg-gray-800/50 text-gray-600 cursor-not-allowed'
                    : activeTab === tab
                      ? 'bg-blue-600 text-white'
                      : 'bg-gray-800 text-gray-400 hover:text-white'
                }`}
                title={isDisabled ? 'Switch to Scenes mode to use this tab' : undefined}
              >
                {tabLabels[tab]}
                {tab === 'prompt' && activeScene?.parameters?.two_pass_scene_prompt && (
                  <span className="ml-1 w-2 h-2 rounded-full bg-green-400 inline-block" title="Has generated prompts" />
                )}
              </button>
            );
          })}
        </div>
        {/* Scene Source toggle — hidden for narration_images (always image) */}
        {activeScene && currentProject?.mode !== 'narration_images' && (
          <div className="flex items-center gap-2 flex-shrink-0 ml-1 pl-3 border-l border-gray-700">
            <span className="text-[10px] text-gray-500 font-medium uppercase tracking-wider">Source:</span>
            <div className="flex gap-0.5 p-0.5 bg-gray-800 rounded">
              <button
                onClick={() => saveSceneSourceType('image')}
                className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                  sceneSourceType === 'image'
                    ? 'bg-green-600 text-white'
                    : 'text-gray-400 hover:text-white'
                }`}
                title="Active image + effects used in export"
              >
                Image
              </button>
              <button
                onClick={() => saveSceneSourceType('video')}
                className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                  sceneSourceType === 'video'
                    ? 'bg-purple-600 text-white'
                    : 'text-gray-400 hover:text-white'
                }`}
                title="Active video used in export"
              >
                Video
              </button>
            </div>
          </div>
        )}
        {onToggleCollapse && (
          <button
            onClick={onToggleCollapse}
            className="p-1.5 text-gray-400 hover:text-white bg-gray-800 rounded transition-colors flex-shrink-0"
            title={collapsed ? 'Expand editor' : 'Collapse editor'}
          >
            {collapsed ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </button>
        )}
      </div>

      {/* Content — collapsible */}
      {!collapsed && (
        <>
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {/* ══════════ IMAGE TAB ══════════ */}
          {activeTab === 'image' && (
            <>
              {/* First Frame / Last Frame sub-tabs */}
              <div className="flex gap-1 p-1 bg-gray-800 rounded">
                <button
                  onClick={() => setFrameSubTab('first')}
                  className={`flex-1 px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                    frameSubTab === 'first'
                      ? 'bg-blue-600 text-white'
                      : 'text-gray-400 hover:text-white'
                  }`}
                >
                  First Frame
                </button>
                <button
                  onClick={() => setFrameSubTab('last')}
                  className={`flex-1 px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                    frameSubTab === 'last'
                      ? 'bg-blue-600 text-white'
                      : 'text-gray-400 hover:text-white'
                  }`}
                >
                  Last Frame
                  {chosenLastFramePath && (
                    <span className="ml-1.5 inline-block w-1.5 h-1.5 bg-green-400 rounded-full" />
                  )}
                </button>
              </div>

              {/* "Use Last Frame of Previous Scene" toggle on First Frame sub-tab */}
              {frameSubTab === 'first' && !isFirstScene && (
                <div className="space-y-2">
                  <label className="flex items-center gap-2 text-xs cursor-pointer group">
                    <input
                      type="checkbox"
                      checked={usePrevSceneLastFrame}
                      onChange={(e) => handleSetUsePrevSceneLastFrame(e.target.checked)}
                      className="accent-amber-500"
                    />
                    <Link size={13} className={usePrevSceneLastFrame ? 'text-amber-400' : 'text-gray-500 group-hover:text-gray-400'} />
                    <span className={usePrevSceneLastFrame ? 'text-amber-300' : 'text-gray-400 group-hover:text-gray-300'}>
                      Use Last Frame of Previous Scene
                    </span>
                    {loadingPrevFrame && (
                      <span className="text-gray-500 animate-pulse">Loading...</span>
                    )}
                  </label>

                  {/* Preview of the previous scene's last frame when toggle is on */}
                  {usePrevSceneLastFrame && (
                    <div className="flex items-center gap-3 p-2.5 bg-amber-900/20 rounded border border-amber-800/40">
                      {prevSceneLastFramePath ? (
                        <img
                          src={`/api/files/${prevSceneLastFramePath}`}
                          alt="Previous scene last frame"
                          className="w-14 h-14 object-cover rounded border border-amber-700/50"
                          onError={handleImgError}
                        />
                      ) : (
                        <div className="w-14 h-14 bg-gray-700 rounded flex items-center justify-center">
                          <ImageIcon size={16} className="text-gray-500" />
                        </div>
                      )}
                      <div className="text-xs flex-1">
                        <span className="text-amber-400 font-medium">Linked to Previous Scene</span>
                        {prevSceneLastFramePath ? (
                          <p className="text-gray-400 mt-0.5">
                            Using the last frame from the previous scene as this scene's first frame.
                            The Last Frame tab will use this as its generation reference.
                          </p>
                        ) : (
                          <p className="text-yellow-500 mt-0.5">
                            Previous scene has no last frame available yet. Generate a video or set a last frame image in the previous scene.
                          </p>
                        )}
                        <button
                          onClick={fetchPrevSceneLastFrame}
                          className="mt-1 text-amber-400 hover:text-amber-300 underline"
                          disabled={loadingPrevFrame}
                        >
                          {loadingPrevFrame ? 'Refreshing...' : 'Refresh'}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* "Ignore Previous Scene Image as Reference" toggle on First Frame sub-tab */}
              {frameSubTab === 'first' && !isFirstScene && (
                <label className="flex items-center gap-2 text-xs cursor-pointer group">
                  <input
                    type="checkbox"
                    checked={ignorePrevSceneRef}
                    onChange={(e) => handleSetIgnorePrevSceneRef(e.target.checked)}
                    className="accent-red-500"
                  />
                  <Unlink size={13} className={ignorePrevSceneRef ? 'text-red-400' : 'text-gray-500 group-hover:text-gray-400'} />
                  <span className={ignorePrevSceneRef ? 'text-red-300' : 'text-gray-400 group-hover:text-gray-300'}>
                    Ignore Previous Scene Image as Reference
                  </span>
                </label>
              )}

              {/* "Two-Pass Generation" toggle — show when on first frame and characters with images exist */}
              {frameSubTab === 'first' && conceptCharacters.some(c => c.image_path) && (
                <label className="flex items-center gap-2 text-xs cursor-pointer group">
                  <input
                    type="checkbox"
                    checked={twoPass}
                    onChange={(e) => handleSetTwoPass(e.target.checked)}
                    className="accent-blue-500"
                  />
                  <Zap size={13} className={twoPass ? 'text-blue-400' : 'text-gray-500 group-hover:text-gray-400'} />
                  <span className={twoPass ? 'text-blue-300' : 'text-gray-400 group-hover:text-gray-300'}>
                    Two-Pass Generation
                  </span>
                </label>
              )}

              {/* Reference indicator for Last Frame tab */}
              {frameSubTab === 'last' && (
                <div className="flex items-center gap-3 p-2.5 bg-gray-800/60 rounded border border-gray-700">
                  {firstFrameUrl ? (
                    <img
                      src={firstFrameUrl}
                      alt="First frame reference"
                      className="w-12 h-12 object-cover rounded border border-gray-600"
                      onError={handleImgError}
                    />
                  ) : (
                    <div className="w-12 h-12 bg-gray-700 rounded flex items-center justify-center">
                      <ImageIcon size={16} className="text-gray-500" />
                    </div>
                  )}
                  <div className="text-xs">
                    <span className="text-gray-400">Reference: </span>
                    {firstFrameUrl ? (
                      <span className="text-green-400">First frame set</span>
                    ) : (
                      <span className="text-yellow-400">Set a first frame first</span>
                    )}
                    <p className="text-gray-500 mt-0.5">The last frame generates a complementary end-point image</p>
                    {usePrevSceneLastFrame && prevSceneLastFramePath && (
                      <p className="text-amber-400 mt-0.5">First frame is linked from previous scene</p>
                    )}
                  </div>
                </div>
              )}

              <div>
                <label className="block text-sm font-medium mb-2">Prompt</label>
                <textarea
                  value={activePrompt}
                  onChange={(e) => setActivePrompt(e.target.value)}
                  placeholder={frameSubTab === 'first'
                    ? 'Describe the opening image for this scene...'
                    : 'Describe the closing image for this scene...'
                  }
                  className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 h-20"
                />
              </div>

              <div>
                <label className="block text-sm font-medium mb-2">Negative Prompt</label>
                <textarea
                  value={activeNegPrompt}
                  onChange={(e) => setActiveNegPrompt(e.target.value)}
                  placeholder="Things to avoid in the image..."
                  className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 h-16"
                />
              </div>

              <div>
                <label className="block text-sm font-medium mb-2">
                  Workflow
                  <span className="text-[10px] text-blue-400 ml-2 font-normal">
                    Auto-selected based on references
                  </span>
                </label>
                <div className="w-full px-3 py-2 bg-gray-800/60 border border-gray-700/50 rounded text-gray-300 text-sm">
                  {computedWorkflowType.replace('klein_', 'FLUX Klein – ').replace('t2i', 'Text to Image').replace('1ref', '1 Reference').replace('2ref', '2 References').replace('3ref', '3 References').replace('4ref', '4 References')}
                  {refAssetIds.length > 0 && (
                    <span className="text-gray-500 ml-1">({refAssetIds.length} ref{refAssetIds.length !== 1 ? 's' : ''})</span>
                  )}
                </div>
              </div>

              {/* Resolution — project default or scene override */}
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium">
                    Resolution
                    {!overrideResolution && (
                      <span className="text-xs text-gray-500 ml-1.5">({projectWidth} × {projectHeight} — project default)</span>
                    )}
                  </label>
                  <label className="flex items-center gap-1.5 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={overrideResolution}
                      onChange={(e) => setOverrideResolution(e.target.checked)}
                      className="w-3.5 h-3.5 rounded border-gray-600 bg-gray-800 text-blue-500 focus:ring-blue-500 focus:ring-offset-0"
                    />
                    <span className="text-xs text-gray-400">Override</span>
                  </label>
                </div>
                {overrideResolution && (
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs text-gray-500 mb-1">Width</label>
                      <input
                        type="number"
                        value={imageWidth}
                        onChange={(e) => setImageWidth(parseInt(e.target.value))}
                        min="256"
                        max="4096"
                        step="64"
                        className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 focus:outline-none focus:border-blue-500"
                      />
                    </div>
                    <div>
                      <label className="block text-xs text-gray-500 mb-1">Height</label>
                      <input
                        type="number"
                        value={imageHeight}
                        onChange={(e) => setImageHeight(parseInt(e.target.value))}
                        min="256"
                        max="4096"
                        step="64"
                        className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 focus:outline-none focus:border-blue-500"
                      />
                    </div>
                  </div>
                )}
              </div>

              <div>
                <div className="flex items-center gap-2 mb-1">
                  <label className="text-sm font-medium">Seed</label>
                  <label className="flex items-center gap-1 text-xs text-gray-400 cursor-pointer ml-auto">
                    <input
                      type="checkbox"
                      checked={frameSubTab === 'first' ? imageSeedOverrideFirst : imageSeedOverrideLast}
                      onChange={(e) => frameSubTab === 'first' ? setImageSeedOverrideFirst(e.target.checked) : setImageSeedOverrideLast(e.target.checked)}
                      className="w-3 h-3"
                    />
                    Override
                  </label>
                </div>
                <input
                  type="text"
                  value={frameSubTab === 'first' ? (imageSeedOverrideFirst ? imageSeedFirst : imageSeed) : (imageSeedOverrideLast ? imageSeedLast : imageSeed)}
                  onChange={(e) => {
                    if (frameSubTab === 'first') {
                      setImageSeedOverrideFirst(true);
                      setImageSeedFirst(e.target.value);
                    } else {
                      setImageSeedOverrideLast(true);
                      setImageSeedLast(e.target.value);
                    }
                  }}
                  readOnly={frameSubTab === 'first' ? !imageSeedOverrideFirst : !imageSeedOverrideLast}
                  placeholder={frameSubTab === 'first' ? (imageSeedOverrideFirst ? 'Enter seed' : 'Random (auto)') : (imageSeedOverrideLast ? 'Enter seed' : 'Random (auto)')}
                  className={`w-full px-3 py-1.5 bg-gray-800 border border-gray-700 rounded text-gray-100 text-sm placeholder-gray-500 focus:outline-none focus:border-blue-500 ${(frameSubTab === 'first' ? !imageSeedOverrideFirst : !imageSeedOverrideLast) ? 'opacity-60' : ''}`}
                />
              </div>

              {/* Reference images — characters + extras */}
              {currentProject && (
                <ReferenceSelector
                  characters={conceptCharacters}
                  value={activeRefs}
                  onChange={setActiveRefs}
                  frameType={frameSubTab}
                  projectId={currentProject.id}
                />
              )}

              {/* Use Story Flow checkbox */}
              {sceneFlowIdea && (
                <label className="flex items-center gap-2 px-1 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={useStoryFlow}
                    onChange={(e) => handleSetUseStoryFlow(e.target.checked)}
                    className="w-3.5 h-3.5 accent-purple-500"
                  />
                  <span className="text-xs text-gray-400">Use Story Flow</span>
                  <span className="text-[10px] text-gray-600 truncate flex-1" title={sceneFlowIdea}>
                    — {sceneFlowIdea.slice(0, 60)}{sceneFlowIdea.length > 60 ? '...' : ''}
                  </span>
                </label>
              )}

              <div className="flex gap-2">
                <button
                  onClick={() => enhancePromptMutation.mutate()}
                  disabled={enhancePromptMutation.isPending}
                  className="flex-1 px-4 py-2 bg-purple-600 hover:bg-purple-700 rounded text-sm font-medium transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
                >
                  <Wand2 size={16} />
                  {enhancePromptMutation.isPending ? 'Enhancing...' : activePrompt ? 'Enhance' : 'Generate Prompt'}
                </button>
                <button
                  onClick={() => generateImageMutation.mutate()}
                  disabled={!activePrompt || generateImageMutation.isPending}
                  className="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded text-sm font-medium transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
                >
                  <Zap size={16} />
                  {generateImageMutation.isPending ? 'Generating...' : 'Generate'}
                </button>
                <button
                  onClick={() => imageUploadRef.current?.click()}
                  disabled={uploadingImage}
                  className="px-3 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm font-medium transition-colors disabled:opacity-50 flex items-center justify-center gap-1.5"
                  title="Upload your own image"
                >
                  <Upload size={16} />
                  {uploadingImage ? '...' : ''}
                </button>
                <input
                  ref={imageUploadRef}
                  type="file"
                  accept="image/*"
                  onChange={handleImageUpload}
                  className="hidden"
                />
              </div>

              {/* ─── Image Gallery ─────────────────────────────────── */}
              {activeVersions.length > 0 && (() => {
                const ver = activeVersions[activeHistoryIndex];
                const imageUrl = ver?.output_path ? `/api/files/${ver.output_path}` : null;
                return (
                  <div className="space-y-2 pt-2 border-t border-gray-800">
                    <div
                      className="relative bg-gray-900 rounded overflow-hidden flex items-center justify-center cursor-pointer group/img"
                      style={{ minHeight: '160px' }}
                      onClick={() => setLightboxOpen(true)}
                    >
                      {imageUrl ? (
                        <>
                          <img
                            src={imageUrl}
                            alt={`Generation ${activeHistoryIndex + 1}`}
                            className="max-w-full max-h-[300px] object-contain"
                            loading="lazy"
                            onError={handleImgError}
                          />
                          <div className="absolute inset-0 bg-black/0 group-hover/img:bg-black/30 transition-colors flex items-center justify-center">
                            <span className="text-white text-sm font-medium opacity-0 group-hover/img:opacity-100 transition-opacity bg-black/60 px-3 py-1.5 rounded">
                              Click to enlarge
                            </span>
                          </div>
                        </>
                      ) : (
                        <div className="flex flex-col items-center gap-2 text-gray-500">
                          <ImageIcon size={32} />
                          <span className="text-xs">No preview available</span>
                        </div>
                      )}
                    </div>

                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <button
                          onClick={() => setActiveHistoryIndex(Math.max(0, activeHistoryIndex - 1))}
                          className="p-1.5 text-gray-400 hover:text-white disabled:opacity-50"
                          disabled={activeHistoryIndex === 0}
                        >
                          <ChevronLeft size={18} />
                        </button>
                        <span className="text-sm text-gray-400">
                          {activeHistoryIndex + 1} / {activeVersions.length}
                        </span>
                        <button
                          onClick={() => setActiveHistoryIndex(Math.min(activeHistoryIndex + 1, activeVersions.length - 1))}
                          className="p-1.5 text-gray-400 hover:text-white disabled:opacity-50"
                          disabled={activeHistoryIndex === activeVersions.length - 1}
                        >
                          <ChevronRight size={18} />
                        </button>
                      </div>

                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => setDetailsVersion(ver)}
                          className="px-2.5 py-1.5 rounded text-xs font-medium transition-colors flex items-center gap-1 bg-gray-600 hover:bg-gray-500 text-white"
                          title="Generation Details"
                        >
                          <Info size={14} />
                        </button>
                        {ver?.output_path && (
                          <a
                            href={`/api/files/${ver.output_path}`}
                            download={ver.output_path.split('/').pop() || 'image'}
                            className="px-2.5 py-1.5 rounded text-xs font-medium transition-colors flex items-center gap-1 bg-gray-600 hover:bg-gray-500 text-white"
                            title="Download image"
                          >
                            <Download size={14} />
                          </a>
                        )}
                        <button
                          onClick={() => handleDeleteVersion(ver, activeHistoryIndex)}
                          className="px-2.5 py-1.5 rounded text-xs font-medium transition-colors flex items-center gap-1 bg-red-600 hover:bg-red-700 text-white"
                          title="Delete this image"
                        >
                          <Trash2 size={14} />
                        </button>
                        <button
                          onClick={() => handleSaveAsPreview(ver)}
                          disabled={savingPreview || !!isCurrentSaved}
                          className={`px-3 py-1.5 rounded text-xs font-medium transition-colors flex items-center gap-1.5 ${
                            isCurrentSaved
                              ? 'bg-green-900/30 text-green-400 border border-green-800'
                              : 'bg-green-600 hover:bg-green-700 text-white'
                          } disabled:opacity-60`}
                        >
                          {isCurrentSaved ? (
                            <><Check size={14} /> Saved</>
                          ) : (
                            <><Save size={14} /> {savingPreview ? 'Saving...' : `Save as ${frameSubTab === 'first' ? 'First' : 'Last'} Frame`}</>
                          )}
                        </button>
                      </div>
                    </div>

                    {/* Two-Pass: View Original + Rerun Pass 2 buttons */}
                    {frameSubTab === 'first' && activeScene?.parameters?.two_pass_base_image_path && (
                      <div className="flex items-center gap-2 pt-1">
                        <button
                          onClick={() => setTwoPassBaseViewOpen(true)}
                          className="flex-1 px-2.5 py-1.5 rounded text-xs font-medium transition-colors flex items-center justify-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white"
                          title="View the Pass 1 scene image (before character compositing)"
                        >
                          <Eye size={14} /> View Original
                        </button>
                        <button
                          onClick={handleRerunPass2}
                          disabled={rerunningPass2}
                          className="flex-1 px-2.5 py-1.5 rounded text-xs font-medium transition-colors flex items-center justify-center gap-1.5 bg-amber-600 hover:bg-amber-700 text-white disabled:opacity-60"
                          title="Rerun Pass 2 (character compositing) using the existing scene image"
                        >
                          <RefreshCw size={14} className={rerunningPass2 ? 'animate-spin' : ''} /> {rerunningPass2 ? 'Submitting...' : 'Rerun Pass 2'}
                        </button>
                      </div>
                    )}

                    {ver && (
                      <div className="text-[10px] text-gray-500 text-center">
                        {ver.status === 'completed' ? 'Completed' : ver.status}
                        {ver.completed_at && (<> · {new Date(ver.completed_at).toLocaleString()}</>)}
                      </div>
                    )}
                  </div>
                );
              })()}

              {activeVersions.length === 0 && (
                <div className="pt-2 border-t border-gray-800">
                  <div className="bg-gray-900 rounded flex flex-col items-center justify-center py-8 text-gray-500">
                    <ImageIcon size={32} />
                    <span className="text-xs mt-2">
                      No {frameSubTab === 'first' ? 'first' : 'last'} frame images yet
                    </span>
                    <span className="text-xs text-gray-600 mt-1">Generate one or upload your own</span>
                  </div>
                </div>
              )}
            </>
          )}

          {/* ══════════ VIDEO TAB ══════════ */}
          {activeTab === 'video' && (
            <>
              {/* Video mode toggle */}
              <div>
                <label className="block text-sm font-medium mb-2">Video Mode</label>
                <div className="flex gap-1 p-1 bg-gray-800 rounded">
                  <button
                    onClick={() => handleSetVideoMode('single')}
                    className={`flex-1 px-3 py-1.5 rounded text-xs font-medium transition-colors flex items-center justify-center gap-1 ${
                      videoMode === 'single'
                        ? 'bg-blue-600 text-white'
                        : 'text-gray-400 hover:text-white'
                    }`}
                  >
                    <ImageIcon size={13} />
                    Single Image
                  </button>
                  <button
                    onClick={() => handleSetVideoMode('ff_lf')}
                    className={`flex-1 px-3 py-1.5 rounded text-xs font-medium transition-colors flex items-center justify-center gap-1 ${
                      videoMode === 'ff_lf'
                        ? 'bg-blue-600 text-white'
                        : 'text-gray-400 hover:text-white'
                    }`}
                  >
                    <Film size={13} />
                    FF / LF
                  </button>
                  <button
                    onClick={() => !isFirstScene && handleSetVideoMode('v2v_extend')}
                    disabled={isFirstScene}
                    className={`flex-1 px-3 py-1.5 rounded text-xs font-medium transition-colors flex items-center justify-center gap-1 ${
                      isFirstScene
                        ? 'text-gray-600 cursor-not-allowed'
                        : videoMode === 'v2v_extend'
                          ? 'bg-purple-600 text-white'
                          : 'text-gray-400 hover:text-white'
                    }`}
                    title={isFirstScene ? 'V2V Extend requires a previous scene — not available for the first scene' : 'V2V Extend: Uses latent conditioning from previous scene\'s video for seamless transitions'}
                  >
                    <Link size={13} />
                    V2V Extend
                  </button>
                </div>
              </div>

              {/* Reference frame indicator */}
              <div className="p-3 bg-gray-800/60 rounded border border-gray-700">
                {videoMode === 'v2v_extend' ? (
                  <div className="flex items-center gap-3">
                    <div className="w-16 h-10 bg-purple-900/40 rounded flex items-center justify-center border border-purple-700/50">
                      <Link size={14} className="text-purple-400" />
                    </div>
                    <div className="text-xs">
                      <span className="text-purple-300 font-medium">V2V Extend: </span>
                      {isFirstScene
                        ? <span className="text-yellow-400">First scene — will generate normally (no previous video to extend from)</span>
                        : <span className="text-green-400">Will extend from previous scene&apos;s video using latent conditioning</span>
                      }
                    </div>
                  </div>
                ) : videoMode === 'single' ? (
                  <div className="flex items-center gap-3">
                    {firstFrameUrl ? (
                      <img src={firstFrameUrl} alt="First frame" className="w-16 h-10 object-cover rounded border border-gray-600" onError={handleImgError} />
                    ) : (
                      <div className="w-16 h-10 bg-gray-700 rounded flex items-center justify-center">
                        <ImageIcon size={14} className="text-gray-500" />
                      </div>
                    )}
                    <div className="text-xs">
                      <span className="text-gray-300 font-medium">Reference image: </span>
                      {firstFrameUrl
                        ? <span className="text-green-400">First frame set</span>
                        : <span className="text-yellow-400">No first frame — set one in the Image tab</span>
                      }
                    </div>
                  </div>
                ) : (
                  <div className="flex items-center gap-3">
                    <div className="flex gap-1">
                      {firstFrameUrl ? (
                        <img src={firstFrameUrl} alt="First frame" className="w-12 h-8 object-cover rounded border border-gray-600" onError={handleImgError} />
                      ) : (
                        <div className="w-12 h-8 bg-gray-700 rounded flex items-center justify-center">
                          <span className="text-[8px] text-gray-500">FF</span>
                        </div>
                      )}
                      {chosenLastFramePath ? (
                        <img src={`/api/files/${chosenLastFramePath}`} alt="Last frame" className="w-12 h-8 object-cover rounded border border-gray-600" onError={handleImgError} />
                      ) : (
                        <div className="w-12 h-8 bg-gray-700 rounded flex items-center justify-center">
                          <span className="text-[8px] text-gray-500">LF</span>
                        </div>
                      )}
                    </div>
                    <div className="text-xs">
                      <span className="text-gray-300 font-medium">FF/LF: </span>
                      {firstFrameUrl && chosenLastFramePath
                        ? <span className="text-green-400">Both frames set</span>
                        : firstFrameUrl
                          ? <span className="text-yellow-400">Last frame missing — set one in Image tab → Last Frame</span>
                          : <span className="text-yellow-400">Set first & last frames in the Image tab</span>
                      }
                    </div>
                  </div>
                )}
              </div>

              {/* "Use LF of Previous Video For FF" checkbox — only in FF/LF mode */}
              {videoMode === 'ff_lf' && (
                <label
                  className={`flex items-center gap-3 p-3 rounded border transition-colors ${
                    isFirstScene
                      ? 'bg-gray-800/40 border-gray-700/50 cursor-not-allowed opacity-50'
                      : 'bg-gray-800 border-gray-700 cursor-pointer hover:bg-gray-750'
                  }`}
                  title={isFirstScene ? 'Not available for the first scene — there is no previous video' : ''}
                >
                  <input
                    type="checkbox"
                    checked={usePrevLfAsFf}
                    onChange={(e) => handleSetUsePrevLfAsFf(e.target.checked)}
                    disabled={isFirstScene}
                    className="w-4 h-4 accent-blue-500"
                  />
                  <div>
                    <span className="text-sm text-gray-200">Use LF of Previous Video For FF</span>
                    <p className="text-[10px] text-gray-500 mt-0.5">
                      {isFirstScene
                        ? 'Not available — this is the first scene in the timeline'
                        : 'Instead of using the First Frame from the Image tab, use the last frame of the previous scene\'s video'
                      }
                    </p>
                  </div>
                </label>
              )}

              {/* Camera Action Dropdown */}
              <div>
                <label className="block text-sm font-medium mb-2">Requested Camera Action</label>
                <select
                  value={cameraAction}
                  onChange={(e) => handleCameraActionChange(e.target.value)}
                  className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 focus:outline-none focus:border-blue-500"
                >
                  {CAMERA_ACTIONS.map((action) => (
                    <option key={action.value} value={action.value}>{action.label}</option>
                  ))}
                </select>
                {cameraAction === 'custom' && (
                  <input
                    type="text"
                    value={customCameraAction}
                    onChange={(e) => handleCustomCameraActionChange(e.target.value)}
                    placeholder="Describe custom camera motion..."
                    className="w-full mt-2 px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500"
                  />
                )}
                {cameraAction !== 'none' && (
                  <p className="text-[10px] text-gray-500 mt-1">
                    This camera action will be included in the LLM enhance context for video prompts.
                  </p>
                )}
              </div>

              <div>
                <label className="block text-sm font-medium mb-2">Prompt</label>
                <textarea
                  value={videoPrompt}
                  onChange={(e) => setVideoPrompt(e.target.value)}
                  placeholder="Describe the video animation..."
                  className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 h-20"
                />
              </div>

              <div>
                <label className="block text-sm font-medium mb-2">Workflow</label>
                {(() => {
                  const customVideoWorkflows = videoWorkflows.filter(w => !w.is_default);
                  if (customVideoWorkflows.length === 0) {
                    /* No custom workflows — show locked display matching video mode */
                    return (
                      <div className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-300 text-sm">
                        {videoMode === 'v2v_extend' ? 'LTX – V2V Extend' : videoMode === 'ff_lf' ? 'LTX – First/Last Frame' : 'LTX – Image to Video'}
                      </div>
                    );
                  }
                  /* Custom workflows uploaded — allow user selection alongside built-ins */
                  return (
                    <select
                      value={videoWorkflowType}
                      onChange={(e) => setVideoWorkflowType(e.target.value)}
                      className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 focus:outline-none focus:border-blue-500"
                    >
                      <option value={videoMode === 'v2v_extend' ? 'ltx_v2v_extend' : videoMode === 'ff_lf' ? 'ltx_fflf' : 'ltx_i2v'}>
                        {videoMode === 'v2v_extend' ? 'LTX – V2V Extend' : videoMode === 'ff_lf' ? 'LTX – First/Last Frame' : 'LTX – Image to Video'}
                      </option>
                      {customVideoWorkflows.map(w => (
                        <option key={w.id} value={w.id}>{w.name}</option>
                      ))}
                    </select>
                  );
                })()}
                <p className="text-[10px] text-gray-500 mt-1">
                  {videoMode === 'v2v_extend'
                    ? 'V2V Extend feeds previous video as latent conditioning — no single-frame bottleneck'
                    : videoMode === 'ff_lf'
                    ? 'First/Last Frame mode requires the FF/LF workflow'
                    : 'Single Image mode uses the Image-to-Video workflow'
                  }
                </p>
              </div>

              <div>
                <label className="block text-sm font-medium mb-1">Duration (seconds)</label>
                {activeScene && activeScene.start_time != null && activeScene.end_time != null && (
                  <div className="text-[10px] text-gray-500 mb-1">
                    Scene length: {(activeScene.end_time - activeScene.start_time).toFixed(2)}s
                  </div>
                )}
                <input
                  type="number"
                  value={videoDuration}
                  onChange={(e) => setVideoDuration(parseFloat(e.target.value))}
                  min="0.5"
                  max="300"
                  step="0.1"
                  className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 focus:outline-none focus:border-blue-500"
                />
                <p className="text-[10px] text-gray-500 mt-2">
                  Framerate is set globally in Settings (current: {videoFramerate} fps)
                </p>
              </div>

              {/* Resolution — project default or scene override (same toggle as Image tab) */}
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium">
                    Resolution
                    {!overrideResolution && (
                      <span className="text-xs text-gray-500 ml-1.5">({projectWidth} × {projectHeight} — project default)</span>
                    )}
                  </label>
                  <label className="flex items-center gap-1.5 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={overrideResolution}
                      onChange={(e) => setOverrideResolution(e.target.checked)}
                      className="w-3.5 h-3.5 rounded border-gray-600 bg-gray-800 text-blue-500 focus:ring-blue-500 focus:ring-offset-0"
                    />
                    <span className="text-xs text-gray-400">Override</span>
                  </label>
                </div>
                {overrideResolution && (
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs text-gray-500 mb-1">Width</label>
                      <input
                        type="number"
                        value={imageWidth}
                        onChange={(e) => setImageWidth(parseInt(e.target.value))}
                        min="256"
                        max="4096"
                        step="64"
                        className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 focus:outline-none focus:border-blue-500"
                      />
                    </div>
                    <div>
                      <label className="block text-xs text-gray-500 mb-1">Height</label>
                      <input
                        type="number"
                        value={imageHeight}
                        onChange={(e) => setImageHeight(parseInt(e.target.value))}
                        min="256"
                        max="4096"
                        step="64"
                        className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 focus:outline-none focus:border-blue-500"
                      />
                    </div>
                  </div>
                )}
              </div>

              {/* Use Story Flow checkbox */}
              {sceneFlowIdea && (
                <label className="flex items-center gap-2 px-1 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={useStoryFlow}
                    onChange={(e) => handleSetUseStoryFlow(e.target.checked)}
                    className="w-3.5 h-3.5 accent-purple-500"
                  />
                  <span className="text-xs text-gray-400">Use Story Flow</span>
                  <span className="text-[10px] text-gray-600 truncate flex-1" title={sceneFlowIdea}>
                    — {sceneFlowIdea.slice(0, 60)}{sceneFlowIdea.length > 60 ? '...' : ''}
                  </span>
                </label>
              )}

              {/* Skip Audio Mux Checkbox */}
              <label className="flex items-center gap-2 px-3 py-2 bg-gray-800 hover:bg-gray-700 rounded cursor-pointer text-sm text-gray-300 transition-colors">
                <input
                  type="checkbox"
                  checked={skipAudioMux}
                  onChange={(e) => setSkipAudioMux(e.target.checked)}
                  className="w-4 h-4 accent-blue-500"
                />
                <span>Keep Model Audio (skip mux)</span>
                <span className="text-xs text-gray-500 ml-auto">(better for lip-sync testing)</span>
              </label>

              {/* Lipsync Toggle — hidden for narration_images */}
              {currentProject?.mode !== 'narration_images' && <div className="space-y-1">
                <label className="flex items-center gap-2 px-3 py-2 bg-gray-800 hover:bg-gray-700 rounded cursor-pointer text-sm text-gray-300 transition-colors">
                  <input
                    type="checkbox"
                    checked={lipsyncEnabled}
                    onChange={(e) => handleSetLipsync(e.target.checked)}
                    className="w-4 h-4 accent-green-500"
                  />
                  <span>Lipsync</span>
                  <span className="text-xs text-gray-500 ml-auto">(boost audio-video sync for singing)</span>
                </label>
                {lipsyncEnabled && (
                  <label className="flex items-center gap-2 px-3 py-1.5 ml-6 bg-gray-750 hover:bg-gray-700 rounded cursor-pointer text-xs text-gray-400 transition-colors">
                    <input
                      type="checkbox"
                      checked={vocalsOnlyForLipsync}
                      onChange={(e) => handleSetVocalsOnlyForLipsync(e.target.checked)}
                      className="w-3.5 h-3.5 accent-green-500"
                    />
                    <span>Send only vocal stem to Generator</span>
                    <span className="text-[10px] text-gray-600 ml-auto">(isolate voice for cleaner sync)</span>
                  </label>
                )}
              </div>}

              <div>
                <div className="flex items-center gap-2 mb-1">
                  <label className="text-sm font-medium">Seed</label>
                  <label className="flex items-center gap-1 text-xs text-gray-400 cursor-pointer ml-auto">
                    <input
                      type="checkbox"
                      checked={videoSeedOverride}
                      onChange={(e) => setVideoSeedOverride(e.target.checked)}
                      className="w-3 h-3"
                    />
                    Override
                  </label>
                </div>
                <input
                  type="text"
                  value={videoSeedOverride ? videoSeed : ''}
                  onChange={(e) => { setVideoSeedOverride(true); setVideoSeed(e.target.value); }}
                  readOnly={!videoSeedOverride}
                  placeholder={videoSeedOverride ? 'Enter seed' : 'Random (auto)'}
                  className={`w-full px-3 py-1.5 bg-gray-800 border border-gray-700 rounded text-gray-100 text-sm placeholder-gray-500 focus:outline-none focus:border-blue-500 ${!videoSeedOverride ? 'opacity-60' : ''}`}
                />
              </div>

              <div className="flex gap-2">
                <button
                  onClick={() => enhanceVideoPromptMutation.mutate()}
                  disabled={enhanceVideoPromptMutation.isPending}
                  className="flex-1 px-4 py-2 bg-purple-600 hover:bg-purple-700 rounded text-sm font-medium transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
                >
                  <Wand2 size={16} />
                  {enhanceVideoPromptMutation.isPending ? 'Enhancing...' : videoPrompt ? 'Enhance' : 'Generate Prompt'}
                </button>
                <button
                  onClick={() => generateVideoMutation.mutate()}
                  disabled={!videoPrompt || generateVideoMutation.isPending}
                  className="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded text-sm font-medium transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
                >
                  <Zap size={16} />
                  {generateVideoMutation.isPending ? 'Generating...' : 'Generate Video'}
                </button>
                <button
                  onClick={() => videoUploadRef.current?.click()}
                  disabled={uploadingVideo}
                  className="px-3 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm font-medium transition-colors disabled:opacity-50 flex items-center justify-center gap-1.5"
                  title="Upload your own video"
                >
                  <Upload size={16} />
                  {uploadingVideo ? '...' : ''}
                </button>
                <input
                  ref={videoUploadRef}
                  type="file"
                  accept="video/*"
                  onChange={handleVideoUpload}
                  className="hidden"
                />
              </div>

              {/* ─── Retrim Button (re-run post-processing) ───────── */}
              {(activeScene?.parameters?.chosen_video_path || activeScene?.parameters?.video_untrimmed_path) && (
                <button
                  onClick={handleRetrimScene}
                  disabled={isRetrimming}
                  className="w-full px-3 py-2 bg-amber-700 hover:bg-amber-600 rounded text-sm font-medium transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
                  title="Re-run post-processing pipeline (trim, color correction, audio mux, last-frame extraction) without regenerating"
                >
                  <RefreshCw size={14} className={isRetrimming ? 'animate-spin' : ''} />
                  {isRetrimming ? 'Retrimming...' : 'Retrim Video'}
                </button>
              )}

              {/* ─── Video Gallery ─────────────────────────────────── */}
              {videoVersions.length > 0 && (() => {
                const ver = videoVersions[videoHistoryIndex];
                const vidUrl = ver?.output_path ? `/api/files/${ver.output_path}` : null;
                const isCurrentVideoSaved = ver?.output_path && chosenVideoPath === ver.output_path;
                return (
                  <div className="space-y-2 pt-2 border-t border-gray-800">
                    <div
                      className="relative bg-gray-900 rounded overflow-hidden flex items-center justify-center"
                      style={{ minHeight: '160px' }}
                    >
                      {vidUrl ? (
                        <video
                          src={vidUrl}
                          className="max-w-full max-h-[300px] object-contain rounded"
                          controls
                          preload="metadata"
                        />
                      ) : (
                        <div className="flex flex-col items-center gap-2 text-gray-500">
                          <Film size={32} />
                          <span className="text-xs">No video preview available</span>
                        </div>
                      )}
                    </div>

                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <button
                          onClick={() => setVideoHistoryIndex(Math.max(0, videoHistoryIndex - 1))}
                          className="p-1.5 text-gray-400 hover:text-white disabled:opacity-50"
                          disabled={videoHistoryIndex === 0}
                        >
                          <ChevronLeft size={18} />
                        </button>
                        <span className="text-sm text-gray-400">
                          {videoHistoryIndex + 1} / {videoVersions.length}
                        </span>
                        <button
                          onClick={() => setVideoHistoryIndex(Math.min(videoHistoryIndex + 1, videoVersions.length - 1))}
                          className="p-1.5 text-gray-400 hover:text-white disabled:opacity-50"
                          disabled={videoHistoryIndex === videoVersions.length - 1}
                        >
                          <ChevronRight size={18} />
                        </button>
                      </div>

                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => setDetailsVersion(ver)}
                          className="px-2.5 py-1.5 rounded text-xs font-medium transition-colors flex items-center gap-1 bg-gray-600 hover:bg-gray-500 text-white"
                          title="Generation Details"
                        >
                          <Info size={14} />
                        </button>
                        {ver?.output_path && (
                          <a
                            href={`/api/files/${ver.output_path}`}
                            download={ver.output_path.split('/').pop() || 'video'}
                            className="px-2.5 py-1.5 rounded text-xs font-medium transition-colors flex items-center gap-1 bg-gray-600 hover:bg-gray-500 text-white"
                            title="Download video (post-processed)"
                          >
                            <Download size={14} />
                          </a>
                        )}
                        {ver?.output_path && (
                          <a
                            href={`/api/files/raw/${ver.output_path}`}
                            download={(ver.output_path.split('/').pop() || 'video').replace(/(\.[^.]+)$/, '_raw$1')}
                            className="px-2.5 py-1.5 rounded text-xs font-medium transition-colors flex items-center gap-1 bg-amber-700 hover:bg-amber-600 text-white"
                            title="Download raw ComfyUI output (no trimming, no color correction, no audio mux)"
                          >
                            <Download size={14} />
                            <span>Raw</span>
                          </a>
                        )}
                        <button
                          onClick={() => handleDeleteVideoVersion(ver, videoHistoryIndex)}
                          className="px-2.5 py-1.5 rounded text-xs font-medium transition-colors flex items-center gap-1 bg-red-600 hover:bg-red-700 text-white"
                          title="Delete this video"
                        >
                          <Trash2 size={14} />
                        </button>
                        <button
                          onClick={() => handleSaveVideoAsActive(ver)}
                          disabled={savingVideoPreview || !!isCurrentVideoSaved}
                          className={`px-3 py-1.5 rounded text-xs font-medium transition-colors flex items-center gap-1.5 ${
                            isCurrentVideoSaved
                              ? 'bg-green-900/30 text-green-400 border border-green-800'
                              : 'bg-green-600 hover:bg-green-700 text-white'
                          } disabled:opacity-60`}
                        >
                          {isCurrentVideoSaved ? (
                            <><Check size={14} /> Active</>
                          ) : (
                            <><Save size={14} /> {savingVideoPreview ? 'Saving...' : 'Set as Active'}</>
                          )}
                        </button>
                      </div>
                    </div>

                    {ver && (
                      <div className="text-[10px] text-gray-500 text-center">
                        {ver.status === 'completed' ? 'Completed' : ver.status}
                        {ver.completed_at && (<> · {new Date(ver.completed_at).toLocaleString()}</>)}
                      </div>
                    )}
                  </div>
                );
              })()}

              {videoVersions.length === 0 && (
                <div className="pt-2 border-t border-gray-800">
                  <div className="bg-gray-900 rounded flex flex-col items-center justify-center py-8 text-gray-500">
                    <Film size={32} />
                    <span className="text-xs mt-2">No videos yet</span>
                    <span className="text-xs text-gray-600 mt-1">Generate one or upload your own</span>
                  </div>
                </div>
              )}
            </>
          )}

          {/* ══════════ IMAGE MOVEMENT TAB ══════════ */}
          {activeTab === 'movement' && (
            <div className="text-sm space-y-4">
              {sceneSourceType !== 'image' ? (
                <div className="text-center py-8 text-gray-400">
                  <p className="text-sm">Image movement only applies to Image source scenes</p>
                  <p className="text-xs text-gray-500 mt-1">Switch scene source to "Image" to configure movement effects</p>
                </div>
              ) : !activeScene?.parameters?.chosen_image_path ? (
                <div className="text-center py-8 text-gray-400">
                  <p className="text-sm">No image selected for this scene</p>
                  <p className="text-xs text-gray-500 mt-1">Generate or upload an image first</p>
                </div>
              ) : (
                <>
                  {/* Effect Preset */}
                  <div>
                    <label className="block text-xs text-gray-400 mb-1.5 font-medium">Movement Effect</label>
                    <select
                      value={movementEffect}
                      onChange={(e) => setMovementEffect(e.target.value)}
                      className="w-full bg-gray-800 text-white rounded px-3 py-2 text-sm border border-gray-700 focus:border-blue-500 focus:outline-none"
                    >
                      <option value="none">None (Static)</option>
                      <optgroup label="Ken Burns">
                        <option value="zoom_in_center">Zoom In (Center)</option>
                        <option value="zoom_out_center">Zoom Out (Center)</option>
                        <option value="zoom_in_top_left">Zoom In (Top Left)</option>
                        <option value="zoom_in_top_right">Zoom In (Top Right)</option>
                        <option value="zoom_in_bottom_left">Zoom In (Bottom Left)</option>
                        <option value="zoom_in_bottom_right">Zoom In (Bottom Right)</option>
                      </optgroup>
                      <optgroup label="Pan">
                        <option value="pan_left">Pan Left</option>
                        <option value="pan_right">Pan Right</option>
                        <option value="pan_up">Pan Up</option>
                        <option value="pan_down">Pan Down</option>
                        <option value="pan_left_to_right">Pan Left to Right</option>
                        <option value="pan_right_to_left">Pan Right to Left</option>
                      </optgroup>
                      <optgroup label="Combo">
                        <option value="zoom_in_pan_left">Zoom In + Pan Left</option>
                        <option value="zoom_in_pan_right">Zoom In + Pan Right</option>
                        <option value="zoom_out_pan_left">Zoom Out + Pan Left</option>
                        <option value="zoom_out_pan_right">Zoom Out + Pan Right</option>
                      </optgroup>
                    </select>
                  </div>

                  {movementEffect !== 'none' && (
                    <>
                      {/* Intensity */}
                      <div>
                        <label className="block text-xs text-gray-400 mb-1.5 font-medium">
                          Intensity: {movementIntensity}%
                        </label>
                        <input
                          type="range"
                          min="10"
                          max="100"
                          value={movementIntensity}
                          onChange={(e) => setMovementIntensity(parseInt(e.target.value))}
                          className="w-full h-1.5 bg-gray-700 rounded-lg cursor-pointer accent-blue-500"
                        />
                        <div className="flex justify-between text-[10px] text-gray-500 mt-1">
                          <span>Subtle</span>
                          <span>Dramatic</span>
                        </div>
                      </div>

                      {/* Easing */}
                      <div>
                        <label className="block text-xs text-gray-400 mb-1.5 font-medium">Easing</label>
                        <select
                          value={movementEasing}
                          onChange={(e) => setMovementEasing(e.target.value)}
                          className="w-full bg-gray-800 text-white rounded px-3 py-2 text-sm border border-gray-700 focus:border-blue-500 focus:outline-none"
                        >
                          <option value="linear">Linear</option>
                          <option value="ease_in">Ease In (Slow Start)</option>
                          <option value="ease_out">Ease Out (Slow End)</option>
                          <option value="ease_in_out">Ease In/Out (Smooth)</option>
                        </select>
                      </div>
                    </>
                  )}

                  {/* Save Button */}
                  <button
                    onClick={saveMovementSettings}
                    className="w-full bg-blue-600 hover:bg-blue-700 text-white text-sm py-2 rounded font-medium transition-colors"
                  >
                    Save Movement Settings
                  </button>

                  {/* Preview info */}
                  {movementEffect !== 'none' && (
                    <div className="p-3 bg-gray-800/50 rounded border border-gray-700 text-xs text-gray-400">
                      <p>Effect will be previewed in the main stage and applied during final export via FFmpeg.</p>
                      <p className="mt-1">Scene duration: {activeScene ? ((activeScene.end_time - activeScene.start_time).toFixed(1)) : '?'}s</p>
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {/* ══════════ TRANSITIONS TAB ══════════ */}
          {activeTab === 'transitions' && (
            <div className="text-sm space-y-4">
              {/* Lead In */}
              <div>
                <label className="block text-xs text-gray-400 mb-1.5 font-medium">Lead In (From Previous Scene)</label>
                <select
                  value={transitionIn}
                  onChange={(e) => {
                    setTransitionIn(e.target.value);
                    // Auto-save on change
                    if (activeScene && currentProject) {
                      const newParams = {
                        ...activeScene.parameters,
                        transition_in: e.target.value !== 'none' ? { type: e.target.value, duration: transitionInDuration } : null,
                        transition_out: transitionOut !== 'none' ? { type: transitionOut, duration: transitionOutDuration } : null,
                      };
                      updateScene(currentProject.id, activeScene.id, { parameters: newParams });
                      useAppStore.getState().updateSceneInStore(activeScene.id, { parameters: newParams });
                    }
                  }}
                  className="w-full bg-gray-800 text-white rounded px-3 py-2 text-sm border border-gray-700 focus:border-blue-500 focus:outline-none"
                >
                  <option value="none">None (Hard Cut)</option>
                  <option value="crossfade">Crossfade</option>
                  <option value="fade_from_black">Fade from Black</option>
                  <option value="fade_from_white">Fade from White</option>
                  <option value="dissolve">Dissolve</option>
                  <option value="wipe_left">Wipe Left</option>
                  <option value="wipe_right">Wipe Right</option>
                  <option value="wipe_up">Wipe Up</option>
                  <option value="wipe_down">Wipe Down</option>
                  <option value="slide_left">Slide Left</option>
                  <option value="slide_right">Slide Right</option>
                </select>
                {transitionIn !== 'none' && (
                  <div className="mt-2">
                    <label className="block text-xs text-gray-400 mb-1 font-medium">
                      Duration: {transitionInDuration.toFixed(1)}s
                    </label>
                    <input
                      type="range"
                      min="0.1"
                      max="2.0"
                      step="0.1"
                      value={transitionInDuration}
                      onChange={(e) => setTransitionInDuration(parseFloat(e.target.value))}
                      onMouseUp={() => saveTransitionSettings()}
                      onTouchEnd={() => saveTransitionSettings()}
                      className="w-full h-1.5 bg-gray-700 rounded-lg cursor-pointer accent-blue-500"
                    />
                    <div className="flex justify-between text-[10px] text-gray-500 mt-1">
                      <span>0.1s</span>
                      <span>2.0s</span>
                    </div>
                  </div>
                )}
              </div>

              {/* Divider */}
              <div className="border-t border-gray-700" />

              {/* Lead Out */}
              <div>
                <label className="block text-xs text-gray-400 mb-1.5 font-medium">Lead Out (To Next Scene)</label>
                <select
                  value={transitionOut}
                  onChange={(e) => {
                    setTransitionOut(e.target.value);
                    // Auto-save on change
                    if (activeScene && currentProject) {
                      const newParams = {
                        ...activeScene.parameters,
                        transition_in: transitionIn !== 'none' ? { type: transitionIn, duration: transitionInDuration } : null,
                        transition_out: e.target.value !== 'none' ? { type: e.target.value, duration: transitionOutDuration } : null,
                      };
                      updateScene(currentProject.id, activeScene.id, { parameters: newParams });
                      useAppStore.getState().updateSceneInStore(activeScene.id, { parameters: newParams });
                    }
                  }}
                  className="w-full bg-gray-800 text-white rounded px-3 py-2 text-sm border border-gray-700 focus:border-blue-500 focus:outline-none"
                >
                  <option value="none">None (Hard Cut)</option>
                  <option value="crossfade">Crossfade</option>
                  <option value="fade_to_black">Fade to Black</option>
                  <option value="fade_to_white">Fade to White</option>
                  <option value="dissolve">Dissolve</option>
                  <option value="wipe_left">Wipe Left</option>
                  <option value="wipe_right">Wipe Right</option>
                  <option value="wipe_up">Wipe Up</option>
                  <option value="wipe_down">Wipe Down</option>
                  <option value="slide_left">Slide Left</option>
                  <option value="slide_right">Slide Right</option>
                </select>
                {transitionOut !== 'none' && (
                  <div className="mt-2">
                    <label className="block text-xs text-gray-400 mb-1 font-medium">
                      Duration: {transitionOutDuration.toFixed(1)}s
                    </label>
                    <input
                      type="range"
                      min="0.1"
                      max="2.0"
                      step="0.1"
                      value={transitionOutDuration}
                      onChange={(e) => setTransitionOutDuration(parseFloat(e.target.value))}
                      onMouseUp={() => saveTransitionSettings()}
                      onTouchEnd={() => saveTransitionSettings()}
                      className="w-full h-1.5 bg-gray-700 rounded-lg cursor-pointer accent-blue-500"
                    />
                    <div className="flex justify-between text-[10px] text-gray-500 mt-1">
                      <span>0.1s</span>
                      <span>2.0s</span>
                    </div>
                  </div>
                )}
              </div>

              {/* Info */}
              <div className="p-3 bg-gray-800/50 rounded border border-gray-700 text-xs text-gray-400">
                <p>Transitions eat into the scene duration — they don't add extra time.</p>
                <p className="mt-1">A 0.5s crossfade means the first 0.5s of this scene overlaps with the previous scene's last 0.5s.</p>
              </div>
            </div>
          )}

          {/* ══════════ STEMS TAB ══════════ */}
          {activeTab === 'stems' && (
            <>
              <p className="text-sm text-gray-400 mb-4">Select which audio stems to include in this scene:</p>
              <div className="space-y-3">
                {[
                  { label: 'Vocals', val: vocalsMix, set: setVocalsMix },
                  { label: 'Drums', val: drumsMix, set: setDrumsMix },
                  { label: 'Bass', val: bassMix, set: setBassMix },
                  { label: 'Other', val: otherMix, set: setOtherMix },
                ].map(({ label, val, set }) => (
                  <label key={label} className="flex items-center gap-3 p-3 bg-gray-800 hover:bg-gray-700 rounded cursor-pointer transition-colors">
                    <input type="checkbox" checked={val} onChange={(e) => set(e.target.checked)} className="w-4 h-4" />
                    <span className="text-sm">{label}</span>
                  </label>
                ))}
              </div>

              <div className="flex gap-2">
                <button
                  onClick={() => setStemsMutation.mutate()}
                  className="flex-1 px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded text-sm font-medium transition-colors"
                  disabled={setStemsMutation.isPending}
                >
                  {setStemsMutation.isPending ? 'Saving...' : 'Save Selection'}
                </button>
                <button
                  onClick={() => mixStemsMutation.mutate()}
                  className="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded text-sm font-medium transition-colors flex items-center justify-center gap-2"
                  disabled={mixStemsMutation.isPending}
                >
                  <Music size={16} />
                  {mixStemsMutation.isPending ? 'Mixing...' : 'Preview Mix'}
                </button>
              </div>
            </>
          )}

          {/* ══════════ LYRICS TAB ══════════ */}
          {activeTab === 'lyrics' && (
            <div className="text-sm space-y-3">
              <div className="flex items-center justify-between">
                <p className="font-medium text-gray-300">
                  Scene Lyrics
                  {activeScene && activeScene.start_time != null && (
                    <span className="text-xs text-gray-500 ml-2">
                      ({activeScene.start_time.toFixed(1)}s – {activeScene.end_time.toFixed(1)}s)
                    </span>
                  )}
                </p>
                <div className="flex items-center gap-2">
                  {(activeScene as any)?.parameters?.lyrics_override && (
                    <span className="text-[10px] text-yellow-500 bg-yellow-500/10 px-1.5 py-0.5 rounded">
                      Overridden
                    </span>
                  )}
                  {!isEditingLyrics ? (
                    <button
                      onClick={() => {
                        setEditedLyrics(sceneLyrics || '');
                        setIsEditingLyrics(true);
                      }}
                      className="flex items-center gap-1 px-2 py-1 text-xs bg-gray-700 hover:bg-gray-600 rounded transition-colors"
                      title="Override lyrics for this scene"
                    >
                      <Pencil size={12} />
                      Override
                    </button>
                  ) : (
                    <button
                      onClick={() => setIsEditingLyrics(false)}
                      className="flex items-center gap-1 px-2 py-1 text-xs bg-gray-700 hover:bg-gray-600 rounded transition-colors"
                    >
                      <X size={12} />
                      Cancel
                    </button>
                  )}
                </div>
              </div>

              {isEditingLyrics ? (
                <div className="space-y-2">
                  <textarea
                    value={editedLyrics}
                    onChange={(e) => setEditedLyrics(e.target.value)}
                    className="w-full px-3 py-2 bg-gray-800 border border-gray-600 rounded text-gray-200 text-sm leading-relaxed resize-y min-h-[120px] focus:border-blue-500 focus:outline-none"
                    rows={8}
                    placeholder="Edit lyrics for this scene..."
                  />
                  <div className="flex gap-2">
                    <button
                      onClick={async () => {
                        if (!activeScene || !currentProject) return;
                        setIsSavingLyrics(true);
                        try {
                          const newParams = {
                            ...activeScene.parameters,
                            lyrics: editedLyrics,
                            lyrics_override: true,
                          };
                          await updateScene(currentProject.id, activeScene.id, { parameters: newParams });
                          useAppStore.getState().updateSceneInStore(activeScene.id, { parameters: newParams });
                          setIsEditingLyrics(false);
                        } catch (err) {
                          console.error('Failed to save lyrics override:', err);
                        } finally {
                          setIsSavingLyrics(false);
                        }
                      }}
                      className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 bg-blue-600 hover:bg-blue-700 rounded text-sm font-medium transition-colors"
                      disabled={isSavingLyrics}
                    >
                      <Save size={14} />
                      {isSavingLyrics ? 'Saving...' : 'Save Override'}
                    </button>
                    {(activeScene as any)?.parameters?.lyrics_override && (
                      <button
                        onClick={async () => {
                          if (!activeScene || !currentProject) return;
                          setIsSavingLyrics(true);
                          try {
                            const newParams = { ...activeScene.parameters };
                            delete (newParams as any).lyrics;
                            delete (newParams as any).lyrics_override;
                            await updateScene(currentProject.id, activeScene.id, { parameters: newParams });
                            useAppStore.getState().updateSceneInStore(activeScene.id, { parameters: newParams });
                            setIsEditingLyrics(false);
                          } catch (err) {
                            console.error('Failed to reset lyrics:', err);
                          } finally {
                            setIsSavingLyrics(false);
                          }
                        }}
                        className="flex items-center gap-1.5 px-3 py-2 bg-red-600/80 hover:bg-red-600 rounded text-sm font-medium transition-colors"
                        disabled={isSavingLyrics}
                        title="Remove override and restore auto-detected lyrics"
                      >
                        <RotateCcw size={14} />
                        Reset
                      </button>
                    )}
                  </div>
                  <p className="text-[10px] text-gray-500">
                    Override lyrics are used for prompt enhancement and export subtitles. Reset to restore auto-detected lyrics.
                  </p>
                </div>
              ) : sceneLyrics ? (
                <p className="p-3 bg-gray-800 rounded text-gray-300 leading-relaxed whitespace-pre-wrap">
                  {sceneLyrics}
                </p>
              ) : (
                <div className="text-center py-8 text-gray-400">
                  <p className="text-sm">No lyrics available for this scene</p>
                  <p className="text-xs text-gray-500 mt-1">Process audio to generate lyrics with timestamps</p>
                </div>
              )}

              <div className="p-2 bg-gray-900 rounded text-[10px] text-gray-600 font-mono">
                <p>words: {lyricsDebugInfo.wordsCount} | text: {lyricsDebugInfo.textLength} chars | initial_text: {lyricsDebugInfo.initialTextLength} chars</p>
                <p>scene: {lyricsDebugInfo.sceneStart?.toFixed?.(1) ?? 'null'}s – {lyricsDebugInfo.sceneEnd?.toFixed?.(1) ?? 'null'}s</p>
                {lyricsDebugInfo.sampleWord && (
                  <p>sample word: {JSON.stringify(lyricsDebugInfo.sampleWord)}</p>
                )}
              </div>
            </div>
          )}

          {/* ══════════ TOOLS TAB ══════════ */}
          {activeTab === 'tools' && activeScene && (
            <ToolsTabContent
              scene={activeScene}
              projectId={currentProject?.id || ''}
            />
          )}

          {/* ══════════ PROMPT TAB ══════════ */}
          {activeTab === 'prompt' && activeScene && (
            <div className="text-sm space-y-4">
              {/* Final Submitted Image Prompt (sent to ComfyUI) */}
              {activeScene.parameters?.submitted_image_prompt && (
                <div>
                  <label className="block text-sm font-medium mb-2 text-cyan-300">Final Submitted Image Prompt (Sent to ComfyUI)</label>
                  <textarea
                    value={activeScene.parameters.submitted_image_prompt}
                    readOnly
                    className="w-full px-3 py-2 bg-cyan-950/30 border border-cyan-700/50 rounded text-cyan-100 text-sm h-28 resize-none font-mono"
                  />
                  <p className="text-xs text-cyan-600 mt-1">The exact prompt sent to the image model — includes all suffixes (anti-text, SFW, image direction style tag)</p>
                </div>
              )}

              {/* Final Submitted Last Frame Prompt */}
              {activeScene.parameters?.submitted_last_frame_prompt && (
                <div>
                  <label className="block text-sm font-medium mb-2 text-cyan-300">Final Submitted Last Frame Prompt (Sent to ComfyUI)</label>
                  <textarea
                    value={activeScene.parameters.submitted_last_frame_prompt}
                    readOnly
                    className="w-full px-3 py-2 bg-cyan-950/30 border border-cyan-700/50 rounded text-cyan-100 text-sm h-28 resize-none font-mono"
                  />
                  <p className="text-xs text-cyan-600 mt-1">The exact last frame prompt sent to the image model — includes all suffixes</p>
                </div>
              )}

              {/* Final Submitted Video Prompt */}
              {activeScene.parameters?.submitted_video_prompt && (
                <div>
                  <label className="block text-sm font-medium mb-2 text-teal-300">Final Submitted Video Prompt (Sent to ComfyUI)</label>
                  <textarea
                    value={activeScene.parameters.submitted_video_prompt}
                    readOnly
                    className="w-full px-3 py-2 bg-teal-950/30 border border-teal-700/50 rounded text-teal-100 text-sm h-28 resize-none font-mono"
                  />
                  <p className="text-xs text-teal-600 mt-1">The exact prompt sent to the video model — includes all suffixes (SFW, image direction style tag)</p>
                </div>
              )}

              {/* Original Prompt */}
              {activeScene.prompt && (
                <div>
                  <label className="block text-sm font-medium mb-2 text-gray-300">Original Prompt (User Input)</label>
                  <textarea
                    value={activeScene.prompt}
                    readOnly
                    className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 text-sm h-24 resize-none"
                  />
                </div>
              )}

              {/* Two-Pass Original Prompt */}
              {activeScene.parameters?.two_pass_original_prompt && (
                <div>
                  <label className="block text-sm font-medium mb-2 text-amber-300">Two-Pass Original Prompt</label>
                  <textarea
                    value={activeScene.parameters.two_pass_original_prompt}
                    readOnly
                    className="w-full px-3 py-2 bg-amber-950/30 border border-amber-700/50 rounded text-amber-100 text-sm h-24 resize-none"
                  />
                  <p className="text-xs text-amber-600 mt-1">The user's prompt before two-pass processing</p>
                </div>
              )}

              {/* Two-Pass Scene Prompt (Pass 1) */}
              {activeScene.parameters?.two_pass_scene_prompt && (
                <div>
                  <label className="block text-sm font-medium mb-2 text-blue-300">Two-Pass Scene Prompt (Pass 1)</label>
                  <textarea
                    value={activeScene.parameters.two_pass_scene_prompt}
                    readOnly
                    className="w-full px-3 py-2 bg-blue-950/30 border border-blue-700/50 rounded text-blue-100 text-sm h-24 resize-none"
                  />
                  <p className="text-xs text-blue-600 mt-1">Scene composition (environment, lighting, atmosphere) — LLM-enhanced for Pass 1</p>
                </div>
              )}

              {/* Two-Pass Composite Prompt (Pass 2) */}
              {activeScene.parameters?.two_pass_composite_prompt && (
                <div>
                  <label className="block text-sm font-medium mb-2 text-green-300">Two-Pass Composite Prompt (Pass 2)</label>
                  <textarea
                    value={activeScene.parameters.two_pass_composite_prompt}
                    readOnly
                    className="w-full px-3 py-2 bg-green-950/30 border border-green-700/50 rounded text-green-100 text-sm h-24 resize-none"
                  />
                  <p className="text-xs text-green-600 mt-1">Character compositing (placing characters into scene) — LLM-enhanced for Pass 2</p>
                </div>
              )}

              {/* Video Prompt */}
              {activeScene.parameters?.video_prompt && (
                <div>
                  <label className="block text-sm font-medium mb-2 text-purple-300">Video Prompt</label>
                  <textarea
                    value={activeScene.parameters.video_prompt}
                    readOnly
                    className="w-full px-3 py-2 bg-purple-950/30 border border-purple-700/50 rounded text-purple-100 text-sm h-24 resize-none"
                  />
                  <p className="text-xs text-purple-600 mt-1">Video generation prompt (motion, camera, action)</p>
                </div>
              )}

              {/* Last Frame Prompt */}
              {activeScene.parameters?.last_frame_prompt && (
                <div>
                  <label className="block text-sm font-medium mb-2 text-pink-300">Last Frame Prompt</label>
                  <textarea
                    value={activeScene.parameters.last_frame_prompt}
                    readOnly
                    className="w-full px-3 py-2 bg-pink-950/30 border border-pink-700/50 rounded text-pink-100 text-sm h-24 resize-none"
                  />
                  <p className="text-xs text-pink-600 mt-1">Endpoint image for first frame / last frame video generation</p>
                </div>
              )}

              {/* Info Box */}
              {!activeScene.parameters?.submitted_image_prompt && !activeScene.parameters?.submitted_video_prompt && !activeScene.parameters?.two_pass_scene_prompt && !activeScene.parameters?.video_prompt && !activeScene.parameters?.last_frame_prompt && (
                <div className="p-3 bg-gray-800/50 rounded border border-gray-700 text-xs text-gray-400">
                  <p>No prompts generated yet. Generate images or videos to see the prompts used.</p>
                </div>
              )}
            </div>
          )}
        </div>
        </>
      )}

      {/* Lightbox — rendered via portal */}
      {lightboxOpen && activeVersions.length > 0 && createPortal(
        <ImageLightbox
          versions={activeVersions}
          initialIndex={activeHistoryIndex}
          onClose={() => setLightboxOpen(false)}
          onSave={handleSaveAsPreview}
          onDelete={handleDeleteVersion}
        />,
        document.body
      )}

      {/* Generation Details Modal — rendered via portal */}
      {detailsVersion && createPortal(
        <GenerationDetailsModal
          version={detailsVersion}
          onClose={() => setDetailsVersion(null)}
          onRerun={handleRerunGeneration}
          isRerunning={isRerunning}
        />,
        document.body
      )}

      {/* Two-Pass Base Image Lightbox (View Original) */}
      {twoPassBaseViewOpen && activeScene?.parameters?.two_pass_base_image_path && createPortal(
        <div
          style={{ position: 'fixed', inset: 0, zIndex: 9999, display: 'flex', alignItems: 'center', justifyContent: 'center', backgroundColor: 'rgba(0,0,0,0.85)' }}
          onClick={() => setTwoPassBaseViewOpen(false)}
        >
          <div style={{ position: 'relative', maxWidth: '90vw', maxHeight: '90vh' }} onClick={(e) => e.stopPropagation()}>
            <img
              src={`/api/files/${activeScene.parameters.two_pass_base_image_path}`}
              alt="Pass 1 Base Image (Scene Only)"
              style={{ maxWidth: '90vw', maxHeight: '85vh', objectFit: 'contain', borderRadius: '8px' }}
              onError={handleImgError}
            />
            <div style={{ position: 'absolute', top: '12px', left: '12px', backgroundColor: 'rgba(79,70,229,0.9)', color: 'white', padding: '4px 10px', borderRadius: '4px', fontSize: '12px', fontWeight: 600 }}>
              Pass 1 — Scene Only (Before Character Compositing)
            </div>
            <button
              onClick={() => setTwoPassBaseViewOpen(false)}
              style={{ position: 'absolute', top: '12px', right: '12px', backgroundColor: 'rgba(0,0,0,0.6)', color: 'white', border: 'none', borderRadius: '50%', width: '32px', height: '32px', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '18px' }}
            >
              ✕
            </button>
          </div>
        </div>,
        document.body
      )}
    </div>
  );
}
