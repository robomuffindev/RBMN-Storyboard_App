import { useState, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { handleImgError } from '@/utils/brokenImage';
import {
  generateCharacterImage,
  getCharacterVersions,
  deleteCharacterVersion,
  setCharacterActiveImage,
  enhancePrompt,
  uploadAsset,
} from '@/api/client';
import { useAssetPicker } from '@/components/AssetManager/AssetPickerModal';
import { useAppStore } from '@/store';
import type { Asset } from '@/types/index';

// ── Resolution presets (shared with ConceptPanel) ─────────────────────
interface ResolutionPreset {
  label: string;
  width: number;
  height: number;
  aspect: string;
}

const RESOLUTION_PRESETS: ResolutionPreset[] = [
  { label: '1536 × 864',  width: 1536, height: 864,  aspect: '16:9' },
  { label: '1344 × 768',  width: 1344, height: 768,  aspect: '16:9' },
  { label: '1280 × 720',  width: 1280, height: 720,  aspect: '16:9' },
  { label: '1152 × 896',  width: 1152, height: 896,  aspect: '9:7' },
  { label: '1216 × 832',  width: 1216, height: 832,  aspect: '3:2' },
  { label: '1344 × 896',  width: 1344, height: 896,  aspect: '3:2' },
  { label: '1024 × 1024', width: 1024, height: 1024, aspect: '1:1' },
  { label: '864 × 1536',  width: 864,  height: 1536, aspect: '9:16' },
  { label: '768 × 1344',  width: 768,  height: 1344, aspect: '9:16' },
  { label: '720 × 1280',  width: 720,  height: 1280, aspect: '9:16' },
  { label: '896 × 1152',  width: 896,  height: 1152, aspect: '7:9' },
  { label: '832 × 1216',  width: 832,  height: 1216, aspect: '2:3' },
  { label: '896 × 1344',  width: 896,  height: 1344, aspect: '2:3' },
];

// findPresetKey not needed here — presets selected by key directly

// ── Types ─────────────────────────────────────────────────────────────

interface CharacterData {
  name: string;
  description: string;
  image_path: string | null;
}

interface ReferenceImage {
  asset_id: string;
  image_path: string;
  description: string;
}

interface CharacterVersion {
  id: string;
  output_path: string | null;
  prompt: string;
  parameters: Record<string, any>;
  status: string;
  created_at: string | null;
}

interface CharacterCreatorModalProps {
  projectId: string;
  characterIndex: number; // -1 = creating new, >=0 = editing existing
  character: CharacterData;
  onClose: () => void;
  onSave: (index: number, character: CharacterData) => void;
}

// ── Component ─────────────────────────────────────────────────────────

export default function CharacterCreatorModal({
  projectId,
  characterIndex,
  character,
  onClose,
  onSave,
}: CharacterCreatorModalProps) {
  const queryClient = useQueryClient();

  // Character fields
  const [name, setName] = useState(character.name);
  const [description, setDescription] = useState(character.description);
  const [prompt, setPrompt] = useState('');

  // Reference images (up to 4)
  const [refImages, setRefImages] = useState<ReferenceImage[]>([]);

  // Resolution
  const [resWidth, setResWidth] = useState(1024);
  const [resHeight, setResHeight] = useState(1024);
  const [resPresetKey, setResPresetKey] = useState('1024x1024');

  // Gallery
  const [galleryIndex, setGalleryIndex] = useState(0);
  const [lightboxOpen, setLightboxOpen] = useState(false);

  // Active image path
  const [activeImagePath, setActiveImagePath] = useState<string | null>(character.image_path);

  // Asset picker for reference images
  const assets = useAppStore(s => s.assets);
  const { openPicker: openRefPicker, PickerModals: RefPickerModals } = useAssetPicker({
    assets: assets || [],
    onFileUpload: (file) => handleRefUpload(file),
    onAssetSelect: (asset: Asset) => {
      if (refImages.length >= 4) return;
      setRefImages((prev) => [...prev, { asset_id: asset.id, image_path: asset.rel_path, description: '' }]);
    },
    accept: 'image/*',
    imagesOnly: true,
    title: 'Add Character Reference',
  });

  // Seed from editing existing character's last generation
  useEffect(() => {
    if (character.description && !prompt) {
      setPrompt(
        `Character portrait: ${character.name || 'Character'}. ${character.description}. ` +
        'Full body or upper body shot, clear features, studio lighting, character reference sheet style.'
      );
    }
  }, []);

  // Fetch versions for existing characters
  const isEditing = characterIndex >= 0;
  const { data: versions = [], refetch: refetchVersions } = useQuery<CharacterVersion[]>({
    queryKey: ['characterVersions', projectId, characterIndex],
    queryFn: async () => {
      if (!isEditing) return [];
      const resp = await getCharacterVersions(projectId, characterIndex);
      return resp.data;
    },
    enabled: isEditing,
    staleTime: 5_000,
  });

  // Keep gallery index in bounds
  useEffect(() => {
    if (galleryIndex >= versions.length && versions.length > 0) {
      setGalleryIndex(versions.length - 1);
    }
  }, [versions.length, galleryIndex]);

  // Sync gallery to active image
  useEffect(() => {
    if (activeImagePath && versions.length > 0) {
      const idx = versions.findIndex((v) => v.output_path === activeImagePath);
      if (idx >= 0) setGalleryIndex(idx);
    }
  }, [activeImagePath, versions]);

  // ── Auto-select workflow from ref count ──
  const autoWorkflowType = (refCount: number) => {
    const map: Record<number, string> = { 0: 'klein_t2i', 1: 'klein_1ref', 2: 'klein_2ref', 3: 'klein_3ref', 4: 'klein_4ref' };
    return map[Math.min(refCount, 4)] || 'klein_t2i';
  };

  // ── Enhance prompt ──
  const enhanceMutation = useMutation({
    mutationFn: async () => {
      const context = [
        `Character portrait generation for "${name || 'Character'}".`,
        description ? `Character description: ${description}` : '',
        'Optimize for a detailed character reference image suitable for AI image generation pipelines.',
      ].filter(Boolean).join(' | ');

      const resp = await enhancePrompt(projectId, { prompt, context });
      setPrompt(resp.data.enhanced_prompt);
      return resp.data;
    },
  });

  // ── Generate image ──
  const generateMutation = useMutation({
    mutationFn: async () => {
      const refAssetIds = refImages.map((r) => r.asset_id);
      const resp = await generateCharacterImage(projectId, {
        character_index: characterIndex >= 0 ? characterIndex : 0,
        prompt_override: prompt,
        width: resWidth,
        height: resHeight,
        workflow_type: autoWorkflowType(refAssetIds.length),
        reference_asset_ids: refAssetIds,
      });
      return resp.data;
    },
    onSuccess: () => {
      // Poll versions after a short delay for the job to complete
      const poll = setInterval(() => {
        refetchVersions();
      }, 3000);
      setTimeout(() => clearInterval(poll), 60_000);
    },
  });

  // ── Delete version ──
  const deleteMutation = useMutation({
    mutationFn: async (versionId: string) => {
      if (!window.confirm('Delete this character image? This is permanent and cannot be undone.')) {
        throw new Error('cancelled');
      }
      await deleteCharacterVersion(projectId, characterIndex, versionId);
    },
    onSuccess: () => {
      refetchVersions();
      queryClient.invalidateQueries({ queryKey: ['concept', projectId] });
    },
  });

  // ── Set active image ──
  const setActiveMutation = useMutation({
    mutationFn: async (outputPath: string) => {
      await setCharacterActiveImage(projectId, characterIndex, outputPath);
    },
    onSuccess: (_, outputPath) => {
      setActiveImagePath(outputPath);
      queryClient.invalidateQueries({ queryKey: ['concept', projectId] });
    },
  });

  // ── Upload reference image ──
  const handleRefUpload = async (file: File) => {
    if (refImages.length >= 4) return;
    const formData = new FormData();
    formData.append('file', file);
    formData.append('asset_type', 'reference');
    try {
      const resp = await uploadAsset(projectId, formData);
      const asset = resp.data;
      setRefImages((prev) => [...prev, { asset_id: asset.id, image_path: asset.rel_path, description: '' }]);
    } catch (err) {
      console.error('Failed to upload reference image:', err);
    }
  };

  const removeRef = (idx: number) => {
    setRefImages((prev) => prev.filter((_, i) => i !== idx));
  };

  // ── Save character data (name/description) back to parent ──
  const handleSaveAndClose = () => {
    onSave(characterIndex, { name, description, image_path: activeImagePath });
    onClose();
  };

  const completedVersions = versions.filter((v) => v.status === 'completed' && v.output_path);
  const currentVersion = completedVersions[galleryIndex];

  // ── Inline styles (pywebview compat) ──
  const overlay: React.CSSProperties = {
    position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
    display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 9999,
  };
  const modal: React.CSSProperties = {
    background: '#111827', border: '1px solid #374151', borderRadius: '0.75rem',
    width: '100%', maxWidth: '700px', maxHeight: '90vh', display: 'flex', flexDirection: 'column',
    overflow: 'hidden',
  };
  const header: React.CSSProperties = {
    padding: '1rem 1.5rem', borderBottom: '1px solid #374151', display: 'flex',
    alignItems: 'center', justifyContent: 'space-between', flexShrink: 0,
  };
  const body: React.CSSProperties = {
    padding: '1.5rem', overflowY: 'auto', flex: 1,
  };
  const footer: React.CSSProperties = {
    padding: '1rem 1.5rem', borderTop: '1px solid #374151', display: 'flex',
    gap: '0.75rem', flexShrink: 0,
  };
  const inputStyle: React.CSSProperties = {
    width: '100%', padding: '0.5rem 0.75rem', background: '#1f2937', border: '1px solid #374151',
    borderRadius: '0.375rem', color: '#f3f4f6', fontSize: '0.875rem', outline: 'none',
  };
  const labelStyle: React.CSSProperties = {
    display: 'block', fontSize: '0.75rem', fontWeight: 500, color: '#9ca3af', marginBottom: '0.25rem',
  };
  const btnPrimary: React.CSSProperties = {
    padding: '0.5rem 1rem', background: '#7c3aed', border: 'none', borderRadius: '0.375rem',
    color: '#fff', fontWeight: 600, cursor: 'pointer', fontSize: '0.85rem',
  };
  const btnSecondary: React.CSSProperties = {
    padding: '0.5rem 1rem', background: '#1f2937', border: '1px solid #374151',
    borderRadius: '0.375rem', color: '#e5e7eb', fontWeight: 500, cursor: 'pointer', fontSize: '0.85rem',
  };
  const sectionGap: React.CSSProperties = { marginBottom: '1rem' };

  return (<>
    <RefPickerModals />
    {createPortal(
    <div style={overlay} onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div style={modal} onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div style={header}>
          <h2 style={{ fontSize: '1.25rem', fontWeight: 700, color: '#f3f4f6', margin: 0 }}>
            {isEditing ? `Edit Character: ${character.name || 'Unnamed'}` : 'Create Character'}
          </h2>
          <button onClick={onClose} style={{ ...btnSecondary, padding: '0.25rem 0.5rem', fontSize: '0.8rem' }}>✕</button>
        </div>

        {/* Body */}
        <div style={body}>
          {/* Name & Description */}
          <div style={sectionGap}>
            <label style={labelStyle}>Character Name</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Detective Morgan"
              style={inputStyle}
            />
          </div>

          <div style={sectionGap}>
            <label style={labelStyle}>Description</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Describe this character's appearance, clothing, features..."
              rows={3}
              style={{ ...inputStyle, resize: 'vertical' }}
            />
          </div>

          {/* Prompt */}
          <div style={sectionGap}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.25rem' }}>
              <label style={{ ...labelStyle, marginBottom: 0 }}>Prompt</label>
              <button
                onClick={() => enhanceMutation.mutate()}
                disabled={enhanceMutation.isPending}
                style={{
                  ...btnSecondary,
                  padding: '0.2rem 0.6rem',
                  fontSize: '0.7rem',
                  background: '#4c1d95',
                  borderColor: '#6d28d9',
                  color: '#c4b5fd',
                  opacity: enhanceMutation.isPending ? 0.5 : 1,
                }}
              >
                {enhanceMutation.isPending ? 'Enhancing...' : prompt.trim() ? '✨ Enhance' : '✨ Generate Prompt'}
              </button>
            </div>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Enter a prompt for generating the character image..."
              rows={4}
              style={{ ...inputStyle, resize: 'vertical' }}
            />
          </div>

          {/* Reference Images (up to 4) */}
          <div style={sectionGap}>
            <label style={labelStyle}>Reference Images (up to 4)</label>
            <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
              {refImages.map((ref, i) => (
                <div key={i} style={{ position: 'relative', width: 64, height: 64 }}>
                  <img
                    src={`/api/projects/${projectId}/assets/${ref.asset_id}/file`}
                    alt={`Ref ${i + 1}`}
                    style={{ width: 64, height: 64, objectFit: 'cover', borderRadius: '0.375rem', border: '1px solid #374151' }}
                    onError={handleImgError}
                  />
                  <button
                    onClick={() => removeRef(i)}
                    style={{
                      position: 'absolute', top: -6, right: -6, width: 18, height: 18,
                      borderRadius: '50%', background: '#dc2626', border: 'none', color: '#fff',
                      fontSize: '0.6rem', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
                    }}
                  >
                    ✕
                  </button>
                </div>
              ))}

              {refImages.length < 4 && (
                <button
                  onClick={() => openRefPicker()}
                  style={{
                    width: 64, height: 64, background: '#1f2937', border: '2px dashed #374151',
                    borderRadius: '0.375rem', color: '#6b7280', cursor: 'pointer',
                    display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1.5rem',
                  }}
                >
                  +
                </button>
              )}
            </div>
            <div style={{ fontSize: '0.65rem', color: '#6b7280', marginTop: '0.25rem' }}>
              Workflow auto-selects: {autoWorkflowType(refImages.length)} ({refImages.length} ref{refImages.length !== 1 ? 's' : ''})
            </div>
          </div>

          {/* Resolution */}
          <div style={sectionGap}>
            <label style={labelStyle}>Resolution</label>
            <select
              value={resPresetKey}
              onChange={(e) => {
                const key = e.target.value;
                setResPresetKey(key);
                if (key !== 'custom') {
                  const preset = RESOLUTION_PRESETS.find((p) => `${p.width}x${p.height}` === key);
                  if (preset) { setResWidth(preset.width); setResHeight(preset.height); }
                }
              }}
              style={{ ...inputStyle, marginBottom: '0.5rem' }}
            >
              <optgroup label="Landscape">
                {RESOLUTION_PRESETS.filter((p) => p.width > p.height).map((p) => (
                  <option key={`${p.width}x${p.height}`} value={`${p.width}x${p.height}`}>{p.label} — {p.aspect}</option>
                ))}
              </optgroup>
              <optgroup label="Square">
                {RESOLUTION_PRESETS.filter((p) => p.width === p.height).map((p) => (
                  <option key={`${p.width}x${p.height}`} value={`${p.width}x${p.height}`}>{p.label} — {p.aspect}</option>
                ))}
              </optgroup>
              <optgroup label="Portrait">
                {RESOLUTION_PRESETS.filter((p) => p.width < p.height).map((p) => (
                  <option key={`${p.width}x${p.height}`} value={`${p.width}x${p.height}`}>{p.label} — {p.aspect}</option>
                ))}
              </optgroup>
              <optgroup label="Other">
                <option value="custom">Custom</option>
              </optgroup>
            </select>

            {resPresetKey === 'custom' && (
              <div style={{ display: 'flex', gap: '0.5rem' }}>
                <div style={{ flex: 1 }}>
                  <label style={{ ...labelStyle, fontSize: '0.6rem' }}>Width</label>
                  <input
                    type="number" value={resWidth}
                    onChange={(e) => setResWidth(parseInt(e.target.value) || 512)}
                    min={256} max={4096} step={64} style={inputStyle}
                  />
                </div>
                <div style={{ flex: 1 }}>
                  <label style={{ ...labelStyle, fontSize: '0.6rem' }}>Height</label>
                  <input
                    type="number" value={resHeight}
                    onChange={(e) => setResHeight(parseInt(e.target.value) || 512)}
                    min={256} max={4096} step={64} style={inputStyle}
                  />
                </div>
              </div>
            )}
          </div>

          {/* Generate Button */}
          <div style={{ ...sectionGap, display: 'flex', gap: '0.5rem' }}>
            <button
              onClick={() => generateMutation.mutate()}
              disabled={generateMutation.isPending || !prompt.trim()}
              style={{
                ...btnPrimary,
                flex: 1,
                opacity: generateMutation.isPending || !prompt.trim() ? 0.5 : 1,
                cursor: generateMutation.isPending || !prompt.trim() ? 'not-allowed' : 'pointer',
              }}
            >
              {generateMutation.isPending ? 'Generating...' : '🎨 Generate'}
            </button>
          </div>

          {/* Image Gallery / Versions */}
          {completedVersions.length > 0 && (
            <div style={{ background: '#1f2937', borderRadius: '0.5rem', padding: '0.75rem', border: '1px solid #374151' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
                <span style={{ fontSize: '0.75rem', fontWeight: 600, color: '#d1d5db' }}>
                  Generated Images ({completedVersions.length})
                </span>
                <div style={{ display: 'flex', gap: '0.25rem', alignItems: 'center' }}>
                  <button
                    onClick={() => setGalleryIndex(Math.max(0, galleryIndex - 1))}
                    disabled={galleryIndex <= 0}
                    style={{ ...btnSecondary, padding: '0.15rem 0.4rem', fontSize: '0.7rem', opacity: galleryIndex <= 0 ? 0.3 : 1 }}
                  >
                    ◀
                  </button>
                  <span style={{ fontSize: '0.7rem', color: '#9ca3af', minWidth: 40, textAlign: 'center' }}>
                    {galleryIndex + 1} / {completedVersions.length}
                  </span>
                  <button
                    onClick={() => setGalleryIndex(Math.min(completedVersions.length - 1, galleryIndex + 1))}
                    disabled={galleryIndex >= completedVersions.length - 1}
                    style={{ ...btnSecondary, padding: '0.15rem 0.4rem', fontSize: '0.7rem', opacity: galleryIndex >= completedVersions.length - 1 ? 0.3 : 1 }}
                  >
                    ▶
                  </button>
                </div>
              </div>

              {currentVersion && currentVersion.output_path && (
                <>
                  <div
                    style={{ position: 'relative', cursor: 'pointer', marginBottom: '0.5rem' }}
                    onClick={() => setLightboxOpen(true)}
                  >
                    <img
                      src={`/api/files/${currentVersion.output_path}`}
                      alt={`Version ${galleryIndex + 1}`}
                      style={{
                        width: '100%', maxHeight: 300, objectFit: 'contain',
                        borderRadius: '0.375rem', border: '1px solid #374151',
                      }}
                      onError={handleImgError}
                    />
                    {activeImagePath === currentVersion.output_path && (
                      <span style={{
                        position: 'absolute', top: 8, left: 8, background: '#059669', color: '#fff',
                        padding: '0.15rem 0.5rem', borderRadius: '0.25rem', fontSize: '0.65rem', fontWeight: 600,
                      }}>
                        Active
                      </span>
                    )}
                  </div>

                  <div style={{ display: 'flex', gap: '0.5rem' }}>
                    <button
                      onClick={() => {
                        if (currentVersion.output_path) {
                          setActiveMutation.mutate(currentVersion.output_path);
                        }
                      }}
                      disabled={activeImagePath === currentVersion.output_path || setActiveMutation.isPending}
                      style={{
                        ...btnSecondary,
                        flex: 1,
                        fontSize: '0.75rem',
                        background: activeImagePath === currentVersion.output_path ? '#065f46' : '#1f2937',
                        borderColor: activeImagePath === currentVersion.output_path ? '#059669' : '#374151',
                        opacity: activeImagePath === currentVersion.output_path ? 0.6 : 1,
                      }}
                    >
                      {activeImagePath === currentVersion.output_path ? '✓ Active' : 'Set as Active'}
                    </button>
                    <button
                      onClick={() => {
                        if (confirm('Delete this version?')) {
                          deleteMutation.mutate(currentVersion.id);
                        }
                      }}
                      disabled={deleteMutation.isPending}
                      style={{
                        ...btnSecondary,
                        fontSize: '0.75rem',
                        color: '#f87171',
                        borderColor: '#7f1d1d',
                      }}
                    >
                      Delete
                    </button>
                  </div>
                </>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={footer}>
          <button onClick={onClose} style={{ ...btnSecondary, flex: 1 }}>Cancel</button>
          <button onClick={handleSaveAndClose} style={{ ...btnPrimary, flex: 1, background: '#059669' }}>
            {isEditing ? 'Save & Close' : 'Create & Close'}
          </button>
        </div>
      </div>

      {/* Lightbox */}
      {lightboxOpen && currentVersion?.output_path && createPortal(
        <div
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.9)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10000 }}
          onClick={() => setLightboxOpen(false)}
        >
          <img
            src={`/api/files/${currentVersion.output_path}`}
            alt="Full size"
            style={{ maxWidth: '90vw', maxHeight: '90vh', objectFit: 'contain', borderRadius: '0.5rem' }}
            onError={handleImgError}
          />
        </div>,
        document.body
      )}
    </div>,
    document.body
  )}
  </>);
}
