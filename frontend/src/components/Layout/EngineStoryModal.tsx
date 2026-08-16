/**
 * 🎬 Engine & Story modal (v1.277.12) — one place for the per-project video
 * engine (LTX 2.3 / MiniMax H3 / LTX 2.5-staged), the MiniMax options
 * (turbo/draft, audio mode, refs), and the Story/World link + pull.
 *
 * Self-contained on purpose: AppLayout only mounts it, so the 2000-line
 * layout file gains two lines, not a subsystem.
 */
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

type LinkT = {
  linked: boolean; world_id?: string; world_name?: string;
  story_id?: string | null; story_title?: string | null;
  style_text?: string;
  cast?: { id: string; name: string; char_slug: string; status: string }[];
  texts?: { id: string; kind: string; title: string; story_id: string }[];
};
type WorldRowT = { id: string; name: string; stories: number; cast: number };
type StoryRowT = { id: string; title: string };
type CfgT = {
  video_engine: string; h3_turbo?: boolean | null; h3_draft?: boolean | null;
  h3_audio_mode?: string | null; h3_use_audio_ref?: boolean | null;
  h3_ref_image_size?: string | null; h3_auto_sheet_refs?: boolean | null;
};

async function jj<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let d = ''; try { d = (await r.json()).detail || ''; } catch { /* */ }
    throw new Error(d || `${r.status}`);
  }
  return r.json() as Promise<T>;
}

