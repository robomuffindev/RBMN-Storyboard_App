/**
 * AssetPickerModal — reusable modal for choosing between uploading from computer
 * or selecting from existing project assets.
 *
 * Used by: ReferenceSelector, CharacterCreatorModal, and anywhere else
 * that needs "pick an image from assets or upload a new one."
 */
import { useState } from 'react';
import { createPortal } from 'react-dom';
import { Upload, Image, X, Check, Filter } from 'lucide-react';
import { handleImgError } from '@/utils/brokenImage';
import type { Asset, AssetType } from '@/types/index';

const IMAGE_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tiff'];
const VIDEO_EXTENSIONS = ['.mp4', '.webm', '.mov', '.avi', '.mkv'];

function isImageFile(filename: string): boolean {
  const lower = filename.toLowerCase();
  return IMAGE_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

function isVideoFile(filename: string): boolean {
  const lower = filename.toLowerCase();
  return VIDEO_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

// ─── Source Chooser (step 1) ──────────────────────────────────────────

interface SourceChooserProps {
  onChooseUpload: () => void;
  onChooseAsset: () => void;
  onClose: () => void;
  title?: string;
}

function SourceChooser({ onChooseUpload, onChooseAsset, onClose, title }: SourceChooserProps) {
  return createPortal(
    <div
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10000,
      }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        style={{
          background: '#111827', border: '1px solid #374151', borderRadius: '0.75rem',
          width: '100%', maxWidth: '400px', overflow: 'hidden',
        }}
      >
        <div style={{
          padding: '1rem 1.5rem', borderBottom: '1px solid #374151',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}>
          <h3 style={{ fontSize: '1rem', fontWeight: 600, color: '#f3f4f6' }}>
            {title || 'Add Reference Image'}
          </h3>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: '#9ca3af', cursor: 'pointer' }}>
            <X size={18} />
          </button>
        </div>
        <div style={{ padding: '1.5rem', display: 'flex', gap: '1rem' }}>
          <button
            onClick={onChooseUpload}
            style={{
              flex: 1, padding: '1.5rem 1rem', background: '#1f2937', border: '2px solid #374151',
              borderRadius: '0.75rem', color: '#e5e7eb', cursor: 'pointer',
              display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.75rem',
              transition: 'border-color 0.2s, background 0.2s',
            }}
            onMouseEnter={(e) => { e.currentTarget.style.borderColor = '#3b82f6'; e.currentTarget.style.background = '#1e293b'; }}
            onMouseLeave={(e) => { e.currentTarget.style.borderColor = '#374151'; e.currentTarget.style.background = '#1f2937'; }}
          >
            <Upload size={28} />
            <span style={{ fontSize: '0.875rem', fontWeight: 500 }}>Upload from Computer</span>
            <span style={{ fontSize: '0.7rem', color: '#6b7280' }}>Browse your files</span>
          </button>
          <button
            onClick={onChooseAsset}
            style={{
              flex: 1, padding: '1.5rem 1rem', background: '#1f2937', border: '2px solid #374151',
              borderRadius: '0.75rem', color: '#e5e7eb', cursor: 'pointer',
              display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.75rem',
              transition: 'border-color 0.2s, background 0.2s',
            }}
            onMouseEnter={(e) => { e.currentTarget.style.borderColor = '#3b82f6'; e.currentTarget.style.background = '#1e293b'; }}
            onMouseLeave={(e) => { e.currentTarget.style.borderColor = '#374151'; e.currentTarget.style.background = '#1f2937'; }}
          >
            <Image size={28} />
            <span style={{ fontSize: '0.875rem', fontWeight: 500 }}>Choose from Assets</span>
            <span style={{ fontSize: '0.7rem', color: '#6b7280' }}>Select an uploaded asset</span>
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}

// ─── Asset Gallery Browser (step 2) ──────────────────────────────────

interface AssetGalleryProps {
  assets: Asset[];
  onSelect: (asset: Asset) => void;
  onClose: () => void;
  filterTypes?: AssetType[];
  imagesOnly?: boolean;
  title?: string;
}

const assetTypeLabels: Record<AssetType, string> = {
  character: 'Character',
  clothing: 'Clothing',
  item: 'Item',
  place: 'Place',
  music: 'Music',
  narration: 'Narration',
  generated_image: 'Generated Image',
  generated_video: 'Generated Video',
  reference: 'Reference',
};

function AssetGallery({ assets, onSelect, onClose, imagesOnly = true, title }: AssetGalleryProps) {
  const [filterType, setFilterType] = useState<AssetType | 'all'>('all');
  const [selectedAsset, setSelectedAsset] = useState<Asset | null>(null);

  // Filter to only image assets if imagesOnly
  const filteredAssets = assets.filter((a) => {
    if (imagesOnly && !isImageFile(a.filename)) return false;
    if (filterType !== 'all' && a.asset_type !== filterType) return false;
    return true;
  });

  // Get unique asset types from available assets for the filter dropdown
  const availableTypes = [...new Set(filteredAssets.map((a) => a.asset_type))];

  return createPortal(
    <div
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10000,
      }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        style={{
          background: '#111827', border: '1px solid #374151', borderRadius: '0.75rem',
          width: '100%', maxWidth: '640px', maxHeight: '80vh', display: 'flex', flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        {/* Header */}
        <div style={{
          padding: '1rem 1.5rem', borderBottom: '1px solid #374151',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0,
        }}>
          <h3 style={{ fontSize: '1rem', fontWeight: 600, color: '#f3f4f6' }}>
            {title || 'Choose from Assets'}
          </h3>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: '#9ca3af', cursor: 'pointer' }}>
            <X size={18} />
          </button>
        </div>

        {/* Filter */}
        <div style={{ padding: '0.75rem 1.5rem', borderBottom: '1px solid #1f2937', flexShrink: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <Filter size={14} style={{ color: '#6b7280' }} />
            <select
              value={filterType}
              onChange={(e) => setFilterType(e.target.value as AssetType | 'all')}
              style={{
                flex: 1, background: '#1f2937', border: '1px solid #374151', borderRadius: '0.375rem',
                padding: '0.375rem 0.5rem', color: '#e5e7eb', fontSize: '0.8125rem',
              }}
            >
              <option value="all">All Types ({filteredAssets.length})</option>
              {availableTypes.map((type) => (
                <option key={type} value={type}>
                  {assetTypeLabels[type]} ({assets.filter((a) => a.asset_type === type && (!imagesOnly || isImageFile(a.filename))).length})
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Gallery Grid */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '1rem 1.5rem' }}>
          {filteredAssets.length === 0 ? (
            <div style={{ textAlign: 'center', color: '#6b7280', padding: '2rem 0', fontSize: '0.875rem' }}>
              No {imagesOnly ? 'image' : ''} assets found. Upload some first in the Assets tab.
            </div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '0.75rem' }}>
              {filteredAssets.map((asset) => {
                const isSelected = selectedAsset?.id === asset.id;
                return (
                  <button
                    key={asset.id}
                    onClick={() => setSelectedAsset(asset)}
                    style={{
                      position: 'relative', background: 'none', border: isSelected ? '2px solid #3b82f6' : '2px solid #374151',
                      borderRadius: '0.5rem', overflow: 'hidden', cursor: 'pointer', padding: 0,
                      aspectRatio: '1', transition: 'border-color 0.15s',
                    }}
                    onMouseEnter={(e) => { if (!isSelected) e.currentTarget.style.borderColor = '#4b5563'; }}
                    onMouseLeave={(e) => { if (!isSelected) e.currentTarget.style.borderColor = '#374151'; }}
                  >
                    <img
                      src={`/api/projects/${asset.project_id}/assets/${asset.id}/file`}
                      alt={asset.filename}
                      style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
                      onError={handleImgError}
                    />
                    {isSelected && (
                      <div style={{
                        position: 'absolute', top: 4, right: 4, width: 22, height: 22,
                        borderRadius: '50%', background: '#3b82f6',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                      }}>
                        <Check size={14} style={{ color: '#fff' }} />
                      </div>
                    )}
                    <div style={{
                      position: 'absolute', bottom: 0, left: 0, right: 0,
                      background: 'linear-gradient(transparent, rgba(0,0,0,0.8))',
                      padding: '0.25rem 0.375rem',
                    }}>
                      <div style={{
                        fontSize: '0.6rem', color: '#d1d5db', whiteSpace: 'nowrap',
                        overflow: 'hidden', textOverflow: 'ellipsis',
                      }}>
                        {asset.filename}
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{
          padding: '0.75rem 1.5rem', borderTop: '1px solid #374151', flexShrink: 0,
          display: 'flex', justifyContent: 'flex-end', gap: '0.75rem',
        }}>
          <button
            onClick={onClose}
            style={{
              padding: '0.5rem 1.25rem', background: '#374151', border: 'none',
              borderRadius: '0.375rem', color: '#d1d5db', cursor: 'pointer', fontSize: '0.875rem',
            }}
          >
            Cancel
          </button>
          <button
            onClick={() => { if (selectedAsset) { onSelect(selectedAsset); onClose(); } }}
            disabled={!selectedAsset}
            style={{
              padding: '0.5rem 1.25rem', background: selectedAsset ? '#3b82f6' : '#1f2937',
              border: 'none', borderRadius: '0.375rem',
              color: selectedAsset ? '#fff' : '#4b5563',
              cursor: selectedAsset ? 'pointer' : 'not-allowed', fontSize: '0.875rem', fontWeight: 500,
            }}
          >
            Select
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}

// ─── Asset Lightbox (for full-size preview) ──────────────────────────

interface AssetLightboxProps {
  asset: Asset;
  onClose: () => void;
}

export function AssetLightbox({ asset, onClose }: AssetLightboxProps) {
  const isVideo = isVideoFile(asset.filename);

  return createPortal(
    <div
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.85)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10000,
        cursor: 'pointer',
      }}
      onClick={onClose}
    >
      <button
        onClick={onClose}
        style={{
          position: 'absolute', top: 16, right: 16, background: 'rgba(0,0,0,0.5)',
          border: '1px solid #4b5563', borderRadius: '50%', width: 36, height: 36,
          color: '#e5e7eb', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
          zIndex: 10001,
        }}
      >
        <X size={20} />
      </button>
      <div onClick={(e) => e.stopPropagation()} style={{ maxWidth: '90vw', maxHeight: '90vh' }}>
        {isVideo ? (
          <video
            src={`/api/projects/${asset.project_id}/assets/${asset.id}/file`}
            controls
            autoPlay
            style={{ maxWidth: '90vw', maxHeight: '85vh', borderRadius: '0.5rem' }}
          />
        ) : (
          <img
            src={`/api/projects/${asset.project_id}/assets/${asset.id}/file`}
            alt={asset.filename}
            style={{ maxWidth: '90vw', maxHeight: '85vh', borderRadius: '0.5rem', objectFit: 'contain' }}
            onError={handleImgError}
          />
        )}
        <div style={{ textAlign: 'center', marginTop: '0.75rem', color: '#9ca3af', fontSize: '0.8125rem' }}>
          {asset.filename}
          {asset.width && asset.height ? ` — ${asset.width}×${asset.height}` : ''}
        </div>
      </div>
    </div>,
    document.body
  );
}

// ─── Main Export: useAssetPicker hook ─────────────────────────────────

interface UseAssetPickerOptions {
  assets: Asset[];
  onFileUpload: (file: File) => void;
  onAssetSelect: (asset: Asset) => void;
  accept?: string;
  imagesOnly?: boolean;
  title?: string;
}

/**
 * Hook that manages the two-step picker flow:
 *   1. Show "Upload or Asset?" chooser
 *   2. If "Asset", show gallery; if "Upload", trigger file input
 *
 * Returns { openPicker, PickerModals } — call openPicker() to start,
 * render <PickerModals /> in your component.
 */
export function useAssetPicker({
  assets,
  onFileUpload,
  onAssetSelect,
  accept = 'image/*',
  imagesOnly = true,
  title,
}: UseAssetPickerOptions) {
  const [step, setStep] = useState<'closed' | 'chooser' | 'gallery'>('closed');

  const openPicker = () => setStep('chooser');
  const close = () => setStep('closed');

  const handleUploadClick = () => {
    close();
    // Slight delay to ensure modal is gone before file picker opens
    setTimeout(() => {
      const input = document.createElement('input');
      input.type = 'file';
      input.accept = accept;
      input.onchange = (e) => {
        const file = (e.target as HTMLInputElement).files?.[0];
        if (file) onFileUpload(file);
      };
      input.click();
    }, 50);
  };

  const handleAssetSelect = (asset: Asset) => {
    onAssetSelect(asset);
    close();
  };

  const PickerModals = () => (
    <>
      {step === 'chooser' && (
        <SourceChooser
          onChooseUpload={handleUploadClick}
          onChooseAsset={() => setStep('gallery')}
          onClose={close}
          title={title}
        />
      )}
      {step === 'gallery' && (
        <AssetGallery
          assets={assets}
          onSelect={handleAssetSelect}
          onClose={close}
          imagesOnly={imagesOnly}
          title={title}
        />
      )}
    </>
  );

  return { openPicker, PickerModals };
}

// Re-export helpers for other components
export { isImageFile, isVideoFile };
