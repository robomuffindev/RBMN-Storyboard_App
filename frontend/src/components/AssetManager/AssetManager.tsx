import { useRef, useState } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { getAssets, uploadAsset, deleteAsset } from '@/api/client';
import { useAppStore } from '@/store';
import { Upload, Trash2, Filter } from 'lucide-react';
import type { AssetType } from '@/types/index';

const assetTypeColors: Record<AssetType, string> = {
  character: 'bg-purple-900',
  clothing: 'bg-pink-900',
  item: 'bg-yellow-900',
  place: 'bg-green-900',
  music: 'bg-blue-900',
  narration: 'bg-orange-900',
  generated_image: 'bg-indigo-900',
  generated_video: 'bg-cyan-900',
  reference: 'bg-teal-900',
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

export default function AssetManager() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selectedType, setSelectedType] = useState<AssetType | 'all'>('all');
  const [assetTypeForUpload, setAssetTypeForUpload] = useState<AssetType>('item');
  const { currentProject, assets, addAsset, removeAsset } = useAppStore();
  const [uploading, setUploading] = useState(false);

  const { refetch } = useQuery({
    queryKey: ['assets', currentProject?.id],
    queryFn: async () => {
      if (!currentProject) return [];
      const response = await getAssets(currentProject.id);
      return response.data;
    },
    enabled: !!currentProject,
  });

  const deleteAssetMutation = useMutation({
    mutationFn: async (assetId: string) => {
      if (!currentProject) return;
      if (!window.confirm('Delete this asset? This is permanent and cannot be undone.')) {
        throw new Error('cancelled');
      }
      await deleteAsset(currentProject.id, assetId);
      removeAsset(assetId);
    },
  });

  const handleFileSelect = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = event.currentTarget.files;
    if (!files || !currentProject) return;

    setUploading(true);
    try {
      for (const file of files) {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('asset_type', assetTypeForUpload);
        const response = await uploadAsset(currentProject.id, formData);
        addAsset(response.data);
      }
      refetch();
    } catch (error) {
      console.error('Upload failed:', error);
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const filteredAssets = (assets || []).filter(
    (asset) => selectedType === 'all' || asset.asset_type === selectedType
  );

  const assetTypeOptions: AssetType[] = [
    'character',
    'clothing',
    'item',
    'place',
    'music',
    'narration',
    'reference',
    'generated_image',
    'generated_video',
  ];

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Filter & Upload */}
      <div className="p-3 border-b border-gray-800 space-y-3">
        <button
          onClick={() => fileInputRef.current?.click()}
          disabled={uploading}
          className="w-full px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded text-sm font-medium transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
        >
          <Upload size={16} />
          {uploading ? 'Uploading...' : 'Upload Asset'}
        </button>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          onChange={handleFileSelect}
          className="hidden"
          accept="image/*,audio/*,video/*"
        />

        <div className="space-y-2">
          <label className="block text-xs font-medium text-gray-400">Asset Type</label>
          <select
            value={assetTypeForUpload}
            onChange={(e) => setAssetTypeForUpload(e.target.value as AssetType)}
            className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-gray-100 text-sm focus:outline-none focus:border-blue-500"
          >
            {assetTypeOptions.map(type => (
              <option key={type} value={type}>{assetTypeLabels[type]}</option>
            ))}
          </select>
        </div>

        <div className="flex items-center gap-2 text-xs">
          <Filter size={14} className="text-gray-400" />
          <select
            value={selectedType}
            onChange={(e) => setSelectedType(e.target.value as AssetType | 'all')}
            className="flex-1 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-gray-100 text-sm focus:outline-none focus:border-blue-500"
          >
            <option value="all">All Types</option>
            {assetTypeOptions.map(type => (
              <option key={type} value={type}>{assetTypeLabels[type]}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Asset Grid */}
      <div className="flex-1 overflow-y-auto p-3">
        {filteredAssets.length === 0 ? (
          <div className="text-center text-gray-400 text-sm py-8">
            <p>No assets yet</p>
            <p className="text-xs text-gray-500 mt-2">Upload files to get started</p>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-3">
            {filteredAssets.map((asset) => (
              <div
                key={asset.id}
                className="bg-gray-800 rounded overflow-hidden hover:bg-gray-700 transition-colors group border border-gray-700"
              >
                {asset.asset_type.startsWith('generated') || asset.asset_type === 'reference' ? (
                  <div className="w-full h-24 bg-gradient-to-br from-gray-700 to-gray-800 flex items-center justify-center">
                    <div className="text-xs text-gray-500">{assetTypeLabels[asset.asset_type]}</div>
                  </div>
                ) : null}
                <div className="p-2">
                  <p className="text-xs font-medium text-gray-100 truncate" title={asset.filename}>
                    {asset.filename}
                  </p>
                  <div className="flex items-center justify-between mt-2">
                    <span
                      className={`text-xs px-2 py-1 rounded text-white font-medium ${
                        assetTypeColors[asset.asset_type]
                      }`}
                    >
                      {assetTypeLabels[asset.asset_type]}
                    </span>
                    <button
                      onClick={() => {
                        if (confirm('Delete this asset?')) {
                          deleteAssetMutation.mutate(asset.id);
                        }
                      }}
                      className="opacity-0 group-hover:opacity-100 transition-opacity text-red-500 hover:text-red-400 disabled:opacity-50"
                      disabled={deleteAssetMutation.isPending}
                      title="Delete asset"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
