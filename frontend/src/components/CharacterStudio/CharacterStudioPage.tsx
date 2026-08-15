/**
 * CharacterStudio — reusable character/item library with base-render generation,
 * multi-shot reference sheets, and LoRA training dataset export.
 *
 * Self-contained page: local fetch helpers only (does NOT touch src/api/client.ts).
 * API base prefix: /api/character-studio (same-origin).
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ChevronLeft,
  Plus,
  Loader2,
  X,
  Pencil,
  Trash2,
  Check,
  AlertTriangle,
  RefreshCw,
  Download,
  Upload,
  Send,
  Sparkles,
} from 'lucide-react';
import { CharacterStatusP2T, EngineT, WizardCharacterInfoT } from './characterStudioP2Api';
import { EngineSelector, PreflightBadges } from './EnginePreflight';
import { PoseStudioTab } from './PoseStudioTab';
import { CostumesTab } from './CostumesTab';
import { EmotionsTab } from './EmotionsTab';
import { ProcessPanel } from './ProcessPanel';
import { GenerateAllModal } from './GenerateAllModal';
import { CharacterWizardButton } from './CharacterWizardButton';
import { CloneFromImageButton } from './CloneFromImageButton';
import { StyleSelect, DEFAULT_STYLE } from './characterStudioStyles';
import { ImageLightbox } from './p2Shared';
import { BaseEditorModal } from './BaseEditorModal';
import { CustomBaseModal } from './CustomBaseModal';

import UnifiedCharacterGrid, { FOCUS_KEY } from './UnifiedCharacterGrid';
import AutogenModal from './AutogenModal';
import AutogenBoard from './AutogenBoard';
// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface StoryT {
  id: string;
  name: string;
  description?: string | null;
  default_style?: string;
  character_count?: number;
}

interface CharacterInfoT {
  sex?: string;
  age?: string;
  race?: string;
  skin_color?: string;
  hair?: string;
  eyes?: string;
  face?: string;
  body?: string;
  additional_details?: string;
  outfit?: string;
  background_color?: string;
  nsfw?: boolean;
  style?: string;
  [key: string]: string | boolean | undefined;
}

interface CharacterT {
  id: string;
  name: string;
  kind: 'character' | 'item';
  story_id?: string | null;
  trigger_word?: string | null;
  class_word?: string | null;
  description?: string | null;
  character_info?: CharacterInfoT | null;
  manifest?: Record<string, any> | null;
  scene_id?: string | null;
  studio_project_id?: string | null;
  created_at?: string | null;
}

interface CatalogTagT {
  tag: string;
  label: string;
}

interface CatalogsT {
  tags?: Record<string, CatalogTagT[]>;
  emotions?: string[];
}

interface ShotPlanItemT {
  id: string;
  label: string;
  instruction: string;
  enabled: boolean;
}

interface ShotStatusT {
  status: 'pending' | 'running' | 'done' | 'failed';
  image_rel?: string;
  asset_id?: string;
  error?: string;
  job_id?: string;
}

interface CharacterStatusT extends CharacterStatusP2T {
  base: {
    image_rel?: string;
    asset_id?: string;
    status?: string | null;
    error?: string | null;
    versions?: { asset_id: string; image_rel?: string; source?: string; style?: string; created_at?: string | null }[];
    active_asset_id?: string | null;
  } | null;
  shots: Record<string, ShotStatusT>;
  shot_plan: ShotPlanItemT[];
  studio_project_id: string;
}

interface DatasetT {
  id: string;
  name: string;
  target: 'kohya' | 'ai_toolkit' | 'both';
  trigger_word?: string;
  class_word?: string;
  status: 'new' | 'captioning' | 'ready' | 'exported' | 'failed';
  error?: string | null;
  config?: Record<string, any>;
  captions?: Record<string, { natural?: string; tags?: string; image_rel?: string }>;
  zip_ready?: boolean;
  created_at?: string | null;
  image_count?: number;
}

interface ProjectRefT {
  id: string;
  name: string;
}

// ---------------------------------------------------------------------------
// Fetch helpers (same-origin, base '')
// ---------------------------------------------------------------------------

const BASE = '/api/character-studio';

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    let detail = '';
    try {
      const body = await res.json();
      detail = body?.detail || JSON.stringify(body);
    } catch {
      detail = res.statusText;
    }
    throw new Error(detail || `Request failed (${res.status})`);
  }
  if (res.status === 204) return undefined as unknown as T;
  const text = await res.text();
  return text ? (JSON.parse(text) as T) : (undefined as unknown as T);
}

const api = {
  listStories: () => apiFetch<StoryT[]>('/stories'),
  createStory: (data: { name: string; description?: string; default_style?: string }) =>
    apiFetch<StoryT>('/stories', { method: 'POST', body: JSON.stringify(data) }),
  updateStory: (id: string, data: { name?: string; description?: string; default_style?: string }) =>
    apiFetch<StoryT>(`/stories/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteStory: (id: string) => apiFetch<void>(`/stories/${id}`, { method: 'DELETE' }),

  listCharacters: (storyId?: string | null) =>
    apiFetch<CharacterT[]>(`/characters${storyId ? `?story_id=${storyId}` : ''}`),
  createCharacter: (data: Partial<CharacterT>) =>
    apiFetch<CharacterT>('/characters', { method: 'POST', body: JSON.stringify(data) }),
  getCharacter: (id: string) => apiFetch<CharacterT>(`/characters/${id}`),
  updateCharacter: (id: string, data: Partial<CharacterT>) =>
    apiFetch<CharacterT>(`/characters/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteCharacter: (id: string) => apiFetch<void>(`/characters/${id}`, { method: 'DELETE' }),

  getCatalogs: () => apiFetch<CatalogsT>('/catalogs'),

  generateBase: (id: string, data: { extra?: string; prompt_override?: string; model?: string; nsfw?: boolean }) =>
    apiFetch<{ job_id: string; prompt: string }>(`/characters/${id}/generate-base`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  setBase: (id: string, assetId: string) =>
    apiFetch<{ ok: boolean; asset_id: string; image_rel: string }>(`/characters/${id}/set-base`, {
      method: 'POST',
      body: JSON.stringify({ asset_id: assetId }),
    }),
  setActiveBase: (id: string, assetId: string) =>
    apiFetch<{ ok: boolean }>(`/characters/${id}/base-versions/set-active`, {
      method: 'POST',
      body: JSON.stringify({ asset_id: assetId }),
    }),
  restyleBase: (id: string, data: { style_key?: string; reference_asset_id?: string; project_id?: string; extra?: string }) =>
    apiFetch<{ job_id: string }>(`/characters/${id}/restyle-base`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  enhanceBasePrompt: (id: string, data: { prompt: string; reference_asset_ids?: string[] }) =>
    apiFetch<{ enhanced_prompt: string }>(`/characters/${id}/enhance-base-prompt`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  generateBaseAdvanced: (id: string, data: { prompt: string; model?: string; reference_asset_ids?: string[]; control_asset_id?: string; lllite_name?: string; img2img_asset_id?: string; denoise?: number; negative?: string }) =>
    apiFetch<{ job_id: string; prompt: string }>(`/characters/${id}/generate-base-advanced`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  resetShotPlan: (id: string) =>
    apiFetch<{ shot_plan: ShotPlanItemT[] }>(`/characters/${id}/reset-shot-plan`, { method: 'POST' }),
  generateShots: (id: string, data: { shot_ids?: string[] | null; regenerate?: boolean }) =>
    apiFetch<{ created: string[] }>(`/characters/${id}/generate-shots`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  getStatus: (id: string) => apiFetch<CharacterStatusT>(`/characters/${id}/status`),

  createDataset: (
    id: string,
    data: {
      name: string;
      target: 'kohya' | 'ai_toolkit' | 'both';
      trigger_word: string;
      class_word: string;
      repeats: number;
      quality_family: 'illustrious' | 'noobai' | 'pony' | 'none';
      include?: string[] | null;
    }
  ) => apiFetch<{ id: string; status: string; image_count: number }>(`/characters/${id}/datasets`, {
    method: 'POST',
    body: JSON.stringify(data),
  }),
  listDatasets: (id: string) => apiFetch<DatasetT[]>(`/characters/${id}/datasets`),
  getDataset: (id: string) => apiFetch<DatasetT>(`/datasets/${id}`),
  updateCaption: (id: string, data: { image: string; style: 'natural' | 'tags'; text: string }) =>
    apiFetch<void>(`/datasets/${id}/captions`, { method: 'PATCH', body: JSON.stringify(data) }),
  exportDataset: (id: string) => apiFetch<{ zip_path: string }>(`/datasets/${id}/export`, { method: 'POST' }),
  deleteDataset: (id: string) => apiFetch<void>(`/datasets/${id}`, { method: 'DELETE' }),

  pushToProject: (id: string, data: { project_id: string; max_extra_images: number }) =>
    apiFetch<{ ok: boolean; replaced: boolean; extra_images: number }>(`/characters/${id}/push-to-project`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
};

// listProjects hits a different base (/api/projects, not /api/character-studio/api/projects)
async function fetchProjects(): Promise<ProjectRefT[]> {
  const res = await fetch('/api/projects');
  if (!res.ok) throw new Error(`Failed to load projects (${res.status})`);
  return res.json();
}

function assetUrl(studioProjectId: string | null | undefined, assetId: string | null | undefined): string | null {
  if (!studioProjectId || !assetId) return null;
  return `/api/projects/${studioProjectId}/assets/${assetId}/file`;
}

// ---------------------------------------------------------------------------
// Small shared UI bits
// ---------------------------------------------------------------------------

function Spinner({ size = 16 }: { size?: number }) {
  return <Loader2 size={size} className="animate-spin text-purple-400" />;
}

function ErrorText({ msg }: { msg: string | null }) {
  if (!msg) return null;
  return (
    <div className="flex items-start gap-2 text-sm text-red-400 bg-red-950/30 border border-red-900/50 rounded-md px-3 py-2">
      <AlertTriangle size={16} className="shrink-0 mt-0.5" />
      <span>{msg}</span>
    </div>
  );
}

function StatusChip({ status }: { status: string }) {
  const map: Record<string, string> = {
    pending: 'bg-gray-700 text-gray-300',
    running: 'bg-indigo-900/60 text-indigo-300',
    done: 'bg-emerald-900/60 text-emerald-300',
    ready: 'bg-emerald-900/60 text-emerald-300',
    failed: 'bg-red-900/60 text-red-300',
    new: 'bg-gray-700 text-gray-300',
    captioning: 'bg-indigo-900/60 text-indigo-300',
    exported: 'bg-purple-900/60 text-purple-300',
  };
  const cls = map[status] || 'bg-gray-700 text-gray-300';
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}>
      {(status === 'running' || status === 'captioning') && <Spinner size={11} />}
      {status}
    </span>
  );
}

function Toast({ message, onClose }: { message: string; onClose: () => void }) {
  useEffect(() => {
    const t = setTimeout(onClose, 4000);
    return () => clearTimeout(t);
  }, [onClose]);
  return (
    <div className="fixed bottom-6 right-6 z-[9999] bg-gray-900 border border-purple-700 text-gray-100 rounded-lg shadow-xl px-4 py-3 text-sm flex items-center gap-2">
      <Check size={16} className="text-purple-400" />
      {message}
      <button onClick={onClose} className="ml-2 text-gray-500 hover:text-gray-300">
        <X size={14} />
      </button>
    </div>
  );
}

function TextField({
  label,
  value,
  onChange,
  placeholder,
  list,
  textarea,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  list?: string;
  textarea?: boolean;
}) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-gray-400">{label}</span>
      {textarea ? (
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          rows={3}
          className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 focus:outline-none focus:border-purple-600 resize-y"
        />
      ) : (
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          list={list}
          className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 focus:outline-none focus:border-purple-600"
        />
      )}
    </label>
  );
}

// ---------------------------------------------------------------------------
// Sidebar: Stories
// ---------------------------------------------------------------------------

function StoriesSidebar({
  stories,
  selectedStoryId,
  onSelect,
  onStoriesChanged,
}: {
  stories: StoryT[];
  selectedStoryId: string | null;
  onSelect: (id: string | null) => void;
  onStoriesChanged: () => void;
}) {
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const [newStyle, setNewStyle] = useState(DEFAULT_STYLE);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState('');

  const submitCreate = async () => {
    if (!newName.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.createStory({ name: newName.trim(), description: newDesc.trim() || undefined, default_style: newStyle });
      setNewName('');
      setNewDesc('');
      setNewStyle(DEFAULT_STYLE);
      setCreating(false);
      onStoriesChanged();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const submitRename = async (id: string) => {
    if (!editName.trim()) return;
    try {
      await api.updateStory(id, { name: editName.trim() });
      setEditingId(null);
      onStoriesChanged();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const doDelete = async (id: string) => {
    if (!window.confirm('Delete this story? Characters will be kept but un-assigned.')) return;
    try {
      await api.deleteStory(id);
      if (selectedStoryId === id) onSelect(null);
      onStoriesChanged();
    } catch (e: any) {
      setError(e.message);
    }
  };

  return (
    <div className="w-64 shrink-0 bg-gray-900 border border-gray-800 rounded-lg p-3 flex flex-col gap-1 h-fit sticky top-6">
      <div className="text-xs uppercase tracking-wide text-gray-500 px-2 pb-2">Stories</div>

      <button
        onClick={() => onSelect(null)}
        className={`text-left px-3 py-2 rounded-md text-sm transition-colors ${
          selectedStoryId === null ? 'bg-purple-900/40 text-purple-200' : 'hover:bg-gray-800 text-gray-300'
        }`}
      >
        All Characters
      </button>

      {stories.map((s) => (
        <div
          key={s.id}
          className={`group flex items-center rounded-md ${
            selectedStoryId === s.id ? 'bg-purple-900/40' : 'hover:bg-gray-800'
          }`}
        >
          {editingId === s.id ? (
            <div className="flex items-center gap-1 flex-1 px-2 py-1">
              <input
                autoFocus
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') submitRename(s.id);
                  if (e.key === 'Escape') setEditingId(null);
                }}
                className="flex-1 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100 min-w-0"
              />
              <button onClick={() => submitRename(s.id)} className="text-emerald-400 hover:text-emerald-300 shrink-0">
                <Check size={14} />
              </button>
              <button onClick={() => setEditingId(null)} className="text-gray-500 hover:text-gray-300 shrink-0">
                <X size={14} />
              </button>
            </div>
          ) : (
            <>
              <button
                onClick={() => onSelect(s.id)}
                className={`flex-1 text-left px-3 py-2 text-sm truncate ${
                  selectedStoryId === s.id ? 'text-purple-200' : 'text-gray-300'
                }`}
                title={s.name}
              >
                {s.name}
                <span className="text-gray-500 ml-1.5">({s.character_count ?? 0})</span>
              </button>
              <div className="hidden group-hover:flex items-center gap-1 pr-2 shrink-0">
                <button
                  onClick={() => {
                    setEditingId(s.id);
                    setEditName(s.name);
                  }}
                  className="text-gray-500 hover:text-gray-200"
                  title="Rename"
                >
                  <Pencil size={13} />
                </button>
                <button onClick={() => doDelete(s.id)} className="text-gray-500 hover:text-red-400" title="Delete">
                  <Trash2 size={13} />
                </button>
              </div>
            </>
          )}
        </div>
      ))}

      {creating ? (
        <div className="flex flex-col gap-2 mt-2 p-2 bg-gray-800/60 rounded-md">
          <input
            autoFocus
            placeholder="Story name"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            className="bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100"
          />
          <input
            placeholder="Description (optional)"
            value={newDesc}
            onChange={(e) => setNewDesc(e.target.value)}
            className="bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100"
          />
          <StyleSelect value={newStyle} onChange={setNewStyle} label="Default style" />
          <div className="flex gap-2">
            <button
              onClick={submitCreate}
              disabled={busy || !newName.trim()}
              className="flex-1 bg-purple-700 hover:bg-purple-600 disabled:opacity-50 rounded px-2 py-1.5 text-sm font-medium flex items-center justify-center gap-1"
            >
              {busy ? <Spinner size={13} /> : <Check size={13} />}
              Create
            </button>
            <button
              onClick={() => {
                setCreating(false);
                setError(null);
              }}
              className="px-2 py-1.5 text-sm text-gray-400 hover:text-gray-200"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button
          onClick={() => setCreating(true)}
          className="mt-2 flex items-center gap-1.5 px-3 py-2 rounded-md text-sm text-gray-400 hover:text-purple-300 hover:bg-gray-800 transition-colors"
        >
          <Plus size={14} /> New Story
        </button>
      )}

      {error && (
        <div className="mt-2">
          <ErrorText msg={error} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Character grid + create card
// ---------------------------------------------------------------------------

// PARKED 2026-08-11 — CharacterCard / NewCharacterCard were the old DB-only
// character grid. v1.276.0 replaced that grid with <UnifiedCharacterGrid />
// (see the render below), and nothing has referenced these two since; they are
// kept here, commented out, as the reference for the old card language.
// Un-commenting also needs `Copy` back in the lucide-react import list.
/*
function CharacterCard({ character, storyName, onClick, onDelete, onClone }: {
  character: CharacterT; storyName: string | null; onClick: () => void; onDelete: () => void;
  onClone?: () => void;
}) {
  const initials = character.name
    .split(/\s+/)
    .map((p) => p[0])
    .filter(Boolean)
    .slice(0, 2)
    .join('')
    .toUpperCase();

  return (
    <div
      onClick={onClick}
      className="bg-gray-900 border border-gray-800 rounded-lg p-4 hover:border-purple-600 hover:shadow-lg transition-all cursor-pointer group flex flex-col gap-3"
    >
      <div className="relative h-28 rounded-md bg-gradient-to-br from-purple-900/30 to-gray-800 flex items-center justify-center overflow-hidden">
        {(() => {
          // thumbnail: user-chosen hero, else active base version, else newest
          const v = ((character.manifest as Record<string, unknown> | undefined)?.vnccs || null) as
            { variant?: string; hero_url?: string;
              active_base?: string; base_versions?: Array<{ id?: string; url?: string }> } | null;
          const vers = (v?.base_versions || []).filter(Boolean);
          const thumb = v?.hero_url
            || vers.find((b) => b.id && b.id === v?.active_base)?.url
            || (vers.length ? vers[vers.length - 1].url : null);
          return thumb ? (
            <img src={thumb} alt={character.name}
                 className="absolute inset-0 w-full h-full object-cover object-top opacity-90" />
          ) : null;
        })()}
        <button
          onClick={(e) => { e.stopPropagation(); onDelete(); }}
          title="Delete character"
          className="absolute top-1.5 left-1.5 p-1 rounded bg-gray-950/70 text-gray-500 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity"
        >
          <Trash2 size={13} />
        </button>
        {onClone && (
          <button
            onClick={(e) => { e.stopPropagation(); onClone(); }}
            title="Clone character — start a new one from this character's base image"
            className="absolute top-1.5 left-8 p-1 rounded bg-gray-950/70 text-gray-500 hover:text-purple-300 opacity-0 group-hover:opacity-100 transition-opacity"
          >
            <Copy size={13} />
          </button>
        )}
        {Boolean((character.manifest as Record<string, unknown> | undefined)?.vnccs) && (
          <span className="absolute top-1.5 right-1.5 text-[10px] px-1.5 py-0.5 rounded bg-purple-900/80 text-purple-200 border border-purple-600/60">
            {(((character.manifest as Record<string, unknown> | undefined)?.vnccs as { variant?: string } | undefined)?.variant === 'klein')
              ? '🧪 VNCCS Klein' : '✨ VNCCS Native'}
          </span>
        )}
        {!(((character.manifest as Record<string, unknown> | undefined)?.vnccs as { hero_url?: string; base_versions?: unknown[] } | undefined)?.hero_url
           || ((((character.manifest as Record<string, unknown> | undefined)?.vnccs as { base_versions?: unknown[] } | undefined)?.base_versions || []).length > 0)) && (
          <span className="text-3xl font-bold text-purple-300/80 group-hover:text-purple-200 transition-colors">
            {initials || '?'}
          </span>
        )}
      </div>
      <div>
        <div className="flex items-center gap-2">
          <h3 className="font-semibold text-gray-100 truncate">{character.name}</h3>
          <span
            className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded ${
              character.kind === 'item' ? 'bg-amber-900/50 text-amber-300' : 'bg-indigo-900/50 text-indigo-300'
            }`}
          >
            {character.kind}
          </span>
        </div>
        {storyName && <div className="text-xs text-gray-500 truncate mt-0.5">{storyName}</div>}
        {character.trigger_word && (
          <div className="text-xs text-purple-400 font-mono mt-1 truncate">{character.trigger_word}</div>
        )}
      </div>
    </div>
  );
}

function NewCharacterCard({ onClick }: { onClick: () => void }) {
  return (
    <div
      onClick={onClick}
      className="bg-gray-900/50 border border-dashed border-gray-700 rounded-lg p-4 hover:border-purple-600 hover:bg-gray-900 transition-all cursor-pointer flex flex-col items-center justify-center gap-2 min-h-[168px] text-gray-500 hover:text-purple-300"
    >
      <Plus size={28} />
      <span className="text-sm font-medium">New Character</span>
    </div>
  );
}
*/

