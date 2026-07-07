/**
 * Character Studio P2 — COSTUMES tab.
 *
 * Costume list from character.manifest.costumes (via status), a create/edit
 * form (name/top/bottom/head/face/shoes/prompt), and per-costume "Generate"
 * dispatching a single job against the base identity.
 *
 * NOTE: the P2 API doc (docs/CHARACTER_STUDIO_P2_API.md, section 7 Wizards)
 * only documents /wizards/character and /wizards/clone — there is no
 * /wizards/costume endpoint in the contract. Per instructions, no costume
 * wizard button is built here (omitted rather than inventing an endpoint).
 */
import { useEffect, useState } from 'react';
import { Plus, Pencil, Trash2, X, Shirt } from 'lucide-react';
import { CostumeFieldsT, CostumeT, EngineT, OutfitCatalogEntryT, p2Api } from './characterStudioP2Api';
import { Spinner, ErrorText, StatusChip, assetUrl, ImageLightbox } from './p2Shared';

const COSTUME_FIELD_KEYS: { key: keyof CostumeFieldsT; label: string }[] = [
  { key: 'top', label: 'Top' },
  { key: 'bottom', label: 'Bottom' },
  { key: 'head', label: 'Head' },
  { key: 'face', label: 'Face' },
  { key: 'shoes', label: 'Shoes' },
];

