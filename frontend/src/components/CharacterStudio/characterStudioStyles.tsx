/**
 * Character Studio — shared art-style presets + reusable dropdown.
 *
 * Mirrors the backend STUDIO_STYLES registry (service.py). Values must match
 * the backend keys; unknown/custom values are accepted verbatim (the backend
 * uses them as the style descriptor), so this list is never a hard limit.
 */
import { useState } from 'react';

export const DEFAULT_STYLE = 'anime';

export const STUDIO_STYLE_PRESETS: { value: string; label: string }[] = [
  { value: 'anime', label: 'Anime / Visual Novel' },
  { value: 'semi_realistic', label: 'Semi-realistic' },
  { value: 'photorealistic', label: 'Photorealistic' },
  { value: '3d_render', label: '3D render' },
  { value: 'comic', label: 'Western comic' },
  { value: 'storybook', label: 'Storybook illustration' },
];

export function styleLabel(value: string | undefined | null): string {
  if (!value) return 'Anime / Visual Novel';
  const p = STUDIO_STYLE_PRESETS.find((s) => s.value === value);
  return p ? p.label : value;
}

/** Dropdown of style presets with a Custom… free-text escape hatch. */
export function StyleSelect({
  value,
  onChange,
  label = 'Style',
}: {
  value: string;
  onChange: (v: string) => void;
  label?: string | null;
}) {
  const known = STUDIO_STYLE_PRESETS.some((s) => s.value === value);
  const isCustom = !!value && !known;
  const [custom, setCustom] = useState(isCustom ? value : '');
  const selectVal = isCustom ? '__custom__' : value || DEFAULT_STYLE;

  return (
    <label className="flex flex-col gap-1 text-sm">
      {label && <span className="text-gray-400">{label}</span>}
      <select
        value={selectVal}
        onChange={(e) => {
          const v = e.target.value;
          if (v === '__custom__') onChange(custom || '');
          else onChange(v);
        }}
        className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 focus:outline-none focus:border-purple-600 text-sm"
      >
        {STUDIO_STYLE_PRESETS.map((s) => (
          <option key={s.value} value={s.value}>
            {s.label}
          </option>
        ))}
        <option value="__custom__">Custom…</option>
      </select>
      {selectVal === '__custom__' && (
        <input
          value={custom}
          onChange={(e) => {
            setCustom(e.target.value);
            onChange(e.target.value);
          }}
          placeholder="e.g. claymation, oil painting, pixel art"
          className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 focus:outline-none focus:border-purple-600 text-sm"
        />
      )}
    </label>
  );
}
