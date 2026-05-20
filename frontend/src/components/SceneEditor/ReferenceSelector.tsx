/**
 * ReferenceSelector — character + extra-image reference picker for image generation.
 *
 * Rules:
 *   First Frame  → max 4 total references  (characters + extras)
 *   Last Frame   → max 3 total references  (first-frame image occupies slot 1)
 *   Characters   → max 2 per frame (too many character refs confuses Klein)
 */
import { useState, useCallback } from 'react';
import { Plus, Trash2, Wand2, User, X } from 'lucide-react';
import { uploadAsset, enhancePrompt } from '@/api/client';

// ─── Types ───────────────────────────────────────────────────────────

export interface CharacterInfo {
  name: string;
  description: string;
  image_path: string | null;
}

export interface ExtraRef {
  asset_id: string;
  image_path: string;
  description: string;
}

export interface ReferenceState {
  /** Indices into the project-level characters array. */
  characterIndices: number[];
  /** Additional uploaded reference images. */
  extras: ExtraRef[];
}

interface ReferenceSelectorProps {
  /** All characters defined in the Concept panel. */
  characters: CharacterInfo[];
  /** Current reference state for this frame sub-tab. */
  value: ReferenceState;
  onChange: (next: ReferenceState) => void;
  /** 'first' = max 4, 'last' = max 3. */
  frameType: 'first' | 'last';
  projectId: string;
}

// ─── Component ───────────────────────────────────────────────────────