export default function EngineStoryModal({ projectId, onClose }: {
  projectId: string; onClose: () => void;
}) {
  const navigate = useNavigate();
  const [cfg, setCfg] = useState<CfgT | null>(null);
  const [link, setLink] = useState<LinkT | null>(null);
  const [worlds, setWorlds] = useState<WorldRowT[]>([]);
  const [stories, setStories] = useState<StoryRowT[]>([]);
  const [pickWorld, setPickWorld] = useState('');
  const [pickStory, setPickStory] = useState('');
  const [pullChars, setPullChars] = useState(false);
  const [pullTextId, setPullTextId] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [c, l, w] = await Promise.all([
        fetch(`/api/projects/${projectId}/video-config`, { method: 'PUT',
          headers: { 'Content-Type': 'application/json' }, body: '{}' }).then(r => jj<CfgT>(r)),
        fetch(`/api/projects/${projectId}/story-link`).then(r => jj<LinkT>(r)),
        fetch('/api/storyworld/worlds').then(r => jj<{ worlds: WorldRowT[] }>(r)),
      ]);
      setCfg(c); setLink(l); setWorlds(w.worlds || []);
      if (l.linked && l.world_id) setPickWorld(l.world_id);
      if (l.story_id) setPickStory(l.story_id);
    } catch (e) { setMsg(`⚠ ${e}`); }
  }, [projectId]);
  useEffect(() => { void load(); }, [load]);

  // stories of the picked world, for the story dropdown
  useEffect(() => {
    if (!pickWorld) { setStories([]); return; }
    let stop = false;
    fetch(`/api/storyworld/worlds/${pickWorld}`)
      .then(r => jj<{ stories: StoryRowT[] }>(r))
      .then(w => { if (!stop) setStories(w.stories || []); })
      .catch(() => { if (!stop) setStories([]); });
    return () => { stop = true; };
  }, [pickWorld]);

  const saveCfg = async (patch: Partial<CfgT>) => {
    try {
      const r = await fetch(`/api/projects/${projectId}/video-config`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      }).then(x => jj<CfgT>(x));
      setCfg(r); setMsg('saved');
    } catch (e) { setMsg(`⚠ ${e}`); }
  };
  const saveLink = async (attach: boolean) => {
    setBusy(true);
    try {
      await fetch(`/api/projects/${projectId}/story-link`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(attach
          ? { world_id: pickWorld, story_id: pickStory, attach: true }
          : { attach: false }),
      }).then(x => jj(x));
      setMsg(attach ? 'linked' : 'unlinked');
      await load();
    } catch (e) { setMsg(`⚠ ${e}`); }
    setBusy(false);
  };
  const pull = async () => {
    setBusy(true);
    try {
      const r = await fetch(`/api/projects/${projectId}/pull-from-story`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ concept: true, style: true,
          characters: pullChars, lyrics_text_id: pullTextId }),
      }).then(x => jj<{ pulled: string[] }>(x));
      setMsg(`pulled: ${r.pulled.join(', ') || 'nothing new'} — reload the page to see it`);
    } catch (e) { setMsg(`⚠ ${e}`); }
    setBusy(false);
  };

  const isH3 = cfg?.video_engine === 'minimax_h3';
  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4"
         onClick={onClose}>
      <div className="bg-gray-900 border border-gray-700 rounded-lg w-full max-w-xl max-h-[90vh] overflow-y-auto p-5"
           onClick={e => e.stopPropagation()}>
        <div className="flex items-center gap-2 mb-4">
          <h2 className="text-lg font-bold">🎬 Video Engine & 🌍 Story</h2>
          <span className="text-xs text-gray-500">{msg}</span>
          <button className="ml-auto text-gray-400 hover:text-gray-200" onClick={onClose}>✕</button>
        </div>

        {/* engine */}
        <div className="mb-5">
          <div className="text-sm font-semibold mb-2">Video engine</div>
          <div className="flex gap-2">
            {/* ltx_2.5 hidden from the UI (2026-08-16, his call: staged in the
                backend, not the current focus). A project already set to it keeps
                working; the button returns when 2.5 becomes a focus. */}
            {([['ltx_2.3', 'LTX 2.3'], ['minimax_h3', 'MiniMax H3']] as const).map(([v, l]) => (
              <button key={v}
                className={`px-3 py-1.5 rounded text-sm border ${cfg?.video_engine === v
                  ? 'border-blue-500 bg-blue-900/40 text-blue-200'
                  : 'border-gray-700 text-gray-300 hover:bg-gray-800'}`}
                onClick={() => void saveCfg({ video_engine: v })}>{l}</button>
            ))}
          </div>
          {cfg?.video_engine === 'ltx_2.5' && (
            <div className="text-xs text-amber-400 mt-1">
              This project is set to LTX 2.5 (staged, currently hidden from selection).
              Pick LTX 2.3 or MiniMax H3 above to switch.
            </div>
          )}
          {isH3 && (
            <div className="mt-3 space-y-2 border border-gray-800 rounded p-3">
              <div className="text-xs font-semibold text-gray-300">MiniMax H3 options</div>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={cfg?.h3_turbo !== false}
                       onChange={e => void saveCfg({ h3_turbo: e.target.checked })} />
                ⚡ Turbo lora (8-step v1.0)
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={!!cfg?.h3_draft}
                       onChange={e => void saveCfg({ h3_draft: e.target.checked })} />
                🏃 Draft (4-step — testing, ~2/3 the render time)
              </label>
              <div className="flex items-center gap-2 text-sm">
                <span>Audio:</span>
                <select className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm"
                        value={cfg?.h3_audio_mode || 'project'}
                        onChange={e => void saveCfg({ h3_audio_mode: e.target.value })}>
                  <option value="project">use OUR audio (narration / music muxed in)</option>
                  <option value="model">keep H3&apos;s generated audio</option>
                </select>
              </div>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={!!cfg?.h3_use_audio_ref}
                       onChange={e => void saveCfg({ h3_use_audio_ref: e.target.checked })} />
                🎵 Also feed the scene&apos;s audio slice as an H3 audio REFERENCE
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={cfg?.h3_auto_sheet_refs !== false && cfg?.h3_auto_sheet_refs !== undefined ? !!cfg?.h3_auto_sheet_refs : false}
                       onChange={e => void saveCfg({ h3_auto_sheet_refs: e.target.checked })} />
                🪪 Auto-attach outfit CHARACTER SHEETS of present characters as identity refs
              </label>
              <div className="flex items-center gap-2 text-sm">
                <span>Ref fidelity:</span>
                <select className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm"
                        value={cfg?.h3_ref_image_size || 'match'}
                        onChange={e => void saveCfg({ h3_ref_image_size: e.target.value })}>
                  <option value="match">match (fast)</option>
                  <option value="max">max (best identity, slower)</option>
                </select>
              </div>
            </div>
          )}
        </div>

        {/* story link */}
        <div className="mb-4">
          <div className="text-sm font-semibold mb-2">🌍 Story / World link</div>
          {link?.linked && (
            <div className="flex items-center gap-2 text-sm mb-2">
              <span className="text-green-300">
                linked: {link.world_name}{link.story_title ? ` › ${link.story_title}` : ''}
              </span>
              <button className="text-blue-300 hover:text-blue-200 text-xs"
                      onClick={() => navigate('/worlds')}>open world →</button>
              <button className="text-red-300 text-xs ml-auto" disabled={busy}
                      onClick={() => void saveLink(false)}>unlink</button>
            </div>
          )}
          <div className="flex gap-2 items-center flex-wrap">
            <select className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm"
                    value={pickWorld} onChange={e => { setPickWorld(e.target.value); setPickStory(''); }}>
              <option value="">— pick a world —</option>
              {worlds.map(w => <option key={w.id} value={w.id}>{w.name} ({w.cast} cast)</option>)}
            </select>
            <select className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm"
                    value={pickStory} onChange={e => setPickStory(e.target.value)}
                    disabled={!pickWorld}>
              <option value="">whole world (no story)</option>
              {stories.map(s => <option key={s.id} value={s.id}>{s.title}</option>)}
            </select>
            <button className="px-3 py-1 rounded text-sm bg-blue-600 hover:bg-blue-500 disabled:opacity-40"
                    disabled={!pickWorld || busy} onClick={() => void saveLink(true)}>
              🔗 Link
            </button>
          </div>
        </div>

        {/* pull */}
        {link?.linked && (
          <div className="border border-gray-800 rounded p-3">
            <div className="text-xs font-semibold text-gray-300 mb-2">
              ⬇ Pull from the story into this project
            </div>
            <div className="text-xs text-gray-500 mb-2">
              Concept + visual style always pull. Copies are independent — later edits in the
              world don&apos;t change the project.
            </div>
            <label className="flex items-center gap-2 text-sm mb-1">
              <input type="checkbox" checked={pullChars}
                     onChange={e => setPullChars(e.target.checked)} />
              🎭 Cast → project characters (generated bases imported as images)
            </label>
            <div className="flex items-center gap-2 text-sm mb-2">
              <span>📝 Lyrics/script:</span>
              <select className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm"
                      value={pullTextId} onChange={e => setPullTextId(e.target.value)}>
                <option value="">don&apos;t pull a text</option>
                {(link.texts || []).map(t => (
                  <option key={t.id} value={t.id}>{t.kind}: {t.title}</option>
                ))}
              </select>
            </div>
            <button className="px-3 py-1.5 rounded text-sm bg-amber-700 hover:bg-amber-600 disabled:opacity-40"
                    disabled={busy} onClick={() => void pull()}>
              {busy ? '⏳ pulling…' : '⬇ Pull into project'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
