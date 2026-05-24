import { useState } from 'react';
import { Upload, X, Plus } from 'lucide-react';
import { uploadBatchAudio } from '@/api/client';
import type { BatchItemConfig } from '@/types/index';

interface BatchItemAddModalProps {
  onAdd: (item: BatchItemConfig) => void;
  onClose: () => void;
}

export default function BatchItemAddModal({ onAdd, onClose }: BatchItemAddModalProps) {
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [projectName, setProjectName] = useState('');
  const [lyrics, setLyrics] = useState('');
  const [conceptDirection, setConceptDirection] = useState('');
  const [style, setStyle] = useState('');
  const [renderType, setRenderType] = useState<'music_video' | 'narration_video'>('music_video');
  const [videoMode, setVideoMode] = useState<'i2v' | 'v2v'>('i2v');
  const [twoPass, setTwoPass] = useState(true);
  const [useStoryFlow, setUseStoryFlow] = useState(true);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState('');

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      setAudioFile(file);
      if (!projectName) {
        setProjectName(file.name.replace(/\.[^/.]+$/, ''));
      }
    }
  };

  const handleAdd = async () => {
    if (!audioFile) return;

    setIsUploading(true);
    setError('');

    try {
      const response = await uploadBatchAudio(audioFile);
      const { upload_path, filename } = response.data;

      const item: BatchItemConfig = {
        id: Date.now().toString(36) + Math.random().toString(36).slice(2),
        audio_filename: filename,
        audio_upload_path: upload_path,
        audioFile,
        lyrics_text: lyrics,
        project_name: projectName || audioFile.name.replace(/\.[^/.]+$/, ''),
        concept_direction: conceptDirection,
        style_text: style,
        render_type: renderType,
        video_mode: videoMode,
        two_pass: twoPass,
        use_story_flow: useStoryFlow,
      };

      onAdd(item);
      onClose();
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'Upload failed');
    } finally {
      setIsUploading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
      <div className="bg-gray-900 border border-gray-800 rounded-lg w-full max-w-lg p-6 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-xl font-bold text-gray-100">Add Batch Item</h2>
          <button
            onClick={onClose}
            className="p-1 text-gray-400 hover:text-gray-200 transition-colors"
          >
            <X size={20} />
          </button>
        </div>

        {/* Audio File */}
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-300 mb-1">
            Audio File <span className="text-red-400">*</span>
          </label>
          <label className="flex items-center gap-3 px-3 py-2 bg-gray-800 border border-gray-700 rounded cursor-pointer hover:border-gray-600 transition-colors">
            <Upload size={16} className="text-gray-400 shrink-0" />
            <span className="text-sm text-gray-400 truncate">
              {audioFile ? audioFile.name : 'Choose audio file...'}
            </span>
            <input
              type="file"
              accept="audio/*"
              onChange={handleFileChange}
              className="hidden"
            />
          </label>
        </div>

        {/* Project Name */}
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-300 mb-1">Project Name</label>
          <input
            type="text"
            value={projectName}
            onChange={(e) => setProjectName(e.target.value)}
            placeholder="Auto-fills from audio filename"
            className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 placeholder-gray-500 text-sm focus:outline-none focus:border-blue-500"
          />
        </div>

        {/* Lyrics */}
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-300 mb-1">Lyrics</label>
          <textarea
            value={lyrics}
            onChange={(e) => setLyrics(e.target.value)}
            placeholder="Paste lyrics (optional)"
            rows={3}
            className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 placeholder-gray-500 text-sm focus:outline-none focus:border-blue-500 resize-y"
          />
        </div>

        {/* Concept Direction */}
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-300 mb-1">Concept Direction</label>
          <textarea
            value={conceptDirection}
            onChange={(e) => setConceptDirection(e.target.value)}
            placeholder="Describe the visual concept (optional)"
            rows={2}
            className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 placeholder-gray-500 text-sm focus:outline-none focus:border-blue-500 resize-y"
          />
        </div>

        {/* Style */}
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-300 mb-1">Style</label>
          <input
            type="text"
            value={style}
            onChange={(e) => setStyle(e.target.value)}
            placeholder="e.g. cinematic, anime, photorealistic (optional)"
            className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 placeholder-gray-500 text-sm focus:outline-none focus:border-blue-500"
          />
        </div>

        {/* Render Type */}
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-300 mb-2">Render Type</label>
          <div className="flex gap-4">
            <label className="flex items-center gap-2 cursor-pointer text-sm text-gray-300">
              <input
                type="radio"
                name="renderType"
                checked={renderType === 'music_video'}
                onChange={() => setRenderType('music_video')}
                className="w-4 h-4"
              />
              Music Video
            </label>
            <label className="flex items-center gap-2 cursor-pointer text-sm text-gray-300">
              <input
                type="radio"
                name="renderType"
                checked={renderType === 'narration_video'}
                onChange={() => setRenderType('narration_video')}
                className="w-4 h-4"
              />
              Narration Video
            </label>
          </div>
        </div>

        {/* Video Mode */}
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-300 mb-2">Video Mode</label>
          <div className="flex gap-4">
            <label className="flex items-center gap-2 cursor-pointer text-sm text-gray-300">
              <input
                type="radio"
                name="videoMode"
                checked={videoMode === 'i2v'}
                onChange={() => setVideoMode('i2v')}
                className="w-4 h-4"
              />
              I2V
            </label>
            <label className="flex items-center gap-2 cursor-pointer text-sm text-gray-300">
              <input
                type="radio"
                name="videoMode"
                checked={videoMode === 'v2v'}
                onChange={() => setVideoMode('v2v')}
                className="w-4 h-4"
              />
              V2V
            </label>
          </div>
        </div>

        {/* Checkboxes */}
        <div className="mb-6 flex flex-col gap-2">
          <label className="flex items-center gap-2 cursor-pointer text-sm text-gray-300">
            <input
              type="checkbox"
              checked={twoPass}
              onChange={(e) => setTwoPass(e.target.checked)}
              className="w-4 h-4 rounded"
            />
            Two-pass image generation
          </label>
          <label className="flex items-center gap-2 cursor-pointer text-sm text-gray-300">
            <input
              type="checkbox"
              checked={useStoryFlow}
              onChange={(e) => setUseStoryFlow(e.target.checked)}
              className="w-4 h-4 rounded"
            />
            Use story flow
          </label>
        </div>

        {error && (
          <div className="mb-4 px-3 py-2 bg-red-900/50 border border-red-800 rounded text-sm text-red-300">
            {error}
          </div>
        )}

        {/* Actions */}
        <div className="flex gap-3">
          <button
            onClick={onClose}
            disabled={isUploading}
            className="flex-1 px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded text-sm font-medium transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={handleAdd}
            disabled={!audioFile || isUploading}
            className="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded text-sm font-medium transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
          >
            {isUploading ? (
              <>Uploading...</>
            ) : (
              <>
                <Plus size={16} />
                Add to Queue
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
