/**
 * Character Studio P2 — Pose Editor modal.
 *
 * A 2D drag-the-joints pose editor rendered over an SVG canvas matching the
 * backend's fixed pose-preset canvas (512x1536, OpenPose-style 18 joints).
 * Joint names/bone pairs mirror backend/services/character_studio/pose_renderer.py
 * (DEFAULT_SKELETON / BONE_CONNECTIONS) exactly so saved joints round-trip.
 *
 * Flow:
 *  - "Load from preset" pulls an existing preset's joints via
 *    GET /pose-presets/joints/{id} and seeds the editable skeleton.
 *  - Dragging a joint updates local state only (no network call).
 *  - "Server preview" posts the current joints to POST /pose-presets/preview
 *    (debounced) and swaps in the rendered PNG (via blob -> object URL).
 *  - "Save as custom pose" posts name+joints to POST /pose-presets/custom.
 *  - For a custom preset opened for editing, a delete button removes it via
 *    DELETE /pose-presets/custom/{id}.
 *
 * Opened from PoseStudioTab via an "Edit" button; on save the parent refreshes
 * its preset grid.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { X, Save, Trash2, RefreshCw } from 'lucide-react';
import { p2Api, PoseJointsT, PosePresetT } from './characterStudioP2Api';
import { Spinner, ErrorText } from './p2Shared';

const CANVAS_W = 512;
const CANVAS_H = 1536;

// Mirrors backend/services/character_studio/pose_renderer.py DEFAULT_SKELETON
// key order exactly (18-joint OpenPose-style subset, r_/l_ prefix naming).
const DEFAULT_JOINTS: PoseJointsT = {
  nose: [256, 160],
  neck: [256, 260],
  r_shoulder: [190, 280],
  r_elbow: [160, 420],
  r_wrist: [140, 560],
  l_shoulder: [322, 280],
  l_elbow: [352, 420],
  l_wrist: [372, 560],
  r_hip: [220, 650],
  r_knee: [210, 900],
  r_ankle: [200, 1150],
  l_hip: [292, 650],
  l_knee: [302, 900],
  l_ankle: [312, 1150],
  r_eye: [235, 140],
  l_eye: [277, 140],
  r_ear: [210, 150],
  l_ear: [302, 150],
};

// Mirrors backend/services/character_studio/pose_renderer.py BONE_CONNECTIONS.
const BONE_PAIRS: [string, string][] = [
  ['nose', 'neck'],
  ['neck', 'r_shoulder'],
  ['r_shoulder', 'r_elbow'],
  ['r_elbow', 'r_wrist'],
  ['neck', 'l_shoulder'],
  ['l_shoulder', 'l_elbow'],
  ['l_elbow', 'l_wrist'],
  ['neck', 'r_hip'],
  ['neck', 'l_hip'],
  ['r_hip', 'r_knee'],
  ['r_knee', 'r_ankle'],
  ['l_hip', 'l_knee'],
  ['l_knee', 'l_ankle'],
  ['nose', 'r_eye'],
  ['r_eye', 'r_ear'],
  ['nose', 'l_eye'],
  ['l_eye', 'l_ear'],
];

const JOINT_COLOR: Record<string, string> = {
  nose: '#f87171',
  r_eye: '#f87171',
  l_eye: '#f87171',
  r_ear: '#f87171',
  l_ear: '#f87171',
  neck: '#facc15',
  r_shoulder: '#34d399',
  r_elbow: '#34d399',
  r_wrist: '#34d399',
  l_shoulder: '#60a5fa',
  l_elbow: '#60a5fa',
  l_wrist: '#60a5fa',
  r_hip: '#a78bfa',
  r_knee: '#a78bfa',
  r_ankle: '#a78bfa',
  l_hip: '#f472b6',
  l_knee: '#f472b6',
  l_ankle: '#f472b6',
};

function cloneJoints(joints: PoseJointsT): PoseJointsT {
  const out: PoseJointsT = {};
  for (const [k, v] of Object.entries(joints)) out[k] = [v[0], v[1]];
  return out;
}

export function PoseEditorModal({
  presets,
  initialPresetId,
  onClose,
  onSaved,
}: {
  presets: PosePresetT[];
  initialPresetId?: string | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [joints, setJoints] = useState<PoseJointsT>(() => cloneJoints(DEFAULT_JOINTS));
  const [selectedPresetId, setSelectedPresetId] = useState<string>(initialPresetId || '');
  const [loadingPreset, setLoadingPreset] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [name, setName] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const previewUrlRef = useRef<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const svgRef = useRef<SVGSVGElement | null>(null);
  const dragJoint = useRef<string | null>(null);

  const editingPreset = presets.find((p) => p.id === selectedPresetId) || null;
  const editingIsCustom = !!editingPreset?.custom;

  const loadPreset = async (presetId: string) => {
    if (!presetId) return;
    setLoadingPreset(true);
    setLoadError(null);
    try {
      const res = await p2Api.getPosePresetJoints(presetId);
      if (res?.joints) setJoints(cloneJoints(res.joints));
      if (res?.name) setName(res.name);
    } catch (e: any) {
      setLoadError(e.message);
    } finally {
      setLoadingPreset(false);
    }
  };

  useEffect(() => {
    if (initialPresetId) loadPreset(initialPresetId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialPresetId]);

  // Debounced server preview whenever joints change.
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      runPreview();
    }, 500);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [joints]);

  useEffect(() => {
    return () => {
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    };
  }, []);

  const runPreview = async () => {
    setPreviewLoading(true);
    setPreviewError(null);
    try {
      const blob = await p2Api.previewPosePreset(joints);
      const url = URL.createObjectURL(blob);
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
      previewUrlRef.current = url;
      setPreviewUrl(url);
    } catch (e: any) {
      setPreviewError(e.message);
    } finally {
      setPreviewLoading(false);
    }
  };

  const toSvgPoint = (clientX: number, clientY: number): [number, number] => {
    const svg = svgRef.current;
    if (!svg) return [0, 0];
    const rect = svg.getBoundingClientRect();
    const x = ((clientX - rect.left) / rect.width) * CANVAS_W;
    const y = ((clientY - rect.top) / rect.height) * CANVAS_H;
    return [Math.max(0, Math.min(CANVAS_W, x)), Math.max(0, Math.min(CANVAS_H, y))];
  };

  const onPointerDownJoint = (jointName: string) => (e: React.PointerEvent) => {
    e.stopPropagation();
    (e.target as Element).setPointerCapture?.(e.pointerId);
    dragJoint.current = jointName;
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const j = dragJoint.current;
    if (!j) return;
    const [x, y] = toSvgPoint(e.clientX, e.clientY);
    setJoints((prev) => ({ ...prev, [j]: [x, y] }));
  };

  const onPointerUp = () => {
    dragJoint.current = null;
  };

  const resetToDefault = () => {
    setJoints(cloneJoints(DEFAULT_JOINTS));
  };

  const save = async () => {
    if (!name.trim()) {
      setSaveError('Name is required.');
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      await p2Api.createCustomPosePreset({ name: name.trim(), joints });
      onSaved();
    } catch (e: any) {
      setSaveError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const doDelete = async () => {
    if (!editingPreset || !editingIsCustom) return;
    if (!window.confirm(`Delete custom pose "${editingPreset.name}"?`)) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await p2Api.deleteCustomPosePreset(editingPreset.id);
      onSaved();
      onClose();
    } catch (e: any) {
      setDeleteError(e.message);
    } finally {
      setDeleting(false);
    }
  };

  const bonesToRender = useMemo(
    () => BONE_PAIRS.filter(([a, b]) => joints[a] && joints[b]),
    [joints]
  );

  return (
    <div className="fixed inset-0 bg-black/70 z-[9995] flex items-center justify-center p-4" onClick={onClose}>
      <div
        className="bg-gray-900 border border-gray-800 rounded-lg p-6 w-full max-w-4xl flex flex-col gap-4 max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Pose Editor</h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300">
            <X size={18} />
          </button>
        </div>

        <div className="flex flex-col md:flex-row gap-5">
          {/* Left: draggable skeleton canvas */}
          <div className="flex flex-col gap-2">
            <div
              className="bg-gray-950 border border-gray-800 rounded-md overflow-hidden"
              style={{ width: 256, height: 768, touchAction: 'none' }}
            >
              <svg
                ref={svgRef}
                viewBox={`0 0 ${CANVAS_W} ${CANVAS_H}`}
                width="100%"
                height="100%"
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
                onPointerLeave={onPointerUp}
              >
                <rect x={0} y={0} width={CANVAS_W} height={CANVAS_H} fill="#0b0f19" />
                {bonesToRender.map(([a, b]) => (
                  <line
                    key={`${a}-${b}`}
                    x1={joints[a][0]}
                    y1={joints[a][1]}
                    x2={joints[b][0]}
                    y2={joints[b][1]}
                    stroke="#6b7280"
                    strokeWidth={4}
                    strokeLinecap="round"
                  />
                ))}
                {Object.entries(joints).map(([jointName, [x, y]]) => (
                  <circle
                    key={jointName}
                    cx={x}
                    cy={y}
                    r={10}
                    fill={JOINT_COLOR[jointName] || '#e5e7eb'}
                    stroke="#111827"
                    strokeWidth={2}
                    onPointerDown={onPointerDownJoint(jointName)}
                    style={{ cursor: 'grab' }}
                  >
                    <title>{jointName}</title>
                  </circle>
                ))}
              </svg>
            </div>
            <button
              onClick={resetToDefault}
              className="self-start flex items-center gap-1.5 text-xs text-gray-500 hover:text-gray-300"
            >
              <RefreshCw size={12} /> Reset to default pose
            </button>
          </div>

          {/* Right: controls */}
          <div className="flex-1 flex flex-col gap-4 min-w-[240px]">
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-gray-400">Load from preset</span>
              <select
                value={selectedPresetId}
                onChange={(e) => {
                  const id = e.target.value;
                  setSelectedPresetId(id);
                  if (id) loadPreset(id);
                }}
                className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 focus:outline-none focus:border-purple-600 text-sm"
              >
                <option value="">(start blank / current)</option>
                {presets.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                    {p.custom ? ' (custom)' : ''}
                  </option>
                ))}
              </select>
            </label>
            {loadingPreset && (
              <div className="flex items-center gap-2 text-xs text-gray-500">
                <Spinner size={12} /> Loading preset joints...
              </div>
            )}
            <ErrorText msg={loadError} />

            <div className="flex flex-col gap-2">
              <button
                onClick={runPreview}
                disabled={previewLoading}
                className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 disabled:opacity-50 rounded-md text-sm font-medium flex items-center justify-center gap-2 border border-gray-700"
              >
                {previewLoading && <Spinner size={13} />}
                Server preview
              </button>
              <ErrorText msg={previewError} />
              <div className="aspect-[1/3] max-h-64 bg-gray-800 border border-gray-700 rounded-md overflow-hidden flex items-center justify-center self-start w-32">
                {previewUrl ? (
                  <img src={previewUrl} alt="Server pose preview" className="w-full h-full object-contain" />
                ) : (
                  <span className="text-xs text-gray-600 px-2 text-center">No preview yet</span>
                )}
              </div>
            </div>

            <div className="flex flex-col gap-2 border-t border-gray-800 pt-3">
              <label className="flex flex-col gap-1 text-sm">
                <span className="text-gray-400">Pose name</span>
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Leaning against wall"
                  className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 focus:outline-none focus:border-purple-600 text-sm"
                />
              </label>
              <ErrorText msg={saveError} />
              <button
                onClick={save}
                disabled={saving || !name.trim()}
                className="px-3 py-1.5 bg-purple-700 hover:bg-purple-600 disabled:opacity-50 rounded-md text-sm font-medium flex items-center justify-center gap-2"
              >
                {saving && <Spinner size={13} />}
                <Save size={14} />
                Save as custom pose
              </button>

              {editingIsCustom && (
                <>
                  <ErrorText msg={deleteError} />
                  <button
                    onClick={doDelete}
                    disabled={deleting}
                    className="px-3 py-1.5 bg-red-900/40 hover:bg-red-900/60 border border-red-900/60 disabled:opacity-50 rounded-md text-sm font-medium flex items-center justify-center gap-2 text-red-300"
                  >
                    {deleting && <Spinner size={13} />}
                    <Trash2 size={14} />
                    Delete this custom pose
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