export default function ReferenceSelector({
  characters,
  value,
  onChange,
  frameType,
  projectId,
}: ReferenceSelectorProps) {
  const maxRefs = frameType === 'first' ? 4 : 3;
  const maxCharRefs = 2; // Klein works best with 1-2 character references
  const totalUsed = value.characterIndices.length + value.extras.length;
  const slotsLeft = maxRefs - totalUsed;
  const charSlotsLeft = maxCharRefs - value.characterIndices.length;

  const [describingIdx, setDescribingIdx] = useState<number | null>(null);

  // ── Character toggle ──────────────────────────────────────────────

  const toggleCharacter = useCallback(
    (idx: number) => {
      const current = value.characterIndices;
      if (current.includes(idx)) {
        onChange({ ...value, characterIndices: current.filter((i) => i !== idx) });
      } else {
        if (slotsLeft <= 0 || charSlotsLeft <= 0) return; // at capacity
        onChange({ ...value, characterIndices: [...current, idx] });
      }
    },
    [value, onChange, slotsLeft, charSlotsLeft]
  );

  const clearCharacters = useCallback(() => {
    onChange({ ...value, characterIndices: [] });
  }, [value, onChange]);

  // ── Extra references ──────────────────────────────────────────────

  const addExtra = useCallback(
    async (file: File) => {
      if (slotsLeft <= 0) return;
      try {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('asset_type', 'reference');
        const response = await uploadAsset(projectId, formData);
        const asset = response.data;
        onChange({
          ...value,
          extras: [
            ...value.extras,
            { asset_id: asset.id, image_path: asset.rel_path, description: '' },
          ],
        });
      } catch (err) {
        console.error('Failed to upload reference image:', err);
      }
    },
    [value, onChange, slotsLeft, projectId]
  );

  const removeExtra = useCallback(
    (idx: number) => {
      onChange({ ...value, extras: value.extras.filter((_, i) => i !== idx) });
    },
    [value, onChange]
  );

  const updateExtraDesc = useCallback(
    (idx: number, desc: string) => {
      const updated = [...value.extras];
      updated[idx] = { ...updated[idx], description: desc };
      onChange({ ...value, extras: updated });
    },
    [value, onChange]
  );

  const clearExtras = useCallback(() => {
    onChange({ ...value, extras: [] });
  }, [value, onChange]);

  // ── Auto-describe via LLM ─────────────────────────────────────────

  const describeExtra = useCallback(
    async (idx: number) => {
      const extra = value.extras[idx];
      if (!extra) return;
      setDescribingIdx(idx);
      try {
        const response = await enhancePrompt(projectId, {
          prompt: `Describe this reference image concisely for an AI image generator. File: ${extra.image_path}`,
          context:
            'You are describing a reference image that will be used in AI image generation. ' +
            'Provide a brief, visual description of the subject — appearance, pose, key features, colors, style. ' +
            'Keep it under 50 words. Output ONLY the description.',
        });
        updateExtraDesc(idx, response.data.enhanced_prompt);
      } catch (err) {
        console.error('Failed to describe image:', err);
      } finally {
        setDescribingIdx(null);
      }
    },
    [value.extras, projectId, updateExtraDesc]
  );

  // ── Render ─────────────────────────────────────────────────────────

  return (
    <div className="space-y-3">
      {/* ── Characters ──────────────────────────────────────────── */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <label className="text-xs font-medium text-gray-400">Characters</label>
          {value.characterIndices.length > 0 && (
            <button
              onClick={clearCharacters}
              className="text-[10px] text-gray-500 hover:text-gray-300 transition-colors"
            >
              Clear
            </button>
          )}
        </div>

        {characters.length === 0 ? (
          <div className="text-[10px] text-gray-600 py-1">
            No characters defined — add them in the Concept panel.
          </div>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {characters.map((char, idx) => {
              const selected = value.characterIndices.includes(idx);
              const disabled = !selected && (slotsLeft <= 0 || charSlotsLeft <= 0);
              return (
                <button
                  key={idx}
                  onClick={() => toggleCharacter(idx)}
                  disabled={disabled}
                  className={`flex items-center gap-1.5 px-2 py-1 rounded text-xs transition-colors border ${
                    selected
                      ? 'bg-blue-600/20 border-blue-500 text-blue-300'
                      : disabled
                        ? 'bg-gray-800/40 border-gray-700/40 text-gray-600 cursor-not-allowed'
                        : 'bg-gray-800 border-gray-700 text-gray-400 hover:text-white hover:border-gray-500'
                  }`}
                  title={char.description || char.name}
                >
                  {char.image_path ? (
                    <img
                      src={`/api/files/${char.image_path}`}
                      alt={char.name}
                      className="w-5 h-5 rounded-full object-cover"
                    />
                  ) : (
                    <User size={12} />
                  )}
                  {char.name || `Char ${idx + 1}`}
                  {selected && <X size={10} className="ml-0.5" />}
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* ── Extra Reference Images ──────────────────────────────── */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <label className="text-xs font-medium text-gray-400">
            Reference Images
          </label>
          <div className="flex items-center gap-2">
            {value.extras.length > 0 && (
              <button
                onClick={clearExtras}
                className="text-[10px] text-gray-500 hover:text-gray-300 transition-colors"
              >
                Clear
              </button>
            )}
            <label
              className={`flex items-center gap-1 px-2 py-0.5 rounded text-[10px] transition-colors border cursor-pointer ${
                slotsLeft <= 0
                  ? 'bg-gray-800/40 border-gray-700/40 text-gray-600 cursor-not-allowed'
                  : 'bg-gray-800 border-gray-700 text-gray-400 hover:text-white hover:border-gray-500'
              }`}
            >
              <Plus size={10} />
              Add
              <input
                type="file"
                accept="image/*"
                multiple
                className="hidden"
                disabled={slotsLeft <= 0}
                onChange={(e) => {
                  const files = e.target.files;
                  if (!files) return;
                  // Upload as many as slots allow
                  const toUpload = Array.from(files).slice(0, slotsLeft);
                  toUpload.forEach((f) => addExtra(f));
                  e.target.value = ''; // reset
                }}
              />
            </label>
          </div>
        </div>

        {value.extras.length === 0 && (
          <div className="text-[10px] text-gray-600 py-1">
            No extra reference images. Upload images to guide generation.
          </div>
        )}

        <div className="space-y-2">
          {value.extras.map((extra, idx) => (
            <div
              key={extra.asset_id}
              className="flex gap-2 p-2 bg-gray-800/50 border border-gray-700/60 rounded"
            >
              <img
                src={`/api/files/${extra.image_path}`}
                alt={`Ref ${idx + 1}`}
                className="w-12 h-12 rounded object-cover border border-gray-600 flex-shrink-0"
              />
              <div className="flex-1 min-w-0 space-y-1">
                <div className="flex items-center gap-1">
                  <span className="text-[10px] text-gray-500 flex-shrink-0">
                    Ref {value.characterIndices.length + idx + 1}
                  </span>
                  <button
                    onClick={() => describeExtra(idx)}
                    disabled={describingIdx === idx}
                    className="p-0.5 text-purple-400 hover:text-purple-300 transition-colors disabled:opacity-50"
                    title="Auto-describe this image"
                  >
                    <Wand2 size={10} />
                  </button>
                  <button
                    onClick={() => removeExtra(idx)}
                    className="p-0.5 text-red-400 hover:text-red-300 transition-colors ml-auto"
                    title="Remove"
                  >
                    <Trash2 size={10} />
                  </button>
                </div>
                <input
                  value={extra.description}
                  onChange={(e) => updateExtraDesc(idx, e.target.value)}
                  placeholder={describingIdx === idx ? 'Describing...' : 'Describe this image...'}
                  className="w-full px-1.5 py-1 bg-gray-900 border border-gray-700 rounded text-[11px] text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500"
                />
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ── Capacity indicator ──────────────────────────────────── */}
      <div className="flex items-center justify-between text-[10px] text-gray-500 pt-1 border-t border-gray-800/50">
        <span>
          {totalUsed} / {maxRefs} refs
          {value.characterIndices.length > 0 && (
            <span className="text-gray-500"> · {value.characterIndices.length}/{maxCharRefs} characters</span>
          )}
          {frameType === 'last' && (
            <span className="text-gray-600"> (1 slot reserved for first frame)</span>
          )}
        </span>
        {totalUsed === 0 && <span className="text-gray-600 italic">None — text-to-image mode</span>}
      </div>
    </div>
  );
}

// ─── Helpers for parent ──────────────────────────────────────────────

/** Given a ReferenceState, return the auto workflow type string. */
export function autoWorkflowType(refs: ReferenceState, _characters?: CharacterInfo[]): string {
  const total = refs.characterIndices.length + refs.extras.length;
  if (total === 0) return 'klein_t2i';
  if (total === 1) return 'klein_1ref';
  if (total === 2) return 'klein_2ref';
  if (total === 3) return 'klein_3ref';
  return 'klein_4ref';
}

/** Collect all asset IDs (character image assets first, then extras). */
export function collectRefAssetIds(
  refs: ReferenceState,
  characters: CharacterInfo[],
  allAssets: Array<{ id: string; rel_path: string; asset_type: string }>,
): string[] {
  const ids: string[] = [];
  // Characters — find the asset whose rel_path matches the character's image_path
  for (const idx of refs.characterIndices) {
    const ch = characters[idx];
    if (ch?.image_path) {
      const asset = allAssets.find(
        (a) => a.rel_path === ch.image_path || a.rel_path?.endsWith(ch.image_path ?? '')
      );
      if (asset) ids.push(asset.id);
    }
  }
  // Extra refs
  for (const extra of refs.extras) {
    ids.push(extra.asset_id);
  }
  return ids;
}

/**
 * Build a text block describing all references for the prompt enhancer.
 * Uses "Image N" syntax which is how Flux Klein 9B references input images.
 */
export function buildRefDescriptions(
  refs: ReferenceState,
  characters: CharacterInfo[],
): string {
  if (refs.characterIndices.length === 0 && refs.extras.length === 0) return '';

  const parts: string[] = [];
  let n = 1;
  for (const idx of refs.characterIndices) {
    const ch = characters[idx];
    parts.push(
      `Image ${n} is character "${ch?.name || 'Unnamed'}" — ${ch?.description || 'no description'}`
    );
    n++;
  }
  for (const extra of refs.extras) {
    parts.push(
      `Image ${n} is ${extra.description || 'no description provided'}`
    );
    n++;
  }
  return parts.join('. ');
}
