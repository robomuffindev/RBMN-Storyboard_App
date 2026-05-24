import { useState } from 'react';
import { Plus, X, Play, Pencil, Check, Loader2, AlertCircle, CheckCircle } from 'lucide-react';
import type { BatchItemConfig, BatchRunStatus } from '@/types/index';

interface BatchQueuePanelProps {
  items: BatchItemConfig[];
  onUpdateItem: (index: number, item: BatchItemConfig) => void;
  onRemoveItem: (index: number) => void;
  onAddMore: () => void;
  onRunBatch: () => void;
  onClose: () => void;
  batchStatus: BatchRunStatus | null;
  isRunning: boolean;
  onCancel: () => void;
}

function StatusDot({ status }: { status: string }) {
  const colors: Record<string, string> = {
    pending: 'bg-gray-500',
    running: 'bg-blue-500 animate-pulse',
    done: 'bg-green-500',
    failed: 'bg-red-500',
  };
  return <span className={`inline-block w-2.5 h-2.5 rounded-full ${colors[status] || 'bg-gray-500'}`} />;
}

function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case 'running':
      return <Loader2 size={16} className="text-blue-400 animate-spin" />;
    case 'done':
      return <CheckCircle size={16} className="text-green-400" />;
    case 'failed':
      return <AlertCircle size={16} className="text-red-400" />;
    default:
      return <StatusDot status={status} />;
  }
}

interface BatchItemCardProps {
  item: BatchItemConfig;
  index: number;
  onUpdate: (index: number, item: BatchItemConfig) => void;
  onRemove: (index: number) => void;
  isRunning: boolean;
  itemStatus?: { status: string; current_step: string; error: string | null };
}

