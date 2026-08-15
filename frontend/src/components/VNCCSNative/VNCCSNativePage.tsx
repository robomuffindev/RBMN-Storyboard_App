/**
 * VNCCS Native mode — Character Studio mode that drives the real VNCCS meganode
 * graphs on a pinned host and catalogs the results in our system.
 *
 * Staged flow (mirrors the VNCCS panel):
 *   Create   — form (+ Character Wizard) → "Generate Character" renders ONE
 *              default-pose preview via /vnccs/preview_generate (fast) →
 *              "Save character" persists the form to our library → pick poses
 *              (default set + Pose Library) → "Generate Poses" runs the full
 *              Step-1 pipeline for exactly those poses.
 *   Cloner   — reference upload + LLM analyze → same pose selection.
 *   Clothes  — pick character (pose-sprite preview switcher) → costume slots
 *              (+ Clothes Wizard) → pose selection → generate.
 *   Emotions — pick character + costume sets (incl. base) + expressions.
 * Upscaler (SeedVR/GAN/Off + resolution) and pose target size are exposed on
 * Create / Cloner / Clothes via generator_overrides.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import * as api from './vnccsNativeApi';
import VaritestPanel, { type VtCtxT } from './VaritestPanel';
import ImageWorkshopLightbox from '../ImageWorkshop/ImageWorkshopLightbox';
import ImageLightbox from '../shared/ImageLightbox';
import type {
  ContextListsT, HostInfoT, ResultImageT, VNCCSCharacterInfoT, VNCCSStepT,
} from './vnccsNativeApi';
import Klein2Panel from './Klein2Panel';
import Klein3Panel from './Klein3Panel';
import LoraPanel from './LoraPanel';
import CharacterSheetPanel from './CharacterSheetPanel';
import Text2ImagePanel from './Text2ImagePanel';
import StudioHubPanel from './StudioHubPanel';
import VideoLabPanel from './VideoLabPanel';


type Phase = 'idle' | 'submitting' | 'polling' | 'ingesting' | 'done' | 'error';
type Tab = 'studio' | 'text2image' | 'video' | 'create' | 'clothes' | 'emotions' | 'cloner' | 'poselib' | 'lora' | 'charsheet';

const SEXES = ['female', 'male'];
const BACKGROUNDS = ['Green', 'Blue', 'White', 'Alpha'];
const SLOTS: Array<keyof CostumeInfo> = ['top', 'bottom', 'head', 'face', 'shoes'];

interface CostumeInfo { top: string; bottom: string; head: string; face: string; shoes: string; [k: string]: string; }
interface EmotionOpt { value: string; label: string; group: string; }
interface ExtraPose { id: string; name: string; pose: Record<string, unknown>; thumbUrl?: string; }
interface EnhanceRec { refs: Record<string, api.UploadRefT>; view: 'original' | 'upscaled'; on: boolean; method: string; model: string; sharpen: string; maxSide: number; }
interface UpscalerState { mode: 'seedvr' | 'gan' | 'off'; resolution: string; targetSize: string; }

interface ChunkState {
  host: string; prompt_id: string; tap_map: Record<string, string>;
  label: string; status: 'running' | 'ingesting' | 'done' | 'error'; images: ResultImageT[];
  pose_names?: string[] | null;
  note?: string;
  startedAt?: number;    // when the chunk was queued/submitted (drives the live ⏱)
  finishedAt?: number;   // stamped by patchChunk on done/error (freezes the ⏱)
}

// ---- Resumable runs ------------------------------------------------------
// A generation is tracked ONLY in React state, so a browser refresh / route
// change would lose the live status even though the work keeps running (queue
// jobs are durable + ingest server-side; direct chunks finish on the workers).
// We persist a compact descriptor of the in-flight run to localStorage so the
// page can restore the status view and resume polling when it re-mounts.
const ACTIVE_RUN_KEY = 'rbmn_vnccs_active_runs';   // v1.199.22: MAP {charName: desc}
type ActiveRunDesc =
  | { kind: 'queue'; runId: string; jobIds: string[]; step: VNCCSStepT; charName: string; started: number }
  | { kind: 'direct'; step: VNCCSStepT; charName: string; engine?: string;
      ingestExtra?: { costume?: string; emotions?: string[]; costumes?: string[] } | null;
      runSeed?: number | null; poseNamesAll?: string[] | null;
      poseSet?: Array<Record<string, unknown>> | null;
      chunks: Array<{ host: string; prompt_id: string; tap_map: Record<string, string>;
                      label: string; pose_names?: string[] | null }>;
      ingested: number[]; started: number };

function _loadRunsMap(): Record<string, ActiveRunDesc> {
  try {
    const s = window.localStorage.getItem(ACTIVE_RUN_KEY);
    if (!s) return {};
    const m = JSON.parse(s) as Record<string, ActiveRunDesc & { started?: number }>;
    const now = Date.now();
    const out: Record<string, ActiveRunDesc> = {};
    for (const [k, v] of Object.entries(m || {})) {
      // drop stale descriptors (>3h) so a long-dead run never re-arms the UI
      if (v && typeof v === 'object' && (now - (v.started || 0)) <= 3 * 60 * 60 * 1000) out[k] = v as ActiveRunDesc;
    }
    return out;
  } catch { return {}; }
}
function _saveRunsMap(m: Record<string, ActiveRunDesc>): void {
  try { window.localStorage.setItem(ACTIVE_RUN_KEY, JSON.stringify(m)); } catch { /* storage disabled */ }
}
// runs are stored per CHARACTER so queueing across characters never clobbers, and
// returning to a character re-shows its status (survives tab switch + browser close).
function saveActiveRun(d: ActiveRunDesc): void {
  const m = _loadRunsMap(); if (d.charName) { m[d.charName] = d; _saveRunsMap(m); }
}
function loadActiveRunFor(charName: string): ActiveRunDesc | null {
  const m = _loadRunsMap(); return (charName && m[charName]) || null;
}
function clearActiveRun(charName?: string): void {
  if (!charName) return;
  const m = _loadRunsMap(); delete m[charName]; _saveRunsMap(m);
}

const shortHost = (h: string) => h.replace(/^https?:\/\//, '').replace(/\/$/, '');

// Final-output node ids for a chunk. Verified against the generator source:
// the BG-REMOVED finals come out of the *sprites* outputs (clothes/emotions/
// cloner) and, for the creator, the *sheet* output (final_images after
// bg_remove) — 'upscaled' is the PRE-BG upscale pass and 'faces' duplicates
// the creator's sheet.
function finalNodeIds(tapMap: Record<string, string>): Set<string> | null {
  const entries = Object.entries(tapMap || {});
  const allSpr = entries.filter(([l]) => l.includes('sprites'));
  // cloner taps original_sprites AND naked_sprites — displaying both doubles
  // every pose. Prefer the Original set; the naked set is the clothes-step
  // mannequin base and stays behind the "show all pipeline outputs" toggle.
  const orig = allSpr.filter(([l]) => l.includes('original_sprites'));
  const spr = (orig.length ? orig : allSpr).map(([, id]) => String(id));
  if (spr.length) return new Set(spr);
  const sheet = entries.filter(([l]) => l === 'sheet' || l.endsWith('/sheet')).map(([, id]) => String(id));
  if (sheet.length) return new Set(sheet);
  return null;
}

// Library (catalog) labels are step-namespaced ("creator/sheet"); the finals —
// the BG-removed sprites the node UI shows — are 'sheet' (creator) and
// '*sprites' (cloner/clothes/emotions). Everything else is pipeline plumbing.
function isFinalLabel(label: string): boolean {
  const last = label.split('/').pop() || '';
  return last === 'sheet' || last.endsWith('sprites');
}
function friendlyLabel(label: string, costume?: string | null): string {
  if (label === 'creator/sprites' || label === 'cloner/sprites') return 'Base poses (Klein)';
  if (label === 'creator/sheet' || label === 'cloner/original_sprites') return 'Base poses';
  if (label === 'cloner/naked_sprites') return 'Base poses (no clothes)';
  if (label.startsWith('clothes/')) return costume ? `Costume poses — ${costume}` : 'Costume poses';
  if (label.startsWith('emotions/')) return costume ? `Emotions — ${costume}` : 'Emotions';
  return label;
}

const box: React.CSSProperties = { background: '#161a22', border: '1px solid #2a2f3a', borderRadius: 10, padding: 16, minWidth: 0 };
const label: React.CSSProperties = { fontSize: 13, fontWeight: 600, color: '#d7dee8', marginBottom: 6, display: 'block', lineHeight: 1.45 };
const pbtn: React.CSSProperties = { padding: '6px 10px', fontSize: 12, borderRadius: 6, border: '1px solid #2a2f3a', background: '#1c2230', color: '#dbe2ea', cursor: 'pointer' };

// v1.179: plain-English "how to use this" guides opened by the ? button beside
// each Generate button. Each: what it does, the settings that matter, GOTCHAS
// (especially two-dials-together traps), and a best-practice starting point.
interface HelpTopic {
  title: string; intro: string;
  settings: { name: string; what: string }[];
  gotchas: string[]; best: string[];
}
const HELP_TOPICS: Record<string, HelpTopic> = {
  base: {
    title: '✨ Generating the base character',
    intro: 'The base is your character’s neutral, body-only reference — the identity every pose, costume and 3D body is built from. Pick the view mode (Single / 4-view / 🧊 Mesh-ready) right above the Generate button FIRST, then generate. Each mode saves as its own base version; switch between them with the tabs above the preview.',
    settings: [
      { name: 'View mode (by the button)', what: 'Single = one fast front view. 4-view = front/right/left/back (needed for a good 3D body — a single view loses depth). 🧊 Mesh-ready = the 4-view set in a locked A-pose on plain gray, the best input for “Generate 3D body.”' },
      { name: 'Body adherence', what: 'How hard the body locks to your reference photos vs. a generic build. Higher = truer body, less drift. 1.60 is the sweet spot.' },
      { name: 'Strip release', what: 'Where the reference stops driving so the final steps can strip leftover clothing/jewelry. LOWER strips harder; “Hold” keeps the reference (and its clothes) the whole way.' },
      { name: 'Reference masking', what: 'What the body ref carries: Person (drop clothes) keeps face+build but not the outfit; Person+clothes keeps the whole clothed look; Full references everything.' },
      { name: 'Match all views + Consistency LoRA', what: 'The two levers that make a SET’s views match: shared seed pins skin tone/lighting across views; the Consistency LoRA holds a matching look. Turn both on for sets.' },
      { name: 'Cleanup / Article cleanup (SAM3)', what: 'Remove leftover shoes/jewelry — Cleanup is a general pass, SAM3 targets named items (necklace, watch…).' },
    ],
    gotchas: [
      'Clothing bleed onto a “stripped” base usually means Strip release is too high (Hold/0.90) OR Reference masking is Person+clothes / Full. Fix: Strip release 0.75–0.80 and masking = Person (drop clothes).',
      'Body adherence too HIGH together with Strip release on HOLD over-references the photo — you get the clothing AND a stiff, photo-locked body. Keep body adherence ≤ 1.6 when holding the reference.',
      'Consistency LoRA strength too high WITH shared seed can flatten the set — all four views look almost identical and lose the angle. Start the LoRA around 0.7.',
      '“Use current preview image settings” loads an OLDER image’s dials and auto-saves them as your defaults — it can silently bring back that image’s bleed/build. Re-check Strip release + Body adherence after using it.',
      'Mesh-ready forces gray background + A-pose — it’s the 3D input, not your hero image. Don’t use it as the final look.',
    ],
    best: [
      'Start here: Body adherence 1.60, Strip release 0.85, Reference masking = Person (drop clothes).',
      'For a set that matches: Match all views ON, Consistency LoRA ON at ~0.7.',
      'Generate a 4-view or 🧊 Mesh-ready set BEFORE “Generate 3D body” for the cleanest mesh.',
    ],
  },
  clone: {
    title: '✨ Cloning from reference photos',
    intro: 'Builds the base FROM your uploaded photos through the full identity chain (multi-ref + face crop + PuLID + body match), so the character IS the person in your refs. Then pick poses and hit Clone character.',
    settings: [
      { name: 'Reference roles (face / body / full)', what: 'Tag which photo drives the FACE and which drive the BODY/build. A clean face ref + one or two clear full-body refs works best.' },
      { name: 'Body adherence', what: 'How strongly the build locks to your body refs. 1.60 default.' },
      { name: 'Strip release + Reference masking', what: 'Same as base: control whether the outfit in your photos comes through. For a neutral base use masking = Person and release ~0.85.' },
      { name: 'Output style', what: 'Photoreal / Semi-real use PuLID for face identity; illustrated styles skip PuLID automatically.' },
    ],
    gotchas: [
      'Reference masking = Person+clothes keeps the photo outfit — it WILL bleed onto a base you meant to strip. Use Person (drop clothes) for a neutral base.',
      'Mixing refs of different outfits/lighting confuses the body match. Use consistent full-body refs for build, plus one sharp face ref.',
      'PuLID only engages for photoreal/semi-real. If you pick an illustrated style, don’t expect PuLID face-lock — lean on the face crop + refs instead.',
    ],
    best: [
      'One clean, front-facing face ref + 1–2 full-body refs. Reference masking = Person, Strip release 0.85.',
      'Review the single preview likeness first; only then generate the full set.',
    ],
  },
  pose: {
    title: '🕺 Generating a pose / clothed set',
    intro: 'Renders your selected poses from the APPROVED active base. Choose a 2D reference (mannequin or skeleton) or the 🧊 3D clay body, which applies each pose to the character’s own rigged mesh so the reference has the REAL body shape.',
    settings: [
      { name: '🧊 Use 3D body', what: 'Poses are rendered on the character’s rigged 3D mesh (clay) instead of the generic mannequin — ends body drift. Turning it on hides the mannequin/skeleton options because they no longer apply.' },
      { name: 'Pose LoRA + strength', what: 'Which pose-control LoRA drives the render. VNCCS PoseStudio is the default; strength 0.6–0.8 keeps the pose without stamping style.' },
      { name: 'Pose ref release', what: 'When to stop referencing the pose capture so its flat texture can’t stamp the skin. ~0.85 = natural skin, pose still locks early; Hold = strictest pose, texture can leak.' },
      { name: 'Steps / Cleanup', what: 'More steps = cleaner skin-on-skin overlaps and hands. Cleanup strips leftover shoes/jewelry.' },
      { name: 'Consistent skin', what: 'Shares one seed across the set so skin tone + lighting hold pose-to-pose (off = more variety).' },
    ],
    gotchas: [
      'VNCCS PoseStudio LoRA at 1.0 draws black lines where skin touches skin (hands are the worst). Keep it 0.6–0.8.',
      'Pose ref release on HOLD + a mannequin reference lets the CGI texture stamp the skin. Release ~0.85 for natural skin.',
      'With 🧊 3D body ON, the mannequin / DWPose-skeleton and RefControl options are hidden and skipped on purpose — don’t go hunting for them.',
      'Low steps + tight/overlapping poses = smeared hands and dark contact lines. Raise steps to 12–16 for those.',
    ],
    best: [
      '🧊 3D clay: VNCCS PoseStudio ~0.7, pose-ref release ~0.85, steps 8–12.',
      'Turn on Consistent skin for a matched set; raise steps if hands/overlaps smear.',
      'Approve the base first — every pose is built from the ACTIVE base version.',
    ],
  },
};

// Collapsible settings group (v1.161 three-column layout)
const blobToDataURL = (b: Blob) => new Promise<string>((res, rej) => {
  const fr = new FileReader();
  fr.onload = () => res(String(fr.result));
  fr.onerror = () => rej(fr.error);
  fr.readAsDataURL(b);
});
// v1.164.3: little "what does each end of this dial do" captions rendered
// under a segmented row -- makes it obvious which way to experiment.
const extremes = (lo: string, hi: string) => (
  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 14, fontSize: 11.5,
                color: '#8fa2c0', marginTop: 4, lineHeight: 1.35 }}>
    <span style={{ maxWidth: '48%' }}>◀ {lo}</span>
    <span style={{ maxWidth: '48%', textAlign: 'right' }}>{hi} ▶</span>
  </div>
);
const fmtMMSS = (sec: number) => {
  const t = Math.max(0, Math.round(sec));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`;
};
// Eye-catching single-operation banner (base preview / dress preview / try-on):
// spinner + what's running + where + a live mm:ss clock.
function LiveBanner({ icon, text, secs }: { icon: string; text: string; secs: number }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 12px', marginBottom: 10,
                  borderRadius: 8, border: '1px solid #3b82f6', background: 'linear-gradient(160deg, #0d1526, #0e1116)',
                  boxShadow: '0 0 12px rgba(59,130,246,0.25)' }}>
      <style>{'@keyframes rbmnSpin { to { transform: rotate(360deg); } }'}</style>
      <span style={{ width: 14, height: 14, borderRadius: '50%', flexShrink: 0,
                     border: '2px solid rgba(59,130,246,0.25)', borderTopColor: '#3b82f6',
                     display: 'inline-block', animation: 'rbmnSpin 0.9s linear infinite' }} />
      <span style={{ fontSize: 13, fontWeight: 600, color: '#cfe0ff' }}>{icon} {text}</span>
      <div style={{ flex: 1 }} />
      <span style={{ fontSize: 13, fontFamily: 'monospace', fontWeight: 700, color: '#8ab4ff' }}>⏱ {fmtMMSS(secs)}</span>
    </div>
  );
}
function Acc({ title, defaultOpen = false, children }: {
  title: string; defaultOpen?: boolean; children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div style={{ border: '1px solid #2a2f3a', borderRadius: 8, background: '#12161d' }}>
      <div onClick={() => setOpen(!open)}
           style={{ cursor: 'pointer', padding: '10px 12px', fontSize: 14, fontWeight: 700, color: '#dbe3ee',
                    userSelect: 'none', display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ color: '#7f8896', fontSize: 10 }}>{open ? '▼' : '▶'}</span>{title}
      </div>
      {open && <div style={{ padding: '4px 12px 14px', display: 'grid', gap: 12 }}>{children}</div>}
    </div>
  );
}
const input: React.CSSProperties = {
  width: '100%', background: '#0e1116', border: '1px solid #2a2f3a', borderRadius: 6,
  color: '#e6e9ee', padding: '8px 10px', fontSize: 13,
};
const btn: React.CSSProperties = {
  background: '#3b82f6', color: '#fff', border: 'none', borderRadius: 6,
  padding: '9px 16px', fontSize: 13, cursor: 'pointer', fontWeight: 600,
};
const btnGhost: React.CSSProperties = { ...btn, background: 'transparent', border: '1px solid #2a2f3a', color: '#cbd2dc' };
const btnGreen: React.CSSProperties = { ...btn, background: '#166534' };
const tabBtn = (active: boolean): React.CSSProperties => ({
  ...btnGhost, borderColor: active ? '#3b82f6' : '#2a2f3a',
  color: active ? '#fff' : '#cbd2dc', background: active ? '#1d2740' : 'transparent',
});
const toggleBox: React.CSSProperties = {
  // section-toggle checkboxes that reveal/steer a group of options -- clearly
  // BLUE so they pop out from plain labels (v1.164.1: the old tint read as
  // grey-on-grey on most monitors)
  display: 'flex', alignItems: 'center', gap: 9, fontSize: 12.5, fontWeight: 600, color: '#e4edff',
  cursor: 'pointer', background: 'linear-gradient(160deg, rgba(37,99,235,0.28), rgba(37,99,235,0.10))',
  border: '1px solid #3b82f6', borderLeft: '3px solid #3b82f6', borderRadius: 7, padding: '8px 11px',
};
const wizBox: React.CSSProperties = {
  background: '#121826', border: '1px dashed #3b4b6b', borderRadius: 8, padding: 10, marginBottom: 10,
};
const sectionBox: React.CSSProperties = {
  background: '#10141c', border: '1px solid #232936', borderRadius: 8, padding: 12, marginTop: 12,
};

function parseCharacters(raw: unknown): string[] {
  if (Array.isArray(raw)) {
    return raw.map((c) => (typeof c === 'string' ? c : (c as { name?: string })?.name || '')).filter(Boolean);
  }
  if (raw && typeof raw === 'object') {
    const arr = (raw as { characters?: unknown }).characters;
    if (Array.isArray(arr)) return parseCharacters(arr);
  }
  return [];
}

function parseEmotions(raw: unknown): EmotionOpt[] {
  const out: EmotionOpt[] = [];
  const groups = (raw && typeof raw === 'object' && !Array.isArray(raw))
    ? (raw as Record<string, unknown>) : {};
  for (const [group, list] of Object.entries(groups)) {
    if (!Array.isArray(list)) continue;
    for (const e of list) {
      if (typeof e === 'string') { out.push({ value: e, label: e, group }); continue; }
      const o = e as { key?: string; safe_name?: string };
      const value = o.safe_name || o.key;
      if (value) out.push({ value, label: o.key || value, group });
    }
  }
  return out;
}

// A pose-library entry's `data` may be the pose itself or wrap it.
function normalizeLibraryPose(d: unknown): Record<string, unknown> | null {
  if (!d || typeof d !== 'object') return null;
  const o = d as Record<string, unknown>;
  if (o.bones) return o;
  const inner = o.pose as Record<string, unknown> | undefined;
  if (inner && inner.bones) return inner;
  return null;
}

function buildGeneratorOverrides(u: UpscalerState): Record<string, unknown> {
  const overrides: Record<string, unknown> = { upscaler: { mode: u.mode } };
  const res = parseInt(u.resolution, 10);
  if (u.mode !== 'off' && res > 0) (overrides.upscaler as Record<string, unknown>).resolution = res;
  const ts = parseInt(u.targetSize, 10);
  if (ts > 0) overrides.pose_generation = { target_size: String(ts) };
  return overrides;
}

// ---------------------------------------------------------------------------
// Small shared components
// ---------------------------------------------------------------------------
function UpscalerControls({ value, onChange }: { value: UpscalerState; onChange: (u: UpscalerState) => void }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
      <div><label style={label}>Upscaler</label>
        <select style={input} value={value.mode}
                onChange={(e) => onChange({ ...value, mode: e.target.value as UpscalerState['mode'] })}>
          <option value="off">Off (default, fastest)</option>
          <option value="gan">GAN (fast)</option>
          <option value="seedvr">SeedVR (best, heavy)</option>
        </select></div>
      <div><label style={label}>Upscale resolution</label>
        <input style={{ ...input, opacity: value.mode === 'off' ? 0.5 : 1 }} value={value.resolution}
               disabled={value.mode === 'off'} placeholder="1024"
               onChange={(e) => onChange({ ...value, resolution: e.target.value })} /></div>
      <div><label style={label}>Pose target size</label>
        <input style={input} value={value.targetSize} placeholder="1024"
               onChange={(e) => onChange({ ...value, targetSize: e.target.value })} /></div>
    </div>
  );
}

// Mannequin picker that spans ALL workers holding this character's sprites.
// With multi-worker fan-out each machine stores only its shard of the poses,
// so a single-host strip shows just a few — this merges every worker's list
// and remembers WHICH worker each pose lives on (the costume preview must run
// on that same machine to dress that sprite).
function MannequinStrip({ character, hosts, sel, onSelect, hint, costume, onOpen }: {
  character: string; hosts: string[];
  sel: { host: string; index: number } | null;
  onSelect: (s: { host: string; index: number } | null) => void; hint?: string;
  costume?: string;
  onOpen?: (urls: string[], i: number, onNav?: (i: number) => void) => void;
}) {
  const [entries, setEntries] = useState<Array<{ host: string; index: number }>>([]);
  const hostsKey = hosts.join('|');
  useEffect(() => {
    let alive = true;
    setEntries([]);
    onSelect(null);
    if (!character || !hosts.length) return () => { alive = false; };
    Promise.all(hosts.map(async (h) => {
      try {
        const m = await api.relayJson<{ count: number }>(
          `get_character_pose_preview_meta?character=${encodeURIComponent(character)}` +
          `${costume ? `&costume=${encodeURIComponent(costume)}` : ''}&_vnccs_host=${encodeURIComponent(h)}`);
        return Array.from({ length: m.count || 0 }, (_, i) => ({ host: h, index: i }));
      } catch { return [] as Array<{ host: string; index: number }>; }
    })).then((lists) => { if (alive) setEntries(lists.flat()); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [character, hostsKey, costume]);
  useEffect(() => {
    if (!sel && entries.length) onSelect(entries[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entries, sel]);
  if (!character) return null;
  if (!entries.length) {
    return <p style={{ fontSize: 12, color: '#6b7280' }}>No pose sprites on the workers yet for “{character}”.</p>;
  }
  const found = entries.findIndex((e) => !!sel && e.host === sel.host && e.index === sel.index);
  const pos = found >= 0 ? found : 0;
  const cur = entries[pos];
  const step = (d: number) => onSelect(entries[(pos + d + entries.length) % entries.length]);
  const shortHost = (h: string) => h.replace(/^https?:\/\//, '');
  const urlOf = (e: { host: string; index: number }) => api.posePreviewUrl(character, e.index, costume, e.host);
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <button style={{ ...btnGhost, padding: '4px 10px' }} onClick={() => step(-1)}>‹</button>
        <img src={urlOf(cur)} alt={`pose ${pos + 1}`}
             onClick={() => onOpen?.(entries.map(urlOf), pos, (i) => onSelect(entries[i]))}
             style={{ height: 220, borderRadius: 6, border: '1px solid #2a2f3a', background: '#0e1116',
                      cursor: onOpen ? 'zoom-in' : 'default' }} />
        <button style={{ ...btnGhost, padding: '4px 10px' }} onClick={() => step(1)}>›</button>
        <span style={{ fontSize: 12, color: '#9aa4b2' }}>
          pose {pos + 1} / {entries.length}{hosts.length > 1 ? ` · ${shortHost(cur.host)}` : ''}
        </span>
      </div>
      {hint && <p style={{ fontSize: 12, color: '#8d97a5', margin: '4px 0 0' }}>{hint}</p>}
    </div>
  );
}

// v1.199.17: catalog-sourced pose preview strip (app-side). MannequinStrip queries
// the WORKER, which is empty for app-catalog characters; this shows the sprites we
// already ingested into the catalog (engine-filtered) so the preview actually renders.
function CatalogPoseStrip({ images, hint, onOpen }: {
  images: string[]; hint?: string;
  onOpen?: (urls: string[], i: number, onNav?: (i: number) => void) => void;
}) {
  const [pos, setPos] = useState(0);
  const key = images.join('|');
  useEffect(() => { setPos(0); }, [key]);
  if (!images.length) {
    return <p style={{ fontSize: 12, color: '#6b7280' }}>No sprites cataloged for this set/engine yet — generate poses or a costume set first (or switch engine).</p>;
  }
  const p = Math.min(pos, images.length - 1);
  const step = (d: number) => setPos((p + d + images.length) % images.length);
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <button style={{ ...btnGhost, padding: '4px 10px' }} onClick={() => step(-1)}>‹</button>
        <img src={images[p]} alt={`sprite ${p + 1}`}
             onClick={() => onOpen?.(images, p, (i) => setPos(i))}
             style={{ height: 220, borderRadius: 6, border: '1px solid #2a2f3a', background: '#0e1116',
                      cursor: onOpen ? 'zoom-in' : 'default' }} />
        <button style={{ ...btnGhost, padding: '4px 10px' }} onClick={() => step(1)}>›</button>
        <span style={{ fontSize: 12, color: '#9aa4b2' }}>sprite {p + 1} / {images.length}</span>
      </div>
      {hint && <p style={{ fontSize: 12, color: '#8d97a5', margin: '4px 0 0' }}>{hint}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Edit Image (v1.158): masked Klein inpaint editor for the base / costume image.
// Brush mode paints a mask; Segments mode runs SAM3 text detection and lets you
// pick one or many found regions. Every Apply repaints ONLY the masked area and
// saves a NEW version (which becomes active) -- edits layer run after run, and
// the existing version arrows / Set-active are the revision history.
// ---------------------------------------------------------------------------
function EditImageModal({ charName, src, costumeName, onSaved, onClose }: {
  charName: string; src: string; costumeName?: string;
  onSaved: () => void | Promise<void>; onClose: () => void;
}) {
  const [curSrc, setCurSrc] = useState(src);
  const [dims, setDims] = useState<{ w: number; h: number } | null>(null);
  const brushRef = useRef<HTMLCanvasElement>(null);
  const segRef = useRef<HTMLCanvasElement>(null);
  const segMasksRef = useRef<HTMLCanvasElement[]>([]);
  const [mode, setMode] = useState<'brush' | 'segments'>('brush');
  const [brushSize, setBrushSize] = useState(48);
  const [erase, setErase] = useState(false);
  const [segPrompt, setSegPrompt] = useState('');
  const [segThresh, setSegThresh] = useState('0.3');
  const [segs, setSegs] = useState<Array<{ b64: string; sel: boolean }>>([]);
  const [segBusy, setSegBusy] = useState(false);
  const [prompt, setPrompt] = useState('');
  const [negative, setNegative] = useState('');
  const [steps, setSteps] = useState('12');
  const [guidance, setGuidance] = useState('1');
  const [grow, setGrow] = useState('6');
  const [refs, setRefs] = useState<Array<{ ref: api.UploadRefT; url: string }>>([]);
  const [refBusy, setRefBusy] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [edits, setEdits] = useState(0);
  const drawing = useRef(false);
  const last = useRef<{ x: number; y: number } | null>(null);

  useEffect(() => {
    if (!dims) return;
    for (const c of [brushRef.current, segRef.current]) {
      if (c && (c.width !== dims.w || c.height !== dims.h)) { c.width = dims.w; c.height = dims.h; }
    }
  }, [dims]);

  const toXY = (e: React.PointerEvent) => {
    const c = brushRef.current as HTMLCanvasElement;
    const r = c.getBoundingClientRect();
    return { x: (e.clientX - r.left) * (c.width / r.width), y: (e.clientY - r.top) * (c.height / r.height) };
  };
  const stroke = (a: { x: number; y: number }, b: { x: number; y: number }) => {
    const ctx = brushRef.current?.getContext('2d');
    if (!ctx) return;
    ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.lineWidth = brushSize;
    ctx.globalCompositeOperation = erase ? 'destination-out' : 'source-over';
    ctx.strokeStyle = 'rgba(255,80,80,0.85)';
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x + 0.01, b.y + 0.01); ctx.stroke();
  };

  const maskToCanvas = (b64: string, w: number, h: number): Promise<HTMLCanvasElement> => new Promise((res) => {
    const img = new Image();
    img.onload = () => {
      const c = document.createElement('canvas'); c.width = w; c.height = h;
      const ctx = c.getContext('2d') as CanvasRenderingContext2D;
      ctx.drawImage(img, 0, 0, w, h);
      const d = ctx.getImageData(0, 0, w, h);
      for (let i = 0; i < d.data.length; i += 4) { d.data[i + 3] = d.data[i]; d.data[i] = 255; d.data[i + 1] = 255; d.data[i + 2] = 255; }
      ctx.putImageData(d, 0, 0);
      res(c);
    };
    img.src = `data:image/png;base64,${b64}`;
  });

  useEffect(() => {
    const c = segRef.current; if (!c || !dims) return;
    const ctx = c.getContext('2d') as CanvasRenderingContext2D;
    ctx.clearRect(0, 0, c.width, c.height);
    ctx.globalAlpha = 0.45;
    segs.forEach((sg, i) => {
      if (!sg.sel || !segMasksRef.current[i]) return;
      const t = document.createElement('canvas'); t.width = c.width; t.height = c.height;
      const tc = t.getContext('2d') as CanvasRenderingContext2D;
      tc.drawImage(segMasksRef.current[i], 0, 0);
      tc.globalCompositeOperation = 'source-in'; tc.fillStyle = '#3b82f6'; tc.fillRect(0, 0, t.width, t.height);
      ctx.drawImage(t, 0, 0);
    });
    ctx.globalAlpha = 1;
  }, [segs, dims]);

  const doDetect = async () => {
    if (!segPrompt.trim() || segBusy || !dims) return;
    setSegBusy(true); setMsg('');
    try {
      const r = await api.baseSegment({
        character_name: charName, prompt: segPrompt.trim(),
        threshold: parseFloat(segThresh) || 0.3, costume_name: costumeName || undefined,
      });
      segMasksRef.current = await Promise.all(r.segments.map((b) => maskToCanvas(b, dims.w, dims.h)));
      setSegs(r.segments.map((b64) => ({ b64, sel: false })));
      setMsg(r.segments.length ? `Found ${r.segments.length} region(s) — click the thumbnails to select one or many.`
                               : 'Nothing detected — try different words or a lower threshold.');
    } catch (e) { setMsg(`Detect failed: ${(e as Error).message}`); }
    finally { setSegBusy(false); }
  };

  const onUploadEditRef = async (files: FileList | null) => {
    if (!files || !files.length || refs.length >= 3) return;
    setRefBusy(true);
    try {
      const url = URL.createObjectURL(files[0]);
      const up = await api.uploadReference(files[0]);
      setRefs((r) => [...r, { ref: up, url }].slice(0, 3));
    } catch (e) { setMsg(`Reference upload failed: ${(e as Error).message}`); }
    finally { setRefBusy(false); }
  };

  const composeMask = (): string | null => {
    if (!dims) return null;
    const c = document.createElement('canvas'); c.width = dims.w; c.height = dims.h;
    const ctx = c.getContext('2d') as CanvasRenderingContext2D;
    ctx.fillStyle = '#000'; ctx.fillRect(0, 0, c.width, c.height);
    segs.forEach((sg, i) => { if (sg.sel && segMasksRef.current[i]) ctx.drawImage(segMasksRef.current[i], 0, 0); });
    if (brushRef.current) {
      const t = document.createElement('canvas'); t.width = c.width; t.height = c.height;
      const tc = t.getContext('2d') as CanvasRenderingContext2D;
      tc.drawImage(brushRef.current, 0, 0);
      tc.globalCompositeOperation = 'source-in'; tc.fillStyle = '#fff'; tc.fillRect(0, 0, t.width, t.height);
      ctx.drawImage(t, 0, 0);
    }
    const d = ctx.getImageData(0, 0, c.width, c.height).data;
    let any = false;
    for (let i = 0; i < d.length; i += 40) { if (d[i] > 8) { any = true; break; } }
    return any ? c.toDataURL('image/png') : null;
  };

  const doApply = async () => {
    if (busy) return;
    const m = composeMask();
    if (!m) { setMsg('Paint a mask (Brush) or select at least one detected region (Segments) first.'); return; }
    if (!prompt.trim()) { setMsg('Describe the change — the prompt drives the repaint.'); return; }
    setBusy(true); setMsg('');
    try {
      const r = await api.baseInpaint({
        character_name: charName, mask_b64: m, prompt: prompt.trim(), negative,
        steps: parseInt(steps, 10) || 12, guidance: parseFloat(guidance) || 1,
        grow: parseInt(grow, 10) || 0,
        refs: refs.map((x) => ({ name: x.ref.name, subfolder: x.ref.subfolder, type: x.ref.type })),
        costume_name: costumeName || undefined,
      });
      setCurSrc(`data:image/png;base64,${r.image}`);
      if (dims) brushRef.current?.getContext('2d')?.clearRect(0, 0, dims.w, dims.h);
      segMasksRef.current = []; setSegs([]); setRefs([]);
      setEdits((n) => n + 1);
      setMsg('✓ Saved as a new version (now active). Keep editing — every Apply layers on this result; the version arrows are your revision history.');
      await onSaved();
    } catch (e) { setMsg(`Edit failed: ${(e as Error).message}`); }
    finally { setBusy(false); }
  };

  const tabBtn = (v: 'brush' | 'segments', txt: string) => (
    <button onClick={() => setMode(v)}
            style={{ padding: '5px 14px', fontSize: 12, borderRadius: 6, cursor: 'pointer',
                     border: `1px solid ${mode === v ? '#3b82f6' : '#2a2f3a'}`,
                     background: mode === v ? '#16233a' : '#12161d',
                     color: mode === v ? '#dbe9ff' : '#9aa4b2', fontWeight: mode === v ? 700 : 400 }}>{txt}</button>
  );

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(5,7,10,0.92)', zIndex: 60,
                  display: 'flex', alignItems: 'flex-start', justifyContent: 'center', gap: 14, padding: 18, overflow: 'auto' }}>
      <div style={{ position: 'relative', flex: '0 1 auto', maxWidth: 'min(58vw, 900px)' }}>
        <img src={curSrc} alt="edit target" onLoad={(e) => setDims({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })}
             style={{ width: '100%', display: 'block', borderRadius: 8, border: '1px solid #2a2f3a', background: '#0e1116' }} />
        <canvas ref={segRef} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }} />
        <canvas ref={brushRef}
                onPointerDown={(e) => { if (mode !== 'brush') return; e.currentTarget.setPointerCapture(e.pointerId); drawing.current = true; const p2 = toXY(e); last.current = p2; stroke(p2, p2); }}
                onPointerMove={(e) => { if (!drawing.current || mode !== 'brush') return; const p2 = toXY(e); if (last.current) stroke(last.current, p2); last.current = p2; }}
                onPointerUp={() => { drawing.current = false; last.current = null; }}
                style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', touchAction: 'none',
                         cursor: mode === 'brush' ? 'crosshair' : 'default', pointerEvents: mode === 'brush' ? 'auto' : 'none' }} />
        {busy && <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
                               background: 'rgba(5,7,10,0.55)', color: '#dbe2ea', fontSize: 14, borderRadius: 8 }}>Repainting the masked region…</div>}
      </div>
      <div style={{ width: 340, flex: '0 0 340px', background: '#12161d', border: '1px solid #2a2f3a', borderRadius: 10, padding: 12, display: 'grid', gap: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <b style={{ color: '#dbe2ea', fontSize: 14 }}>🖌 Edit {costumeName ? `costume “${costumeName}”` : 'base image'}</b>
          <span style={{ flex: 1 }} />
          <button style={{ ...btnGhost, padding: '3px 10px' }} onClick={onClose}>✕ Done{edits ? ` (${edits} edit${edits === 1 ? '' : 's'})` : ''}</button>
        </div>
        <div style={{ display: 'flex', gap: 6 }}>{tabBtn('brush', 'Brush mask')}{tabBtn('segments', 'Segments (SAM3)')}</div>
        {mode === 'brush' ? (
          <div style={{ display: 'grid', gap: 6 }}>
            <label style={{ ...label, marginBottom: 0 }}>Paint over what should change (red). Erase to fix mistakes.</label>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 12, color: '#a8b2c0' }}>size</span>
              <input type="range" min={8} max={160} value={brushSize} onChange={(e) => setBrushSize(parseInt(e.target.value, 10))} style={{ flex: 1 }} />
              <button style={{ ...btnGhost, padding: '3px 10px', fontSize: 12, borderColor: erase ? '#3b82f6' : '#2a2f3a' }}
                      onClick={() => setErase(!erase)}>{erase ? '✏️ Draw' : '🩹 Erase'}</button>
              <button style={{ ...btnGhost, padding: '3px 10px', fontSize: 12 }}
                      onClick={() => { if (dims) brushRef.current?.getContext('2d')?.clearRect(0, 0, dims.w, dims.h); }}>Clear</button>
            </div>
          </div>
        ) : (
          <div style={{ display: 'grid', gap: 6 }}>
            <label style={{ ...label, marginBottom: 0 }}>Detect regions by name, then click to select one or many (combines with any brush strokes).</label>
            <div style={{ display: 'flex', gap: 6 }}>
              <input style={{ ...input, flex: 1 }} placeholder="what to find — “earrings”, “hair”, “left arm”…"
                     value={segPrompt} onChange={(e) => setSegPrompt(e.target.value)} />
              <input style={{ ...input, width: 56 }} title="detection threshold (lower = more matches)"
                     value={segThresh} onChange={(e) => setSegThresh(e.target.value)} />
              <button style={{ ...btnGhost, padding: '4px 12px' }} disabled={segBusy} onClick={doDetect}>{segBusy ? '…' : 'Detect'}</button>
            </div>
            {segs.length > 0 && (
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {segs.map((sg, i) => (
                  <img key={i} src={`data:image/png;base64,${sg.b64}`} alt={`region ${i + 1}`}
                       onClick={() => setSegs((arr) => arr.map((x, xi) => xi === i ? { ...x, sel: !x.sel } : x))}
                       style={{ width: 64, height: 64, objectFit: 'contain', background: '#0e1116', cursor: 'pointer',
                                borderRadius: 6, border: `2px solid ${sg.sel ? '#3b82f6' : '#2a2f3a'}` }} />
                ))}
              </div>
            )}
          </div>
        )}
        <label style={{ ...label, marginBottom: 0 }}>What should appear there? (refs are “image 1”, “image 2”… in this prompt)</label>
        <textarea style={{ ...input, minHeight: 64, resize: 'vertical' }} value={prompt} onChange={(e) => setPrompt(e.target.value)}
                  placeholder="e.g. “bare skin, remove the necklace” · “add the makeup style from image 1” · “the tattoo design from image 1 on her upper arm”" />
        <input style={input} placeholder="negative (only active when guidance > 1)" value={negative} onChange={(e) => setNegative(e.target.value)} />
        <div style={{ display: 'grid', gap: 4 }}>
          <label style={{ ...label, marginBottom: 0 }}>Reference images (up to 3) — makeup / hair / tattoo / jewelry to copy from</label>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <input type="file" accept="image/*" disabled={refBusy || refs.length >= 3}
                   onChange={(e) => { onUploadEditRef(e.target.files); e.currentTarget.value = ''; }} style={{ fontSize: 12, color: '#cbd2dc' }} />
            {refBusy && <span style={{ fontSize: 12, color: '#9aa4b2' }}>uploading…</span>}
          </div>
          {refs.length > 0 && (
            <div style={{ display: 'flex', gap: 6 }}>
              {refs.map((r, i) => (
                <div key={r.ref.name} style={{ position: 'relative' }}>
                  <img src={r.url} alt={`ref ${i + 1}`} style={{ width: 56, height: 56, objectFit: 'cover', borderRadius: 6, border: '1px solid #2a2f3a' }} />
                  <span style={{ position: 'absolute', bottom: 2, left: 4, fontSize: 9, color: '#dbe2ea', textShadow: '0 0 3px #000' }}>img {i + 1}</span>
                  <button onClick={() => setRefs((arr) => arr.filter((_, xi) => xi !== i))}
                          style={{ position: 'absolute', top: -6, right: -6, width: 18, height: 18, borderRadius: 9, border: '1px solid #4a2a2a',
                                   background: '#20100f', color: '#ff8a8a', fontSize: 10, lineHeight: '16px', padding: 0, cursor: 'pointer' }}>✕</button>
                </div>
              ))}
            </div>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 12, color: '#a8b2c0' }}>steps</span>
          <input style={{ ...input, width: 52 }} value={steps} onChange={(e) => setSteps(e.target.value)} />
          <span style={{ fontSize: 12, color: '#a8b2c0' }}>guidance</span>
          <input style={{ ...input, width: 52 }} value={guidance} onChange={(e) => setGuidance(e.target.value)} />
          <span style={{ fontSize: 12, color: '#a8b2c0' }} title="grow/feather the mask edge (px) so the seam blends">edge</span>
          <input style={{ ...input, width: 52 }} value={grow} onChange={(e) => setGrow(e.target.value)} />
        </div>
        <button style={{ ...btn, opacity: busy ? 0.5 : 1 }} disabled={busy} onClick={doApply}>
          {busy ? 'Repainting…' : '✨ Apply edit (saves a new version)'}
        </button>
        {msg && <p style={{ fontSize: 12, color: msg.startsWith('✓') ? '#5ee08a' : '#ffb86a', margin: 0 }}>{msg}</p>}
        <p style={{ fontSize: 12, color: '#8d97a5', margin: 0 }}>
          Only the masked area is repainted — everything else stays pixel-identical. Each Apply saves a new
          {costumeName ? ' costume' : ' base'} version and becomes active; browse revisions with the version arrows
          and Set-active the one you want to keep. Great for removing reference remnants: brush over them and
          prompt “bare skin” / “plain background”.
        </p>
      </div>
    </div>
  );
}

// v1.276.0: the app's ONLY zoom+pan image viewer used to live here, unexported,
// while nine other places rendered a flat no-zoom copy. It now lives in
// components/shared/ImageLightbox.tsx and everything imports that one — the
// control-slot types come from there too, so the call sites below are unchanged.

// ---------------------------------------------------------------------------
export default function VNCCSNativePage({ variant = 'native' }: { variant?: 'native' | 'klein' }) {
  const [tab, setTab] = useState<Tab>('studio');
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [existingOutputs, setExistingOutputs] = useState<api.CharacterImagesT['outputs']>([]);
  const [host, setHostInfo] = useState<HostInfoT | null>(null);
  const [ctx, setCtx] = useState<ContextListsT | null>(null);
  const [editModel, setEditModel] = useState<string>('');
  // Character-generation settings (Creator/Cloner base render + Emotions).
  const [genMode, setGenMode] = useState<string>('');
  const [genModel, setGenModel] = useState<string>('');
  const [genSteps, setGenSteps] = useState<string>('');
  const [genCfg, setGenCfg] = useState<string>('');
  const [genSampler, setGenSampler] = useState<string>('');
  const [genScheduler, setGenScheduler] = useState<string>('');
  const [genSeed, setGenSeed] = useState<string>('');
  // Klein face-consistency settings (studio_vnccs_settings keys)
  const [kpMode, setKpMode] = useState<string>('auto');
  const [kpStrength, setKpStrength] = useState<string>('');
  const [frMode, setFrMode] = useState<string>('auto');
  const [frDenoise, setFrDenoise] = useState<string>('');
  const [frSteps, setFrSteps] = useState<string>('');
  const [frGuide, setFrGuide] = useState<string>('');
  const [baseClothing, setBaseClothing] = useState<string>('strip');
  const [runBaseClothing, setRunBaseClothing] = useState<string>('');
  const [faceKind, setFaceKind] = useState<string>('auto');
  const [styleCustom, setStyleCustom] = useState<string>('');
  const [lockBase, setLockBase] = useState<boolean>(true);
  const [autofitProps, setAutofitProps] = useState<boolean>(true);  // klein_autofit_proportions (SAM3D image auto-fit)
  // v1.176: base render mode — 'single' (front only), 'set' (4-view), or
  // 'mesh' (🧊 Mesh-ready A-pose set optimized for 3D mesh generation).
  const [baseMode, setBaseMode] = useState<'single' | 'set' | 'mesh'>('single');
  // v1.176: pinned seed from "Use current preview image settings" — when set,
  // the next Generate reproduces that exact image instead of rolling a new seed.
  const [reproSeed, setReproSeed] = useState<string>('');
  const [lastPreviewSeed, setLastPreviewSeed] = useState<number | null>(null);  // v1.196: seed of the last preview, for one-click Lock
  // v1.178: base-SET consistency — the same tools the pose sets use, now on base
  // sets so the 4 views match. Consistency LoRA (dx8152) + a shared seed across
  // the set (pins skin tone / lighting so views don't drift).
  const [baseConsLora, setBaseConsLora] = useState<boolean>(false);
  const [baseConsLoraStr, setBaseConsLoraStr] = useState<string>('');
  const [baseTurnLora, setBaseTurnLora] = useState<boolean>(false);
  const [baseTurnLoraName, setBaseTurnLoraName] = useState<string>('');
  const [baseTurnLoraStr, setBaseTurnLoraStr] = useState<string>('');
  const [baseTurnLoraTrig, setBaseTurnLoraTrig] = useState<string>('');
  const [qwenRefWeight, setQwenRefWeight] = useState<string>('');   // v1.194: Qwen reference strength (body adherence)
  const [qwenHeadwearRoom, setQwenHeadwearRoom] = useState<string>('');   // v1.199.13: Qwen reserved top headroom for tall hats
  const [qwenBaseBody, setQwenBaseBody] = useState<'underwear' | 'nude' | 'keep'>('underwear');  // v1.197: Qwen-mode base body (SFW/NSFW toggle lives in Klein-only controls)
  const [baseMatchViews, setBaseMatchViews] = useState<boolean>(true);
  const [cleanup, setCleanup] = useState<string>('gentle');
  const [kSteps, setKSteps] = useState<number>(6);
  // Pose-SET render overrides (separate from the base preview): poses add
  // mannequin-driven overlap the base never has, so they need more steps to avoid
  // dark occlusion lines where skin meets skin (hands worst-case).
  const [poseSteps, setPoseSteps] = useState<number>(8);
  const [poseFr, setPoseFr] = useState<string>('');            // '' global | 'on' | 'off'
  const [poseFrDenoise, setPoseFrDenoise] = useState<string>('');
  const [poseFrSteps, setPoseFrSteps] = useState<string>('');
  const [poseFrGuide, setPoseFrGuide] = useState<string>('');
  const [poseRefEnd, setPoseRefEnd] = useState<string>('');    // '' = default 0.85; '1' = off
  const [poseInput, setPoseInput] = useState<string>('');     // '' = mannequin; 'skeleton' = DWPose; 'depth' = RefControl depth
  const [poseSlock, setPoseSlock] = useState<string>('');     // '' = off; 0.35-0.95 = img2img structure lock
  const [poseSource, setPoseSource] = useState<string>('');    // '' = mannequin/clay; 'sam3d' = SAM3D reconstructed-body render
  const [poseLora, setPoseLora] = useState<string>('');       // '' = VNCCS PoseStudio; filename = override
  const [poseLoraStr, setPoseLoraStr] = useState<string>(''); // '' = 1.0
  const [consLora, setConsLora] = useState<boolean>(false);
  const [consLoraStr, setConsLoraStr] = useState<string>(''); // '' = 0.7
  const [posePu, setPosePu] = useState<string>('');           // '' = follow global PuLID; 'on' | 'off'
  const [posePuStr, setPosePuStr] = useState<string>('');     // '' = global strength
  // Presets (v1.156): named snapshots of EVERY klein_* dial (base + pose)
  const [presets, setPresets] = useState<Record<string, Record<string, unknown>>>({});
  const [presetSel, setPresetSel] = useState('');
  const [presetName, setPresetName] = useState('');
  const [presetMsg, setPresetMsg] = useState('');
  const [poseCleanup, setPoseCleanup] = useState<string>('gentle');
  const [rbEnd, setRbEnd] = useState<string>('0.85');
  const [bodyMatch, setBodyMatch] = useState<string>('1.6');
  const [bodyKeep, setBodyKeep] = useState<string>('person');  // reference masking: what the body-ref carries
  const [consistentSkin, setConsistentSkin] = useState(false);  // share one seed + colour-lock across a pose set
  // v1.199.23: pose-run identity aids now default OFF (bare = ground-truth Klein). Re-enable one at a time.
  const [poseBodyMatch, setPoseBodyMatch] = useState(false);   // klein_pose_body_match (ReferenceLatentPlus for POSES)
  const [poseFaceCropRef, setPoseFaceCropRef] = useState(false); // klein_face_crop_ref (dedicated face-crop reference latent)
  const [canvasW, setCanvasW] = useState<string>('1024');
  const [baseFr, setBaseFr] = useState<boolean>(true);
  const [baseFrDenoise, setBaseFrDenoise] = useState<string>('');
  const [baseFrSteps, setBaseFrSteps] = useState<string>('');
  const [samClean, setSamClean] = useState<boolean>(false);
  const [samPrompt, setSamPrompt] = useState<string>('');
  const [samThresh, setSamThresh] = useState<string>('');
  const [savingHost, setSavingHost] = useState(false);
  const settingsLoaded = useRef(false);
  const [showSettings, setShowSettings] = useState(false);
  const [showWorkshop, setShowWorkshop] = useState(false);

  const [hostChars, setHostChars] = useState<string[]>([]);
  const [emotionOpts, setEmotionOpts] = useState<EmotionOpt[]>([]);

  // Create form
  const [name, setName] = useState('');
  const [info, setInfo] = useState<VNCCSCharacterInfoT>({
    sex: 'female', age: 20, race: 'human', hair: '', eyes: '', face: '', body: '',
    skin_color: '', additional_details: '', aesthetics: 'masterpiece, best quality', nsfw: false,
  });
  const [background, setBackground] = useState('Green');
  const [previewImg, setPreviewImg] = useState<string>('');
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewElapsed, setPreviewElapsed] = useState(0);
  useEffect(() => {
    if (!previewBusy) { setPreviewElapsed(0); return; }
    const t0 = Date.now();
    setPreviewElapsed(0);
    const id = setInterval(() => setPreviewElapsed(Math.round((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(id);
  }, [previewBusy]);
  const [baseVersions, setBaseVersions] = useState<api.BaseVersionT[]>([]);
  const [baseImgDims, setBaseImgDims] = useState<{ w: number; h: number } | null>(null);
  const [regenPose, setRegenPose] = useState('');  // pose_name currently being re-rolled
  const [poseUpBusy, setPoseUpBusy] = useState<Set<string>>(new Set());  // asset_ids currently upscaling
  const [poseUpMsg, setPoseUpMsg] = useState('');
  const [activeBase, setActiveBase] = useState<string>('');
  const [verIdx, setVerIdx] = useState(0);
  const [baseViewIdx, setBaseViewIdx] = useState(0);
  const [editingCharId, setEditingCharId] = useState('');
  const [showAllVersions, setShowAllVersions] = useState(false);
  const [saveMsg, setSaveMsg] = useState('');

  // Pose selection (shared by Create / Cloner / Clothes)
  const [poseDefaults, setPoseDefaults] = useState<api.PoseDefaultT[]>([]);
  const [maxPoseSet, setMaxPoseSet] = useState(16);
  const [defaultSel, setDefaultSel] = useState<Set<number>>(new Set());
  const [removedDefaults, setRemovedDefaults] = useState<Set<number>>(new Set());
  const [extraPoses, setExtraPoses] = useState<ExtraPose[]>([]);
  const [poseSetMsg, setPoseSetMsg] = useState('');
  const [lightboxSrc, setLightboxSrc] = useState('');
  const [lightboxMode, setLightboxMode] = useState<'' | 'base' | 'costume'>('');
  const [helpTopic, setHelpTopic] = useState<string>('');   // v1.179: which how-to guide is open
  // v1.183: in-UI 3D viewer — the character whose mesh is open in the orbit lightbox.
  const [view3dChar, setView3dChar] = useState<string>('');
  const [view3dReady, setView3dReady] = useState<boolean>(typeof (globalThis as { customElements?: unknown }).customElements !== 'undefined'
    && !!(globalThis as { customElements?: { get?: (n: string) => unknown } }).customElements?.get?.('model-viewer'));
  // v1.180: live base-SET run (4-view / mesh) — front-anchored, per-view status, cancellable.
  const [baseSetRunId, setBaseSetRunId] = useState<string>('');
  const [baseSetData, setBaseSetData] = useState<import('./vnccsNativeApi').BaseSetStatusT | null>(null);
  // v1.181: anchor a set on a freshly-rendered front, or on the approved base image.
  const [baseSetAnchor, setBaseSetAnchor] = useState<'fresh' | 'approved'>('fresh');
  const [baseDeriveMethod, setBaseDeriveMethod] = useState<'reference' | 'matchpose'>('reference');
  const [visionView, setVisionView] = useState<{ name: string; text: string } | null>(null);
  const [libOpen, setLibOpen] = useState(false);
  const [libPoses, setLibPoses] = useState<Array<{ id: string; name: string; category: string; repository: string; pose: Record<string, unknown> | null }>>([]);
  const [libLoading, setLibLoading] = useState(false);
  const [libRepos, setLibRepos] = useState<api.PoseRepoT[]>([]);
  const [repoBusy, setRepoBusy] = useState('');
  const [libNote, setLibNote] = useState('');

  // Upscaler / pose-generation overrides (shared by Create / Cloner / Clothes)
  // defaults chosen for modest GPUs: upscaler OFF, 1024 when enabled
  const [upscaler, setUpscaler] = useState<UpscalerState>({ mode: 'off', resolution: '1024', targetSize: '1024' });

  // Clothes form
  const [clothesChar, setClothesChar] = useState('');
  const [costumeName, setCostumeName] = useState('');
  const [costume, setCostume] = useState<CostumeInfo>({ top: '', bottom: '', head: '', face: '', shoes: '' });
  // Klein clothing: optional reference-image of the target outfit (uploaded ref).
  const [garmentRef, setGarmentRef] = useState<api.UploadRefT | null>(null);
  const [garmentBusy, setGarmentBusy] = useState(false);
  // v1.166: Clothes engine sub-tab on the Klein page. 'klein' = our reference-
  // edit dressing pipeline (the puzzle we're tuning); 'qwen' = VNCCS's OWN
  // clothes process, exactly as their nodes do it (same path the Native page
  // uses). On the Native page this is always effectively 'qwen'.
  const [clothesSub, setClothesSub] = useState<'klein' | 'qwen'>('klein');
  const [emotionsSub, setEmotionsSub] = useState<'klein' | 'qwen'>('klein');   // v1.199.15: Emotions engine
  const [emoCostumesMap, setEmoCostumesMap] = useState<Record<string, unknown>>({});
  const [emoBaseVersions, setEmoBaseVersions] = useState<unknown[]>([]);
  // v1.168: Create tab engine sub-tab. 'klein' = our reference/identity chain;
  // 'qwen' = VNCCS's exact creation process rebuilt app-side (t2i base render
  // 640x1536 for new characters, reference-collage pipeline for clones, plus
  // the QIE2511 PoseStudio Pass-B pose sets).
  const [createEngine, setCreateEngine] = useState<'klein' | 'qwen' | 'klein2' | 'klein3'>('klein');
  // v1.276.0 — Experimental Modes gate. Klein 1.0 and Klein 2.0 are parked dev
  // lanes: they still work and are kept for later (game-asset export), but they
  // are not what someone making a character today should be steered into. The
  // setting hides their BUTTONS; nothing about their code paths changes.
  // NOTE: `settings` in this file is already the VNCCS/forge worker blob, hence
  // the deliberately different name.
  const [expModes, setExpModes] = useState(false);
  // v1.276.0 — New / Clone belong to the Qwen VNCCS flow (they seed a VNCCS
  // character on the worker), so they only make sense there. On the plain
  // VNCCS Native page the whole page IS that flow, so they always show.
  const showNewClone = variant !== 'klein' || createEngine === 'qwen';
  useEffect(() => {
    let alive = true;
    fetch('/api/settings')
      .then((r) => (r.ok ? r.json() : null))
      .then((st) => { if (alive && st) setExpModes(st.enable_experimental_modes === true); })
      .catch(() => { /* default stays off — the safe direction */ });
    return () => { alive = false; };
  }, []);
  // If the lane we are sitting in just got hidden, do not strand the user on an
  // invisible tab: fall back to the live Klein lane.
  useEffect(() => {
    if (!expModes && (createEngine === 'klein' || createEngine === 'klein2')) setCreateEngine('klein3');
  }, [expModes, createEngine]);
  // v1.171 Debug Options: persisted toggle + the Settings Variation Test panel
  const [debugOn, setDebugOn] = useState(false);
  const [vtOpen, setVtOpen] = useState(false);
  // v1.172 Simple pose mode: one toggle + a tiny surface instead of the full
  // dial stack; the tuned recipe rides as per-run settings_overrides so the
  // saved Advanced dials are never touched.
  const [poseMode, setPoseMode] = useState<'advanced' | 'simple'>('advanced');
  const [simpleRef, setSimpleRef] = useState<'mannequin' | 'skeleton'>('mannequin');
  // v1.199.92: PERSISTED. This used to be plain per-session state, so a page
  // reload (e.g. after a run.bat restart) silently dropped it -- the next run
  // then used the GENERIC MANNEQUIN with no error anywhere and the character
  // came out thin and generic. Too costly a footgun for a per-run toggle.
  const [m3dPoseOn, setM3dPoseOn] = useState<boolean>(() => {
    try { return localStorage.getItem('rbmn_m3d_pose_on') === '1'; } catch { return false; }
  });
  useEffect(() => {
    try { localStorage.setItem('rbmn_m3d_pose_on', m3dPoseOn ? '1' : '0'); } catch { /* ignore */ }
  }, [m3dPoseOn]);
  const [simpleQuality, setSimpleQuality] = useState<'fast' | 'balanced' | 'max'>('balanced');
  // v1.173 Tier-1 3D body: generation status + template pick
  const [m3dStatus, setM3dStatus] = useState<api.Mesh3dStatusT | null>(null);
  const [m3dTemplate, setM3dTemplate] = useState<'mixamo' | 'articulationxl'>('mixamo');
  const [m3dBusy, setM3dBusy] = useState(false);
  const [promoteBusy, setPromoteBusy] = useState(false);
  const [promoteMsg, setPromoteMsg] = useState('');
  const m3dPoll = useRef<number | null>(null);
  const m3dCharName = () => (createSub === 'clone' ? cloneName.trim() : name.trim());
  const refreshM3d = async (nm: string) => {
    if (!nm) { setM3dStatus(null); return; }
    try {
      const st3 = await api.mesh3dStatus(nm);
      setM3dStatus(st3);
      const running = st3.run?.status === 'running';
      setM3dBusy(!!running);
      if (!running && m3dPoll.current) { window.clearInterval(m3dPoll.current); m3dPoll.current = null; }
    } catch { /* backend older than 1.173 */ }
  };
  const startM3d = async (reuseMesh = false) => {
    const nm = m3dCharName();
    if (!nm || !host?.online || m3dBusy) return;
    setM3dBusy(true);
    try {
      const r = await api.mesh3dGenerate({ character_name: nm, template: m3dTemplate, reuse_mesh: reuseMesh });
      if (r.rig_available === false && r.rig_hint) setErrMsg(`3D body: ${r.rig_hint}`);
      if (m3dPoll.current) window.clearInterval(m3dPoll.current);
      m3dPoll.current = window.setInterval(() => { void refreshM3d(nm); }, 4000);
      void refreshM3d(nm);
    } catch (e) {
      setM3dBusy(false);
      setErrMsg(`3D body failed to start: ${(e as Error).message}`);
    }
  };
  // Promote the mesh-turnaround sprites (front/right/left/back) into a BASE VERSION with
  // the FRONT as the ACTIVE base, so "Generate 3D body" (which reads the active base's
  // views) consumes the perfect turnaround output.  Finds the 4 views by pose_name.
  const promoteTurnaroundBase = async () => {
    const nm = m3dCharName();
    if (!nm || !host?.online || promoteBusy) return;
    const ORDER = ['front', 'right', 'left', 'back'];
    const found: Record<string, string> = {};
    for (const o of existingOutputs) {
      for (const im of (o.images || [])) {
        const pn = String((im as { pose_name?: string | null }).pose_name || '').trim().toLowerCase();
        if (!pn.includes('mesh')) continue;
        for (const vw of ORDER) { if (pn.endsWith(vw)) found[vw] = im.asset_id; }
      }
    }
    if (!found['front']) {
      setPromoteMsg('⚠ No 🧊 Mesh-turnaround FRONT sprite found. Run a 🧊 Mesh turnaround (front/right/left/back) first, then promote.');
      return;
    }
    const views = ORDER.filter((v) => found[v]).map((v) => ({ view: v, asset_id: found[v] }));
    setPromoteBusy(true); setPromoteMsg('Promoting turnaround to the active base…');
    try {
      const r = await api.promoteTurnaround(nm, views);
      if (editingCharId) {
        try {
          const im = await api.getCharacterImages(editingCharId);
          const vers = im.base_versions || [];
          setBaseVersions(vers);
          setActiveBase(im.active_base || '');
          const ai = vers.findIndex((x) => x.id === im.active_base);
          setVerIdx(ai >= 0 ? ai : Math.max(0, vers.length - 1));
          if (vers.length) setPreviewImg(vers[ai >= 0 ? ai : vers.length - 1].url);
          setExistingOutputs(im.outputs || []);
        } catch { /* best-effort refresh */ }
      }
      void refreshM3d(nm);
      setPromoteMsg(`✓ Turnaround promoted — ${(r.views || []).join(' / ') || 'views'} saved as the ACTIVE base. “Generate 3D body” will now use them.`);
    } catch (e) {
      setPromoteMsg(`Promote failed: ${(e as Error).message}`);
    } finally {
      setPromoteBusy(false);
    }
  };
  const [qwenCreateMode, setQwenCreateMode] = useState(''); // '' auto | illustrious | anima | qwen | klein | zimage | krea2
  const [qwenCreateSteps, setQwenCreateSteps] = useState(''); // '' = model default
  const [qwenCreateCfg, setQwenCreateCfg] = useState('');     // '' = model default
  const [qwenCreateQL, setQwenCreateQL] = useState(true);     // per-model quality LoRA stacks
  // v1.167 Qwen (VNCCS-replica) dials -- '' = the suite's exact defaults
  const [qwenSteps, setQwenSteps] = useState('');          // def 4 (Lightning turbo)
  const [qwenCfg, setQwenCfg] = useState('');              // def 1.0
  const [qwenClothesLora, setQwenClothesLora] = useState(''); // ClothesCore strength, def 1.0
  const [qwenPoseLora, setQwenPoseLora] = useState('');    // PoseStudio strength (set runs), def 1.0
  const [qwenTarget, setQwenTarget] = useState('');        // encoder target size, def 1024
  const [garmentRefUrl, setGarmentRefUrl] = useState('');   // local preview of the uploaded outfit reference
  const [garmentPersisted, setGarmentPersisted] = useState(false); // v1.199.5: saved app-side for this costume
  const [cloMannequin, setCloMannequin] = useState<{ host: string; index: number } | null>(null);
  const [createSub, setCreateSub] = useState<'new' | 'clone'>('new');
  const [cloneSelIdx, setCloneSelIdx] = useState(0);
  const lastSyncedChar = useRef('');
  const [costumesMap, setCostumesMap] = useState<NonNullable<api.CharacterImagesT['costumes']>>({});
  // Klein clothes: pose-sprite target ('' = active base) + virtual try-on (v1.157)
  const [kcPose, setKcPose] = useState<string>('');
  // Dressing settings (v1.159): fix clothing blending into skin
  const [dressStrength, setDressStrength] = useState('');   // '' = 1.0
  const [dressRefEnd, setDressRefEnd] = useState('');       // '' = 0.8 def; '1' = old always-on
  const [dressSteps, setDressSteps] = useState('');         // '' = global klein steps
  const [dressGuide, setDressGuide] = useState('');         // '' = 1.0
  const [dressNeg, setDressNeg] = useState('');
  const [dressCons, setDressCons] = useState(false);      // stack the Consistency LoRA while dressing (identity guard)
  const [dressIdLock, setDressIdLock] = useState(true);   // v1.164: late face/hair identity ref (split-gated)
  const [garmentClean, setGarmentClean] = useState(true); // v1.164: extract garment photos onto white bg first
  const [tgar, setTgar] = useState<Array<{ ref: api.UploadRefT; desc: string; slot: string; url: string }>>([]);
  const [tgarBusy, setTgarBusy] = useState(false);
  const [tgarSlot, setTgarSlot] = useState('top');
  const [tgarDescIdx, setTgarDescIdx] = useState<number | null>(null);  // which try-on garment is vision-scanning
  const [tryBusy, setTryBusy] = useState(false);
  const [tryImg, setTryImg] = useState('');
  const [tryResultRef, setTryResultRef] = useState<{ name: string; subfolder: string; type: string } | null>(null);
  const [tryChain, setTryChain] = useState(false);
  const [trySteps, setTrySteps] = useState('28');
  const [tryGuide, setTryGuide] = useState('2.5');
  const [tryPersonDesc, setTryPersonDesc] = useState('');
  const [tryMsg, setTryMsg] = useState('');
  const [tryElapsed, setTryElapsed] = useState(0);
  useEffect(() => {
    if (!tryBusy) { setTryElapsed(0); return; }
    const t0 = Date.now();
    setTryElapsed(0);
    const id = setInterval(() => setTryElapsed(Math.round((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(id);
  }, [tryBusy]);
  // Pose output gallery view: upscaled copies take precedence as the defaults
  const [libView, setLibView] = useState<'upscaled' | 'original'>('upscaled');
  // Edit-Image modal (v1.158): { src, charName, costume? } or null
  const [editModal, setEditModal] = useState<null | { src: string; charName: string; costume?: string }>(null);
  const [costumeVersions, setCostumeVersions] = useState<api.CostumeVersionT[]>([]);
  const [costActive, setCostActive] = useState('');
  const [costVerIdx, setCostVerIdx] = useState(0);
  const [costPrevBusy, setCostPrevBusy] = useState(false);
  const [costPrevElapsed, setCostPrevElapsed] = useState(0);
  useEffect(() => {
    if (!costPrevBusy) { setCostPrevElapsed(0); return; }
    const t0 = Date.now();
    setCostPrevElapsed(0);
    const id = setInterval(() => setCostPrevElapsed(Math.round((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(id);
  }, [costPrevBusy]);
  const [costPrevImg, setCostPrevImg] = useState('');
  const [catNames, setCatNames] = useState<string[]>([]);
  const [catItems, setCatItems] = useState<api.CatalogItemT[]>([]);
  const [cloCharId, setCloCharId] = useState('');
  const [cloHostCostumes, setCloHostCostumes] = useState<string[]>([]);
  const [cloOutputs, setCloOutputs] = useState<api.CharacterImagesT['outputs']>([]);
  const [emoOutputs, setEmoOutputs] = useState<api.CharacterImagesT['outputs']>([]);
  const [emoRuns, setEmoRuns] = useState<NonNullable<api.CharacterImagesT['emotion_runs']>>([]);
  const [poseRuns, setPoseRuns] = useState<NonNullable<api.CharacterImagesT['pose_runs']>>([]);
  const [emoCharId, setEmoCharId] = useState('');
  const [costSaveMsg, setCostSaveMsg] = useState('');
  const [importOpen, setImportOpen] = useState(false);
  const [importChar, setImportChar] = useState('');
  const [importCostumes, setImportCostumes] = useState<NonNullable<api.CharacterImagesT['costumes']>>({});
  const [importLoading, setImportLoading] = useState(false);

  // Emotions form
  const [emoChar, setEmoChar] = useState('');
  const [emoCostumeOpts, setEmoCostumeOpts] = useState<string[]>([]);
  const [emoCostumesSel, setEmoCostumesSel] = useState<string[]>(['Original']);
  const [emoCostumesText, setEmoCostumesText] = useState('Original');
  const [emoPreviewCostume, setEmoPreviewCostume] = useState<string>('');
  const [emoSelected, setEmoSelected] = useState<string[]>([]);

  // Cloner form
  const [cloneName, setCloneName] = useState('');
  const [cloneRefs, setCloneRefs] = useState<api.UploadRefT[]>([]);
  const [enhanceOn, setEnhanceOn] = useState(false);
  const [enhanceModel, setEnhanceModel] = useState('');
  const [enhanceSharpen, setEnhanceSharpen] = useState('off');
  const [enhanceMaxSide, setEnhanceMaxSide] = useState(2048);
  const [enhancedMap, setEnhancedMap] = useState<Record<string, api.UploadRefT>>({});
  const [enhanceBusy, setEnhanceBusy] = useState(false);
  const [enhanceMsg, setEnhanceMsg] = useState('');
  const [refView, setRefView] = useState<'original' | 'upscaled'>('original');
  const [upscaleModels, setUpscaleModels] = useState<string[]>([]);
  const [enhanceMethod, setEnhanceMethod] = useState<'gan' | 'seedvr2'>('gan');
  const [enhanceStatus, setEnhanceStatus] = useState<Array<{ name: string; host: string; status: 'pending' | 'running' | 'done' | 'error'; detail: string }>>([]);
  const [enhanceByChar, setEnhanceByChar] = useState<Record<string, EnhanceRec>>({});
  const enhanceByCharRef = useRef<Record<string, EnhanceRec>>({});
  useEffect(() => { enhanceByCharRef.current = enhanceByChar; }, [enhanceByChar]);
  const [baseEnhanceOn, setBaseEnhanceOn] = useState(false);
  const [baseEnhanceMethod, setBaseEnhanceMethod] = useState<'gan' | 'seedvr2'>('gan');
  const [baseEnhanceModel, setBaseEnhanceModel] = useState('');
  const [baseEnhanceSharpen, setBaseEnhanceSharpen] = useState('off');
  const [baseEnhanceMaxSide, setBaseEnhanceMaxSide] = useState(2048);
  const [baseEnhanceBusy, setBaseEnhanceBusy] = useState(false);
  const [baseEnhanceMsg, setBaseEnhanceMsg] = useState('');
  // Live worker + elapsed timer while the base enhance runs, so you can see it's
  // actually working (not stuck) and on which worker.
  const [baseEnhanceWorker, setBaseEnhanceWorker] = useState('');
  const [baseEnhanceElapsed, setBaseEnhanceElapsed] = useState(0);
  useEffect(() => {
    if (!baseEnhanceBusy) { setBaseEnhanceElapsed(0); return; }
    const t0 = Date.now();
    setBaseEnhanceElapsed(0);
    const id = setInterval(() => setBaseEnhanceElapsed(Math.round((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(id);
  }, [baseEnhanceBusy]);
  // Switch Style (restyle the base into a new art style, saved as a new base version)
  const [switchStyleOn, setSwitchStyleOn] = useState(false);
  const [switchStyle, setSwitchStyle] = useState('photorealistic');
  const [switchStyleCustom, setSwitchStyleCustom] = useState('');
  const [switchStyleStrength, setSwitchStyleStrength] = useState('balanced'); // subtle|balanced|strong
  const [switchStyleRealism, setSwitchStyleRealism] = useState(true);
  const [switchStyleRef, setSwitchStyleRef] = useState<api.UploadRefT | null>(null);
  const [switchStyleBusy, setSwitchStyleBusy] = useState(false);
  const [switchStyleMsg, setSwitchStyleMsg] = useState('');
  const [switchStyleWorker, setSwitchStyleWorker] = useState('');
  const [switchStyleElapsed, setSwitchStyleElapsed] = useState(0);
  useEffect(() => {
    if (!switchStyleBusy) { setSwitchStyleElapsed(0); return; }
    const t0 = Date.now();
    setSwitchStyleElapsed(0);
    const id = setInterval(() => setSwitchStyleElapsed(Math.round((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(id);
  }, [switchStyleBusy]);
  useEffect(() => {
    if (!(enhanceOn || baseEnhanceOn) || !host?.online || upscaleModels.length) return;
    let cancelled = false;
    api.getUpscaleModels().then((r) => { if (!cancelled) setUpscaleModels(r.models || []); }).catch(() => { /* best-effort */ });
    return () => { cancelled = true; };
  }, [enhanceOn, baseEnhanceOn, host, upscaleModels.length]);
  const [uploading, setUploading] = useState(false);
  const [genViewBusy, setGenViewBusy] = useState(false);
  const [cloneInfo, setCloneInfo] = useState<VNCCSCharacterInfoT | null>(null);

  // LLM wizards
  const [wizCreateText, setWizCreateText] = useState('');
  const [wizClothesText, setWizClothesText] = useState('');
  const [wizBusy, setWizBusy] = useState(false);
  const [wizMsg, setWizMsg] = useState('');

  // PARKED 2026-08-11 — Library (catalog + project-linking) state. The Library
  // tab's JSX is gone (no `tab === 'library'` branch remains), so nothing reads
  // these and nothing calls loadLibrary / doDeleteCatalog / doLink (also parked,
  // below). Kept together, commented out, so the tab can be restored as a unit.
  // const [catalog, setCatalog] = useState<api.CatalogItemT[]>([]);
  // const [projects, setProjects] = useState<api.ProjectLiteT[]>([]);
  // const [linkProject, setLinkProject] = useState('');
  // const [libMsg, setLibMsg] = useState('');

  const [phase, setPhase] = useState<Phase>('idle');
  const [statusText, setStatusText] = useState('');
  const [chunks, setChunks] = useState<ChunkState[]>([]);
  const [runId, setRunId] = useState('');
  const [stopping, setStopping] = useState(false);
  const [showAllOutputs, setShowAllOutputs] = useState(false);
  const [showAllLibOutputs, setShowAllLibOutputs] = useState(false);
  const [lightboxList, setLightboxList] = useState<string[]>([]);
  const [lightboxIdx, setLightboxIdx] = useState(0);
  const lightboxNavRef = useRef<((i: number) => void) | null>(null);
  const [vnccsHosts, setVnccsHosts] = useState<string[]>([]);
  const [parallelOn, setParallelOn] = useState(true);
  const [seedMode, setSeedMode] = useState<'randomize' | 'fixed'>('randomize');
  const [seedVal, setSeedVal] = useState('');
  const [ingestMsg, setIngestMsg] = useState('');
  const [errMsg, setErrMsg] = useState('');

  // Apply every klein_* dial from a settings-shaped object (used by the initial
  // load AND by Presets).  Missing keys fall back to the loader's defaults.
  const applyKleinSettings = (st: Record<string, unknown>) => {
    setKpMode(String(st.klein_pulid ?? 'off'));
    setKpStrength(st.klein_pulid_strength !== undefined ? String(st.klein_pulid_strength) : '');
    setFrMode(String(st.klein_face_refine ?? 'auto'));
    setFrDenoise(st.klein_face_refine_denoise !== undefined ? String(st.klein_face_refine_denoise) : '');
    setFrSteps(st.klein_face_refine_steps !== undefined ? String(st.klein_face_refine_steps) : '');
    setFrGuide(st.klein_face_refine_guide !== undefined ? String(st.klein_face_refine_guide) : '');
    setBaseClothing(String(st.klein_base_clothing ?? 'strip'));
    // 'realistic' was the old value for the photoreal style — map it forward
    setFaceKind((s => (s === 'realistic' ? 'photorealistic' : s))(String(st.klein_face_kind ?? 'auto')));
    setStyleCustom(String(st.klein_style_custom ?? ''));
    setLockBase(!['off', 'false', '0', 'no'].includes(String(st.klein_lock_base ?? 'on').toLowerCase()));
    setAutofitProps(!['off', 'false', '0', 'no'].includes(String(st.klein_autofit_proportions ?? 'on').toLowerCase()));
    setBaseMode((() => { const s = String(st.klein_base_set ?? 'off').toLowerCase();
      return ['mesh', 'mesh-ready', 'mesh_ready', '3d'].includes(s) ? 'mesh'
        : ['on', 'true', '1', 'yes', 'set'].includes(s) ? 'set' : 'single'; })());
    setBaseSetAnchor(String(st.klein_base_set_anchor ?? 'fresh').toLowerCase() === 'approved' ? 'approved' : 'fresh');
    setBaseDeriveMethod(String(st.klein_base_derive_method ?? 'reference').toLowerCase() === 'matchpose' ? 'matchpose' : 'reference');
    // ⚠ v1.277.10 — this async settings load used to STOMP a deep-linked
    // 'klein3' back to 'klein', unmounting Klein3Panel after it had already
    // consumed the one-shot focus key; the remounted panel then fell back to
    // whichever character sorted first. Never downgrade klein3 here.
    setCreateEngine((prev) => prev === 'klein3' ? prev
      : (String(st.vnccs_create_engine ?? 'klein').toLowerCase() === 'qwen' ? 'qwen' : 'klein'));
    setClothesSub(String(st.vnccs_clothes_engine ?? 'klein').toLowerCase() === 'qwen' ? 'qwen' : 'klein');
    setEmotionsSub(String(st.vnccs_emotions_engine ?? 'klein').toLowerCase() === 'qwen' ? 'qwen' : 'klein');
    setQwenRefWeight(st.qwen_ref_weight !== undefined ? String(st.qwen_ref_weight) : '');
    setQwenHeadwearRoom(st.qwen_headwear_room !== undefined ? String(st.qwen_headwear_room) : '');
    setQwenBaseBody(['nude', 'keep', 'underwear'].includes(String(st.qwen_base_body)) ? (String(st.qwen_base_body) as 'underwear' | 'nude' | 'keep') : 'underwear');
    setCleanup(String(st.klein_cleanup ?? 'gentle'));
    setKSteps(parseInt(String(st.klein_steps ?? '6'), 10) || 6);
    setPoseSteps(parseInt(String(st.klein_pose_steps ?? '8'), 10) || 8);
    setPoseCleanup(String(st.klein_pose_cleanup ?? st.klein_cleanup ?? 'gentle'));
    setPoseFr(st.klein_pose_face_refine !== undefined ? String(st.klein_pose_face_refine) : '');
    setPoseSource(st.klein_pose_source !== undefined ? String(st.klein_pose_source) : '');
    setPoseFrDenoise(st.klein_pose_face_refine_denoise !== undefined ? String(st.klein_pose_face_refine_denoise) : '');
    setPoseFrSteps(st.klein_pose_face_refine_steps !== undefined ? String(st.klein_pose_face_refine_steps) : '');
    setPoseFrGuide(st.klein_pose_face_refine_guide !== undefined ? String(st.klein_pose_face_refine_guide) : '');
    setPoseRefEnd(st.klein_pose_ref_end !== undefined ? String(st.klein_pose_ref_end) : '');
    setPoseBodyMatch(['on', 'auto', 'true', '1', 'yes'].includes(String(st.klein_pose_body_match ?? 'off').toLowerCase()));
    setPoseFaceCropRef(['on', 'auto', 'true', '1', 'yes'].includes(String(st.klein_face_crop_ref ?? 'off').toLowerCase()));
    setPoseInput(st.klein_pose_input !== undefined ? String(st.klein_pose_input) : '');
    setPoseSlock(st.klein_pose_structure_lock !== undefined ? String(st.klein_pose_structure_lock) : '');
    setDressStrength(st.klein_clothes_strength !== undefined ? String(st.klein_clothes_strength) : '');
    setDressRefEnd(st.klein_clothes_ref_end !== undefined ? String(st.klein_clothes_ref_end) : '');
    setDressSteps(st.klein_clothes_steps !== undefined ? String(st.klein_clothes_steps) : '');
    setDressGuide(st.klein_clothes_guidance !== undefined ? String(st.klein_clothes_guidance) : '');
    setDressNeg(st.klein_clothes_negative !== undefined ? String(st.klein_clothes_negative) : '');
    setDressCons(String(st.klein_clothes_consistency || '') === 'on');
    setDressIdLock(String(st.klein_clothes_identity_lock || '') !== 'off');
    setGarmentClean(String(st.klein_clothes_clean_garment || '') !== 'off');
    setQwenSteps(st.qwen_steps !== undefined ? String(st.qwen_steps) : '');
    setQwenCfg(st.qwen_cfg !== undefined ? String(st.qwen_cfg) : '');
    setQwenClothesLora(st.qwen_clothes_lora_strength !== undefined ? String(st.qwen_clothes_lora_strength) : '');
    setQwenPoseLora(st.qwen_pose_lora_strength !== undefined ? String(st.qwen_pose_lora_strength) : '');
    setQwenTarget(st.qwen_target_size !== undefined ? String(st.qwen_target_size) : '');
    setQwenCreateMode(st.qwen_create_mode !== undefined ? String(st.qwen_create_mode) : '');
    setQwenCreateSteps(st.qwen_create_steps !== undefined ? String(st.qwen_create_steps) : '');
    setQwenCreateCfg(st.qwen_create_cfg !== undefined ? String(st.qwen_create_cfg) : '');
    setQwenCreateQL(String(st.qwen_create_quality_loras || '') !== 'off');
    setDebugOn(String(st.studio_debug || '') === 'on');
    setPoseMode(String(st.klein_pose_mode || '') === 'simple' ? 'simple' : 'advanced');
    setSimpleRef(String(st.klein_pose_simple_ref || '') === 'skeleton' ? 'skeleton' : 'mannequin');
    const _sq = String(st.klein_pose_simple_quality || '');
    setSimpleQuality(_sq === 'fast' ? 'fast' : _sq === 'max' ? 'max' : 'balanced');
    setPoseLora(st.klein_pose_lora !== undefined ? String(st.klein_pose_lora) : '');
    setPoseLoraStr(st.klein_pose_lora_strength !== undefined ? String(st.klein_pose_lora_strength) : '');
    setConsLora(['on', 'true', '1', 'yes'].includes(String(st.klein_consistency_lora ?? 'off').toLowerCase()));
    setConsLoraStr(st.klein_consistency_lora_strength !== undefined ? String(st.klein_consistency_lora_strength) : '');
    setPosePu(st.klein_pose_pulid !== undefined ? String(st.klein_pose_pulid) : '');
    setPosePuStr(st.klein_pose_pulid_strength !== undefined ? String(st.klein_pose_pulid_strength) : '');
    setRbEnd(st.klein_refbase_ref_end !== undefined ? String(st.klein_refbase_ref_end) : '0.85');
    setBodyMatch(st.klein_body_match_strength !== undefined ? String(st.klein_body_match_strength) : '1.6');
    setBodyKeep(st.klein_body_match_keep !== undefined ? String(st.klein_body_match_keep) : 'person');
    setConsistentSkin(['on', 'true', '1', 'yes'].includes(String(st.klein_consistent_skin ?? 'off').toLowerCase()));
    setCanvasW(st.klein_canvas_width !== undefined ? String(st.klein_canvas_width) : '1024');
    setBaseFr(!['off', 'false', '0', 'no'].includes(String(st.klein_base_face_refine ?? 'on').toLowerCase()));
    setBaseFrDenoise(st.klein_base_face_refine_denoise !== undefined ? String(st.klein_base_face_refine_denoise) : '');
    setBaseFrSteps(st.klein_base_face_refine_steps !== undefined ? String(st.klein_base_face_refine_steps) : '');
    setBaseConsLora(['on', 'true', '1', 'yes'].includes(String(st.klein_base_consistency_lora ?? 'off').toLowerCase()));
    setBaseConsLoraStr(st.klein_base_consistency_lora_strength !== undefined ? String(st.klein_base_consistency_lora_strength) : '');
    setBaseTurnLora(['on', 'true', '1', 'yes'].includes(String(st.klein_base_turnaround_lora ?? 'off').toLowerCase()));
    setBaseTurnLoraName(st.klein_base_turnaround_lora_name !== undefined ? String(st.klein_base_turnaround_lora_name) : '');
    setBaseTurnLoraStr(st.klein_base_turnaround_lora_strength !== undefined ? String(st.klein_base_turnaround_lora_strength) : '');
    setBaseTurnLoraTrig(st.klein_base_turnaround_lora_trigger !== undefined ? String(st.klein_base_turnaround_lora_trigger) : '');
    setBaseMatchViews(!['off', 'false', '0', 'no'].includes(String(st.klein_base_consistent_seed ?? 'on').toLowerCase()));
    setSamClean(['on', 'true', '1', 'yes'].includes(String(st.klein_sam_cleanup ?? 'off').toLowerCase()));
    setSamPrompt(st.klein_sam_cleanup_prompt !== undefined ? String(st.klein_sam_cleanup_prompt) : '');
    setSamThresh(st.klein_sam_cleanup_threshold !== undefined ? String(st.klein_sam_cleanup_threshold) : '');
    setRunBaseClothing(String(st.klein_run_base_clothing ?? ''));
  };

  // Presets: a snapshot is every klein_* key the auto-save writes (future dials
  // are captured automatically); applying merges the preset over the current
  // dials, and the auto-save effect then persists it as the working defaults.
  const snapshotPreset = (): Record<string, unknown> => {
    const all = buildSettings();
    const snap: Record<string, unknown> = {};
    Object.entries(all).forEach(([k, v]) => {
      if (k.startsWith('klein_') && k !== 'klein_presets' && v !== undefined) snap[k] = v;
    });
    return snap;
  };
  const savePreset = (name: string) => {
    const n = name.trim();
    if (!n) { setPresetMsg('Give the preset a name first.'); return; }
    setPresets((p) => ({ ...p, [n]: snapshotPreset() }));
    setPresetSel(n); setPresetName('');
    setPresetMsg(`Saved “${n}” from the current dials.`);
  };
  const applyPreset = (name: string) => {
    const pz = presets[name];
    if (!pz) return;
    applyKleinSettings({ ...buildSettings(), ...pz });
    setPresetMsg(`Applied “${name}” — the dials update and auto-save as the new working defaults.`);
  };
  const deletePreset = (name: string) => {
    setPresets((p) => { const q = { ...p }; delete q[name]; return q; });
    if (presetSel === name) setPresetSel('');
    setPresetMsg(`Deleted “${name}”.`);
  };
  // v1.176: reverse of _klein_gen_meta — take the settings snapshot stored on a
  // base version (curVersion.gen_meta) and load them back into the live dials so
  // the user can reproduce or iterate on exactly that image. Also pins the seed
  // so the very next Generate re-rolls the identical output.
  const KNOWN_FACE_KINDS = ['auto', 'photorealistic', 'semi-realistic', 'anime',
    'manga', 'comic', 'cartoon', '3d', 'painting', 'custom'];
  const applyGenMetaToDials = (gm?: Record<string, unknown> | null) => {
    if (!gm || typeof gm !== 'object' || !Object.keys(gm).length) {
      setPresetMsg('That preview image has no saved generation settings to restore.'); return;
    }
    const g = gm as Record<string, unknown>;
    const st: Record<string, unknown> = {};
    const set = (k: string, v: unknown) => { if (v !== undefined && v !== null && v !== '') st[k] = v; };
    set('klein_body_match_strength', g.body_adherence);
    set('klein_refbase_ref_end', g.strip_release);
    set('klein_cleanup', g.cleanup);
    set('klein_steps', g.steps);
    set('klein_base_face_refine', g.face_refine);
    set('klein_base_face_refine_denoise', g.face_refine_denoise);
    set('klein_base_face_refine_steps', g.face_refine_steps);
    set('klein_pulid', g.pulid);
    set('klein_sam_cleanup', g.sam_cleanup);
    set('klein_lock_base', g.lock_base);
    set('klein_base_clothing', g.base_clothing);
    set('klein_base_set', g.base_mode);   // restore the view mode too
    const canvas = String(g.canvas || '');
    if (canvas.includes('x')) set('klein_canvas_width', canvas.split('x')[0]);
    const style = String(g.style || '').trim();
    if (style && style.toLowerCase() !== 'auto') {
      if (KNOWN_FACE_KINDS.includes(style.toLowerCase())) set('klein_face_kind', style.toLowerCase());
      else { set('klein_face_kind', 'custom'); set('klein_style_custom', style); }
    }
    applyKleinSettings({ ...buildSettings(), ...st });
    const seed = g.seed;
    if (seed !== undefined && seed !== null && String(seed) !== '' && !Number.isNaN(Number(seed))) {
      setReproSeed(String(seed));
      setPresetMsg(`Loaded the settings that made this image. Seed ${seed} is pinned — the next ` +
        `Generate reproduces it. Clear the seed chip to explore variations again.`);
    } else {
      setPresetMsg('Loaded the settings that made this image into the live dials.');
    }
  };

  const loadHost = useCallback(async () => {
    try {
      const h = await api.getHost();
      setHostInfo(h);
      const st = (h.settings || {}) as Record<string, unknown>;
      applyKleinSettings(st);
      {
        // Presets: load saved ones; on the FIRST run after this update, clone the
        // CURRENT tuned settings as the "Realistic" preset (per Lorenzo's ask).
        let pr: Record<string, Record<string, unknown>> = {};
        const raw = st.klein_presets;
        if (raw && typeof raw === 'object' && !Array.isArray(raw)) pr = { ...(raw as Record<string, Record<string, unknown>>) };
        if (!Object.keys(pr).length) {
          const seed: Record<string, unknown> = {};
          Object.entries(st).forEach(([k, v]) => {
            if (k.startsWith('klein_') && k !== 'klein_presets' && v !== undefined && v !== null) seed[k] = v;
          });
          if (Object.keys(seed).length) pr = { Realistic: seed };
        }
        setPresets(pr);
      }
      setEnhanceOn(['on', 'true', '1', 'yes'].includes(String(st.enhance_on ?? 'off').toLowerCase()));
      setEnhanceMethod(String(st.enhance_method ?? 'gan') === 'seedvr2' ? 'seedvr2' : 'gan');
      setEnhanceModel(String(st.enhance_model ?? ''));
      setEnhanceSharpen(String(st.enhance_sharpen ?? 'off'));
      setEnhanceMaxSide(parseInt(String(st.enhance_max_side ?? '2048'), 10) || 2048);
      setEnhanceByChar((st.enhance_by_char as Record<string, EnhanceRec>) || {});
      setBaseEnhanceOn(['on', 'true', '1', 'yes'].includes(String(st.base_enhance_on ?? 'off').toLowerCase()));
      setBaseEnhanceMethod(String(st.base_enhance_method ?? 'gan') === 'seedvr2' ? 'seedvr2' : 'gan');
      setBaseEnhanceModel(String(st.base_enhance_model ?? ''));
      setBaseEnhanceSharpen(String(st.base_enhance_sharpen ?? 'off'));
      setBaseEnhanceMaxSide(parseInt(String(st.base_enhance_max_side ?? '2048'), 10) || 2048);
      setSwitchStyleOn(['on', 'true', '1', 'yes'].includes(String(st.klein_switch_style_on ?? 'off').toLowerCase()));
      setSwitchStyle(String(st.klein_switch_style ?? 'photorealistic'));
      setSwitchStyleRealism(!['off', 'false', '0', 'no'].includes(String(st.klein_switch_style_realism ?? 'on').toLowerCase()));
      setSwitchStyleCustom(String(st.klein_switch_style_custom ?? ''));
      setSwitchStyleStrength(String(st.klein_switch_style_strength ?? 'balanced'));
      const cc = (h.settings?.control_center as Record<string, unknown>) || {};
      if (typeof cc.selected_model === 'string') setEditModel(cc.selected_model);
      const gs = (h.settings?.gen_settings as Record<string, unknown>) || {};
      setGenMode(typeof gs.generation_mode === 'string' ? gs.generation_mode : '');
      setGenModel(String(gs.diffusion_model_name || gs.ckpt_name || ''));
      setGenSteps(gs.steps !== undefined ? String(gs.steps) : '');
      setGenCfg(gs.cfg !== undefined ? String(gs.cfg) : '');
      setGenSampler(typeof gs.sampler === 'string' ? gs.sampler : '');
      setGenScheduler(typeof gs.scheduler === 'string' ? gs.scheduler : '');
      setGenSeed(gs.seed !== undefined && Number(gs.seed) !== 0 ? String(gs.seed) : '');
      const ps = (h.settings?.pose_set as { removed?: number[]; extras?: ExtraPose[] }) || null;
      if (ps) {
        if (Array.isArray(ps.removed)) setRemovedDefaults(new Set(ps.removed));
        if (Array.isArray(ps.extras)) {
          const extras = ps.extras.filter((x) => x && x.pose);
          setExtraPoses(extras);
          // v1.165: older saved pose sets stored LIVE relay URLs as thumbs --
          // re-fetch each once and bake it into a data URI (best-effort; a
          // failed fetch clears the URL so the tile shows its name cleanly).
          extras.forEach((x) => {
            if (!x.thumbUrl || x.thumbUrl.startsWith('data:')) return;
            fetch(x.thumbUrl)
              .then((rsp) => (rsp.ok ? rsp.blob().then(blobToDataURL) : Promise.reject()))
              .then((du) => setExtraPoses((prev) => prev.map((y) => (y.id === x.id ? { ...y, thumbUrl: du } : y))))
              .catch(() => setExtraPoses((prev) => prev.map((y) => (y.id === x.id ? { ...y, thumbUrl: undefined } : y))));
          });
        }
      }
      settingsLoaded.current = true;
      if (h.online) {
        try { setCtx(await api.getContextLists()); } catch { /* flaky host */ }
        try { setHostChars(parseCharacters(await api.getVnccsCharacters())); } catch { /* none */ }
        try { setEmotionOpts(parseEmotions(await api.getEmotions())); } catch { /* none */ }
      }
    } catch {
      setHostInfo({ host: null, configured: null, online: false, settings: {} });
      settingsLoaded.current = true;
    }
  }, []);

  useEffect(() => { loadHost(); }, [loadHost]);

  // keep the worker-availability badge fresh without clobbering local settings
  useEffect(() => {
    const tick = () => api.getHost()
      .then((h) => setHostInfo((prev) => (prev ? { ...prev, host: h.host, configured: h.configured, online: h.online } : h)))
      .catch(() => { /* ignore */ });
    const id = setInterval(tick, 15000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    api.getVnccsHosts().then((r) => setVnccsHosts(r.hosts || [])).catch(() => setVnccsHosts([]));
  }, [host?.online]);

  // Results are per-tab: don't show the Create run's images on Clothes/Emotions.
  useEffect(() => {
    if (!busy) { setChunks([]); setIngestMsg(''); setErrMsg(''); setStatusText(''); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  // Clothes/Emotions follow the character of the active Create sub-mode (New
  // vs Clone) — applied once per character so manual picks aren't stomped.
  useEffect(() => {
    if (tab !== 'clothes' && tab !== 'emotions') return;
    const src = (createSub === 'clone' ? cloneName : name).trim();
    if (src && src !== lastSyncedChar.current) {
      lastSyncedChar.current = src;
      setClothesChar(src);
      setEmoChar(src);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  // Freshly generated characters appear in pickers: refresh the host list AND
  // merge our catalog names (a character may live on a shard worker the pinned
  // host doesn't know about).
  useEffect(() => {
    if (tab !== 'clothes' && tab !== 'emotions') return;
    if (host?.online) {
      api.getVnccsCharacters().then((r) => setHostChars(parseCharacters(r))).catch(() => { /* keep old */ });
    }
    api.getCatalog().then((cat) => { setCatNames(cat.map((c) => c.name)); setCatItems(cat); })
      .catch(() => setCatNames([]));
  }, [tab, host?.online]);

  // Outfit gallery data follows the character picked on the Clothes tab: our
  // catalog's costume versions + saved prompts, plus host-side costume names.
  useEffect(() => {
    if (tab !== 'clothes' || !clothesChar) return;
    api.getCatalog().then(async (cat) => {
      const item = cat.find((c) => c.name === clothesChar);
      if (item) {
        setCloCharId(item.character_id);
        try {
          const r = await api.getCharacterImages(item.character_id);
          setCostumesMap(r.costumes || {});
          setCloOutputs(r.outputs || []);
          setPoseRuns(r.pose_runs || []);
        } catch { setCostumesMap({}); setCloOutputs([]); }
      } else {
        setCloCharId('');
        setCostumesMap({});
        setCloOutputs([]);
      }
    }).catch(() => { /* keep old */ });
    if (host?.online) {
      const rec = catItems.find((c) => c.name === clothesChar)?.hosts || [];
      const online = rec.filter((h) => vnccsHosts.includes(h));
      const hostsToAsk = online.length ? online : (host?.host ? [host.host] : []);
      Promise.all(hostsToAsk.map(async (h) => {
        try {
          const r = await api.relayJson<unknown>(
            `get_character_costumes?character=${encodeURIComponent(clothesChar)}&_vnccs_host=${encodeURIComponent(h)}`);
          return Array.isArray(r) ? r.map(String) : [];
        } catch { return [] as string[]; }
      })).then((lists) => setCloHostCostumes(Array.from(new Set(lists.flat()))
        .filter((c) => c !== 'Naked' && c !== 'Original')));
    } else setCloHostCostumes([]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, clothesChar, host?.online]);

  // Default poses (app-side thumbnails; independent of the host)
  useEffect(() => {
    api.getPoseDefaults().then((r) => {
      setPoseDefaults(r.poses);
      setMaxPoseSet(r.max_pose_set || 16);
      setDefaultSel(new Set(r.poses.map((p) => p.index)));
    }).catch(() => setPoseDefaults([]));
  }, []);

  // Emotions library: the selected character's generated emotion sets + run
  // history, loaded on every Emotions tab open. Emotions already generated
  // get PRE-SELECTED (so the UI tracks what's been done).
  useEffect(() => {
    if (tab !== 'emotions' || !emoChar) { return; }
    api.getCatalog().then(async (cat) => {
      const item = cat.find((c) => c.name === emoChar);
      if (!item) { setEmoOutputs([]); setEmoRuns([]); setEmoCharId(''); return; }
      try {
        const r = await api.getCharacterImages(item.character_id);
        setEmoOutputs(r.outputs || []);
        setEmoRuns(r.emotion_runs || []);
        setEmoCharId(item.character_id);
        setEmoCostumesMap(r.costumes || {});
        setEmoBaseVersions(r.base_versions || []);
        const done = Array.from(new Set((r.emotion_runs || []).flatMap((x) => x.emotions || [])));
        if (done.length) setEmoSelected(done);
      } catch { setEmoOutputs([]); setEmoRuns([]); }
    }).catch(() => { /* keep old */ });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, emoChar]);

  // Costume/base sets for the Emotions tab — sourced from the APP CATALOG (not the
  // worker store, which is empty for Klein-Hybrid characters) and filtered by the
  // active engine toggle: Qwen shows Qwen-made sets; Klein shows Klein-made + any
  // untagged/legacy sets. 'Base' (the character's base pose sprites) is always
  // offered; the backend engine-filters the actual sprites it runs on.
  useEffect(() => {
    if (tab !== 'emotions') { return; }
    const matchEng = (eng?: unknown) => {
      const e = String(eng || '').toLowerCase();
      if (!e) return true;                       // untagged legacy shows under BOTH engines
      return emotionsSub === 'qwen' ? e === 'qwen' : e === 'klein';
    };
    const cos: string[] = [];
    for (const [nm, entry] of Object.entries(emoCostumesMap || {})) {
      const vers = ((entry as { versions?: { engine?: unknown }[] })?.versions) || [];
      if (!vers.length || vers.some((v) => matchEng(v?.engine))) cos.push(nm);
    }
    const list = ['Base', ...cos];
    setEmoCostumeOpts(list);
    setEmoCostumesSel((prev) => {
      const keep = prev.filter((c) => list.includes(c));
      return keep.length ? keep : ['Base'];
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, emoChar, emotionsSub, emoCostumesMap, emoBaseVersions]);

  const modelOptions = useMemo(() => {
    if (!ctx) return [] as string[];
    const merged = [
      ...(ctx.diffusion_models || []), ...(ctx.checkpoints || []),
      ...((ctx as Record<string, unknown>).gguf as string[] | undefined || []),
    ];
    return Array.from(new Set(merged));
  }, [ctx]);

  const buildSettings = (): Record<string, unknown> => {
    const settings: Record<string, unknown> = { ...(host?.settings || {}) };
    const cc = { ...((settings.control_center as Record<string, unknown>) || {}) };
    if (editModel) cc.selected_model = editModel; else delete cc.selected_model;
    if (Object.keys(cc).length) settings.control_center = cc; else delete settings.control_center;
    const gs: Record<string, unknown> = {};
    if (genMode) gs.generation_mode = genMode;
    if (genModel) {
      if ((genMode || 'anima') === 'anima') gs.diffusion_model_name = genModel;
      else gs.ckpt_name = genModel;
    }
    if (genSteps.trim() !== '') gs.steps = parseInt(genSteps, 10) || undefined;
    if (genCfg.trim() !== '') gs.cfg = parseFloat(genCfg) || undefined;
    if (genSampler) gs.sampler = genSampler;
    if (genScheduler) gs.scheduler = genScheduler;
    if (genSeed.trim() !== '') gs.seed = parseInt(genSeed, 10) || 0;
    if (Object.keys(gs).length) settings.gen_settings = gs; else delete settings.gen_settings;
    settings.klein_pulid = kpMode || 'off';
    if (kpStrength.trim() !== '') settings.klein_pulid_strength = parseFloat(kpStrength) || 1.4;
    else delete settings.klein_pulid_strength;
    settings.klein_face_refine = frMode || 'auto';
    if (frDenoise.trim() !== '') settings.klein_face_refine_denoise = parseFloat(frDenoise) || 0.55;
    else delete settings.klein_face_refine_denoise;
    if (frSteps.trim() !== '') settings.klein_face_refine_steps = parseInt(frSteps, 10) || 6;
    else delete settings.klein_face_refine_steps;
    if (frGuide.trim() !== '') settings.klein_face_refine_guide = parseInt(frGuide, 10) || 768;
    else delete settings.klein_face_refine_guide;
    settings.klein_base_clothing = baseClothing || 'strip';
    settings.klein_face_kind = faceKind || 'auto';
    settings.klein_style_custom = styleCustom;
    settings.klein_lock_base = lockBase ? 'on' : 'off';
    settings.klein_autofit_proportions = autofitProps ? 'on' : 'off';
    settings.klein_base_set = baseMode === 'mesh' ? 'mesh' : baseMode === 'set' ? 'set' : 'off';
    settings.klein_base_set_anchor = baseSetAnchor;   // v1.181: remember last anchor choice
    settings.klein_base_derive_method = baseDeriveMethod;   // v1.189: reference | matchpose
    settings.vnccs_create_engine = createEngine;   // v1.191: remember last Create/Clone engine (klein|qwen)
    settings.vnccs_clothes_engine = clothesSub;   // v1.199.3: remember last Clothes engine (klein|qwen)
    settings.vnccs_emotions_engine = emotionsSub; // v1.199.15: remember last Emotions engine
    if (qwenRefWeight.trim() !== '') settings.qwen_ref_weight = qwenRefWeight; else delete settings.qwen_ref_weight;
    if (qwenHeadwearRoom.trim() !== '') settings.qwen_headwear_room = qwenHeadwearRoom; else delete settings.qwen_headwear_room;
    settings.qwen_base_body = qwenBaseBody;   // v1.197: Qwen-mode base body (underwear|nude|keep)
    settings.klein_cleanup = cleanup || 'gentle';
    settings.klein_steps = kSteps || 6;
    settings.klein_pose_steps = poseSteps || 8;
    settings.klein_pose_cleanup = poseCleanup || 'gentle';
    if (poseFr.trim() !== '') settings.klein_pose_face_refine = poseFr;
    else delete settings.klein_pose_face_refine;
    if (poseFrDenoise.trim() !== '') settings.klein_pose_face_refine_denoise = poseFrDenoise;
    else delete settings.klein_pose_face_refine_denoise;
    if (poseFrSteps.trim() !== '') settings.klein_pose_face_refine_steps = poseFrSteps;
    else delete settings.klein_pose_face_refine_steps;
    if (poseFrGuide.trim() !== '') settings.klein_pose_face_refine_guide = poseFrGuide;
    else delete settings.klein_pose_face_refine_guide;
    if (poseRefEnd.trim() !== '') settings.klein_pose_ref_end = poseRefEnd;
    else delete settings.klein_pose_ref_end;
    settings.klein_pose_body_match = poseBodyMatch ? 'on' : 'off';   // pose-only; base sets keep their own klein_body_match
    settings.klein_face_crop_ref = poseFaceCropRef ? 'on' : 'off';
    if (poseInput.trim() !== '') settings.klein_pose_input = poseInput;
    else delete settings.klein_pose_input;
    if (poseSlock.trim() !== '') settings.klein_pose_structure_lock = poseSlock;
    else delete settings.klein_pose_structure_lock;
    if (poseSource.trim() !== '') settings.klein_pose_source = poseSource;
    else delete settings.klein_pose_source;
    if (dressStrength.trim() !== '') settings.klein_clothes_strength = dressStrength; else delete settings.klein_clothes_strength;
    if (dressRefEnd.trim() !== '') settings.klein_clothes_ref_end = dressRefEnd; else delete settings.klein_clothes_ref_end;
    if (dressSteps.trim() !== '') settings.klein_clothes_steps = dressSteps; else delete settings.klein_clothes_steps;
    if (dressGuide.trim() !== '') settings.klein_clothes_guidance = dressGuide; else delete settings.klein_clothes_guidance;
    if (dressNeg.trim() !== '') settings.klein_clothes_negative = dressNeg; else delete settings.klein_clothes_negative;
    if (dressCons) settings.klein_clothes_consistency = 'on'; else delete settings.klein_clothes_consistency;
    if (!dressIdLock) settings.klein_clothes_identity_lock = 'off'; else delete settings.klein_clothes_identity_lock;
    if (!garmentClean) settings.klein_clothes_clean_garment = 'off'; else delete settings.klein_clothes_clean_garment;
    if (qwenSteps.trim() !== '') settings.qwen_steps = qwenSteps; else delete settings.qwen_steps;
    if (qwenCfg.trim() !== '') settings.qwen_cfg = qwenCfg; else delete settings.qwen_cfg;
    if (qwenClothesLora.trim() !== '') settings.qwen_clothes_lora_strength = qwenClothesLora; else delete settings.qwen_clothes_lora_strength;
    if (qwenPoseLora.trim() !== '') settings.qwen_pose_lora_strength = qwenPoseLora; else delete settings.qwen_pose_lora_strength;
    if (qwenTarget.trim() !== '') settings.qwen_target_size = qwenTarget; else delete settings.qwen_target_size;
    if (qwenCreateMode.trim() !== '') settings.qwen_create_mode = qwenCreateMode; else delete settings.qwen_create_mode;
    if (qwenCreateSteps.trim() !== '') settings.qwen_create_steps = qwenCreateSteps; else delete settings.qwen_create_steps;
    if (qwenCreateCfg.trim() !== '') settings.qwen_create_cfg = qwenCreateCfg; else delete settings.qwen_create_cfg;
    if (!qwenCreateQL) settings.qwen_create_quality_loras = 'off'; else delete settings.qwen_create_quality_loras;
    if (debugOn) settings.studio_debug = 'on'; else delete settings.studio_debug;
    if (poseMode === 'simple') settings.klein_pose_mode = 'simple'; else delete settings.klein_pose_mode;
    if (simpleRef === 'skeleton') settings.klein_pose_simple_ref = 'skeleton'; else delete settings.klein_pose_simple_ref;
    if (simpleQuality !== 'balanced') settings.klein_pose_simple_quality = simpleQuality; else delete settings.klein_pose_simple_quality;
    if (poseLora.trim() !== '') settings.klein_pose_lora = poseLora;
    else delete settings.klein_pose_lora;
    if (poseLoraStr.trim() !== '') settings.klein_pose_lora_strength = poseLoraStr;
    else delete settings.klein_pose_lora_strength;
    settings.klein_consistency_lora = consLora ? 'on' : 'off';
    if (consLoraStr.trim() !== '') settings.klein_consistency_lora_strength = consLoraStr;
    else delete settings.klein_consistency_lora_strength;
    if (posePu.trim() !== '') settings.klein_pose_pulid = posePu;
    else delete settings.klein_pose_pulid;
    if (posePuStr.trim() !== '') settings.klein_pose_pulid_strength = posePuStr;
    else delete settings.klein_pose_pulid_strength;
    if (Object.keys(presets).length) settings.klein_presets = presets;
    else delete settings.klein_presets;
    settings.klein_refbase_ref_end = rbEnd || '0.85';
    settings.klein_body_match_strength = bodyMatch || '1.6';
    settings.klein_body_match_keep = bodyKeep || 'person';
    settings.klein_consistent_skin = consistentSkin ? 'on' : 'off';
    settings.klein_canvas_width = canvasW || '1024';
    settings.klein_base_face_refine = baseFr ? 'on' : 'off';
    if (baseFrDenoise.trim() !== '') settings.klein_base_face_refine_denoise = baseFrDenoise;
    else delete settings.klein_base_face_refine_denoise;
    if (baseFrSteps.trim() !== '') settings.klein_base_face_refine_steps = baseFrSteps;
    else delete settings.klein_base_face_refine_steps;
    settings.klein_base_consistency_lora = baseConsLora ? 'on' : 'off';
    if (baseConsLoraStr.trim() !== '') settings.klein_base_consistency_lora_strength = baseConsLoraStr;
    else delete settings.klein_base_consistency_lora_strength;
    settings.klein_base_turnaround_lora = baseTurnLora ? 'on' : 'off';
    if (baseTurnLoraName.trim() !== '') settings.klein_base_turnaround_lora_name = baseTurnLoraName.trim();
    else delete settings.klein_base_turnaround_lora_name;
    if (baseTurnLoraStr.trim() !== '') settings.klein_base_turnaround_lora_strength = baseTurnLoraStr;
    else delete settings.klein_base_turnaround_lora_strength;
    if (baseTurnLoraTrig.trim() !== '') settings.klein_base_turnaround_lora_trigger = baseTurnLoraTrig.trim();
    else delete settings.klein_base_turnaround_lora_trigger;
    settings.klein_base_consistent_seed = baseMatchViews ? 'on' : 'off';
    settings.klein_sam_cleanup = samClean ? 'on' : 'off';
    if (samPrompt.trim() !== '') settings.klein_sam_cleanup_prompt = samPrompt;
    else delete settings.klein_sam_cleanup_prompt;
    if (samThresh.trim() !== '') settings.klein_sam_cleanup_threshold = samThresh;
    else delete settings.klein_sam_cleanup_threshold;
    settings.klein_run_base_clothing = runBaseClothing;
    settings.enhance_on = enhanceOn ? 'on' : 'off';
    settings.enhance_method = enhanceMethod;
    settings.enhance_model = enhanceModel;
    settings.enhance_sharpen = enhanceSharpen;
    settings.enhance_max_side = enhanceMaxSide;
    settings.enhance_by_char = enhanceByChar;
    settings.base_enhance_on = baseEnhanceOn ? 'on' : 'off';
    settings.base_enhance_method = baseEnhanceMethod;
    settings.base_enhance_model = baseEnhanceModel;
    settings.base_enhance_sharpen = baseEnhanceSharpen;
    settings.base_enhance_max_side = baseEnhanceMaxSide;
    settings.klein_switch_style_on = switchStyleOn ? 'on' : 'off';
    settings.klein_switch_style = switchStyle;
    settings.klein_switch_style_custom = switchStyleCustom;
    settings.klein_switch_style_strength = switchStyleStrength;
    settings.klein_switch_style_realism = switchStyleRealism ? 'on' : 'off';
    return settings;
  };

  const saveHost = async () => {
    setSavingHost(true);
    try {
      const h = await api.setHost(null, buildSettings());
      setHostInfo(h);
      if (h.online) {
        try { setCtx(await api.getContextLists()); } catch { /* ignore */ }
        try { setHostChars(parseCharacters(await api.getVnccsCharacters())); } catch { /* ignore */ }
        try { setEmotionOpts(parseEmotions(await api.getEmotions())); } catch { /* ignore */ }
      }
    } catch (e) {
      setErrMsg(`Save host failed: ${(e as Error).message}`);
    } finally {
      setSavingHost(false);
    }
  };

  // Auto-persist the ⚙ Settings on change (debounced) so they survive a refresh
  // and carry into the next generation — no need to remember "Save host".
  useEffect(() => {
    if (!settingsLoaded.current) return;
    const t = setTimeout(() => {
      // Persist quietly — do NOT setHostInfo here.  Re-storing host state on every
      // enhance/base-enhance option change churns the availability badge (top of
      // screen) into a reconnect and briefly hides the host?.online-gated enhance
      // panels.  The badge has its own 15s refresh and the enhance/base-enhance
      // runs resolve the worker at click-time, so no live host check is needed
      // just because an upscale option changed.
      api.setHost(null, buildSettings()).catch(() => { /* best-effort */ });
    }, 700);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editModel, genMode, genModel, genSteps, genCfg, genSampler, genScheduler, genSeed,
      kpMode, kpStrength, frMode, frDenoise, frSteps, frGuide, baseClothing, faceKind, styleCustom, runBaseClothing, lockBase, baseMode, baseSetAnchor, cleanup, kSteps, poseSteps, poseCleanup, poseFr, poseFrDenoise, poseFrSteps, poseFrGuide, poseRefEnd, poseInput, poseSlock, poseLora, poseLoraStr, consLora, consLoraStr, posePu, posePuStr, presets, dressStrength, dressRefEnd, dressSteps, dressGuide, dressNeg, dressCons, dressIdLock, garmentClean, qwenSteps, qwenCfg, qwenClothesLora, qwenPoseLora, qwenTarget, qwenCreateMode, qwenCreateSteps, qwenCreateCfg, qwenCreateQL, debugOn, poseMode, simpleRef, simpleQuality, rbEnd, bodyMatch, bodyKeep, consistentSkin, canvasW, baseFr, baseFrDenoise, baseFrSteps, baseConsLora, baseConsLoraStr, baseMatchViews, samClean, samPrompt, samThresh,
      enhanceOn, enhanceMethod, enhanceModel, enhanceSharpen, enhanceMaxSide, enhanceByChar,
      baseEnhanceOn, baseEnhanceMethod, baseEnhanceModel, baseEnhanceSharpen, baseEnhanceMaxSide,
      switchStyleOn, switchStyle, switchStyleCustom, switchStyleStrength, switchStyleRealism,
      createEngine, clothesSub, emotionsSub, baseDeriveMethod, qwenRefWeight, qwenHeadwearRoom, qwenBaseBody]);

  const resetSettings = () => {
    setEditModel(''); setGenMode(''); setGenModel(''); setGenSteps(''); setGenCfg('');
    setGenSampler(''); setGenScheduler(''); setGenSeed('');
    setKpMode('off'); setKpStrength(''); setFrMode('auto'); setFrDenoise(''); setFrSteps('');
    setBaseClothing('strip'); setFaceKind('auto'); setRunBaseClothing('');
    setLockBase(true);
    setBaseMode('single'); setReproSeed(''); setBaseSetAnchor('fresh'); setBaseDeriveMethod('reference'); setCreateEngine('klein');
    setCleanup('gentle'); setKSteps(6); setPoseSteps(8); setPoseCleanup('gentle'); setRbEnd('0.85');
    setBaseFr(true); setBaseFrDenoise(''); setBaseFrSteps('');
    setBaseConsLora(false); setBaseConsLoraStr(''); setBaseMatchViews(true);
    setBaseTurnLora(false); setBaseTurnLoraName(''); setBaseTurnLoraStr(''); setBaseTurnLoraTrig(''); setQwenRefWeight(''); setQwenHeadwearRoom(''); setEmotionsSub('klein'); setQwenBaseBody('underwear');
    setSamClean(false); setSamPrompt(''); setSamThresh('');
    // the debounced auto-save effect persists these defaults
  };

  const busy = phase === 'submitting' || phase === 'polling' || phase === 'ingesting';
  const [runStarted, setRunStarted] = useState(0);   // Date.now() when the current multi-chunk run began
  const [nowTick, setNowTick] = useState(0);         // 1s heartbeat that drives every live ⏱ in the status panel
  useEffect(() => {
    if (!busy) return;
    setNowTick(Date.now());
    const id = setInterval(() => setNowTick(Date.now()), 1000);
    return () => clearInterval(id);
  }, [busy]);

  const selectedPoseSet = useMemo(() => {
    const fromDefaults = poseDefaults
      .filter((p) => !removedDefaults.has(p.index) && defaultSel.has(p.index))
      .map((p) => p.pose);
    const extras = extraPoses.map((p) => p.pose);
    return [...fromDefaults, ...extras];
  }, [poseDefaults, defaultSel, removedDefaults, extraPoses]);
  const selectedPoseNames = useMemo(() => {
    const fromDefaults = poseDefaults
      .filter((p) => !removedDefaults.has(p.index) && defaultSel.has(p.index))
      .map((p) => p.name);
    return [...fromDefaults, ...extraPoses.map((p) => p.name)];
  }, [poseDefaults, defaultSel, removedDefaults, extraPoses]);

  // v1.193/v1.198: one-click 🧊 Mesh turnaround preset. Clones a real default pose
  // (for valid bone NAMES the renderer needs) but ZEROES every bone rotation -> the
  // mannequin's neutral rest A-pose (arms clear, no hand-over-chest stance), then
  // re-aims the 3D mannequin per view via modelRotation (front 0°, right 90°, left
  // −90°, back 180°). Those 4 views are the clean multi-view input for 3D meshing.
  const addMeshTurnaround = () => {
    const src = poseDefaults[0]?.pose;
    if (!src) { setPoseSetMsg('⚠ No poses loaded yet — connect a worker so the pose set populates, then try again.'); return; }
    const views: Array<[string, number[]]> = [
      ['front', [0, 0, 0]], ['right', [0, 90, 0]], ['left', [0, -90, 0]], ['back', [0, 180, 0]]];
    const stamp = Date.now();
    const meshPoses: ExtraPose[] = views.map(([v, rot], i) => {
      const clone = JSON.parse(JSON.stringify(src)) as Record<string, unknown>;
      const srcBones = (clone.bones as Record<string, unknown>) || {};
      // zero every bone rotation -> neutral rest A-pose (not pose 0's stance)
      clone.bones = Object.fromEntries(Object.keys(srcBones).map((k) => [k, [0, 0, 0]]));
      clone.modelRotation = rot;
      return { id: `mesh_${v}_${stamp}_${i}`, name: `🧊 Mesh ${v}`, pose: clone };
    });
    setDefaultSel(new Set());          // a mesh set = ONLY the four turnaround views
    setExtraPoses(meshPoses);
    setPoseSetMsg('🧊 Mesh turnaround selected — front / right / left / back (neutral A-pose). Generate to build the 3D-mesh views.');
  };

  const savePoseSet = async () => {
    setPoseSetMsg('Saving…');
    try {
      const settings: Record<string, unknown> = { ...(host?.settings || {}) };
      settings.pose_set = { removed: Array.from(removedDefaults), extras: extraPoses };
      const h = await api.setHost(null, settings);
      setHostInfo(h);
      setPoseSetMsg('✓ Pose set saved — it will be preselected next time.');
    } catch (e) {
      setPoseSetMsg(`⚠ ${(e as Error).message}`);
    }
  };

  const patchChunk = (idx: number, patch: Partial<ChunkState>) =>
    setChunks((prev) => prev.map((c, i) => (i === idx
      ? { ...c, ...patch,
          ...(((patch.status === 'done' || patch.status === 'error') && !c.finishedAt)
            ? { finishedAt: Date.now() } : {}) }
      : c)));

  const seedGenSettings = (): Record<string, unknown> | undefined => {
    if (seedMode === 'fixed') {
      const n = parseInt(seedVal, 10);
      return { seed_mode: 'fixed', ...(Number.isFinite(n) && n > 0 ? { seed: n } : {}) };
    }
    return undefined; // backend default: fresh random seed per run
  };
  // Per-character canvas: send the live Canvas-width value on every Klein base +
  // pose generation so this character's frame wins over the global default
  // (persisted per-character on Save; reloaded when the character is opened).
  const kleinCanvasBody = (): { canvas_w?: number } => {
    const n = parseInt(canvasW, 10);
    return (variant === 'klein' && Number.isFinite(n) && n > 0) ? { canvas_w: n } : {};
  };
  const seedRow = (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 8, flexWrap: 'wrap' }}>
      <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: '#cbd2dc' }}>
        <input type="checkbox" checked={seedMode === 'randomize'}
               onChange={(e) => setSeedMode(e.target.checked ? 'randomize' : 'fixed')} />
        🎲 New random seed each run
      </label>
      {seedMode === 'fixed' && (
        <input style={{ ...input, width: 200 }} value={seedVal}
               placeholder="seed (blank = roll once & keep)"
               onChange={(e) => setSeedVal(e.target.value.replace(/[^0-9]/g, ''))} />
      )}
      {/* v1.196: after a preview, show the seed that made it so a good one can be
          LOCKED. Locking freezes it for further previews AND the pose set (shared
          seed => base<->poses consistency). */}
      {lastPreviewSeed != null && seedMode === 'randomize' && (
        <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: '#7ee0b0' }}>
          🎲 last: {lastPreviewSeed}
          <button type="button" style={{ ...btnGhost, padding: '3px 9px', fontSize: 11.5, borderColor: '#2a4a3a', color: '#7ee0b0' }}
                  title="Lock this seed — reuse it for further previews and the pose set to keep the result consistent"
                  onClick={() => { setSeedMode('fixed'); setSeedVal(String(lastPreviewSeed)); }}>🔒 Lock this seed</button>
        </span>
      )}
      {seedMode === 'fixed' && lastPreviewSeed != null && String(lastPreviewSeed) !== seedVal.trim() && (
        <button type="button" style={{ ...btnGhost, padding: '3px 9px', fontSize: 11.5 }}
                title="Use the seed from the most recent preview"
                onClick={() => setSeedVal(String(lastPreviewSeed))}>↩ Use last ({lastPreviewSeed})</button>
      )}
    </div>
  );

  const previewStatus = (previewBusy && baseSetData?.status !== 'running') ? (
    <div style={{ marginTop: 8, marginBottom: 4 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5, fontWeight: 600, color: '#c9d3df', marginBottom: 5 }}>
        <span>⏳ Rendering preview{host?.host ? ` on ${shortHost(host.host)}` : ' on the host'}…</span>
        <span>{Math.floor(previewElapsed / 60)}:{String(previewElapsed % 60).padStart(2, '0')} elapsed</span>
      </div>
      <div style={{ position: 'relative', height: 8, background: '#0e1116', border: '1px solid #2a2f3a', borderRadius: 6, overflow: 'hidden' }}>
        <div style={{ position: 'absolute', top: 0, bottom: 0, left: '-35%', width: '35%', background: '#3b82f6', borderRadius: 6, animation: 'rbmnIndeterminate 1.3s ease-in-out infinite' }} />
      </div>
      <p style={{ color: '#6b7280', fontSize: 11, marginTop: 4, marginBottom: 0 }}>
        No live percentage from the host — Klein identity previews take a while (a 4-view base set can run several minutes). The button re-enables when it finishes.
      </p>
      <style>{'@keyframes rbmnIndeterminate { 0% { left: -35%; } 100% { left: 100%; } }'}</style>
    </div>
  ) : null;

  // v1.180: live per-view status for a base SET run — front-anchored, one tile
  // per view (front/right/left/back) showing queued → rendering on worker →
  // done, with the thumbnail streaming in the moment it lands, plus Stop.
  const baseSetPanel = baseSetData ? (() => {
    const done = baseSetData.views.filter((v) => v.state === 'done').length;
    const total = baseSetData.views.length;
    const running = baseSetData.status === 'running';
    const stIcon: Record<string, string> = { pending: '•', rendering: '…', done: '✓', error: '⚠', skipped: '⊘' };
    const stColor: Record<string, string> = { pending: '#6b7280', rendering: '#8ab4ff', done: '#5ee08a', error: '#ff8a8a', skipped: '#9aa4b2' };
    return (
      <div style={{ marginTop: 8, marginBottom: 4, border: '1px solid #34518a', background: '#101623', borderRadius: 8, padding: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
          <span style={{ fontSize: 12.5, fontWeight: 700, color: '#cbd2dc', flex: 1 }}>
            🖼 {baseSetData.base_mode === 'mesh' ? '🧊 Mesh-ready' : '4-view'} set — front-anchored · {done}/{total} views
            {baseSetData.status === 'cancelled' ? ' · stopped' : baseSetData.status === 'error' ? ' · error' : running ? ' · rendering…' : ' · done'}
          </span>
          {running
            ? <button onClick={() => stopBaseSet()}
                      style={{ ...pbtn, borderColor: '#5a2a2a', background: '#2a1414', color: '#ff9a9a', fontWeight: 700 }}>
                ⛔ Stop
              </button>
            : done > 0 && (
              <button onClick={() => saveBaseSetNow()} title="Save the current set (after any regenerates) as a new base version"
                      style={{ ...pbtn, borderColor: '#2a4a3a', background: '#12211a', color: '#7ee0b0', fontWeight: 700 }}>
                💾 Save set
              </button>
            )}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 6 }}>
          {baseSetData.views.map((v, i) => (
            <div key={v.view} style={{ border: `2px solid ${v.state === 'done' ? '#2a4a3a' : v.state === 'rendering' ? '#34518a' : '#2a2f3a'}`,
                                       borderRadius: 6, background: '#0e1116', overflow: 'hidden', textAlign: 'center' }}>
              <div style={{ height: 92, display: 'flex', alignItems: 'center', justifyContent: 'center', position: 'relative' }}>
                {v.ready
                  ? <img src={`${api.baseSetImageUrl(baseSetRunId, i)}?rev=${v.rev || 0}`} alt={v.view} title="Click to view large"
                         onClick={() => { setLightboxList([]); setLightboxMode(''); setLightboxSrc(`${api.baseSetImageUrl(baseSetRunId, i)}?rev=${v.rev || 0}`); }}
                         style={{ maxWidth: '100%', maxHeight: 92, objectFit: 'contain', cursor: 'zoom-in' }} />
                  : <span style={{ fontSize: 22, color: stColor[v.state] || '#6b7280' }}>
                      {v.state === 'rendering'
                        ? <span style={{ display: 'inline-block', animation: 'rbmnSpin 1s linear infinite' }}>◠</span>
                        : (stIcon[v.state] || '•')}
                    </span>}
              </div>
              <div style={{ fontSize: 10.5, padding: '3px 2px', textTransform: 'capitalize',
                            color: stColor[v.state] || '#9aa4b2', borderTop: '1px solid #1c2230' }}>
                {i === 0 ? '★ ' : ''}{v.view} · {v.state}
                {v.host ? <span style={{ display: 'block', color: '#6b7280', fontSize: 9.5 }}>{shortHost(v.host)}</span> : null}
              </div>
              {!running && (v.state === 'done' || v.state === 'error') && (
                <button onClick={() => regenBaseSetView(i)}
                        title="Regenerate just this view with a new seed (keeps the character, re-rolls this angle)"
                        style={{ width: '100%', border: 'none', borderTop: '1px solid #1c2230', background: '#141a24',
                                 color: '#8ab4ff', fontSize: 10.5, padding: '3px 0', cursor: 'pointer' }}>
                  ↻ Regenerate
                </button>
              )}
            </div>
          ))}
        </div>
        <p style={{ fontSize: 10.5, color: '#6b7280', margin: '6px 0 0' }}>
          ★ Front renders first, then the other views derive from it across your workers so the set matches — rotating the character, NOT re-stripping it. If a view is off, hit ↻ Regenerate on it, then 💾 Save set. Stop keeps whatever finished.
        </p>
        <style>{'@keyframes rbmnSpin { to { transform: rotate(360deg); } }'}</style>
      </div>
    );
  })() : null;

  // Klein pose runs via the central Job queue: enqueue one Job per chunk, then
  // watch each job's status.  The backend selects a worker, submits, monitors
  // and INGESTS each chunk (so we do NOT call api.ingest here).  Cancellable
  // via the Stop button (api.cancelRun -> interrupts the workers).
  // Watch a queued run's chunk jobs to completion.  This is the RESUMABLE core:
  // both a fresh enqueue and a restore-after-refresh call it.  The backend
  // ingests each chunk server-side, so we only track status + refresh the grid.
  // v1.199.22: supersede token + which char's run is on screen (so switching
  // characters stops the old poll loop and shows the new character's status).
  const watchTokRef = useRef(0);
  const displayedRunCharRef = useRef<string>('');
  const watchQueueRun = async (_rid: string, jobIds: string[], step: VNCCSStepT,
                               charName: string, started: number) => {
    const myTok = ++watchTokRef.current;
    displayedRunCharRef.current = charName;
    let cid = '';
    const refreshLib = async () => {
      if (!cid) {
        try {
          const cat = await api.getCatalog();
          const hit = cat.find((c) => c.name === charName);
          if (hit) cid = hit.character_id;
        } catch { /* best-effort */ }
        if (!cid) cid = editingCharId;
      }
      if (!cid) return;
      try {
        const r = await api.getCharacterImages(cid);
        setExistingOutputs(r.outputs || []);
        setCloOutputs(r.outputs || []);
        setEmoOutputs(r.outputs || []);
        setEmoRuns(r.emotion_runs || []);
        setPoseRuns(r.pose_runs || []);
        setEmoCharId(cid);
        setCostumesMap(r.costumes || {});
        if (!editingCharId) setEditingCharId(cid);
      } catch { /* library refresh is best-effort */ }
    };
    const idxOf = new Map(jobIds.map((id, i) => [id, i] as const));
    const terminal = new Set<string>();
    let anyDone = false;
    let anyError = false;
    const missCount = new Map<string, number>();  // consecutive poll failures per job
    const deadline = Date.now() + 2 * 60 * 60 * 1000; // 2h safety net
    while (terminal.size < jobIds.length) {
      if (watchTokRef.current !== myTok) return;   // superseded by another character's run
      let newDone = false;
      await Promise.all(jobIds.filter((id) => !terminal.has(id)).map(async (id) => {
        try {
          const jr = await api.getJob(id);
          missCount.set(id, 0);
          const idx = idxOf.get(id);
          if (idx === undefined) return;
          const s = (jr.status || '').toLowerCase();
          const wk = jr.worker_url || '';
          const rc = jr.result ? (jr.result as Record<string, unknown>)['image_count'] : undefined;
          const cnt = typeof rc === 'number' ? rc : 0;
          if (s === 'done') {
            terminal.add(id); anyDone = true; newDone = true;
            patchChunk(idx, {
              status: 'done', host: wk,
              note: cnt ? `${cnt} image${cnt === 1 ? '' : 's'} filed` : 'filed',
            });
          } else if (s === 'failed' || s === 'cancelled') {
            terminal.add(id); anyError = true;
            patchChunk(idx, { status: 'error', host: wk, note: jr.error || s });
          } else {
            patchChunk(idx, { status: 'running', host: wk });
          }
        } catch {
          // job vanished (deleted / purged / never existed after a restart): after
          // a few misses, mark it terminal so the loop can finish + clear instead
          // of spinning to the 2h deadline (which would keep the UI busy).
          const n = (missCount.get(id) || 0) + 1;
          missCount.set(id, n);
          if (n >= 4) {
            const idx = idxOf.get(id);
            terminal.add(id); anyError = true;
            if (idx !== undefined) patchChunk(idx, { status: 'error', note: 'job no longer on the queue' });
          }
        }
      }));
      if (newDone) await refreshLib();
      if (terminal.size >= jobIds.length) break;
      if (Date.now() > deadline) break;
      await new Promise((r) => setTimeout(r, 4000));
    }

    await refreshLib();

    const mins = Math.max(1, Math.round((Date.now() - started) / 60000));
    if (anyDone) {
      setIngestMsg(`Pose run filed into “${charName}” (${step}) — ${jobIds.length} chunk${jobIds.length === 1 ? '' : 's'} via the queue in ~${mins} min${anyError ? ' (some chunks failed)' : ''}.`);
    }
    setPhase(anyError && !anyDone ? 'error' : 'done');
    if (anyError && !anyDone) setErrMsg('All queued chunks failed — check the Generation Queue / worker consoles.');
    setStatusText('');
    setRunId('');
    clearActiveRun(charName);
    if (displayedRunCharRef.current === charName) displayedRunCharRef.current = '';
  };

  const runGenerateQueued = async (step: VNCCSStepT, charName: string,
                                   body: Partial<api.GenerateBody>) => {
    setErrMsg(''); setIngestMsg(''); setChunks([]); setRunId('');
    setPhase('submitting'); setStatusText('Enqueuing pose run to the Generation Queue…');
    try {
      const control_center = editModel
        ? { ...((host?.settings?.control_center as Record<string, unknown>) || {}), selected_model: editModel }
        : undefined;
      const g = await api.enqueueParallel(step, {
        character_name: charName, character_info: {}, control_center, ...body,
        // a single-pose regen passes its own pose_names; otherwise use the full selection
        pose_names: (body as { pose_names?: string[] }).pose_names
          ?? ((body.pose_set && body.pose_set.length) ? selectedPoseNames : undefined),
        max_hosts: parallelOn ? 0 : 1,
      } as api.GenerateBody & { max_hosts?: number });
      if (g.seed && seedMode === 'fixed' && !seedVal) setSeedVal(String(g.seed));
      setRunId(g.run_id);
      const jobIds = g.job_ids || [];
      if (!jobIds.length) { setPhase('error'); setErrMsg('No chunks were queued.'); setStatusText(''); clearActiveRun(charName); return; }
      const initial: ChunkState[] = jobIds.map((jid, i) => ({
        host: '', prompt_id: jid, tap_map: {},
        label: `chunk ${i + 1}`, status: 'running', images: [],
        pose_names: null, startedAt: Date.now(),
      }));
      setChunks(initial);
      setRunStarted(Date.now());
      setPhase('polling');
      setStatusText(`Queued ${jobIds.length} chunk${jobIds.length === 1 ? '' : 's'} — running through the Generation Queue…`);
      // persist so a refresh / navigation can restore this run and resume polling
      saveActiveRun({ kind: 'queue', runId: g.run_id, jobIds, step, charName, started: Date.now() });
      await watchQueueRun(g.run_id, jobIds, step, charName, Date.now());
    } catch (e) {
      clearActiveRun(charName);
      setPhase('error');
      setErrMsg((e as Error).message);
      setStatusText('');
      setRunId('');
    }
  };

  // Stop a queued run: cancels every chunk job (interrupts running workers).
  // The poll loop above then observes the jobs flip to cancelled and finishes.
  const stopRun = async () => {
    if (!runId) return;
    setStopping(true);
    setStatusText('Cancelling run — interrupting workers…');
    try {
      await api.cancelRun(runId);
    } catch (e) {
      setErrMsg(`Cancel failed: ${(e as Error).message}`);
    } finally {
      setStopping(false);
    }
  };

  // Direct (non-queue) Klein clothes/emotions run: poll each chunk's prompt_id on
  // its worker, then ingest client-side.  RESUMABLE core shared by a fresh run and
  // a restore-after-refresh: the ctx carries everything ingest needs (captured at
  // run time, NOT read from current React state), and ``alreadyIngested`` skips
  // chunks that already filed so a resume never double-ingests.
  type DirectCtx = {
    step: VNCCSStepT; charName: string; engine?: string;
    ingestExtra?: { costume?: string; emotions?: string[]; costumes?: string[] } | null;
    runSeed?: number | null; poseNamesAll?: string[] | null;
    poseSet?: Array<Record<string, unknown>> | null;
  };
  const watchDirectRun = async (initial: ChunkState[], ctx: DirectCtx,
                                started: number, alreadyIngested: Set<number>) => {
    const myTok = ++watchTokRef.current;
    displayedRunCharRef.current = ctx.charName;
    const ingested: string[] = [];
    const filed = new Set<number>(alreadyIngested);
    let lastCharId = '';
    let totalOut = 0;
    let anyError = false;
    const persist = () => saveActiveRun({
      kind: 'direct', step: ctx.step, charName: ctx.charName, engine: ctx.engine,
      ingestExtra: ctx.ingestExtra ?? null, runSeed: ctx.runSeed ?? null,
      poseNamesAll: ctx.poseNamesAll ?? null, poseSet: ctx.poseSet ?? null,
      chunks: initial.map((c) => ({ host: c.host, prompt_id: c.prompt_id, tap_map: c.tap_map,
                                    label: c.label, pose_names: c.pose_names || null })),
      ingested: Array.from(filed), started,
    });
    await Promise.all(initial.map(async (c, idx) => {
      if (filed.has(idx)) { patchChunk(idx, { status: 'done', note: 'filed' }); return; }
      try {
        const res = await api.pollUntilDone(c.prompt_id, {
          host: c.host,
          onTick: (r) => patchChunk(idx, { images: r.images }),
        });
        patchChunk(idx, { images: res.images });
        if (res.status === 'error') {
          anyError = true;
          patchChunk(idx, { status: 'error', note: 'job errored on the worker' });
          return;
        }
        patchChunk(idx, { status: 'ingesting' });
        const ing = await api.ingest({
          prompt_id: c.prompt_id, host: c.host, character_name: ctx.charName, step: ctx.step, tap_map: c.tap_map,
          costume: ctx.ingestExtra?.costume || null,
          emotions: ctx.ingestExtra?.emotions || null,
          costumes: ctx.ingestExtra?.costumes || null,
          seed: ctx.runSeed ?? null,
          pose_names: (ctx.poseSet && ctx.poseSet.length) ? (ctx.poseNamesAll || null) : null,
          pose_set: ctx.poseSet || null,
          postprocess: ctx.engine === 'klein' ? 'chroma' : null,
          chunk_pose_names: c.pose_names || null,
          engine: ctx.engine || null,
        });
        totalOut += Object.values(ing.outputs || {}).reduce((a, v) => a + v.length, 0);
        if (!ingested.includes(ing.ref)) ingested.push(ing.ref);
        lastCharId = ing.character_id;
        filed.add(idx); persist();      // mark filed so a later resume skips it
        patchChunk(idx, { status: 'done' });
      } catch (e) {
        anyError = true;
        patchChunk(idx, { status: 'error', note: (e as Error).message });
      }
    }));

    const mins = Math.max(1, Math.round((Date.now() - started) / 60000));
    if (totalOut > 0) {
      setIngestMsg(`Cataloged “${ingested.join('”, “') || ctx.charName}” (${ctx.step}) — ${totalOut} images from ${initial.length} worker${initial.length === 1 ? '' : 's'} in ~${mins} min${ctx.runSeed ? ` · seed ${ctx.runSeed}` : ''}.`);
      const cid = lastCharId || editingCharId;
      if (cid) {
        try {
          const r = await api.getCharacterImages(cid);
          setExistingOutputs(r.outputs || []);
          setCloOutputs(r.outputs || []);
          setEmoOutputs(r.outputs || []);
          setEmoRuns(r.emotion_runs || []);
          setPoseRuns(r.pose_runs || []);
          setEmoCharId(cid);
          setCostumesMap(r.costumes || {});
          if (!editingCharId) setEditingCharId(cid);
        } catch { /* library refresh is best-effort */ }
      }
    }
    setPhase(anyError && totalOut === 0 ? 'error' : 'done');
    if (watchTokRef.current !== myTok) return;    // superseded — another char owns the display
    if (anyError && totalOut === 0) setErrMsg('All chunks failed — check the worker consoles.');
    setStatusText('');
    clearActiveRun(ctx.charName);
    if (displayedRunCharRef.current === ctx.charName) displayedRunCharRef.current = '';
  };

  const runGenerate = async (step: VNCCSStepT, charName: string, body: Partial<api.GenerateBody>,
                             ingestExtra?: { costume?: string; emotions?: string[]; costumes?: string[] }) => {
    // Character-Studio runs now go through the central Job queue (queue-managed
    // + cancellable): native creator/cloner/clothes/emotions, and Klein poses.
    // Klein emotions (crop-and-stitch) still use the direct path below.
    const _eng = (body as { engine?: string }).engine;
    // v1.199.20: Qwen EMOTIONS go through the queue (cancel/retry/threading); Qwen
    // pose sets/clothes stay on the direct path.
    const qwenEmotionsQueued = _eng === 'qwen' && step === 'emotions';
    if (qwenEmotionsQueued || (_eng !== 'qwen' && (_eng !== 'klein' || step === 'creator' || step === 'cloner'))) {
      return runGenerateQueued(step, charName, body);
    }
    setErrMsg(''); setIngestMsg(''); setChunks([]);
    setPhase('submitting'); setStatusText('Assembling VNCCS graph(s) and submitting…');
    try {
      const control_center = editModel
        ? { ...((host?.settings?.control_center as Record<string, unknown>) || {}), selected_model: editModel }
        : undefined;
      const g = await api.generateParallel(step, {
        character_name: charName, character_info: {}, control_center, ...body,
        pose_names: (body.pose_set && body.pose_set.length) ? selectedPoseNames : undefined,
        max_hosts: parallelOn ? 0 : 1,
      } as api.GenerateBody & { max_hosts?: number });
      if (g.seed && seedMode === 'fixed' && !seedVal) setSeedVal(String(g.seed));
      const runSeed = g.seed;
      const initial: ChunkState[] = g.chunks.map((c) => ({
        host: c.host, prompt_id: c.prompt_id, tap_map: c.tap_map,
        label: c.label, status: 'running', images: [],
        pose_names: c.pose_names || null, startedAt: Date.now(),
      }));
      setChunks(initial);
      setRunStarted(Date.now());
      if (g.errors?.length) setErrMsg(`⚠ ${g.errors.length} worker(s) failed to accept a chunk (running on the rest).`);
      setPhase('polling');
      setStatusText(`Generating on ${initial.length} worker${initial.length === 1 ? '' : 's'}…`);
      const ctx: DirectCtx = {
        step, charName, engine: _eng, ingestExtra: ingestExtra || null, runSeed: runSeed ?? null,
        poseNamesAll: (body.pose_set && body.pose_set.length) ? selectedPoseNames : null,
        poseSet: (body.pose_set as Array<Record<string, unknown>> | undefined) || null,
      };
      // persist so a refresh / navigation can restore this run and resume polling
      saveActiveRun({
        kind: 'direct', step, charName, engine: _eng, ingestExtra: ingestExtra || null,
        runSeed: runSeed ?? null, poseNamesAll: ctx.poseNamesAll, poseSet: ctx.poseSet,
        chunks: initial.map((c) => ({ host: c.host, prompt_id: c.prompt_id, tap_map: c.tap_map,
                                      label: c.label, pose_names: c.pose_names || null })),
        ingested: [], started: Date.now(),
      });
      await watchDirectRun(initial, ctx, Date.now(), new Set());
    } catch (e) {
      clearActiveRun(charName);
      setPhase('error');
      const msg = (e as Error).message;
      setErrMsg(msg.includes('Timed out')
        ? `${msg} — the job may STILL be running on the host; check the worker console. Fewer poses / Upscaler=Off make runs much faster.`
        : msg);
      setStatusText('');
    }
  };

  // Resume an in-flight run after a browser refresh / route change: if a run
  // descriptor was persisted, restore the status view and re-attach the poll
  // loop (queue jobs keep running + ingesting server-side; direct chunks finish
  // on the workers).  Runs once on mount.
  // Manually clear the run view (non-destructive: any real server-side work keeps
  // going).  Escape hatch if a restored run ever looks stuck.
  const dismissRun = () => {
    watchTokRef.current++;                       // stop any poll loop
    clearActiveRun(displayedRunCharRef.current);
    displayedRunCharRef.current = '';
    setPhase('idle'); setChunks([]); setRunId(''); setStatusText('');
  };

  // v1.199.22: resume the VIEWED character's run — on mount and whenever the active
  // tab's character changes. Queued jobs are durable server-side; direct chunks finish
  // on the workers. Supersedes any previously-shown run via watchTokRef so switching
  // characters stops the old poll loop and shows the new character's status.
  const resumeRun = async (d: ActiveRunDesc) => {
    const myTok = ++watchTokRef.current;
    displayedRunCharRef.current = d.charName;
    const refreshFor = async (charName: string) => {
      try {
        const cat = await api.getCatalog();
        const hit = cat.find((c) => c.name === charName);
        const cid = hit?.character_id || editingCharId;
        if (!cid) return;
        const r = await api.getCharacterImages(cid);
        setExistingOutputs(r.outputs || []); setEmoOutputs(r.outputs || []); setCloOutputs(r.outputs || []);
        setEmoRuns(r.emotion_runs || []); setPoseRuns(r.pose_runs || []); setCostumesMap(r.costumes || {});
      } catch { /* best-effort */ }
    };
    if (d.kind === 'queue') {
      if (!d.jobIds?.length) { clearActiveRun(d.charName); displayedRunCharRef.current = ''; return; }
      let anyLive = false;
      await Promise.all(d.jobIds.map(async (id) => {
        try { const jr = await api.getJob(id); const st = (jr.status || '').toLowerCase();
          if (st === 'pending' || st === 'running' || st === 'queued') anyLive = true; } catch { /* gone */ }
      }));
      if (watchTokRef.current !== myTok) return;   // switched away during the check
      if (!anyLive) { clearActiveRun(d.charName); displayedRunCharRef.current = ''; await refreshFor(d.charName); return; }
      setRunId(d.runId);
      setChunks(d.jobIds.map((jid, i) => ({
        host: '', prompt_id: jid, tap_map: {}, label: `chunk ${i + 1}`,
        status: 'running', images: [], pose_names: null, startedAt: d.started || Date.now(),
      })));
      setRunStarted(d.started || Date.now());
      setPhase('polling');
      setStatusText('Reconnected to a running queue job — resuming live status…');
      void watchQueueRun(d.runId, d.jobIds, d.step, d.charName, d.started);
    } else if (d.kind === 'direct') {
      if (!d.chunks?.length || (d.ingested?.length || 0) >= d.chunks.length) {
        clearActiveRun(d.charName); displayedRunCharRef.current = ''; await refreshFor(d.charName); return;
      }
      const done = new Set(d.ingested || []);
      const initial: ChunkState[] = d.chunks.map((c, i) => ({
        host: c.host, prompt_id: c.prompt_id, tap_map: c.tap_map,
        label: c.label || `chunk ${i + 1}`, status: done.has(i) ? 'done' : 'running',
        images: [], pose_names: c.pose_names || null, startedAt: d.started || Date.now(),
      }));
      if (watchTokRef.current !== myTok) return;
      setChunks(initial);
      setRunStarted(d.started || Date.now());
      setPhase('polling');
      setStatusText('Reconnected to a running generation — resuming live status…');
      const ctx: DirectCtx = {
        step: d.step, charName: d.charName, engine: d.engine, ingestExtra: d.ingestExtra ?? null,
        runSeed: d.runSeed ?? null, poseNamesAll: d.poseNamesAll ?? null, poseSet: d.poseSet ?? null,
      };
      void watchDirectRun(initial, ctx, d.started, done);
    }
  };
  useEffect(() => {
    const viewed = (tab === 'emotions' ? emoChar
      : tab === 'clothes' ? clothesChar
      : (createSub === 'clone' ? cloneName : name)).trim();
    if (displayedRunCharRef.current === viewed) return;   // already showing this character's run
    const d = viewed ? loadActiveRunFor(viewed) : null;
    if (!d) {
      // viewing a character with no active run — drop any stale status view (the other
      // character's run keeps going server-side + stays persisted for when you return).
      if (displayedRunCharRef.current) {
        watchTokRef.current++;
        displayedRunCharRef.current = '';
        setPhase('idle'); setChunks([]); setRunId(''); setStatusText('');
      }
      return;
    }
    void resumeRun(d);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, emoChar, clothesChar, name, cloneName, createSub]);

  // --- Create-tab actions -------------------------------------------------
  // v1.180: live, parallel, cancellable base-SET generation (4-view / mesh).
  // Front renders first (the anchor), then right/left/back derive from it across
  // workers; we poll per-view status and stream thumbnails. Stop keeps finished
  // views. Falls back to the plain synchronous doPreview for Single.
  const runBaseSet = async (clone: boolean) => {
    const nm = (clone ? cloneName : name).trim();
    if (!nm || !host?.online) return;
    setPreviewBusy(true); setErrMsg(''); setSaveMsg(''); setBaseSetData(null); setBaseSetRunId('');
    try {
      const start = await api.startBaseSet({
        character_name: nm,
        character_info: (clone ? (cloneInfo || {}) : info) as Record<string, unknown>,
        base_mode: baseMode === 'mesh' ? 'mesh' : 'set',
        engine: 'klein',
        cloner_images: clone ? (cloneRefsForGen() as unknown as Array<Record<string, unknown>>) : undefined,
        nsfw: clone ? !!cloneInfo?.nsfw : !!info.nsfw,
        background,
        face_kind: faceKind, style_custom: styleCustom,
        base_clothing: runBaseClothing || undefined,
        use_active_base: baseSetAnchor === 'approved',
        derive_method: baseDeriveMethod,
        ...(reproSeed ? { seed: Number(reproSeed) } : {}),
        ...kleinCanvasBody(),
      });
      setBaseSetRunId(start.run_id);
      let misses = 0;
      for (;;) {
        await new Promise((r) => setTimeout(r, 1500));
        let s: import('./vnccsNativeApi').BaseSetStatusT | null = null;
        try { s = await api.baseSetStatus(start.run_id); misses = 0; }
        catch { if (++misses > 20) { setErrMsg('Lost contact with the set run (backend restarted?). Refresh to check results.'); break; } continue; }
        setBaseSetData(s);
        if (s.status === 'running') continue;
        if (s.status === 'error') setErrMsg(`Set generation failed: ${s.error || 'unknown error'}`);
        if (s.version) {
          const v = s.version;
          setBaseVersions((prev) => {
            const next = v.character_id === editingCharId ? [...prev, v.version] : [v.version];
            setVerIdx(next.length - 1);
            return next;
          });
          setEditingCharId(v.character_id);
          setActiveBase(v.active);
          setPreviewImg('');
        }
        break;
      }
    } catch (e) {
      setErrMsg(`Set generation failed: ${(e as Error).message}`);
    } finally {
      setPreviewBusy(false);
    }
  };
  const stopBaseSet = async () => {
    if (!baseSetRunId) return;
    try { await api.cancelBaseSet(baseSetRunId); setSaveMsg('Stopping — views already finished will be kept.'); }
    catch (e) { setErrMsg(`Stop failed: ${(e as Error).message}`); }
  };
  // v1.182: regenerate ONE view of a finished set (new seed) if it came out off,
  // then poll just until that view lands again.
  const regenBaseSetView = async (idx: number) => {
    if (!baseSetRunId) return;
    try {
      await api.regenBaseSetView(baseSetRunId, idx);
      for (let n = 0; n < 120; n++) {
        await new Promise((r) => setTimeout(r, 1500));
        let s: import('./vnccsNativeApi').BaseSetStatusT | null = null;
        try { s = await api.baseSetStatus(baseSetRunId); } catch { continue; }
        setBaseSetData(s);
        const v = s.views[idx];
        if (v && (v.state === 'done' || v.state === 'error')) break;
      }
    } catch (e) { setErrMsg(`Regenerate failed: ${(e as Error).message}`); }
  };
  const saveBaseSetNow = async () => {
    if (!baseSetRunId) return;
    try {
      const r = await api.saveBaseSet(baseSetRunId);
      if (r.version) {
        const v = r.version;
        setBaseVersions((prev) => {
          const next = v.character_id === editingCharId ? [...prev, v.version] : [v.version];
          setVerIdx(next.length - 1);
          return next;
        });
        setEditingCharId(v.character_id);
        setActiveBase(v.active);
        setPreviewImg('');
        setSaveMsg('Saved the current set as a new base version.');
      }
    } catch (e) { setErrMsg(`Save failed: ${(e as Error).message}`); }
  };
  // v1.183: open the orbit 3D viewer for a character's mesh. Loads Google's
  // <model-viewer> web component from a CDN once (browser needs internet; if it
  // can't load, the lightbox falls back to the GLB download link).
  const open3dViewer = (charName: string) => {
    if (!charName) return;
    const ce = (globalThis as { customElements?: { get?: (n: string) => unknown } }).customElements;
    if (ce?.get?.('model-viewer')) { setView3dReady(true); setView3dChar(charName); return; }
    if (!document.querySelector('script[data-model-viewer]')) {
      const s = document.createElement('script');
      s.type = 'module';
      s.src = 'https://unpkg.com/@google/model-viewer@3.5.0/dist/model-viewer.min.js';
      s.setAttribute('data-model-viewer', '1');
      s.onload = () => setView3dReady(true);
      document.head.appendChild(s);
    }
    setView3dChar(charName);
  };

  // v1.184: one action → BOTH the SFW (underwear) and NSFW (nude) STRIPPED base
  // (single front) from the same references — the "perfect stripper" pair. Runs
  // the base twice with base_clothing=strip; each files as its own base version.
  const doStripPair = async (clone: boolean) => {
    const nm = (clone ? cloneName : name).trim();
    if (!nm || !host?.online) return;
    setPreviewBusy(true); setErrMsg(''); setSaveMsg(''); setBaseSetData(null); setBaseSetRunId('');
    try {
      for (const nsfwv of [false, true]) {
        const r = await api.generatePreview({
          character_name: nm,
          character_info: (clone ? (cloneInfo || {}) : info) as VNCCSCharacterInfoT,
          nsfw: nsfwv, background,
          ...(variant === 'klein' ? { engine: 'klein', face_kind: faceKind, style_custom: styleCustom, base_mode: 'single' } : {}),
          ...(clone ? { cloner_images: cloneRefsForGen() as unknown as Array<Record<string, unknown>> } : {}),
          base_clothing: 'strip',
          ...(reproSeed ? { gen_settings: { seed: Number(reproSeed) } } : {}),
          ...kleinCanvasBody(),
        });
        if (r.image) setPreviewImg(`data:image/png;base64,${r.image}`);
        if (r.version) {
          const v = r.version;
          setBaseVersions((prev) => {
            const next = v.character_id === editingCharId ? [...prev, v.version] : [v.version];
            setVerIdx(next.length - 1);
            return next;
          });
          setEditingCharId(v.character_id);
          setActiveBase(v.active);
        }
        setSaveMsg(nsfwv ? 'NSFW (nude) strip base saved.' : 'SFW (underwear) strip base saved — generating NSFW…');
      }
      setSaveMsg('SFW + NSFW strip pair saved as two base versions.');
    } catch (e) {
      setErrMsg(`Strip pair failed: ${(e as Error).message}`);
    } finally {
      setPreviewBusy(false);
    }
  };

  const doPreview = async () => {
    if (!name.trim() || !host?.online) return;
    if (kleinCreate && variant === 'klein' && baseMode !== 'single') { await runBaseSet(false); return; }
    setPreviewBusy(true); setErrMsg(''); setSaveMsg(''); setBaseSetData(null); setBaseSetRunId('');
    try {
      const r = !kleinCreate
        ? await api.generateQwenCreatePreview({
            character_name: name.trim(), character_info: info as Record<string, unknown>,
            nsfw: qwenBaseBody === 'nude', background,   // v1.197: Qwen-mode base body control
            mode: qwenCreateMode || undefined,
            ...(seedMode === 'fixed' && seedVal.trim() ? { seed: Number(seedVal) } : {}),
          })
        : await api.generatePreview({
        character_name: name.trim(), character_info: info, nsfw: !!info.nsfw, background,
        ...(variant === 'klein' ? { engine: 'klein', face_kind: faceKind, style_custom: styleCustom, base_mode: baseMode } : {}),
        ...(reproSeed ? { gen_settings: { seed: Number(reproSeed) } } : {}),
        ...kleinCanvasBody(),
      });
      if ((r as { seed?: number }).seed) setLastPreviewSeed((r as { seed?: number }).seed as number);
      setPreviewImg(`data:image/png;base64,${r.image}`);
      if (r.version) {
        setBaseVersions((prev) => {
          // switching characters -> start a fresh version list instead of mixing
          const next = r.version!.character_id === editingCharId ? [...prev, r.version!.version] : [r.version!.version];
          setVerIdx(next.length - 1);
          return next;
        });
        setEditingCharId(r.version.character_id);
        setActiveBase(r.version.active);
      }
    } catch (e) {
      const m = (e as Error).message;
      setErrMsg(`Preview failed: ${m}${/Method Not Allowed|Not Found/i.test(m)
        ? ' — the app backend is running OLD code; restart the RBMN backend to pick up the new /preview route.' : ''}`);
    } finally {
      setPreviewBusy(false);
    }
  };

  // Clone-tab preview: ONE default-pose render from the uploaded references
  // (Klein: multi-ref + face crop + PuLID chain) — review the likeness before
  // committing to a full pose run.  Files as a base VERSION like ✨ on New.
  const doClonePreview = async () => {
    if (!cloneName.trim() || !host?.online || !cloneRefs.length) return;
    if (kleinCreate && variant === 'klein' && baseMode !== 'single') { await runBaseSet(true); return; }
    setPreviewBusy(true); setErrMsg(''); setSaveMsg(''); setBaseSetData(null); setBaseSetRunId('');
    try {
      const r = !kleinCreate
        ? await api.generateQwenClonePreview({
            character_name: cloneName.trim(),
            cloner_images: cloneRefsForGen() as unknown as Array<Record<string, unknown>>,
            // v1.197: base body (SFW underwear / NSFW nude / keep) is set by the Qwen-mode
            // control since the Klein SFW/NSFW toggle is hidden here. Nude -> nsfw flag
            // (drives the two-pass full strip on the backend).
            character_info: { ...(cloneInfo || {}), nsfw: qwenBaseBody === 'nude' } as Record<string, unknown>,
            background,
            base_clothing: qwenBaseBody === 'keep' ? 'keep' : 'strip',
            ref_weight: qwenRefWeight.trim() !== '' ? (parseFloat(qwenRefWeight) || undefined) : undefined,
            headwear_room: qwenHeadwearRoom.trim() !== '' ? (parseFloat(qwenHeadwearRoom) || undefined) : undefined,
            ...(seedMode === 'fixed' && seedVal.trim() ? { seed: Number(seedVal) } : {}),
          })
        : await api.generatePreview({
        character_name: cloneName.trim(),
        character_info: (cloneInfo || {}) as VNCCSCharacterInfoT,
        nsfw: !!cloneInfo?.nsfw, background,
        cloner_images: cloneRefsForGen() as unknown as Array<Record<string, unknown>>,
        ...(variant === 'klein' ? { engine: 'klein', face_kind: faceKind, style_custom: styleCustom, base_mode: baseMode,
              cleanup, klein_steps: kSteps,
              ...(runBaseClothing ? { base_clothing: runBaseClothing } : {}) } : {}),
        ...(reproSeed ? { gen_settings: { seed: Number(reproSeed) } } : {}),
        ...kleinCanvasBody(),
      });
      if ((r as { seed?: number }).seed) setLastPreviewSeed((r as { seed?: number }).seed as number);
      setPreviewImg(`data:image/png;base64,${r.image}`);
      if (r.version) {
        setBaseVersions((prev) => {
          const next = r.version!.character_id === editingCharId ? [...prev, r.version!.version] : [r.version!.version];
          setVerIdx(next.length - 1);
          return next;
        });
        setEditingCharId(r.version.character_id);
        setActiveBase(r.version.active);
      }
    } catch (e) {
      setErrMsg(`Preview failed: ${(e as Error).message}`);
    } finally {
      setPreviewBusy(false);
    }
  };

  const doSaveCharacter = async () => {
    if (!name.trim()) return;
    setSaveMsg('Saving…');
    try {
      const gs = (host?.settings?.gen_settings as Record<string, unknown>) || undefined;
      const r = await api.saveCharacter({ name: name.trim(), character_info: info, gen_settings: gs || null,
                                          create_mode: 'new', variant,
                                          ...(variant === 'klein' ? { canvas_w: parseInt(canvasW, 10) || null } : {}) });
      setSaveMsg(`✓ Saved “${r.name}” to your library — reload it any time from the Library tab.`);
    } catch (e) {
      setSaveMsg(`⚠ Save failed: ${(e as Error).message}`);
    }
  };

  const canPreview = !!host?.online && !!name.trim() && !previewBusy && !busy;
  const canGenPoses = !!host?.online && !!name.trim() && selectedPoseSet.length > 0 && !busy;
  // v1.172 Simple pose mode recipe: the session's proven config, keyed off the
  // chosen 2D pose reference. Mannequin = the tuned "Realistic" combo (VNCCS
  // LoRA @0.7); Skeleton = the pure-2D stick figure through RefControl @0.9
  // (no CGI material to leak). Both: consistency stack 0.7, PuLID 1.0,
  // face refine 0.45, gentle cleanup, ref release default.
  const simplePoseSteps = simpleQuality === 'fast' ? 10 : simpleQuality === 'max' ? 20 : 14;
  const simpleOverrides = (): Record<string, unknown> | undefined =>
    poseMode !== 'simple' ? undefined : {
      klein_pose_steps: simplePoseSteps,
      klein_pose_cleanup: 'gentle',
      klein_pose_input: simpleRef === 'skeleton' ? 'skeleton' : '',
      klein_pose_lora: simpleRef === 'skeleton' ? 'refcontrol_v2_poses.safetensors' : '',
      klein_pose_lora_strength: simpleRef === 'skeleton' ? '0.9' : '0.7',
      klein_consistency_lora: 'on',
      klein_consistency_lora_strength: '0.7',
      klein_pose_ref_end: '',
      klein_pose_pulid: 'on',
      klein_pose_pulid_strength: '1.0',
      klein_pose_face_refine: 'on',
      klein_pose_face_refine_denoise: '0.45',
    };
  const effPoseSteps = poseMode === 'simple' ? simplePoseSteps : poseSteps;
  const effPoseCleanup = poseMode === 'simple' ? 'gentle' : poseCleanup;
  // v1.175 (B3): pose references from the character's rigged 3D body (clay
  // renders of the REAL shape) instead of the generic mannequin. Works in
  // both Simple and Advanced modes; backend falls back to the mannequin if
  // the character has no rigged mesh yet.
  const poseOverrides = (): Record<string, unknown> | undefined => {
    const base = simpleOverrides() || {};
    const o: Record<string, unknown> = { ...base, ...(m3dPoseOn ? { mesh3d_pose: true } : {}) };
    return Object.keys(o).length ? o : undefined;
  };
  const doGeneratePoses = () => runGenerate('creator', name.trim(), {
    character_info: info, nsfw: !!info.nsfw, background, gen_settings: seedGenSettings(),
    ...(!kleinCreate ? { engine: 'qwen', ...kleinCanvasBody() } : {}),
    ...(kleinCreate && variant === 'klein' ? { engine: 'klein' } : {}),
    ...(kleinCreate && variant === 'klein' && runBaseClothing ? { base_clothing: runBaseClothing } : {}),
    ...(kleinCreate && variant === 'klein' ? { face_kind: faceKind, style_custom: styleCustom } : {}),
    ...(kleinCreate && variant === 'klein' ? { lock_base: lockBase } : {}),
    ...(variant === 'klein' ? { cleanup: effPoseCleanup, klein_steps: effPoseSteps, consistent_skin: consistentSkin,
                                settings_overrides: poseOverrides() } : {}),
    ...kleinCanvasBody(),
    pose_set: selectedPoseSet as Array<Record<string, unknown>>,
    generator_overrides: buildGeneratorOverrides(upscaler),
  });

  // --- Clothes / Emotions / Cloner actions ---------------------------------
  const canClothes = !!host?.online && !!clothesChar.trim() && !!costumeName.trim() && !busy;
  // true = the Klein dressing pipeline drives the Clothes tab; false = the
  // VNCCS/Qwen node process (their clothes designer + mannequin sprites)
  const kleinClothes = variant === 'klein' && clothesSub === 'klein';
  // true = the Klein identity chain drives the Create tab; false (on the Klein
  // page) = the VNCCS-replica Qwen creation process
  const kleinCreate = variant !== 'klein' || createEngine === 'klein';
  const doClothes = () => {
    saveOutfitPrompts(true);  // keep the exact prompts that produced this run
    return runGenerate('clothes', clothesChar.trim(), {
      costume_name: costumeName.trim(), costume_info: costume, background, gen_settings: seedGenSettings(),
      pose_set: selectedPoseSet.length ? selectedPoseSet as Array<Record<string, unknown>> : undefined,
      generator_overrides: buildGeneratorOverrides(upscaler),
      // Klein clothed pose SET: reference the approved DRESSED costume version and
      // reproduce its outfit on every pose (backend forces base_clothing='keep').
      // Qwen sub-tab (v1.167): the VNCCS-replica Pass-B set, assembled app-side.
      ...(kleinClothes
        ? { engine: 'klein', face_kind: faceKind, style_custom: styleCustom, cleanup: effPoseCleanup, klein_steps: effPoseSteps,
            lock_base: true, consistent_skin: consistentSkin, settings_overrides: simpleOverrides(), ...kleinCanvasBody() }
        : variant === 'klein'
          ? { engine: 'qwen', ...kleinCanvasBody() }
          : {}),
    }, { costume: costumeName.trim() });
  };

  const emoCostumes = emoCostumeOpts.length
    ? emoCostumesSel
    : emoCostumesText.split(',').map((c) => c.trim()).filter(Boolean);
  const canEmotions = !!host?.online && !!emoChar.trim() && emoSelected.length > 0
    && ((variant === 'klein' && emotionsSub === 'klein') || emoCostumes.length > 0) && !busy;
  const doEmotions = () => runGenerate('emotions', emoChar.trim(), {
    costumes: emoCostumes,
    ...(variant === 'klein' ? { engine: emotionsSub } : {}),
    emotions: emoSelected,
    gen_settings: seedGenSettings(),
  }, { emotions: emoSelected, costumes: emoCostumes });

  const onUploadRefs = async (files: FileList | null) => {
    if (!files || !files.length) return;
    setUploading(true); setErrMsg('');
    try {
      const uploaded: api.UploadRefT[] = [];
      for (const f of Array.from(files)) {
        const u = await api.uploadReference(f);
        // default the role from the server's auto-suggestion (user can override)
        const sg = (u.role || (u.suggested_role as api.RefRole) || 'full');
        uploaded.push({ ...u, role: (['face', 'body', 'full'].includes(sg) ? sg : 'full') as api.RefRole });
      }
      setCloneRefs((prev) => [...prev, ...uploaded]);
    } catch (e) {
      setErrMsg(`Upload failed: ${(e as Error).message}`);
    } finally {
      setUploading(false);
    }
  };

  // Generate the MISSING standard turnaround views (back / left / right) from the
  // references we DO have, using Klein image-edit. Each result is added to the set
  // tagged with its angle, so the turnaround uses it directly (like a real photo).
  const genMissingViews = async () => {
    if (!host?.online || genViewBusy || !cloneRefs.length) return;
    const present = new Set(cloneRefs.map((r) => (String((r as { angle?: string }).angle || '').toLowerCase() || 'front')));
    const wanted = ['back', 'left', 'right'].filter((v) => !present.has(v));
    if (!wanted.length) {
      setErrMsg('All standard views (back / left / right) are already in the reference set.');
      return;
    }
    setGenViewBusy(true); setErrMsg('');
    try {
      const cis = cloneRefs.map((r) => ({
        name: r.name, subfolder: r.subfolder, type: r.type,
        role: r.role, angle: (r as { angle?: string }).angle,
      }));
      for (const v of wanted) {
        const gen = await api.generateRefView(cis, v);
        setCloneRefs((prev) => [...prev, {
          ...gen, role: ((gen.suggested_role as api.RefRole) || 'full') as api.RefRole, angle: v,
        } as api.UploadRefT]);
      }
    } catch (e) {
      setErrMsg(`Generate missing views failed: ${(e as Error).message}`);
    } finally {
      setGenViewBusy(false);
    }
  };

  // Effective references for the clone run: enhanced set when the toggle is on
  // (falling back to the original for any ref that wasn't enhanced), else originals.
  const cloneRefsForGen = () =>
    cloneRefs.map((r) => {
      const base = (enhanceOn && enhancedMap[r.name]) ? enhancedMap[r.name] : r;
      // role + v1.185 angle both ride from the original ref
      return { ...base, role: (r.role || 'full') as api.RefRole,
               angle: String((r as { angle?: string }).angle || '') };
    });

  // Cycle a reference's role: full -> face -> body -> full.
  const cycleRefRole = (i: number) =>
    setCloneRefs((prev) => prev.map((r, k) => {
      if (k !== i) return r;
      const order: api.RefRole[] = ['full', 'face', 'body'];
      const cur = (r.role || 'full') as api.RefRole;
      return { ...r, role: order[(order.indexOf(cur) + 1) % order.length] };
    }));
  // v1.185: tag a reference's ANGLE so a 4-view / mesh set uses THIS photo for
  // that view (left/right/back) instead of a rotated guess. '' = not a side ref.
  const cycleRefAngle = (i: number) =>
    setCloneRefs((prev) => prev.map((r, k) => {
      if (k !== i) return r;
      const order = ['', 'left', 'right', 'back'];
      const cur = String((r as { angle?: string }).angle || '');
      return { ...r, angle: order[(order.indexOf(cur) + 1) % order.length] } as typeof r;
    }));
  const roleChip = (role: api.RefRole) => {
    const map: Record<api.RefRole, { txt: string; bg: string; fg: string; bd: string }> = {
      face: { txt: 'Face', bg: '#12233f', fg: '#8ab4ff', bd: '#3b82f6' },
      body: { txt: 'Body', bg: '#0f2417', fg: '#5ee08a', bd: '#2f7d4f' },
      full: { txt: 'Full', bg: '#1e1a2e', fg: '#c4a8ff', bd: '#6b5bd0' },
    };
    return map[role] || map.full;
  };

  const doEnhanceRefs = async () => {
    if (!cloneRefs.length || !host?.online || enhanceBusy) return;
    setEnhanceBusy(true); setEnhanceMsg(''); setErrMsg('');
    const refs = cloneRefs;
    const pool = (parallelOn && vnccsHosts.length ? vnccsHosts : [host?.host || '']).filter(Boolean);
    const workers = pool.length ? pool : [host?.host || ''];
    setEnhanceStatus(refs.map((r, i) => ({ name: r.name, host: workers[i % workers.length], status: 'pending' as const, detail: '' })));
    const setOne = (k: number, patch: Partial<{ host: string; status: 'pending' | 'running' | 'done' | 'error'; detail: string }>) =>
      setEnhanceStatus((prev) => prev.map((st, m) => (m === k ? { ...st, ...patch } : st)));
    const next: Record<string, api.UploadRefT> = { ...enhancedMap };
    let done = 0; let failed = 0;
    await Promise.all(refs.map(async (r, i) => {
      const wk = workers[i % workers.length];
      setOne(i, { status: 'running', detail: `${enhanceMethod === 'seedvr2' ? 'SeedVR2' : 'GAN'} on ${shortHost(wk)}…` });
      const t0 = Date.now();
      try {
        const out = await api.enhanceReference({
          ref: r, host: wk || undefined, method: enhanceMethod,
          model: enhanceMethod === 'gan' ? (enhanceModel || undefined) : undefined,
          sharpen: enhanceSharpen, max_side: enhanceMaxSide,
        });
        next[r.name] = { name: out.name, subfolder: out.subfolder, type: out.type };
        setEnhancedMap({ ...next });
        const secs = Math.max(1, Math.round((Date.now() - t0) / 1000));
        const dims = (out.width && out.height) ? `${out.width}×${out.height}` : '';
        setOne(i, { status: 'done', detail: `${out.method || 'done'}${dims ? ` · ${dims}` : ''} · ${secs}s` });
        done += 1;
      } catch (e) {
        failed += 1;
        setOne(i, { status: 'error', detail: (e as Error).message });
      }
    }));
    setEnhancedMap({ ...next });
    setEnhanceBusy(false);
    setEnhanceMsg(`Enhanced ${done}/${refs.length}${failed ? ` · ${failed} failed` : ''}${workers.length > 1 ? ` across ${workers.length} workers` : ''}.`);
    if (done > 0) setRefView('upscaled');
  };
  const doCloner = () => {
    doSaveClone(true);  // clone takes precedence: persist mode + fields + refs with the run
    return runGenerate('cloner', cloneName.trim(), {
    character_info: (cloneInfo || {}) as VNCCSCharacterInfoT,
    cloner_images: cloneRefsForGen() as unknown as Array<Record<string, unknown>>,
    nsfw: !!cloneInfo?.nsfw, background, gen_settings: seedGenSettings(),
    ...(!kleinCreate ? { engine: 'qwen' } : {}),
    ...(kleinCreate && variant === 'klein' ? { engine: 'klein' } : {}),
    ...(kleinCreate && variant === 'klein' && runBaseClothing ? { base_clothing: runBaseClothing } : {}),
    ...(kleinCreate && variant === 'klein' ? { face_kind: faceKind, style_custom: styleCustom } : {}),
    ...(kleinCreate && variant === 'klein' ? { lock_base: lockBase } : {}),
    ...(kleinCreate && variant === 'klein' ? { cleanup: effPoseCleanup, klein_steps: effPoseSteps, consistent_skin: consistentSkin,
                                               settings_overrides: poseOverrides() } : {}),
    ...kleinCanvasBody(),
    pose_set: selectedPoseSet.length ? selectedPoseSet as Array<Record<string, unknown>> : undefined,
    generator_overrides: buildGeneratorOverrides(upscaler),
    });
  };
  // Re-roll a SINGLE pose on a fresh seed with the same settings, replacing it in
  // place (the ingest replaces the sprite by pose name). Uses the poses currently
  // selected below, matched by name — the practical fix for the ~1/12 bad pose.
  const regeneratePose = async (poseName: string) => {
    if (!poseName || busy || regenPose) return;
    const idx = selectedPoseNames.indexOf(poseName);
    if (idx < 0) {
      setErrMsg(`Pose "${poseName}" isn't in the current pose selection — reselect the poses below, then regenerate.`);
      return;
    }
    const pose = selectedPoseSet[idx];
    const isClone = createSub === 'clone';
    const step: VNCCSStepT = isClone ? 'cloner' : 'creator';
    const charName = isClone ? cloneName.trim() : name.trim();
    if (!charName) return;
    const freshSeed = Math.floor(Math.random() * 2_000_000_000) + 1;
    const kleinExtras = variant === 'klein'
      ? { engine: 'klein', face_kind: faceKind, style_custom: styleCustom, lock_base: lockBase, cleanup: poseCleanup, klein_steps: poseSteps,
          consistent_skin: consistentSkin,
          ...kleinCanvasBody(),
          ...(runBaseClothing ? { base_clothing: runBaseClothing } : {}) }
      : {};
    const common: Partial<api.GenerateBody> = isClone
      ? { character_info: (cloneInfo || {}) as VNCCSCharacterInfoT,
          cloner_images: cloneRefsForGen() as unknown as Array<Record<string, unknown>>,
          nsfw: !!cloneInfo?.nsfw, background, ...kleinExtras }
      : { character_info: info, nsfw: !!info.nsfw, background, ...kleinExtras };
    setRegenPose(poseName);
    try {
      await runGenerate(step, charName, {
        ...common,
        gen_settings: { ...(seedGenSettings() || {}), seed: freshSeed },
        pose_set: [pose] as Array<Record<string, unknown>>,
        pose_names: [poseName],
        generator_overrides: buildGeneratorOverrides(upscaler),
      } as Partial<api.GenerateBody>);
    } catch (e) {
      setErrMsg(`Regenerate "${poseName}" failed: ${(e as Error).message}`);
    } finally {
      setRegenPose('');
    }
  };

  // Post-hoc AI-upscale of cataloged pose sprites (same GAN/SeedVR2 path + the
  // same method/model/sharpen/size controls as "Enhance base").  Sources the
  // ORIGINAL every time (backend resolves upscaled ids back to their source), so
  // repeated runs never stack.  Pass many ids to upscale a whole set at once.
  const upscalePoses = async (assetIds: string[]) => {
    const nm = (createSub === 'clone' ? cloneName : name).trim();
    if (!nm || !host?.online || !assetIds.length) return;
    if (assetIds.some((id) => poseUpBusy.has(id))) return;
    setPoseUpBusy((prev) => { const n = new Set(prev); assetIds.forEach((id) => n.add(id)); return n; });
    setPoseUpMsg(`Upscaling ${assetIds.length} pose${assetIds.length === 1 ? '' : 's'}…`);
    try {
      const r = await api.enhancePoses({
        character_name: nm, asset_ids: assetIds,
        method: baseEnhanceMethod,
        model: baseEnhanceMethod === 'gan' ? (baseEnhanceModel || undefined) : undefined,
        sharpen: baseEnhanceSharpen, max_side: baseEnhanceMaxSide,
      });
      if (editingCharId) {
        const img = await api.getCharacterImages(editingCharId);
        setExistingOutputs(img.outputs || []);
      }
      setPoseUpMsg(`✓ Upscaled ${r.count} pose${r.count === 1 ? '' : 's'}${r.failed ? ` · ${r.failed} failed` : ''} (${r.results[0]?.method || 'done'}).`);
    } catch (e) {
      setPoseUpMsg('');
      setErrMsg(`Pose upscale failed: ${(e as Error).message}`);
    } finally {
      setPoseUpBusy((prev) => { const n = new Set(prev); assetIds.forEach((id) => n.delete(id)); return n; });
    }
  };
  // All ORIGINAL (non-upscaled) base-pose sprite ids currently shown, for the
  // "Upscale all poses" whole-set action (respects the version filter).
  const collectBasePoseOriginalIds = (): string[] => {
    const ids: string[] = [];
    for (const o of existingOutputs) {
      if (!isFinalLabel(o.label) || o.label.startsWith('clothes/')
          || o.label.startsWith('emotions/') || o.label.endsWith('naked_sprites')) continue;
      for (const im of o.images) {
        if (!im.pose_name || im.upscaled) continue;
        if (!showAllVersions && baseVersions.length && curVersion
            && im.base_version && im.base_version !== curVersion.id) continue;
        ids.push(im.asset_id);
      }
    }
    return Array.from(new Set(ids));
  };

  const canCloner = !!host?.online && !!cloneName.trim() && cloneRefs.length > 0 && !busy;
  const canClonePreview = !!host?.online && !!cloneName.trim() && cloneRefs.length > 0 && !previewBusy && !busy;
  // Stable signature of the current reference set (name + role), used to auto-save
  // reference edits and to avoid redundant saves right after a load.
  const cloneRefsSig = useMemo(
    () => cloneRefs.map((r) => `${r.name}:${r.role || 'full'}`).join('|'),
    [cloneRefs]);
  const savedRefsSig = useRef('');
  const doSaveClone = async (silent = false) => {
    if (!cloneName.trim()) return;
    if (!silent) setSaveMsg('Saving…');
    try {
      const r = await api.saveCharacter({
        name: cloneName.trim(), character_info: (cloneInfo || {}) as VNCCSCharacterInfoT,
        create_mode: 'clone', clone_refs: cloneRefs, variant,
        ...(variant === 'klein' ? { canvas_w: parseInt(canvasW, 10) || null } : {}),
      });
      const _nm = cloneName.trim();
      if (r.character_id) setEditingCharId(r.character_id);  // so ref auto-save can persist edits
      savedRefsSig.current = cloneRefsSig;                   // mark this ref set as persisted
      setEnhanceByChar((prev) => ({ ...prev, [_nm]: {
        refs: enhancedMap, view: refView, on: enhanceOn,
        method: enhanceMethod, model: enhanceModel, sharpen: enhanceSharpen, maxSide: enhanceMaxSide,
      } }));
      if (!silent) setSaveMsg(`✓ Saved “${r.name}” (clone) to your library — reopening it restores this screen.`);
    } catch (e) {
      if (!silent) setSaveMsg(`⚠ Save failed: ${(e as Error).message}`);
    }
  };

  // Auto-persist reference edits (add / remove / role change) so they survive a
  // reload WITHOUT hitting Save — but only for a clone that already exists
  // (editingCharId set) so a brand-new draft isn't created prematurely.  The
  // signature guard skips the redundant save that a fresh load would trigger.
  useEffect(() => {
    if (!settingsLoaded.current) return;
    if (createSub !== 'clone' || !cloneName.trim()) return;
    // v1.165: also auto-save for a clone that was never explicitly Saved --
    // uploading a reference is intent enough; leaving the page must not lose
    // it. (Only skip when there is nothing to save AND no character exists
    // yet, so emptying a never-saved draft doesn't create one.)
    if (!editingCharId && !cloneRefs.length) return;
    if (cloneRefsSig === savedRefsSig.current) return;
    const t = setTimeout(() => {
      savedRefsSig.current = cloneRefsSig;
      doSaveClone(true);
    }, 800);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cloneRefsSig, editingCharId, createSub, cloneName]);

  // --- LLM wizards ----------------------------------------------------------
  const CHAR_WIZARD_KEYS = ['sex', 'age', 'race', 'skin_color', 'hair', 'eyes', 'face', 'body', 'additional_details'] as const;

  const runCharacterWizard = async () => {
    if (!wizCreateText.trim()) return;
    setWizBusy(true); setWizMsg('Wizard thinking… (first host run may download its LLM)');
    try {
      const r = await api.wizardCharacter(wizCreateText.trim());
      const patch: Partial<VNCCSCharacterInfoT> = {};
      for (const k of CHAR_WIZARD_KEYS) {
        if (r.fields[k] !== undefined) (patch as Record<string, unknown>)[k] = r.fields[k];
      }
      setInfo((prev) => ({ ...prev, ...patch }));
      setWizMsg(`✓ Form filled by the ${r.source === 'host' ? 'VNCCS host wizard' : 'app LLM (Ollama)'} — review & tweak.`);
    } catch (e) {
      setWizMsg(`⚠ ${(e as Error).message}`);
    } finally {
      setWizBusy(false);
    }
  };

  const runGarmentAnalyze = async () => {
    const imgs: Array<{ name: string; subfolder?: string; type?: string }> = [];
    if (garmentRef?.name) imgs.push({ name: garmentRef.name, subfolder: garmentRef.subfolder, type: garmentRef.type });
    for (const g of tgar) imgs.push({ name: g.ref.name, subfolder: g.ref.subfolder, type: g.ref.type });
    if (!imgs.length || wizBusy) return;
    setWizBusy(true); setWizMsg('🔍 Vision-scanning the outfit reference(s)…');
    try {
      const r = await api.wizardGarmentAnalyze(imgs.slice(0, 4));
      setCostume((prev) => {
        const next = { ...prev };
        for (const slot of SLOTS) {
          if (typeof r.fields[slot] === 'string' && (r.fields[slot] as string).trim()) next[slot] = r.fields[slot] as string;
        }
        return next;
      });
      const scanText = r.vision.map((v2, i2) => `— Image ${i2 + 1} (${v2.name}) —\n${v2.description}`).join('\n\n');
      setVisionView({ name: 'Outfit reference scan', text: scanText });
      setWizMsg(`✓ Outfit slots filled from ${r.vision.length} reference image${r.vision.length === 1 ? '' : 's'} — review & tweak, then generate.`);
    } catch (e) {
      setWizMsg(`⚠ ${(e as Error).message}`);
    } finally {
      setWizBusy(false);
    }
  };
  // Per-garment vision describe (try-on): scan ONE garment photo and fill that
  // row's description (and slot, if the scan detects one) — leaves the others.
  const runTgarDescribe = async (gi: number) => {
    const g = tgar[gi];
    if (!g || tgarDescIdx !== null) return;
    setTgarDescIdx(gi); setErrMsg('');
    try {
      const r = await api.wizardGarmentAnalyze([{ name: g.ref.name, subfolder: g.ref.subfolder, type: g.ref.type }]);
      // Prefer the description the scan wrote into this garment's own slot;
      // fall back to the raw per-image vision text.
      const slotText = typeof r.fields[g.slot] === 'string' ? (r.fields[g.slot] as string).trim() : '';
      const visionText = (r.vision && r.vision[0]?.description ? r.vision[0].description : '').trim();
      const desc = slotText || visionText;
      // If the current slot is empty but another slot got filled, adopt that slot.
      let slot = g.slot;
      if (!slotText) {
        const hit = SLOTS.find((s) => typeof r.fields[s] === 'string' && (r.fields[s] as string).trim());
        if (hit) slot = hit as string;
      }
      const finalDesc = desc || (typeof r.fields[slot] === 'string' ? (r.fields[slot] as string).trim() : '');
      setTgar((arr) => arr.map((x, xi) => xi === gi ? { ...x, desc: finalDesc || x.desc, slot } : x));
      if (visionText) setVisionView({ name: `Garment ${gi + 1} scan`, text: visionText });
    } catch (e) {
      setErrMsg(`Describe failed: ${(e as Error).message}`);
    } finally {
      setTgarDescIdx(null);
    }
  };
  const runClothesWizard = async () => {
    if (!wizClothesText.trim()) return;
    setWizBusy(true); setWizMsg('Wizard thinking… (first host run may download its LLM)');
    try {
      const r = await api.wizardClothes(wizClothesText.trim());
      setCostume((prev) => {
        const next = { ...prev };
        for (const slot of SLOTS) {
          if (typeof r.fields[slot] === 'string') next[slot] = r.fields[slot] as string;
        }
        return next;
      });
      setWizMsg(`✓ Outfit slots filled by the ${r.source === 'host' ? 'VNCCS host wizard' : 'app LLM (Ollama)'} — review & tweak.`);
    } catch (e) {
      setWizMsg(`⚠ ${(e as Error).message}`);
    } finally {
      setWizBusy(false);
    }
  };

  const cmFromHeight = (h: unknown): number => {
    const m = /(\d+(?:\.\d+)?)\s*cm/.exec(String(h || ''));
    return m ? parseFloat(m[1]) : 0;
  };
  const runCloneAnalyze = async () => {
    if (!cloneRefs.length) return;
    setWizBusy(true);
    setWizMsg(cloneRefs.length > 1 ? `Analyzing all ${cloneRefs.length} reference images…` : 'Analyzing the reference image…');
    try {
      const allRefs = cloneRefs as unknown as Array<Record<string, unknown>>;
      const r = await api.wizardCloneAnalyze(allRefs[0], 'auto', allRefs);
      setCloneInfo(r.fields as VNCCSCharacterInfoT);
      // prefill the SAM "articles to remove" field from what the vision model saw
      const _arts = (r.fields as Record<string, unknown>).worn_articles;
      if (typeof _arts === 'string' && _arts.trim()) setSamPrompt(_arts.trim());
      // store each image's Vision Scan Data on its reference (viewable via the 🔍 icon)
      if (r.vision && r.vision.length) {
        const vmap: Record<string, string> = {};
        for (const v of r.vision) if (v.name) vmap[v.name] = v.description;
        setCloneRefs((prev) => prev.map((x) => (vmap[x.name] ? { ...x, vision: vmap[x.name] } : x)));
      }
      const cnt = (r.source === 'ollama' && typeof r.analyzed === 'number' && cloneRefs.length > 1)
        ? ` (synthesized from ${r.analyzed}/${cloneRefs.length} image scans)` : '';
      setWizMsg(`✓ Character described by the ${r.source === 'host' ? 'VNCCS host wizard' : 'app vision LLM (Ollama)'}${cnt} — review & tweak. Tap 🔍 on an image to see its scan.`);
    } catch (e) {
      setWizMsg(`⚠ ${(e as Error).message}`);
    } finally {
      setWizBusy(false);
    }
  };

  // --- Pose library modal ---------------------------------------------------
  const loadPoseLibrary = async () => {
    setLibLoading(true); setLibNote('');
    try {
      const raw = await api.getPoseLibrary(true) as { poses?: Array<Record<string, unknown>> };
      const list = (raw?.poses || []).map((p) => ({
        id: String(p.id || p.name), name: String(p.name || ''),
        category: String(p.category || ''), repository: String(p.repository || ''),
        pose: normalizeLibraryPose(p.data),   // null => fetched on demand when added
      })).filter((p) => p.name);
      setLibPoses(list as typeof libPoses);
    } catch (e) {
      setLibNote(`⚠ Pose library load failed: ${(e as Error).message}`);
      setLibPoses([]);
    }
    try {
      const r = await api.getPoseRepositories();
      setLibRepos(r.repositories || []);
    } catch { setLibRepos([]); }
    setLibLoading(false);
  };
  const openPoseLibrary = () => { setLibOpen(true); loadPoseLibrary(); };
  // The Pose Library tab auto-loads on open (same data as the ➕ modal).
  useEffect(() => {
    if (tab === 'poselib') loadPoseLibrary();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  const addLibraryPose = async (p: { id: string; name: string; category: string; repository: string; pose: Record<string, unknown> | null }) => {
    let pose = p.pose;
    if (!pose) {
      try {
        const r = await api.getPoseFromLibrary(p.name, p.repository, p.category);
        pose = normalizeLibraryPose(r.pose);
      } catch { pose = null; }
    }
    if (!pose) { setLibNote(`⚠ Could not load pose data for “${p.name}”.`); return; }
    // v1.165: BAKE the thumbnail into a data URI at add time. A live relay URL
    // 404s later (worker offline / repo unloaded) and the pose tiles degrade
    // to border+name; a data URI survives in the saved pose set forever.
    let thumb: string | undefined = api.poseLibraryPreviewUrl(p.name, p.repository, p.category);
    try {
      const rsp = await fetch(thumb);
      if (rsp.ok) thumb = await blobToDataURL(await rsp.blob());
    } catch { /* keep the URL -- better than nothing */ }
    setExtraPoses((prev) => prev.some((x) => x.id === p.id) ? prev
      : [...prev, { id: p.id, name: p.name, pose: pose!, thumbUrl: thumb }]);
    setLibNote(`✓ Added “${p.name}” to the pose set.`);
  };

  const doToggleRepo = async (repo: api.PoseRepoT) => {
    setRepoBusy(repo.repo_id); setLibNote('');
    try {
      await api.togglePoseRepository(repo.repo_id, !repo.enabled);
      await loadPoseLibrary();
    } catch (e) {
      setLibNote(`⚠ ${(e as Error).message}`);
    } finally { setRepoBusy(''); }
  };

  const doRefreshRepo = async (repoId?: string) => {
    setRepoBusy(repoId || '*');
    setLibNote('Downloading pose pack(s) from Hugging Face — this can take a minute…');
    try {
      await api.refreshPoseRepositories(repoId);
      await loadPoseLibrary();
      setLibNote('✓ Pose pack(s) downloaded.');
    } catch (e) {
      setLibNote(`⚠ Download failed: ${(e as Error).message}`);
    } finally { setRepoBusy(''); }
  };

  // --- Library tab ----------------------------------------------------------
  // PARKED 2026-08-11 — see the parked Library state above. The Library tab's
  // JSX no longer exists, so loadLibrary / doDeleteCatalog / doLink have no
  // callers anywhere in the file. Left intact, commented out, as a unit.
  /*
  const loadLibrary = useCallback(async () => {
    setLibMsg('');
    try { setCatalog(await api.getCatalog()); } catch { setCatalog([]); }
    try { setProjects(await api.getProjects()); } catch { setProjects([]); }
  }, []);
  const doDeleteCatalog = async (c: api.CatalogItemT) => {
    if (!window.confirm(`Delete “${c.name}” from your library? All of its cataloged images are removed from the app.`)) return;
    const fromHosts = window.confirm(
      'ALSO delete the character from the VNCCS workers (node-side sprites/config)?\n\nOK = delete on workers too · Cancel = keep worker files');
    setLibMsg('Deleting…');
    try {
      const r = await api.deleteCatalogCharacter(c.character_id, fromHosts);
      const bad = (r.hosts || []).filter((h) => h.status !== 200 && h.status !== 404);
      setLibMsg(`Deleted “${c.name}” — ${r.assets_removed} image(s) removed`
        + (fromHosts ? `; workers: ${(r.hosts || []).length - bad.length} ok${bad.length ? `, ${bad.length} failed` : ''}` : '')
        + '.');
      loadLibrary();
    } catch (e) {
      setLibMsg(`⚠ Delete failed: ${(e as Error).message}`);
    }
  };
  const doLink = async (characterId: string) => {
    if (!linkProject) { setLibMsg('Pick a project first.'); return; }
    setLibMsg('Linking…');
    try {
      const r = await api.linkToProject({ character_id: characterId, project_id: linkProject });
      setLibMsg(`Linked ${r.character} → project (${r.created_asset_ids.length} assets copied).`);
    } catch (e) {
      setLibMsg(`Link failed: ${(e as Error).message}`);
    }
  };
  */
  const loadIntoCreate = useCallback(async (c: api.CatalogItemT) => {
    setName(c.name);
    setClothesChar(c.name);
    setEmoChar(c.name);
    const ci = (c.form?.character_info || {}) as VNCCSCharacterInfoT;
    if (Object.keys(ci).length) setInfo((prev) => ({ ...prev, ...ci }));
    setPreviewImg('');
    setTab('create');
    setCreateSub('new');
    setSaveMsg(`Loaded “${c.name}” from the library — tweak and regenerate.`);
    setEditingCharId(c.character_id);
    try {
      const r = await api.getCharacterImages(c.character_id);
      setExistingOutputs(r.outputs || []);
      setPoseRuns(r.pose_runs || []);
      setCostumesMap(r.costumes || {});
      const vers = r.base_versions || [];
      setBaseVersions(vers);
      setActiveBase(r.active_base || '');
      const ai = vers.findIndex((x) => x.id === r.active_base);
      setVerIdx(ai >= 0 ? ai : Math.max(0, vers.length - 1));
      if (vers.length) setPreviewImg(vers[ai >= 0 ? ai : vers.length - 1].url);
      const fci = (r.form?.character_info || {}) as VNCCSCharacterInfoT;
      if (Object.keys(fci).length) setInfo((prev) => ({ ...prev, ...fci }));
      // per-character canvas: restore this character's saved frame (falls back to
      // the global default the control already holds when none was saved)
      if (r.form?.canvas_w) setCanvasW(String(r.form.canvas_w));
      if (r.create_mode === 'clone') {
        // clone takes precedence: reopen straight into the Clone screen with
        // the saved reference images + analyzed fields ready to tweak
        setCreateSub('clone');
        setCloneName(c.name);
        const cinfo = (r.clone?.character_info || r.form?.character_info || {}) as VNCCSCharacterInfoT;
        if (Object.keys(cinfo).length) setCloneInfo(cinfo);
        if (r.clone?.refs?.length) {
          // default any missing role to 'full' so older saved characters still work
          setCloneRefs(r.clone.refs.map((x) => ({ ...x, role: (x.role || 'full') as api.RefRole })));
          setCloneSelIdx(0);
          // mark this loaded set as already persisted so the auto-save effect
          // doesn't immediately re-save the just-loaded references
          savedRefsSig.current = r.clone.refs.map((x) => `${x.name}:${x.role || 'full'}`).join('|');
        }
        const erec = enhanceByCharRef.current[c.name];
        if (erec) {
          setEnhancedMap(erec.refs || {});
          setEnhanceOn(!!erec.on);
          setRefView(erec.view === 'original' ? 'original' : 'upscaled');
          setEnhanceMethod(erec.method === 'seedvr2' ? 'seedvr2' : 'gan');
          setEnhanceModel(erec.model || '');
          if (erec.sharpen) setEnhanceSharpen(erec.sharpen);
          if (erec.maxSide) setEnhanceMaxSide(erec.maxSide);
        } else { setEnhancedMap({}); setRefView('original'); }
      }
    } catch {
      setExistingOutputs([]);
    }
  }, []);

  const openLightboxGallery = (urls: string[], i: number, onNav?: (i: number) => void) => {
    setLightboxMode('');
    setLightboxList(urls);
    setLightboxIdx(i);
    setLightboxSrc(urls[i]);
    lightboxNavRef.current = onNav || null;  // e.g. keeps the mannequin selection in sync
  };
  const stepLightbox = (d: number) => {
    setLightboxIdx((i) => {
      if (!lightboxList.length) return i;
      const n = (i + d + lightboxList.length) % lightboxList.length;
      setLightboxSrc(lightboxList[n]);
      lightboxNavRef.current?.(n);
      return n;
    });
  };

  const doDeleteLibImage = async (assetId: string, charId?: string) => {
    const cid = charId || editingCharId;
    if (!cid) return;
    try {
      await api.deleteCharacterImage(cid, assetId);
      const prune = (prev: api.CharacterImagesT['outputs']) => prev
        .map((o) => ({ ...o, images: o.images.filter((im) => im.asset_id !== assetId) }))
        .filter((o) => o.images.length > 0);
      setExistingOutputs(prune);
      setCloOutputs(prune);
      setEmoOutputs(prune);
    } catch (e) {
      setErrMsg(`Delete failed: ${(e as Error).message}`);
    }
  };

  // Deep link: /studio/vnccs?char=Name — the Character Studio list routes
  // VNCCS-created characters here so this page acts as their editor.
  useEffect(() => {
    // 🧬 Clone-from-character (v1.161): main-screen Clone button routes here with
    // ?cloneof=Name — open the Clone screen with the source's ACTIVE base image
    // preloaded as a full reference.
    const cloneOf = searchParams.get('cloneof');
    if (cloneOf) {
      setTab('create'); setCreateSub('clone');
      setCloneName(`${cloneOf} v2`);
      api.getCatalog().then(async (cat) => {
        const item = cat.find((c) => c.name === cloneOf);
        if (!item) return;
        try {
          const im = await api.getCharacterImages(item.character_id);
          const vers = im.base_versions || [];
          const act = vers.find((x) => x.id === im.active_base) || vers[vers.length - 1];
          if (!act) return;
          const blob = await fetch(act.url).then((r) => r.blob());
          const file = new File([blob], `${cloneOf.replace(/[^a-z0-9]/gi, '_')}_base.png`, { type: 'image/png' });
          const up = await api.uploadReference(file);
          setCloneRefs([{ ...up, role: 'full' } as api.UploadRefT]);
        } catch { /* best-effort — the user can add references manually */ }
      }).catch(() => { /* ignore */ });
      return;
    }
    // v1.276.15 — HONOUR ?tab=. The Character Studio grid has always sent a
    // tab in the URL, and nothing here read it, so every jump landed on the
    // default 🏠 Studio tab: you picked a character from a list and were shown
    // the same list again. `klein3` is a pseudo-tab because Klein 3.0 is not a
    // tab at all — it is tab `create` plus createEngine `klein3`.
    const want = (searchParams.get('tab') || '').toLowerCase();
    if (want) {
      if (want === 'klein3') { setTab('create'); setCreateEngine('klein3'); }
      else if (want === 'klein2') { setTab('create'); setCreateEngine('klein2'); }
      else if (want === 'qwen') { setTab('create'); setCreateEngine('qwen'); }
      else if (['studio', 'text2image', 'video', 'create', 'clothes',
                'emotions', 'poselib', 'lora', 'charsheet'].includes(want)) {
        setTab(want as Tab);
      }
    }

    const cname = searchParams.get('char');
    if (!cname) return;
    api.getCatalog().then((cat) => {
      const item = cat.find((c) => c.name === cname);
      if (item) {
        // characters open in the editor of the mode that MADE them — a Klein
        // character loaded on the Native route (or vice versa) redirects
        const v = item.variant === 'klein' ? 'klein' : 'native';
        if (v !== variant) {
          navigate(`/studio/${v === 'klein' ? 'vnccs-klein' : 'vnccs'}?char=${encodeURIComponent(cname)}`,
                   { replace: true });
          return;
        }
        loadIntoCreate(item);
      } else { setName(cname); setClothesChar(cname); setEmoChar(cname); }
    }).catch(() => setName(cname));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  const curVersion = baseVersions[verIdx];
  const shownPreviewSrc = curVersion ? curVersion.url : previewImg;
  const curViews = curVersion?.views || [];
  // v1.176.1: which base MODE a version is — from gen_meta.base_mode (new
  // renders) or inferred from its view count (older ones). Drives the set-tabs
  // above the preview so you can flip between the single / 4-view / mesh-ready
  // renders you've generated.
  const versionMode = (v?: api.BaseVersionT): 'single' | 'set' | 'mesh' => {
    const gm = (v?.gen_meta || {}) as Record<string, unknown>;
    const m = String(gm.base_mode || '').toLowerCase();
    if (m === 'mesh' || m === 'set' || m === 'single') return m;
    return (v?.views && v.views.length > 1) ? 'set' : 'single';
  };
  const BASE_MODE_TABS: { m: 'single' | 'set' | 'mesh'; label: string }[] = [
    { m: 'single', label: 'Single' }, { m: 'set', label: '4-view' }, { m: 'mesh', label: '🧊 Mesh-ready' }];
  const jumpToLatestMode = (m: 'single' | 'set' | 'mesh') => {
    for (let i = baseVersions.length - 1; i >= 0; i--) {
      if (versionMode(baseVersions[i]) === m) { setVerIdx(i); setBaseViewIdx(0); return; }
    }
  };
  const baseViewSel = curViews.length ? Math.min(baseViewIdx, curViews.length - 1) : 0;
  const mainBaseSrc = curViews.length ? (curViews[baseViewSel]?.url || shownPreviewSrc) : shownPreviewSrc;
  const stepVersion = (d: number) => {
    setBaseViewIdx(0);
    setVerIdx((i) => (baseVersions.length ? (i + d + baseVersions.length) % baseVersions.length : 0));
  };
  const doSetActiveBase = async () => {
    if (!editingCharId || !curVersion) return;
    try {
      await api.setActiveBase(editingCharId, curVersion.id);
      setActiveBase(curVersion.id);
    } catch (e) {
      setErrMsg(`Set active failed: ${(e as Error).message}`);
    }
  };
  const doBaseEnhance = async () => {
    const nm = createSub === 'clone' ? cloneName.trim() : name.trim();
    if (!nm || !host?.online || baseEnhanceBusy || !baseVersions.length) return;
    setBaseEnhanceBusy(true); setBaseEnhanceMsg(''); setErrMsg('');
    setBaseEnhanceWorker(host?.host || '');
    const t0 = Date.now();
    try {
      const r = await api.enhanceBase({
        character_name: nm, method: baseEnhanceMethod,
        model: baseEnhanceMethod === 'gan' ? (baseEnhanceModel || undefined) : undefined,
        sharpen: baseEnhanceSharpen, max_side: baseEnhanceMaxSide,
      });
      if (editingCharId) {
        try {
          const im = await api.getCharacterImages(editingCharId);
          const vers = im.base_versions || [];
          setBaseVersions(vers);
          setActiveBase(im.active_base || '');
          const ai = vers.findIndex((x) => x.id === (im.active_base || ''));
          setVerIdx(ai >= 0 ? ai : Math.max(0, vers.length - 1));
          setBaseViewIdx(0);
        } catch { /* best-effort */ }
      }
      const secs = Math.max(1, Math.round((Date.now() - t0) / 1000));
      const onWk = baseEnhanceWorker ? ` on ${shortHost(baseEnhanceWorker)}` : '';
      setBaseEnhanceMsg(`\u2713 Upscaled base (${r.method || 'done'}${r.views && r.views > 1 ? `, ${r.views} views` : ''})${onWk} \u00b7 ${secs}s \u2014 now the active base.`);
    } catch (e) {
      setBaseEnhanceMsg('');
      setErrMsg(`Base enhance failed: ${(e as Error).message}`);
    } finally {
      setBaseEnhanceBusy(false);
    }
  };
  const onUploadStyleRef = async (files: FileList | null) => {
    if (!files || !files.length) return;
    setErrMsg('');
    try { setSwitchStyleRef(await api.uploadReference(files[0])); }
    catch (e) { setErrMsg(`Style image upload failed: ${(e as Error).message}`); }
  };
  const onUploadGarment = async (files: FileList | null) => {
    if (!files || !files.length) return;
    setGarmentBusy(true); setErrMsg('');
    try {
      const f = files[0];
      const ref = await api.uploadReference(f);
      setGarmentRef(ref);
      setGarmentRefUrl(URL.createObjectURL(f));
      // v1.199.5: persist the outfit reference for this costume so it survives
      // reloads / worker restarts and can be re-rendered later with tweaks.
      const cid = cloCharId || editingCharId;
      if (cid && costumeName.trim()) {
        try { await api.saveGarmentRef(cid, costumeName.trim(), ref); setGarmentPersisted(true); }
        catch { /* non-fatal: still usable this session */ }
      }
    }
    catch (e) { setErrMsg(`Garment image upload failed: ${(e as Error).message}`); }
    finally { setGarmentBusy(false); }
  };
  const clearGarment = () => {
    const cid = cloCharId || editingCharId;
    if (garmentPersisted && cid && costumeName.trim()) {
      api.deleteGarmentRef(cid, costumeName.trim()).catch(() => { /* best-effort */ });
    }
    setGarmentRef(null); setGarmentRefUrl(''); setGarmentPersisted(false);
  };
  const doRestyleBase = async () => {
    const nm = createSub === 'clone' ? cloneName.trim() : name.trim();
    if (!nm || !host?.online || switchStyleBusy || !baseVersions.length) return;
    setSwitchStyleBusy(true); setSwitchStyleMsg(''); setErrMsg('');
    setSwitchStyleWorker(host?.host || '');
    const t0 = Date.now();
    const strengthMap: Record<string, number> = { subtle: 0.9, balanced: 0.7, strong: 0.45 };
    try {
      const r = await api.restyleBase({
        character_name: nm, style: switchStyle,
        style_custom: switchStyle === 'custom' ? switchStyleCustom : undefined,
        style_ref: switchStyleRef
          ? { name: switchStyleRef.name, subfolder: switchStyleRef.subfolder, type: switchStyleRef.type } : null,
        strength: strengthMap[switchStyleStrength] ?? 0.7,
        use_realism_lora: switchStyleRealism,
      });
      if (editingCharId) {
        try {
          const im = await api.getCharacterImages(editingCharId);
          const vers = im.base_versions || [];
          setBaseVersions(vers);
          setActiveBase(im.active_base || '');
          const ai = vers.findIndex((x) => x.id === (im.active_base || ''));
          setVerIdx(ai >= 0 ? ai : Math.max(0, vers.length - 1));
          setBaseViewIdx(0);
        } catch { /* best-effort */ }
      }
      const secs = Math.max(1, Math.round((Date.now() - t0) / 1000));
      const onWk = switchStyleWorker ? ` on ${shortHost(switchStyleWorker)}` : '';
      setSwitchStyleMsg(`✓ Restyled base (${r.style || switchStyle}${r.views && r.views > 1 ? `, ${r.views} views` : ''})${onWk} · ${secs}s — now the active base.`);
    } catch (e) {
      setSwitchStyleMsg('');
      setErrMsg(`Switch style failed: ${(e as Error).message}`);
    } finally {
      setSwitchStyleBusy(false);
    }
  };
  const versionCtl = baseVersions.length ? {
    index: verIdx, count: baseVersions.length,
    isActive: !!curVersion && curVersion.id === activeBase,
    onPrev: () => stepVersion(-1), onNext: () => stepVersion(1),
    onSetActive: doSetActiveBase,
  } : undefined;

  // costume versions follow the selected character+costume
  useEffect(() => {
    const entry = costumesMap[costumeName.trim()];
    const vers = entry?.versions || [];
    setCostumeVersions(vers);
    setCostActive(entry?.active || '');
    const ai = vers.findIndex((x) => x.id === entry?.active);
    setCostVerIdx(ai >= 0 ? ai : Math.max(0, vers.length - 1));
    setCostPrevImg('');
  }, [costumeName, costumesMap]);

  // v1.199.5: restore (or clear) the SAVED outfit reference when the character or
  // costume changes, so a set reference comes back on reload / costume switch.
  useEffect(() => {
    let cancelled = false;
    const cid = cloCharId || editingCharId;
    const co = costumeName.trim();
    if (!cid || !co) { setGarmentRef(null); setGarmentRefUrl(''); setGarmentPersisted(false); return; }
    (async () => {
      try {
        const m = await api.garmentRefMeta(cid, co);
        if (cancelled) return;
        if (m.exists && m.url) {
          // placeholder ref: no live worker name -> render sends use_saved_garment
          setGarmentRef({ name: '', subfolder: '', type: 'input' } as api.UploadRefT);
          setGarmentRefUrl(`${m.url}?t=${Date.now()}`);
          setGarmentPersisted(true);
        } else {
          setGarmentRef(null); setGarmentRefUrl(''); setGarmentPersisted(false);
        }
      } catch { if (!cancelled) { setGarmentPersisted(false); } }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cloCharId, editingCharId, costumeName]);

  const curCostVersion = costumeVersions[costVerIdx];
  const costShownSrc = curCostVersion ? curCostVersion.url : costPrevImg;
  const stepCostVersion = (d: number) => {
    if (!costumeVersions.length) return;
    setCostVerIdx((i) => {
      const n = (i + d + costumeVersions.length) % costumeVersions.length;
      const v = costumeVersions[n];
      if (v?.costume_info) setCostume((prev) => ({ ...prev, ...v.costume_info }));  // prompts match the image
      return n;
    });
  };
  const doSetActiveCostume = async () => {
    const cid = cloCharId || editingCharId;
    if (!cid || !curCostVersion) return;
    try {
      await api.setActiveCostume(cid, costumeName.trim(), curCostVersion.id);
      setCostActive(curCostVersion.id);
      if (curCostVersion.costume_info) setCostume((prev) => ({ ...prev, ...curCostVersion.costume_info }));
    } catch (e) {
      setErrMsg(`Set active failed: ${(e as Error).message}`);
    }
  };
  const costumeVersionCtl = costumeVersions.length ? {
    index: costVerIdx, count: costumeVersions.length,
    isActive: !!curCostVersion && curCostVersion.id === costActive,
    onPrev: () => stepCostVersion(-1), onNext: () => stepCostVersion(1),
    onSetActive: doSetActiveCostume,
  } : undefined;

  // The worker whose sprite files the mannequin strip shows AND the costume
  // preview dresses — MUST be the same machine: each fan-out worker holds only
  // its own shard of pose sprites, so "pose 3" is a different file per worker.
  const cloPreviewHost = useMemo(() => {
    const rec = catItems.find((c) => c.name === clothesChar)?.hosts || [];
    return rec.find((h) => vnccsHosts.includes(h)) || host?.host || '';
  }, [catItems, clothesChar, vnccsHosts, host?.host]);
  // every worker holding a shard of this character's sprites (online ones)
  const cloMannequinHosts = useMemo(() => {
    const rec = catItems.find((c) => c.name === clothesChar)?.hosts || [];
    const online = rec.filter((h) => vnccsHosts.includes(h));
    if (online.length) return online;
    return host?.host ? [host.host] : [];
  }, [catItems, clothesChar, vnccsHosts, host?.host]);
  // Emotions preview sprites — from the CATALOG (emoOutputs), for the selected preview
  // set (blank/Base = base sprites), filtered by the active engine (untagged shows under both).
  const emoPreviewSprites = useMemo(() => {
    const want = emoPreviewCostume;
    const wantBase = !want || want === 'Base';
    const matchEng = (e?: unknown) => {
      const x = String(e || '').toLowerCase();
      if (!x) return true;
      return emotionsSub === 'qwen' ? x === 'qwen' : x === 'klein';
    };
    const baseLabels = ['creator/sprites', 'cloner/sprites', 'creator/sheet', 'cloner/original_sprites'];
    const urls: string[] = [];
    for (const grp of emoOutputs) {
      const isSprite = grp.label.includes('sprites') || grp.label.endsWith('sheet');
      if (!isSprite) continue;
      if (wantBase && !baseLabels.includes(grp.label)) continue;
      for (const im of grp.images) {
        const meta = im as { url: string; engine?: string; costume?: string };
        if (!matchEng(meta.engine)) continue;
        if (!wantBase && String(meta.costume || '') !== want) continue;
        urls.push(meta.url);
      }
    }
    return urls;
  }, [emoOutputs, emoPreviewCostume, emotionsSub]);

  const outfitInfoOf = (entry?: { versions?: api.CostumeVersionT[]; active?: string; costume_info?: Record<string, string> }) => {
    const vlist = entry?.versions || [];
    return entry?.costume_info
      || vlist.find((v) => v.id === entry?.active)?.costume_info
      || (vlist.length ? vlist[vlist.length - 1].costume_info : undefined);
  };
  const saveOutfitPrompts = async (silent = false) => {
    const cn = clothesChar.trim(); const co = costumeName.trim();
    if (!cn || !co) { if (!silent) setCostSaveMsg('⚠ Pick a character and outfit name first.'); return; }
    try {
      const r = await api.saveCostumeInfo({ character_name: cn, costume: co, costume_info: { ...costume } });
      setCloCharId(r.character_id);
      setCostumesMap((prev) => ({ ...prev, [co]: { ...(prev[co] || {}), costume_info: { ...costume } } }));
      if (!silent) setCostSaveMsg(`✓ Prompts saved for “${co}”.`);
    } catch (e) {
      if (!silent) setCostSaveMsg(`⚠ Save failed: ${(e as Error).message}`);
    }
  };
  const loadOutfit = (nm: string) => {
    setCostumeName(nm);
    const info = outfitInfoOf(costumesMap[nm]);
    if (info) setCostume((prev) => ({ ...prev, ...info }));
    setCostSaveMsg('');
  };
  const newOutfit = () => {
    setCostumeName('');
    setCostume({ top: '', bottom: '', head: '', face: '', shoes: '' });
    setCostPrevImg(''); setCostSaveMsg('');
  };
  const openImport = () => { setImportOpen(true); setImportChar(''); setImportCostumes({}); };
  const pickImportChar = async (nm: string) => {
    setImportChar(nm); setImportCostumes({});
    if (!nm) return;
    const item = catItems.find((c) => c.name === nm);
    if (!item) return;
    setImportLoading(true);
    try {
      const r = await api.getCharacterImages(item.character_id);
      setImportCostumes(r.costumes || {});
    } catch { setImportCostumes({}); } finally { setImportLoading(false); }
  };
  const doImportCostume = (nm: string) => {
    const info = outfitInfoOf(importCostumes[nm]);
    if (info) setCostume((prev) => ({ ...prev, ...info }));
    setCostumeName(nm);
    setImportOpen(false);
    setCostSaveMsg(`✓ Imported “${nm}” prompts from ${importChar} — tweak and preview.`);
  };

  const refreshCharPanels = async () => {
    try {
      if (editingCharId) {
        const r = await api.getCharacterImages(editingCharId);
        setExistingOutputs(r.outputs || []);
        setCostumesMap(r.costumes || {});
        const vers = r.base_versions || [];
        setBaseVersions(vers);
        setActiveBase(r.active_base || '');
        const ai = vers.findIndex((x) => x.id === r.active_base);
        setVerIdx(ai >= 0 ? ai : Math.max(0, vers.length - 1));
        if (vers.length) setPreviewImg(vers[ai >= 0 ? ai : vers.length - 1].url);
      }
      if (cloCharId && cloCharId !== editingCharId) {
        const r2 = await api.getCharacterImages(cloCharId);
        setCostumesMap(r2.costumes || {});
        setCloOutputs(r2.outputs || []);
      }
    } catch { /* best-effort */ }
  };
  const onUploadTryGarment = async (files: FileList | null) => {
    if (!files || !files.length || tgar.length >= 3) return;
    setTgarBusy(true); setErrMsg('');
    try {
      const localUrl = URL.createObjectURL(files[0]);
      const up = await api.uploadReference(files[0]);
      setTgar((g) => [...g, { ref: up, desc: '', slot: tgarSlot, url: localUrl }].slice(0, 3));
    } catch (e) { setErrMsg(`Garment upload failed: ${(e as Error).message}`); }
    finally { setTgarBusy(false); }
  };
  const doTryOn = async () => {
    if (!host?.online || !clothesChar.trim() || !tgar.length || tryBusy || busy) return;
    setTryBusy(true); setTryMsg(''); setErrMsg('');
    try {
      const r = await api.kleinTryOn({
        character_name: clothesChar.trim(),
        costume_name: costumeName.trim() || null,
        garments: tgar.map((g) => ({ ref: { name: g.ref.name, subfolder: g.ref.subfolder, type: g.ref.type }, desc: g.desc, slot: g.slot })),
        person_asset_id: (tryChain && tryResultRef) ? undefined : (kcPose || undefined),
        person_ref: (tryChain && tryResultRef) ? tryResultRef : undefined,
        person_desc: tryPersonDesc,
        steps: parseInt(trySteps, 10) || 28,
        guidance: parseFloat(tryGuide) || 2.5,
        clean_garments: garmentClean,
        host: cloPreviewHost || undefined,
      });
      setTryImg(`data:image/png;base64,${r.image}`);
      setTryResultRef(r.result_ref || null);
      setTryChain(true);
      setTgar([]);
      setTryMsg(r.version
        ? `✓ Saved as a version of “${costumeName.trim()}”. Add the next piece (it layers on this result) or pick poses below.`
        : '✓ Done — add the next piece to layer, or set a Costume name first so results save as versions.');
      if (r.version && cloCharId) {
        try { const im2 = await api.getCharacterImages(cloCharId); setCostumesMap(im2.costumes || {}); } catch { /* best-effort */ }
      }
    } catch (e) { setErrMsg(`Try-on failed: ${(e as Error).message}`); }
    finally { setTryBusy(false); }
  };
  const canCostumePreview = !!host?.online && !!clothesChar.trim() && !!costumeName.trim() && !costPrevBusy && !busy;
  const doCostumePreview = async () => {
    if (!canCostumePreview) return;
    setCostPrevBusy(true); setErrMsg('');
    try {
      // Klein Hybrid: dress the character's ACTIVE BASE render (identity/body/pose
      // preserved, outfit redrawn) from description slots and/or a garment image.
      // Legacy (qwen) path: dress a VNCCS pose sprite via the clothes designer.
      const r = (variant === 'klein' && clothesSub === 'qwen')
        ? await api.generateQwenClothesPreview({
            character_name: clothesChar.trim(), costume_name: costumeName.trim(),
            costume_info: costume, background,
            garment_ref: garmentRef?.name
              ? { name: garmentRef.name, subfolder: garmentRef.subfolder, type: garmentRef.type }
              : null,
            use_saved_garment: !!(garmentPersisted && garmentRef && !garmentRef.name),
            pose_asset_id: kcPose || undefined,
            steps: qwenSteps.trim() !== '' ? parseInt(qwenSteps, 10) || undefined : undefined,
            cfg: qwenCfg.trim() !== '' ? parseFloat(qwenCfg) || undefined : undefined,
            clothes_lora_strength: qwenClothesLora.trim() !== '' ? parseFloat(qwenClothesLora) || undefined : undefined,
            target_size: qwenTarget.trim() !== '' ? parseInt(qwenTarget, 10) || undefined : undefined,
            headwear_room: qwenHeadwearRoom.trim() !== '' ? (parseFloat(qwenHeadwearRoom) || undefined) : undefined,
            host: cloPreviewHost || undefined,
          })
        : kleinClothes
        ? await api.generateKleinClothesPreview({
            character_name: clothesChar.trim(), costume_name: costumeName.trim(),
            costume_info: costume, background,
            garment_ref: garmentRef?.name
              ? { name: garmentRef.name, subfolder: garmentRef.subfolder, type: garmentRef.type }
              : null,
            use_saved_garment: !!(garmentPersisted && garmentRef && !garmentRef.name),
            pose_asset_id: kcPose || undefined,
            strength: dressStrength.trim() !== '' ? parseFloat(dressStrength) || 1 : undefined,
            ref_end: dressRefEnd.trim() !== '' ? parseFloat(dressRefEnd) || 0.8 : undefined,
            steps: dressSteps.trim() !== '' ? parseInt(dressSteps, 10) || undefined : undefined,
            guidance: dressGuide.trim() !== '' ? parseFloat(dressGuide) || 1 : undefined,
            negative: dressNeg.trim() || undefined,
            consistency: dressCons || undefined,
            identity_lock: dressIdLock,
            clean_garment: garmentClean,
            host: cloPreviewHost || undefined,
          })
        : await api.generateCostumePreview({
            character_name: clothesChar.trim(), costume_name: costumeName.trim(),
            costume_info: costume, background, sprite_index: cloMannequin?.index ?? 0,
            host: cloMannequin?.host || cloPreviewHost || undefined,
          });
      setCostPrevImg(`data:image/png;base64,${r.image}`);
      if (r.version) {
        setCloCharId(r.version.character_id);
        setCostumesMap((prev) => {
          const key = costumeName.trim();
          const entry = { ...(prev[key] || {}) };
          entry.versions = [...(entry.versions || []), r.version!.version];
          entry.active = r.version!.active;
          entry.costume_info = { ...costume };
          return { ...prev, [key]: entry };
        });
      }
    } catch (e) {
      setErrMsg(`Costume preview failed: ${(e as Error).message}`);
    } finally {
      setCostPrevBusy(false);
    }
  };

  const allCharOptions = useMemo(
    () => Array.from(new Set([...hostChars, ...catNames])).sort((a, b) => a.localeCompare(b)),
    [hostChars, catNames]);
  const charPicker = (value: string, setter: (v: string) => void) => (
    allCharOptions.length ? (
      <select style={input} value={value} onChange={(e) => setter(e.target.value)}>
        <option value="">(select character)</option>
        {allCharOptions.map((c) => <option key={c} value={c}>{c}</option>)}
      </select>
    ) : (
      <input style={input} value={value} onChange={(e) => setter(e.target.value)} placeholder="character name on host" />
    )
  );

  const relevantPoseRuns = useMemo(() => (
    tab === 'clothes'
      ? poseRuns.filter((r) => r.step === 'clothes' && (r.costume || '') === costumeName.trim())
      : poseRuns.filter((r) => r.step === 'creator' || r.step === 'cloner')
  ), [poseRuns, tab, costumeName]);
  const donePoseNames = useMemo(
    () => new Set(relevantPoseRuns.flatMap((r) => r.pose_names || [])),
    [relevantPoseRuns]);
  const loadPoseRun = (r: NonNullable<api.CharacterImagesT['pose_runs']>[number]) => {
    const set = (r.pose_set || []) as Array<Record<string, unknown>>;
    const names = r.pose_names || [];
    const selIdx = new Set<number>();
    const extras: ExtraPose[] = [];
    set.forEach((p, i) => {
      const byName = poseDefaults.find((d) => d.name === names[i]);
      if (byName) selIdx.add(byName.index);
      else extras.push({ id: `run_${r.prompt_id || 'x'}_${i}`, name: names[i] || `pose ${i + 1}`, pose: p });
    });
    setDefaultSel(selIdx);
    setRemovedDefaults(new Set());
    setExtraPoses(extras);
    if (r.seed) { setSeedMode('fixed'); setSeedVal(String(r.seed)); }
    if (tab === 'clothes' && r.costume) setCostumeName(r.costume);
    setPoseSetMsg(`↻ Restored “${names.length} pose(s)${r.seed ? ` · seed ${r.seed}` : ''}” — hit Generate to redo this run.`);
  };

  // v1.179: the ? help button that sits beside a Generate button and opens the
  // matching how-to guide (best practices + gotchas) in a lightbox.
  const helpBtn = (topic: string) => (
    <button type="button" title="How to use this — settings, tips & gotchas"
            onClick={() => setHelpTopic(topic)}
            style={{ flex: '0 0 auto', width: 38, borderRadius: 8, border: '1px solid #34518a',
                     background: '#121826', color: '#8ab4ff', fontWeight: 800, fontSize: 16,
                     cursor: 'pointer' }}>?</button>
  );
  const segRow = (opts: { v: string; label: string }[], val: string, on: (v: string) => void) => (
    <div style={{ display: 'flex', gap: 6 }}>
      {opts.map((o) => (
        <button key={o.v} type="button" onClick={() => on(o.v)}
          style={{ flex: 1, padding: '6px 8px', fontSize: 12, borderRadius: 6, cursor: 'pointer',
                   border: `1px solid ${val === o.v ? '#4a6cff' : '#333'}`,
                   background: val === o.v ? '#22314f' : '#141414',
                   color: val === o.v ? '#cfe0ff' : '#9aa4b2' }}>
          {o.label}
        </button>
      ))}
    </div>
  );
  // v1.164.1: Presets live at the TOP of the left column (they used to sit
  // buried mid-list inside Base render settings)
  const presetsBox = variant !== 'klein' ? null : (
    <div style={{ marginBottom: 12 }}>
      <div style={{ border: '1px solid #34518a', background: '#121826', borderRadius: 8, padding: 10 }}>
          <label style={{ ...label, fontWeight: 600, color: '#cbd2dc' }}>🎛 Presets — named snapshots of EVERY Klein dial (base + pose). The live dials always auto-save as your working defaults; a preset is a copy you can come back to after experimenting</label>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
            <select style={{ ...input, width: 180 }} value={presetSel} onChange={(e) => setPresetSel(e.target.value)}>
              <option value="">— presets —</option>
              {Object.keys(presets).sort().map((n) => (<option key={n} value={n}>{n}</option>))}
            </select>
            <button style={{ ...pbtn, opacity: presetSel ? 1 : 0.5 }} disabled={!presetSel} onClick={() => applyPreset(presetSel)}>Apply</button>
            <button style={{ ...pbtn, opacity: presetSel ? 1 : 0.5 }} disabled={!presetSel} onClick={() => savePreset(presetSel)} title="Overwrite the selected preset with the current dials">Update</button>
            <button style={{ ...pbtn, opacity: presetSel ? 1 : 0.5 }} disabled={!presetSel} onClick={() => deletePreset(presetSel)}>Delete</button>
            <input style={{ ...input, width: 150 }} placeholder="new preset name" value={presetName}
                   onChange={(e) => setPresetName(e.target.value)} />
            <button style={pbtn} onClick={() => savePreset(presetName)}>💾 Save as</button>
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', marginTop: 8,
                        paddingTop: 8, borderTop: '1px solid #23324f' }}>
            <button style={{ ...pbtn, opacity: (curVersion?.gen_meta && Object.keys(curVersion.gen_meta).length) ? 1 : 0.5 }}
                    disabled={!(curVersion?.gen_meta && Object.keys(curVersion.gen_meta).length)}
                    title={(curVersion?.gen_meta && Object.keys(curVersion.gen_meta).length)
                      ? 'Load the exact settings that produced the base image currently shown in the preview — so you can reproduce it or iterate from the same starting point (used across the multi-view modes too)'
                      : 'This base was generated before per-image settings were tracked, so there’s nothing saved to restore. Any base you generate now will carry its settings and enable this.'}
                    onClick={() => applyGenMetaToDials(curVersion?.gen_meta)}>
              ⤵ Use current preview image settings
            </button>
            {reproSeed !== '' && (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12,
                             padding: '3px 8px', borderRadius: 12, border: '1px solid #4a6cff',
                             background: '#22314f', color: '#cfe0ff' }}
                    title="This seed is pinned — the next Generate reproduces the loaded image. Click × to randomize again.">
                🎲 seed {reproSeed}
                <button type="button" onClick={() => { setReproSeed(''); setPresetMsg('Seed cleared — Generate will randomize again.'); }}
                        style={{ border: 'none', background: 'transparent', color: '#cfe0ff', cursor: 'pointer', fontSize: 14, lineHeight: 1, padding: 0 }}>×</button>
              </span>
            )}
          </div>
          {presetMsg !== '' && <p style={{ fontSize: 12, color: '#a8b2c0', margin: '6px 0 0' }}>{presetMsg}</p>}
        </div>
    </div>
  );
  // v1.176.1: the base-render-mode picker, sat DIRECTLY above the Generate
  // buttons (Create + Clone) so it's the first thing you choose. Single / 4-view
  // / 🧊 Mesh-ready all file as base VERSIONS, so you can generate any or all of
  // them and flip between them with the set tabs above the preview.
  const baseModePicker = variant !== 'klein' ? null : (
    <div style={{ border: '1px solid #34518a', background: '#121826', borderRadius: 8, padding: '8px 10px' }}>
      <label style={{ ...label, fontWeight: 600, color: '#cbd2dc', margin: '0 0 5px' }}>
        What to generate — base views
      </label>
      {segRow([{ v: 'single', label: 'Single (front)' },
               { v: 'set', label: '4-view set' },
               { v: 'mesh', label: '🧊 Mesh-ready' }],
              baseMode, (v) => setBaseMode(v as 'single' | 'set' | 'mesh'))}
      <p style={{ fontSize: 11.5, color: '#8d97a5', margin: '5px 0 0' }}>
        <b>Single</b> = one fast front view. <b>4-view set</b> = front/right/left/back (needed for a
        good 3D body — one view loses detail). <b>🧊 Mesh-ready</b> = that 4-view set in a locked
        A-pose, arms clear, plain gray backdrop — the best input for “🧊 Generate 3D body”. Each is
        saved as its own base version; generate any or all and switch between them with the tabs
        above the preview. More views = more render time.
      </p>
      {baseMode !== 'single' && (
        <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px solid #23324f' }}>
          <label style={{ ...label, fontWeight: 600, color: '#cbd2dc', margin: '0 0 5px' }}>
            Anchor the set on
          </label>
          {segRow([{ v: 'fresh', label: 'A fresh front' },
                   { v: 'approved', label: '★ The approved base' }],
                  baseSetAnchor, (v) => setBaseSetAnchor(v as 'fresh' | 'approved'))}
          <p style={{ fontSize: 11.5, color: '#8d97a5', margin: '5px 0 0' }}>
            The set is built by rendering the FRONT first, then turning it to the other views so they
            match. <b>Fresh front</b> renders a new front now. <b>★ Approved base</b> uses the base image
            you’ve already got selected in the preview as the starting point — 4-view reuses it as the
            front as-is; Mesh-ready derives the A-pose set from it. {baseVersions.length === 0 &&
              <span style={{ color: '#e4b483' }}>(No approved base yet — generate a Single base first to use this.)</span>}
          </p>
          <label style={{ ...label, fontWeight: 600, color: '#cbd2dc', margin: '10px 0 5px' }}>
            How to turn the other views
          </label>
          {segRow([{ v: 'reference', label: 'Reference-edit (default)' },
                   { v: 'matchpose', label: '🧍 MatchingPose (mannequin)' }],
                  baseDeriveMethod, (v) => setBaseDeriveMethod(v as 'reference' | 'matchpose'))}
          <p style={{ fontSize: 11.5, color: '#8d97a5', margin: '5px 0 0' }}>
            <b>Reference-edit</b> rotates the front image directly (no LoRA). <b>🧍 MatchingPose</b> keeps
            the anchor base as the identity and rotates a <b>generic mannequin</b> — built only from the
            character’s description, never an existing 3D mesh asset — with the MatchingPose LoRA, to
            preserve the real body shape for meshing. Needs <code>Maching_Pose_9B_Rank256.safetensors</code>
            in the worker’s loras (you already use it for pose sets). Best paired with <b>★ The approved base</b>.
          </p>
        </div>
      )}
    </div>
  );
  // v1.178: big labeled section header for the base panel (matches the pose
  // panel's dividers) so consistency / clothing-bleed / framing controls are
  // grouped and easy to find instead of one flat list.
  const bhead = (t: string) => (
    <div style={{ fontSize: 13, fontWeight: 700, color: '#8ab4ff', margin: '6px 0 0',
                  paddingTop: 10, borderTop: '1px solid #2a3242' }}>{t}</div>
  );
  const kleinBaseControls = (nsfw: boolean, setNsfw: (b: boolean) => void,
                             binfo?: Record<string, unknown>,
                             setBinfoPatch?: (patch: Record<string, unknown>) => void) => (
    <div style={{ display: 'grid', gap: 13, padding: '8px 0' }}>
      {variant === 'klein' && bhead('🧍 Character & build')}
      <div>
        <label style={label}>Content</label>
        {segRow([{ v: 'sfw', label: 'SFW' }, { v: 'nsfw', label: 'NSFW' }],
                nsfw ? 'nsfw' : 'sfw', (v) => setNsfw(v === 'nsfw'))}
      </div>
      {variant === 'klein' && binfo && setBinfoPatch && (() => {
        const manual = binfo.body_weight != null || binfo.body_muscle != null
          || binfo.body_height != null || binfo.body_breast != null || binfo.body_belly != null;
        const isFemale = !String((binfo.sex as string) || 'female').toLowerCase().startsWith('m');
        const bslider = (lbl: string, key: string, hint: string) => {
          const val = binfo[key] != null ? Number(binfo[key]) : 50;
          return (
            <div key={key} style={{ display: 'grid', gridTemplateColumns: '58px 1fr 30px', gap: 8, alignItems: 'center' }}>
              <label style={{ ...label, marginBottom: 0 }} title={hint}>{lbl}</label>
              <input type="range" min={0} max={100} value={val}
                     onChange={(e) => setBinfoPatch!({ [key]: parseInt(e.target.value, 10) })} />
              <span style={{ fontSize: 12, color: '#a8b2c0' }}>{val}</span>
            </div>
          );
        };
        const hcm = cmFromHeight(binfo.height);
        return (
          <div>
            <div style={{ display: 'grid', gridTemplateColumns: '110px 90px 1fr', gap: 8, marginBottom: 8 }}>
              <div><label style={label} title="Real height - drives the pose mannequin's stature (relative to sex; a 5'6&quot; man renders short)">Height (ft / in)</label>
                <div style={{ display: 'flex', gap: 4 }}>
                  <input style={{ ...input, width: 44 }} type="number" min={2} max={8}
                         value={hcm ? Math.floor(Math.round(hcm / 2.54) / 12) : ''}
                         onChange={(e) => { const ft = parseInt(e.target.value || '0', 10); const tot = hcm ? Math.round(hcm / 2.54) : 66; const nt = ft * 12 + (tot % 12); setBinfoPatch!({ height: nt > 0 ? `${Math.round(nt * 2.54)} cm` : '' }); }} />
                  <input style={{ ...input, width: 44 }} type="number" min={0} max={11}
                         value={hcm ? Math.round(hcm / 2.54) % 12 : ''}
                         onChange={(e) => { const inch = parseInt(e.target.value || '0', 10); const tot = hcm ? Math.round(hcm / 2.54) : 60; const nt = Math.floor(tot / 12) * 12 + inch; setBinfoPatch!({ height: nt > 0 ? `${Math.round(nt * 2.54)} cm` : '' }); }} />
                </div></div>
              <div><label style={label}>Height (cm)</label>
                <input style={input} type="number" min={80} max={230} value={hcm || ''}
                       onChange={(e) => { const cm = parseFloat(e.target.value); setBinfoPatch!({ height: cm > 0 ? `${Math.round(cm)} cm` : '' }); }} /></div>
              <div><label style={label}>&nbsp;</label>
                <div style={{ fontSize: 12, color: '#9aa4b2', paddingTop: 8 }}>
                  {hcm ? `${Math.floor(Math.round(hcm / 2.54) / 12)}ft ${Math.round(hcm / 2.54) % 12}in - stature follows this (sex-relative). Regenerate poses after changing.`
                       : (String(binfo.height || '').trim() ? `"${String(binfo.height)}" (text descriptor)` : 'not set - renders average height')}
                </div></div>
            </div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: '#cbd2dc' }}>
              <input type="checkbox" checked={!manual}
                     onChange={(e) => setBinfoPatch!(e.target.checked
                       ? { body_weight: undefined, body_muscle: undefined, body_height: undefined, body_breast: undefined, body_belly: undefined }
                       : { body_weight: 55, body_muscle: 50, body_height: 50 })} />
              Build — auto from description (uncheck to dial the mannequin body by hand)
            </label>
            {manual && (
              <div style={{ ...wizBox, display: 'grid', gap: 6, marginTop: 6 }}>
                {bslider('Weight', 'body_weight', 'body fat / heaviness')}
                {bslider('Belly', 'body_belly', 'forward hanging gut (0 = flat; unset = auto from description)')}
                {bslider('Muscle', 'body_muscle', 'muscle tone')}
                {bslider('Height', 'body_height', 'stature')}
                {isFemale && bslider('Bust', 'body_breast', 'bust size')}
                <p style={{ fontSize: 12, color: '#8d97a5', margin: 0 }}>
                  Sets the pose mannequin's body so the character is generated to match it — the most direct way to hit a reference build. Regenerate the base (then poses) after changing.
                </p>
              </div>
            )}
          </div>
        );
      })()}
      {variant === 'klein' && bhead('🎨 Style & look')}
      {variant === 'klein' && (
        <div>
          <label style={label}>Output style — tell the model what look you're aiming for</label>
          <select style={input} value={faceKind} onChange={(e) => setFaceKind(e.target.value)}>
            <option value="auto">Auto (match the references)</option>
            <option value="photorealistic">Photorealistic</option>
            <option value="semi-realistic">Semi-realistic</option>
            <option value="anime">Anime</option>
            <option value="manga">Manga (black &amp; white)</option>
            <option value="comic">Western comic</option>
            <option value="cartoon">Cartoon</option>
            <option value="3d">3D render</option>
            <option value="painting">Digital painting</option>
            <option value="custom">Custom…</option>
          </select>
          {faceKind === 'custom' && (
            <input style={{ ...input, marginTop: 6 }} value={styleCustom}
                   onChange={(e) => setStyleCustom(e.target.value)}
                   placeholder="Describe the style, e.g. gritty film-noir black & white photography" />
          )}
          <p style={{ fontSize: 12, color: '#8d97a5', margin: '3px 0 0' }}>
            Set this to the look you're going for so the generator aims for it from the start — e.g. if your
            reference is anime, pick Anime; if you want a realistic person, pick Photorealistic. It steers the
            base preview and every pose toward that style, but it's guidance, not a hard override — for a
            definitive style change, use “Switch Style” under the base image. Photoreal / Semi-real use PuLID
            for face identity; illustrated styles skip it.
          </p>
        </div>
      )}
      {variant === 'klein' && bhead('👕 Base outfit, strip & cleanup — clothing bleed is tuned here')}
      {variant === 'klein' && (
        <div>
          <label style={label}>Base outfit — what the pose set wears</label>
          {segRow([{ v: '', label: `Settings (${baseClothing === 'keep' ? 'keep' : 'strip'})` },
                   { v: 'strip', label: 'Underwear / nude base' },
                   { v: 'keep', label: 'Keep clothing' }], runBaseClothing, setRunBaseClothing)}
          <p style={{ fontSize: 11.5, color: '#8d97a5', margin: '4px 0 0' }}>
            Seeing clothing bleed onto a stripped base, or a body that's off? Check these three:
            <b> Strip release</b> lower (0.75–0.80) strips leftover clothing harder, <b>Article cleanup (SAM3)</b>
            below removes named items, and <b>Body adherence</b> (under Identity &amp; consistency) controls how
            hard the body locks to your refs. Recommended base defaults: strip release 0.85, body adherence 1.60.
          </p>
        </div>
      )}
      {/* v1.176.1: the base-mode picker moved OUT of this buried settings block
          to sit right next to the Generate buttons (baseModePicker) so it's
          visible without opening the advanced accordion. */}
      {variant === 'klein' && (
        <div>
          <label style={label}>Cleanup — strips shoes/jewelry (higher = stronger, a bit more grain)</label>
          {segRow([{ v: 'off', label: 'Off' }, { v: 'gentle', label: 'Gentle' }, { v: 'strong', label: 'Strong' }],
                  cleanup, setCleanup)}
          {extremes('keeps everything from the references', 'strips leftover clothing/jewelry hardest — can harden edges')}
        </div>
      )}
      {variant === 'klein' && (
        <div>
          <label style={label}>Strip release — where the body reference lets go so the final steps can strip leftover clothing/jewelry (lower = strips harder; Hold keeps the reference the whole way)</label>
          {segRow([{ v: '1', label: 'Hold' }, { v: '0.9', label: '0.90' }, { v: '0.85', label: '0.85 (def)' }, { v: '0.8', label: '0.80' }, { v: '0.75', label: '0.75' }, { v: '0.7', label: '0.70' }, { v: '0.65', label: '0.65' }],
                  rbEnd, setRbEnd)}
        </div>
      )}
      {variant === 'klein' && bhead('🧬 Identity & consistency — match the reference across all views')}
      {variant === 'klein' && (
        <div>
          <label style={label}>Body adherence — how strongly poses lock to your source body vs. the pose mannequin's generic build (higher = truer body, less drift; too high can over-reference). Applies to base + pose sets.</label>
          {segRow([{ v: '1', label: '1.0' }, { v: '1.25', label: '1.25' }, { v: '1.4', label: '1.40' }, { v: '1.6', label: '1.60 (def)' }, { v: '1.8', label: '1.80' }, { v: '2', label: '2.0' }],
                  bodyMatch, setBodyMatch)}
        </div>
      )}
      {variant === 'klein' && (
        <div>
          <label style={label}>Reference masking — what the body reference carries from your source photos. Person (default) keeps face + build but drops the outfit so it can't leak; Person + clothes keeps the whole clothed silhouette; Full image references everything unmasked; Body only is skin-build without the head (legacy — can swap a wrong face).</label>
          {segRow([{ v: 'person', label: 'Person (drop clothes)' }, { v: 'person+clothes', label: 'Person + clothes' }, { v: 'full', label: 'Full image' }, { v: 'body', label: 'Body only' }],
                  bodyKeep, setBodyKeep)}
        </div>
      )}
      {variant === 'klein' && (
        <div>
          <label style={label}>Match all views (shared seed) — renders every view in a 4-view / 🧊 Mesh-ready set from ONE seed so skin tone and lighting stay consistent across front/side/back (off = each view gets its own seed: more variety, more drift). Sets only.</label>
          {segRow([{ v: 'on', label: 'On (matched set)' }, { v: 'off', label: 'Off (varied)' }],
                  baseMatchViews ? 'on' : 'off', (v) => setBaseMatchViews(v === 'on'))}
        </div>
      )}
      {variant === 'klein' && (
        <div>
          <label style={label}>Consistency LoRA (base sets) — the SAME dx8152 cross-image LoRA your pose sets use, applied to the 4-view / mesh set so every view holds a matching look (needs the LoRA on the worker; only affects reference-driven sets, not a plain single front base).</label>
          {segRow([{ v: 'on', label: 'On' }, { v: 'off', label: 'Off' }],
                  baseConsLora ? 'on' : 'off', (v) => setBaseConsLora(v === 'on'))}
          {baseConsLora && (
            <div style={{ marginTop: 6 }}>
              <label style={label}>Consistency LoRA strength (Global = the ⚙ Settings value)</label>
              {segRow([{ v: '', label: 'Global' }, { v: '0.4', label: '0.4' }, { v: '0.6', label: '0.6' }, { v: '0.75', label: '0.75' }, { v: '0.9', label: '0.9' }, { v: '1', label: '1.0' }],
                      baseConsLoraStr, setBaseConsLoraStr)}
            </div>
          )}
        </div>
      )}
      {variant === 'klein' && (
        <div>
          <label style={label}>Turnaround LoRA (base sets) — a trained multi-view / turnaround LoRA (e.g. a Flux.2 Klein 4-view / character-turnaround LoRA on the worker) applied ONLY to the DERIVED views (right/left/back + the mesh A-pose) so they actually rotate instead of copying the front. The front anchor stays untouched. Needs the LoRA file in the worker's models/loras.</label>
          {segRow([{ v: 'on', label: 'On' }, { v: 'off', label: 'Off' }],
                  baseTurnLora ? 'on' : 'off', (v) => setBaseTurnLora(v === 'on'))}
          {baseTurnLora && (
            <div style={{ marginTop: 6 }}>
              <label style={label}>LoRA filename (blank = auto-match a file whose name contains turnaround / multi-view / sprite / 4-view / rotation)</label>
              <input style={input} type="text" value={baseTurnLoraName}
                     placeholder="e.g. flux2_klein_multiview.safetensors"
                     onChange={(e) => setBaseTurnLoraName(e.target.value)} />
              <label style={{ ...label, marginTop: 6 }}>Turnaround LoRA strength</label>
              {segRow([{ v: '', label: '1.0 (def)' }, { v: '0.5', label: '0.5' }, { v: '0.7', label: '0.7' }, { v: '0.85', label: '0.85' }, { v: '1', label: '1.0' }, { v: '1.2', label: '1.2' }, { v: '1.4', label: '1.4' }],
                      baseTurnLoraStr, setBaseTurnLoraStr)}
              <label style={{ ...label, marginTop: 6 }}>Trigger word (optional) — activation text some LoRAs need at the start of the prompt (e.g. matchingpose9b). Blank for a triggerless LoRA. Added only to the derived views.</label>
              <input style={input} type="text" value={baseTurnLoraTrig}
                     placeholder="e.g. matchingpose9b"
                     onChange={(e) => setBaseTurnLoraTrig(e.target.value)} />
            </div>
          )}
        </div>
      )}
      {variant === 'klein' && (
        <div>
          <label style={label}>Pose consistency — where each pose gets its body</label>
          {segRow([{ v: 'lock', label: 'Lock to approved base' }, { v: 'refs', label: 'Use references' }],
                  lockBase ? 'lock' : 'refs', (v) => setLockBase(v === 'lock'))}
        </div>
      )}
      {variant === 'klein' && (
        <div>
          <label style={label}>Auto-fit body proportions from the reference image — scans a clean full-body reference with SAM 3D Body (once per character, cached) and matches the pose mannequin's limb/torso/head proportions to that build, so poses stop copying a generic body. Needs the RBMN SAM3D Proportions node + SAM 3D Body model on the worker; falls back to the description-based build if unavailable. Manual mesh values still win.</label>
          {segRow([{ v: 'on', label: 'On (image-fit)' }, { v: 'off', label: 'Off (description only)' }],
                  autofitProps ? 'on' : 'off', (v) => setAutofitProps(v === 'on'))}
        </div>
      )}
      {variant === 'klein' && bhead('🖼 Framing')}
      {variant === 'klein' && (
        <div>
          <label style={label}>Canvas width (per character) — the shared frame width for BOTH the base and pose images (height ~1216). Wider = more room for plump/muscular/wide characters, base and poses stay the same size (higher = more VRAM/time). Saved with this character (via Save) so a round character keeps its wider frame; the current value here also seeds the default for new characters.</label>
          {segRow([{ v: '832', label: '832 (old)' }, { v: '896', label: '896' }, { v: '960', label: '960' }, { v: '1024', label: '1024 (def)' }, { v: '1152', label: '1152' }, { v: '1280', label: '1280' }],
                  canvasW, setCanvasW)}
          {extremes('narrow frame — slim characters, least VRAM', 'wide frame — room for plump/muscular builds, more VRAM/time')}
        </div>
      )}
      {variant === 'klein' && bhead('✨ Quality & face')}
      {variant === 'klein' && (
        <div>
          <label style={label}>Steps — sampler steps (higher = cleaner skin, fixes scan-line grain on complex skin tones; slower)</label>
          {segRow([{ v: '4', label: '4' }, { v: '6', label: '6' }, { v: '8', label: '8' }, { v: '10', label: '10' }, { v: '12', label: '12' }, { v: '14', label: '14' }, { v: '16', label: '16' }, { v: '20', label: '20' }, { v: '24', label: '24' }],
                  String(kSteps), (v) => setKSteps(parseInt(v, 10) || 6))}
          {extremes('fast draft quality', 'cleaner skin — fixes scan-line grain on complex skin, slower')}
        </div>
      )}
      {variant === 'klein' && (
        <div>
          <label style={label}>Face refine (base) — FaceDetailer sharpen pass on the base face (your poses already get this; uses the YOLO detector, not PuLID)</label>
          {segRow([{ v: 'on', label: 'On' }, { v: 'off', label: 'Off' }],
                  baseFr ? 'on' : 'off', (v) => setBaseFr(v === 'on'))}
        </div>
      )}
      {variant === 'klein' && baseFr && (
        <div>
          <label style={label}>Refine denoise (base) — higher rebuilds more face detail, lower stays truer to the reference (Global = use the ⚙ Settings value)</label>
          {segRow([{ v: '', label: `Global (${frDenoise.trim() || '0.55'})` }, { v: '0.35', label: '0.35' }, { v: '0.4', label: '0.40' }, { v: '0.45', label: '0.45' }, { v: '0.5', label: '0.50' }, { v: '0.55', label: '0.55' }, { v: '0.6', label: '0.60' }, { v: '0.65', label: '0.65' }],
                  baseFrDenoise, setBaseFrDenoise)}
        </div>
      )}
      {variant === 'klein' && baseFr && (
        <div>
          <label style={label}>Refine steps (base) — more = cleaner face, slower (Global = use the ⚙ Settings value)</label>
          {segRow([{ v: '', label: `Global (${frSteps.trim() || '6'})` }, { v: '4', label: '4' }, { v: '6', label: '6' }, { v: '8', label: '8' }, { v: '10', label: '10' }, { v: '12', label: '12' }, { v: '16', label: '16' }, { v: '20', label: '20' }],
                  baseFrSteps, setBaseFrSteps)}
        </div>
      )}
      {variant === 'klein' && bhead('🧽 Article cleanup (SAM3) — remove leftover clothing/jewelry by name')}
      {variant === 'klein' && (
        <div>
          <label style={label}>Article cleanup (SAM3) — segments leftover clothing/jewelry by name and inpaints it to skin, so you can keep Strip release high for max likeness (needs the SAM3 node on the worker)</label>
          {segRow([{ v: 'on', label: 'On' }, { v: 'off', label: 'Off' }],
                  samClean ? 'on' : 'off', (v) => setSamClean(v === 'on'))}
        </div>
      )}
      {variant === 'klein' && samClean && (
        <div>
          <label style={label}>Articles to remove — comma-separated things SAM3 should cut out (blank = jewelry + shirt/collar/sleeve defaults)</label>
          <input style={input} type="text" value={samPrompt}
                 placeholder="necklace, bracelet, watch, ring, earrings, shirt, collar, sleeve"
                 onChange={(e) => setSamPrompt(e.target.value)} />
        </div>
      )}
      {variant === 'klein' && samClean && (
        <div>
          <label style={label}>Detection threshold — lower catches more (may over-select), higher is stricter (Global = 0.40)</label>
          {segRow([{ v: '', label: 'Global' }, { v: '0.25', label: '0.25' }, { v: '0.3', label: '0.30' }, { v: '0.4', label: '0.40' }, { v: '0.5', label: '0.50' }, { v: '0.6', label: '0.60' }],
                  samThresh, setSamThresh)}
        </div>
      )}
    </div>
  );

  const poseSection = (generateLabel: string, onGenerate: () => void, canRun: boolean, required: boolean) => (
    <div style={sectionBox}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <h4 style={{ margin: 0, fontSize: 13 }}>Poses ({selectedPoseSet.length} selected{selectedPoseSet.length > maxPoseSet ? ` — max ${maxPoseSet}!` : ''})</h4>
        <div style={{ flex: 1 }} />
        <button style={{ ...btnGhost, padding: '4px 10px', fontSize: 12 }}
                onClick={() => setDefaultSel(new Set(poseDefaults.map((p) => p.index)))}>All</button>
        <button style={{ ...btnGhost, padding: '4px 10px', fontSize: 12 }}
                onClick={() => setDefaultSel(new Set())}>None</button>
        <button style={{ ...btnGhost, padding: '4px 10px', fontSize: 12, borderColor: '#2a4a3a', color: '#7ee0b0' }}
                title="Select exactly the 4 turnaround views (front/right/left/back) needed as input for a 3D mesh — the mannequin is rotated per view. Best with Qwen."
                onClick={addMeshTurnaround}>🧊 Mesh turnaround</button>
        <button style={{ ...btnGhost, padding: '4px 10px', fontSize: 12 }} onClick={openPoseLibrary}>➕ Pose Library</button>
        {removedDefaults.size > 0 && (
          <button style={{ ...btnGhost, padding: '4px 10px', fontSize: 12 }}
                  onClick={() => setRemovedDefaults(new Set())}>↺ Restore defaults ({removedDefaults.size})</button>
        )}
        <button style={{ ...btnGhost, padding: '4px 10px', fontSize: 12 }} onClick={savePoseSet}>💾 Save pose set</button>
      </div>
      {poseSetMsg && <p style={{ fontSize: 11, color: poseSetMsg.startsWith('⚠') ? '#ff8a8a' : '#5ee08a', margin: '0 0 6px' }}>{poseSetMsg}</p>}
      {!required && (
        <p style={{ fontSize: 12, color: '#8d97a5', margin: '0 0 8px' }}>
          Leave selection as-is to use the step's default pose handling; the selection below replaces the pose set for this run.
        </p>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(76px,1fr))', gap: 6 }}>
        {poseDefaults.filter((p) => !removedDefaults.has(p.index)).map((p) => {
          const on = defaultSel.has(p.index);
          return (
            <div key={p.index}
                 onClick={() => setDefaultSel((prev) => { const n = new Set(prev); if (on) n.delete(p.index); else n.add(p.index); return n; })}
                 style={{ position: 'relative', cursor: 'pointer', border: `2px solid ${on ? '#3b82f6' : '#2a2f3a'}`, borderRadius: 6,
                          background: '#0e1116', padding: 3, textAlign: 'center', opacity: on ? 1 : 0.55 }}>
              <button title="Remove from list (like the node UI's ✕)"
                      style={{ position: 'absolute', top: 2, right: 2, background: '#3a1414', color: '#ff8a8a',
                               border: 'none', borderRadius: 4, fontSize: 10, padding: '1px 5px', cursor: 'pointer', zIndex: 2 }}
                      onClick={(e) => { e.stopPropagation(); setRemovedDefaults((prev) => new Set(prev).add(p.index)); }}>✕</button>
              {donePoseNames.has(p.name) && (
                <span title="Already generated for this character (this context)"
                      style={{ position: 'absolute', top: 2, left: 4, fontSize: 11, color: '#5ee08a', zIndex: 2 }}>✓</span>
              )}
              {p.thumb
                ? <img src={p.thumb} alt={p.name} style={{ width: '100%', borderRadius: 4 }}
                       onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }} />
                : <div style={{ height: 90, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, color: '#a8b2c0' }}>{p.name}</div>}
              <div style={{ fontSize: 10, color: on ? '#cbd2dc' : '#6b7280' }}>{p.name}</div>
            </div>
          );
        })}
      </div>
      {extraPoses.length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(76px,1fr))', gap: 6, marginTop: 8 }}>
          {extraPoses.map((p) => (
            <div key={p.id} style={{ position: 'relative', border: '2px solid #7c5cff', borderRadius: 6,
                     background: '#0e1116', padding: 3, textAlign: 'center' }}>
              {donePoseNames.has(p.name) && (
                <span title="Already generated for this character (this context)"
                      style={{ position: 'absolute', top: 2, left: 4, fontSize: 11, color: '#5ee08a', zIndex: 2 }}>✓</span>
              )}
              <button title="Remove"
                      style={{ position: 'absolute', top: 2, right: 2, background: '#3a1414', color: '#ff8a8a',
                               border: 'none', borderRadius: 4, fontSize: 10, padding: '1px 5px', cursor: 'pointer', zIndex: 2 }}
                      onClick={() => setExtraPoses((prev) => prev.filter((x) => x.id !== p.id))}>✕</button>
              {p.thumbUrl
                ? <img src={p.thumbUrl} alt={p.name} style={{ width: '100%', borderRadius: 4 }}
                       onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }} />
                : <div style={{ height: 90, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, color: '#a8b2c0' }}>{p.name}</div>}
              <div style={{ fontSize: 10, color: '#cbd2dc', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name}</div>
            </div>
          ))}
        </div>
      )}
      {relevantPoseRuns.length > 0 && (
        <div style={{ ...sectionBox, marginTop: 10 }}>
          <label style={{ ...label, fontWeight: 600, color: '#cbd2dc' }}>Previous pose runs</label>
          {relevantPoseRuns.slice().reverse().map((r, i) => (
            <div key={`${r.prompt_id || i}`} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12,
                                                      color: '#9aa4b2', padding: '3px 0' }}>
              <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                    title={(r.pose_names || []).join(', ')}>
                {r.step}{r.costume ? ` · ${r.costume}` : ''} · {(r.pose_names || []).length} pose(s)
                {r.seed ? ` · seed ${r.seed}` : ''}{r.at ? ` · ${r.at.slice(0, 16).replace('T', ' ')}` : ''}
              </span>
              <button style={{ ...btnGhost, padding: '2px 10px', fontSize: 11 }}
                      title="Restore this run's pose selection and seed — then hit Generate to redo it"
                      onClick={() => loadPoseRun(r)}>↻ Load</button>
            </div>
          ))}
        </div>
      )}
      <div style={{ marginTop: 10 }}>
        <UpscalerControls value={upscaler} onChange={setUpscaler} />
      </div>
      {vnccsHosts.length > 1 && (
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, marginTop: 8, color: '#cbd2dc' }}>
          <input type="checkbox" checked={parallelOn} onChange={(e) => setParallelOn(e.target.checked)} />
          ⚡ Split across {vnccsHosts.length} VNCCS workers (chunks run in parallel; later steps route to the workers holding this character)
        </label>
      )}
      {seedRow}
      {variant === 'klein' && kleinCreate && (
        <div style={{ ...sectionBox, marginTop: 10, padding: 12 }}>
          <label style={{ ...label, marginBottom: 4, color: '#cbd2dc', fontWeight: 600 }}>
            Pose render settings — separate from the base preview
          </label>
          <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
            <button style={tabBtn(poseMode === 'simple')} onClick={() => setPoseMode('simple')}>✨ Simple (2D reference)</button>
            <button style={tabBtn(poseMode === 'advanced')} onClick={() => setPoseMode('advanced')}>🔬 Advanced (all dials)</button>
          </div>
          {poseMode === 'simple' ? (
            <div style={{ display: 'grid', gap: 10 }}>
              <p style={{ fontSize: 12, color: '#a8b2c0', margin: 0 }}>
                Simple mode locks everything to the session's tuned recipe — consistency stack 0.7, PuLID 1.0,
                face refine 0.45, gentle cleanup, pose-ref release at default — and applies it per-run WITHOUT
                touching your saved Advanced dials. Pick a reference, a quality, and generate.
              </p>
              {!m3dPoseOn ? (
                <div>
                  <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '10px 0 5px' }}>Pose reference — what drives the pose</div>
                  {segRow([{ v: 'mannequin', label: '🧍 Mannequin (tuned — VNCCS LoRA 0.7)' },
                           { v: 'skeleton', label: '🦴 2D skeleton (stick figure — RefControl 0.9)' }],
                          simpleRef, (v) => setSimpleRef(v === 'skeleton' ? 'skeleton' : 'mannequin'))}
                  {extremes('the proven combo — best body+face consistency so far', 'pure 2D pose geometry — zero CGI material to leak into skin')}
                </div>
              ) : (
                <p style={{ fontSize: 12, color: '#7ee0b0', margin: '6px 0 0', padding: '6px 9px',
                            border: '1px solid #2a4a3a', borderRadius: 6, background: 'rgba(10,22,16,0.6)' }}>
                  🧊 Pose reference is this character's own 3D clay body — the mannequin / 2D-skeleton
                  choice doesn't apply. Tune the pose with Quality below.
                </p>
              )}
              <div>
                <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '10px 0 5px' }}>Quality</div>
                {segRow([{ v: 'fast', label: 'Fast (10 steps)' }, { v: 'balanced', label: 'Balanced (14)' }, { v: 'max', label: 'Max (20)' }],
                        simpleQuality, (v) => setSimpleQuality(v === 'fast' ? 'fast' : v === 'max' ? 'max' : 'balanced'))}
              </div>
              <label style={{ ...toggleBox, margin: 0 }}>
                <input type="checkbox" checked={consistentSkin} onChange={(e) => setConsistentSkin(e.target.checked)} />
                Consistent skin/lighting across the set (one shared seed)
              </label>
              <label style={{ ...toggleBox, margin: 0 }}
                     title="Requires a rigged 3D body (Create tab → 🧊 Generate 3D body). Poses are applied to the character's own 3D mesh and rendered as clay figures — the pose reference has the REAL body shape, ending body drift. Falls back to the mannequin automatically if no rig exists.">
                <input type="checkbox" checked={m3dPoseOn} onChange={(e) => setM3dPoseOn(e.target.checked)} />
                🧊 Use 3D body for pose references (clay renders of THIS character's shape)
              </label>
            </div>
          ) : (
          <>
          <p style={{ fontSize: 12, color: '#a8b2c0', margin: '0 0 6px' }}>
            Poses add mannequin-driven overlap the single clean base never has, so they usually need MORE than the base.
            Raise steps if you see dark lines where skin meets skin (hands are the worst case).
          </p>
          <label style={{ ...toggleBox, margin: '0 0 6px' }}
                 title="Requires a rigged 3D body (Create tab → 🧊 Generate 3D body). Poses are applied to the character's own 3D mesh and rendered as clay figures — the pose reference has the REAL body shape. Falls back to the mannequin automatically if no rig exists.">
            <input type="checkbox" checked={m3dPoseOn} onChange={(e) => setM3dPoseOn(e.target.checked)} />
            🧊 Use 3D body for pose references (clay renders of THIS character's shape)
          </label>
          <div style={{ fontSize: 13, fontWeight: 700, color: '#8ab4ff', margin: '16px 0 8px', paddingTop: 12, borderTop: '1px solid #2a3242' }}>🎛 Render quality</div>
          <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '10px 0 5px' }}>Steps (higher = cleaner overlaps/hands, slower)</div>
          {segRow([{ v: '6', label: '6 (fast)' }, { v: '8', label: '8 (def)' }, { v: '10', label: '10' }, { v: '12', label: '12' }, { v: '14', label: '14' }, { v: '16', label: '16' }, { v: '20', label: '20' }, { v: '24', label: '24 (max)' }],
                  String(poseSteps), (v) => setPoseSteps(parseInt(v, 10) || 8))}
          {extremes('fast, but skin-on-skin overlaps and hands can smear', 'cleaner overlaps and hands, slower')}
          <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '10px 0 5px' }}>Cleanup — strips leftover shoes/jewelry (higher raises guidance; too high hardens edges / adds the rainbow fringe)</div>
          {segRow([{ v: 'off', label: 'Off' }, { v: 'gentle', label: 'Gentle' }, { v: 'strong', label: 'Strong' }],
                  poseCleanup, setPoseCleanup)}
          {extremes('keeps every accessory the base had', 'strips leftover shoes/jewelry hardest — can harden edges / rainbow fringe')}
          <div style={{ fontSize: 13, fontWeight: 700, color: '#8ab4ff', margin: '16px 0 8px', paddingTop: 12, borderTop: '1px solid #2a3242' }}>🕺 Core pose — the pose itself</div>
          {m3dPoseOn && (
            <p style={{ fontSize: 12, color: '#7ee0b0', margin: '0 0 8px', padding: '6px 9px',
                        border: '1px solid #2a4a3a', borderRadius: 6, background: 'rgba(10,22,16,0.6)' }}>
              🧊 Using 3D clay references — Klein's pose reference comes from this character's own
              rigged body. Pose input = Skeleton now works WITH clay (v1.199.80): DWPose extracts a
              stick figure with the character's REAL proportions from the clay render — immune to
              surface smear, no limb ambiguity; pair it with the RefControl LoRA. Mannequin passes
              the clay image through directly (VNCCS PoseStudio LoRA ~0.7, release ~0.85).
            </p>
          )}
          <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '10px 0 5px' }}>Pose input — what Klein sees as reference image 1. <b style={{ color: '#7ee0b0' }}>Depth (recommended)</b> renders a true depth map from this character's own rigged 3D body: pose + VOLUME + height in one signal, driving the RefControl depth LoRA (auto-selects the LoRA, the undistilled base checkpoint, cfg 5 / 20 steps — nothing to dial per character). Skeleton sends a DWPose stick figure instead: accurate joints but ZERO body mass, so heavy/short characters get redrawn on the LoRA's tall-lean prior. Mannequin = pass the capture through directly. <b style={{ color: '#c9a7ff' }}>🟪 Normal (A/B vs Depth)</b> renders a colour-coded surface-normal map from the same rigged body — same recipe, different channel: normals keep a hard boundary where an arm presses against the torso, exactly the case where depth still merges the tucked arm</div>
          {segRow([{ v: '', label: 'Mannequin / clay' }, { v: 'skeleton', label: 'Skeleton (DWPose)' }, { v: 'depth', label: '🟦 Depth (3D body)' }, { v: 'normal', label: '🟪 Normal (3D body)' }],
                  poseInput, setPoseInput)}
          {(poseInput === 'depth' || poseInput === 'normal') && !m3dPoseOn && (
            <p style={{ fontSize: 12, color: '#ffcf8a', margin: '6px 0 0', padding: '6px 9px',
                        border: '1px solid #5a4526', borderRadius: 6, background: 'rgba(40,30,10,0.6)' }}>
              ⚠️ <b>“Use 3D body” is OFF.</b> Depth mode will still run, but the depth map would come
              from the GENERIC mannequin — the wrong body — and the character renders thin and generic.
              The backend now auto-enables the 3D body when this character has a rigged mesh, so you
              are covered either way; turn it on to make the intent explicit.
            </p>
          )}
          {(poseInput === 'depth' || poseInput === 'normal') && (
            <p style={{ fontSize: 12, color: '#7ee0b0', margin: '6px 0 0', padding: '6px 9px',
                        border: '1px solid #2a4a3a', borderRadius: 6, background: 'rgba(10,22,16,0.6)' }}>
              🟦 Depth mode needs <code>flux2_klein_9b_refcontrol_depth.safetensors</code>, 🟪 Normal mode
              <code>flux2_klein_9b_refcontrol_normal.safetensors</code>, in models/loras
              (and ideally <code>flux-2-klein-base-9b-fp8.safetensors</code> in models/diffusion_models).
              If the LoRA is missing the run falls back to the previous pose input and says so in the log.
              Turn 🧊 <b>Use 3D body</b> ON as well so the depth comes from the rigged mesh rather than the
              generic mannequin.
            </p>
          )}
          <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '10px 0 5px' }}>Structure lock — the hard backstop for body size. Starts sampling FROM the pose render instead of empty noise and denoises only the tail, so the silhouette is already in the pixels and cannot drift. Lower = stronger lock but more of the render's own grey survives; 0.7–0.8 repaints texture while holding proportions. Off = reference conditioning only (advisory)</div>
          {segRow([{ v: '', label: 'Off (def)' }, { v: '0.85', label: '0.85 light' }, { v: '0.8', label: '0.8' }, { v: '0.75', label: '0.75' }, { v: '0.7', label: '0.7' }, { v: '0.6', label: '0.6 hard' }],
                  poseSlock, setPoseSlock)}
          {extremes('no lock — body size is negotiable and the LoRA prior can win', 'silhouette welded to the 3D body — but clay tint can survive into the skin')}
          <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '10px 0 5px' }}>Pose reference body — SAM3D renders the character's OWN reconstructed body (front/side/back) as the pose image, matching the real build (the same clean body the VNCCS Pose Studio workflow uses). Mannequin/clay = the older generic/Hunyuan3D body. Needs the RBMN SAM3D Body Views node + SAM 3D Body model on the worker; best for base/turnaround sets.</div>
          {segRow([{ v: '', label: 'Mannequin / clay' }, { v: 'sam3d', label: 'SAM3D reconstructed body' }],
                  poseSource, setPoseSource)}
          <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '10px 0 5px' }}>Pose LoRA — which pose-control LoRA drives the render. {m3dPoseOn ? 'With 3D clay refs, VNCCS PoseStudio matches the clay figure; MatchingPose is the photoreal alternative. (RefControl is skeleton-only, so it’s hidden here.)' : 'RefControl is purpose-trained for photoreal pose transfer from a SKELETON (use with Pose input = Skeleton; its trigger phrase is added automatically). VNCCS = the PoseStudio LoRA (old behavior)'}</div>
          {segRow([{ v: '', label: 'VNCCS PoseStudio' },
                   { v: 'refcontrol_v2_poses.safetensors', label: 'RefControl (skeleton)' },
                   { v: 'flux2_klein_9b_refcontrol_depth.safetensors', label: 'RefControl (depth)' },
                   { v: 'Maching_Pose_9B_Rank256.safetensors', label: 'MatchingPose (photoreal)' }, { v: 'none', label: 'None (Klein native)' }],
                  poseLora, setPoseLora)}
          {(poseInput === 'depth' || poseInput === 'normal') && (
            <div style={{ fontSize: 11.5, color: '#8ea3bd', margin: '4px 0 0' }}>
              Pose input = Depth/Normal selects the RefControl LoRA automatically; this row is ignored in that mode.
            </div>
          )}
          {poseLora !== 'none' && (
            <>
              <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '10px 0 5px' }}>Pose LoRA strength — at 1.0 the VNCCS LoRA imposes its trained style hard enough to draw black lines where skin touches skin; 0.6–0.8 keeps the pose while weakening the style stamp (MatchingPose likes 0.9–1.0)</div>
              {segRow([{ v: '0.5', label: '0.5' }, { v: '0.6', label: '0.6' }, { v: '0.7', label: '0.7' }, { v: '0.8', label: '0.8' }, { v: '0.9', label: '0.9' }, { v: '', label: '1.0 (def)' }],
                      poseLoraStr, setPoseLoraStr)}
              {extremes('weak style stamp — no black contact lines, pose may loosen', 'full pose control — VNCCS at 1.0 draws black lines where skin touches skin')}
            </>
          )}
          <div style={{ fontSize: 13, fontWeight: 700, color: '#8ab4ff', margin: '16px 0 8px', paddingTop: 12, borderTop: '1px solid #2a3242' }}>🙂 Identity & face — how likeness is carried (Global = ⚙ Settings)</div>
          <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '10px 0 5px' }}>PuLID (pose sets) — identity adapter override for pose runs only; Global follows the ⚙ Settings PuLID (strength 1.4 stamps texture into skin — 1.0 is the safe zone for poses)</div>
          {segRow([{ v: '', label: 'Global' }, { v: 'on', label: 'On' }, { v: 'off', label: 'Off' }],
                  posePu, setPosePu)}
          {posePu !== 'off' && (
            <>
              <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '10px 0 5px' }}>PuLID strength (pose)</div>
              {segRow([{ v: '', label: 'Global' }, { v: '0.8', label: '0.8' }, { v: '0.9', label: '0.9' }, { v: '1.0', label: '1.0' }, { v: '1.2', label: '1.2' }, { v: '1.4', label: '1.4' }, { v: '1.6', label: '1.6' }, { v: '1.8', label: '1.8' }, { v: '2.0', label: '2.0' }],
                      posePuStr, setPosePuStr)}
              {extremes('gentle face nudge — safest for skin texture', 'hard identity stamp — likeness up, but waxy/etched skin risk grows fast past 1.6')}
            </>
          )}
          <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '10px 0 5px' }}>Face refine (pose sets) — FaceDetailer pass on each pose face, tuned separately from the base (Global = follow ⚙ Settings)</div>
          {segRow([{ v: '', label: 'Off (def)' }, { v: 'on', label: 'On' }, { v: 'off', label: 'Off' }],
                  poseFr, setPoseFr)}
          {poseFr !== 'off' && (
            <>
              <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '10px 0 5px' }}>Refine denoise (pose) — lower stays truer to the render, higher rebuilds more face</div>
              {segRow([{ v: '', label: 'Global' }, { v: '0.3', label: '0.30' }, { v: '0.35', label: '0.35' }, { v: '0.4', label: '0.40' }, { v: '0.45', label: '0.45' }, { v: '0.5', label: '0.50' }, { v: '0.55', label: '0.55' }, { v: '0.6', label: '0.60' }],
                      poseFrDenoise, setPoseFrDenoise)}
              <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '10px 0 5px' }}>Refine steps (pose)</div>
              {segRow([{ v: '', label: 'Global' }, { v: '4', label: '4' }, { v: '6', label: '6' }, { v: '8', label: '8' }, { v: '10', label: '10' }, { v: '12', label: '12' }],
                      poseFrSteps, setPoseFrSteps)}
              <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '10px 0 5px' }}>Refine guide size (pose) — smaller = less blow-up/shrink on the face crop = least striping risk</div>
              {segRow([{ v: '', label: 'Global' }, { v: '512', label: '512' }, { v: '640', label: '640' }, { v: '768', label: '768' }, { v: '1024', label: '1024' }],
                      poseFrGuide, setPoseFrGuide)}
            </>
          )}
          <div style={{ fontSize: 13, fontWeight: 700, color: '#8ab4ff', margin: '16px 0 8px', paddingTop: 12, borderTop: '1px solid #2a3242' }}>🧩 Consistency extras — default OFF</div>
          <p style={{ fontSize: 12, color: '#c7a86a', margin: '0 0 8px', padding: '6px 9px', border: '1px solid #4a3f2a', borderRadius: 6, background: 'rgba(26,22,10,0.6)' }}>
            These are EXTRA identity/consistency tricks layered on top of the bare Klein pipeline
            (which matches the VNCCS PoseStudio Klein9b workflow: one character reference + “draw
            character from image”). They now default OFF so you can confirm poses hold on the bare
            base, then switch these on ONE at a time to see which your character actually needs.
          </p>
          <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '10px 0 5px' }}>Pose ref release — stop referencing the {m3dPoseOn ? '3D clay' : '3D mannequin'} capture for the last part of the render, so its flat {m3dPoseOn ? 'clay' : 'CGI'} texture can't stamp the skin (the pose itself locks in early). Lower = released sooner = more natural skin, slightly looser pose; Off = referenced the whole run</div>
          {segRow([{ v: '', label: '0.85 (def)' }, { v: '0.9', label: '0.90' }, { v: '0.8', label: '0.80' }, { v: '0.7', label: '0.70' }, { v: '0.6', label: '0.60' }, { v: '1', label: 'Off (Hold)' }],
                  poseRefEnd, setPoseRefEnd)}
          {extremes('holds the mannequin ref the whole run — strictest pose, CGI look can leak into the skin', 'releases it early — natural skin, slightly looser pose')}
          <label style={{ ...toggleBox, margin: '4px 0 0' }}>
            <input type="checkbox" checked={poseBodyMatch} onChange={(e) => setPoseBodyMatch(e.target.checked)} />
            Body-match (ReferenceLatentPlus) — route your body/full references through the masked
            body channel so the pose locks to your source build/shoulders/chest (uses the Body adherence
            + Reference masking values above). Needs the ReferenceLatentPlus node on the worker.
          </label>
          <label style={{ ...toggleBox, margin: '8px 0 0' }}>
            <input type="checkbox" checked={poseFaceCropRef} onChange={(e) => setPoseFaceCropRef(e.target.checked)} />
            Face-crop reference — add a dedicated close-up face crop as an EXTRA reference latch
            (on top of the character reference) to hold facial likeness when the full-body ref leaves
            the face at only a few dozen pixels. Ground truth uses a single reference; this is additive.
          </label>
          <label style={{ ...toggleBox, margin: '4px 0 0' }}>
            <input type="checkbox" checked={consistentSkin} onChange={(e) => setConsistentSkin(e.target.checked)} />
            Consistent skin/lighting across the set — shares ONE seed for every pose (incl. the face refine pass) and pins skin tone + even lighting, so the set doesn't drift in complexion or exposure pose-to-pose (off = each pose gets its own seed for more variety)
          </label>
          <label style={{ ...toggleBox, margin: '8px 0 0' }}>
            <input type="checkbox" checked={consLora} onChange={(e) => setConsLora(e.target.checked)} />
            Stack the Consistency LoRA (dx8152) — triggerless identity/colour-coherence booster layered on top of the pose LoRA; needs a LoRA with “consistency” in its filename on the worker (skipped with a log line otherwise)
          </label>
          {consLora && (
            <>
              <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '10px 0 5px' }}>Consistency strength</div>
              {segRow([{ v: '0.5', label: '0.5' }, { v: '0.6', label: '0.6' }, { v: '', label: '0.7 (def)' }, { v: '0.8', label: '0.8' }, { v: '0.9', label: '0.9' }, { v: '1', label: '1.0' }],
                      consLoraStr, setConsLoraStr)}
              {extremes('subtle identity/colour help', 'strong lock — can stiffen the render')}
            </>
          )}
          </>
          )}
        </div>
      )}
      {/* v1.192: Qwen (VNCCS) mode -> show the VNCCS/Qwen pose-set dials here, NOT the
          Klein ones. These mirror the suite's exact values; this panel is also where
          our own Qwen tweaks will live once every stage is confirmed VNCCS-faithful. */}
      {variant === 'klein' && !kleinCreate && (
        <div style={{ ...sectionBox, marginTop: 10, padding: 12 }}>
          <label style={{ ...label, marginBottom: 4, color: '#cbd2dc', fontWeight: 600 }}>
            🟣 Qwen (VNCCS) pose-set settings — the suite's exact dials
          </label>
          <div style={{ marginBottom: 10, paddingBottom: 10, borderBottom: '1px solid #2a3242' }}>
            <label style={label}>Base body — what the base/clone renders as (the SFW/NSFW toggle only shows in Klein mode, so set it here for Qwen)</label>
            {segRow([{ v: 'underwear', label: '🩲 Underwear (SFW)' }, { v: 'nude', label: '🍑 Nude (NSFW)' }, { v: 'keep', label: '👕 Keep clothes' }],
                    qwenBaseBody, (v) => setQwenBaseBody(v as 'underwear' | 'nude' | 'keep'))}
            <p style={{ fontSize: 11.5, color: '#8d97a5', margin: '4px 0 0' }}>
              <b>Underwear</b> strips to plain white underwear, bare feet. <b>Nude</b> runs the two-pass
              full strip (NSFW). <b>Keep</b> clones the reference outfit as-is.
            </p>
          </div>
          <p style={{ fontSize: 12, color: '#a8b2c0', margin: '0 0 8px' }}>
            Defaults = exactly what VNCCS runs: 4 steps · CFG 1.0 · euler/simple · Lightning turbo LoRA ·
            PoseStudio LoRA 1.0 · 1024px target. The Lightning LoRA is trained for 4-step/CFG-1, so only
            move these if you know why. (This is where our Qwen-specific tweaks will go.)
          </p>
          <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '2px 0 5px' }}>Steps — the Lightning LoRA is TRAINED for 4</div>
          {segRow([{ v: '', label: '4 (VNCCS)' }, { v: '6', label: '6' }, { v: '8', label: '8' }],
                  qwenSteps, setQwenSteps)}
          {extremes('fast, exactly as the suite runs', 'slower — only helps if artifacts appear at 4')}
          <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '12px 0 5px' }}>CFG — 1.0 is required for the 4-step turbo LoRA</div>
          {segRow([{ v: '', label: '1.0 (VNCCS)' }, { v: '1.5', label: '1.5' }, { v: '2', label: '2.0' }],
                  qwenCfg, setQwenCfg)}
          <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '12px 0 5px' }}>PoseStudio LoRA strength — how hard the pose is imposed</div>
          {segRow([{ v: '0.6', label: '0.6' }, { v: '0.8', label: '0.8' }, { v: '', label: '1.0 (VNCCS)' }],
                  qwenPoseLora, setQwenPoseLora)}
          {extremes('looser pose — more of the source stance survives', 'full pose transfer, exactly as the suite runs')}
          <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '12px 0 5px' }}>Target size — the encoder's pixel budget per image</div>
          {segRow([{ v: '768', label: '768' }, { v: '', label: '1024 (VNCCS)' }, { v: '1344', label: '1344' }, { v: '1536', label: '1536' }],
                  qwenTarget, setQwenTarget)}
          {extremes('faster, less VRAM', 'sharper, slower / more VRAM')}
          <div style={{ fontSize: 12.5, fontWeight: 600, color: '#7ee0b0', margin: '14px 0 5px', paddingTop: 10, borderTop: '1px solid #2a3242' }}>💪 Reference strength (body adherence) — how hard the reference body is held. 1.0 = VNCCS-exact; raise it if the build comes out slightly slimmer than your reference. Applies to the base preview AND the pose set (encoder weight, mapped quadratically node-side).</div>
          {segRow([{ v: '0.8', label: '0.8' }, { v: '', label: '1.0 (VNCCS)' }, { v: '1.15', label: '1.15' }, { v: '1.3', label: '1.3' }, { v: '1.45', label: '1.45' }, { v: '1.6', label: '1.6' }],
                  qwenRefWeight, setQwenRefWeight)}
          {extremes('lets the model idealise more — slimmer/cleaner', 'holds your reference build harder — fuller, truer body (too high can stiffen)')}
          <div style={{ fontSize: 12.5, fontWeight: 600, color: '#7ee0b0', margin: '14px 0 5px', paddingTop: 10, borderTop: '1px solid #2a3242' }}>🎩 Headwear room — blank space reserved above the head so tall hats / headdresses have canvas to render into before the top edge clips them. Raise it for stovepipe hats, big feathered pieces or tall hair; the figure sits a little smaller in frame. 14% = prior default. Applies to the base preview AND the pose set.</div>
          {segRow([{ v: '', label: '14% · default' }, { v: '0.22', label: '22% · tall hat' }, { v: '0.28', label: '28% · stovepipe' }, { v: '0.34', label: '34% · headdress' }],
                  qwenHeadwearRoom, setQwenHeadwearRoom)}
          {extremes('tighter frame, bigger figure — normal hats', 'more room up top for very tall headwear — figure sits a bit smaller')}
        </div>
      )}
      {variant === 'klein' && tab === 'create' && kleinCreate && (
        <p style={{ fontSize: 11, color: '#c4b5fd', margin: '8px 0 0' }}>
          🧪 Klein 9B pose engine — poses render via the VNCCS PoseStudio Klein LoRA
          (repo MIUProject/VNCCS_PoseStudio_Klein on the worker). Identity = the ACTIVE base image
          (Clone: up to 4 references, fed natively). GAN upscale applies when the upscaler isn't Off
          (SeedVR maps to GAN here); backgrounds are removed app-side after ingest.
        </p>
      )}
      {variant === 'klein' && !kleinCreate && (
        <p style={{ fontSize: 11, color: '#c4b5fd', margin: '8px 0 0' }}>
          🟣 Qwen (VNCCS) pose engine — poses render exactly through the VNCCS QIE2511 PoseStudio
          pipeline (Qwen-Image-Edit 2511 + Lightning + PoseStudio LoRA), matching the suite's own nodes.
        </p>
      )}
      <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
        {variant === 'klein' && helpBtn('pose')}
        <button style={{ ...btn, flex: 1, opacity: canRun ? 1 : 0.5, cursor: canRun ? 'pointer' : 'not-allowed' }}
                disabled={!canRun} onClick={onGenerate}>
          {busy ? 'Working…' : `${generateLabel} (${selectedPoseSet.length} pose${selectedPoseSet.length === 1 ? '' : 's'})`}
        </button>
      </div>
    </div>
  );

  const poseLibraryPanel = (
    <>
      {libLoading && <p style={{ color: '#9aa4b2', fontSize: 13 }}>Loading…</p>}
      {libNote && <p style={{ fontSize: 12, color: libNote.startsWith('⚠') ? '#ff8a8a' : '#5ee08a' }}>{libNote}</p>}
      {!libLoading && !libPoses.length && (
        <p style={{ color: '#9aa4b2', fontSize: 13 }}>
          The host's pose library is empty. Download a pose pack below (the node UI's built-in
          repositories), or add your own Hugging Face pose pack — bulk custom-pose import lands here next.
        </p>
      )}
      {libRepos.length > 0 && (
        <div style={{ ...sectionBox, marginTop: 0, marginBottom: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', marginBottom: 6 }}>
            <h4 style={{ margin: 0, fontSize: 13 }}>Pose packs (Hugging Face repositories on the host)</h4>
            <div style={{ flex: 1 }} />
            <button style={{ ...btnGhost, padding: '3px 10px', fontSize: 11 }}
                    disabled={!!repoBusy} onClick={() => doRefreshRepo()}>
              {repoBusy === '*' ? 'Downloading…' : '⬇ Download all enabled'}
            </button>
          </div>
          <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
            <input style={{ ...input, fontSize: 12 }} placeholder="Add a Hugging Face repo id (needs a pose_library.json manifest), e.g. user/my-pose-pack"
                   id="vnccs-add-repo-input"
                   onKeyDown={async (e) => {
                     if (e.key !== 'Enter') return;
                     const el = e.target as HTMLInputElement;
                     const rid = el.value.trim();
                     if (!rid) return;
                     setRepoBusy(rid); setLibNote('Adding repository…');
                     try {
                       await api.relayPost('pose_library/repositories/add', { repo_id: rid });
                       el.value = '';
                       await loadPoseLibrary();
                       setLibNote(`✓ Added ${rid} — hit Download to pull its poses.`);
                     } catch (err) {
                       setLibNote(`⚠ ${(err as Error).message}`);
                     } finally { setRepoBusy(''); }
                   }} />
          </div>
          <div style={{ display: 'grid', gap: 6 }}>
            {libRepos.map((r) => (
              <div key={r.repo_id} style={{ display: 'flex', alignItems: 'center', gap: 8,
                   background: '#0e1116', border: '1px solid #2a2f3a', borderRadius: 6, padding: '6px 10px' }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, cursor: 'pointer' }}>
                  <input type="checkbox" checked={!!r.enabled} disabled={!!repoBusy}
                         onChange={() => doToggleRepo(r)} />
                  <span style={{ fontWeight: 600 }}>{r.title || r.repo_id}</span>
                </label>
                <span style={{ fontSize: 12, color: '#8d97a5' }}>{r.pose_count || 0} poses</span>
                <div style={{ flex: 1 }} />
                <button style={{ ...btnGhost, padding: '3px 10px', fontSize: 11 }}
                        disabled={!!repoBusy || !r.enabled} onClick={() => doRefreshRepo(r.repo_id)}>
                  {repoBusy === r.repo_id ? 'Downloading…' : '⬇ Download'}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(150px,1fr))', gap: 8 }}>
        {libPoses.map((p) => (
          <div key={p.id} style={{ background: '#0e1116', border: '1px solid #2a2f3a', borderRadius: 6, padding: 8 }}>
            <img src={api.poseLibraryPreviewUrl(p.name, p.repository, p.category)} alt=""
                 style={{ width: '100%', borderRadius: 4, marginBottom: 4, background: '#161a22' }}
                 onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }} />
            <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 2 }}>{p.name}</div>
            <div style={{ fontSize: 10, color: '#6b7280', marginBottom: 6 }}>{p.repository} · {p.category}</div>
            <button style={{ ...btn, width: '100%', padding: '4px 8px', fontSize: 11 }}
                    onClick={() => addLibraryPose(p)}>Add to pose set</button>
          </div>
        ))}
      </div>
    </>
  );

  useEffect(() => {
    const nm = (createSub === 'clone' ? cloneName.trim() : name.trim());
    if (tab === 'create' && nm) { void refreshM3d(nm); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, createSub, name, cloneName, editingCharId]);

  const exportSettingsJson = () => {
    const payload = {
      exported_at: new Date().toISOString(),
      page: variant === 'klein' ? 'VNCCS Klein Hybrid' : 'VNCCS Native',
      character: (createSub === 'clone' ? cloneName : name) || clothesChar || '',
      settings: buildSettings(),
    };
    const blob = new Blob([JSON.stringify(payload, null, 1)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `studio_settings_${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  const vtCtx: VtCtxT = {
    characterName: (createSub === 'clone' ? cloneName.trim() : name.trim()) || clothesChar.trim(),
    characterInfo: (createSub === 'clone' ? (cloneInfo || {}) : info) as Record<string, unknown>,
    clonerImages: cloneRefs.length ? (cloneRefsForGen() as unknown as Array<Record<string, unknown>>) : null,
    background,
    faceKind, styleCustom,
    baseClothing: runBaseClothing || undefined,
    canvasW: parseInt(canvasW, 10) || null,
    nsfw: createSub === 'clone' ? !!cloneInfo?.nsfw : !!info.nsfw,
    poseOptions: [
      ...poseDefaults.filter((pd) => !removedDefaults.has(pd.index))
        .map((pd) => ({ id: `d${pd.index}`, name: pd.name, pose: pd.pose as Record<string, unknown>, thumb: pd.thumb || undefined })),
      ...extraPoses.map((ep) => ({ id: ep.id, name: ep.name, pose: ep.pose, thumb: ep.thumbUrl })),
    ],
    defaultType: tab === 'create' && createSub === 'clone' ? 'base_clone'
      : tab === 'create' ? 'base_new' : 'pose_set',
  };
  return (
    <div style={{ maxWidth: 1880, margin: '0 auto', padding: 24, color: '#e6e9ee' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <Link to="/studio" style={{ ...btnGhost, textDecoration: 'none' }}>← Studio</Link>
        <h1 style={{ fontSize: 20, margin: 0 }}>{variant === 'klein' ? 'VNCCS Klein Hybrid' : 'VNCCS Native'}</h1>
        {variant === 'klein' && (
          <span style={{ fontSize: 11, padding: '3px 8px', borderRadius: 12, background: '#2a1d40', color: '#c4b5fd' }}>
            experimental — same engine as Native for now; Klein-specific steps land here
          </span>
        )}
        <span style={{
          fontSize: 12, padding: '3px 8px', borderRadius: 12,
          background: host?.online ? '#14351f' : '#3a1414', color: host?.online ? '#5ee08a' : '#ff8a8a',
        }}>
          {host?.online ? `worker online${host.host ? `: ${host.host}` : ''}` : 'no worker detected'}
        </span>
        <div style={{ flex: 1 }} />
        <button style={{ ...btnGhost, borderColor: '#3a4a7a', color: '#c9d6ff' }}
          onClick={() => setShowWorkshop(true)}
          title="Open the Image Workshop — free-form model playground with a shared gallery">
          🎨 Image Workshop
        </button>
        <button style={btnGhost} onClick={() => setShowSettings((s) => !s)}>⚙ Settings</button>
      </div>
      {showWorkshop && <ImageWorkshopLightbox onClose={() => setShowWorkshop(false)} />}

      {debugOn && (
        <div style={{ ...box, marginBottom: 16, padding: 10, display: 'flex', gap: 8, alignItems: 'center',
                      flexWrap: 'wrap', border: '1px solid #6a5b1e', background: '#171507' }}>
          <b style={{ fontSize: 12.5, color: '#f7d154' }}>🐞 Debug</b>
          <button style={{ ...btnGhost, padding: '5px 12px', fontSize: 12 }} onClick={exportSettingsJson}
                  title="Download every studio setting as JSON — paste it to an LLM to troubleshoot">
            📤 Export settings JSON
          </button>
          <button style={{ ...btnGhost, padding: '5px 12px', fontSize: 12, borderColor: '#3b82f6', color: '#cfe0ff' }}
                  onClick={() => setVtOpen(true)}
                  title="Sweep a batch of renders across setting variations, rate them 👍/👎, and get a settings report">
            🧪 Run Settings Variation Test
          </button>
          <span style={{ fontSize: 11, color: '#8a7a3a' }}>
            variation runs save under the app's varitests folder — past runs stay reviewable inside the test panel
          </span>
        </div>
      )}
      {vtOpen && <VaritestPanel ctx={vtCtx} onClose={() => setVtOpen(false)} />}

      {showSettings && (
        <div style={{ ...box, marginBottom: 16 }}>
          <h3 style={{ marginTop: 0, fontSize: 15 }}>Settings — models &amp; generation</h3>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', alignItems: 'center' }}>
            <label style={{ ...toggleBox, marginRight: 'auto' }}>
              <input type="checkbox" checked={debugOn} onChange={(e) => setDebugOn(e.target.checked)} />
              🐞 Debug options — shows a debug toolbox under the header (settings export, Settings Variation Test)
            </label>
            <button style={btn} disabled={savingHost} onClick={saveHost}>{savingHost ? 'Saving…' : 'Save settings'}</button>
            <button style={btnGhost} onClick={resetSettings}
                    title="Reset all these settings back to their base defaults">↺ Reset to defaults</button>
          </div>
          {ctx && (
            <div style={{ marginTop: 12 }}>
              <label style={label}>Edit model (Qwen-Image-Edit — poses/costumes; optional override)</label>
              <select style={input} value={editModel} onChange={(e) => setEditModel(e.target.value)}>
                <option value="">(use graph default)</option>
                {modelOptions.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </div>
          )}
          <div style={{ marginTop: 12 }}>
            <label style={{ ...label, fontWeight: 600, color: '#cbd2dc' }}>
              Klein face consistency — PuLID identity adapter + FaceDetailer face refine
              (Auto = engage whenever the worker has the nodes; see /api/studio/vnccs/klein-status)
            </label>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 8 }}>
              <div><label style={label}>PuLID</label>
                <select style={input} value={kpMode} onChange={(e) => setKpMode(e.target.value)}>
                  <option value="off">Off</option>
                  <option value="on">On (needs 'insightface' on worker)</option>
                </select></div>
              <div><label style={label}>PuLID strength (def 1.4 — up to 3.0; above ~1.6 the identity stamp starts etching texture into the skin, so climb in 0.1 steps and let Face refine clean up)</label>
                <input style={input} type="number" step="0.05" min="0" max="3" value={kpStrength}
                       placeholder="1.4" onChange={(e) => setKpStrength(e.target.value)} /></div>
              <div><label style={label}>Face refine</label>
                <select style={input} value={frMode} onChange={(e) => setFrMode(e.target.value)}>
                  <option value="auto">Auto (recommended)</option>
                  <option value="off">Off</option>
                </select></div>
              <div><label style={label}>Refine denoise (def 0.55)</label>
                <input style={input} type="number" step="0.05" min="0.1" max="0.8" value={frDenoise}
                       placeholder="0.55" onChange={(e) => setFrDenoise(e.target.value)} /></div>
              <div><label style={label}>Refine steps (def 6)</label>
                <input style={input} type="number" step="1" min="2" max="32" value={frSteps}
                       placeholder="6" onChange={(e) => setFrSteps(e.target.value)} /></div>
              <div><label style={label}>Refine guide size (def 768)</label>
                <select style={input} value={frGuide} onChange={(e) => setFrGuide(e.target.value)}>
                  <option value="">Default (768)</option>
                  <option value="512">512 (min striping)</option>
                  <option value="640">640</option>
                  <option value="768">768</option>
                  <option value="1024">1024</option>
                  <option value="1280">1280</option>
                  <option value="1536">1536 (old default)</option>
                </select></div>
            </div>
            <p style={{ fontSize: 12, color: '#8d97a5', margin: '4px 0 0' }}>
              Klein-mode runs only. Raise refine denoise if eyes still look off; lower it (or PuLID strength)
              if the likeness drifts. Guide size = how big the face crop is blown up before refining — the
              shrink-back from big values stamps horizontal “scan lines” into freckled/textured skin
              (worst on small pose faces); 512–768 kills the striping, 1536 is the old detail-max.
              Persisted with “Save host”.
            </p>
          </div>
          <div style={{ marginTop: 12 }}>
            <label style={{ ...label, fontWeight: 600, color: '#cbd2dc' }}>
              Klein base outfit — what the base pose set wears
            </label>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: 8 }}>
              <div><label style={label}>Base clothing</label>
                <select style={input} value={baseClothing} onChange={(e) => setBaseClothing(e.target.value)}>
                  <option value="strip">Strip to a clean base body — underwear, or nude when NSFW (recommended)</option>
                  <option value="keep">Keep / clone the reference's clothing</option>
                </select></div>
            </div>
            <p style={{ fontSize: 12, color: '#8d97a5', margin: '4px 0 0' }}>
              Klein-mode pose runs. “Strip” takes only the character's identity, face and body and renders a
              neutral underwear base (fully nude when the character's NSFW flag is on) so the Clothes /
              Expressions modes can dress it — matching VNCCS Native bases. “Keep” clones the outfit from your
              reference images instead (gaps filled from Analyze-Reference, never invented). Persisted with “Save host”.
            </p>
          </div>
          {ctx && (
            <div style={{ marginTop: 12 }}>
              <label style={{ ...label, fontWeight: 600, color: '#cbd2dc' }}>
                Character generation — base model for Creator / Cloner renders + Emotions (blank = graph default: Anima Base v1.0)
              </label>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: 8 }}>
                <div><label style={label}>Mode</label>
                  <select style={input} value={genMode} onChange={(e) => { setGenMode(e.target.value); setGenModel(''); }}>
                    <option value="">(graph default — anima)</option>
                    <option value="anima">Anima (diffusion model)</option>
                    <option value="illustrious">Illustrious (checkpoint)</option>
                  </select></div>
                <div><label style={label}>{(genMode || 'anima') === 'anima' ? 'Diffusion model' : 'Checkpoint'}</label>
                  <select style={input} value={genModel} onChange={(e) => setGenModel(e.target.value)}>
                    <option value="">(graph default)</option>
                    {(((genMode || 'anima') === 'anima' ? ctx.diffusion_models : ctx.checkpoints) || []).map((m) => (
                      <option key={m} value={m}>{m}</option>
                    ))}
                  </select></div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr 1fr', gap: 8, marginTop: 8 }}>
                <div><label style={label}>Steps</label>
                  <input style={input} value={genSteps} onChange={(e) => setGenSteps(e.target.value)} placeholder="12" /></div>
                <div><label style={label}>CFG</label>
                  <input style={input} value={genCfg} onChange={(e) => setGenCfg(e.target.value)} placeholder="1" /></div>
                <div><label style={label}>Sampler</label>
                  <select style={input} value={genSampler} onChange={(e) => setGenSampler(e.target.value)}>
                    <option value="">(default)</option>
                    {(ctx.samplers || []).map((s) => <option key={s} value={s}>{s}</option>)}
                  </select></div>
                <div><label style={label}>Scheduler</label>
                  <select style={input} value={genScheduler} onChange={(e) => setGenScheduler(e.target.value)}>
                    <option value="">(default)</option>
                    {(ctx.schedulers || []).map((s) => <option key={s} value={s}>{s}</option>)}
                  </select></div>
                <div><label style={label}>Seed (blank = random)</label>
                  <input style={input} value={genSeed} onChange={(e) => setGenSeed(e.target.value)} placeholder="random" /></div>
              </div>
            </div>
          )}
        </div>
      )}

      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        <button style={tabBtn(tab === 'studio')} onClick={() => setTab('studio')}>🏠 Studio</button>
        <button style={tabBtn(tab === 'text2image')} onClick={() => setTab('text2image')}>🧬 Text 2 Image</button>
        {/* 🎬 Video Lab moved to the HOME screen (/video-lab, v1.277.7) — it
            consumes characters rather than creating them. The tab union,
            ?tab=video whitelist and render branch stay, so old deep links
            still work; only the strip button is gone. */}
        <button style={tabBtn(tab === 'create')} onClick={() => setTab('create')}>Create</button>
        <button style={tabBtn(tab === 'clothes')} onClick={() => setTab('clothes')}>Clothes</button>
        <button style={tabBtn(tab === 'emotions')} onClick={() => setTab('emotions')}>Emotions</button>
        <button style={tabBtn(tab === 'poselib')} onClick={() => setTab('poselib')}>Pose Library</button>
        <button style={tabBtn(tab === 'lora')} onClick={() => setTab('lora')}>🎓 LoRA Dataset Gen</button>
        <button style={tabBtn(tab === 'charsheet')} onClick={() => setTab('charsheet')}>🪪 Character Sheet</button>
      </div>

      {tab === 'create' && variant === 'klein' && createEngine === 'klein2' ? (
        <div>
          <div style={{ display: 'flex', gap: 6, marginBottom: 12, alignItems: 'center', flexWrap: 'wrap' }}>
            {expModes && <button style={tabBtn(false)} onClick={() => setCreateEngine('klein')}>🧪 Klein</button>}
            <button style={tabBtn(false)} onClick={() => setCreateEngine('qwen')}>🟣 Qwen (VNCCS)</button>
            {expModes && <button style={tabBtn(true)}>🚀 Klein 2.0</button>}
            <button style={tabBtn(false)} onClick={() => setCreateEngine('klein3')}>🎯 Klein 3.0</button>
          </div>
          <p style={{ fontSize: 11, color: '#8d97a5', margin: '0 0 10px' }}>
            🚀 Klein 2.0: statue-reference posing — rotate the textured 3D statue to the exact
            angle, snapshot it, pair it with a pose image, and let Klein do the rest. No dials.
          </p>
          <Klein2Panel />
        </div>
      ) : tab === 'create' && variant === 'klein' && createEngine === 'klein3' ? (
        <div>
          <div style={{ display: 'flex', gap: 6, marginBottom: 12, alignItems: 'center', flexWrap: 'wrap' }}>
            {expModes && <button style={tabBtn(false)} onClick={() => setCreateEngine('klein')}>🧪 Klein</button>}
            <button style={tabBtn(false)} onClick={() => setCreateEngine('qwen')}>🟣 Qwen (VNCCS)</button>
            {expModes && <button style={tabBtn(false)} onClick={() => setCreateEngine('klein2')}>🚀 Klein 2.0</button>}
            <button style={tabBtn(true)}>🎯 Klein 3.0</button>
          </div>
          <p style={{ fontSize: 11, color: '#8d97a5', margin: '0 0 10px' }}>
            🎯 Klein 3.0: pure reference mode — tagged refs + one base image + a pose image; no 3D
            anywhere. Klein does the rest: "the person from image 1 in the pose from image 2".
          </p>
          <Klein3Panel />
        </div>
      ) : tab === 'lora' ? (
        <LoraPanel />
      ) : tab === 'studio' ? (
        <StudioHubPanel goTo={(t, _slug) => setTab(t as Tab)} />
      ) : tab === 'text2image' ? (
        <Text2ImagePanel />
      ) : tab === 'video' ? (
        <VideoLabPanel />
      ) : tab === 'charsheet' ? (
        <CharacterSheetPanel />
      ) : tab === 'poselib' ? (
        <div style={box}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
            <h3 style={{ margin: 0, fontSize: 15 }}>Pose Library (host)</h3>
            <div style={{ flex: 1 }} />
            <button style={btnGhost} onClick={loadPoseLibrary}>Refresh</button>
          </div>
          <p style={{ color: '#9aa4b2', fontSize: 12, marginTop: 0 }}>
            The VNCCS host's pose library — download Hugging Face pose packs, add your own repos, and pull
            poses into the generation pose set. (Custom pose creation will integrate here.)
          </p>
          {poseLibraryPanel}
        </div>
      ) : (
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(340px,1.1fr) minmax(360px,1.15fr) minmax(340px,1.1fr)', gap: 16, alignItems: 'start' }}>
        <div style={{ ...box, order: 3, minWidth: 0, display: 'grid', gap: 10, alignContent: 'start', position: 'sticky', top: 12, maxHeight: 'calc(100vh - 24px)', overflowY: 'auto' }}>
          <h3 style={{ margin: 0, fontSize: 15 }}>⚙ Generation settings</h3>
          {tab === 'create' && createSub === 'new' && (
            <>
              <Acc title="🧍 Pose set — pick poses & generate" defaultOpen>
                {poseSection('Generate Poses', doGeneratePoses, canGenPoses, true)}
              </Acc>
            </>
          )}
          {tab === 'create' && createSub === 'clone' && (
            <>
              <Acc title="🧍 Pose set — pick poses & generate" defaultOpen>
                {poseSection('Clone character', doCloner, canCloner && selectedPoseSet.length > 0, false)}
              </Acc>
            </>
          )}
          {tab === 'clothes' && (
            <>
              {kleinClothes && (
                <Acc title="🧥 Virtual try-on — dress from garment photos">
                {kleinClothes && (
                  <div style={{ border: '1px solid #2a2f3a', borderRadius: 8, padding: 11, background: '#0e1116', display: 'grid', gap: 11 }}>
                    <label style={{ ...label, marginBottom: 0, fontWeight: 600, color: '#cbd2dc' }}>🧥 Virtual try-on — dress from garment PHOTOS (fal try-on LoRA; combine with or instead of the text slots)</label>
                    <p style={{ fontSize: 12, color: '#a8b2c0', margin: 0 }}>
                      Add up to 3 garment photos per pass (top → bottom order). Person = the Dress target above (or the base render).
                      After a pass, the next pieces LAYER onto the result — keep adding until the outfit is complete.
                      Set a Costume name above and every pass saves as a version of it.
                    </p>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                      <select style={{ ...input, width: 110 }} value={tgarSlot} onChange={(e) => setTgarSlot(e.target.value)}>
                        <option value="top">top</option><option value="bottom">bottom</option>
                        <option value="shoes">shoes</option><option value="accessory">accessory</option>
                        <option value="dress">dress (full)</option>
                      </select>
                      <input type="file" accept="image/*" disabled={tgarBusy || tgar.length >= 3}
                             onChange={(e) => { onUploadTryGarment(e.target.files); e.currentTarget.value = ''; }}
                             style={{ fontSize: 12, color: '#cbd2dc' }} />
                      {tgarBusy && <span style={{ fontSize: 12, color: '#9aa4b2' }}>uploading…</span>}
                    </div>
                    {tgar.length > 0 && (
                      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                        {tgar.map((g, gi) => (
                          <div key={g.ref.name} style={{ width: 160, border: '1px solid #2a2f3a', borderRadius: 6, padding: 4 }}>
                            <img src={g.url} alt={g.slot}
                                 title="Click to view large"
                                 onClick={() => openLightboxGallery(tgar.map((x) => x.url), gi)}
                                 style={{ width: '100%', height: 130, objectFit: 'contain', background: '#12161d',
                                          borderRadius: 4, cursor: 'zoom-in' }} />
                            <div style={{ fontSize: 10, color: '#9aa4b2', margin: '2px 0' }}>{g.slot}</div>
                            <input style={{ ...input, fontSize: 11, padding: '3px 6px' }} placeholder="describe it (optional)"
                                   value={g.desc} onChange={(e) => setTgar((arr) => arr.map((x, xi) => xi === gi ? { ...x, desc: e.target.value } : x))} />
                            <div style={{ display: 'flex', gap: 4, marginTop: 3 }}>
                              <button style={{ ...btnGhost, padding: '2px 8px', fontSize: 11, borderColor: '#3a4a7a', color: '#c9d6ff', opacity: tgarDescIdx !== null ? 0.5 : 1 }}
                                      disabled={tgarDescIdx !== null}
                                      title="Vision-scan THIS garment photo and fill its description (and slot)"
                                      onClick={() => runTgarDescribe(gi)}>
                                {tgarDescIdx === gi ? '🔍 scanning…' : '🔍 Describe'}
                              </button>
                              <button style={{ ...btnGhost, padding: '2px 8px', fontSize: 11 }}
                                      onClick={() => setTgar((arr) => arr.filter((_, xi) => xi !== gi))}>✕ remove</button>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                    <input style={{ ...input, width: 260 }} placeholder="person description (optional)"
                           value={tryPersonDesc} onChange={(e) => setTryPersonDesc(e.target.value)} />
                    <div>
                      <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '2px 0 5px' }}>Try-on steps — the LoRA was TRAINED at 28 (it runs non-distilled, heavier than normal Klein). Lower = faster but mushier garments; only drop if 28 is too slow</div>
                      {segRow([{ v: '20', label: '20 (fast)' }, { v: '24', label: '24' }, { v: '28', label: '28 (trained)' }, { v: '32', label: '32' }, { v: '36', label: '36' }],
                              trySteps, setTrySteps)}
                    {extremes('faster, mushier garments', 'sharper garment detail, slower')}
                    </div>
                    <div>
                      <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '2px 0 5px' }}>Try-on guidance — trained at 2.5. Higher = follows the garment photos harder (risk: stiff/oversaturated); lower = more natural blend (risk: garment drifts from the photo)</div>
                      {segRow([{ v: '1.5', label: '1.5' }, { v: '2', label: '2.0' }, { v: '2.5', label: '2.5 (trained)' }, { v: '3', label: '3.0' }, { v: '3.5', label: '3.5' }],
                              tryGuide, setTryGuide)}
                    {extremes('softer, more natural blend — garment may drift from the photo', 'matches the garment photos hard — can look stiff/oversaturated')}
                    </div>
                    {tryResultRef && (
                      <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: '#cbd2dc', cursor: 'pointer' }}>
                        <input type="checkbox" checked={tryChain} onChange={(e) => setTryChain(e.target.checked)} />
                        Layer on the last result (uncheck to start over from the Dress target)
                      </label>
                    )}
                    <label style={toggleBox}>
                      <input type="checkbox" checked={garmentClean} onChange={(e) => setGarmentClean(e.target.checked)} />
                      🧼 Clean garment photos first (extract onto white background before the try-on — shared with the Dressing settings toggle)
                    </label>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <button style={{ ...btn, padding: '8px 14px', opacity: (!tgar.length || tryBusy || busy) ? 0.5 : 1 }}
                              disabled={!tgar.length || tryBusy || busy} onClick={doTryOn}>
                        {tryBusy ? 'Dressing… (28-step run, give it a few minutes)' : `👗 Try on ${tgar.length || 'the'} piece${tgar.length === 1 ? '' : 's'}`}
                      </button>
                      {tryMsg && <span style={{ fontSize: 12, color: tryMsg.startsWith('✓') ? '#5ee08a' : '#9aa4b2' }}>{tryMsg}</span>}
                    </div>
                    {tryImg && <img src={tryImg} alt="try-on result" style={{ maxWidth: 260, borderRadius: 8, border: '1px solid #2a2f3a' }} />}
                    <p style={{ fontSize: 12, color: '#8d97a5', margin: 0 }}>
                      Runs at the LoRA's trained settings (steps 28, guidance 2.5 — heavier than pose runs).
                      Needs flux-klein-tryon-comfy.safetensors on the worker (installed on all three).
                      Happy with a layered result saved as the costume? Use “Generate clothed set” below to put it on every pose.
                    </p>
                  </div>
                )}
                </Acc>
              )}
              <Acc title="🧍 Clothed pose set — pick poses & generate" defaultOpen>
              {kleinClothes ? (
                <>
                  <p style={{ fontSize: 12, color: '#98a2b2', margin: '4px 0 0', padding: 8, border: '1px dashed #2a2f3a', borderRadius: 6 }}>
                    🧵 Clothed <b>pose set</b>: generates every selected pose wearing the costume you dialed in above. It references the approved DRESSED costume version (the preview you just made), so make a costume preview you're happy with first — each pose then reproduces that exact outfit on your locked body.
                  </p>
                  {poseSection('Generate clothed set', doClothes, canClothes && selectedPoseSet.length > 0, false)}
                </>
              ) : variant === 'klein' ? (
                <>
                  <p style={{ fontSize: 12, color: '#c4b5fd', margin: '4px 0 0', padding: 8, border: '1px dashed #4a3a6a', borderRadius: 6 }}>
                    🟣 Qwen clothed <b>pose set</b> — the VNCCS process, rebuilt app-side: each selected pose's 3D-mannequin render (image 1) + your ACTIVE dressed costume version (image 2) go through Qwen-Image-Edit-2511 with the PoseStudio LoRA, one shared seed for the whole set. Make a costume preview you're happy with first.
                  </p>
                  {poseSection('Generate clothed set (Qwen)', doClothes, canClothes && selectedPoseSet.length > 0, false)}
                </>
              ) : (
                poseSection('Generate costume', doClothes, canClothes && selectedPoseSet.length > 0, false)
              )}
              </Acc>
            </>
          )}
          {tab === 'emotions' && (
            <Acc title="😊 Generate emotions" defaultOpen>
              <button style={{ ...btn, width: '100%', opacity: canEmotions ? 1 : 0.5, cursor: canEmotions ? 'pointer' : 'not-allowed' }}
                      disabled={!canEmotions} onClick={doEmotions}>
                {variant === 'klein'
                  ? `Generate emotions (${emoSelected.length} × sprites)`
                  : `Generate emotions (${emoSelected.length} × ${emoCostumes.length} sets)`}
              </button>
              <p style={{ fontSize: 12, color: '#8d97a5', margin: '6px 0 0' }}>
                Pick the character, clothing sets and emotions in the left column — this runs the batch.
              </p>
            </Acc>
          )}
        </div>
        <div style={{ ...box, position: 'sticky', top: 12, maxHeight: 'calc(100vh - 24px)', overflowY: 'auto' }}>
          {tab === 'create' && (
            <div style={{ display: 'flex', gap: 6, marginBottom: 12, alignItems: 'center', flexWrap: 'wrap' }}>
              {showNewClone && (
                <>
                  <button style={tabBtn(createSub === 'new')} onClick={() => setCreateSub('new')}>New</button>
                  <button style={tabBtn(createSub === 'clone')} onClick={() => setCreateSub('clone')}>Clone</button>
                </>
              )}
              {variant === 'klein' && (
                <>
                  {showNewClone && <span style={{ width: 1, height: 22, background: '#2a2f3a', margin: '0 4px' }} />}
                  {expModes && (
                    <button style={tabBtn(createEngine === 'klein')} onClick={() => setCreateEngine('klein')}>🧪 Klein</button>
                  )}
                  <button style={tabBtn(createEngine === 'qwen')} onClick={() => setCreateEngine('qwen')}>🟣 Qwen (VNCCS)</button>
                  {expModes && (
                    <button style={tabBtn(false)} onClick={() => setCreateEngine('klein2')}>🚀 Klein 2.0</button>
                  )}
                  <button style={tabBtn(false)} onClick={() => setCreateEngine('klein3')}>🎯 Klein 3.0</button>
                </>
              )}
            </div>
          )}
          {tab === 'create' && variant === 'klein' && (
            <p style={{ fontSize: 11, color: '#8d97a5', margin: '0 0 10px' }}>
              {createEngine === 'klein'
                ? '🧪 Klein: our identity chain (multi-ref + face crop + PuLID) — the experimental path we keep tuning.'
                : "🟣 Qwen: VNCCS's exact creation process rebuilt app-side — new characters render text-to-image at 640×1536 (Illustrious/Anima), clones draw from a collage of your reference photos; pose sets run the QIE2511 PoseStudio pass."}
            </p>
          )}
          {tab === 'create' && kleinCreate && presetsBox}
          {tab === 'create' && createSub === 'new' && (
            <>
              <h3 style={{ marginTop: 0, fontSize: 15 }}>Create character</h3>
              <div style={wizBox}>
                <label style={label}>✨ Character Wizard — describe the character idea in plain words</label>
                <textarea style={{ ...input, minHeight: 48 }} value={wizCreateText}
                          onChange={(e) => setWizCreateText(e.target.value)}
                          placeholder="e.g. a battle-scarred elven ranger in her 30s with silver hair" />
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 6 }}>
                  <button style={{ ...btnGhost, opacity: wizBusy || !wizCreateText.trim() ? 0.5 : 1 }}
                          disabled={wizBusy || !wizCreateText.trim()} onClick={runCharacterWizard}>
                    {wizBusy ? 'Wizard…' : '✨ Character Wizard'}
                  </button>
                  {wizMsg && <span style={{ fontSize: 12, color: wizMsg.startsWith('⚠') ? '#ff8a8a' : '#9aa4b2' }}>{wizMsg}</span>}
                </div>
              </div>
              <div style={{ display: 'grid', gap: 10 }}>
                <div><label style={label}>Name *</label>
                  <input style={input} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Zara" /></div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
                  <div><label style={label}>Sex</label>
                    <select style={input} value={info.sex} onChange={(e) => setInfo({ ...info, sex: e.target.value })}>
                      {SEXES.map((s) => <option key={s} value={s}>{s}</option>)}</select></div>
                  <div><label style={label}>Age</label>
                    <input style={input} type="number" min={18} value={info.age ?? 20}
                           onChange={(e) => setInfo({ ...info, age: parseInt(e.target.value || '20', 10) })} /></div>
                  <div><label style={label}>Race</label>
                    <input style={input} value={info.race || ''} onChange={(e) => setInfo({ ...info, race: e.target.value })} /></div>
                </div>
                {(['hair', 'eyes', 'face', 'body', 'skin_color'] as const).map((k) => (
                  <div key={k}><label style={label}>{k.replace('_', ' ')}</label>
                    <input style={input} value={(info[k] as string) || ''}
                           onChange={(e) => setInfo({ ...info, [k]: e.target.value })} /></div>
                ))}
                <div><label style={label}>Additional details</label>
                  <textarea style={{ ...input, minHeight: 54 }} value={info.additional_details || ''}
                            onChange={(e) => setInfo({ ...info, additional_details: e.target.value })} /></div>
                <div><label style={label}>Aesthetics / quality tags</label>
                  <input style={input} value={info.aesthetics || ''}
                         onChange={(e) => setInfo({ ...info, aesthetics: e.target.value })} /></div>
                <div><label style={label}>Background</label>
                  <select style={input} value={background} onChange={(e) => setBackground(e.target.value)}>
                    {BACKGROUNDS.map((b) => <option key={b} value={b}>{b}</option>)}</select></div>
                {kleinCreate && (
                <Acc title="🎛 Base render settings — steps, refine, PuLID, presets…">
                  {kleinBaseControls(!!info.nsfw, (b) => setInfo({ ...info, nsfw: b }),
                    info as Record<string, unknown>,
                    (patch) => setInfo({ ...info, ...patch } as VNCCSCharacterInfoT))}
                </Acc>
                )}
                {!kleinCreate && (
                  <Acc title="🟣 Qwen create settings — the suite's exact t2i stack">
                    <div style={{ border: '1px solid #2a2f3a', borderRadius: 8, padding: 11, background: '#0e1116', display: 'grid', gap: 11 }}>
                      <p style={{ fontSize: 12, color: '#a8b2c0', margin: 0 }}>
                        New characters render text-to-image at 640×1536. <b>Illustrious / Anima</b> use VNCCS's
                        exact tag-template prompt and negatives (their process, field for field). <b>Qwen /
                        Klein 9B / Z-Image / Krea2</b> run the same character semantics adapted to natural-language
                        prose (each model's proven style from our project-side prompting rules — no booster tags,
                        no weight syntax, zeroed negatives at CFG 1). Auto = Illustrious if installed, else Anima.
                      </p>
                      <div>
                        <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '2px 0 5px' }}>t2i model — tag family: Illustrious (euler/normal 20/8, DMD2 turbo → 4/1) · Anima (er_sde/simple 30/4, turbo → 12/1). NL family: Qwen (Lightning 4/1) · Klein 9B (8 steps CFG 1) · Z-Image Turbo (8 steps, res_multistep) · Krea2 Turbo (8 steps, er_sde)</div>
                        {segRow([{ v: '', label: 'Auto (VNCCS)' }, { v: 'illustrious', label: 'Illustrious' }, { v: 'anima', label: 'Anima' },
                                 { v: 'qwen', label: 'Qwen' }, { v: 'klein', label: 'Klein 9B' },
                                 { v: 'zimage', label: 'Z-Image Turbo' }, { v: 'krea2', label: 'Krea2' }],
                                qwenCreateMode, setQwenCreateMode)}
                        {extremes('tag models — anime-leaning, VNCCS-exact prompts', 'NL models — photoreal-leaning, prose prompts (our adaptation)')}
                      </div>
                      <div>
                        <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '2px 0 5px' }}>Steps override — blank = each model's proven default (Illustrious 20/4t · Anima 30/12t · Qwen 4 · Klein 8 · Z-Image 8 · Krea2 8)</div>
                        {segRow([{ v: '', label: 'Model default' }, { v: '4', label: '4' }, { v: '8', label: '8' }, { v: '12', label: '12' }, { v: '20', label: '20' }, { v: '30', label: '30' }],
                                qwenCreateSteps, setQwenCreateSteps)}
                      </div>
                      <div>
                        <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '2px 0 5px' }}>CFG override — blank = each model's default (turbo models NEED 1.0; Illustrious 8 · Anima 4 non-turbo)</div>
                        {segRow([{ v: '', label: 'Model default' }, { v: '1', label: '1.0' }, { v: '2.5', label: '2.5' }, { v: '4', label: '4.0' }, { v: '8', label: '8.0' }],
                                qwenCreateCfg, setQwenCreateCfg)}
                      </div>
                      <label style={toggleBox}>
                        <input type="checkbox" checked={qwenCreateQL} onChange={(e) => setQwenCreateQL(e.target.checked)} />
                        ✨ Per-model quality LoRA stacks — Anima: the app's tuned aesthetic stack (highres boost + masterpieces + rdbt); Klein 9B: the lenovo realism LoRA (our proven t2i look). Untick for a bone-stock / VNCCS-exact render. Auto-skipped when the files aren't on the worker.
                      </label>
                    </div>
                  </Acc>
                )}
                {kleinCreate && baseModePicker}
                <div style={{ display: 'flex', gap: 8 }}>
                  {variant === 'klein' && helpBtn('base')}
                  <button style={{ ...btn, flex: 1, opacity: canPreview ? 1 : 0.5, cursor: canPreview ? 'pointer' : 'not-allowed' }}
                          disabled={!canPreview} onClick={doPreview}>
                    {previewBusy ? 'Rendering preview…'
                      : baseMode === 'mesh' ? '✨ Generate 🧊 Mesh-ready set'
                      : baseMode === 'set' ? '✨ Generate 4-view set'
                      : '✨ Generate Character'}
                  </button>
                  <button style={{ ...btnGreen, opacity: name.trim() ? 1 : 0.5, cursor: name.trim() ? 'pointer' : 'not-allowed' }}
                          disabled={!name.trim()} onClick={doSaveCharacter}>💾 Save</button>
                </div>
                {kleinCreate && variant === 'klein' && (
                  <button style={{ ...btnGhost, opacity: canPreview ? 1 : 0.5, cursor: canPreview ? 'pointer' : 'not-allowed', borderColor: '#4a3a5a', color: '#d8b4fe' }}
                          disabled={!canPreview} onClick={() => doStripPair(false)}
                          title="Generate BOTH the SFW (underwear) and NSFW (nude) stripped base from these references — your 'perfect stripper' pair, saved as two versions">
                    {previewBusy ? 'Working…' : '🩲 SFW + NSFW strip pair'}
                  </button>
                )}
                {previewStatus}
                {baseSetPanel}
                <p style={{ fontSize: 12, color: '#8d97a5', margin: 0 }}>
                  “Generate Character” renders ONE default-pose image (fast). Happy with it? Pick poses below and hit “Generate Poses”.
                </p>
                {saveMsg && <span style={{ fontSize: 12, color: saveMsg.startsWith('⚠') ? '#ff8a8a' : '#5ee08a' }}>{saveMsg}</span>}
              </div>
            </>
          )}

          {tab === 'clothes' && (
            <>
              {variant === 'klein' && (
                <div style={{ display: 'flex', gap: 6, marginBottom: 12, alignItems: 'center' }}>
                  <button style={tabBtn(clothesSub === 'klein')} onClick={() => setClothesSub('klein')}>🧪 Klein</button>
                  <button style={tabBtn(clothesSub === 'qwen')} onClick={() => setClothesSub('qwen')}>🟣 Qwen (VNCCS)</button>
                  <span style={{ fontSize: 11, color: '#8d97a5' }}>
                    {clothesSub === 'klein'
                      ? 'our reference-edit dressing pipeline (base render + garment refs)'
                      : "VNCCS's exact process (Qwen-Image-Edit-2511 + ClothesCore/PoseStudio LoRAs), rebuilt app-side — works with our catalog characters, no worker-side character needed"}
                  </span>
                </div>
              )}
              <h3 style={{ marginTop: 0, fontSize: 15 }}>Design a costume</h3>
              <p style={{ color: '#9aa4b2', fontSize: 12, marginTop: 0 }}>Re-dresses an existing character across the chosen poses (ClothesCore).</p>
              <div style={wizBox}>
                <label style={label}>✨ Clothes Wizard — describe the outfit idea in plain words</label>
                <textarea style={{ ...input, minHeight: 48 }} value={wizClothesText}
                          onChange={(e) => setWizClothesText(e.target.value)}
                          placeholder="e.g. a cyberpunk courier outfit with a neon rain jacket" />
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 6 }}>
                  <button style={{ ...btnGhost, opacity: wizBusy || !wizClothesText.trim() ? 0.5 : 1 }}
                          disabled={wizBusy || !wizClothesText.trim()} onClick={runClothesWizard}>
                    {wizBusy ? 'Wizard…' : '✨ Clothes Wizard'}
                  </button>
                  {wizMsg && <span style={{ fontSize: 12, color: wizMsg.startsWith('⚠') ? '#ff8a8a' : '#9aa4b2' }}>{wizMsg}</span>}
                </div>
              </div>
              <div style={{ display: 'grid', gap: 10 }}>
                <div><label style={label}>Character *</label>{charPicker(clothesChar, setClothesChar)}</div>
                {clothesChar && (
                  <div style={sectionBox}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                      <label style={{ ...label, marginBottom: 0 }}>Outfits for “{clothesChar}”</label>
                      <div style={{ flex: 1 }} />
                      <button style={{ ...btnGhost, padding: '4px 10px', fontSize: 12 }} onClick={openImport}>⬇ Import from character…</button>
                      <button style={{ ...btnGhost, padding: '4px 10px', fontSize: 12 }} onClick={newOutfit}>➕ New outfit</button>
                    </div>
                    {(() => {
                      const names = Array.from(new Set([...Object.keys(costumesMap), ...cloHostCostumes]))
                        .sort((a, b) => a.localeCompare(b));
                      if (!names.length) {
                        return <p style={{ fontSize: 12, color: '#6b7280', margin: 0 }}>No outfits yet — name one below and generate a preview, or import one from another character.</p>;
                      }
                      return (
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(120px,1fr))', gap: 8 }}>
                          {names.map((nm) => {
                            const entry = costumesMap[nm];
                            const vlist = entry?.versions || [];
                            const act = vlist.find((v) => v.id === entry?.active) || vlist[vlist.length - 1];
                            const sel = nm === costumeName.trim();
                            return (
                              <div key={nm} onClick={() => loadOutfit(nm)}
                                   style={{ cursor: 'pointer', border: `2px solid ${sel ? '#3b82f6' : '#2a2f3a'}`,
                                            borderRadius: 8, background: '#0e1116', padding: 6, textAlign: 'center' }}>
                                {act ? (
                                  <img src={act.url} alt={nm} style={{ width: '100%', height: 110, objectFit: 'contain', borderRadius: 6 }} />
                                ) : (
                                  <div style={{ height: 110, display: 'flex', alignItems: 'center', justifyContent: 'center',
                                                color: '#6b7280', fontSize: 11 }}>no preview yet</div>
                                )}
                                <div style={{ fontSize: 12, color: '#cbd2dc', marginTop: 4, fontWeight: sel ? 700 : 400,
                                              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{nm}</div>
                                <div style={{ fontSize: 10, color: '#6b7280' }}>{vlist.length ? `${vlist.length} version${vlist.length === 1 ? '' : 's'}` : '—'}</div>
                              </div>
                            );
                          })}
                        </div>
                      );
                    })()}
                  </div>
                )}
                {clothesChar && variant !== 'klein' && (
                  <div style={sectionBox}>
                    <label style={label}>Mannequin pose — the sprite the costume preview dresses</label>
                    <MannequinStrip character={clothesChar} hosts={cloMannequinHosts}
                                    sel={cloMannequin} onSelect={setCloMannequin}
                                    onOpen={openLightboxGallery}
                                    hint="Cycle through ALL generated base poses (across every worker) — click the image to view it large; arrowing in the lightbox also picks the pose." />
                  </div>
                )}
                {clothesChar && variant === 'klein' && (() => {
                  // v1.157: Klein sprites live in the APP CATALOG (not on the workers) —
                  // pick the dress target from the cataloged pose set; UPSCALED copies
                  // take precedence per pose. '' = the active base render (old behavior).
                  const seen = new Map<string, { asset_id: string; url: string; upscaled?: boolean; pose_name?: string | null }>();
                  for (const o of cloOutputs) {
                    if (o.label.startsWith('clothes/') || o.label.startsWith('emotions/')) continue;
                    for (const im of o.images) {
                      if (!im.pose_name) continue;
                      const prev = seen.get(im.pose_name);
                      if (!prev || (im.upscaled && !prev.upscaled)) seen.set(im.pose_name, im);
                    }
                  }
                  const sprites = Array.from(seen.entries());
                  const baseUrl = (baseVersions.find((b) => b.id === activeBase) || null)?.url || '';
                  const selIm = kcPose ? sprites.find(([, im2]) => im2.asset_id === kcPose)?.[1] : null;
                  const selUrl = selIm ? selIm.url : baseUrl;
                  const selName = selIm ? (selIm.pose_name || 'pose') : 'Base render';
                  return (
                    <div style={sectionBox}>
                      <label style={label}>Dress target — what the costume preview / try-on dresses (upscaled poses are used automatically when they exist)</label>
                      {selUrl && (
                        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 10, marginBottom: 6 }}>
                          <img src={selUrl} alt={selName}
                               onClick={() => openLightboxGallery([selUrl], 0)}
                               style={{ height: 240, maxWidth: '60%', objectFit: 'contain', borderRadius: 8,
                                        border: '2px solid #3b82f6', background: '#0e1116', cursor: 'zoom-in' }} />
                          <div style={{ fontSize: 12, color: '#cbd2dc' }}>
                            <b>{selName}</b>{selIm?.upscaled ? ' · HD' : ''}
                            <div style={{ fontSize: 12, color: '#8d97a5', marginTop: 2 }}>click to zoom · pick another below</div>
                          </div>
                        </div>
                      )}
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'flex-start' }}>
                        <div onClick={() => setKcPose('')}
                             style={{ cursor: 'pointer', width: 66, textAlign: 'center',
                                      border: `2px solid ${kcPose === '' ? '#3b82f6' : '#2a2f3a'}`, borderRadius: 8, padding: 3, background: '#0e1116' }}>
                          {baseUrl ? <img src={baseUrl} alt="base" style={{ width: '100%', height: 70, objectFit: 'contain' }} />
                                   : <div style={{ height: 70, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#6b7280', fontSize: 10 }}>base</div>}
                          <div style={{ fontSize: 9, color: '#cbd2dc' }}>Base</div>
                        </div>
                        {sprites.map(([pn, im]) => (
                          <div key={im.asset_id} onClick={() => setKcPose(im.asset_id)}
                               style={{ cursor: 'pointer', width: 66, textAlign: 'center', position: 'relative',
                                        border: `2px solid ${kcPose === im.asset_id ? '#3b82f6' : '#2a2f3a'}`, borderRadius: 8, padding: 3, background: '#0e1116' }}>
                            <img src={im.url} alt={pn} style={{ width: '100%', height: 70, objectFit: 'contain' }} />
                            <div style={{ fontSize: 9, color: '#cbd2dc', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{pn}</div>
                            {im.upscaled && <span style={{ position: 'absolute', top: 4, right: 4, background: 'rgba(59,130,246,0.9)', color: '#fff', borderRadius: 4, padding: '0 3px', fontSize: 8, fontWeight: 700, lineHeight: '12px' }}>HD</span>}
                          </div>
                        ))}
                      </div>
                      {!sprites.length && <p style={{ fontSize: 12, color: '#8d97a5', margin: '4px 0 0' }}>No cataloged pose sprites yet — generate a pose set on the Create tab; the base render is used meanwhile.</p>}
                    </div>
                  );
                })()}
                <div><label style={label}>Costume name *</label>
                  <input style={input} value={costumeName} onChange={(e) => setCostumeName(e.target.value)}
                         placeholder="e.g. Ranger Outfit" list="vnccs-costume-names" />
                  <datalist id="vnccs-costume-names">
                    {Object.keys(costumesMap).map((c) => <option key={c} value={c} />)}
                  </datalist></div>
                {SLOTS.map((slot) => (
                  <div key={slot}><label style={label}>{slot}</label>
                    <input style={input} value={costume[slot]}
                           onChange={(e) => setCostume({ ...costume, [slot]: e.target.value })}
                           placeholder={`describe the ${slot}`} /></div>
                ))}
                {variant === 'klein' && (
                  <div style={{ border: '1px solid #2a2f3a', borderRadius: 8, padding: 11, background: '#0e1116', display: 'grid', gap: 11 }}>
                    <label style={{ ...label, marginBottom: 0 }}>
                      {kleinClothes
                        ? 'Outfit reference image — optional (dress from a photo; combine with or instead of the text slots above)'
                        : 'Outfit reference image — optional. Qwen CLONE mode: with a photo set, VNCCS dresses the character in "clothes, footwear and accessories from Picture 2" (the text slots are ignored for that pass)'}
                    </label>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                      <input type="file" accept="image/*" disabled={garmentBusy}
                             onChange={(e) => onUploadGarment(e.target.files)} style={{ fontSize: 12, color: '#cbd2dc' }} />
                      {garmentBusy && <span style={{ fontSize: 12, color: '#9aa4b2' }}>uploading…</span>}
                      {garmentRef && !garmentBusy && !garmentRefUrl && (
                        <>
                          <span style={{ fontSize: 12, color: '#5ee08a' }}>✓ {garmentRef.name}</span>
                          <button style={{ ...btnGhost, padding: '4px 8px', fontSize: 12 }} onClick={clearGarment}>✕ clear</button>
                        </>
                      )}
                      {(garmentRef || tgar.length > 0) && (
                        <button style={{ ...btn, padding: '5px 12px', fontSize: 12.5, fontWeight: 600, opacity: wizBusy ? 0.5 : 1 }}
                                disabled={wizBusy}
                                title="Vision-scan the outfit reference image(s) — including any try-on garment photos — and auto-fill the top/bottom/head/face/shoes slots above so the prompt matches the reference"
                                onClick={runGarmentAnalyze}>
                          {wizBusy ? '🔍 scanning…' : '🔍 Describe outfit → fill slots'}
                        </button>
                      )}
                    </div>
                    {garmentRef && garmentRefUrl && (
                      <div style={{ position: 'relative', width: 170 }}>
                        <img src={garmentRefUrl} alt="outfit reference"
                             title="Click to view large"
                             onClick={() => openLightboxGallery([garmentRefUrl], 0)}
                             style={{ width: '100%', maxHeight: 200, objectFit: 'contain', background: '#12161d',
                                      borderRadius: 6, border: '1px solid #2a2f3a', cursor: 'zoom-in', display: 'block' }} />
                        <button title="Remove this outfit reference (also deletes the saved copy)"
                                onClick={clearGarment}
                                style={{ position: 'absolute', top: 4, right: 4, width: 22, height: 22,
                                         borderRadius: 11, border: '1px solid #4a2a2a', background: 'rgba(20,10,10,0.85)',
                                         color: '#ff8a8a', fontSize: 12, lineHeight: '20px', padding: 0, cursor: 'pointer' }}>
                          ✕
                        </button>
                        {garmentPersisted && (
                          <div style={{ fontSize: 10.5, color: '#7fd0a0', marginTop: 3 }}>
                            💾 saved with this outfit — reloads for re-rendering
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}

                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <button style={{ ...btnGhost, padding: '6px 12px', fontSize: 12 }} onClick={() => saveOutfitPrompts()}>💾 Save outfit prompts</button>
                  {costSaveMsg && <span style={{ fontSize: 12, color: costSaveMsg.startsWith('⚠') ? '#ff8a8a' : '#5ee08a' }}>{costSaveMsg}</span>}
                </div>
                <div><label style={label}>Background</label>
                  <select style={input} value={background} onChange={(e) => setBackground(e.target.value)}>
                    {BACKGROUNDS.map((b) => <option key={b} value={b}>{b}</option>)}</select></div>

              {variant === 'klein' && clothesSub === 'qwen' && (
                <Acc title="🟣 Qwen dressing settings — the suite's exact dials">
                  <div style={{ border: '1px solid #2a2f3a', borderRadius: 8, padding: 11, background: '#0e1116', display: 'grid', gap: 11 }}>
                    <p style={{ fontSize: 12, color: '#a8b2c0', margin: 0 }}>
                      Defaults = exactly what VNCCS runs: 4 steps · CFG 1.0 · euler/simple · LoRAs at 1.0 · 1024px
                      target — the Lightning turbo LoRA is trained for 4-step/CFG-1, so only move these if you know why.
                    </p>
                    <div>
                      <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '2px 0 5px' }}>Steps — the Lightning LoRA is TRAINED for 4</div>
                      {segRow([{ v: '', label: '4 (VNCCS)' }, { v: '6', label: '6' }, { v: '8', label: '8' }],
                              qwenSteps, setQwenSteps)}
                      {extremes('fast, exactly as the suite runs', 'slower — only helps if artifacts appear at 4')}
                    </div>
                    <div>
                      <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '2px 0 5px' }}>CFG — 1.0 is required for the 4-step turbo LoRA</div>
                      {segRow([{ v: '', label: '1.0 (VNCCS)' }, { v: '1.5', label: '1.5' }, { v: '2', label: '2.0' }],
                              qwenCfg, setQwenCfg)}
                    </div>
                    <div>
                      <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '2px 0 5px' }}>ClothesCore LoRA strength (costume preview pass)</div>
                      {segRow([{ v: '0.6', label: '0.6' }, { v: '0.8', label: '0.8' }, { v: '', label: '1.0 (VNCCS)' }],
                              qwenClothesLora, setQwenClothesLora)}
                      {extremes('weaker dressing — base look survives more', 'full dressing power, exactly as the suite runs')}
                    </div>
                    <div>
                      <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '2px 0 5px' }}>PoseStudio LoRA strength (clothed set pass)</div>
                      {segRow([{ v: '0.6', label: '0.6' }, { v: '0.8', label: '0.8' }, { v: '', label: '1.0 (VNCCS)' }],
                              qwenPoseLora, setQwenPoseLora)}
                    </div>
                    <div>
                      <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '2px 0 5px' }}>Target size — the encoder's pixel budget per image</div>
                      {segRow([{ v: '768', label: '768' }, { v: '', label: '1024 (VNCCS)' }, { v: '1344', label: '1344' }, { v: '1536', label: '1536' }],
                              qwenTarget, setQwenTarget)}
                      {extremes('faster, less VRAM', 'sharper garments, slower / more VRAM')}
                    </div>
                  </div>
                </Acc>
              )}
              {kleinClothes && (
                <Acc title="👗 Dressing settings — fix skin-blend, strength, steps, negative">
                {kleinClothes && (
                  <div style={{ border: '1px solid #2a2f3a', borderRadius: 8, padding: 11, background: '#0e1116', display: 'grid', gap: 11 }}>
                    <label style={{ ...label, marginBottom: 0, fontWeight: 600, color: '#cbd2dc' }}>Dressing settings — fix clothing blending into skin</label>
                    <div style={{ fontSize: 12, color: '#a8b2c0' }}>Body-ref release — stop showing the model the BARE-SKIN body reference before the garment textures form. 1.0 (old) = skin bleeds through fabric; 0.8 (default) keeps body/identity locked with opaque clothes; lower if garments still look translucent</div>
                    {segRow([{ v: '1', label: 'Off (1.0)' }, { v: '0.9', label: '0.90' }, { v: '', label: '0.80 (def)' }, { v: '0.7', label: '0.70' }, { v: '0.6', label: '0.60' }],
                            dressRefEnd, setDressRefEnd)}
                    {extremes('holds the bare-skin body ref to the END — strongest identity, but skin bleeds through clothes', 'releases it early — most OPAQUE clothing, identity leans on the late face/hair ref')}
                    <div style={{ fontSize: 12, color: '#a8b2c0' }}>Body-ref strength — how hard the base body/identity is enforced (lower = more redraw freedom for the outfit)</div>
                    {segRow([{ v: '0.6', label: '0.6' }, { v: '0.8', label: '0.8' }, { v: '', label: '1.0 (def)' }, { v: '1.2', label: '1.2' }, { v: '1.5', label: '1.5' }],
                            dressStrength, setDressStrength)}
                    {extremes('loose body grip — outfit redraws freely, body may shift', 'iron body grip — body/pose locked, outfit may refuse to change')}
                    <div>
                      <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '2px 0 5px' }}>Dressing steps — sampler steps for the dress pass. Higher = cleaner fabric detail and edges where clothing meets skin, but slower. Global = follow the base Steps setting</div>
                      {segRow([{ v: '', label: 'Global' }, { v: '8', label: '8' }, { v: '10', label: '10' }, { v: '12', label: '12' }, { v: '14', label: '14' }, { v: '16', label: '16' }, { v: '20', label: '20' }],
                              dressSteps, setDressSteps)}
                    {extremes('faster renders', 'cleaner fabric detail + crisper cloth-vs-skin edges, slower')}
                    </div>
                    <div>
                      <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '2px 0 5px' }}>Guidance — how hard the model follows the text prompt. 1.0 (Klein's default) IGNORES the negative box below; raise to 1.5–2.5 to activate it when clothes come out sheer or wrong. Too high hardens edges and dulls skin</div>
                      {segRow([{ v: '', label: '1.0 (off, def)' }, { v: '1.5', label: '1.5' }, { v: '2', label: '2.0' }, { v: '2.5', label: '2.5' }, { v: '3', label: '3.0' }, { v: '4', label: '4.0' }],
                              dressGuide, setDressGuide)}
                    {extremes('natural look, negative box IGNORED', 'obeys prompt + negative hard — edges harden, skin can dull')}
                    </div>
                    <div>
                      <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '2px 0 5px' }}>Negative prompt — things the dress pass must AVOID. Only works when Guidance above is 1.5+ (at 1.0 it's ignored). Typical use: fighting see-through clothing</div>
                      <input style={input} value={dressNeg} onChange={(e) => setDressNeg(e.target.value)}
                             placeholder="e.g. sheer fabric, see-through, translucent clothing, skin showing through fabric, body paint" />
                    </div>
                    <label style={toggleBox}>
                      <input type="checkbox" checked={dressCons} onChange={(e) => setDressCons(e.target.checked)} />
                      🧬 Identity guard — stack the Consistency LoRA while dressing (helps hold the face, body and skin colour while the outfit is redrawn; uses the same LoRA + strength as the pose Consistency stack)
                    </label>
                    <label style={toggleBox}>
                      <input type="checkbox" checked={dressIdLock} onChange={(e) => setDressIdLock(e.target.checked)} />
                      🪪 Late face/hair identity ref — a second, face+hair-only reference held through the FINAL render steps (identity forms late; the body ref still releases at the setting above, so garments stay opaque while the face stops drifting). Recommended ON.
                    </label>
                    <label style={toggleBox}>
                      <input type="checkbox" checked={garmentClean} onChange={(e) => setGarmentClean(e.target.checked)} />
                      🧼 Clean garment photos first — a quick extra pass extracts each garment reference onto a plain white background (person/background removed) before dressing or try-on. Big garment-accuracy win; adds ~20–40s per garment. Recommended ON.
                    </label>
                  </div>
                )}
                </Acc>
              )}
                <button style={{ ...btn, opacity: canCostumePreview ? 1 : 0.5, cursor: canCostumePreview ? 'pointer' : 'not-allowed' }}
                        disabled={!canCostumePreview} onClick={doCostumePreview}>
                  {costPrevBusy ? 'Rendering costume preview…' : '✨ Generate costume preview'}
                </button>
                {variant === 'klein' && (() => {
                  const entry = costumesMap[costumeName.trim()];
                  const vlist = entry?.versions || [];
                  const act = vlist.find((vv) => vv.id === entry?.active) || vlist[vlist.length - 1];
                  return act ? (
                    <button style={{ ...btnGhost, padding: '6px 12px', fontSize: 12 }}
                            title="Inpaint-edit the costume's active version — fix details, add pieces, remove artifacts; each edit saves a new costume version"
                            onClick={() => setEditModal({ src: act.url, charName: clothesChar.trim(), costume: costumeName.trim() })}>
                      🖌 Edit costume image
                    </button>
                  ) : null;
                })()}
                <p style={{ fontSize: 12, color: '#8d97a5', margin: 0 }}>
                  {kleinClothes
                    ? 'Dresses your active BASE render in this outfit — identity, body and pose are preserved, only the clothing changes — from the text slots and/or the reference image above. Happy with it? Pick poses below and generate the full set.'
                    : 'VNCCS/Qwen process: dresses the chosen mannequin pose in this outfit on the selected background via the VNCCS clothes designer nodes (the preview supports Green/Blue — White/Alpha fall back to Green). Happy with it? Pick poses below and generate the full set.'}
                </p>
              </div>

            </>
          )}

          {tab === 'emotions' && (
            <>
              <h3 style={{ marginTop: 0, fontSize: 15 }}>Generate emotions</h3>
              <p style={{ color: '#9aa4b2', fontSize: 12, marginTop: 0 }}>FaceDetailer re-render per (costume × emotion) using EmotionCore.</p>
              <div style={{ display: 'grid', gap: 10 }}>
                <div><label style={label}>Character *</label>{charPicker(emoChar, setEmoChar)}</div>
                {emoChar && (
                  <div style={sectionBox}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                      <label style={{ ...label, marginBottom: 0 }}>Character preview</label>
                      {emoCostumeOpts.length > 0 && (
                        <select style={{ ...input, width: 180 }} value={emoPreviewCostume}
                                onChange={(e) => setEmoPreviewCostume(e.target.value)}>
                          <option value="">(base sprites)</option>
                          {emoCostumeOpts.filter((c) => c !== 'Base').map((c) => <option key={c} value={c}>{c}</option>)}
                        </select>
                      )}
                    </div>
                    <CatalogPoseStrip images={emoPreviewSprites} onOpen={openLightboxGallery}
                                      hint={`${emoPreviewSprites.length} cataloged ${emotionsSub === 'qwen' ? 'Qwen/untagged' : 'Klein/untagged'} sprite(s) for this set — click to view large. This is what the emotion pass will run on.`} />
                  </div>
                )}
                {variant === 'klein' && (
                  <div>
                    <label style={label}>Engine</label>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button style={tabBtn(emotionsSub === 'klein')} onClick={() => setEmotionsSub('klein')}>🧪 Klein</button>
                      <button style={tabBtn(emotionsSub === 'qwen')} onClick={() => setEmotionsSub('qwen')}>🟣 Qwen (VNCCS)</button>
                    </div>
                  </div>
                )}
                <div>
                  <label style={label}>{emotionsSub === 'qwen' ? 'Sets to emote * ' : 'Clothing sets '}({emoCostumes.length} selected — emotions are generated for each)</label>
                  {emoCostumeOpts.length ? (
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                      {emoCostumeOpts.map((c) => {
                        const on = emoCostumesSel.includes(c);
                        return (
                          <label key={c} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12,
                                 background: on ? '#1d2740' : '#0e1116', border: `1px solid ${on ? '#3b82f6' : '#2a2f3a'}`,
                                 borderRadius: 5, padding: '4px 9px', cursor: 'pointer' }}>
                            <input type="checkbox" checked={on}
                                   onChange={() => setEmoCostumesSel((prev) => on ? prev.filter((x) => x !== c) : [...prev, c])} />
                            {c}
                          </label>
                        );
                      })}
                    </div>
                  ) : (
                    <input style={input} value={emoCostumesText} onChange={(e) => setEmoCostumesText(e.target.value)}
                           placeholder="Original, Ranger Outfit (comma-separated)" />
                  )}
                </div>
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <label style={{ ...label, marginBottom: 0 }}>Emotions * ({emoSelected.length} selected)</label>
                    <div style={{ flex: 1 }} />
                    <button style={{ ...btnGhost, padding: '3px 10px', fontSize: 11 }}
                            onClick={() => setEmoSelected(emotionOpts.map((o) => o.value))}>All</button>
                    <button style={{ ...btnGhost, padding: '3px 10px', fontSize: 11 }}
                            onClick={() => setEmoSelected([])}>None</button>
                  </div>
                  {emotionOpts.length ? (
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(88px,1fr))', gap: 6,
                                  maxHeight: 440, overflowY: 'auto', marginTop: 6 }}>
                      {emotionOpts.map((o) => {
                        const on = emoSelected.includes(o.value);
                        const done = emoRuns.some((x) => (x.emotions || []).includes(o.value));
                        return (
                          <div key={o.value} title={`${o.group} — ${o.label}${done ? ' (already generated)' : ''}`}
                               onClick={() => setEmoSelected((prev) => on ? prev.filter((x) => x !== o.value) : [...prev, o.value])}
                               style={{ position: 'relative', cursor: 'pointer', border: `2px solid ${on ? '#3b82f6' : '#2a2f3a'}`,
                                        borderRadius: 8, background: on ? '#1d2740' : '#0e1116', padding: 4, textAlign: 'center' }}>
                            {done && (
                              <span style={{ position: 'absolute', top: 2, right: 4, fontSize: 11, color: '#5ee08a' }}>✓</span>
                            )}
                            <img src={api.emotionImageUrl(o.value)} alt={o.label} loading="lazy"
                                 onError={(e) => {
                                   const el = e.target as HTMLImageElement;
                                   el.style.display = 'none';
                                   const ph = el.nextElementSibling as HTMLElement | null;
                                   if (ph) ph.style.display = 'flex';
                                 }}
                                 style={{ width: '100%', aspectRatio: '1', objectFit: 'cover', borderRadius: 6, display: 'block' }} />
                            <div style={{ display: 'none', width: '100%', aspectRatio: '1', alignItems: 'center',
                                          justifyContent: 'center', fontSize: 10, color: '#6b7280' }}>no image</div>
                            <div style={{ fontSize: 10, color: '#cbd2dc', marginTop: 3, overflow: 'hidden',
                                          textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{o.label}</div>
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <span style={{ fontSize: 12, color: '#9aa4b2' }}>Emotion catalog loads from the host.</span>
                  )}
                </div>
                {emoRuns.length > 0 && (
                  <div style={sectionBox}>
                    <label style={{ ...label, fontWeight: 600, color: '#cbd2dc' }}>Previous emotion runs</label>
                    {emoRuns.slice().reverse().map((r, i) => (
                      <div key={`${r.prompt_id || i}`} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12,
                                                                color: '#9aa4b2', padding: '3px 0' }}>
                        <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {(r.emotions || []).length} emotion(s) × [{(r.costumes || []).join(', ') || '—'}]
                          {r.seed ? ` · seed ${r.seed}` : ''}{r.at ? ` · ${r.at.slice(0, 16).replace('T', ' ')}` : ''}
                        </span>
                        <button style={{ ...btnGhost, padding: '2px 10px', fontSize: 11 }}
                                title="Restore this run's emotions, sets and seed — then hit Generate to redo it"
                                onClick={() => {
                                  setEmoSelected(r.emotions || []);
                                  if (r.costumes?.length) setEmoCostumesSel(r.costumes);
                                  if (r.seed) { setSeedMode('fixed'); setSeedVal(String(r.seed)); }
                                }}>↻ Load</button>
                      </div>
                    ))}
                  </div>
                )}
                {vnccsHosts.length > 1 && (
                  <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: '#cbd2dc' }}>
                    <input type="checkbox" checked={parallelOn} onChange={(e) => setParallelOn(e.target.checked)} />
                    ⚡ Run on every worker holding this character (each processes its own sprites)
                  </label>
                )}
                {seedRow}
                {variant === 'klein' && (
                  <p style={{ fontSize: 11, color: '#c4b5fd', margin: 0 }}>
                    {emotionsSub === 'qwen'
                      ? '🟣 Qwen (VNCCS) emotions — re-renders the FACE of each selected set\'s sprites via VNCCS_QWEN_Detailer ("Change emotion to X"), leaving body/clothes/background intact. Runs on the Qwen-made (or untagged) sprites for each set.'
                      : '🧪 Klein face-inpaint engine — re-renders the FACES of this character\'s cataloged sprites (app-side face detection + Klein inpaint). Runs across all sprites; the clothing-set choice mainly matters for the Qwen engine.'}
                  </p>
                )}
                <button style={{ ...btn, opacity: canEmotions ? 1 : 0.5, cursor: canEmotions ? 'pointer' : 'not-allowed' }}
                        disabled={!canEmotions} onClick={doEmotions}>
                  {busy ? 'Working…' : variant === 'klein'
                    ? `Generate emotions (${emoSelected.length} × sprites)`
                    : `Generate emotions (${emoSelected.length} × ${emoCostumes.length} sets)`}</button>
              </div>
            </>
          )}

          {tab === 'create' && createSub === 'clone' && (
            <>
              <h3 style={{ marginTop: 0, fontSize: 15 }}>Clone from reference</h3>
              <p style={{ color: '#9aa4b2', fontSize: 12, marginTop: 0 }}>Reproduces a character from reference photos (uploaded to the host).</p>
              <div style={{ display: 'grid', gap: 10 }}>
                <div><label style={label}>Name *</label>
                  <input style={input} value={cloneName} onChange={(e) => setCloneName(e.target.value)} placeholder="e.g. Nova" /></div>
                <div>
                  <label style={label}>Reference images * ({cloneRefs.length} uploaded)</label>
                  <input type="file" accept="image/*" multiple disabled={uploading || !host?.online}
                         onChange={(e) => onUploadRefs(e.target.files)} style={{ fontSize: 12, color: '#cbd2dc' }} />
                  {uploading && <span style={{ fontSize: 12, color: '#9aa4b2' }}> uploading…</span>}
                  {cloneRefs.length > 0 && (
                    <div style={{ marginTop: 8 }}>
                      <label style={toggleBox}>
                        <input type="checkbox" checked={enhanceOn} onChange={(e) => setEnhanceOn(e.target.checked)} />
                        ✨ Enhance references (AI upscale + sharpen)
                      </label>
                      {enhanceOn && (
                        <div style={{ ...wizBox, display: 'grid', gap: 8, marginTop: 6 }}>
                          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                            <div><label style={label}>Method</label>
                              <select style={input} value={enhanceMethod} onChange={(e) => setEnhanceMethod(e.target.value as 'gan' | 'seedvr2')}>
                                <option value="gan">GAN upscale (fast)</option>
                                <option value="seedvr2">SeedVR2 (higher quality, slower)</option>
                              </select></div>
                            {enhanceMethod === 'gan' && (
                              <div><label style={label}>Upscale model</label>
                                <select style={input} value={enhanceModel} onChange={(e) => setEnhanceModel(e.target.value)}>
                                  <option value="">Auto (best 4×)</option>
                                  {upscaleModels.map((m) => <option key={m} value={m}>{m}</option>)}
                                </select></div>
                            )}
                            <div><label style={label}>Sharpen</label>
                              <select style={input} value={enhanceSharpen} onChange={(e) => setEnhanceSharpen(e.target.value)}>
                                <option value="off">Off</option>
                                <option value="light">Light</option>
                                <option value="medium">Medium</option>
                                <option value="strong">Strong</option>
                              </select></div>
                            <div><label style={label}>Max size (px)</label>
                              <select style={input} value={String(enhanceMaxSide)} onChange={(e) => setEnhanceMaxSide(parseInt(e.target.value, 10) || 2048)}>
                                <option value="1536">1536</option>
                                <option value="2048">2048</option>
                                <option value="3072">3072</option>
                                <option value="4096">4096</option>
                              </select></div>
                          </div>
                          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                            <button style={{ ...btn, opacity: (enhanceBusy || !host?.online) ? 0.5 : 1, cursor: (enhanceBusy || !host?.online) ? 'not-allowed' : 'pointer' }}
                                    disabled={enhanceBusy || !host?.online} onClick={doEnhanceRefs}>
                              {enhanceBusy ? (enhanceMsg || 'Enhancing…') : `✨ Enhance ${cloneRefs.length} reference${cloneRefs.length === 1 ? '' : 's'}`}
                            </button>
                            {!enhanceBusy && enhanceMsg && <span style={{ fontSize: 12, color: '#a8b2c0' }}>{enhanceMsg}</span>}
                          </div>
                          {enhanceStatus.length > 0 && (
                            <div style={{ display: 'grid', gap: 3 }}>
                              {enhanceStatus.map((es, i) => (
                                <div key={`${es.name}-${i}`} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11,
                                     background: '#0e1116', border: '1px solid #2a2f3a', borderRadius: 6, padding: '4px 8px' }}>
                                  <span style={{ width: 8, height: 8, borderRadius: 8, flexShrink: 0,
                                    background: es.status === 'done' ? '#5ee08a' : es.status === 'error' ? '#ff8a8a'
                                      : es.status === 'running' ? '#3b82f6' : '#4b5563' }} />
                                  <span style={{ color: '#cbd2dc', maxWidth: 130, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{es.name}</span>
                                  {es.host && <span style={{ color: '#6b7280' }}>{shortHost(es.host)}</span>}
                                  <div style={{ flex: 1 }} />
                                  <span style={{ color: es.status === 'error' ? '#ff8a8a' : '#9aa4b2' }}>
                                    {es.status === 'pending' ? 'waiting…' : es.status === 'running' ? (es.detail || 'running…') : es.detail}
                                  </span>
                                </div>
                              ))}
                            </div>
                          )}
                          <p style={{ fontSize: 12, color: '#8d97a5', margin: 0 }}>
                            The AI upscale (GAN or SeedVR2) runs on your ComfyUI worker; the app only sharpens + caps the result, so a low-spec interface machine is fine. Each image's method, size and time shows above. Compare with the Original / Upscaled tabs; with this toggle on the clone run uses the enhanced set.
                          </p>
                        </div>
                      )}
                    </div>
                  )}
                  {cloneRefs.length > 0 && (() => {
                    const hasEnh = Object.keys(enhancedMap).length > 0;
                    const useEnh = enhanceOn && refView === 'upscaled' && hasEnh;
                    const displayRefs = useEnh ? cloneRefs.map((r) => enhancedMap[r.name] || r) : cloneRefs;
                    const idx = Math.min(cloneSelIdx, displayRefs.length - 1);
                    const urls = displayRefs.map((r) => api.viewUrl({ filename: r.name, subfolder: r.subfolder, type: r.type } as api.ResultImageT));
                    const refTab = (v: 'original' | 'upscaled', txt: string) => (
                      <button onClick={() => setRefView(v)}
                              style={{ padding: '3px 12px', fontSize: 12, borderRadius: 6, cursor: 'pointer',
                                       border: `1px solid ${refView === v ? '#3b82f6' : '#2a2f3a'}`,
                                       background: refView === v ? '#12233f' : '#0e1116',
                                       color: refView === v ? '#cbd2dc' : '#9aa4b2' }}>{txt}</button>
                    );
                    return (
                      <div style={{ marginTop: 6 }}>
                        {hasEnh && (
                          <div style={{ display: 'flex', gap: 6, marginBottom: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                            {refTab('original', 'Original')}
                            {refTab('upscaled', 'Upscaled')}
                            <span style={{ fontSize: 12, color: '#8d97a5' }}>
                              {enhanceOn ? (useEnh ? '· clone run will use these' : '· clone run will use originals')
                                : '· enhance toggle is off — originals will be used'}
                            </span>
                          </div>
                        )}
                        <img src={urls[idx]} alt="source preview"
                             onClick={() => openLightboxGallery(urls, idx, (i) => setCloneSelIdx(i))}
                             style={{ display: 'block', width: '100%', maxHeight: 340, objectFit: 'contain',
                                      borderRadius: 8, border: '1px solid #2a2f3a', background: '#0e1116', cursor: 'zoom-in' }} />
                        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 6 }}>
                          {displayRefs.map((r, i) => {
                            const role = (cloneRefs[i]?.role || 'full') as api.RefRole;
                            const rc = roleChip(role);
                            return (
                            <div key={`${r.name}-${i}`} style={{ position: 'relative', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3 }}>
                              <img src={urls[i]} alt={r.name}
                                   onClick={() => setCloneSelIdx(i)}
                                   style={{ width: 64, height: 64, objectFit: 'cover', borderRadius: 6, cursor: 'pointer',
                                            border: `2px solid ${i === idx ? '#3b82f6' : '#2a2f3a'}` }} />
                              <button title="Click to change how this image is used: Full = face + body, Face = face/identity only, Body = body shape only"
                                      onClick={() => cycleRefRole(i)}
                                      style={{ width: 64, padding: '1px 0', fontSize: 10, borderRadius: 5, cursor: 'pointer',
                                               border: `1px solid ${rc.bd}`, background: rc.bg, color: rc.fg }}>{rc.txt}</button>
                              {(() => {
                                const a = String((cloneRefs[i] as { angle?: string })?.angle || '');
                                const lab = a === 'left' ? '◀ Left' : a === 'right' ? 'Right ▶' : a === 'back' ? 'Back' : '∠ front';
                                return (
                                  <button title="Tag this photo's ANGLE so a 4-view / mesh set builds that view from THIS real photo (left / right / back) instead of a rotated guess. '∠ front' = a normal front/identity ref, not a side."
                                          onClick={() => cycleRefAngle(i)}
                                          style={{ width: 64, padding: '1px 0', fontSize: 10, borderRadius: 5, cursor: 'pointer',
                                                   border: `1px solid ${a ? '#6b4a8a' : '#2a2f3a'}`, background: a ? '#221a2e' : '#12161d',
                                                   color: a ? '#d8b4fe' : '#6b7280' }}>{lab}</button>
                                );
                              })()}
                              <button title="Remove this reference"
                                      onClick={() => { setCloneRefs((prev) => prev.filter((_, k) => k !== i)); setCloneSelIdx(0); }}
                                      style={{ position: 'absolute', top: -6, right: -6, width: 18, height: 18, borderRadius: 9,
                                               border: '1px solid #4a2a2a', background: '#20100f', color: '#ff8a8a',
                                               fontSize: 10, lineHeight: '16px', padding: 0, cursor: 'pointer' }}>✕</button>
                              {cloneRefs[i]?.vision && (
                                <button title="View this image's Vision Scan Data"
                                        onClick={(e) => { e.stopPropagation(); setVisionView({ name: r.name, text: cloneRefs[i]?.vision || '' }); }}
                                        style={{ position: 'absolute', top: -6, left: -6, width: 18, height: 18, borderRadius: 9,
                                                 border: '1px solid #2a3a4a', background: '#0e1a2a', color: '#8ab4ff',
                                                 fontSize: 9, lineHeight: '16px', padding: 0, cursor: 'pointer' }}>🔍</button>
                              )}
                            </div>
                            );
                          })}
                          <button style={{ ...btnGhost, padding: '2px 8px', fontSize: 11 }}
                                  onClick={() => { setCloneRefs([]); setCloneSelIdx(0); setEnhancedMap({}); setRefView('original'); }}>clear all</button>
                          <button style={{ ...btnGhost, padding: '2px 8px', fontSize: 11,
                                           borderColor: '#6b5bd0', color: '#c4a8ff',
                                           opacity: (genViewBusy || !host?.online || !cloneRefs.length) ? 0.5 : 1 }}
                                  disabled={genViewBusy || !host?.online || !cloneRefs.length}
                                  title="Use Klein to generate the reference views you're missing (back / left / right) from the ones you have. Each is added to the set tagged with its angle, so the turnaround uses it directly."
                                  onClick={genMissingViews}>
                            {genViewBusy ? 'Generating…' : '✨ Generate missing views'}</button>
                        </div>
                        <p style={{ fontSize: 12, color: '#8d97a5', margin: '4px 0 0' }}>
                          The selected source image is the character preview (node-UI behavior) — click to zoom / arrow through.
                          {enhanceOn ? ' Enhanced references feed the clone run while the ✨ toggle is on.' : ' All uploaded references feed the clone run.'}
                        </p>
                        <p style={{ fontSize: 12, color: '#8d97a5', margin: '3px 0 0' }}>
                          Tap the tag under each image to set its role — <span style={{ color: '#c4a8ff' }}>Full</span> (face + body),
                          <span style={{ color: '#8ab4ff' }}> Face</span> (identity only — best for tight close-ups),
                          <span style={{ color: '#5ee08a' }}> Body</span> (body shape only). Roles are auto-suggested on upload.
                          Body-shape matching from photos activates only when the <code>ReferenceLatentPlus</code> node is installed on your workers; until then it falls back to the current behavior.
                        </p>
                      </div>
                    );
                  })()}
                </div>
                <div style={wizBox}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <button style={{ ...btnGhost, opacity: wizBusy || !cloneRefs.length ? 0.5 : 1 }}
                            disabled={wizBusy || !cloneRefs.length} onClick={runCloneAnalyze}>
                      {wizBusy ? 'Analyzing…' : '🔎 Analyze all references (LLM)'}
                    </button>
                    <span style={{ fontSize: 12, color: wizMsg.startsWith('⚠') ? '#ff8a8a' : '#9aa4b2' }}>
                      {wizMsg || 'Describes the character from ALL uploaded images and fills the fields below, incl. body build & height.'}
                    </span>
                  </div>
                  {cloneInfo && (
                    <div style={{ display: 'grid', gap: 8, marginTop: 10 }}>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
                        <div><label style={label}>Sex</label>
                          <select style={input} value={cloneInfo.sex || 'female'}
                                  onChange={(e) => setCloneInfo({ ...cloneInfo, sex: e.target.value })}>
                            {SEXES.map((s) => <option key={s} value={s}>{s}</option>)}</select></div>
                        <div><label style={label}>Age</label>
                          <input style={input} type="number" value={cloneInfo.age ?? 20}
                                 onChange={(e) => setCloneInfo({ ...cloneInfo, age: parseInt(e.target.value || '20', 10) })} /></div>
                        <div><label style={label}>Race</label>
                          <input style={input} value={cloneInfo.race || ''}
                                 onChange={(e) => setCloneInfo({ ...cloneInfo, race: e.target.value })} /></div>
                      </div>
                      {(['hair', 'eyes', 'face', 'body', 'skin_color', 'additional_details', 'aesthetics'] as const).map((k) => (
                        <div key={k}><label style={label}>{k.replace('_', ' ')}</label>
                          <input style={input} value={(cloneInfo[k] as string) || ''}
                                 onChange={(e) => setCloneInfo({ ...cloneInfo, [k]: e.target.value })} /></div>
                      ))}
                      <div style={{ borderTop: '1px solid #2a2f3a', marginTop: 4, paddingTop: 8 }}>
                        <label style={{ ...label, fontWeight: 600, color: '#cbd2dc' }}>
                          Body Helper - height &amp; build (fed to Klein as proportions)
                        </label>
                        <div style={{ display: 'grid', gridTemplateColumns: '0.8fr 0.8fr 1fr 1.2fr', gap: 8 }}>
                          <div><label style={label}>Height (ft)</label>
                            <input style={input} type="number" min="2" max="8"
                                   value={cmFromHeight(cloneInfo.height) ? Math.floor(Math.round(cmFromHeight(cloneInfo.height) / 2.54) / 12) : ''}
                                   onChange={(e) => { const ft = parseInt(e.target.value || '0', 10); const tot = cmFromHeight(cloneInfo.height) ? Math.round(cmFromHeight(cloneInfo.height) / 2.54) : 0; const nt = ft * 12 + (tot % 12); setCloneInfo({ ...cloneInfo, height: nt > 0 ? `${Math.round(nt * 2.54)} cm` : '' }); }} /></div>
                          <div><label style={label}>Height (in)</label>
                            <input style={input} type="number" min="0" max="11"
                                   value={cmFromHeight(cloneInfo.height) ? Math.round(cmFromHeight(cloneInfo.height) / 2.54) % 12 : ''}
                                   onChange={(e) => { const inch = parseInt(e.target.value || '0', 10); const tot = cmFromHeight(cloneInfo.height) ? Math.round(cmFromHeight(cloneInfo.height) / 2.54) : 0; const nt = Math.floor(tot / 12) * 12 + inch; setCloneInfo({ ...cloneInfo, height: nt > 0 ? `${Math.round(nt * 2.54)} cm` : '' }); }} /></div>
                          <div><label style={label}>Height (cm)</label>
                            <input style={input} type="number" min="80" max="230"
                                   value={cmFromHeight(cloneInfo.height) || ''}
                                   onChange={(e) => { const cm = parseFloat(e.target.value); setCloneInfo({ ...cloneInfo, height: cm > 0 ? `${Math.round(cm)} cm` : '' }); }} /></div>
                          <div><label style={label}>&nbsp;</label>
                            <div style={{ ...input, display: 'flex', alignItems: 'center', color: '#9aa4b2' }}>
                              {cmFromHeight(cloneInfo.height)
                                ? `${Math.floor(Math.round(cmFromHeight(cloneInfo.height) / 2.54) / 12)}ft ${Math.round(cmFromHeight(cloneInfo.height) / 2.54) % 12}in (${Math.round(cmFromHeight(cloneInfo.height))}cm)`
                                : 'not set'}
                            </div></div>
                        </div>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 6 }}>
                          <div><label style={label}>+ Body type (adds a tag to "body")</label>
                            <select style={input} value=""
                                    onChange={(e) => { const tag = e.target.value; if (!tag) return; const parts = String(cloneInfo.body || '').split(',').map((t) => t.trim()).filter(Boolean); if (!parts.some((p) => p.toLowerCase() === tag.toLowerCase())) parts.push(tag); setCloneInfo({ ...cloneInfo, body: parts.join(', ') }); }}>
                              <option value="">choose...</option>
                              {['petite', 'slim', 'slender', 'athletic', 'toned', 'fit', 'average build', 'curvy', 'voluptuous', 'thick', 'chubby', 'plump', 'plus-sized', 'muscular', 'soft, untoned'].map((o) => <option key={o} value={o}>{o}</option>)}
                            </select></div>
                          <div><label style={label}>+ Chest / bust (adds a tag to "body")</label>
                            <select style={input} value=""
                                    onChange={(e) => { const tag = e.target.value; if (!tag) return; const parts = String(cloneInfo.body || '').split(',').map((t) => t.trim()).filter(Boolean); if (!parts.some((p) => p.toLowerCase() === tag.toLowerCase())) parts.push(tag); setCloneInfo({ ...cloneInfo, body: parts.join(', ') }); }}>
                              <option value="">choose...</option>
                              {['flat chest', 'small breasts', 'medium breasts', 'large breasts', 'huge breasts', 'perky breasts', 'natural breasts', 'sagging breasts'].map((o) => <option key={o} value={o}>{o}</option>)}
                            </select></div>
                        </div>
                        <p style={{ fontSize: 12, color: '#8d97a5', margin: '4px 0 0' }}>
                          Set height in ft/in OR cm (they stay in sync). The number DRIVES the pose
                          mannequin's stature directly, relative to the character's sex (a 5'6" man
                          renders short); it is also kept for LoRA captions. The body-type and chest
                          pickers append Danbooru-style tags into the "body" field above (the models read those tags
                          well) - edit "body" directly to fine-tune. "Analyze all references" fills it from your refs.
                        </p>
                      </div>
                    </div>
                  )}
                </div>
                <div><label style={label}>Background</label>
                  <select style={input} value={background} onChange={(e) => setBackground(e.target.value)}>
                    {BACKGROUNDS.map((b) => <option key={b} value={b}>{b}</option>)}</select></div>
                {kleinCreate && (
                <Acc title="🎛 Base render settings — steps, refine, PuLID, presets…">
                  {kleinBaseControls(!!cloneInfo?.nsfw, (b) => setCloneInfo({ ...(cloneInfo || {}), nsfw: b }),
                    (cloneInfo || {}) as Record<string, unknown>,
                    (patch) => setCloneInfo({ ...(cloneInfo || {}), ...patch } as VNCCSCharacterInfoT))}
                </Acc>
                )}
                {!kleinCreate && (
                  <Acc title="🟣 Qwen clone settings — collage → undress → neutral render">
                    <div style={{ border: '1px solid #2a2f3a', borderRadius: 8, padding: 11, background: '#0e1116', display: 'grid', gap: 11 }}>
                      <p style={{ fontSize: 12, color: '#a8b2c0', margin: 0 }}>
                        VNCCS's cloner: your reference photos are packed into ONE collage grid and every render
                        draws the character from it. "Generate Preview" makes the neutral-pose base: with Base
                        outfit = <b>Strip</b> it first runs the suite's remove-clothes edit ("Undress character",
                        ClothesCore LoRA) on the collage — the Naked branch; <b>Keep</b> draws them in their
                        photo clothes — the Original branch. Pose sets then draw every pose from the
                        ACTIVE base.
                      </p>
                      <div>
                        <div style={{ fontSize: 12.5, fontWeight: 600, color: '#c9d3df', margin: '2px 0 5px' }}>Base outfit — which cloner branch the preview runs</div>
                        {segRow([{ v: '', label: 'Strip (Naked branch, def)' },
                                 { v: 'strip', label: 'Strip' },
                                 { v: 'keep', label: 'Keep (Original branch)' }], runBaseClothing, setRunBaseClothing)}
                        {extremes('undress first — clean base for future costumes', 'keep their photo clothes — fastest faithful look')}
                      </div>
                    </div>
                  </Acc>
                )}
                {kleinCreate && baseModePicker}
                <div style={{ display: 'flex', gap: 8 }}>
                  {variant === 'klein' && helpBtn('clone')}
                  <button style={{ ...btn, flex: 1, opacity: canClonePreview ? 1 : 0.5, cursor: canClonePreview ? 'pointer' : 'not-allowed' }}
                          disabled={!canClonePreview} onClick={doClonePreview}>
                    {previewBusy ? 'Rendering preview…'
                      : baseMode === 'mesh' ? '✨ Generate 🧊 Mesh-ready set'
                      : baseMode === 'set' ? '✨ Generate 4-view set'
                      : '✨ Generate Preview'}
                  </button>
                  <button style={{ ...btnGreen, flex: 1, opacity: cloneName.trim() ? 1 : 0.5, cursor: cloneName.trim() ? 'pointer' : 'not-allowed' }}
                          disabled={!cloneName.trim()} onClick={() => doSaveClone()}>💾 Save analyzed fields</button>
                </div>
                {kleinCreate && variant === 'klein' && (
                  <button style={{ ...btnGhost, opacity: canClonePreview ? 1 : 0.5, cursor: canClonePreview ? 'pointer' : 'not-allowed', borderColor: '#4a3a5a', color: '#d8b4fe' }}
                          disabled={!canClonePreview} onClick={() => doStripPair(true)}
                          title="Generate BOTH the SFW (underwear) and NSFW (nude) stripped base from your references — the 'perfect stripper' pair, saved as two versions">
                    {previewBusy ? 'Working…' : '🩲 SFW + NSFW strip pair'}
                  </button>
                )}
                {previewStatus}
                {baseSetPanel}
                <p style={{ fontSize: 12, color: '#8d97a5', margin: 0 }}>
                  “✨ Generate Preview” renders the character ONCE from your references (Klein: full identity chain —
                  multi-ref + face crop + PuLID) and files it as a base version. Review the likeness, then pick poses
                  below and hit “Clone character”.
                </p>
                {saveMsg && <span style={{ fontSize: 12, color: saveMsg.startsWith('⚠') ? '#ff8a8a' : '#5ee08a' }}>{saveMsg}</span>}
              </div>
            </>
          )}
          {!host?.online && <span style={{ fontSize: 12, color: '#ff8a8a' }}>No VNCCS host — pin one in Settings.</span>}
        </div>

        <div style={{ ...box, position: 'sticky', top: 12, maxHeight: 'calc(100vh - 24px)', overflowY: 'auto' }}>
          <h3 style={{ marginTop: 0, fontSize: 15 }}>Results</h3>
          {previewBusy && <LiveBanner icon="✨" secs={previewElapsed}
            text={`Rendering base preview${host?.host ? ` on ${shortHost(host.host)}` : ''}…`} />}
          {costPrevBusy && <LiveBanner icon="👗" secs={costPrevElapsed}
            text={`Dressing costume preview${host?.host ? ` on ${shortHost(host.host)}` : ''}…`} />}
          {tryBusy && <LiveBanner icon="🧥" secs={tryElapsed}
            text={`Virtual try-on${host?.host ? ` on ${shortHost(host.host)}` : ''} — 28-step pass, give it a few minutes…`} />}
          {statusText && <p style={{ color: '#9aa4b2', fontSize: 13 }}>{statusText}</p>}
          {errMsg && <p style={{ color: '#ff8a8a', fontSize: 13 }}>⚠ {errMsg}</p>}
          {ingestMsg && <p style={{ fontSize: 13, color: '#5ee08a' }}>✓ {ingestMsg}</p>}
          {chunks.length > 0 && (() => {
            const doneN = chunks.filter((c) => c.status === 'done' || c.status === 'error').length;
            const errsN = chunks.filter((c) => c.status === 'error').length;
            const pct = Math.round((doneN / chunks.length) * 100);
            const totalImgs = chunks.reduce((n, c) => n + c.images.length, 0);
            const nowMs = nowTick || Date.now();
            const overall = runStarted ? Math.round((nowMs - runStarted) / 1000) : 0;
            const hostList = Array.from(new Set(chunks.map((c) => c.host).filter(Boolean)));
            const HOST_COLORS = ['#8ab4ff', '#7ee0b0', '#f7b955', '#e08ae0', '#8ae0d8', '#ffa3a3'];
            const hostColor = (h: string) => HOST_COLORS[Math.max(0, hostList.indexOf(h)) % HOST_COLORS.length];
            return (
              <div style={{ marginBottom: 12, borderRadius: 10, padding: 12,
                            border: `1px solid ${busy ? '#3b82f6' : errsN ? '#b91c1c' : '#166534'}`,
                            background: busy ? 'linear-gradient(160deg, #0d1526 0%, #0e1116 65%)' : '#0e1116',
                            boxShadow: busy ? '0 0 14px rgba(59,130,246,0.30)' : 'none' }}>
                <style>{'@keyframes rbmnSpin { to { transform: rotate(360deg); } } @keyframes rbmnPulse { 0%,100% { opacity: 1; } 50% { opacity: 0.25; } }'}</style>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                  {busy ? (
                    <span style={{ width: 16, height: 16, borderRadius: '50%', flexShrink: 0,
                                   border: '2px solid rgba(59,130,246,0.25)', borderTopColor: '#3b82f6',
                                   display: 'inline-block', animation: 'rbmnSpin 0.9s linear infinite' }} />
                  ) : (
                    <span style={{ fontSize: 15 }}>{errsN ? '⚠' : '✅'}</span>
                  )}
                  <span style={{ fontSize: 14, fontWeight: 700, color: busy ? '#cfe0ff' : errsN ? '#ffb4b4' : '#8fe6ae' }}>
                    {busy
                      ? (phase === 'submitting' ? 'Submitting run…'
                        : `Generating — ${chunks.length} chunk${chunks.length === 1 ? '' : 's'}${hostList.length ? ` across ${hostList.length} worker${hostList.length === 1 ? '' : 's'}` : ''}`)
                      : errsN ? `Run finished — ${errsN} chunk${errsN === 1 ? '' : 's'} failed` : 'Run finished'}
                  </span>
                  <div style={{ flex: 1 }} />
                  {runStarted > 0 && (
                    <span title="Total run time" style={{ fontSize: 14, fontFamily: 'monospace', fontWeight: 700,
                          color: busy ? '#8ab4ff' : '#9aa4b2' }}>⏱ {fmtMMSS(overall)}</span>
                  )}
                  <span style={{ fontSize: 12, color: '#9aa4b2' }}>
                    {doneN}/{chunks.length} · {pct}%{totalImgs ? ` · ${totalImgs} img${totalImgs === 1 ? '' : 's'}` : ''}
                  </span>
                </div>
                <div style={{ height: 10, background: '#0a0d12', border: '1px solid #2a2f3a', borderRadius: 6, overflow: 'hidden', marginTop: 8 }}>
                  <div style={{ height: '100%', width: `${pct}%`, transition: 'width 0.6s',
                                background: pct === 100 ? '#166534' : 'linear-gradient(90deg, #2563eb, #3b82f6, #60a5fa)' }} />
                </div>
                {(busy || phase === 'error') && (
                  <div style={{ marginTop: 8, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    {runId && busy && (
                      <button onClick={stopRun} disabled={stopping}
                        style={{ background: stopping ? '#7f1d1d' : '#b91c1c', color: '#fff', border: '1px solid #ef4444',
                                 borderRadius: 6, padding: '5px 12px', fontSize: 12, fontWeight: 600,
                                 cursor: stopping ? 'default' : 'pointer' }}>
                        {stopping ? 'Stopping…' : '■ Stop run'}
                      </button>
                    )}
                    <button onClick={dismissRun}
                      title="Clear this status view. Non-destructive — any real run keeps going on the workers. Use this if a reconnected run looks stuck and is blocking new previews/generations."
                      style={{ background: 'transparent', color: '#9aa4b2', border: '1px solid #3a3f4a',
                               borderRadius: 6, padding: '5px 10px', fontSize: 11, cursor: 'pointer' }}>
                      ✕ Reset status view
                    </button>
                  </div>
                )}
                <div style={{ display: 'grid', gap: 5, marginTop: 8 }}>
                  {chunks.map((c, i) => {
                    const queued = c.status === 'running' && !c.host;
                    const live = (c.status === 'running' || c.status === 'ingesting');
                    const secs = c.startedAt
                      ? Math.round(((c.finishedAt || nowMs) - c.startedAt) / 1000) : 0;
                    const edge = c.status === 'done' ? '#166534' : c.status === 'error' ? '#b91c1c'
                      : c.status === 'ingesting' ? '#a16207' : queued ? '#4b5563' : '#2563eb';
                    return (
                      <div key={`${c.prompt_id}-${i}`}
                           style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12,
                                    background: '#0a0d12', border: '1px solid #232936', borderRadius: 6,
                                    borderLeft: `3px solid ${edge}`, padding: '6px 9px' }}>
                        {live && !queued ? (
                          <span style={{ width: 11, height: 11, borderRadius: '50%', flexShrink: 0,
                                         border: '2px solid rgba(59,130,246,0.25)',
                                         borderTopColor: c.status === 'ingesting' ? '#eab308' : '#3b82f6',
                                         display: 'inline-block', animation: 'rbmnSpin 0.9s linear infinite' }} />
                        ) : (
                          <span style={{ width: 9, height: 9, borderRadius: 9, flexShrink: 0,
                                         background: c.status === 'done' ? '#5ee08a' : c.status === 'error' ? '#ff8a8a' : '#6b7280',
                                         animation: queued ? 'rbmnPulse 1.4s ease-in-out infinite' : 'none' }} />
                        )}
                        <span style={{ fontWeight: 700, color: c.host ? hostColor(c.host) : '#8a94a6',
                                       background: 'rgba(255,255,255,0.04)', border: '1px solid #2a3242',
                                       borderRadius: 10, padding: '1px 8px', whiteSpace: 'nowrap' }}>
                          {c.host ? shortHost(c.host) : 'in queue'}
                        </span>
                        <span style={{ color: '#8a94a6', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.label}</span>
                        <div style={{ flex: 1 }} />
                        {c.startedAt && (live || c.finishedAt) ? (
                          <span title={c.finishedAt ? 'Chunk total time' : 'Running time'}
                                style={{ fontFamily: 'monospace', fontWeight: 700, fontSize: 12,
                                         color: live ? '#8ab4ff' : '#6b7280' }}>{fmtMMSS(secs)}</span>
                        ) : null}
                        <span style={{ color: c.status === 'error' ? '#ff8a8a' : c.status === 'done' ? '#5ee08a' : '#9aa4b2', whiteSpace: 'nowrap' }}>
                          {queued ? 'waiting for a worker…'
                            : c.status === 'running' ? (c.images.length ? `rendering · ${c.images.length} img${c.images.length === 1 ? '' : 's'}` : 'rendering')
                            : c.status === 'ingesting' ? 'filing into library…'
                            : c.status === 'done' ? (c.images.length ? `done · ${c.images.length} img${c.images.length === 1 ? '' : 's'}` : `done · ${c.note || 'filed'}`)
                            : `error${c.note ? ` — ${c.note.slice(0, 60)}` : ''}`}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })()}

          {tab === 'clothes' && (costShownSrc || costumeVersions.length > 0) && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                <label style={{ ...label, marginBottom: 0 }}>Costume image — “{costumeName || '?'}”</label>
                {costumeVersions.length > 0 && (
                  <>
                    <button style={{ ...btnGhost, padding: '1px 9px', fontSize: 12 }} onClick={() => stepCostVersion(-1)}>‹</button>
                    <span style={{ fontSize: 12, color: '#cbd2dc', fontWeight: 600 }}>{costVerIdx + 1} / {costumeVersions.length}</span>
                    <button style={{ ...btnGhost, padding: '1px 9px', fontSize: 12 }} onClick={() => stepCostVersion(1)}>›</button>
                    {curCostVersion && curCostVersion.id === costActive ? (
                      <span style={{ fontSize: 11, color: '#5ee08a' }}>● Active</span>
                    ) : (
                      <button style={{ ...btnGreen, padding: '2px 9px', fontSize: 11 }} onClick={doSetActiveCostume}>Set active</button>
                    )}
                  </>
                )}
              </div>
              {costShownSrc && (
                <img src={costShownSrc} alt="costume preview"
                     onClick={() => { setLightboxMode('costume'); setLightboxSrc(costShownSrc); }}
                     style={{ display: 'block', width: '100%', height: 'auto', maxHeight: 'min(calc(58vh + 350px), 86vh)', objectFit: 'contain',
                              margin: '0 auto', borderRadius: 8,
                              border: `2px solid ${curCostVersion && curCostVersion.id === costActive ? '#166534' : '#2a2f3a'}`,
                              background: '#0e1116', cursor: 'zoom-in' }} />
              )}
              {costumeVersions.length > 0 && (
                <p style={{ fontSize: 12, color: '#8d97a5', margin: '4px 0 0' }}>
                  Cycling versions restores each image's outfit prompts; pose runs link to the ACTIVE costume version.
                </p>
              )}
            </div>
          )}
          {tab === 'clothes' && !busy && costumeName.trim() !== '' && (() => {
            const co = costumeName.trim();
            const groups = cloOutputs
              .filter((o) => isFinalLabel(o.label) && (o.label.startsWith('clothes/') || o.label.startsWith('emotions/')))
              .map((o) => ({ o, ims: o.images.filter((im) => (im.costume || '') === co) }))
              .filter((g) => g.ims.length > 0);
            if (!groups.length) return null;
            return (
              <div style={{ marginBottom: 12 }}>
                <label style={{ ...label, fontWeight: 600, color: '#cbd2dc' }}>Generated poses — “{co}”</label>
                {groups.map(({ o, ims }) => (
                  <div key={o.label} style={{ marginBottom: 8 }}>
                    <div style={{ fontSize: 12, color: '#a8b2c0', margin: '6px 0 4px' }}>
                      {o.label.startsWith('emotions/') ? 'Emotions' : 'Costume poses'} · {ims.length}
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(110px,1fr))', gap: 6 }}>
                      {ims.map((im, ii) => (
                        <div key={im.asset_id} style={{ position: 'relative' }}>
                          <img src={im.url} alt={o.label}
                               onClick={() => openLightboxGallery(ims.map((x) => x.url), ii)}
                               style={{ width: '100%', borderRadius: 6, border: '1px solid #2a2f3a',
                                        background: '#0e1116', cursor: 'zoom-in', display: 'block' }} />
                          <button title="Delete this image from the library"
                                  onClick={(e) => { e.stopPropagation(); doDeleteLibImage(im.asset_id, cloCharId); }}
                                  style={{ position: 'absolute', top: 4, right: 4, width: 20, height: 20,
                                           borderRadius: 10, border: '1px solid #4a2a2a', background: 'rgba(20,10,10,0.85)',
                                           color: '#ff8a8a', fontSize: 11, lineHeight: '18px', padding: 0, cursor: 'pointer' }}>
                            ✕
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            );
          })()}
          {tab === 'emotions' && (() => {
            const groups = emoOutputs
              .filter((o) => isFinalLabel(o.label) && o.label.startsWith('emotions/'))
              .flatMap((o) => {
                const byCostume = new Map<string, typeof o.images>();
                for (const im of o.images) {
                  const k = im.costume || '';
                  const arr = byCostume.get(k) || [];
                  arr.push(im);
                  byCostume.set(k, arr);
                }
                return Array.from(byCostume.entries()).map(([cost, ims]) => ({ o, cost, ims }));
              });
            if (!groups.length) return null;
            return (
              <div style={{ marginBottom: 12 }}>
                <label style={{ ...label, fontWeight: 600, color: '#cbd2dc' }}>Generated emotions for “{emoChar}”</label>
                {groups.map(({ o, cost, ims }) => (
                  <div key={`${o.label}|${cost}`} style={{ marginBottom: 8 }}>
                    <div style={{ fontSize: 12, color: '#a8b2c0', margin: '6px 0 4px' }}>
                      {cost ? `Emotions — ${cost}` : 'Emotions'} · {ims.length}
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(110px,1fr))', gap: 6 }}>
                      {ims.map((im, ii) => (
                        <div key={im.asset_id} style={{ position: 'relative' }}>
                          <img src={im.url} alt={o.label}
                               onClick={() => openLightboxGallery(ims.map((x) => x.url), ii)}
                               style={{ width: '100%', borderRadius: 6, border: '1px solid #2a2f3a',
                                        background: '#0e1116', cursor: 'zoom-in', display: 'block' }} />
                          <button title="Delete this image from the library"
                                  onClick={(e) => { e.stopPropagation(); doDeleteLibImage(im.asset_id, emoCharId); }}
                                  style={{ position: 'absolute', top: 4, right: 4, width: 20, height: 20,
                                           borderRadius: 10, border: '1px solid #4a2a2a', background: 'rgba(20,10,10,0.85)',
                                           color: '#ff8a8a', fontSize: 11, lineHeight: '18px', padding: 0, cursor: 'pointer' }}>
                            ✕
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            );
          })()}
          {(shownPreviewSrc || baseVersions.length > 0) && tab === 'create' && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                <label style={{ ...label, marginBottom: 0 }}>Base image</label>
                <button style={{ ...btnGhost, padding: '2px 10px', fontSize: 12, opacity: host?.online ? 1 : 0.5 }}
                        disabled={!host?.online}
                        title="Open the inpaint editor — brush or SAM3-segment a mask, prompt the change (with optional reference images), and save layered revisions"
                        onClick={() => {
                          const nm2 = (createSub === 'clone' ? cloneName : name).trim();
                          const src2 = curVersion?.url || shownPreviewSrc;
                          if (nm2 && src2) setEditModal({ src: src2, charName: nm2 });
                        }}>🖌 Edit image</button>
                {baseImgDims && (
                  <span style={{ fontSize: 11, color: '#7f8896', fontFamily: 'monospace' }}
                        title="Actual pixel dimensions of this base file">
                    {baseImgDims.w}×{baseImgDims.h}px
                  </span>
                )}
                {curVersion && (
                  curVersion.gen_meta && curVersion.gen_meta.upscaled ? (
                    <span title={`Upscaled render${curVersion.gen_meta.upscale_method ? ` · ${curVersion.gen_meta.upscale_method}` : ''} — the original render is kept as an earlier version`}
                          style={{ fontSize: 10, fontWeight: 700, padding: '1px 7px', borderRadius: 8,
                                   border: '1px solid #2a4a3a', background: 'rgba(10,22,16,0.9)', color: '#7ee0b0' }}>
                      ⬆ Upscaled
                    </span>
                  ) : (
                    <span title="Original render (not upscaled)"
                          style={{ fontSize: 10, fontWeight: 700, padding: '1px 7px', borderRadius: 8,
                                   border: '1px solid #34405a', background: 'rgba(14,17,22,0.9)', color: '#8ab4ff' }}>
                      Original
                    </span>
                  )
                )}
                {baseVersions.length > 0 && (
                  <>
                    <button style={{ ...btnGhost, padding: '1px 9px', fontSize: 12 }} onClick={() => stepVersion(-1)}>‹</button>
                    <span style={{ fontSize: 12, color: '#cbd2dc', fontWeight: 600 }}>{verIdx + 1} / {baseVersions.length}</span>
                    <button style={{ ...btnGhost, padding: '1px 9px', fontSize: 12 }} onClick={() => stepVersion(1)}>›</button>
                    {curVersion && curVersion.id === activeBase ? (
                      <span style={{ fontSize: 11, color: '#5ee08a' }}>● Active</span>
                    ) : (
                      <button style={{ ...btnGreen, padding: '2px 9px', fontSize: 11 }} onClick={doSetActiveBase}>Set active</button>
                    )}
                  </>
                )}
              </div>
              {variant === 'klein' && baseVersions.length > 0 && (() => {
                const counts = { single: 0, set: 0, mesh: 0 } as Record<'single' | 'set' | 'mesh', number>;
                baseVersions.forEach((v) => { counts[versionMode(v)]++; });
                const present = BASE_MODE_TABS.filter((t) => counts[t.m] > 0);
                if (present.length < 2) return null;   // only worth showing once >1 kind exists
                const cur = curVersion ? versionMode(curVersion) : null;
                return (
                  <div style={{ display: 'flex', gap: 6, margin: '2px 0 6px', flexWrap: 'wrap' }}
                       title="Switch which generated set you're viewing">
                    {present.map((t) => (
                      <button key={t.m} type="button" onClick={() => jumpToLatestMode(t.m)}
                        style={{ padding: '4px 11px', fontSize: 12, borderRadius: 6, cursor: 'pointer',
                                 border: `1px solid ${cur === t.m ? '#4a6cff' : '#333'}`,
                                 background: cur === t.m ? '#22314f' : '#141414',
                                 color: cur === t.m ? '#cfe0ff' : '#9aa4b2', fontWeight: cur === t.m ? 600 : 400 }}>
                        {t.label} <span style={{ opacity: 0.7 }}>({counts[t.m]})</span>
                      </button>
                    ))}
                  </div>
                );
              })()}
              {mainBaseSrc && (
                <img src={mainBaseSrc} alt="character base"
                     onLoad={(e) => setBaseImgDims({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })}
                     onClick={() => { setLightboxMode('base'); setLightboxSrc(mainBaseSrc); }}
                     style={{ display: 'block', width: '100%', height: 'auto', maxHeight: 'min(calc(58vh + 350px), 86vh)', objectFit: 'contain',
                              margin: '0 auto', borderRadius: 8,
                              border: `2px solid ${curVersion && curVersion.id === activeBase ? '#166534' : '#2a2f3a'}`,
                              background: '#0e1116', cursor: 'zoom-in' }} />
              )}
              {curVersion?.gen_meta && Object.keys(curVersion.gen_meta).length > 0 && (
                <details style={{ marginTop: 6, fontSize: 12, color: '#a8b2c0' }}>
                  <summary style={{ cursor: 'pointer', color: '#8ab4ff' }}>⚙ Settings that made this image (v{verIdx + 1})</summary>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 4 }}>
                    {Object.entries(curVersion.gen_meta).map(([k, v]) => (
                      <span key={k} style={{ padding: '2px 6px', borderRadius: 4, background: '#12161d', border: '1px solid #2a2f3a' }}>
                        <b style={{ color: '#cbd2dc' }}>{k.replace(/_/g, ' ')}</b>: {String(v)}
                      </span>
                    ))}
                  </div>
                  {variant === 'klein' && (
                    <button style={{ ...pbtn, marginTop: 6, fontSize: 11, padding: '4px 9px' }}
                            title="Load these settings back into the live dials so you can reproduce or iterate on this image"
                            onClick={() => applyGenMetaToDials(curVersion?.gen_meta)}>
                      ⤵ Use these settings
                    </button>
                  )}
                </details>
              )}
              {curViews.length > 1 && (
                <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                  {curViews.map((vw, i) => (
                    <button key={`${vw.view}${i}`} type="button" onClick={() => setBaseViewIdx(i)}
                      style={{ flex: 1, padding: 0, borderRadius: 6, cursor: 'pointer', overflow: 'hidden',
                               border: `2px solid ${i === baseViewSel ? '#4a6cff' : '#2a2f3a'}`, background: '#0e1116' }}>
                      <img src={vw.url} alt={vw.view}
                           style={{ display: 'block', width: '100%', height: 64, objectFit: 'contain', background: '#0e1116' }} />
                      <div style={{ fontSize: 10, textAlign: 'center', padding: '2px 0',
                                    color: i === baseViewSel ? '#cfe0ff' : '#9aa4b2', textTransform: 'capitalize' }}>{vw.view}</div>
                    </button>
                  ))}
                </div>
              )}
              {baseVersions.length > 0 && (
                <p style={{ fontSize: 12, color: '#8d97a5', margin: '4px 0 0' }}>
                  Pose runs link to the ACTIVE base version — tweak the base, set it active, rerun Generate Poses.
                </p>
              )}
              {baseVersions.length > 0 && host?.online && (
                <div style={{ marginTop: 8 }}>
                  <label style={toggleBox}>
                    <input type="checkbox" checked={baseEnhanceOn} onChange={(e) => setBaseEnhanceOn(e.target.checked)} />
                    ✨ Enhance base (AI upscale + sharpen) — a sharper base gives sharper pose faces
                  </label>
                  {baseEnhanceOn && (
                    <div style={{ ...wizBox, display: 'grid', gap: 8, marginTop: 6 }}>
                      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                        <div><label style={label}>Method</label>
                          <select style={input} value={baseEnhanceMethod} onChange={(e) => setBaseEnhanceMethod(e.target.value as 'gan' | 'seedvr2')}>
                            <option value="gan">GAN upscale (fast)</option>
                            <option value="seedvr2">SeedVR2 (higher quality, slower)</option>
                          </select></div>
                        {baseEnhanceMethod === 'gan' && (
                          <div><label style={label}>Upscale model</label>
                            <select style={input} value={baseEnhanceModel} onChange={(e) => setBaseEnhanceModel(e.target.value)}>
                              <option value="">Auto (best 4×)</option>
                              {upscaleModels.map((m) => <option key={m} value={m}>{m}</option>)}
                            </select></div>
                        )}
                        <div><label style={label}>Sharpen</label>
                          <select style={input} value={baseEnhanceSharpen} onChange={(e) => setBaseEnhanceSharpen(e.target.value)}>
                            <option value="off">Off</option>
                            <option value="light">Light</option>
                            <option value="medium">Medium</option>
                            <option value="strong">Strong</option>
                          </select></div>
                        <div><label style={label}>Max size (px)</label>
                          <select style={input} value={String(baseEnhanceMaxSide)} onChange={(e) => setBaseEnhanceMaxSide(parseInt(e.target.value, 10) || 2048)}>
                            <option value="1536">1536</option>
                            <option value="2048">2048</option>
                            <option value="3072">3072</option>
                            <option value="4096">4096</option>
                          </select></div>
                      </div>
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                        <button style={{ ...btn, opacity: (baseEnhanceBusy || !host?.online) ? 0.5 : 1, cursor: (baseEnhanceBusy || !host?.online) ? 'not-allowed' : 'pointer' }}
                                disabled={baseEnhanceBusy || !host?.online} onClick={doBaseEnhance}>
                          {baseEnhanceBusy ? 'Upscaling base…' : `✨ Enhance base${curViews.length > 1 ? ` (${curViews.length} views)` : ''}`}
                        </button>
                        {baseEnhanceBusy && (
                          <span style={{ fontSize: 12, color: '#a8b2c0', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                            <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#3b82f6', display: 'inline-block', animation: 'rbmnPulse 1s ease-in-out infinite' }} />
                            <style>{'@keyframes rbmnPulse { 0%,100% { opacity: 1; } 50% { opacity: 0.25; } }'}</style>
                            Running{baseEnhanceWorker ? ` on ${shortHost(baseEnhanceWorker)}` : ''} · {Math.floor(baseEnhanceElapsed / 60)}:{String(baseEnhanceElapsed % 60).padStart(2, '0')} elapsed
                          </span>
                        )}
                        {!baseEnhanceBusy && baseEnhanceMsg && <span style={{ fontSize: 11, color: baseEnhanceMsg.startsWith('✓') ? '#5ee08a' : '#9aa4b2' }}>{baseEnhanceMsg}</span>}
                      </div>
                      <p style={{ fontSize: 12, color: '#8d97a5', margin: 0 }}>
                        Runs on your worker and saves the result as a NEW base version that becomes ACTIVE — so lock-base pose runs use the sharper base. Use the ‹ › arrows above to compare with the original version, and “Set active” to switch back. Your last-used enhance options are remembered.
                      </p>
                    </div>
                  )}
                  <div style={{ marginTop: 10, border: '1px dashed #34518a', borderRadius: 8, padding: 9, display: 'grid', gap: 7 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                      <b style={{ fontSize: 12.5, color: '#cfe0ff' }}>🧊 3D body</b>
                      {m3dStatus?.mesh3d ? (
                        <span style={{ fontSize: 11, color: m3dStatus.mesh3d.rigged ? '#5ee08a' : '#ffce6b' }}
                              title={`template ${m3dStatus.mesh3d.template} · ${m3dStatus.mesh3d.created?.slice(0, 16).replace('T', ' ')}${m3dStatus.mesh3d.rig_error ? `\nRig error: ${m3dStatus.mesh3d.rig_error}` : ''}`}>
                          ● {m3dStatus.mesh3d.rigged ? 'rigged mesh ready' : 'mesh ready (not rigged)'}
                        </span>
                      ) : (
                        <span style={{ fontSize: 11, color: '#8a94a6' }}>none yet</span>
                      )}
                      {m3dBusy && m3dStatus?.run && (
                        <span style={{ fontSize: 11, color: '#8ab4ff' }}>
                          ⏳ {m3dStatus.run.phase === 'rig' ? 'rigging…' : 'generating mesh (Hunyuan3D)…'}
                          {m3dStatus.run.detail ? ` ${m3dStatus.run.detail}` : ''}
                        </span>
                      )}
                      {m3dStatus?.run?.status === 'error' && (
                        <span style={{ fontSize: 11, color: '#ff8a8a' }} title={m3dStatus.run.error || ''}>
                          ⚠ {String(m3dStatus.run.error || 'failed').slice(0, 90)}
                        </span>
                      )}
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                      {segRow([{ v: 'mixamo', label: '🧍 Humanoid (mixamo)' },
                               { v: 'articulationxl', label: '🐾 Creature (articulationxl)' }],
                              m3dTemplate, (v) => setM3dTemplate(v === 'articulationxl' ? 'articulationxl' : 'mixamo'))}
                      <button style={{ ...btn, padding: '6px 12px', fontSize: 12, opacity: (m3dBusy || !host?.online) ? 0.5 : 1 }}
                              disabled={m3dBusy || !host?.online} onClick={() => { void startM3d(false); }}>
                        {m3dBusy ? 'Working…' : m3dStatus?.mesh3d ? '🧊 Regenerate 3D body' : '🧊 Generate 3D body'}
                      </button>
                      <button style={{ ...btnGhost, padding: '6px 12px', fontSize: 12, borderColor: '#2f7d4f', color: '#8fe0a0', opacity: (promoteBusy || !host?.online) ? 0.5 : 1 }}
                              disabled={promoteBusy || !host?.online}
                              onClick={() => { void promoteTurnaroundBase(); }}
                              title="Take the 🧊 Mesh-turnaround pose set (front/right/left/back) you just generated and save it as the ACTIVE base — the front becomes the base image. 'Generate 3D body' reads the active base, so this feeds it the perfect turnaround views directly.">
                        {promoteBusy ? 'Promoting…' : '⬆ Use Mesh-turnaround as base'}
                      </button>
                      {m3dStatus?.mesh3d && !m3dStatus.mesh3d.rigged && !m3dBusy && (
                        <button style={{ ...btnGhost, padding: '6px 12px', fontSize: 12, opacity: !host?.online ? 0.5 : 1 }}
                                disabled={!host?.online} onClick={() => { void startM3d(true); }}
                                title="Retry only the auto-rig on the saved mesh (skips the ~2min Hunyuan3D step). Uses worker UniRig when available, otherwise rigs locally with Make-It-Animatable (first run downloads ~2.2GB and sets up its environment).">
                          🦴 Rig existing mesh
                        </button>
                      )}
                      {m3dStatus?.mesh3d && (
                        <button style={{ ...btnGhost, padding: '5px 10px', fontSize: 11, borderColor: '#34518a', color: '#8ab4ff' }}
                                onClick={() => open3dViewer(m3dCharName())}
                                title="Open the 3D mesh in an orbit viewer — drag to rotate, scroll to zoom, so you can check it before trusting it">🧊 View 3D</button>
                      )}
                      {m3dStatus?.mesh3d && (
                        <a style={{ ...btnGhost, padding: '5px 10px', fontSize: 11, textDecoration: 'none' }}
                           href={api.mesh3dFileUrl(m3dCharName(), 'glb')} download>⬇ GLB</a>
                      )}
                      {m3dStatus?.mesh3d?.rigged && (
                        <a style={{ ...btnGhost, padding: '5px 10px', fontSize: 11, textDecoration: 'none' }}
                           href={api.mesh3dFileUrl(m3dCharName(), 'fbx')} download>⬇ FBX</a>
                      )}
                    </div>
                    {promoteMsg && (
                      <p style={{ fontSize: 11, margin: 0,
                                  color: promoteMsg.startsWith('✓') ? '#5ee08a'
                                       : (promoteMsg.startsWith('⚠') || promoteMsg.startsWith('Promote failed')) ? '#ffce6b'
                                       : '#8ab4ff' }}>
                        {promoteMsg}
                      </p>
                    )}
                    <p style={{ fontSize: 11, color: '#8d97a5', margin: 0 }}>
                      Builds a character-shaped 3D mannequin from the ACTIVE base render (all 4 views when the base
                      is a view set): Hunyuan3D shape on the worker → auto-rig, saved with the character (once per
                      character). For the best shape, make the active base a <b>🧊 Mesh-ready</b> set on the Create
                      tab (locked A-pose, arms clear, 4 views) — a single front view loses detail. Rigging uses the worker’s UniRig nodes when present, otherwise runs LOCALLY via
                      Make-It-Animatable (Mixamo skeleton; CPU-capable, ~1–3 min; first run sets up its environment
                      and downloads ~2.2GB of models — watch the status line). Only the Hunyuan3D checkpoint is
                      needed on the worker. If rigging fails the mesh is still saved; “🦴 Rig existing mesh” retries
                      just the rig.
                    </p>
                  </div>
                  <label style={{ ...toggleBox, marginTop: 10 }}>
                    <input type="checkbox" checked={switchStyleOn} onChange={(e) => setSwitchStyleOn(e.target.checked)} />
                    🎨 Switch Style — restyle the base into a new art style (e.g. photoreal ↔ anime)
                  </label>
                  {switchStyleOn && (
                    <div style={{ ...wizBox, display: 'grid', gap: 8, marginTop: 6 }}>
                      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                        <div><label style={label}>Target style</label>
                          <select style={input} value={switchStyle} onChange={(e) => setSwitchStyle(e.target.value)}>
                            <option value="photorealistic">Photorealistic</option>
                            <option value="semi-realistic">Semi-realistic</option>
                            <option value="anime">Anime</option>
                            <option value="manga">Manga (black &amp; white)</option>
                            <option value="comic">Western comic</option>
                            <option value="cartoon">Cartoon</option>
                            <option value="3d">3D render</option>
                            <option value="painting">Digital painting</option>
                            <option value="custom">Custom…</option>
                          </select></div>
                        <div><label style={label}>Restyle amount</label>
                          <select style={input} value={switchStyleStrength} onChange={(e) => setSwitchStyleStrength(e.target.value)}>
                            <option value="subtle">Subtle (keep most detail)</option>
                            <option value="balanced">Balanced</option>
                            <option value="strong">Strong (full redraw)</option>
                          </select></div>
                      </div>
                      {['photorealistic', 'semi-realistic'].includes(switchStyle) && (
                        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: '#cbd2dc' }}>
                          <input type="checkbox" checked={switchStyleRealism}
                                 onChange={(e) => setSwitchStyleRealism(e.target.checked)} />
                          Use realism LoRA (anime2real-semi) — stronger photoreal conversion (needs the LoRA on the worker)
                        </label>
                      )}
                      {switchStyle === 'custom' && (
                        <input style={input} value={switchStyleCustom} onChange={(e) => setSwitchStyleCustom(e.target.value)}
                               placeholder="Describe the target style, e.g. gritty film-noir black & white" />
                      )}
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                        <label style={{ fontSize: 12, color: '#9aa4b2' }}>Style reference image (optional):</label>
                        <input type="file" accept="image/*" disabled={!host?.online}
                               onChange={(e) => onUploadStyleRef(e.target.files)} style={{ fontSize: 11, color: '#cbd2dc' }} />
                        {switchStyleRef && (
                          <span style={{ fontSize: 11, color: '#5ee08a', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                            ✓ {switchStyleRef.name.slice(0, 24)}
                            <button style={{ ...btnGhost, padding: '0 6px', fontSize: 10 }} onClick={() => setSwitchStyleRef(null)}>✕</button>
                          </span>
                        )}
                      </div>
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                        <button style={{ ...btn, opacity: (switchStyleBusy || !host?.online) ? 0.5 : 1, cursor: (switchStyleBusy || !host?.online) ? 'not-allowed' : 'pointer' }}
                                disabled={switchStyleBusy || !host?.online} onClick={doRestyleBase}>
                          {switchStyleBusy ? 'Restyling base…' : `🎨 Generate New Style${curViews.length > 1 ? ` (${curViews.length} views)` : ''}`}
                        </button>
                        {switchStyleBusy && (
                          <span style={{ fontSize: 12, color: '#a8b2c0', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                            <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#a855f7', display: 'inline-block', animation: 'rbmnPulse 1s ease-in-out infinite' }} />
                            <style>{'@keyframes rbmnPulse { 0%,100% { opacity: 1; } 50% { opacity: 0.25; } }'}</style>
                            Running{switchStyleWorker ? ` on ${shortHost(switchStyleWorker)}` : ''} · {Math.floor(switchStyleElapsed / 60)}:{String(switchStyleElapsed % 60).padStart(2, '0')} elapsed
                          </span>
                        )}
                        {!switchStyleBusy && switchStyleMsg && <span style={{ fontSize: 11, color: switchStyleMsg.startsWith('✓') ? '#5ee08a' : '#9aa4b2' }}>{switchStyleMsg}</span>}
                      </div>
                      <p style={{ fontSize: 12, color: '#8d97a5', margin: 0 }}>
                        Redraws the current base into the target style (or the style of your reference image) and saves it as a NEW active base version — so lock-base pose runs come out in this style. Keeps the same character, pose, framing and undressed state; only the art style changes. Use the ‹ › arrows above to compare / revert. Chains with Enhance base (restyle, then upscale, or vice-versa). Your options are remembered.
                      </p>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
          {phase === 'idle' && !chunks.length && !previewImg && !existingOutputs.length && (
            <p style={{ color: '#6b7280', fontSize: 13 }}>Previews and generated images appear here; full runs are saved to your library.</p>
          )}
          {tab === 'create' && existingOutputs.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <label style={{ ...label, fontWeight: 600, color: '#cbd2dc', marginBottom: 0 }}>Base poses for “{name}”</label>
                {busy && (
                  <span style={{ fontSize: 11, color: '#5ee08a', display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                    <span style={{ width: 7, height: 7, borderRadius: '50%', background: '#5ee08a', display: 'inline-block', animation: 'rbmnPulse 1s ease-in-out infinite' }} />
                    <style>{'@keyframes rbmnPulse { 0%,100% { opacity: 1; } 50% { opacity: 0.25; } }'}</style>
                    filing live — each pose appears as it finishes
                  </span>
                )}
                {baseVersions.length > 0 && (
                  <label style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, color: '#a8b2c0' }}>
                    <input type="checkbox" checked={showAllVersions} onChange={(e) => setShowAllVersions(e.target.checked)} />
                    all versions (off = poses linked to base v{verIdx + 1})
                  </label>
                )}
                <label style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, color: '#a8b2c0' }}>
                  <input type="checkbox" checked={showAllLibOutputs} onChange={(e) => setShowAllLibOutputs(e.target.checked)} />
                  show pipeline intermediates (faces / pre-BG / undressed base)
                </label>
                {(() => { const n = collectBasePoseOriginalIds().length; return n > 0 ? (
                  <button title={`AI-upscale all ${n} pose${n === 1 ? '' : 's'} in this set (${baseEnhanceMethod.toUpperCase()}, ${baseEnhanceMaxSide}px). Keeps originals; each upscales from its original so re-runs don't stack. Uses the same method/model/size as Enhance base.`}
                          disabled={poseUpBusy.size > 0 || !host?.online}
                          onClick={() => upscalePoses(collectBasePoseOriginalIds())}
                          style={{ fontSize: 11, padding: '3px 9px', borderRadius: 7,
                                   border: '1px solid #2a4a3a', background: 'rgba(10,22,16,0.85)',
                                   color: '#7ee0b0', cursor: (poseUpBusy.size > 0 || !host?.online) ? 'default' : 'pointer',
                                   opacity: (poseUpBusy.size > 0 || !host?.online) ? 0.5 : 1 }}>
                    {poseUpBusy.size > 0 ? 'Upscaling…' : `⬆ Upscale all poses (${n})`}
                  </button>
                ) : null; })()}
                {poseUpMsg && <span style={{ fontSize: 11, color: poseUpMsg.startsWith('✓') ? '#5ee08a' : '#9aa4b2' }}>{poseUpMsg}</span>}
              </div>
              <div style={{ display: 'flex', gap: 6, margin: '6px 0 8px' }}>
                {(['upscaled', 'original'] as const).map((v) => (
                  <button key={v} onClick={() => setLibView(v)}
                          style={{ padding: '5px 14px', fontSize: 12, borderRadius: 6, cursor: 'pointer',
                                   border: `1px solid ${libView === v ? '#3b82f6' : '#2a2f3a'}`,
                                   background: libView === v ? '#16233a' : '#12161d',
                                   color: libView === v ? '#dbe9ff' : '#9aa4b2', fontWeight: libView === v ? 700 : 400 }}>
                    {v === 'upscaled' ? 'Upscaled (default refs)' : 'Originals'}
                  </button>
                ))}
                <span style={{ fontSize: 12, color: '#8d97a5', alignSelf: 'center' }}>
                  {libView === 'upscaled'
                    ? 'HD copies where they exist, originals elsewhere — these are the default references for this character'
                    : 'raw renders only'}
                </span>
              </div>
              {(showAllLibOutputs ? existingOutputs
                : existingOutputs.filter((o) => isFinalLabel(o.label)
                    && !o.label.startsWith('clothes/') && !o.label.startsWith('emotions/')
                    && !o.label.endsWith('naked_sprites')))
                .flatMap((o) => {
                  // split clothes/emotions finals by costume so each outfit reads as its own row
                  const byCostume = new Map<string, typeof o.images>();
                  for (const im of o.images) {
                    const k = (o.label.startsWith('clothes/') || o.label.startsWith('emotions/')) ? (im.costume || '') : '';
                    const arr = byCostume.get(k) || [];
                    arr.push(im);
                    byCostume.set(k, arr);
                  }
                  return Array.from(byCostume.entries()).map(([cost, ims]) => ({ o, cost, ims }));
                })
                .map(({ o, cost, ims }) => {
                const vimgs = (showAllVersions || !baseVersions.length || !curVersion)
                  ? ims
                  : ims.filter((im) => !im.base_version || im.base_version === curVersion.id);
                // Originals/Upscaled view (v1.157): 'upscaled' shows HD copies plus any
                // original that has no HD copy yet (a complete set, upscales preferred);
                // 'original' shows only the raw renders.
                const upSrcs = new Set(vimgs.filter((x) => x.upscaled && x.upscale_source).map((x) => String(x.upscale_source)));
                const imgs = libView === 'original'
                  ? vimgs.filter((x) => !x.upscaled)
                  : vimgs.filter((x) => x.upscaled || !upSrcs.has(String(x.asset_id)));
                if (!imgs.length) return null;
                return (
                  <div key={`${o.label}|${cost}`} style={{ marginBottom: 8 }}>
                    <div style={{ fontSize: 12, color: '#a8b2c0', margin: '6px 0 4px' }}>
                      {showAllLibOutputs ? `${o.label}${cost ? ` — ${cost}` : ''}` : friendlyLabel(o.label, cost)} · {imgs.length}
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(110px,1fr))', gap: 6 }}>
                      {imgs.map((im, ii) => (
                        <div key={im.asset_id} style={{ position: 'relative' }}>
                          <img src={im.url} alt={o.label}
                               onClick={() => openLightboxGallery(imgs.map((x) => x.url), ii)}
                               style={{ width: '100%', borderRadius: 6, border: '1px solid #2a2f3a',
                                        background: '#0e1116', cursor: 'zoom-in', display: 'block' }} />
                          <button title="Delete this image from the library"
                                  onClick={(e) => { e.stopPropagation(); doDeleteLibImage(im.asset_id); }}
                                  style={{ position: 'absolute', top: 4, right: 4, width: 20, height: 20,
                                           borderRadius: 10, border: '1px solid #4a2a2a', background: 'rgba(20,10,10,0.85)',
                                           color: '#ff8a8a', fontSize: 11, lineHeight: '18px', padding: 0, cursor: 'pointer' }}>
                            ✕
                          </button>
                          <button title="Use this image as the character's thumbnail"
                                  onClick={async (e) => {
                                    e.stopPropagation();
                                    if (!editingCharId) return;
                                    try {
                                      await api.setHeroImage(editingCharId, im.asset_id);
                                      setSaveMsg('✓ Thumbnail updated — shown on the Character Studio main screen.');
                                    } catch (err) { setErrMsg(`Thumbnail failed: ${(err as Error).message}`); }
                                  }}
                                  style={{ position: 'absolute', top: 4, right: 28, width: 20, height: 20,
                                           borderRadius: 10, border: '1px solid #4a4a2a', background: 'rgba(20,20,10,0.85)',
                                           color: '#ffd76a', fontSize: 11, lineHeight: '18px', padding: 0, cursor: 'pointer' }}>
                            ★
                          </button>
                          {im.pose_name && !im.upscaled && (
                            <button title={regenPose === im.pose_name
                                     ? `Re-rolling “${im.pose_name}” on a fresh seed…`
                                     : `Regenerate just this pose (“${im.pose_name}”) on a fresh seed, same settings as the rest of the set`}
                                    disabled={busy || !!regenPose}
                                    onClick={(e) => { e.stopPropagation(); regeneratePose(im.pose_name as string); }}
                                    style={{ position: 'absolute', top: 4, left: 4, width: 20, height: 20,
                                             borderRadius: 10, border: '1px solid #2a3a4a',
                                             background: regenPose === im.pose_name ? 'rgba(94,224,138,0.9)' : 'rgba(10,16,22,0.85)',
                                             color: regenPose === im.pose_name ? '#0e1116' : '#7ec8ff',
                                             fontSize: 12, lineHeight: '18px', padding: 0,
                                             cursor: (busy || regenPose) ? 'default' : 'pointer',
                                             opacity: (busy || regenPose) && regenPose !== im.pose_name ? 0.4 : 1,
                                             animation: regenPose === im.pose_name ? 'rbmnSpin 0.9s linear infinite' : 'none' }}>
                              <style>{'@keyframes rbmnSpin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }'}</style>
                              ↻
                            </button>
                          )}
                          {im.pose_name && !im.upscaled && (
                            <button title={poseUpBusy.has(im.asset_id)
                                     ? `Upscaling “${im.pose_name}”…`
                                     : `Upscale this pose (${baseEnhanceMethod.toUpperCase()}, ${baseEnhanceMaxSide}px) — saves a sharper copy from the original, keeps this one`}
                                    disabled={poseUpBusy.has(im.asset_id) || !host?.online}
                                    onClick={(e) => { e.stopPropagation(); upscalePoses([im.asset_id]); }}
                                    style={{ position: 'absolute', bottom: 4, left: 4, width: 20, height: 20,
                                             borderRadius: 10, border: '1px solid #2a4a3a',
                                             background: poseUpBusy.has(im.asset_id) ? 'rgba(94,224,138,0.9)' : 'rgba(10,22,16,0.85)',
                                             color: poseUpBusy.has(im.asset_id) ? '#0e1116' : '#7ee0b0',
                                             fontSize: 12, lineHeight: '18px', padding: 0,
                                             cursor: (poseUpBusy.has(im.asset_id) || !host?.online) ? 'default' : 'pointer',
                                             animation: poseUpBusy.has(im.asset_id) ? 'rbmnSpin 0.9s linear infinite' : 'none' }}>
                              <style>{'@keyframes rbmnSpin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }'}</style>
                              ⬆
                            </button>
                          )}
                          {im.upscaled && (
                            <span title="Upscaled copy — the original is preserved in this set"
                                  style={{ position: 'absolute', bottom: 4, left: 4, padding: '1px 5px',
                                           borderRadius: 8, border: '1px solid #2a4a3a',
                                           background: 'rgba(10,22,16,0.9)', color: '#7ee0b0',
                                           fontSize: 9, fontWeight: 700, lineHeight: '14px' }}>HD</span>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
              {!showAllLibOutputs && !existingOutputs.some((o) => isFinalLabel(o.label)
                  && !o.label.startsWith('clothes/') && !o.label.startsWith('emotions/')) && (
                <p style={{ color: '#6b7280', fontSize: 12 }}>
                  No base sprites cataloged yet — costume/emotion poses show on their own tabs;
                  tick “show pipeline intermediates” to see everything ingested.
                </p>
              )}
            </div>
          )}
          {chunks.some((c) => c.images.length > 0) && (
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: '#a8b2c0', marginBottom: 6 }}>
              <input type="checkbox" checked={showAllOutputs} onChange={(e) => setShowAllOutputs(e.target.checked)} />
              Show all pipeline outputs (sheet / faces / pre-BG intermediates) — off shows final sprites only, like the node UI
            </label>
          )}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(120px,1fr))', gap: 8 }}>
            {(() => {
              const urls = chunks.flatMap((c) => {
                const finals = showAllOutputs ? null : finalNodeIds(c.tap_map);
                return c.images
                  .filter((img) => !finals || finals.has(String((img as { node_id?: string | number }).node_id ?? '')))
                  .map((img) => api.viewUrl(img, c.host));
              });
              return urls.map((u, i) => (
                <img key={`${u}-${i}`} src={u} alt={`result ${i + 1}`}
                     onClick={() => openLightboxGallery(urls, i)}
                     style={{ width: '100%', borderRadius: 6, border: '1px solid #2a2f3a',
                              background: '#0e1116', cursor: 'zoom-in' }} />
              ));
            })()}
          </div>
        </div>
      </div>
      )}

      {visionView && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.65)', zIndex: 60,
                      display: 'flex', alignItems: 'center', justifyContent: 'center' }}
             onClick={() => setVisionView(null)}>
          <div style={{ ...box, width: 520, maxHeight: '80vh', overflowY: 'auto' }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 8 }}>
              <h3 style={{ margin: 0, fontSize: 15 }}>🔍 Vision Scan Data</h3>
              <div style={{ flex: 1 }} />
              <button style={btnGhost} onClick={() => setVisionView(null)}>Close</button>
            </div>
            <p style={{ fontSize: 12, color: '#8d97a5', margin: '0 0 8px' }}>
              What the vision model saw in this reference — used to synthesize the character fields.
            </p>
            <div style={{ fontSize: 13, color: '#cbd2dc', lineHeight: 1.5, whiteSpace: 'pre-wrap',
                          background: '#0e1116', border: '1px solid #2a2f3a', borderRadius: 8, padding: 10 }}>
              {visionView.text || '(no scan data)'}
            </div>
          </div>
        </div>
      )}

      {editModal && (
        <EditImageModal charName={editModal.charName} src={editModal.src}
                        costumeName={editModal.costume}
                        onSaved={refreshCharPanels}
                        onClose={() => setEditModal(null)} />
      )}

      {view3dChar && (
        <div onClick={() => setView3dChar('')}
             style={{ position: 'fixed', inset: 0, zIndex: 3100, background: 'rgba(4,6,10,0.82)',
                      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '3vh 16px' }}>
          <div onClick={(e) => e.stopPropagation()}
               style={{ width: 'min(960px, 95vw)', background: '#0f131b', border: '1px solid #2a3550',
                        borderRadius: 12, padding: '14px 16px', boxShadow: '0 20px 60px rgba(0,0,0,0.55)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
              <h3 style={{ margin: 0, fontSize: 16, color: '#e6edf6', flex: 1 }}>🧊 3D preview — {view3dChar}</h3>
              <a style={{ ...btnGhost, padding: '4px 10px', fontSize: 11, textDecoration: 'none' }}
                 href={api.mesh3dFileUrl(view3dChar, 'glb')} download>⬇ GLB</a>
              <button style={{ ...pbtn, fontSize: 13 }} onClick={() => setView3dChar('')}>✕ Close</button>
            </div>
            {view3dReady ? (
              <div style={{ height: '70vh', background: '#0e1116', borderRadius: 8, overflow: 'hidden' }}
                   dangerouslySetInnerHTML={{ __html:
                     `<model-viewer src="${api.mesh3dFileUrl(view3dChar, 'glb')}" camera-controls auto-rotate `
                     + `rotation-per-second="20deg" interaction-prompt="none" shadow-intensity="0.3" exposure="1.15" `
                     + `environment-image="neutral" style="width:100%;height:100%;background:#0e1116;"></model-viewer>` }} />
            ) : (
              <div style={{ height: '70vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
                            color: '#8d97a5', fontSize: 13, textAlign: 'center', padding: 20 }}>
                Loading the 3D viewer… (it fetches the viewer once — needs internet the first time).<br />
                If it doesn’t appear, use ⬇ GLB and open it in any 3D viewer.
              </div>
            )}
            <p style={{ fontSize: 11.5, color: '#8d97a5', margin: '8px 0 0' }}>
              Drag to rotate · scroll to zoom · right-drag to pan. This is the untextured mesh shape — check the
              silhouette, limbs and proportions for weirdness before you trust it.
            </p>
          </div>
        </div>
      )}
      {helpTopic && HELP_TOPICS[helpTopic] && (() => {
        const h = HELP_TOPICS[helpTopic];
        const sec = (_t: string) => ({ fontSize: 13, fontWeight: 700 as const, color: '#8ab4ff', margin: '16px 0 6px' });
        return (
          <div onClick={() => setHelpTopic('')}
               style={{ position: 'fixed', inset: 0, zIndex: 3000, background: 'rgba(4,6,10,0.72)',
                        display: 'flex', alignItems: 'flex-start', justifyContent: 'center', padding: '5vh 16px', overflowY: 'auto' }}>
            <div onClick={(e) => e.stopPropagation()}
                 style={{ maxWidth: 620, width: '100%', background: '#0f131b', border: '1px solid #2a3550',
                          borderRadius: 12, padding: '18px 22px', boxShadow: '0 20px 60px rgba(0,0,0,0.5)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <h2 style={{ margin: 0, fontSize: 18, color: '#e6edf6', flex: 1 }}>{h.title}</h2>
                <button onClick={() => setHelpTopic('')}
                        style={{ ...pbtn, fontSize: 13 }}>✕ Close</button>
              </div>
              <p style={{ fontSize: 13.5, color: '#c4ccd8', lineHeight: 1.55, margin: '10px 0 0' }}>{h.intro}</p>

              <div style={sec('s')}>🎛 Settings that matter</div>
              <div style={{ display: 'grid', gap: 7 }}>
                {h.settings.map((s, i) => (
                  <div key={i} style={{ fontSize: 12.5, color: '#b8c2d0', lineHeight: 1.5 }}>
                    <b style={{ color: '#dbe7f5' }}>{s.name}</b> — {s.what}
                  </div>
                ))}
              </div>

              <div style={{ ...sec('g'), color: '#ffcf8a' }}>⚠ Watch out (gotchas)</div>
              <ul style={{ margin: 0, paddingLeft: 20, display: 'grid', gap: 6 }}>
                {h.gotchas.map((g, i) => (
                  <li key={i} style={{ fontSize: 12.5, color: '#e4c9a8', lineHeight: 1.5 }}>{g}</li>
                ))}
              </ul>

              <div style={{ ...sec('b'), color: '#7ee0b0' }}>✅ Best-practice starting point</div>
              <ul style={{ margin: 0, paddingLeft: 20, display: 'grid', gap: 6 }}>
                {h.best.map((b, i) => (
                  <li key={i} style={{ fontSize: 12.5, color: '#b6e6cd', lineHeight: 1.5 }}>{b}</li>
                ))}
              </ul>

              <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 18 }}>
                <button onClick={() => setHelpTopic('')} style={{ ...btn, padding: '7px 16px' }}>Got it</button>
              </div>
            </div>
          </div>
        );
      })()}
      {lightboxSrc && (
        <ImageLightbox
          src={lightboxMode === 'base' && mainBaseSrc ? mainBaseSrc
            : lightboxMode === 'costume' && costShownSrc ? costShownSrc
            : lightboxSrc}
          onClose={() => { setLightboxSrc(''); setLightboxMode(''); setLightboxList([]); lightboxNavRef.current = null; }}
          version={lightboxMode === 'base' ? versionCtl
            : lightboxMode === 'costume' ? costumeVersionCtl : undefined}
          nav={lightboxMode === '' && lightboxList.length > 1 ? {
            index: lightboxIdx, count: lightboxList.length,
            onPrev: () => stepLightbox(-1), onNext: () => stepLightbox(1),
          } : undefined}
        />
      )}

      {importOpen && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.65)', zIndex: 60,
                      display: 'flex', alignItems: 'center', justifyContent: 'center' }}
             onClick={() => setImportOpen(false)}>
          <div style={{ ...box, width: 560, maxHeight: '80vh', overflowY: 'auto' }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 10 }}>
              <h3 style={{ margin: 0, fontSize: 15 }}>Import an outfit from another character</h3>
              <div style={{ flex: 1 }} />
              <button style={btnGhost} onClick={() => setImportOpen(false)}>Close</button>
            </div>
            <p style={{ fontSize: 12, color: '#9aa4b2', marginTop: 0 }}>
              Copies the outfit's prompt set into the slots so you can replicate it on “{clothesChar || '…'}”.
            </p>
            <label style={label}>Character</label>
            <select style={input} value={importChar} onChange={(e) => pickImportChar(e.target.value)}>
              <option value="">(select character)</option>
              {catItems.filter((c) => c.name !== clothesChar).map((c) => <option key={c.character_id} value={c.name}>{c.name}</option>)}
            </select>
            {importLoading && <p style={{ fontSize: 12, color: '#9aa4b2' }}>Loading outfits…</p>}
            {!!importChar && !importLoading && !Object.keys(importCostumes).length && (
              <p style={{ fontSize: 12, color: '#6b7280' }}>No outfits with saved prompts on “{importChar}”.</p>
            )}
            {Object.keys(importCostumes).length > 0 && (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(120px,1fr))', gap: 8, marginTop: 10 }}>
                {Object.entries(importCostumes).map(([nm, entry]) => {
                  const vlist = entry?.versions || [];
                  const act = vlist.find((v) => v.id === entry?.active) || vlist[vlist.length - 1];
                  return (
                    <div key={nm} onClick={() => doImportCostume(nm)}
                         style={{ cursor: 'pointer', border: '1px solid #2a2f3a', borderRadius: 8,
                                  background: '#0e1116', padding: 6, textAlign: 'center' }}>
                      {act ? <img src={act.url} alt={nm} style={{ width: '100%', height: 110, objectFit: 'contain', borderRadius: 6 }} />
                           : <div style={{ height: 110, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#6b7280', fontSize: 11 }}>no preview</div>}
                      <div style={{ fontSize: 12, color: '#cbd2dc', marginTop: 4 }}>{nm}</div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}

      {libOpen && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.65)', zIndex: 60,
                      display: 'flex', alignItems: 'center', justifyContent: 'center' }}
             onClick={() => setLibOpen(false)}>
          <div style={{ ...box, width: 640, maxHeight: '80vh', overflowY: 'auto' }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 10 }}>
              <h3 style={{ margin: 0, fontSize: 15 }}>Pose Library (host)</h3>
              <div style={{ flex: 1 }} />
              <button style={btnGhost} onClick={() => setLibOpen(false)}>Close</button>
            </div>
            {poseLibraryPanel}
          </div>
        </div>
      )}
    </div>
  );
}
