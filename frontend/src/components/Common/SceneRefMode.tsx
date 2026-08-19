/**
 * 🎛 SCENE REF MODE — the two routes auto-gen can take (v1.277.37).
 *
 *   t2i_swap        render the frame with nobody in it, then composite the
 *                   characters in (two-pass). Best COMPOSITION.
 *   full_reference  hand the model the character sheets; on MiniMax H3 the
 *                   video runs ref2v. Best IDENTITY, and the only route that
 *                   holds a face through the motion.
 *
 * One control, three placements: the project default (Concept tab + the engine
 * modal) and the per-scene override (Scene editor), which adds "use the project
 * default". Backend truth is backend/services/scene_ref_mode.py — keep the
 * value strings identical to MODES there.
 */
import { useCallback, useEffect, useState } from 'react';

export type SceneRefModeT = 't2i_swap' | 'full_reference';

export const SCENE_REF_MODES: {
  value: SceneRefModeT; label: string; hint: string;
}[] = [
  {
    value: 'full_reference',
    label: '🪪 Full reference mode',
    hint: 'Character sheets go to the model as references — one pass. Best identity, and on MiniMax H3 the video routes to ref2v so the face holds through the motion.',
  },
  {
    value: 't2i_swap',
    label: '🖼 T2I → swap refs in',
    hint: 'Pass 1 stages the shot with nobody in it, Pass 2 composites the characters in. Best composition — the model is not fighting four reference images while blocking the frame.',
  },
];

/** The per-scene picker. `value` is undefined/'' when the scene inherits. */
export function SceneRefModePicker({
  value, projectDefault, onChange, disabled,
}: {
  value?: string | null;
  projectDefault?: SceneRefModeT | string | null;
  onChange: (v: string) => void;
  disabled?: boolean;
}) {
  const inherited = SCENE_REF_MODES.find(m => m.value === projectDefault);
  const current = SCENE_REF_MODES.find(m => m.value === value);
  return (
    <div>
      <select
        className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
        value={value || 'inherit'}
        disabled={disabled}
        onChange={e => onChange(e.target.value)}
      >
        <option value="inherit">
          ↩ Use the project default{inherited ? ` (${inherited.label})` : ''}
        </option>
        {SCENE_REF_MODES.map(m => (
          <option key={m.value} value={m.value}>{m.label}</option>
        ))}
      </select>
      <div className="text-[11px] text-gray-500 mt-1 leading-snug">
        {(current || inherited)?.hint || 'How this scene carries character identity.'}
      </div>
    </div>
  );
}

/**
 * The project-wide default. Reads and writes `/video-config` — the same object
 * the engine picker uses, because the choice only makes sense next to the
 * engine (H3 is the only one with a native reference mode).
 */
export function SceneRefModeGlobal({ projectId, compact }: {
  projectId: string; compact?: boolean;
}) {
  const [mode, setMode] = useState<SceneRefModeT | ''>('');
  const [msg, setMsg] = useState('');

  const load = useCallback(async () => {
    try {
      // ⚠ a PUT with an empty body is the READ here (the route treats an
      // all-None payload as a read so it never bumps updated_at)
      const r = await fetch(`/api/projects/${projectId}/video-config`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: '{}',
      });
      if (!r.ok) return;
      const j = await r.json();
      setMode((j.scene_ref_mode as SceneRefModeT) || 'full_reference');
    } catch { /* the badge is not worth an error toast */ }
  }, [projectId]);
  useEffect(() => { void load(); }, [load]);

  const save = async (v: SceneRefModeT) => {
    setMode(v); setMsg('saving…');
    try {
      const r = await fetch(`/api/projects/${projectId}/video-config`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scene_ref_mode: v }),
      });
      setMsg(r.ok ? 'saved' : '⚠ not saved');
    } catch { setMsg('⚠ not saved'); }
  };

  return (
    <div className={compact ? '' : 'border border-gray-800 rounded p-2.5'}>
      <div className="text-xs font-semibold text-gray-300 mb-1.5">
        🎛 Scene reference mode <span className="text-gray-600 font-normal">· project default</span>
        {msg && <span className="ml-2 text-[11px] text-gray-500 font-normal">{msg}</span>}
      </div>
      <div className="space-y-1.5">
        {SCENE_REF_MODES.map(m => (
          <label key={m.value}
                 className={`block rounded border px-2.5 py-2 cursor-pointer transition-colors ${
                   mode === m.value
                     ? 'border-blue-500 bg-blue-900/30'
                     : 'border-gray-700 hover:bg-gray-800/60'}`}>
            <div className="flex items-center gap-2 text-xs text-gray-100">
              <input type="radio" checked={mode === m.value}
                     onChange={() => void save(m.value)} />
              {m.label}
            </div>
            <div className="text-[11px] text-gray-500 mt-1 ml-5 leading-snug">{m.hint}</div>
          </label>
        ))}
      </div>
      <div className="text-[11px] text-gray-600 mt-1.5">
        Any scene can override this in the Scene editor.
      </div>
    </div>
  );
}