function CostumeForm({
  characterId,
  existing,
  onDone,
  onCancel,
}: {
  characterId: string;
  existing: CostumeT | null;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(existing?.name || '');
  const [fields, setFields] = useState<CostumeFieldsT>(existing?.fields || {});
  const [prompt, setPrompt] = useState(existing?.prompt || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [outfits, setOutfits] = useState<OutfitCatalogEntryT[]>([]);
  const [outfitQuery, setOutfitQuery] = useState('');

  useEffect(() => {
    p2Api
      .getCatalogsRaw()
      .then((res) => setOutfits(res?.outfits || []))
      .catch(() => setOutfits([]));
  }, []);

  const applyOutfit = (outfitName: string) => {
    const found = outfits.find((o) => o.name === outfitName);
    if (!found) return;
    setOutfitQuery(found.name);
    setPrompt((prev) => (prev.trim() ? `${prev.trim()}, ${found.content}` : found.content));
  };

  const submit = async () => {
    if (!name.trim()) {
      setError('Name is required.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (existing) {
        await p2Api.updateCostume(characterId, existing.id, { name: name.trim(), fields, prompt: prompt.trim() });
      } else {
        await p2Api.createCostume(characterId, { name: name.trim(), fields, prompt: prompt.trim() || undefined });
      }
      onDone();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-5 flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">
          {existing ? 'Edit Costume' : 'New Costume'}
        </h3>
        <button onClick={onCancel} className="text-gray-500 hover:text-gray-300">
          <X size={16} />
        </button>
      </div>

      <label className="flex flex-col gap-1 text-sm">
        <span className="text-gray-400">Name</span>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Winter coat"
          className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 focus:outline-none focus:border-purple-600"
        />
      </label>

      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        {COSTUME_FIELD_KEYS.map((f) => (
          <label key={f.key} className="flex flex-col gap-1 text-sm">
            <span className="text-gray-400">{f.label}</span>
            <input
              value={fields[f.key] || ''}
              onChange={(e) => setFields((prev) => ({ ...prev, [f.key]: e.target.value }))}
              className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 focus:outline-none focus:border-purple-600"
            />
          </label>
        ))}
      </div>

      <label className="flex flex-col gap-1 text-sm">
        <span className="text-gray-400">Outfit aesthetic (search {outfits.length ? `${outfits.length} ` : ''}presets)</span>
        <input
          value={outfitQuery}
          onChange={(e) => {
            setOutfitQuery(e.target.value);
            applyOutfit(e.target.value);
          }}
          list="costume-outfit-catalog"
          placeholder="e.g. cyberpunk streetwear"
          className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 focus:outline-none focus:border-purple-600"
        />
        <datalist id="costume-outfit-catalog">
          {outfits.map((o) => (
            <option key={o.name} value={o.name} />
          ))}
        </datalist>
        <span className="text-xs text-gray-500">Picking a preset appends its tags to the prompt below (fields stay editable).</span>
      </label>

      <label className="flex flex-col gap-1 text-sm">
        <span className="text-gray-400">Prompt (free-text addendum)</span>
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={2}
          className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 focus:outline-none focus:border-purple-600 resize-y"
        />
      </label>

      <ErrorText msg={error} />

      <div className="flex justify-end gap-2">
        <button onClick={onCancel} className="px-4 py-2 text-sm text-gray-400 hover:text-gray-200">
          Cancel
        </button>
        <button
          onClick={submit}
          disabled={busy}
          className="px-4 py-2 bg-purple-700 hover:bg-purple-600 disabled:opacity-50 rounded-md text-sm font-medium flex items-center gap-2"
        >
          {busy && <Spinner size={14} />}
          {existing ? 'Save' : 'Create'}
        </button>
      </div>
    </div>
  );
}

function CostumeCard({
  characterId,
  studioProjectId,
  costume,
  engine,
  hasBase,
  onChanged,
  onEdit,
}: {
  characterId: string;
  studioProjectId: string | null | undefined;
  costume: CostumeT;
  engine: EngineT;
  hasBase: boolean;
  onChanged: () => void;
  onEdit: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lightbox, setLightbox] = useState(false);

  const sprite = costume.sprites?.base;
  const url = sprite?.asset_id ? assetUrl(studioProjectId, sprite.asset_id) : null;

  const generate = async () => {
    setBusy(true);
    setError(null);
    try {
      await p2Api.generateCostume(characterId, costume.id, { engine });
      onChanged();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const doDelete = async () => {
    if (!window.confirm(`Delete costume "${costume.name}"?`)) return;
    setBusy(true);
    setError(null);
    try {
      await p2Api.deleteCostume(characterId, costume.id);
      onChanged();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-3 flex flex-col gap-2">
      <div className="aspect-square bg-gray-800 border border-gray-700 rounded overflow-hidden flex items-center justify-center">
        {url ? (
          <img
            src={url}
            alt={costume.name}
            onClick={() => setLightbox(true)}
            title="Click to enlarge"
            className="w-full h-full object-cover cursor-pointer"
          />
        ) : (
          <Shirt size={28} className="text-gray-600" />
        )}
      </div>
      {lightbox && url && <ImageLightbox url={url} onClose={() => setLightbox(false)} />}
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm text-gray-200 font-medium truncate" title={costume.name}>
          {costume.name}
        </span>
        {sprite && (
          <span title={sprite.error || ''}>
            <StatusChip status={sprite.status} />
          </span>
        )}
      </div>
      <ErrorText msg={error} />
      <div className="flex items-center gap-2">
        <button
          onClick={generate}
          disabled={busy || !hasBase}
          className="flex-1 px-2 py-1.5 bg-indigo-700 hover:bg-indigo-600 disabled:opacity-50 rounded-md text-xs font-medium flex items-center justify-center gap-1.5"
        >
          {busy && <Spinner size={12} />}
          Generate
        </button>
        <button onClick={onEdit} className="p-1.5 text-gray-500 hover:text-gray-200" title="Edit">
          <Pencil size={13} />
        </button>
        <button onClick={doDelete} className="p-1.5 text-gray-500 hover:text-red-400" title="Delete">
          <Trash2 size={13} />
        </button>
      </div>
    </div>
  );
}

export function CostumesTab({
  characterId,
  studioProjectId,
  costumes,
  engine,
  hasBase,
  onChanged,
}: {
  characterId: string;
  studioProjectId: string | null | undefined;
  costumes: Record<string, CostumeT> | undefined;
  engine: EngineT;
  hasBase: boolean;
  onChanged: () => void;
}) {
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<CostumeT | null>(null);

  const list = Object.values(costumes || {});

  return (
    <div className="flex flex-col gap-4">
      {!hasBase && (
        <div className="text-sm text-amber-300 bg-amber-950/20 border border-amber-900/40 rounded-md px-3 py-2">
          Generate a base render first (Sheet tab) — costume generation requires it.
        </div>
      )}

      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">Costumes</h3>
        {!showForm && (
          <button
            onClick={() => {
              setEditing(null);
              setShowForm(true);
            }}
            className="px-3 py-1.5 bg-purple-700 hover:bg-purple-600 rounded-md text-sm font-medium flex items-center gap-2"
          >
            <Plus size={14} />
            New Costume
          </button>
        )}
      </div>

      {showForm && (
        <CostumeForm
          characterId={characterId}
          existing={editing}
          onCancel={() => {
            setShowForm(false);
            setEditing(null);
          }}
          onDone={() => {
            setShowForm(false);
            setEditing(null);
            onChanged();
          }}
        />
      )}

      {!list.length ? (
        <div className="text-sm text-gray-500 bg-gray-900 border border-gray-800 rounded-lg p-6 text-center">
          No costumes yet. Create one to render an alternate outfit for this character.
        </div>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3">
          {list.map((c) => (
            <CostumeCard
              key={c.id}
              characterId={characterId}
              studioProjectId={studioProjectId}
              costume={c}
              engine={engine}
              hasBase={hasBase}
              onChanged={onChanged}
              onEdit={() => {
                setEditing(c);
                setShowForm(true);
              }}
            />
          ))}
        </div>
      )}
    </div>
  );
}