function CreateCharacterForm({
  stories,
  defaultStoryId,
  onCancel,
  onCreated,
}: {
  stories: StoryT[];
  defaultStoryId: string | null;
  onCancel: () => void;
  onCreated: (c: CharacterT) => void;
}) {
  const [name, setName] = useState('');
  const [storyId, setStoryId] = useState<string>(defaultStoryId || '');
  const [kind, setKind] = useState<'character' | 'item'>('character');
  const [triggerWord, setTriggerWord] = useState('');
  const [classWord, setClassWord] = useState('');
  const [description, setDescription] = useState('');
  const [wizardInfo, setWizardInfo] = useState<WizardCharacterInfoT | null>(null);
  const [style, setStyle] = useState(
    stories.find((s) => s.id === (defaultStoryId || ''))?.default_style || DEFAULT_STYLE
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Following the selected Story's default style (set a project's style once).
  useEffect(() => {
    const s = stories.find((x) => x.id === storyId)?.default_style;
    if (s) setStyle(s);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storyId]);

  const applyWizardInfo = (info: WizardCharacterInfoT) => {
    setWizardInfo(info);
    if (info.additional_details && !description.trim()) {
      setDescription(String(info.additional_details));
    }
  };

  // WizardCharacterInfoT.age may come back as a number from the LLM;
  // CharacterInfoT (Phase 1's character_info shape) expects string fields.
  const wizardInfoAsCharacterInfo = (): CharacterInfoT | undefined => {
    const out: CharacterInfoT = {};
    if (wizardInfo) {
      for (const [k, v] of Object.entries(wizardInfo)) {
        if (v === undefined || v === null) continue;
        out[k] = String(v);
      }
    }
    out.style = style;
    return out;
  };

  const submit = async () => {
    if (!name.trim()) {
      setError('Name is required.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await api.createCharacter({
        name: name.trim(),
        story_id: storyId || undefined,
        kind,
        trigger_word: triggerWord.trim() || undefined,
        class_word: classWord.trim() || undefined,
        description: description.trim() || undefined,
        character_info: wizardInfoAsCharacterInfo(),
      });
      onCreated(created);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/70 z-[9990] flex items-center justify-center p-4" onClick={onCancel}>
      <div
        className="bg-gray-900 border border-gray-800 rounded-lg p-6 w-full max-w-lg flex flex-col gap-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">New Character</h2>
          <button onClick={onCancel} className="text-gray-500 hover:text-gray-300">
            <X size={18} />
          </button>
        </div>

        <TextField label="Name" value={name} onChange={setName} placeholder="e.g. Aria Nightshade" />

        <div className="grid grid-cols-2 gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-gray-400">Story</span>
            <select
              value={storyId}
              onChange={(e) => setStoryId(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 focus:outline-none focus:border-purple-600"
            >
              <option value="">(none)</option>
              {stories.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-gray-400">Kind</span>
            <select
              value={kind}
              onChange={(e) => setKind(e.target.value as 'character' | 'item')}
              className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 focus:outline-none focus:border-purple-600"
            >
              <option value="character">Character</option>
              <option value="item">Item</option>
            </select>
          </label>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <TextField label="Trigger Word" value={triggerWord} onChange={setTriggerWord} placeholder="e.g. ariaknshd" />
          <TextField label="Class Word" value={classWord} onChange={setClassWord} placeholder="e.g. woman" />
        </div>

        <TextField label="Description" value={description} onChange={setDescription} textarea placeholder="Short description..." />

        <StyleSelect value={style} onChange={setStyle} />

        {kind === 'character' && (
          <div className="flex flex-col gap-2">
            <CharacterWizardButton onApply={applyWizardInfo} style={style} />
            {wizardInfo && (
              <div className="text-xs text-gray-400 bg-gray-800/60 border border-gray-700 rounded-md px-3 py-2 flex flex-wrap gap-x-4 gap-y-1">
                {Object.entries(wizardInfo)
                  .filter(([, v]) => v !== undefined && v !== null && String(v).trim() !== '')
                  .map(([k, v]) => (
                    <span key={k}>
                      <span className="text-gray-500">{k}:</span> {String(v)}
                    </span>
                  ))}
              </div>
            )}
          </div>
        )}

        <ErrorText msg={error} />

        <div className="flex justify-end gap-2 pt-2">
          <button onClick={onCancel} className="px-4 py-2 text-sm text-gray-400 hover:text-gray-200">
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={busy}
            className="px-4 py-2 bg-purple-700 hover:bg-purple-600 disabled:opacity-50 rounded-md text-sm font-medium flex items-center gap-2"
          >
            {busy && <Spinner size={14} />}
            Create
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Push to project modal
// ---------------------------------------------------------------------------

function PushToProjectModal({
  character,
  onClose,
  onDone,
}: {
  character: CharacterT;
  onClose: () => void;
  onDone: (msg: string) => void;
}) {
  const [projects, setProjects] = useState<ProjectRefT[]>([]);
  const [projectId, setProjectId] = useState('');
  const [maxExtra, setMaxExtra] = useState(3);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadingProjects, setLoadingProjects] = useState(true);

  useEffect(() => {
    fetchProjects()
      .then((p) => {
        setProjects(p);
        if (p.length) setProjectId(p[0].id);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoadingProjects(false));
  }, []);

  const submit = async () => {
    if (!projectId) {
      setError('Select a project.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await api.pushToProject(character.id, { project_id: projectId, max_extra_images: maxExtra });
      onDone(`Pushed "${character.name}" — ${res.extra_images} extra image(s) copied.`);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/70 z-[9990] flex items-center justify-center p-4" onClick={onClose}>
      <div
        className="bg-gray-900 border border-gray-800 rounded-lg p-6 w-full max-w-md flex flex-col gap-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <Send size={18} className="text-purple-400" />
            Push to Project
          </h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300">
            <X size={18} />
          </button>
        </div>

        {loadingProjects ? (
          <div className="flex items-center gap-2 text-sm text-gray-400">
            <Spinner size={14} /> Loading projects...
          </div>
        ) : (
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-gray-400">Target Project</span>
            <select
              value={projectId}
              onChange={(e) => setProjectId(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 focus:outline-none focus:border-purple-600"
            >
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
        )}

        <label className="flex flex-col gap-1 text-sm">
          <span className="text-gray-400">Max Extra Images</span>
          <input
            type="number"
            min={0}
            value={maxExtra}
            onChange={(e) => setMaxExtra(Number(e.target.value))}
            className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 focus:outline-none focus:border-purple-600 w-32"
          />
        </label>

        <ErrorText msg={error} />

        <div className="flex justify-end gap-2 pt-2">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-400 hover:text-gray-200">
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={busy || !projectId}
            className="px-4 py-2 bg-purple-700 hover:bg-purple-600 disabled:opacity-50 rounded-md text-sm font-medium flex items-center gap-2"
          >
            {busy && <Spinner size={14} />}
            Push
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SHEET tab
// ---------------------------------------------------------------------------

const CHAR_INFO_FIELDS: { key: keyof CharacterInfoT; label: string; catalogKey?: string; select?: string[] }[] = [
  { key: 'sex', label: 'Sex', select: ['male', 'female', 'other'] },
  { key: 'age', label: 'Age' },
  { key: 'race', label: 'Race', catalogKey: 'races' },
  { key: 'skin_color', label: 'Skin Color', catalogKey: 'skin_colors' },
  { key: 'hair', label: 'Hair', catalogKey: 'hairstyles' },
  { key: 'eyes', label: 'Eyes', catalogKey: 'eyes' },
  { key: 'face', label: 'Face' },
  { key: 'body', label: 'Body' },
  { key: 'additional_details', label: 'Additional Details' },
  { key: 'outfit', label: 'Outfit' },
  { key: 'background_color', label: 'Background Color' },
];

function SheetTab({
  character,
  stories,
  catalogs,
  onSaved,
  onStatusRefresh,
  status,
}: {
  character: CharacterT;
  stories: StoryT[];
  catalogs: CatalogsT | null;
  onSaved: (c: CharacterT) => void;
  onStatusRefresh: () => void;
  status: CharacterStatusT | null;
}) {
  const [name, setName] = useState(character.name);
  const [storyId, setStoryId] = useState(character.story_id || '');
  const [kind, setKind] = useState<'character' | 'item'>(character.kind);
  const [triggerWord, setTriggerWord] = useState(character.trigger_word || '');
  const [classWord, setClassWord] = useState(character.class_word || '');
  const [description, setDescription] = useState(character.description || '');
  const [info, setInfo] = useState<CharacterInfoT>(character.character_info || {});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveOk, setSaveOk] = useState(false);

  const [extra, setExtra] = useState('');
  const [promptOverride, setPromptOverride] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);
  const [baseModel, setBaseModel] = useState('');
  const [defaultModel, setDefaultModel] = useState('z_image_turbo');
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [baseEditorOpen, setBaseEditorOpen] = useState(false);
  const [customBaseOpen, setCustomBaseOpen] = useState(false);
  const [pendingBase, setPendingBase] = useState(false);
  const [baseStartedAt, setBaseStartedAt] = useState<number | null>(null);
  const [elapsedSec, setElapsedSec] = useState(0);
  const basePollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Default the base-render model dropdown to the configured First Frame model
  // (Settings -> single_image_generator).
  useEffect(() => {
    let alive = true;
    fetch('/api/settings')
      .then((r) => (r.ok ? r.json() : null))
      .then((s) => {
        if (alive && s && s.single_image_generator) setDefaultModel(s.single_image_generator);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    setName(character.name);
    setStoryId(character.story_id || '');
    setKind(character.kind);
    setTriggerWord(character.trigger_word || '');
    setClassWord(character.class_word || '');
    setDescription(character.description || '');
    setInfo(character.character_info || {});
  }, [character]);

  const save = async () => {
    setSaving(true);
    setSaveError(null);
    setSaveOk(false);
    try {
      const updated = await api.updateCharacter(character.id, {
        name: name.trim(),
        story_id: storyId || null,
        kind,
        trigger_word: triggerWord.trim(),
        class_word: classWord.trim(),
        description: description.trim(),
        character_info: info,
      });
      // Guard: only adopt a well-formed character back into state so a
      // malformed/legacy response can never wipe the form fields.
      if (updated && (updated as { id?: string }).id) onSaved(updated);
      setSaveOk(true);
      setTimeout(() => setSaveOk(false), 2500);
    } catch (e: any) {
      setSaveError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const generateBase = async () => {
    setGenerating(true);
    setGenError(null);
    try {
      await api.generateBase(character.id, {
        extra: extra.trim() || undefined,
        prompt_override: promptOverride.trim() || undefined,
        model: baseModel || undefined,
        nsfw: typeof info.nsfw === 'boolean' ? info.nsfw : undefined,
      });
      setPendingBase(true);
      setBaseStartedAt(Date.now());
      onStatusRefresh();
    } catch (e: any) {
      setGenError(e.message);
    } finally {
      setGenerating(false);
    }
  };

  // NVCCS-style import: upload an image and use it AS the base render.
  const uploadAsBase = async (file: File) => {
    if (!status?.studio_project_id) {
      setUploadError('Studio project not ready — try again in a moment.');
      return;
    }
    setUploading(true);
    setUploadError(null);
    try {
      const fd = new FormData();
      fd.append('asset_type', 'generated_image');
      fd.append('file', file);
      const res = await fetch(`/api/projects/${status.studio_project_id}/assets/upload`, {
        method: 'POST',
        body: fd,
      });
      if (!res.ok) throw new Error(`Upload failed (${res.status})`);
      const asset = await res.json();
      await api.setBase(character.id, asset.id);
      onStatusRefresh();
    } catch (e: any) {
      setUploadError(e.message);
    } finally {
      setUploading(false);
    }
  };

  // Merge an LLM tag sheet (from the Wizard or Clone-from-image) into the
  // editable Character Info fields; fill Description if still empty.
  const applyTagSheet = (wi: WizardCharacterInfoT, desc?: string) => {
    setInfo((prev) => {
      const merged = { ...prev };
      for (const [k, v] of Object.entries(wi)) {
        if (v === undefined || v === null) continue;
        // Preserve booleans (e.g. nsfw) — String(false) is truthy and would
        // wrongly tick the NSFW toggle.
        merged[k] = typeof v === 'boolean' ? v : String(v);
      }
      return merged;
    });
    const fill = desc || (wi.additional_details ? String(wi.additional_details) : '');
    if (fill && !description.trim()) setDescription(fill);
  };

  const baseUrl = status?.base?.asset_id ? assetUrl(status.studio_project_id, status.base.asset_id) : null;
  const baseState = status?.base?.status;
  const baseRunning = baseState === 'pending' || baseState === 'running';
  const baseFailed = baseState === 'failed';
  const baseError = status?.base?.error || null;
  const baseInFlight = pendingBase || baseRunning;

  // Clear the in-flight flag once the image lands or the render fails.
  useEffect(() => {
    if (baseUrl || baseFailed) setPendingBase(false);
  }, [baseUrl, baseFailed]);

  // Robust completion detection: while a base render is in-flight, poll status
  // ourselves (independent of the parent poll) so the preview reliably updates
  // the moment it's done. Soft-capped by the elapsed ticker below.
  useEffect(() => {
    if (basePollRef.current) {
      clearInterval(basePollRef.current);
      basePollRef.current = null;
    }
    if (baseInFlight) {
      basePollRef.current = setInterval(() => {
        onStatusRefresh();
      }, 2500);
    }
    return () => {
      if (basePollRef.current) clearInterval(basePollRef.current);
    };
  }, [baseInFlight, onStatusRefresh]);

  // Elapsed-seconds ticker for the running indicator.
  useEffect(() => {
    if (!baseInFlight) {
      setElapsedSec(0);
      return;
    }
    const t0 = baseStartedAt || Date.now();
    const id = setInterval(() => {
      const s = Math.floor((Date.now() - t0) / 1000);
      setElapsedSec(s);
      if (s > 600) setPendingBase(false); // soft cap; parent poll still governs
    }, 1000);
    return () => clearInterval(id);
  }, [baseInFlight, baseStartedAt]);

  return (
    <div className="flex flex-col gap-6">
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-5 flex flex-col gap-4">
        <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">Identity</h3>
        <div className="grid grid-cols-2 gap-4">
          <TextField label="Name" value={name} onChange={setName} />
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-gray-400">Story</span>
            <select
              value={storyId}
              onChange={(e) => setStoryId(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 focus:outline-none focus:border-purple-600"
            >
              <option value="">(none)</option>
              {stories.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-gray-400">Kind</span>
            <select
              value={kind}
              onChange={(e) => setKind(e.target.value as 'character' | 'item')}
              className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 focus:outline-none focus:border-purple-600"
            >
              <option value="character">Character</option>
              <option value="item">Item</option>
            </select>
          </label>
          <TextField label="Trigger Word" value={triggerWord} onChange={setTriggerWord} />
          <TextField label="Class Word" value={classWord} onChange={setClassWord} />
        </div>
        <TextField label="Description" value={description} onChange={setDescription} textarea />

        <StyleSelect
          value={info.style || DEFAULT_STYLE}
          onChange={(v) => setInfo((prev) => ({ ...prev, style: v }))}
        />

        {kind === 'character' && (
          <label className="flex items-start gap-2 text-sm text-gray-300 bg-gray-800/40 border border-gray-800 rounded-md px-3 py-2">
            <input
              type="checkbox"
              checked={!!(info as any).nsfw}
              onChange={(e) => setInfo((prev) => ({ ...prev, nsfw: e.target.checked }))}
              className="mt-0.5"
            />
            <span>
              <b>NSFW base</b> — renders a nude base (vs SFW underwear) so costumes layer cleanly over a
              clothing-ready body. Per-character; overrides the global SFW/NSFW setting. Default: SFW.
            </span>
          </label>
        )}

        {kind === 'character' && (
          <>
            <div className="flex items-center justify-between gap-3 mt-2">
              <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">Character Info</h3>
              {/* Auto-fill the tag sheet: from a text description (Wizard) or by
                  analyzing a reference character image (Clone from image). */}
              <div className="flex items-start gap-2">
                <CharacterWizardButton onApply={(wi) => applyTagSheet(wi)} style={info.style || DEFAULT_STYLE} />
                <CloneFromImageButton
                  studioProjectId={status?.studio_project_id}
                  onApply={(wi, desc) => applyTagSheet(wi, desc)}
                  style={info.style || DEFAULT_STYLE}
                />
              </div>
            </div>
            <p className="text-xs text-gray-500 -mt-1">
              Tip: use the Wizard to describe the character in plain words and auto-fill these
              fields, then tweak and Save.
            </p>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
              {CHAR_INFO_FIELDS.map((f) => {
                const catalogArr = f.catalogKey && catalogs?.tags?.[f.catalogKey];
                const listId = `catalog-${f.key}`;
                if (f.select) {
                  return (
                    <label key={f.key} className="flex flex-col gap-1 text-sm">
                      <span className="text-gray-400">{f.label}</span>
                      <select
                        value={(info[f.key] as string) || ''}
                        onChange={(e) => setInfo((prev) => ({ ...prev, [f.key]: e.target.value }))}
                        className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 focus:outline-none focus:border-purple-600"
                      >
                        <option value="">(unset)</option>
                        {f.select.map((v) => (
                          <option key={v} value={v}>
                            {v}
                          </option>
                        ))}
                      </select>
                    </label>
                  );
                }
                return (
                  <div key={f.key} className="flex flex-col gap-1 text-sm">
                    <TextField
                      label={f.label}
                      value={(info[f.key] as string) || ''}
                      onChange={(v) => setInfo((prev) => ({ ...prev, [f.key]: v }))}
                      list={Array.isArray(catalogArr) ? listId : undefined}
                    />
                    {Array.isArray(catalogArr) && (
                      <datalist id={listId}>
                        {catalogArr.map((t) => (
                          <option key={t.tag} value={t.label} />
                        ))}
                      </datalist>
                    )}
                  </div>
                );
              })}
            </div>
          </>
        )}

        {saveError && <ErrorText msg={saveError} />}

        <div className="flex items-center gap-3">
          <button
            onClick={save}
            disabled={saving}
            className="px-4 py-2 bg-purple-700 hover:bg-purple-600 disabled:opacity-50 rounded-md text-sm font-medium flex items-center gap-2 self-start"
          >
            {saving && <Spinner size={14} />}
            Save
          </button>
          {saveOk && (
            <span className="text-emerald-400 text-sm flex items-center gap-1">
              <Check size={14} /> Saved
            </span>
          )}
        </div>
      </div>

      <div className="bg-gray-900 border border-gray-800 rounded-lg p-5 flex flex-col gap-4">
        <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">Base Render</h3>

        {baseInFlight && (
          <div className="flex items-center gap-2 text-sm text-indigo-200 bg-indigo-950/40 border border-indigo-800/50 rounded-md px-3 py-2">
            <Spinner size={14} />
            <span>
              Rendering base image on a worker… <span className="text-indigo-300/70">({elapsedSec}s)</span>
            </span>
          </div>
        )}

        <div className="flex flex-col md:flex-row gap-5">
          <div className="flex-1 flex flex-col gap-3">
            <TextField
              label="Extra instructions (optional)"
              value={extra}
              onChange={setExtra}
              placeholder="e.g. dramatic lighting, three-quarter view"
            />
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-gray-400">Model</span>
              <select
                value={baseModel}
                onChange={(e) => setBaseModel(e.target.value)}
                className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 focus:outline-none focus:border-purple-600 text-sm"
              >
                <option value="">Use First Frame default ({defaultModel})</option>
                <option value="z_image_turbo">Z-Image Turbo</option>
                <option value="krea2_turbo">Krea 2 Turbo</option>
                <option value="anima">Anima (anime base)</option>
                <option value="flux2_klein_dev_9b">FLUX.2 Klein T2I</option>
              </select>
            </label>
            <button
              onClick={() => setShowAdvanced((v) => !v)}
              className="text-xs text-gray-500 hover:text-gray-300 self-start"
            >
              {showAdvanced ? 'Hide' : 'Show'} advanced prompt override
            </button>
            {showAdvanced && (
              <TextField
                label="Prompt Override"
                value={promptOverride}
                onChange={setPromptOverride}
                textarea
                placeholder="Full manual prompt (overrides auto-built prompt)"
              />
            )}
            {genError && <ErrorText msg={genError} />}
            {baseFailed && baseError && <ErrorText msg={`Base render failed: ${baseError}`} />}
            <button
              onClick={generateBase}
              disabled={generating || baseInFlight}
              className="px-4 py-2 bg-indigo-700 hover:bg-indigo-600 disabled:opacity-50 rounded-md text-sm font-medium flex items-center gap-2 self-start"
            >
              {(generating || baseInFlight) && <Spinner size={14} />}
              {baseInFlight ? 'Rendering…' : baseFailed ? 'Retry Base Render' : 'Generate Base Render'}
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) uploadAsBase(f);
                e.target.value = '';
              }}
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
              className="px-3 py-2 bg-gray-800 hover:bg-gray-700 border border-gray-700 disabled:opacity-50 rounded-md text-sm font-medium flex items-center gap-2 text-gray-300 self-start"
            >
              {uploading ? <Spinner size={13} /> : <Upload size={13} />}
              Upload image as base
            </button>
            <button
              type="button"
              onClick={() => setCustomBaseOpen(true)}
              className="px-3 py-2 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-md text-sm font-medium flex items-center gap-2 text-gray-300 self-start"
            >
              <Sparkles size={13} className="text-indigo-400" />
              Create Custom Base (Advanced)
            </button>
            {uploadError && <ErrorText msg={uploadError} />}
          </div>

          <div className="w-full md:w-64 shrink-0">
            <div className="aspect-square bg-gray-800 border border-gray-700 rounded-lg overflow-hidden flex items-center justify-center">
              {baseUrl ? (
                <img
                  src={baseUrl}
                  alt="Base render"
                  onClick={() => setLightboxOpen(true)}
                  title="Click to enlarge"
                  className="w-full h-full object-cover cursor-pointer"
                />
              ) : baseInFlight ? (
                <div className="flex flex-col items-center gap-2 text-indigo-300 text-sm">
                  <Spinner size={22} />
                  Rendering… {elapsedSec}s
                </div>
              ) : baseFailed ? (
                <div className="flex flex-col items-center gap-1.5 text-red-400 text-sm px-3 text-center">
                  <AlertTriangle size={20} />
                  Render failed
                </div>
              ) : (
                <span className="text-gray-600 text-sm">No base render yet</span>
              )}
            </div>
            {baseUrl && (
              <button
                type="button"
                onClick={() => setBaseEditorOpen(true)}
                className="mt-2 w-full px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-md text-sm flex items-center justify-center gap-2 text-gray-300"
              >
                <Pencil size={13} /> Edit / Versions
                {status?.base?.versions?.length ? ` (${status.base.versions.length})` : ''}
              </button>
            )}
          </div>
        </div>
      </div>

      {lightboxOpen && baseUrl && (
        <div
          className="fixed inset-0 bg-black/80 z-[9995] flex items-center justify-center p-6"
          onClick={() => setLightboxOpen(false)}
        >
          <button
            type="button"
            onClick={() => setLightboxOpen(false)}
            className="absolute top-4 right-4 text-gray-300 hover:text-white"
          >
            <X size={24} />
          </button>
          <img
            src={baseUrl}
            alt="Base render"
            onClick={(e) => e.stopPropagation()}
            className="max-w-full max-h-full object-contain rounded-lg"
          />
        </div>
      )}

      {baseEditorOpen && (
        <BaseEditorModal
          characterId={character.id}
          studioProjectId={status?.studio_project_id}
          versions={status?.base?.versions || []}
          activeAssetId={status?.base?.active_asset_id || status?.base?.asset_id}
          characterStyle={info.style || DEFAULT_STYLE}
          onClose={() => setBaseEditorOpen(false)}
          onChanged={onStatusRefresh}
          api={{ setActiveBase: api.setActiveBase, restyleBase: api.restyleBase }}
        />
      )}

      {customBaseOpen && (
        <CustomBaseModal
          characterId={character.id}
          studioProjectId={status?.studio_project_id}
          defaultModel={defaultModel}
          onClose={() => setCustomBaseOpen(false)}
          onGenerated={() => {
            setPendingBase(true);
            setBaseStartedAt(Date.now());
            onStatusRefresh();
          }}
          api={{ enhanceBasePrompt: api.enhanceBasePrompt, generateBaseAdvanced: api.generateBaseAdvanced }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// RENDERS tab
// ---------------------------------------------------------------------------

function RendersTab({
  character,
  status,
  engine,
  onStatusChanged,
}: {
  character: CharacterT;
  status: CharacterStatusT | null;
  engine: EngineT;
  onStatusChanged: () => void;
}) {
  const [plan, setPlan] = useState<ShotPlanItemT[]>(status?.shot_plan || []);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dirtyIds, setDirtyIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (status?.shot_plan) setPlan(status.shot_plan);
  }, [status?.shot_plan]);

  const persistManifest = async (nextPlan: ShotPlanItemT[]) => {
    const manifest = { ...(character.manifest || {}), shot_plan: nextPlan };
    await api.updateCharacter(character.id, { manifest });
  };

  const updateShot = (id: string, patch: Partial<ShotPlanItemT>) => {
    setPlan((prev) => prev.map((s) => (s.id === id ? { ...s, ...patch } : s)));
    setDirtyIds((prev) => new Set(prev).add(id));
  };

  const commitShot = async (id: string) => {
    const nextPlan = plan;
    try {
      await persistManifest(nextPlan);
      setDirtyIds((prev) => {
        const n = new Set(prev);
        n.delete(id);
        return n;
      });
    } catch (e: any) {
      setError(e.message);
    }
  };

  const toggleEnabled = async (id: string, enabled: boolean) => {
    const nextPlan = plan.map((s) => (s.id === id ? { ...s, enabled } : s));
    setPlan(nextPlan);
    try {
      await persistManifest(nextPlan);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  };

  const generateMissing = async () => {
    setBusyAction('missing');
    setError(null);
    try {
      await api.generateShots(character.id, { shot_ids: null, regenerate: false });
      onStatusChanged();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusyAction(null);
    }
  };

  const regenerateSelected = async () => {
    if (!selected.size) return;
    setBusyAction('regen');
    setError(null);
    try {
      await api.generateShots(character.id, { shot_ids: Array.from(selected), regenerate: true });
      onStatusChanged();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusyAction(null);
    }
  };

  const resetPlan = async () => {
    if (!window.confirm('Reset the shot plan to defaults? Unsaved instruction edits will be lost.')) return;
    setBusyAction('reset');
    setError(null);
    try {
      const res = await api.resetShotPlan(character.id);
      setPlan(res.shot_plan);
      onStatusChanged();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusyAction(null);
    }
  };

  const [lightboxUrl, setLightboxUrl] = useState<string | null>(null);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={generateMissing}
          disabled={busyAction !== null}
          className="px-3 py-1.5 bg-indigo-700 hover:bg-indigo-600 disabled:opacity-50 rounded-md text-sm font-medium flex items-center gap-2"
        >
          {busyAction === 'missing' && <Spinner size={13} />}
          Generate Missing Shots
        </button>
        <button
          onClick={regenerateSelected}
          disabled={busyAction !== null || !selected.size}
          className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 disabled:opacity-50 rounded-md text-sm font-medium flex items-center gap-2 border border-gray-700"
        >
          {busyAction === 'regen' && <Spinner size={13} />}
          Regenerate Selected ({selected.size})
        </button>
        <button
          onClick={resetPlan}
          disabled={busyAction !== null}
          className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 disabled:opacity-50 rounded-md text-sm font-medium flex items-center gap-2 border border-gray-700 ml-auto"
        >
          {busyAction === 'reset' && <Spinner size={13} />}
          <RefreshCw size={13} />
          Reset Plan to Defaults
        </button>
      </div>

      <ErrorText msg={error} />

      <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-gray-800/60 text-gray-400 text-xs uppercase tracking-wide">
              <th className="w-10 px-3 py-2 text-left"> </th>
              <th className="w-10 px-3 py-2 text-left">Use</th>
              <th className="px-3 py-2 text-left">Label</th>
              <th className="px-3 py-2 text-left">Instruction</th>
              <th className="w-24 px-3 py-2 text-left">Status</th>
              <th className="w-20 px-3 py-2 text-left">Thumb</th>
            </tr>
          </thead>
          <tbody>
            {plan.map((shot) => {
              const shotStatus = status?.shots?.[shot.id];
              const thumbUrl = shotStatus ? assetUrl(status?.studio_project_id, shotStatus.asset_id) : null;
              return (
                <tr key={shot.id} className="border-t border-gray-800 align-top">
                  <td className="px-3 py-2">
                    <input
                      type="checkbox"
                      checked={selected.has(shot.id)}
                      onChange={() => toggleSelect(shot.id)}
                    />
                  </td>
                  <td className="px-3 py-2">
                    <input
                      type="checkbox"
                      checked={shot.enabled}
                      onChange={(e) => toggleEnabled(shot.id, e.target.checked)}
                    />
                  </td>
                  <td className="px-3 py-2 font-medium text-gray-200 whitespace-nowrap">{shot.label}</td>
                  <td className="px-3 py-2 min-w-[240px]">
                    <textarea
                      value={shot.instruction}
                      onChange={(e) => updateShot(shot.id, { instruction: e.target.value })}
                      onBlur={() => dirtyIds.has(shot.id) && commitShot(shot.id)}
                      rows={2}
                      className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-gray-100 text-xs focus:outline-none focus:border-purple-600 resize-y"
                    />
                  </td>
                  <td className="px-3 py-2">
                    {shotStatus ? (
                      <span title={shotStatus.error || ''}>
                        <StatusChip status={shotStatus.status} />
                      </span>
                    ) : (
                      <StatusChip status="pending" />
                    )}
                  </td>
                  <td className="px-3 py-2">
                    {thumbUrl ? (
                      <img
                        src={thumbUrl}
                        alt={shot.label}
                        onClick={() => setLightboxUrl(thumbUrl)}
                        className="w-14 h-14 object-cover rounded cursor-pointer border border-gray-700 hover:border-purple-500"
                      />
                    ) : (
                      <div className="w-14 h-14 rounded bg-gray-800 border border-gray-700 flex items-center justify-center text-gray-600 text-[10px]">
                        —
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
            {!plan.length && (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-gray-500 text-sm">
                  No shot plan yet — click "Reset Plan to Defaults" to generate one.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {lightboxUrl && <ImageLightbox url={lightboxUrl} onClose={() => setLightboxUrl(null)} />}

      <ProcessPanel
        characterId={character.id}
        studioProjectId={status?.studio_project_id}
        shotPlan={plan}
        costumes={status?.costumes}
        emotions={status?.emotions}
        processed={status?.processed}
        engine={engine}
        hasBase={!!status?.base?.asset_id}
        onProcessed={onStatusChanged}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// DATASETS tab
// ---------------------------------------------------------------------------

function NewDatasetForm({
  character,
  onCreated,
}: {
  character: CharacterT;
  onCreated: () => void;
}) {
  const [name, setName] = useState(`${character.name} LoRA`);
  const [target, setTarget] = useState<'kohya' | 'ai_toolkit' | 'both'>('kohya');
  const [triggerWord, setTriggerWord] = useState(character.trigger_word || '');
  const [classWord, setClassWord] = useState(character.class_word || '');
  const [repeats, setRepeats] = useState(10);
  const [qualityFamily, setQualityFamily] = useState<'illustrious' | 'noobai' | 'pony' | 'none'>('none');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (!name.trim()) {
      setError('Name is required.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.createDataset(character.id, {
        name: name.trim(),
        target,
        trigger_word: triggerWord.trim(),
        class_word: classWord.trim(),
        repeats,
        quality_family: qualityFamily,
        include: null,
      });
      onCreated();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-5 flex flex-col gap-4">
      <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">New Dataset</h3>
      <div className="grid grid-cols-2 gap-4">
        <TextField label="Name" value={name} onChange={setName} />
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-gray-400">Target</span>
          <select
            value={target}
            onChange={(e) => setTarget(e.target.value as 'kohya' | 'ai_toolkit' | 'both')}
            className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 focus:outline-none focus:border-purple-600"
          >
            <option value="kohya">kohya_ss</option>
            <option value="ai_toolkit">ai-toolkit</option>
            <option value="both">both</option>
          </select>
        </label>
        <TextField label="Trigger Word" value={triggerWord} onChange={setTriggerWord} />
        <TextField label="Class Word" value={classWord} onChange={setClassWord} />
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-gray-400">Repeats</span>
          <input
            type="number"
            min={1}
            value={repeats}
            onChange={(e) => setRepeats(Number(e.target.value))}
            className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 focus:outline-none focus:border-purple-600"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-gray-400">Quality Family</span>
          <select
            value={qualityFamily}
            onChange={(e) => setQualityFamily(e.target.value as 'illustrious' | 'noobai' | 'pony' | 'none')}
            className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 focus:outline-none focus:border-purple-600"
          >
            <option value="none">none</option>
            <option value="illustrious">illustrious</option>
            <option value="noobai">noobai</option>
            <option value="pony">pony</option>
          </select>
        </label>
      </div>
      <p className="text-xs text-gray-500">
        Caption generation uses the Ollama vision model configured in Settings.
      </p>
      <ErrorText msg={error} />
      <button
        onClick={submit}
        disabled={busy}
        className="px-4 py-2 bg-purple-700 hover:bg-purple-600 disabled:opacity-50 rounded-md text-sm font-medium flex items-center gap-2 self-start"
      >
        {busy && <Spinner size={14} />}
        Create Dataset
      </button>
    </div>
  );
}

function DatasetRow({
  dataset,
  status,
  onChanged,
}: {
  dataset: DatasetT;
  status: CharacterStatusT | null;
  onChanged: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState<DatasetT | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [lightboxUrl, setLightboxUrl] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await api.getDataset(dataset.id);
      setDetail(d);
    } catch (e: any) {
      setError(e.message);
    }
  }, [dataset.id]);

  useEffect(() => {
    if (expanded) load();
  }, [expanded, load]);

  useEffect(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    if (expanded && detail?.status === 'captioning') {
      pollRef.current = setInterval(load, 4000);
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [expanded, detail?.status, load]);

  // Map image name -> a thumb URL by matching against base/shots asset ids
  const thumbForImage = (imageName: string): string | null => {
    if (!status) return null;
    if (status.base?.asset_id && (imageName.includes('base') || imageName === 'base')) {
      return assetUrl(status.studio_project_id, status.base.asset_id);
    }
    for (const [shotId, s] of Object.entries(status.shots || {})) {
      if (imageName.includes(shotId) && s.asset_id) {
        return assetUrl(status.studio_project_id, s.asset_id);
      }
    }
    return null;
  };

  const saveCaption = async (image: string, style: 'natural' | 'tags', text: string) => {
    try {
      await api.updateCaption(dataset.id, { image, style, text });
    } catch (e: any) {
      setError(e.message);
    }
  };

  const doExport = async () => {
    setExporting(true);
    setError(null);
    try {
      await api.exportDataset(dataset.id);
      await load();
      onChanged();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setExporting(false);
    }
  };

  const doDownload = () => {
    window.open(`${BASE}/datasets/${dataset.id}/download`, '_blank');
  };

  const doDelete = async () => {
    if (!window.confirm(`Delete dataset "${dataset.name}"?`)) return;
    try {
      await api.deleteDataset(dataset.id);
      onChanged();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const captionsList = detail?.captions ? Object.entries(detail.captions) : [];
  const wantsNatural = dataset.target === 'ai_toolkit' || dataset.target === 'both';
  const wantsTags = dataset.target === 'kohya' || dataset.target === 'both';

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
      <div className="flex items-center gap-3 px-4 py-3 cursor-pointer" onClick={() => setExpanded((v) => !v)}>
        <span className="font-medium text-gray-200 flex-1 truncate">{dataset.name}</span>
        <span className="text-xs text-gray-500 uppercase">{dataset.target}</span>
        <StatusChip status={dataset.status} />
        <span className="text-gray-500 text-xs">{expanded ? '▲' : '▼'}</span>
      </div>

      {expanded && (
        <div className="border-t border-gray-800 p-4 flex flex-col gap-3">
          <ErrorText msg={error || detail?.error || null} />

          {!detail ? (
            <div className="flex items-center gap-2 text-sm text-gray-400">
              <Spinner size={14} /> Loading...
            </div>
          ) : (
            <>
              {captionsList.length > 0 ? (
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-gray-400 uppercase tracking-wide">
                        <th className="px-2 py-1 text-left w-16">Image</th>
                        {wantsTags && <th className="px-2 py-1 text-left">Tags</th>}
                        {wantsNatural && <th className="px-2 py-1 text-left">Natural</th>}
                      </tr>
                    </thead>
                    <tbody>
                      {captionsList.map(([imgName, cap]) => {
                        const thumb = thumbForImage(imgName);
                        return (
                          <tr key={imgName} className="border-t border-gray-800 align-top">
                            <td className="px-2 py-2">
                              {thumb ? (
                                <img
                                  src={thumb}
                                  alt={imgName}
                                  onClick={() => setLightboxUrl(thumb)}
                                  title="Click to enlarge"
                                  className="w-12 h-12 object-cover rounded border border-gray-700 cursor-pointer"
                                />
                              ) : (
                                <div className="w-12 h-12 rounded bg-gray-800 border border-gray-700 flex items-center justify-center text-gray-600 text-[9px]">
                                  {imgName.slice(0, 6)}
                                </div>
                              )}
                            </td>
                            {wantsTags && (
                              <td className="px-2 py-2 min-w-[200px]">
                                <textarea
                                  defaultValue={cap.tags || ''}
                                  onBlur={(e) => saveCaption(imgName, 'tags', e.target.value)}
                                  rows={2}
                                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-gray-100 resize-y"
                                />
                              </td>
                            )}
                            {wantsNatural && (
                              <td className="px-2 py-2 min-w-[200px]">
                                <textarea
                                  defaultValue={cap.natural || ''}
                                  onBlur={(e) => saveCaption(imgName, 'natural', e.target.value)}
                                  rows={2}
                                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-gray-100 resize-y"
                                />
                              </td>
                            )}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="text-sm text-gray-500">
                  {detail.status === 'captioning' ? 'Captioning in progress...' : 'No captions yet.'}
                </div>
              )}

              <div className="flex items-center gap-2 pt-2">
                <button
                  onClick={doExport}
                  disabled={exporting || detail.status === 'captioning'}
                  className="px-3 py-1.5 bg-indigo-700 hover:bg-indigo-600 disabled:opacity-50 rounded-md text-sm font-medium flex items-center gap-2"
                >
                  {exporting && <Spinner size={13} />}
                  <Upload size={13} />
                  Export
                </button>
                <button
                  onClick={doDownload}
                  disabled={!detail.zip_ready}
                  className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 disabled:opacity-50 rounded-md text-sm font-medium flex items-center gap-2 border border-gray-700"
                >
                  <Download size={13} />
                  Download ZIP
                </button>
                <button
                  onClick={doDelete}
                  className="px-3 py-1.5 text-red-400 hover:text-red-300 text-sm font-medium flex items-center gap-2 ml-auto"
                >
                  <Trash2 size={13} />
                  Delete
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {lightboxUrl && <ImageLightbox url={lightboxUrl} onClose={() => setLightboxUrl(null)} />}
    </div>
  );
}

function DatasetsTab({ character, status }: { character: CharacterT; status: CharacterStatusT | null }) {
  const [datasets, setDatasets] = useState<DatasetT[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showNew, setShowNew] = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await api.listDatasets(character.id);
      setDatasets(d);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [character.id]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">Datasets</h3>
        <button
          onClick={() => setShowNew((v) => !v)}
          className="px-3 py-1.5 bg-purple-700 hover:bg-purple-600 rounded-md text-sm font-medium flex items-center gap-2"
        >
          <Plus size={14} />
          New Dataset
        </button>
      </div>

      {showNew && (
        <NewDatasetForm
          character={character}
          onCreated={() => {
            setShowNew(false);
            load();
          }}
        />
      )}

      <ErrorText msg={error} />

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-gray-400">
          <Spinner size={14} /> Loading datasets...
        </div>
      ) : datasets.length === 0 ? (
        <div className="text-sm text-gray-500 bg-gray-900 border border-gray-800 rounded-lg p-6 text-center">
          No datasets yet. Create one to start building a LoRA training set.
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {datasets.map((d) => (
            <DatasetRow key={d.id} dataset={d} status={status} onChanged={load} />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Character detail view
// ---------------------------------------------------------------------------

type DetailTab = 'sheet' | 'renders' | 'poses' | 'costumes' | 'emotions' | 'datasets';

function CharacterDetail({
  characterId,
  stories,
  catalogs,
  onBack,
  onCharacterChanged,
}: {
  characterId: string;
  stories: StoryT[];
  catalogs: CatalogsT | null;
  onBack: () => void;
  onCharacterChanged: () => void;
}) {
  const [character, setCharacter] = useState<CharacterT | null>(null);
  const [status, setStatus] = useState<CharacterStatusT | null>(null);
  const [tab, setTab] = useState<DetailTab>('sheet');
  const [error, setError] = useState<string | null>(null);
  const [showPush, setShowPush] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [engine, setEngine] = useState<EngineT>('auto');
  const [showGenerateAll, setShowGenerateAll] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadCharacter = useCallback(async () => {
    try {
      const c = await api.getCharacter(characterId);
      setCharacter(c);
    } catch (e: any) {
      setError(e.message);
    }
  }, [characterId]);

  const loadStatus = useCallback(async () => {
    try {
      const s = await api.getStatus(characterId);
      setStatus(s);
    } catch (e: any) {
      setError(e.message);
    }
  }, [characterId]);

  useEffect(() => {
    loadCharacter();
    loadStatus();
  }, [loadCharacter, loadStatus]);

  // Poll status while base is rendering, any shot/pose/costume/emotion is
  // pending/running, or a generate-all run is in progress.
  useEffect(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    const baseRendering = status?.base?.status === 'pending' || status?.base?.status === 'running';
    const isActive = (s: { status?: string } | undefined) => s?.status === 'pending' || s?.status === 'running';
    const anyShotActive = Object.values(status?.shots || {}).some(isActive);
    const anyPoseActive = Object.values(status?.pose_sets || {}).some(isActive);
    const anyCostumeActive = Object.values(status?.costumes || {}).some((c) =>
      Object.values(c.sprites || {}).some(isActive)
    );
    const anyEmotionActive = Object.values(status?.emotions || {}).some(isActive);
    // (audit FE-H2) cutout/upscale jobs from /process live under `processed`
    const anyProcessActive = Object.values(status?.processed || {}).some(
      (e: any) => isActive(e?.cutout) || isActive(e?.upscale)
    );
    const generateAllRunning = status?.generate_all?.status === 'running';
    if (
      baseRendering ||
      anyShotActive ||
      anyPoseActive ||
      anyCostumeActive ||
      anyEmotionActive ||
      anyProcessActive ||
      generateAllRunning
    ) {
      pollRef.current = setInterval(loadStatus, baseRendering ? 4000 : 5000);
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, loadStatus]);

  if (!character) {
    return (
      <div className="flex items-center gap-2 text-gray-400 text-sm p-6">
        <Spinner size={16} /> Loading character...
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <button onClick={onBack} className="flex items-center gap-1 text-sm text-gray-400 hover:text-gray-200">
          <ChevronLeft size={16} /> Back to characters
        </button>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowGenerateAll(true)}
            className="px-3 py-1.5 bg-purple-700 hover:bg-purple-600 rounded-md text-sm font-medium flex items-center gap-2"
          >
            <Sparkles size={14} />
            Generate All
          </button>
          <button
            onClick={() => setShowPush(true)}
            className="px-3 py-1.5 bg-indigo-700 hover:bg-indigo-600 rounded-md text-sm font-medium flex items-center gap-2"
          >
            <Send size={14} />
            Push to Project
          </button>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-bold">{character.name}</h1>
        <span
          className={`text-xs uppercase tracking-wide px-2 py-0.5 rounded ${
            character.kind === 'item' ? 'bg-amber-900/50 text-amber-300' : 'bg-indigo-900/50 text-indigo-300'
          }`}
        >
          {character.kind}
        </span>
      </div>

      <div className="flex flex-wrap items-start gap-4 bg-gray-900 border border-gray-800 rounded-lg p-3">
        <EngineSelector engine={engine} onChange={setEngine} />
        <div className="flex-1 min-w-[240px]">
          <PreflightBadges characterId={characterId} engine={engine} />
        </div>
      </div>

      <ErrorText msg={error} />

      <div className="flex gap-1 border-b border-gray-800">
        {(
          [
            ['sheet', 'Sheet'],
            ['renders', 'Renders'],
            ['poses', 'Poses'],
            ['costumes', 'Costumes'],
            ['emotions', 'Emotions'],
            ['datasets', 'Datasets'],
          ] as [DetailTab, string][]
        ).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === key
                ? 'border-purple-500 text-purple-300'
                : 'border-transparent text-gray-500 hover:text-gray-300'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="pt-2">
        {tab === 'sheet' && (
          <SheetTab
            character={character}
            stories={stories}
            catalogs={catalogs}
            status={status}
            onSaved={(c) => {
              setCharacter(c);
              onCharacterChanged();
            }}
            onStatusRefresh={loadStatus}
          />
        )}
        {tab === 'renders' && (
          <RendersTab character={character} status={status} engine={engine} onStatusChanged={loadStatus} />
        )}
        {tab === 'poses' && (
          <PoseStudioTab
            characterId={character.id}
            studioProjectId={status?.studio_project_id}
            poseSets={status?.pose_sets}
            engine={engine}
            hasBase={!!status?.base?.asset_id}
            onGenerated={loadStatus}
          />
        )}
        {tab === 'costumes' && (
          <CostumesTab
            characterId={character.id}
            studioProjectId={status?.studio_project_id}
            costumes={status?.costumes}
            engine={engine}
            hasBase={!!status?.base?.asset_id}
            onChanged={loadStatus}
          />
        )}
        {tab === 'emotions' && (
          <EmotionsTab
            characterId={character.id}
            studioProjectId={status?.studio_project_id}
            emotions={status?.emotions}
            costumes={status?.costumes}
            engine={engine}
            hasBase={!!status?.base?.asset_id}
            onGenerated={loadStatus}
          />
        )}
        {tab === 'datasets' && <DatasetsTab character={character} status={status} />}
      </div>

      {showPush && (
        <PushToProjectModal
          character={character}
          onClose={() => setShowPush(false)}
          onDone={(msg) => {
            setShowPush(false);
            setToast(msg);
          }}
        />
      )}

      {showGenerateAll && (
        <GenerateAllModal
          characterId={character.id}
          engine={engine}
          costumes={status?.costumes}
          generateAll={status?.generate_all}
          onClose={() => setShowGenerateAll(false)}
          onStarted={loadStatus}
          onPoll={loadStatus}
        />
      )}

      {toast && <Toast message={toast} onClose={() => setToast(null)} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function CharacterStudioPage() {
  const navigate = useNavigate();
  const [stories, setStories] = useState<StoryT[]>([]);
  // the list itself is no longer read here — <UnifiedCharacterGrid /> fetches
  // its own — but the loads below still run (they surface load errors), so the
  // setter stays.
  const [, setCharacters] = useState<CharacterT[]>([]);
  const [catalogs, setCatalogs] = useState<CatalogsT | null>(null);
  const [selectedStoryId, setSelectedStoryId] = useState<string | null>(null);
  const [selectedCharacterId, setSelectedCharacterId] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [showModePicker, setShowModePicker] = useState(false);
  const [, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // v1.276.40 — 🎯 Klein 3.0 is the MAIN mode and was not in this picker at all.
  // "+ New Character" offered the two VNCCS lanes and a line of small print
  // saying Klein 3.0 characters get made somewhere else, which is a workaround
  // wearing a label, not a choice. Klein 3.0 is name-first (POST creates an
  // empty character, references and views come after), so the picker can make
  // it here and hand you straight to the panel with it selected.
  const [k3Name, setK3Name] = useState('');
  const [k3Busy, setK3Busy] = useState(false);
  const [k3Err, setK3Err] = useState('');
  const [showAutogen, setShowAutogen] = useState(false);   // v1.276.42

  /** Create a Klein 3.0 character and open it. A blank name just opens the
   *  panel — the same name box lives there, so an empty field is a navigation,
   *  not an error. */
  const startKlein3 = async () => {
    const name = k3Name.trim();
    if (!name) {
      setShowModePicker(false);
      navigate('/studio/vnccs-klein?tab=klein3');
      return;
    }
    setK3Busy(true);
    setK3Err('');
    try {
      const r = await fetch('/api/klein3/characters', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      const j = await r.json().catch(() => ({}));
      // a duplicate name 409s with a readable detail — show it rather than
      // navigating to a character this did not create.
      if (!r.ok) throw new Error(j?.detail || `HTTP ${r.status}`);
      // the panel preselects whatever is under this key (Klein3Panel reads and
      // clears it on mount) — the same mechanism the grid's jump buttons use.
      try {
        window.localStorage.setItem(FOCUS_KEY, j?.slug || name);
        window.localStorage.setItem('rbmn_current_char', j?.slug || name);
      } catch { /* non-fatal */ }
      setShowModePicker(false);
      setK3Name('');
      navigate('/studio/vnccs-klein?tab=klein3');
    } catch (e) {
      setK3Err((e as Error).message);
    } finally {
      setK3Busy(false);
    }
  };

  const loadStories = useCallback(async () => {
    try {
      const s = await api.listStories();
      setStories(s);
    } catch (e: any) {
      setError(e.message);
    }
  }, []);

  const loadCharacters = useCallback(async () => {
    try {
      const c = await api.listCharacters(selectedStoryId);
      setCharacters(c);
    } catch (e: any) {
      setError(e.message);
    }
  }, [selectedStoryId]);

  useEffect(() => {
    setLoading(true);
    Promise.all([api.listStories(), api.listCharacters(selectedStoryId), api.getCatalogs().catch(() => null)])
      .then(([s, c, cat]) => {
        setStories(s);
        setCharacters(c);
        setCatalogs(cat);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedStoryId]);

  // PARKED 2026-08-11 — fed the `storyName` prop of the old CharacterCard
  // (commented out above); unreferenced since <UnifiedCharacterGrid /> landed.
  // const storyNameFor = (id: string | null | undefined): string | null => {
  //   if (!id) return null;
  //   return stories.find((s) => s.id === id)?.name || null;
  // };

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-8">
      <div className="max-w-7xl mx-auto flex flex-col gap-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate('/')}
              className="p-2 rounded-md hover:bg-gray-800 text-gray-400 hover:text-gray-200"
              title="Back"
            >
              <ChevronLeft size={20} />
            </button>
            <h1 className="text-2xl font-bold flex items-center gap-2">
              <span role="img" aria-label="masks">
                🎭
              </span>
              Character Studio
            </h1>
          </div>
        </div>

        <ErrorText msg={error} />

        {selectedCharacterId ? (
          <CharacterDetail
            characterId={selectedCharacterId}
            stories={stories}
            catalogs={catalogs}
            onBack={() => {
              setSelectedCharacterId(null);
              loadCharacters();
              loadStories();
            }}
            onCharacterChanged={() => {
              loadCharacters();
              loadStories();
            }}
          />
        ) : (
          <div className="flex gap-6 items-start">
            <StoriesSidebar
              stories={stories}
              selectedStoryId={selectedStoryId}
              onSelect={setSelectedStoryId}
              onStoriesChanged={() => {
                loadStories();
                loadCharacters();
              }}
            />

            <div className="flex-1">
              {/* v1.276.0 — the unified grid: every character from every mode,
                  in the 🏠 Studio Hub's card language. The old grid read only
                  the studio_characters DB table, so anything made in Klein 3.0
                  was invisible here. */}
              <UnifiedCharacterGrid onOpenDbCharacter={(id) => setSelectedCharacterId(id)} />
              {/* v1.276.42 — the batch board sits with the characters it is
                  making, not on another page. It renders nothing when there
                  is nothing queued. */}
              <div className="mt-2"><AutogenBoard /></div>
              <div className="flex items-center gap-2 mt-1">
                <button
                  onClick={() => setShowModePicker(true)}
                  className="px-3 py-1.5 rounded-md bg-purple-700 hover:bg-purple-600 text-sm font-medium"
                >+ New Character</button>
                <span className="text-xs text-gray-500">
                  🎯 Klein 3.0 is the default — start with a name, then add a reference photo.
                </span>
              </div>
            </div>
          </div>
        )}
      </div>

      {showModePicker && (
        <div
          className="fixed inset-0 bg-black/70 z-50 flex items-center justify-center"
          onClick={() => setShowModePicker(false)}
        >
          <div
            className="bg-gray-900 border border-gray-700 rounded-xl p-6 w-[560px] max-w-[92vw]"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="text-lg font-semibold mb-1">New Character</h2>
            <p className="text-sm text-gray-400 mb-4">Pick the creation mode.</p>

            {/* 🎯 Klein 3.0 first and full width — it is the mode this app is
                built around now. Name-first: this creates the character and
                opens it, and the reference photo, views, outfits and LoRA all
                follow inside the panel. */}
            <div className="border-2 border-purple-600 rounded-lg p-4 mb-3 bg-purple-900/10">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-2xl">🎯</span>
                <span className="font-medium">Klein 3.0</span>
                <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-purple-700/70">
                  main mode
                </span>
              </div>
              <div className="text-xs text-gray-400 mb-3">
                Reference-driven characters — upload one photo, generate the four base views,
                then outfits, poses and LoRA datasets. No 3D, no worker sprites.
              </div>
              <div className="flex items-center gap-2">
                <input
                  autoFocus
                  value={k3Name}
                  onChange={(e) => { setK3Name(e.target.value); setK3Err(''); }}
                  onKeyDown={(e) => { if (e.key === 'Enter' && !k3Busy) void startKlein3(); }}
                  placeholder="character name"
                  className="flex-1 px-2 py-1.5 rounded bg-gray-800 border border-gray-700 text-sm outline-none focus:border-purple-500"
                />
                <button
                  disabled={k3Busy}
                  onClick={() => void startKlein3()}
                  className="px-3 py-1.5 rounded-md bg-purple-700 hover:bg-purple-600 disabled:opacity-50 text-sm font-medium whitespace-nowrap"
                >{k3Busy ? 'Creating…' : k3Name.trim() ? 'Create & open' : 'Open Klein 3.0'}</button>
              </div>
              {k3Err && <div className="text-xs text-red-400 mt-2">{k3Err}</div>}
            </div>

            {/* ⚡ Autogen — the same Klein 3.0 character, built for you.
                Sits directly under the manual option because it is the same
                destination by a different road: you describe it (or hand it
                photos) and it runs the chain as far as you tick. */}
            <div
              onClick={() => { setShowModePicker(false); setShowAutogen(true); }}
              className="border border-blue-600 rounded-lg p-4 mb-3 bg-blue-900/10 cursor-pointer hover:border-blue-400 transition-all"
            >
              <div className="flex items-center gap-2 mb-1">
                <span className="text-2xl">⚡</span>
                <span className="font-medium">Autogen character</span>
                <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-blue-700/70">
                  hands off
                </span>
              </div>
              <div className="text-xs text-gray-400">
                Reference photos or just a description → base character, the four views,
                clothing, character sheet, LoRA dataset and a trained LoRA. Tick how far
                to go, see the cost first, and queue a whole batch of characters if you want.
              </div>
            </div>

            <div className="text-xs text-gray-500 mb-2">Or the VNCCS lanes:</div>
            <div className="grid grid-cols-2 gap-3">
              <div
                onClick={() => { setShowModePicker(false); navigate('/studio/vnccs'); }}
                className="border border-gray-700 rounded-lg p-4 cursor-pointer hover:border-purple-500 hover:bg-gray-800/60 transition-all"
              >
                <div className="text-2xl mb-2">✨</div>
                <div className="font-medium mb-1">VNCCS Native</div>
                <div className="text-xs text-gray-400">
                  The full staged flow on the VNCCS meganodes — New or Clone, base versions,
                  costumes, emotions, multi-worker poses.
                </div>
              </div>
              <div
                onClick={() => { setShowModePicker(false); navigate('/studio/vnccs-klein'); }}
                className="border border-gray-700 rounded-lg p-4 cursor-pointer hover:border-purple-500 hover:bg-gray-800/60 transition-all"
              >
                <div className="text-2xl mb-2">🧪</div>
                <div className="font-medium mb-1">VNCCS Klein Hybrid</div>
                <div className="text-xs text-gray-400">
                  Same interface, separate mode — Klein-powered steps will be grafted into this
                  process. Currently identical to Native.
                </div>
              </div>
            </div>
            <button
              onClick={() => { setShowModePicker(false); setShowCreate(true); }}
              className="mt-4 text-xs text-gray-500 hover:text-gray-300 underline"
            >
              use the legacy engine-based form instead
            </button>
          </div>
        </div>
      )}

      {showAutogen && (
        <AutogenModal onClose={() => setShowAutogen(false)}
                      onQueued={() => loadCharacters()} />
      )}

      {showCreate && (
        <CreateCharacterForm
          stories={stories}
          defaultStoryId={selectedStoryId}
          onCancel={() => setShowCreate(false)}
          onCreated={(c) => {
            setShowCreate(false);
            loadCharacters();
            loadStories();
            setSelectedCharacterId(c.id);
          }}
        />
      )}
    </div>
  );
}
