/**
 * AssetManageModal — Full-screen modal for managing and bulk-deleting project assets.
 * Opens from the "Manage" button on the Assets tab.
 *
 * Features:
 * - Always-visible checkbox with white border (filled blue when selected)
 * - Clickable thumbnail opens lightbox for full preview
 * - Per-item red Delete button on each card
 * - Bulk select/delete with confirmation dialog
 * - Search, filter by type, sort
 */
import { useState, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { useMutation } from '@tanstack/react-query';
import { X, Trash2, Filter, Check, CheckSquare, Square, Play, AlertTriangle, Image as ImageIcon } from 'lucide-react';
import { bulkDeleteAssets, deleteAsset } from '@/api/client';
import { useAppStore } from '@/store';
import { AssetLightbox } from './AssetPickerModal';
import { handleImgError } from '@/utils/brokenImage';
import type { Asset, AssetType } from '@/types/index';
import { parseBackendMs, parseBackendDate } from '@/utils/time';

const IMAGE_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tiff'];
const VIDEO_EXTENSIONS = ['.mp4', '.webm', '.mov', '.avi', '.mkv'];

function isImageFile(filename: string): boolean {
  return IMAGE_EXTENSIONS.some((ext) => filename.toLowerCase().endsWith(ext));
}
function isVideoFile(filename: string): boolean {
  return VIDEO_EXTENSIONS.some((ext) => filename.toLowerCase().endsWith(ext));
}

const assetTypeColors: Record<AssetType, string> = {
  character: 'bg-purple-900/60 text-purple-300',
  clothing: 'bg-pink-900/60 text-pink-300',
  item: 'bg-yellow-900/60 text-yellow-300',
  place: 'bg-green-900/60 text-green-300',
  music: 'bg-blue-900/60 text-blue-300',
  narration: 'bg-orange-900/60 text-orange-300',
  generated_image: 'bg-indigo-900/60 text-indigo-300',
  generated_video: 'bg-cyan-900/60 text-cyan-300',
  reference: 'bg-teal-900/60 text-teal-300',
};

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

const assetTypeOptions: AssetType[] = [
  'character', 'clothing', 'item', 'place', 'music',
  'narration', 'reference', 'generated_image', 'generated_video',
];

type SortBy = 'name' | 'date' | 'type' | 'size';

interface AssetManageModalProps {
  onClose: () => void;
  onAssetsDeleted: () => void;
}

export default function AssetManageModal({ onClose, onAssetsDeleted }: AssetManageModalProps) {
  const { currentProject, assets, removeAsset } = useAppStore();
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [filterType, setFilterType] = useState<AssetType | 'all'>('all');
  const [sortBy, setSortBy] = useState<SortBy>('date');
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [lightboxAsset, setLightboxAsset] = useState<Asset | null>(null);
  const [singleDeleteId, setSingleDeleteId] = useState<string | null>(null);

  // Filter and sort assets
  const filteredAssets = useMemo(() => {
    let result = assets || [];

    // Type filter
    if (filterType !== 'all') {
      result = result.filter((a) => a.asset_type === filterType);
    }

    // Search filter
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      result = result.filter((a) => a.filename.toLowerCase().includes(q));
    }

    // Sort
    result = [...result].sort((a, b) => {
      switch (sortBy) {
        case 'name':
          return a.filename.localeCompare(b.filename);
        case 'date':
          return (parseBackendMs(b.created_at) ?? 0) - (parseBackendMs(a.created_at) ?? 0);
        case 'type':
          return a.asset_type.localeCompare(b.asset_type);
        case 'size':
          return (b.file_size || 0) - (a.file_size || 0);
        default:
          return 0;
      }
    });

    return result;
  }, [assets, filterType, sortBy, searchQuery]);

  // Count per type for filter badges
  const typeCounts = useMemo(() => {
    const counts: Record<string, number> = { all: (assets || []).length };
    for (const a of assets || []) {
      counts[a.asset_type] = (counts[a.asset_type] || 0) + 1;
    }
    return counts;
  }, [assets]);

  // Selection helpers
  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAll = () => {
    if (selectedIds.size === filteredAssets.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(filteredAssets.map((a) => a.id)));
    }
  };

  const allSelected = filteredAssets.length > 0 && selectedIds.size === filteredAssets.length;

  // Bulk delete mutation
  const bulkDeleteMutation = useMutation({
    mutationFn: async () => {
      if (!currentProject) return;
      const ids = Array.from(selectedIds);
      await bulkDeleteAssets(currentProject.id, ids);
      for (const id of ids) {
        removeAsset(id);
      }
    },
    onSuccess: () => {
      setSelectedIds(new Set());
      setShowDeleteConfirm(false);
      onAssetsDeleted();
    },
  });

  // Single delete mutation
  const singleDeleteMutation = useMutation({
    mutationFn: async (assetId: string) => {
      if (!currentProject) return;
      await deleteAsset(currentProject.id, assetId);
      removeAsset(assetId);
    },
    onSuccess: () => {
      setSingleDeleteId(null);
      onAssetsDeleted();
    },
  });

  function formatFileSize(bytes?: number): string {
    if (!bytes) return '—';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function formatDate(dateStr: string): string {
    const d = parseBackendDate(dateStr);
    if (!d) return '';
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  }

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(0,0,0,0.85)' }}
    >
      <div className="bg-gray-900 rounded-xl shadow-2xl border border-gray-700 flex flex-col"
        style={{ width: '90vw', maxWidth: '1400px', height: '85vh' }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-700">
          <div className="flex items-center gap-3">
            <ImageIcon size={20} className="text-blue-400" />
            <h2 className="text-lg font-semibold text-white">Manage Assets</h2>
            <span className="text-sm text-gray-400">
              {filteredAssets.length} asset{filteredAssets.length !== 1 ? 's' : ''}
              {selectedIds.size > 0 && (
                <span className="text-blue-400 ml-2">({selectedIds.size} selected)</span>
              )}
            </span>
          </div>
          <div className="flex items-center gap-3">
            {selectedIds.size > 0 && (
              <button
                onClick={() => setShowDeleteConfirm(true)}
                className="px-4 py-2 bg-red-600 hover:bg-red-700 rounded-lg text-sm font-medium transition-colors flex items-center gap-2"
              >
                <Trash2 size={16} />
                Delete Selected ({selectedIds.size})
              </button>
            )}
            <button
              onClick={onClose}
              className="p-2 rounded-lg hover:bg-gray-700 transition-colors text-gray-400 hover:text-white"
            >
              <X size={20} />
            </button>
          </div>
        </div>

        {/* Toolbar: Search, Filter, Sort, Select All */}
        <div className="px-6 py-3 border-b border-gray-800 flex flex-wrap items-center gap-4">
          {/* Search */}
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search by filename..."
            className="px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-gray-100 text-sm focus:outline-none focus:border-blue-500 w-64"
          />

          {/* Filter by type */}
          <div className="flex items-center gap-2">
            <Filter size={14} className="text-gray-400" />
            <select
              value={filterType}
              onChange={(e) => {
                setFilterType(e.target.value as AssetType | 'all');
                setSelectedIds(new Set());
              }}
              className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-gray-100 text-sm focus:outline-none focus:border-blue-500"
            >
              <option value="all">All Types ({typeCounts.all || 0})</option>
              {assetTypeOptions.map((type) => (
                typeCounts[type] ? (
                  <option key={type} value={type}>
                    {assetTypeLabels[type]} ({typeCounts[type]})
                  </option>
                ) : null
              ))}
            </select>
          </div>

          {/* Sort */}
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-400">Sort:</span>
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as SortBy)}
              className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-gray-100 text-sm focus:outline-none focus:border-blue-500"
            >
              <option value="date">Date (Newest)</option>
              <option value="name">Name (A-Z)</option>
              <option value="type">Type</option>
              <option value="size">Size (Largest)</option>
            </select>
          </div>

          {/* Select All */}
          <button
            onClick={selectAll}
            className="flex items-center gap-2 px-3 py-2 rounded-lg hover:bg-gray-700 transition-colors text-sm text-gray-300"
          >
            {allSelected ? <CheckSquare size={16} className="text-blue-400" /> : <Square size={16} />}
            {allSelected ? 'Deselect All' : 'Select All'}
          </button>
        </div>

        {/* Asset Grid */}
        <div className="flex-1 overflow-y-auto p-6">
          {filteredAssets.length === 0 ? (
            <div className="text-center text-gray-400 text-sm py-16">
              <ImageIcon size={48} className="mx-auto mb-4 text-gray-600" />
              <p className="text-lg mb-1">No assets found</p>
              <p className="text-xs text-gray-500">
                {searchQuery ? 'Try a different search term' : 'Upload files from the Assets tab to get started'}
              </p>
            </div>
          ) : (
            <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))' }}>
              {filteredAssets.map((asset) => {
                const isImage = isImageFile(asset.filename);
                const isVideo = isVideoFile(asset.filename);
                const hasVisual = isImage || isVideo;
                const selected = selectedIds.has(asset.id);

                return (
                  <div
                    key={asset.id}
                    className={`bg-gray-800 rounded-lg overflow-hidden transition-all border-2 group ${
                      selected
                        ? 'border-blue-500 ring-1 ring-blue-500/30'
                        : 'border-transparent hover:border-gray-600'
                    }`}
                  >
                    {/* Thumbnail — click opens lightbox for preview */}
                    <div
                      className={`relative w-full h-32 overflow-hidden ${hasVisual ? 'cursor-pointer' : 'cursor-default'}`}
                      style={{ background: '#12121e' }}
                      onClick={() => { if (hasVisual) setLightboxAsset(asset); }}
                    >
                      {isImage ? (
                        <img
                          src={`/api/projects/${asset.project_id}/assets/${asset.id}/file`}
                          alt={asset.filename}
                          className="w-full h-full object-cover"
                          loading="lazy"
                          onError={handleImgError}
                        />
                      ) : isVideo ? (
                        <>
                          <video
                            src={`/api/projects/${asset.project_id}/assets/${asset.id}/file#t=0.5`}
                            className="w-full h-full object-cover"
                            muted
                            preload="metadata"
                          />
                          <div className="absolute inset-0 flex items-center justify-center bg-black/30">
                            <div className="w-8 h-8 rounded-full bg-white/20 flex items-center justify-center backdrop-blur-sm">
                              <Play size={16} className="text-white ml-0.5" />
                            </div>
                          </div>
                        </>
                      ) : (
                        <div className="w-full h-full flex items-center justify-center">
                          <span className="text-xs text-gray-500 uppercase">{asset.filename.split('.').pop()}</span>
                        </div>
                      )}

                      {/* Checkbox overlay — ALWAYS visible with white border */}
                      <div
                        className="absolute top-2 left-2"
                        onClick={(e) => {
                          e.stopPropagation();
                          toggleSelect(asset.id);
                        }}
                      >
                        <div className={`w-6 h-6 rounded flex items-center justify-center cursor-pointer ${
                          selected
                            ? 'bg-blue-500 border-2 border-blue-400'
                            : 'bg-black/40 border-2 border-white/80'
                        }`}>
                          {selected && <Check size={14} className="text-white" />}
                        </div>
                      </div>
                    </div>

                    {/* Info */}
                    <div className="p-3 space-y-2">
                      <p className="text-sm font-medium text-gray-100 truncate" title={asset.filename}>
                        {asset.filename}
                      </p>
                      <div className="flex items-center justify-between">
                        <span className={`text-xs px-2 py-0.5 rounded font-medium ${assetTypeColors[asset.asset_type]}`}>
                          {assetTypeLabels[asset.asset_type]}
                        </span>
                        <span className="text-xs text-gray-500">{formatFileSize(asset.file_size)}</span>
                      </div>
                      <div className="flex items-center justify-between text-xs text-gray-500">
                        <span>{formatDate(asset.created_at)}</span>
                        {asset.width && asset.height && (
                          <span>{asset.width}x{asset.height}</span>
                        )}
                      </div>

                      {/* Per-item Delete button */}
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setSingleDeleteId(asset.id);
                        }}
                        className="w-full mt-1 px-3 py-1.5 bg-red-900/40 hover:bg-red-700 border border-red-800/50 hover:border-red-600 rounded text-xs font-medium text-red-300 hover:text-white transition-colors flex items-center justify-center gap-1.5"
                      >
                        <Trash2 size={12} />
                        Delete
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Bulk Delete Confirmation Dialog */}
      {showDeleteConfirm && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center"
          style={{ backgroundColor: 'rgba(0,0,0,0.7)' }}
          onClick={() => setShowDeleteConfirm(false)}
        >
          <div
            className="bg-gray-900 rounded-xl border border-red-900/50 shadow-2xl p-6 max-w-md w-full mx-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start gap-4 mb-4">
              <div className="p-3 rounded-full bg-red-900/30">
                <AlertTriangle size={24} className="text-red-400" />
              </div>
              <div>
                <h3 className="text-lg font-semibold text-white mb-2">Delete {selectedIds.size} Asset{selectedIds.size !== 1 ? 's' : ''}?</h3>
                <p className="text-sm text-gray-300 leading-relaxed">
                  This will delete these assets and will not be reversible. They are lost forever so be careful.
                </p>
              </div>
            </div>

            <div className="flex justify-end gap-3 mt-6">
              <button
                onClick={() => setShowDeleteConfirm(false)}
                className="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded-lg text-sm font-medium transition-colors"
                disabled={bulkDeleteMutation.isPending}
              >
                Cancel
              </button>
              <button
                onClick={() => bulkDeleteMutation.mutate()}
                className="px-4 py-2 bg-red-600 hover:bg-red-700 rounded-lg text-sm font-medium transition-colors flex items-center gap-2"
                disabled={bulkDeleteMutation.isPending}
              >
                <Trash2 size={16} />
                {bulkDeleteMutation.isPending ? 'Deleting...' : 'Delete Forever'}
              </button>
            </div>

            {bulkDeleteMutation.isError && (
              <p className="mt-3 text-sm text-red-400">
                Failed to delete assets. Please try again.
              </p>
            )}
          </div>
        </div>
      )}

      {/* Single Delete Confirmation Dialog */}
      {singleDeleteId && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center"
          style={{ backgroundColor: 'rgba(0,0,0,0.7)' }}
          onClick={() => setSingleDeleteId(null)}
        >
          <div
            className="bg-gray-900 rounded-xl border border-red-900/50 shadow-2xl p-6 max-w-md w-full mx-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start gap-4 mb-4">
              <div className="p-3 rounded-full bg-red-900/30">
                <AlertTriangle size={24} className="text-red-400" />
              </div>
              <div>
                <h3 className="text-lg font-semibold text-white mb-2">Delete Asset?</h3>
                <p className="text-sm text-gray-300 leading-relaxed">
                  This will delete this asset and will not be reversible. It is lost forever so be careful.
                </p>
              </div>
            </div>

            <div className="flex justify-end gap-3 mt-6">
              <button
                onClick={() => setSingleDeleteId(null)}
                className="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded-lg text-sm font-medium transition-colors"
                disabled={singleDeleteMutation.isPending}
              >
                Cancel
              </button>
              <button
                onClick={() => singleDeleteMutation.mutate(singleDeleteId)}
                className="px-4 py-2 bg-red-600 hover:bg-red-700 rounded-lg text-sm font-medium transition-colors flex items-center gap-2"
                disabled={singleDeleteMutation.isPending}
              >
                <Trash2 size={16} />
                {singleDeleteMutation.isPending ? 'Deleting...' : 'Delete Forever'}
              </button>
            </div>

            {singleDeleteMutation.isError && (
              <p className="mt-3 text-sm text-red-400">
                Failed to delete asset. Please try again.
              </p>
            )}
          </div>
        </div>
      )}

      {/* Lightbox for full-size image/video preview */}
      {lightboxAsset && (
        <AssetLightbox
          asset={lightboxAsset}
          onClose={() => setLightboxAsset(null)}
        />
      )}
    </div>,
    document.body
  );
}
