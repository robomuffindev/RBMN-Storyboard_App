import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Plus, UserPlus, ImageOff, Loader2, Check, Trash2, Sparkles,
} from 'lucide-react';
import {
  saveConcept, generateCharacterImage, getCharacterVersions,
  setCharacterActiveImage, deleteCharacterVersion,
} from '@/api/client';
import { handleImgError } from '@/utils/brokenImage';
import MobileShell from './MobileShell';
import MobileSheet from './MobileSheet';
import { useProjectData, fileUrl } from './useProjectData';

interface Char { name: string; description: string; image_path: string | null; last_prompt?: string; reference_images?: any[]; extra_images?: string[]; }

export default function MobileCharacters() {
  const { id } = useParams<{ id: string }>();
  const projectId = id!;
  const qc = useQueryClient();
  const { project, concept, defaultWidth, defaultHeight } = useProjectData(projectId);
  const characters: Char[] = concept?.characters || [];

  const [editIdx, setEditIdx] = useState<number | null>(null); // -1 = new
  const [addOpen, setAddOpen] = useState(false);

  const persist = async (chars: Char[]) => {
    if (!concept) return;
    // saveConcept is a FULL replace on the backend — spread the entire loaded
    // concept so we don't reset the ~19 other project settings (json/scene-intent/
    // video-json modes, ken burns, color, model-audio, global context…), and
    // spread each character to preserve provenance keys (library_origin_id,
    // source, studio_character_id) that must survive round-trips.
    await saveConcept(projectId, {
      ...concept,
      concept_text: concept.concept_text || '',
      style_text: concept.style_text || '',
      characters: chars.map((c) => ({ ...c, image_path: c.image_path ?? null })),
    });
    qc.invalidateQueries({ queryKey: ['concept', projectId] });
  };

  return (
    <MobileShell
      projectId={projectId}
      title={project?.name || 'Project'}
      subtitle={`${characters.length} characters`}
      active="characters"
      right={
        <button onClick={() => setAddOpen(true)} className="p-2 rounded-lg active:bg-gray-800 text-indigo-400" aria-label="Add character">
          <Plus className="w-6 h-6" />
        </button>
      }
    >
      <div className="p-3 grid grid-cols-2 gap-3">
        {characters.length === 0 && (
          <div className="col-span-2 text-center text-gray-500 py-16">
            <UserPlus className="w-8 h-8 mx-auto mb-2 opacity-50" />
            No characters yet. Tap + to add one.
          </div>
        )}
        {characters.map((c, i) => (
          <button key={i} onClick={() => setEditIdx(i)} className="rounded-xl bg-gray-900 border border-gray-800 overflow-hidden active:border-indigo-500 text-left">
            <div className="aspect-square bg-gray-950 flex items-center justify-center">
              {c.image_path ? (
                <img src={fileUrl(c.image_path)} onError={handleImgError} className="w-full h-full object-cover" alt={c.name} />
              ) : (
                <ImageOff className="w-7 h-7 text-gray-700" />
              )}
            </div>
            <div className="p-2">
              <div className="font-semibold text-sm truncate">{c.name || 'Unnamed'}</div>
              <div className="text-[11px] text-gray-500 line-clamp-1">{c.description}</div>
            </div>
          </button>
        ))}
      </div>

      {/* Add new */}
      <AddCharacterSheet
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onAdd={async (name, description) => {
          await persist([...characters, { name, description, image_path: null }]);
          setAddOpen(false);
        }}
      />

      {/* Edit / generate */}
      {editIdx !== null && characters[editIdx] && (
        <CharacterDetailSheet
          projectId={projectId}
          index={editIdx}
          character={characters[editIdx]}
          allCharacters={characters}
          defaultWidth={defaultWidth}
          defaultHeight={defaultHeight}
          onClose={() => setEditIdx(null)}
          persist={persist}
        />
      )}
    </MobileShell>
  );
}

function AddCharacterSheet({ open, onClose, onAdd }: { open: boolean; onClose: () => void; onAdd: (n: string, d: string) => Promise<void> }) {
  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (open) { setName(''); setDesc(''); } }, [open]);
  return (
    <MobileSheet open={open} title="New character" onClose={onClose}>
      <div className="space-y-3">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name"
          className="w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2.5 text-base" />
        <textarea value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="Description (appearance, clothing, vibe…)" rows={4}
          className="w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2.5 text-base resize-none" />
        <button disabled={busy || !name.trim()} onClick={async () => { setBusy(true); try { await onAdd(name.trim(), desc.trim()); } finally { setBusy(false); } }}
          className="w-full py-3 rounded-lg bg-indigo-600 active:bg-indigo-700 font-semibold disabled:opacity-50 flex items-center justify-center gap-2">
          {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <UserPlus className="w-4 h-4" />} Add character
        </button>
      </div>
    </MobileSheet>
  );
}