function BatchItemCard({ item, index, onUpdate, onRemove, isRunning, itemStatus }: BatchItemCardProps) {
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState(item.project_name);
  const [editVideoMode, setEditVideoMode] = useState(item.video_mode);
  const [editTwoPass, setEditTwoPass] = useState(item.two_pass);
  const [editStoryFlow, setEditStoryFlow] = useState(item.use_story_flow);

  const handleSave = () => {
    onUpdate(index, {
      ...item,
      project_name: editName,
      video_mode: editVideoMode,
      two_pass: editTwoPass,
      use_story_flow: editStoryFlow,
    });
    setEditing(false);
  };

  const handleCancel = () => {
    setEditName(item.project_name);
    setEditVideoMode(item.video_mode);
    setEditTwoPass(item.two_pass);
    setEditStoryFlow(item.use_story_flow);
    setEditing(false);
  };

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          {/* Header row */}
          <div className="flex items-center gap-2 mb-1">
            {itemStatus && <StatusIcon status={itemStatus.status} />}
            {editing ? (
              <input
                type="text"
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                className="px-2 py-1 bg-gray-800 border border-gray-700 rounded text-sm text-gray-100 focus:outline-none focus:border-blue-500"
                autoFocus
              />
            ) : (
              <span className="text-sm font-semibold text-gray-100 truncate">{item.project_name}</span>
            )}
          </div>

          {/* Audio filename */}
          <p className="text-xs text-gray-500 truncate mb-2">{item.audio_filename}</p>

          {/* Labels */}
          {editing ? (
            <div className="flex flex-col gap-2 mt-2">
              <div className="flex items-center gap-3">
                <span className="text-xs text-gray-400 w-20">Video Mode:</span>
                <label className="flex items-center gap-1 text-xs text-gray-300 cursor-pointer">
                  <input
                    type="radio"
                    checked={editVideoMode === 'i2v'}
                    onChange={() => setEditVideoMode('i2v')}
                    className="w-3 h-3"
                  />
                  I2V
                </label>
                <label className="flex items-center gap-1 text-xs text-gray-300 cursor-pointer">
                  <input
                    type="radio"
                    checked={editVideoMode === 'v2v'}
                    onChange={() => setEditVideoMode('v2v')}
                    className="w-3 h-3"
                  />
                  V2V
                </label>
              </div>
              <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer">
                <input
                  type="checkbox"
                  checked={editTwoPass}
                  onChange={(e) => setEditTwoPass(e.target.checked)}
                  className="w-3 h-3"
                />
                Two-pass
              </label>
              <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer">
                <input
                  type="checkbox"
                  checked={editStoryFlow}
                  onChange={(e) => setEditStoryFlow(e.target.checked)}
                  className="w-3 h-3"
                />
                Story flow
              </label>
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              <span className="px-2 py-0.5 bg-gray-800 rounded text-xs text-gray-400">
                {item.render_type === 'music_video' ? 'Music Video' : 'Narration'}
              </span>
              <span className="px-2 py-0.5 bg-gray-800 rounded text-xs text-gray-400 uppercase">
                {item.video_mode}
              </span>
              {item.two_pass && (
                <span className="px-2 py-0.5 bg-gray-800 rounded text-xs text-gray-400">2-pass</span>
              )}
              {item.use_story_flow && (
                <span className="px-2 py-0.5 bg-gray-800 rounded text-xs text-gray-400">Flow</span>
              )}
            </div>
          )}

          {/* Status step text */}
          {itemStatus?.current_step && (
            <p className="text-xs text-blue-400 mt-2">{itemStatus.current_step}</p>
          )}
          {itemStatus?.error && (
            <p className="text-xs text-red-400 mt-1">{itemStatus.error}</p>
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-1 shrink-0">
          {editing ? (
            <>
              <button
                onClick={handleSave}
                className="p-1.5 text-green-400 hover:text-green-300 transition-colors"
                title="Save"
              >
                <Check size={16} />
              </button>
              <button
                onClick={handleCancel}
                className="p-1.5 text-gray-400 hover:text-gray-200 transition-colors"
                title="Cancel"
              >
                <X size={16} />
              </button>
            </>
          ) : (
            <>
              {!isRunning && (
                <button
                  onClick={() => setEditing(true)}
                  className="p-1.5 text-gray-400 hover:text-gray-200 transition-colors"
                  title="Edit"
                >
                  <Pencil size={16} />
                </button>
              )}
              <button
                onClick={() => onRemove(index)}
                disabled={isRunning}
                className="p-1.5 text-gray-400 hover:text-red-400 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                title="Remove"
              >
                <X size={16} />
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default function BatchQueuePanel({
  items,
  onUpdateItem,
  onRemoveItem,
  onAddMore,
  onRunBatch,
  onClose,
  batchStatus,
  isRunning,
  onCancel,
}: BatchQueuePanelProps) {
  const completedCount = batchStatus?.completed_items ?? 0;
  const totalCount = batchStatus?.total_items ?? items.length;

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-8">
      <div className="max-w-3xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-2xl font-bold">Batch Mode</h1>
          <button
            onClick={onClose}
            disabled={isRunning}
            className="p-2 text-gray-400 hover:text-gray-200 transition-colors disabled:opacity-30"
            title="Close batch mode"
          >
            <X size={24} />
          </button>
        </div>

        {/* Add Item button */}
        {!isRunning && (
          <button
            onClick={onAddMore}
            className="mb-6 px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded text-sm font-medium transition-colors flex items-center gap-2"
          >
            <Plus size={16} />
            Add Item
          </button>
        )}

        {/* Progress bar when running */}
        {isRunning && batchStatus && (
          <div className="mb-6">
            <div className="flex items-center justify-between text-sm text-gray-400 mb-2">
              <span>
                {batchStatus.status === 'done'
                  ? 'Batch complete'
                  : batchStatus.status === 'failed'
                    ? 'Batch failed'
                    : batchStatus.status === 'cancelled'
                      ? 'Batch cancelled'
                      : `Processing ${completedCount + 1} of ${totalCount}`}
              </span>
              <span>{completedCount} / {totalCount} done</span>
            </div>
            <div className="w-full h-2 bg-gray-800 rounded-full overflow-hidden">
              <div
                className="h-full bg-blue-600 transition-all duration-500 rounded-full"
                style={{ width: totalCount > 0 ? `${(completedCount / totalCount) * 100}%` : '0%' }}
              />
            </div>
          </div>
        )}

        {/* Queue list */}
        {items.length === 0 ? (
          <div className="bg-gray-900 border border-gray-800 rounded-lg text-center py-12 px-6">
            <p className="text-gray-400">No items in the batch queue. Add some to get started.</p>
          </div>
        ) : (
          <div className="flex flex-col gap-3 mb-6">
            {items.map((item, index) => {
              const itemStatus = batchStatus?.items?.find((s) => s.index === index);
              return (
                <BatchItemCard
                  key={item.id}
                  item={item}
                  index={index}
                  onUpdate={onUpdateItem}
                  onRemove={onRemoveItem}
                  isRunning={isRunning}
                  itemStatus={itemStatus ? {
                    status: itemStatus.status,
                    current_step: itemStatus.current_step,
                    error: itemStatus.error,
                  } : undefined}
                />
              );
            })}
          </div>
        )}

        {/* Footer actions */}
        <div className="flex gap-3">
          {isRunning ? (
            <button
              onClick={onCancel}
              className="px-6 py-2 bg-red-700 hover:bg-red-600 rounded text-sm font-medium transition-colors"
            >
              Cancel Batch
            </button>
          ) : (
            <button
              onClick={onRunBatch}
              disabled={items.length === 0}
              className="px-6 py-2 bg-green-700 hover:bg-green-600 rounded text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
            >
              <Play size={16} />
              Run Batch ({items.length} item{items.length !== 1 ? 's' : ''})
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