function CharacterDetailSheet({ projectId, index, character, allCharacters, defaultWidth, defaultHeight, onClose, persist }: {
  projectId: string; index: number; character: Char; allCharacters: Char[];
  defaultWidth: number; defaultHeight: number; onClose: () => void; persist: (c: Char[]) => Promise<void>;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState(character.name);
  const [desc, setDesc] = useState(character.description);
  const [prompt, setPrompt] = useState(character.last_prompt || character.description || '');
  const [generating, setGenerating] = useState(false);
  const [baseline, setBaseline] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const versionsQ = useQuery({
    queryKey: ['character-versions', projectId, index],
    queryFn: async () => (await getCharacterVersions(projectId, index)).data,
    refetchInterval: generating ? 2500 : false,
  });
  const versions = (versionsQ.data || []).filter((v: any) => v.output_path);

  useEffect(() => {
    if (generating && baseline !== null && versions.length > baseline) {
      setGenerating(false); setBaseline(null);
      qc.invalidateQueries({ queryKey: ['concept', projectId] });
    }
  }, [versions.length, generating, baseline, qc, projectId]);

  // Safety net: clear the in-flight state if no new version lands (e.g. the
  // render job failed) so the button doesn't stay disabled forever.
  useEffect(() => {
    if (!generating) return;
    const t = setTimeout(() => { setGenerating(false); setBaseline(null); }, 120000);
    return () => clearTimeout(t);
  }, [generating]);

  const doGenerate = async () => {
    setErr(null); setGenerating(true); setBaseline(versions.length);
    try {
      // Save the prompt back onto the character first.
      const next = allCharacters.slice();
      next[index] = { ...character, name, description: desc, last_prompt: prompt };
      await persist(next);
      await generateCharacterImage(projectId, {
        character_index: index,
        prompt_override: prompt,
        width: defaultWidth,
        height: defaultHeight,
        workflow_type: 'klein_t2i',
        reference_asset_ids: [],
      });
    } catch (e: any) {
      setErr(e?.response?.data?.detail || e?.message || 'Generation failed');
      setGenerating(false); setBaseline(null);
    }
  };

  const setActive = async (outputPath: string) => {
    await setCharacterActiveImage(projectId, index, outputPath);
    qc.invalidateQueries({ queryKey: ['concept', projectId] });
  };
  const delVersion = async (versionId: string) => {
    await deleteCharacterVersion(projectId, index, versionId);
    qc.invalidateQueries({ queryKey: ['character-versions', projectId, index] });
  };
  const saveMeta = async () => {
    const next = allCharacters.slice();
    next[index] = { ...character, name, description: desc, last_prompt: prompt };
    await persist(next);
    onClose();
  };
  const deleteChar = async () => {
    const next = allCharacters.filter((_, i) => i !== index);
    await persist(next);
    onClose();
  };

  return (
    <MobileSheet open title={name || 'Character'} onClose={onClose}>
      <div className="space-y-3">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name"
          className="w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2.5 text-base" />
        <textarea value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="Description" rows={2}
          className="w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2.5 text-base resize-none" />

        <div>
          <label className="text-xs text-gray-400 mb-1 block">Image prompt</label>
          <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={3}
            className="w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2.5 text-sm resize-none" />
        </div>
        {err && <div className="text-xs text-red-400">{err}</div>}
        <button onClick={doGenerate} disabled={generating}
          className="w-full py-3 rounded-lg bg-indigo-600 active:bg-indigo-700 font-semibold disabled:opacity-60 flex items-center justify-center gap-2">
          {generating ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
          {generating ? 'Rendering…' : 'Generate image'}
        </button>

        {versions.length > 0 && (
          <div>
            <div className="text-xs text-gray-400 mb-1.5">{versions.length} version{versions.length !== 1 ? 's' : ''}</div>
            <div className="flex gap-2 overflow-x-auto pb-1">
              {versions.map((v: any) => {
                const active = v.output_path === character.image_path;
                return (
                  <div key={v.id} className="relative flex-shrink-0">
                    <button onClick={() => setActive(v.output_path)}
                      className={`relative w-20 h-20 rounded-lg overflow-hidden border-2 ${active ? 'border-emerald-500' : 'border-gray-700'}`}>
                      <img src={fileUrl(v.output_path)} onError={handleImgError} className="w-full h-full object-cover" alt="" />
                      {active && <span className="absolute bottom-0 inset-x-0 bg-emerald-600/90 text-white text-[9px] py-0.5 flex items-center justify-center gap-0.5"><Check className="w-2.5 h-2.5" />Active</span>}
                    </button>
                    <button onClick={() => delVersion(v.id)} className="absolute -top-1.5 -right-1.5 p-0.5 rounded-full bg-red-600 text-white">
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        <div className="flex gap-2 pt-1">
          <button onClick={saveMeta} className="flex-1 py-2.5 rounded-lg bg-gray-700 active:bg-gray-600 text-sm font-medium">Save</button>
          <button onClick={deleteChar} className="px-4 py-2.5 rounded-lg bg-red-900/60 active:bg-red-900 text-red-300 text-sm font-medium flex items-center gap-1.5">
            <Trash2 className="w-4 h-4" /> Delete
          </button>
        </div>
      </div>
    </MobileSheet>
  );
}
