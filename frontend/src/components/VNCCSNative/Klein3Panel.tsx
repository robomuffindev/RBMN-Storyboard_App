/**
 * Klein 3.0 — pure Klein reference mode (v1.201.0). No 3D anywhere.
 *
 * Character = tagged 2D reference images + ONE active base image.
 *   - upload refs, tag them (front/back/left/right/face/outfit/garment/other)
 *     `garment` = a photo of CLOTHING, not of the character: never an identity
 *     reference, never part of the core set.
 *   - 🪄 Analyze a reference → fills the description fields (existing VNCCS
 *     vision wizard endpoints)
 *   - generate MISSING views with Klein N-ref edits (no 3D)
 *   - 👙 strip any ref (underwear or nude) → becomes the base
 *   - ⬆ GAN-upscale the active base (upscale becomes active)
 * Poses = Pose Library 2.0 (SHARED with Klein 2.0: prompt-generated with
 * stored/editable prompts, uploads incl. openpose/depth images).
 * Generate = image 1 (active base) + image 2 (pose) → Klein 2-ref edit.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ImageLightbox } from '../CharacterStudio/p2Shared';
import { consumeFocusChar, setCurrentChar } from '../shared/currentChar';

const BASE = '/api/klein3';
const BASE_COS = '/api/costumes';   // v1.276.27 shared costume library

interface SlotMetaT { key: string; label: string; group: string; example: string }
interface OutfitViewT {
  id: string; url: string; download_url?: string; created_at?: string;
  // v1.276.24 — the reference images this view was actually built from, so the
  // output can be compared against its sources without guessing.
  built_from?: { id: string; url: string }[];
}
interface OutfitVariantT {
  variant: string; label: string; slots: Record<string, string>; extra?: string;
  garment_ref?: string | null; garment_url?: string | null;
  views: Record<string, OutfitViewT>; created_at?: string;
}
interface OutfitT { name: string; created_at?: string; variants: OutfitVariantT[] }
const POSE_BASE = '/api/klein2';   // shared Pose Library 2.0

// ── styles (match the page) ─────────────────────────────────────────────────
const box: React.CSSProperties = { background: '#161a22', border: '1px solid #2a2f3a', borderRadius: 10, padding: 16 };
const label: React.CSSProperties = { fontSize: 12, color: '#9aa4b2', marginBottom: 4, display: 'block' };
const input: React.CSSProperties = {
  width: '100%', background: '#0e1116', border: '1px solid #2a2f3a', borderRadius: 6,
  color: '#e6e9ee', padding: '7px 9px', fontSize: 13, boxSizing: 'border-box',
};
const btn: React.CSSProperties = {
  background: '#3b82f6', color: '#fff', border: 'none', borderRadius: 6,
  padding: '8px 14px', fontSize: 13, cursor: 'pointer', fontWeight: 600,
};
const btnGhost: React.CSSProperties = { ...btn, background: 'transparent', border: '1px solid #2a2f3a', color: '#cbd2dc' };
const btnSm: React.CSSProperties = { ...btnGhost, padding: '4px 8px', fontSize: 12 };
const chip = (active: boolean): React.CSSProperties => ({
  ...btnSm, borderColor: active ? '#3b82f6' : '#2a2f3a', color: active ? '#9cc2ff' : '#cbd2dc',
});
const errTxt: React.CSSProperties = { color: '#ff8a8a', fontSize: 12 };
const okTxt: React.CSSProperties = { color: '#5ee08a', fontSize: 12 };
const hint: React.CSSProperties = { color: '#8d97a5', fontSize: 12 };

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let d = `${res.status}`;
    try { d = (await res.json())?.detail || d; } catch { /* ignore */ }
    throw new Error(d);
  }
  return res.json() as Promise<T>;
}

// ── types ───────────────────────────────────────────────────────────────────
interface RefT {
  id: string; tag: string; name: string; source: string; url: string;
  // v1.276.25 — real pixel size, so "is this big enough to be a reference?" is
  // answerable at a glance instead of guessable. `size` is what the file IS
  // now (post-upscale); `orig_size` is what it was before.
  size?: [number, number] | null; orig_size?: [number, number] | null;
  small?: boolean; upscaled?: boolean; upscaled_engine?: string | null;
}
interface BaseVerT { id: string; kind: string; url: string; view?: string; created_at?: string }
interface TaskT { worker?: string | null; status: string; error?: string | null }
interface JobT {
  status?: string; detail?: string; error?: string | null; done?: string[];
  worker?: string | null; tasks?: Record<string, TaskT>; workers?: string[];
}
interface WorkerT { url: string; healthy: boolean; klein: boolean; in_flight?: number | null }
interface CharT {
  slug: string; name: string; fields: Record<string, string>;
  ref_count: number; has_base: boolean; active_base_url?: string | null;
  missing_views: string[]; refs?: RefT[]; base_versions?: BaseVerT[];
  active_base?: string | null; jobs?: Record<string, JobT>; updated_at?: string;
  base_mode?: string;                       // auto | dressed | stripped (v1.217)
  base_sources?: Record<string, string>;    // view -> which image actually wins
}
interface PoseT { id: string; name: string; category: string; set?: string; tags?: string[]; prompt: string; source: string; url: string; seed?: number | null; has_image?: boolean }
interface SetInfoT { name: string; count: number; rendered: number; created_at?: string }
interface BatchRunT { status: string; done: number; total: number; tasks?: Record<string, { name: string; worker?: string | null; status: string }>; errors?: string[] }
interface GenT {
  gen_id: string; status: string; done: number; total: number; prompt: string;
  images: Array<{ id: string; url: string; seed?: number }>;
  refs: Array<{ name: string; url: string }>; error?: string | null;
  tasks?: Record<string, TaskT>; workers?: string[];
  pose?: string | null; pose_id?: string | null; created_at?: string | null;
}

// Downloadable instructions for ANY LLM to produce a valid pose-set import
// file — user says "I want a set of XYZ poses", pastes this doc, gets our format.
const POSE_IMPORT_LLM_DOC = `# Pose Set Generator — instructions for the assistant

You are generating a POSE SET import file for a character-image tool. The user
will tell you what kind of poses they want (theme, count, style of action).
Your ONLY output should be the file content described below — no commentary.

## Output format (JSON, preferred)

Output a single JSON array. Each element:

{
  "name": "Short unique pose name",
  "prompt": "description of the BODY POSE only",
  "category": "One word group like Standing, Seated, Action, Gesture, Combat, Emotional"
}

Rules for "prompt" — the most important part:
- Describe ONLY the body position: torso, arms, hands, legs, feet, head
  direction. Example: "crouching low on both feet, one fist planted on the
  ground, head raised looking forward"
- One single person, FULL BODY visible head to feet.
- Be explicit about limbs: which arm is raised, which knee is bent, where the
  hands are, where the person is looking.
- DO NOT describe: identity, face details, clothing, hair, props with brand
  names, backgrounds, scenery, lighting, camera settings, art style, quality
  words. The tool wraps your description in a standardized neutral-mannequin
  style prompt automatically — anything beyond the pose will conflict with it.
- Poses may use simple generic supports when needed: a plain chair, a plain
  wall, the ground. Nothing else.
- Prefer poses that read clearly in silhouette from a single camera viewpoint.

Rules for "name": unique within the set, 2-4 words, human-scannable.
Rules for "category": reuse the same few categories across the set.

## Count and variety

Default to 20 poses unless the user asks for a different count. Vary: stance
width, arm positions, head direction, kneeling/sitting/lying variants of the
theme, and at least a few side-on or turned poses.

## CSV alternative (only if the user asks for CSV)

Header row: name,prompt,category
Quote any field containing commas. Same content rules as JSON.

## Example output (3 poses, JSON)

[
  {"name": "Hero landing", "prompt": "crouching low with one fist and one knee touching the ground, other arm swept back, head raised looking forward", "category": "Action"},
  {"name": "Casual lean", "prompt": "leaning one shoulder against a plain wall, ankles crossed, arms folded loosely, head turned toward the camera", "category": "Standing"},
  {"name": "Floor read", "prompt": "sitting on the ground with legs crossed, leaning forward, both hands resting near the ankles, head tilted down", "category": "Seated"}
]
`;

const FIELDS: Array<[string, string]> = [
  ['age', 'Age'], ['sex', 'Sex'], ['race', 'Race'], ['skin_color', 'Skin color'],
  ['hair', 'Hair'], ['eyes', 'Eyes'], ['face', 'Face'], ['body', 'Body'],
  ['height', 'Height'], ['aesthetics', 'Aesthetics'], ['additional_details', 'Details'],
];
const TAGS = ['front', 'back', 'left', 'right', 'face', 'outfit', 'other'];
// the four the base picker actually resolves (v1.217 VIEW_TAGS)
const VIEWS = ['front', 'back', 'left', 'right'];
// v1.276.21 — an outfit renders the four body views PLUS a 🙂 face close-up.
// Earrings, a necklace, glasses and a collar are decided at head height and are
// a few pixels in an 832x1216 full body: the thing a wardrobe most needs to
// show is the thing the body views cannot.
const OUTFIT_VIEWS = [...VIEWS, 'face'];
// The set that actually drives generation: the four viewpoints plus the face
// anchor. Everything else is an extra, and outfit renders have their own panel.
const CORE_TAGS = ['front', 'back', 'left', 'right', 'face'];

export default function Klein3Panel() {
  // characters
  const [chars, setChars] = useState<CharT[]>([]);
  // v1.277.10: focus jump wins, else the PERSISTENT current character — so a
  // remount (the settings-load race) or a tab switch keeps the character.
  const [slug, _setSlug] = useState(() => consumeFocusChar());
  // every character change here becomes the studio-wide current character
  const setSlug = useCallback((v: React.SetStateAction<string>) => {
    _setSlug((prev) => {
      const next = typeof v === 'function' ? (v as (p: string) => string)(prev) : v;
      setCurrentChar(next);
      return next;
    });
  }, []);
  const [cur, setCur] = useState<CharT | null>(null);
  const [newName, setNewName] = useState('');
  const [msg, setMsg] = useState('');
  const [fields, setFields] = useState<Record<string, string>>({});
  const [analyzing, setAnalyzing] = useState(false);

  // refs / strip / upscale
  const [upTag, setUpTag] = useState('front');
  const [baseMode, setBaseMode] = useState<'auto' | 'dressed' | 'stripped'>('auto');
  const [baseResolve, setBaseResolve] = useState<Record<string, { found: boolean; source: string }> | null>(null);
  const [baseBusy, setBaseBusy] = useState(false);
  const [stripMode, setStripMode] = useState<'underwear' | 'nude'>('underwear');
  const [stripSrc, setStripSrc] = useState('');       // ref id ('' = front default)
  const [busy, setBusy] = useState(false);

  // poses (shared library)
  const [poses, setPoses] = useState<PoseT[]>([]);
  const [cats, setCats] = useState<string[]>([]);
  const [catFilter, setCatFilter] = useState('');
  const [poseId, setPoseId] = useState('');
  const [editPose, setEditPose] = useState<PoseT | null>(null);
  const [editPrompt, setEditPrompt] = useState('');
  const [poseBusy, setPoseBusy] = useState(false);
  const [poseMsg, setPoseMsg] = useState('');
  const [seedRun, setSeedRun] = useState<{ status: string; done: number; total: number } | null>(null);
  const [batchRun, setBatchRun] = useState<BatchRunT | null>(null);
  const [newPoseOpen, setNewPoseOpen] = useState(false);
  const [npName, setNpName] = useState('');
  const [npDesc, setNpDesc] = useState('');

  // generate
  const [extra, setExtra] = useState('');
  const [count, setCount] = useState(2);
  const [seed, setSeed] = useState('');
  const [gen, setGen] = useState<GenT | null>(null);
  const [genErr, setGenErr] = useState('');
  const [workers, setWorkers] = useState<WorkerT[]>([]);
  const [lightbox, setLightbox] = useState('');

  // ── 👗 Outfits (v1.276.7) ────────────────────────────────────────────────
  // The wardrobe lane. 13 slots, 4 shown by default and 9 behind a toggle so
  // the simple case stays four fields (Lorenzo: "if people want to do it
  // simply they can, but if they want more detail, this gives them the
  // avenue"). A VARIANT is one look within an outfit - "jacket off" - so a
  // scene change does not need a second wardrobe entry.
  const [oSlots, setOSlots] = useState<SlotMetaT[]>([]);
  const [outfits, setOutfits] = useState<OutfitT[]>([]);
  const [oName, setOName] = useState('');
  const [oVariant, setOVariant] = useState('');
  const [oVals, setOVals] = useState<Record<string, string>>({});
  const [oExtra, setOExtra] = useState('');
  const [oViews, setOViews] = useState<string[]>([...VIEWS, 'face']);
  const [oMore, setOMore] = useState(false);
  const [oBusy, setOBusy] = useState(false);
  // v1.276.17 — the form is now an EDITOR, not just a create box. `oEdit` holds
  // the (name, variant) currently loaded so Save knows what it is updating and
  // a rename knows what it is renaming FROM. null = composing something new.
  const [oEdit, setOEdit] = useState<{ name: string; variant: string } | null>(null);
  // 🧭 view verification (v1.276.18) — free, CPU, no render.
  const [verifying, setVerifying] = useState(false);
  const [verifyRows, setVerifyRows] = useState<{ view: string; ok: boolean; why: string }[]>([]);
  // ✅ auto-verify + retry on generation. Default ON: a wrong-facing base view
  // is not one bad image, it is a bad ingredient in every dataset, LoRA,
  // sheet and outfit built from it.
  const [vRetry, setVRetry] = useState(true);
  const [vTries, setVTries] = useState(3);
  // 🖼 the garment photo an outfit was scanned from. Kept with the form so it
  // rides into the render (reference 2) and is stored on the outfit, which is
  // what makes ↻ regenerate reproduce the same jacket months later.
  const [oGarment, setOGarment] = useState<{ ref: string; url: string } | null>(null);
  const [oScanKeep, setOScanKeep] = useState('');
  const [oScanning, setOScanning] = useState(false);
  // 🎨 v1.276.27 — describe-to-slots + the Costume Studio modal.
  const [oDesc, setODesc] = useState('');
  const [oDrafting, setODrafting] = useState(false);
  const [csOpen, setCsOpen] = useState(false);
  const [csModel, setCsModel] = useState('krea2');      // his call: Krea 2 default
  const [csModels, setCsModels] = useState<{ key: string; label: string; note: string; refs: number }[]>([]);
  // 🖼 v1.276.33 — reference images for EDIT models only (klein ≤5, qie ≤2).
  const [csRefs, setCsRefs] = useState<{ id: string; url: string; name: string }[]>([]);
  const csRefInput = useRef<HTMLInputElement | null>(null);
  const csRefCap = csModels.find((m) => m.key === csModel)?.refs ?? 0;
  const [csCount, setCsCount] = useState(4);
  const [csPrompt, setCsPrompt] = useState('');         // '' = build it from the slots
  const [csJob, setCsJob] = useState<any>(null);
  const [csLib, setCsLib] = useState<any[]>([]);
  const [csCand, setCsCand] = useState<any[]>([]);   // v1.276.30 staging area
  const [csWearer, setCsWearer] = useState('unisex');
  const [csName, setCsName] = useState('');
  // 🔎 v1.276.35 — library filter + search + the ℹ info panel
  const [csFilter, setCsFilter] = useState('');      // '' | woman | man | unisex
  const [csQuery, setCsQuery] = useState('');
  const [csInfo, setCsInfo] = useState<string>('');  // costume id, '' = closed
  const [csCounts, setCsCounts] = useState<Record<string, number>>({});
  const [csRaw, setCsRaw] = useState(false);
  const [csBusy, setCsBusy] = useState(false);
  // 👗 v1.276.22 — vision-check each finished outfit view against the garment
  // list and re-render it. Catches the case no prompt review ever will: an item
  // that is PRESENT but was never asked for.
  const [oVerify, setOVerify] = useState(true);
  const [oTries, setOTries] = useState(2);
  const garmentInput = useRef<HTMLInputElement | null>(null);

  // ⬆ Reference upscale target (v1.276.11). The GAN model is a fixed 4x
  // (4x-ClearRealityV1.pth since v1.276.14 — the workflow's baked-in default
  // was an ANIME model; the graph has no scale input), so the
  // amount is controlled by the long side we resize DOWN to afterwards. Same
  // shape as the Video Lab's largest_size picker. 2048 is the default because
  // a reference is uploaded to a worker on every render that reads it, and
  // full 4x measured 3328x4864 / 5.33 MB against 1401x2048 / 0.72 MB.
  const [upTarget, setUpTarget] = useState(2048);
  // ⬆ engine. Same vocabulary as the Character Studio: auto | seedvr2 | gan.
  // auto prefers SeedVR2 when a capable worker is online (it restores rather
  // than sharpens); an EXPLICIT seedvr2 request fails loudly if no box has the
  // node pack, instead of quietly giving you GAN output.
  const [upEngine, setUpEngine] = useState<'auto' | 'seedvr2' | 'gan'>('auto');
  // v1.276.25 — an upscale replaces the file IN PLACE under the same id, so the
  // panel has to re-fetch to pick up the new `?v=` revision. Without this the
  // browser keeps showing the pre-upscale copy and it looks like nothing
  // happened (Lorenzo: "it shows the original size and not the upscaled size").
  const refupStatus = cur?.jobs?.refup?.status;
  useEffect(() => {
    if (refupStatus && refupStatus !== 'running') { void loadCur(); void loadOutfits(); }
  }, [refupStatus]);   // eslint-disable-line react-hooks/exhaustive-deps

  // 🎨 poll the costume design job only while the studio is open
  // v1.276.35 — refetch when the filter or search box changes
  useEffect(() => {
    if (csOpen) void loadCostumes();
  }, [csFilter, csQuery, csOpen]);   // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!csOpen) return;
    let stop = false;
    const tick = async () => {
      try {
        const r = await j<any>(await fetch(`${BASE_COS}/job?t=${Date.now()}`));
        if (stop) return;
        setCsJob(r);
        // v1.276.31 — refresh on EVERY tick, not only at the end. Lorenzo:
        // "as images are rendered they are not showing up as they are
        // generated. it waits till they are all generated." Each image is
        // filed the moment it lands, so the list can show it immediately.
        if (r?.status === 'running' || r?.status === 'done' || r?.status === 'error') {
          // ⚠⚠ v1.276.32 — THIS is what put candidates in the library grid.
          // It fetched the UNFILTERED list (`?t=…`, no `stage`) and assigned it
          // to setCsLib, so every poll overwrote the library with EVERYTHING —
          // approved and unapproved together. The backend was correct the whole
          // time (`{candidates: 17, library: 1}`); the polling loop was
          // undoing the split three seconds after it was drawn. Lorenzo:
          // "when i look at our costume library its our approved costumes and
          // all candidates in the same grid. very wierd."
          // Inline rather than via loadCostumes() because this effect is
          // declared above it — but it must use the SAME two stage queries.
          try {
            const [lib, cand] = await Promise.all([
              j<{ costumes: any[] }>(await fetch(`${BASE_COS}?stage=library&t=${Date.now()}`)),
              j<{ costumes: any[] }>(await fetch(`${BASE_COS}?stage=candidates&t=${Date.now()}`)),
            ]);
            if (!stop) { setCsLib(lib.costumes || []); setCsCand(cand.costumes || []); }
          } catch { /* keep the last list */ }
        }
      } catch { /* transient */ }
    };
    void tick();
    const iv = window.setInterval(tick, 3000);
    return () => { stop = true; window.clearInterval(iv); };
  }, [csOpen]);   // eslint-disable-line react-hooks/exhaustive-deps
  const [libOpen, setLibOpen] = useState(false);
  const [selectedSet, setSelectedSet] = useState('');       // set-mode selection
  const [selectedTags, setSelectedTags] = useState<string[]>([]);  // tag-mode selection
  const [newSetName, setNewSetName] = useState('');
  const [setsInfo, setSetsInfo] = useState<SetInfoT[]>([]);
  const [allTags, setAllTags] = useState<string[]>([]);
  const [tagsSel, setTagsSel] = useState<string[]>([]);     // modal tag filter
  const [gens, setGens] = useState<GenT[]>([]);
  const [gensPoseOnly, setGensPoseOnly] = useState(false);

  const loadGens = useCallback(async (s?: string) => {
    const use = s || slug;
    if (!use) { setGens([]); return; }
    try {
      const r = await j<{ gens: GenT[] }>(await fetch(`${BASE}/characters/${use}/gens`));
      setGens(r.gens || []);
    } catch { /* gallery is best-effort */ }
  }, [slug]);

  const loadHealth = useCallback(async () => {
    try {
      const r = await j<{ workers: WorkerT[] }>(await fetch(`${BASE}/health`));
      setWorkers(r.workers || []);
    } catch { /* worker bar is best-effort */ }
  }, []);

  const loadChars = useCallback(async (keep?: string) => {
    try {
      const r = await j<{ characters: CharT[] }>(await fetch(`${BASE}/characters`));
      setChars(r.characters);
      const want = keep || slug;
      if (!want && r.characters.length) setSlug(r.characters[0].slug);
      else if (want && !r.characters.some((c) => c.slug === want)) setSlug(r.characters[0]?.slug || '');
    } catch (e) { setMsg(`characters: ${(e as Error).message}`); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug]);

  const loadCur = useCallback(async () => {
    if (!slug) { setCur(null); return; }
    try {
      const r = await j<CharT>(await fetch(`${BASE}/characters/${slug}?t=${Date.now()}`));
      setCur(r);
      setFields((f) => (Object.keys(f).length ? f : { ...r.fields }));
    } catch { setCur(null); }
  }, [slug]);

  const loadOutfits = useCallback(async () => {
    if (!slug) { setOutfits([]); return; }
    try {
      const r = await j<{ outfits: OutfitT[]; slots: SlotMetaT[] }>(
        await fetch(`${BASE}/characters/${slug}/outfits?t=${Date.now()}`));
      setOutfits(r.outfits || []);
      if (r.slots?.length) setOSlots(r.slots);
    } catch { setOutfits([]); }
  }, [slug]);

  const upscaleRef = async (rid: string) => {
    setMsg('');
    try {
      await j(await fetch(`${BASE}/characters/${slug}/refs/${rid}/upscale`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ max_side: upTarget, engine: upEngine }),
      }));
      await loadCur();
    } catch (e) { setMsg((e as Error).message); }
  };

  const delOutfit = async (name: string, variant: string | null) => {
    const what = variant === null
      ? `Delete the whole "${name}" outfit and every variant of it?`
      : `Delete "${name}${variant ? ` / ${variant}` : ' (base look)'}"?`;
    if (!window.confirm(`${what}\n\nThe rendered images are removed from disk.`)) return;
    setMsg('');
    try {
      await j(await fetch(`${BASE}/characters/${slug}/outfits/delete`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(variant === null ? { name } : { name, variant }),
      }));
      await loadOutfits(); await loadCur();
    } catch (e) { setMsg((e as Error).message); }
  };

  /** Delete ONE view of one variant (v1.276.16). Lorenzo: "when we get a bad
   *  output and just want to regenerate the one". Deleting a view is also what
   *  makes it eligible for "＋ missing" below, so bad → gone → refilled is a
   *  two-click loop that never touches the views that came out fine. */
  const delOutfitView = async (o: OutfitT, v: OutfitVariantT, view: string) => {
    if (!window.confirm(
      `Delete the ${view} view of "${o.name}${v.variant ? ` / ${v.variant}` : ''}"?`
      + '\n\nThe other views are untouched. You can refill it with ＋ missing.')) return;
    setMsg('');
    try {
      await j(await fetch(`${BASE}/characters/${slug}/outfits/delete`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: o.name, variant: v.variant || '', view }),
      }));
      await loadOutfits(); await loadCur();
    } catch (e) { setMsg((e as Error).message); }
  };

  /** Re-render a saved variant against the CURRENT base images. Outfits hold one
   *  slot per (name, variant, view), so this replaces in place rather than
   *  stacking another copy — the reason to press it is that the refs changed.
   *
   *  v1.276.16: `views` scopes it to one view (the ↻ on a thumbnail) and
   *  `onlyMissing` asks the backend for the gaps, so all three buttons — one
   *  view, the missing ones, everything — are the same call. */
  const regenOutfit = async (o: OutfitT, v: OutfitVariantT,
                             views?: string[], onlyMissing = false) => {
    setMsg(''); setOBusy(true);
    try {
      await j(await fetch(`${BASE}/characters/${slug}/outfits`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: o.name, variant: v.variant || '', slots: v.slots || {},
          extra: v.extra || '', garment_ref: v.garment_ref || null,
          // ⚠ v1.276.21: this used to be Object.keys(v.views) — "regenerate all"
          // meant "the views this outfit already has", so a 3-of-4 outfit could
          // never recover the 4th however many times you pressed it.
          views: views ?? OUTFIT_VIEWS,
          only_missing: onlyMissing,
          verify: oVerify, max_tries: oTries,
        }),
      }));
      await loadCur();
    } catch (e) { setMsg((e as Error).message); }
    setOBusy(false);
  };

  /** ✍ Describe the outfit in a sentence; the LLM fills the thirteen slots.
   *  Text model, not the vision one — there is no image yet. */
  const draftOutfit = async () => {
    if (!oDesc.trim()) return;
    setMsg(''); setODrafting(true);
    try {
      const r = await j<{ slots: Record<string, string>; model: string }>(
        await fetch(`${BASE_COS}/draft`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ description: oDesc.trim(), extra: oExtra.trim() }),
        }));
      const found = Object.keys(r.slots || {});
      setOVals((cur) => ({ ...cur, ...r.slots }));      // MERGE, never clobber
      setOMore((m) => m || found.some(
        (k) => (oSlots.find((s2) => s2.key === k)?.group || 'more') === 'more'));
      setMsg(`Filled ${found.length} slot(s) from your description — edit anything before generating.`);
    } catch (e) { setMsg((e as Error).message); }
    setODrafting(false);
  };

  const loadCostumes = useCallback(async () => {
    try {
      const qs = `${csFilter ? `&wearer=${csFilter}` : ''}${
        csQuery.trim() ? `&q=${encodeURIComponent(csQuery.trim())}` : ''}`;
      const [lib, cand] = await Promise.all([
        j<{ costumes: any[]; by_wearer: Record<string, number> }>(
          await fetch(`${BASE_COS}?stage=library&t=${Date.now()}${qs}`)),
        j<{ costumes: any[] }>(await fetch(`${BASE_COS}?stage=candidates&t=${Date.now()}`)),
      ]);
      setCsLib(lib.costumes || []); setCsCand(cand.costumes || []);
      setCsCounts(lib.by_wearer || {});
    } catch { /* keep what we have */ }
  }, [csFilter, csQuery]);

  const openCostumeStudio = async () => {
    setCsOpen(true); setMsg('');
    setCsName((v) => v || oName.trim());
    try {
      const r = await j<{ models: any[]; default: string }>(await fetch(`${BASE_COS}/models`));
      setCsModels(r.models || []);
      setCsModel((m) => m || r.default || 'krea2');
    } catch { /* the picker just stays empty */ }
    void loadCostumes();
  };

  /** Render candidate costume images on a neutral mannequin. */
  const designCostume = async () => {
    setMsg(''); setCsBusy(true);
    try {
      await j(await fetch(`${BASE_COS}/design`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: csName.trim() || oName.trim() || 'untitled costume', slots: cleanSlots(),
          extra: oExtra.trim(), prompt: csPrompt.trim(), raw_prompt: csRaw,
          wearer: csWearer, model: csModel, count: csCount,
          refs: csRefs.map((r) => r.id),
        }),
      }));
    } catch (e) { setMsg((e as Error).message); }
    setCsBusy(false);
  };

  /** Adopt a library costume onto THIS character: copies it in as the garment
   *  reference and (his call) rescans it back into the slots, so the text
   *  describes what was rendered rather than what was asked for. */
  const useCostume = async (cid: string) => {
    setMsg(''); setCsBusy(true);
    try {
      const r = await j<{ ref: string; url: string; slots: Record<string, string>; rescanned: boolean; name: string }>(
        await fetch(`${BASE_COS}/${cid}/adopt`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ slug, rescan: true }),
        }));
      setOGarment({ ref: r.ref, url: r.url });
      if (r.slots && Object.keys(r.slots).length) setOVals({ ...r.slots });
      if (!oName.trim()) setOName(r.name);
      setCsOpen(false);
      setMsg(r.rescanned
        ? `"${r.name}" attached and rescanned into the slots — check the wording, then Generate.`
        : `"${r.name}" attached as the costume reference.`);
      await loadCur();
    } catch (e) { setMsg((e as Error).message); }
    setCsBusy(false);
  };

  const cleanSlots = () => {
    const slots: Record<string, string> = {};
    for (const [k, v] of Object.entries(oVals)) if (v.trim()) slots[k] = v.trim();
    return slots;
  };

  const genOutfit = async () => {
    if (!oName.trim() || !oViews.length) return;
    setMsg(''); setOBusy(true);
    try {
      await j(await fetch(`${BASE}/characters/${slug}/outfits`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: oName.trim(), variant: oVariant.trim(),
                               slots: cleanSlots(), extra: oExtra.trim(),
                               garment_ref: oGarment?.ref || null,
                               views: oViews,
                               verify: oVerify, max_tries: oTries }),
      }));
      setOEdit({ name: oName.trim(), variant: oVariant.trim() });
      await loadCur();
    } catch (e) { setMsg((e as Error).message); }
    setOBusy(false);
  };

  /** 💾 Save — TEXT ONLY. No worker is contacted and no image changes; this is
   *  deliberately not the Generate button, so fixing a typo does not cost four
   *  renders. A rename moves the existing renders onto the new name. */
  const saveOutfit = async () => {
    if (!oEdit || !oName.trim()) return;
    setMsg(''); setOBusy(true);
    try {
      const r = await j<{ renamed: boolean; updated: number }>(
        await fetch(`${BASE}/characters/${slug}/outfits/update`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: oEdit.name, variant: oEdit.variant,
            new_name: oName.trim(), new_variant: oVariant.trim(),
            slots: cleanSlots(), extra: oExtra.trim(),
            garment_ref: oGarment?.ref || null,
          }),
        }));
      setOEdit({ name: oName.trim(), variant: oVariant.trim() });
      setMsg(r.renamed
        ? `Saved — ${r.updated} image(s) moved to "${oName.trim()}".`
        : `Saved. The images are unchanged — press ↻ regenerate to re-render them.`);
      await loadOutfits(); await loadCur();
    } catch (e) { setMsg((e as Error).message); }
    setOBusy(false);
  };

  /** ＋ New outfit — an empty form. Without this the only way to start a second
   *  outfit was to hand-clear thirteen fields. */
  const newOutfit = (base?: { o: OutfitT; v: OutfitVariantT }) => {
    setOEdit(null);
    setOName(base ? base.o.name : '');
    setOVariant('');
    setOVals(base ? { ...(base.v.slots || {}) } : {});
    setOExtra(base ? base.v.extra || '' : '');
    setOGarment(base?.v.garment_ref
      ? { ref: base.v.garment_ref, url: base.v.garment_url || '' } : null);
    setOViews(OUTFIT_VIEWS);
    setOMore(base ? Object.keys(base.v.slots || {}).some(
      (k) => (oSlots.find((s2) => s2.key === k)?.group || 'more') === 'more') : false);
    setMsg(base
      ? `New variation of "${base.o.name}" — name the change (e.g. "jacket off") and Generate.`
      : '');
  };

  /** Load an existing variant into the form for EDITING (v1.276.17). Clicking
   *  the outfit does this now; the form header shows what is loaded. */
  const editOutfit = (o: OutfitT, v: OutfitVariantT) => {
    setOEdit({ name: o.name, variant: v.variant || '' });
    setOName(o.name);
    setOVariant(v.variant || '');
    setOVals({ ...(v.slots || {}) });
    setOExtra(v.extra || '');
    setOGarment(v.garment_ref ? { ref: v.garment_ref, url: v.garment_url || '' } : null);
    setOViews(OUTFIT_VIEWS);
    setOMore(Object.keys(v.slots || {}).some(
      (k) => (oSlots.find((s2) => s2.key === k)?.group || 'more') === 'more'));
    setMsg('');
  };

  /** 🖼 Scan a garment PHOTO into the slots (v1.276.17). Two things happen and
   *  both matter: the vision model NAMES the items into editable text, and the
   *  photo is kept as a render reference so Klein copies the real cut and
   *  hardware rather than a paraphrase of them. */
  const scanGarment = async (f: File) => {
    setMsg(''); setOScanning(true);
    try {
      const fd = new FormData();
      fd.append('file', f);
      fd.append('keep', oScanKeep.trim());
      const r = await j<{ ref: string; url: string; slots: Record<string, string>; warning: string | null }>(
        await fetch(`${BASE}/characters/${slug}/outfits/scan`, { method: 'POST', body: fd }));
      setOGarment({ ref: r.ref, url: r.url });
      const found = Object.keys(r.slots || {});
      if (found.length) {
        // MERGE, never clobber: a scan of "just the hat" should not wipe the
        // trousers you already typed.
        setOVals((cur) => ({ ...cur, ...r.slots }));
        setOMore((m) => m || found.some(
          (k) => (oSlots.find((s2) => s2.key === k)?.group || 'more') === 'more'));
        setMsg(`Scanned: ${found.join(', ')}. Edit the wording before generating if you like.`);
      } else {
        setMsg(r.warning || 'The vision model found no garments — the photo is still attached as a reference.');
      }
    } catch (e) { setMsg((e as Error).message); }
    setOScanning(false);
  };

  const loadPoses = useCallback(async () => {
    try {
      const r = await j<{ poses: PoseT[]; categories: string[]; sets?: SetInfoT[]; tags?: string[]; seed_run: any; batch_run: BatchRunT | null }>(
        await fetch(`${POSE_BASE}/poses`));
      setPoses(r.poses); setCats(r.categories);
      setSetsInfo(r.sets || []); setAllTags(r.tags || []);
      setSeedRun(r.seed_run && r.seed_run.status ? r.seed_run : null);
      setBatchRun(r.batch_run && r.batch_run.status ? r.batch_run : null);
    } catch (e) { setPoseMsg(`pose library: ${(e as Error).message}`); }
  }, []);

  useEffect(() => {
    void loadChars(); void loadPoses(); void loadHealth();
    const iv = window.setInterval(() => { void loadHealth(); }, 30000);
    return () => window.clearInterval(iv);
  }, []);           // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setFields({}); void loadCur(); void loadGens(); void loadOutfits(); }, [slug]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {                          // refresh the gallery when a batch finishes
    if (gen && gen.status !== 'running') void loadGens();
  }, [gen?.status]);                         // eslint-disable-line react-hooks/exhaustive-deps
  // v1.276.7: the outfit job runs in the background, so the wardrobe list has
  // to refresh when it moves — otherwise the renders land on disk and the panel
  // still says "No outfits yet".
  const outfitJobStatus = cur?.jobs?.outfit?.status;
  useEffect(() => {
    if (!outfitJobStatus) return;
    void loadOutfits();
  }, [outfitJobStatus, cur?.jobs?.outfit?.detail]);   // eslint-disable-line react-hooks/exhaustive-deps

  const setJobStatus = cur?.jobs?.set?.status;
  useEffect(() => {                          // refresh gallery as set runs progress/finish
    if (!setJobStatus) return;
    void loadGens();
  }, [setJobStatus, cur?.jobs?.set?.detail]);   // eslint-disable-line react-hooks/exhaustive-deps

  // poll char jobs while any running
  const anyJobRunning = Object.values(cur?.jobs || {}).some((jb) => jb?.status === 'running');
  useEffect(() => {
    if (!anyJobRunning) return;
    const iv = window.setInterval(() => { void loadCur(); }, 3000);
    return () => window.clearInterval(iv);
  }, [anyJobRunning, loadCur]);
  useEffect(() => {
    if (seedRun?.status !== 'running' && batchRun?.status !== 'running') return;
    const iv = window.setInterval(() => { void loadPoses(); }, 3500);
    return () => window.clearInterval(iv);
  }, [seedRun?.status, batchRun?.status, loadPoses]);
  useEffect(() => {
    if (!gen || gen.status !== 'running') return;
    const iv = window.setInterval(async () => {
      try { setGen(await j<GenT>(await fetch(`${BASE}/gen/${gen.gen_id}`))); } catch { /* keep */ }
    }, 2500);
    return () => window.clearInterval(iv);
  }, [gen?.gen_id, gen?.status]);      // eslint-disable-line react-hooks/exhaustive-deps

  // ── character actions ─────────────────────────────────────────────────────
  const createChar = async () => {
    if (!newName.trim()) return;
    setBusy(true); setMsg('');
    try {
      const r = await j<CharT>(await fetch(`${BASE}/characters`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName.trim() }),
      }));
      setNewName(''); await loadChars(r.slug); setSlug(r.slug);
    } catch (e) { setMsg((e as Error).message); }
    setBusy(false);
  };

  const saveFields = async () => {
    setBusy(true); setMsg('');
    try {
      await j(await fetch(`${BASE}/characters/${slug}/fields`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fields }),
      }));
      setMsg('✓ description saved');
    } catch (e) { setMsg((e as Error).message); }
    setBusy(false);
  };

  // Analyze: reuse the app's existing vision wizard (upload ref bytes to the
  // VNCCS host, clone-analyze returns creator-style fields).  Multi-image:
  // front + face + newest others, up to 4 refs in one analyze call.
  const analyzeRefs = async (only?: RefT) => {
    const all = cur?.refs || [];
    const pick = only ? [only]
      : [...all.filter((r) => r.tag === 'front'), ...all.filter((r) => r.tag === 'face'),
         ...all.filter((r) => r.tag !== 'front' && r.tag !== 'face')].slice(0, 4);
    if (!pick.length) { setMsg('upload references first'); return; }
    setAnalyzing(true); setMsg(`🪄 analyzing ${pick.length} reference${pick.length > 1 ? 's' : ''}…`);
    try {
      const ups: Array<{ name: string; subfolder: string; type: string }> = [];
      for (const r of pick) {
        const blob = await (await fetch(r.url)).blob();
        const fd = new FormData();
        fd.append('file', new File([blob], `${r.id}.png`, { type: 'image/png' }));
        ups.push(await j(await fetch('/api/studio/vnccs/upload', { method: 'POST', body: fd })));
      }
      const res = await j<{ fields: Record<string, unknown> }>(
        await fetch('/api/studio/vnccs/wizard/clone-analyze', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ image: ups[0], images: ups, backend: 'auto' }),
        }));
      const merged: Record<string, string> = { ...fields };
      for (const [k] of FIELDS) {
        const v = res.fields?.[k];
        if (v != null && String(v).trim()) merged[k] = String(v).trim();
      }
      setFields(merged);
      setMsg('✓ analyzed — review the fields, then 💾 Save');
    } catch (e) { setMsg(`analyze failed: ${(e as Error).message}`); }
    setAnalyzing(false);
  };

  // ── ref actions ───────────────────────────────────────────────────────────
  const uploadRef = async (file: File) => {
    setBusy(true); setMsg('');
    try {
      const fd = new FormData();
      fd.append('file', file); fd.append('tag', upTag);
      await j(await fetch(`${BASE}/characters/${slug}/refs`, { method: 'POST', body: fd }));
      await loadCur();
    } catch (e) { setMsg(`upload failed: ${(e as Error).message}`); }
    setBusy(false);
  };

  const setTag = async (rid: string, tag: string) => {
    try {
      await j(await fetch(`${BASE}/characters/${slug}/refs/${rid}/update`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tag }),
      }));
      await loadCur();
    } catch (e) { setMsg((e as Error).message); }
  };

  const delRef = async (rid: string) => {
    try {
      await j(await fetch(`${BASE}/characters/${slug}/refs/${rid}/delete`, { method: 'POST' }));
      await loadCur();
    } catch (e) { setMsg((e as Error).message); }
  };

  const genViews = async () => {
    if (!cur?.missing_views.length) return;
    setMsg('');
    try {
      await j(await fetch(`${BASE}/characters/${slug}/views/generate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ views: cur.missing_views,
                               verify: vRetry, max_tries: vTries }),
      }));
      await loadCur();
    } catch (e) { setMsg((e as Error).message); }
  };

  /** 🧭 Verify the existing view set — FREE. No worker, no GPU, no render:
   *  insightface on the CPU measures which way each view is actually facing.
   *  `demote` files the failures under `other` so they stop being used as
   *  references and the view reads as MISSING again, ready to be refilled. */
  const verifyViews = async (demote: boolean) => {
    setMsg(''); setVerifying(true);
    try {
      const r = await j<{ checked: number; failed: number; demoted: number;
                          rows: { view: string; ok: boolean; why: string }[];
                          missing_views: string[] }>(
        await fetch(`${BASE}/characters/${slug}/views/verify`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ demote }),
        }));
      setVerifyRows(r.rows);
      setMsg(r.failed === 0
        ? `✅ ${r.checked} view reference(s) checked — all facing the right way.`
        : demote
          ? `${r.failed} of ${r.checked} were wrong and have been set aside. Missing now: ${r.missing_views.join(', ') || 'none'}.`
          : `⚠ ${r.failed} of ${r.checked} are wrong — press "set aside" to remove them from the reference set.`);
      await loadCur();
    } catch (e) { setMsg((e as Error).message); }
    setVerifying(false);
  };

  // v1.275.2: full re-run with a FRESH 🙂 face anchor — for when the set
  // exists but the faces drifted. Renders the close-up first, then all views.
  const regenViewsFaced = async () => {
    setMsg('');
    try {
      await j(await fetch(`${BASE}/characters/${slug}/views/generate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ views: ['front', 'back', 'left', 'right'], regen_face: true,
                               verify: vRetry, max_tries: vTries }),
      }));
      await loadCur();
    } catch (e) { setMsg((e as Error).message); }
  };

  // v1.225: PUT returns `resolves_to` for every view, so the consequence of the
  // toggle is visible BEFORE a render is spent on it.
  const setMode = async (m: 'auto' | 'dressed' | 'stripped') => {
    setMsg(''); setBaseBusy(true); setBaseMode(m);
    try {
      const r = await j<{ mode: string; resolves_to: Record<string, { found: boolean; source: string }> }>(
        await fetch(`${BASE}/characters/${slug}/base-mode`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode: m }),
        }));
      setBaseResolve(r.resolves_to);
      await loadCur();
    } catch (e) { setMsg((e as Error).message); }
    finally { setBaseBusy(false); }
  };

  const doStrip = async () => {
    setMsg('');
    try {
      await j(await fetch(`${BASE}/characters/${slug}/strip`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: stripMode, source_ref_id: stripSrc || null }),
      }));
      await loadCur();
    } catch (e) { setMsg((e as Error).message); }
  };

  useEffect(() => {
    if (cur?.base_mode) setBaseMode(cur.base_mode as 'auto' | 'dressed' | 'stripped');
    if (cur?.base_sources) {
      setBaseResolve(Object.fromEntries(Object.entries(cur.base_sources)
        .map(([v, s]) => [v, { found: !String(s).startsWith('active base (no'), source: String(s) }])));
    }
  }, [cur?.slug, cur?.base_mode, cur?.updated_at]);

  const doUpscale = async () => {
    setMsg('');
    try {
      await j(await fetch(`${BASE}/characters/${slug}/base/upscale`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
      }));
      await loadCur();
    } catch (e) { setMsg((e as Error).message); }
  };

  const activate = async (vid: string) => {
    try {
      await j(await fetch(`${BASE}/characters/${slug}/base/activate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ version_id: vid }),
      }));
      await loadCur();
    } catch (e) { setMsg((e as Error).message); }
  };

  const delVersion = async (vid: string) => {
    try {
      await j(await fetch(`${BASE}/characters/${slug}/base/${vid}/delete`, { method: 'POST' }));
      await loadCur();
    } catch (e) { setMsg((e as Error).message); }
  };

  const regenVersion = async (v: BaseVerT) => {
    if (!v.view || !v.kind.startsWith('stripped_')) return;
    setMsg('');
    try {
      await j(await fetch(`${BASE}/characters/${slug}/strip`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: v.kind.replace('stripped_', ''), view: v.view }),
      }));
      await loadCur();
    } catch (e) { setMsg((e as Error).message); }
  };

  const baseFromRef = async (rid: string) => {
    try {
      await j(await fetch(`${BASE}/characters/${slug}/base/from_ref`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ref_id: rid }),
      }));
      await loadCur();
    } catch (e) { setMsg((e as Error).message); }
  };

  // ── pose actions (shared Pose Library 2.0) ────────────────────────────────
  const savePose = async (regenerate: boolean) => {
    if (!editPose) return;
    setPoseBusy(true); setPoseMsg('');
    try {
      await j(await fetch(`${POSE_BASE}/poses/${editPose.id}/update`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: editPrompt, regenerate }),
      }));
      setEditPose(null); await loadPoses();
      setPoseMsg(regenerate ? '✓ saved + regenerated' : '✓ saved');
    } catch (e) { setPoseMsg((e as Error).message); }
    setPoseBusy(false);
  };
  const createPose = async () => {
    if (!npName.trim() || !npDesc.trim()) return;
    setPoseBusy(true); setPoseMsg('');
    try {
      await j(await fetch(`${POSE_BASE}/poses`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: npName.trim(), category: catFilter || 'Custom', prompt: npDesc.trim() }),
      }));
      setNewPoseOpen(false); setNpName(''); setNpDesc('');
      await loadPoses(); setPoseMsg('✓ pose generated');
    } catch (e) { setPoseMsg((e as Error).message); }
    setPoseBusy(false);
  };
  const uploadPose = async (file: File) => {
    setPoseBusy(true); setPoseMsg('');
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('name', file.name.replace(/\.[^.]+$/, ''));
      if (catFilter) fd.append('category', catFilter);
      await j(await fetch(`${POSE_BASE}/poses/upload`, { method: 'POST', body: fd }));
      await loadPoses(); setPoseMsg('✓ uploaded (openpose/depth/photo all fine)');
    } catch (e) { setPoseMsg((e as Error).message); }
    setPoseBusy(false);
  };
  const seedDefaults = async () => {
    setPoseMsg('');
    try {
      await j(await fetch(`${POSE_BASE}/poses/seed-defaults`, { method: 'POST' }));
      setSeedRun({ status: 'running', done: 0, total: 1 }); await loadPoses();
    } catch (e) { setPoseMsg((e as Error).message); }
  };
  const importPack = async (file: File) => {
    setPoseBusy(true); setPoseMsg('📦 importing pack…');
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('category', catFilter || file.name.replace(/\.(zip|json)$/i, '').slice(0, 24) || 'Pack');
      const r = await j<{ imported: number; skipped: number; errors: string[] }>(
        await fetch(`${POSE_BASE}/poses/import-pack`, { method: 'POST', body: fd }));
      await loadPoses();
      setPoseMsg(`✓ pack: ${r.imported} poses imported${r.skipped ? `, ${r.skipped} dupes skipped` : ''}${r.errors?.length ? ` — ${r.errors.length} errors (${r.errors[0]})` : ''}`);
    } catch (e) { setPoseMsg(`pack import failed: ${(e as Error).message}`); }
    setPoseBusy(false);
  };
  const importPoses = async (file: File) => {
    setPoseBusy(true); setPoseMsg('');
    try {
      const fd = new FormData();
      fd.append('file', file);
      if (catFilter) fd.append('category', catFilter);   // import INTO the open set
      const r = await j<{ imported: number; skipped: number }>(
        await fetch(`${POSE_BASE}/poses/import`, { method: 'POST', body: fd }));
      await loadPoses();
      setPoseMsg(`✓ imported ${r.imported} into ${catFilter ? `set “${catFilter}”` : 'their own sets'}${r.skipped ? `, skipped ${r.skipped} (dupes/invalid)` : ''} — hit 🎨 to render them`);
    } catch (e) { setPoseMsg(`import failed: ${(e as Error).message}`); }
    setPoseBusy(false);
  };
  const generateMissing = async () => {
    setPoseMsg('');
    try {
      const r = await j<{ started: boolean; total?: number; note?: string }>(
        await fetch(`${POSE_BASE}/poses/generate-missing`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(catFilter ? { category: catFilter } : {}),
        }));
      if (!r.started) setPoseMsg(r.note || 'nothing to generate');
      else setBatchRun({ status: 'running', done: 0, total: r.total || 0 });
      await loadPoses();
    } catch (e) { setPoseMsg((e as Error).message); }
  };
  const delPose = async (id: string) => {
    setPoseBusy(true);
    try {
      await j(await fetch(`${POSE_BASE}/poses/${id}/delete`, { method: 'POST' }));
      if (poseId === id) setPoseId('');
      setEditPose(null); await loadPoses();
    } catch (e) { setPoseMsg((e as Error).message); }
    setPoseBusy(false);
  };

  // ── set management ────────────────────────────────────────────────────────
  const createSet = async () => {
    const nm = newSetName.trim();
    if (!nm) return;
    try {
      await j(await fetch(`${POSE_BASE}/pose-sets`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category: nm }),
      }));
      setNewSetName('');
      await loadPoses();
      setCatFilter(nm);          // open the new (empty) set's screen
      setPoseMsg(`✓ set “${nm}” created — import or create poses into it`);
    } catch (e) { setPoseMsg((e as Error).message); }
  };

  const renameSet = async () => {
    if (!catFilter) return;
    const nn = window.prompt('Rename set to:', catFilter);
    if (!nn || nn.trim() === catFilter) return;
    try {
      await j(await fetch(`${POSE_BASE}/poses/set-rename`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category: catFilter, new: nn.trim() }),
      }));
      if (selectedSet === catFilter) setSelectedSet(nn.trim());
      setCatFilter(nn.trim());
      await loadPoses();
    } catch (e) { setPoseMsg((e as Error).message); }
  };
  const purgeImageless = async () => {
    if (!catFilter) return;
    const targets = poses.filter((p) => p.category === catFilter && p.has_image === false);
    if (!targets.length) { setPoseMsg('no image-less poses in this set'); return; }
    if (!window.confirm(`Remove ${targets.length} image-less pose(s) from "${catFilter}"?`)) return;
    setPoseBusy(true);
    for (const p of targets) {
      try { await j(await fetch(`${POSE_BASE}/poses/${p.id}/delete`, { method: 'POST' })); }
      catch { /* keep going */ }
    }
    setPoseBusy(false);
    await loadPoses();
    setPoseMsg(`✓ removed ${targets.length} image-less pose(s)`);
  };

  const deleteSet = async () => {
    if (!catFilter) return;
    if (!window.confirm(`Delete set "${catFilter}" and ALL its poses?`)) return;
    try {
      await j(await fetch(`${POSE_BASE}/poses/set-delete`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category: catFilter }),
      }));
      if (selectedSet === catFilter) setSelectedSet('');
      setCatFilter('');
      await loadPoses();
    } catch (e) { setPoseMsg((e as Error).message); }
  };

  // ── generate ──────────────────────────────────────────────────────────────
  const generateSetRun = async () => {
    setGenErr('');
    if (!cur?.has_base) { setGenErr('No base yet — tag a front reference or strip one.'); return; }
    if (!selectedSet && !selectedTags.length) { setGenErr('Pick a set or tags in the Pose Library.'); return; }
    try {
      await j(await fetch(`${BASE}/generate-set`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          slug, category: selectedSet || null,
          tags: selectedTags.length ? selectedTags : null, prompt_extra: extra,
          width: 832, height: 1216, seed: seed.trim() ? Number(seed.trim()) : null,
        }),
      }));
      await loadCur(); await loadGens();
    } catch (e) { setGenErr((e as Error).message); }
  };

  const doGenerate = async () => {
    if (selectedSet || selectedTags.length) { await generateSetRun(); return; }
    setGenErr('');
    if (!cur?.has_base) { setGenErr('No base yet — tag a front reference or strip one.'); return; }
    if (!poseId) { setGenErr('Pick a pose in the Pose Library.'); return; }
    try {
      const r = await j<{ gen_id: string }>(await fetch(`${BASE}/generate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          slug, pose_id: poseId, prompt_extra: extra, count,
          width: 832, height: 1216, seed: seed.trim() ? Number(seed.trim()) : null,
        }),
      }));
      setGen({ gen_id: r.gen_id, status: 'running', done: 0, total: count, prompt: '', images: [], refs: [] });
    } catch (e) { setGenErr((e as Error).message); }
  };

  const shownPoses = poses
    .filter((p) => !catFilter || (p.set || p.category) === catFilter)
    .filter((p) => !tagsSel.length || (p.tags || []).some((t) => tagsSel.includes(t)));
  const selPose = poses.find((p) => p.id === poseId) || null;
  const jobs = cur?.jobs || {};
  const taskIcon = (s: string) => s === 'done' ? '✓' : s === 'error' ? '✗' : s === 'running' ? '⏳' : '·';
  const jobLine = (k: string, jb?: JobT) => {
    if (!jb?.status) return null;
    let line = `${k}: ${jb.status}${jb.detail ? ` (${jb.detail})` : ''}${jb.worker ? ` @ ${jb.worker}` : ''}`;
    if (jb.tasks) {
      line += ' — ' + Object.entries(jb.tasks)
        .map(([key, t]) => `${key}${t.worker ? `@${t.worker}` : ''} ${taskIcon(t.status)}`).join(' · ');
    }
    if (jb.error) line += ` — ${jb.error}`;
    return line;
  };

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(330px,1fr) minmax(380px,1.2fr) minmax(360px,1.1fr)', gap: 16, alignItems: 'start' }}>
      {/* v1.276.0 — WHO AM I WORKING ON. The character was only identifiable by
          reading the dropdown's current value, which is easy to lose track of
          once you are three columns deep in refs and poses. */}
      {slug && (
        <div style={{ gridColumn: '1 / -1', display: 'flex', alignItems: 'center', gap: 10,
                      background: '#161a22', border: '1px solid #2a2f3a', borderRadius: 8,
                      padding: '7px 12px', position: 'sticky', top: 0, zIndex: 5 }}>
          <span style={{ fontSize: 15, fontWeight: 700, color: '#e6e9ee' }}>
            🎯 {chars.find((c) => c.slug === slug)?.name || slug}
          </span>
          <span style={{ fontSize: 11, color: '#8d97a5' }}>
            Klein 3.0 · {chars.find((c) => c.slug === slug)?.ref_count ?? 0} refs
            {chars.find((c) => c.slug === slug)?.has_base ? ' · ✓ base' : ' · no base yet'}
          </span>
        </div>
      )}
      {/* ── column 1: character + description ── */}
      <div style={{ ...box, display: 'grid', gap: 10, alignContent: 'start' }}>
        <h3 style={{ margin: 0, fontSize: 15 }}>🎯 Character</h3>
        <div style={{ display: 'flex', gap: 6 }}>
          <select style={input} value={slug} onChange={(e) => setSlug(e.target.value)}>
            {chars.map((c) => <option key={c.slug} value={c.slug}>{c.name}{c.has_base ? ' — ✓ base' : ''}</option>)}
            {!chars.length && <option value="">(no characters yet)</option>}
          </select>
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          <input style={input} value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="new character name" />
          <button style={btnGhost} disabled={busy || !newName.trim()} onClick={createChar}>➕</button>
        </div>
        {msg && <p style={msg.startsWith('✓') ? okTxt : errTxt}>{msg}</p>}
        {cur && (
          <>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <b style={{ fontSize: 13, color: '#e6e9ee' }}>Description</b>
              <div style={{ flex: 1 }} />
              <button style={btnSm} disabled={busy} onClick={saveFields}>💾 Save</button>
            </div>
            <button style={{ ...btn, background: '#7c3aed' }} disabled={analyzing || !(cur.refs?.length)}
                    onClick={() => void analyzeRefs()}>
              {analyzing ? '🪄 Analyzing…' : '🪄 Analyze references (LLM) → fill description'}
            </button>
            <p style={{ ...hint, margin: 0 }}>
              Sends up to 4 refs (front + face first) to the vision LLM and fills the fields
              below — review + edit, then 💾 Save. The tiny 🪄 on a thumbnail analyzes just that one.
            </p>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
              {FIELDS.map(([k, lbl]) => (
                <div key={k} style={k === 'additional_details' || k === 'aesthetics' ? { gridColumn: '1 / -1' } : undefined}>
                  <label style={label}>{lbl}</label>
                  <input style={input} value={fields[k] || ''} onChange={(e) => setFields({ ...fields, [k]: e.target.value })} />
                </div>
              ))}
            </div>
            <div>
              <label style={label}>Active base (image 1 of every generation)</label>
              {cur.active_base_url
                ? <img src={`${cur.active_base_url}?t=${cur.updated_at || ''}`} alt="base"
                       style={{ width: 150, borderRadius: 6, border: '1px solid #2a2f3a', cursor: 'zoom-in' }}
                       onClick={() => setLightbox(`${cur.active_base_url}?t=${cur.updated_at || ''}`)} />
                : <p style={{ ...hint, margin: 0 }}>none yet — tag a front ref, or strip one below</p>}
              {cur.active_base_url && (
                <div style={{ marginTop: 6 }}>
                  <button style={btnSm} disabled={jobs.upscale?.status === 'running'} onClick={doUpscale}>
                    {jobs.upscale?.status === 'running' ? '⏳ Upscaling…' : '⬆ Upscale base (result becomes active)'}
                  </button>
                </div>
              )}
            </div>
            {(cur.base_versions?.length || 0) > 0 && (
              <div>
                <label style={label}>Base versions (click to activate)</label>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {cur.base_versions!.map((v) => (
                    <div key={v.id} onClick={() => activate(v.id)} title={`${v.view ? v.view + ' · ' : ''}${v.kind} — click to use as image 1`}
                         style={{ cursor: 'pointer', border: `2px solid ${cur.active_base === v.id ? '#3b82f6' : '#2a2f3a'}`, borderRadius: 6, padding: 2 }}>
                      <img src={`${v.url}?t=${v.created_at || ''}`} alt={v.kind} style={{ width: 64, borderRadius: 4, display: 'block' }} />
                      <div style={{ fontSize: 10, color: cur.active_base === v.id ? '#9cc2ff' : '#8d97a5', textAlign: 'center' }}>
                        {v.view ? `${v.view} ` : ''}{v.kind.replace('stripped_', '👙')}
                      </div>
                      <div style={{ display: 'flex', gap: 2, justifyContent: 'center', marginTop: 2 }}>
                        {v.kind.startsWith('stripped_') && v.view && (
                          <button style={{ ...btnSm, padding: '1px 5px', fontSize: 10 }}
                                  title={`Regenerate this ${v.view} ${v.kind.replace('stripped_', '')} image (replaces this slot)`}
                                  disabled={jobs.strip?.status === 'running'}
                                  onClick={(e) => { e.stopPropagation(); void regenVersion(v); }}>🔁</button>
                        )}
                        <button style={{ ...btnSm, padding: '1px 5px', fontSize: 10, color: '#ff8a8a' }}
                                title="Delete this version"
                                onClick={(e) => { e.stopPropagation(); void delVersion(v.id); }}>🗑</button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* ── column 2: references + views + strip ── */}
      <div style={{ ...box, display: 'grid', gap: 10, alignContent: 'start' }}>
        <h3 style={{ margin: 0, fontSize: 15 }}>🖼 References</h3>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
          <select style={{ ...input, width: 'auto' }} value={upTag} onChange={(e) => setUpTag(e.target.value)}>
            {TAGS.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <label style={{ ...btnSm, display: 'inline-block' }}>
            ⬆ Upload reference
            <input type="file" accept="image/*" multiple style={{ display: 'none' }}
                   onChange={(e) => {
                     const files = Array.from(e.target.files || []);
                     e.target.value = '';
                     void (async () => { for (const f of files) await uploadRef(f); })();
                   }} />
          </label>
          {cur && cur.missing_views.length > 0 && (
            <button style={btnSm} disabled={jobs.views?.status === 'running'} onClick={genViews}>
              {jobs.views?.status === 'running'
                ? `⏳ Views ${jobs.views.detail || ''}`
                : `🧭 Generate missing views (${cur.missing_views.join(', ')})`}
            </button>
          )}
          {cur && cur.missing_views.length === 0 && (cur.ref_count || 0) > 0 && (
            <button style={btnSm} disabled={jobs.views?.status === 'running'}
                    title="Renders a zoomed face close-up FIRST, then regenerates all four views with it as the lead reference — use when the set's faces drifted."
                    onClick={regenViewsFaced}>
              {jobs.views?.status === 'running'
                ? `⏳ Views ${jobs.views.detail || ''}`
                : '🙂 Regenerate views (face-anchored)'}
            </button>
          )}
        </div>

        {/* 🧭 v1.276.18 — VERIFY. His ask: "add an option to verify our
            references so it verifies it rendered in the proper view or type
            and if it didn't retry to render it… so when we auto gen characters
            it does this itself and we won't end up with an incorrect base as
            that will poison all the other additional tasks in the autogen
            chain." The CHECK is free; only the retry costs a render. */}
        {cur && (
          <div style={{ background: '#0e1116', border: '1px solid #2a2f3a', borderRadius: 8,
                        padding: 8, display: 'grid', gap: 6 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <label style={{ ...hint, display: 'flex', alignItems: 'center', gap: 5, cursor: 'pointer' }}
                     title="After each view renders, measure which way it is actually facing and re-render it if it is wrong. The check costs nothing — only a retry spends a render.">
                <input type="checkbox" checked={vRetry} onChange={(e) => setVRetry(e.target.checked)} />
                ✅ verify &amp; retry on generate
              </label>
              <select style={{ ...input, width: 'auto', padding: '2px 6px' }} value={vTries}
                      disabled={!vRetry}
                      title="Attempts per view, including the first. Retries alternate between the plain route and the 🪞 mirror route."
                      onChange={(e) => setVTries(Number(e.target.value))}>
                {[2, 3, 4, 5, 6].map((n) => <option key={n} value={n}>up to {n} tries</option>)}
              </select>
              <div style={{ flex: 1 }} />
              <button style={btnSm} disabled={verifying}
                      title="Check the views you already have. FREE — no worker, no GPU, no render."
                      onClick={() => void verifyViews(false)}>
                {verifying ? '⏳ Checking…' : '🧭 Verify current views (free)'}
              </button>
              {verifyRows.some((r) => !r.ok) && (
                <button style={{ ...btnSm, color: '#ffcf8a', borderColor: '#5c4a22' }}
                        disabled={verifying}
                        title="Move the failing views out of the reference set. They are kept and labelled, not deleted — and the view reads as MISSING again so you can re-render it."
                        onClick={() => void verifyViews(true)}>⤵ set aside the bad ones</button>
              )}
            </div>
            {verifyRows.length > 0 && (
              <div style={{ display: 'grid', gap: 2 }}>
                {verifyRows.map((r, n) => (
                  <div key={n} style={{ fontSize: 11, color: r.ok ? '#8fe0ac' : '#ff8a8a' }}>
                    {r.ok ? '✅' : '❌'} <b>{r.view}</b> — {r.why}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {['views', 'strip', 'upscale', 'refup'].map((k) => {
          const l = jobLine(k, jobs[k]);
          return l && jobs[k]?.status !== 'done' ? <p key={k} style={jobs[k]?.status === 'error' ? errTxt : hint}>{l}</p> : null;
        })}
        {/* v1.276.9 — THE CORE SET, separated from everything else.
            The list used to be every ref flat, so the four images that actually
            drive every render sat among outfit renders and one-offs with no
            visual difference. Outfit renders are excluded entirely here: the
            👗 Outfits panel already shows them, grouped properly. */}
        {(() => {
          const all = cur?.refs || [];
          const core = CORE_TAGS.map((t) => ({ t, rs: all.filter((r) => r.tag === t) }));
          const other = all.filter((r) => !CORE_TAGS.includes(r.tag) && r.tag !== 'outfit');
          const outfitCount = all.filter((r) => r.tag === 'outfit').length;
          const RefCard = ({ r }: { r: RefT }) => (
            <div style={{ border: '1px solid #2a2f3a', borderRadius: 8, padding: 4, background: '#0e1116' }}>
              <div style={{ position: 'relative' }}>
                <img src={r.url} alt={r.name} style={{ width: '100%', borderRadius: 5, display: 'block', cursor: 'zoom-in' }}
                     onClick={() => setLightbox(r.url)} />
                {r.upscaled && (
                  <span title={`Upscaled${r.upscaled_engine ? ` (${r.upscaled_engine})` : ''}${
                    r.orig_size ? ` from ${r.orig_size[0]}×${r.orig_size[1]}` : ''}`}
                        style={{ position: 'absolute', top: 3, right: 3, fontSize: 10,
                                 background: 'rgba(14,17,22,0.85)', color: '#5ee08a',
                                 border: '1px solid #1e4d31', borderRadius: 8, padding: '0 5px' }}>⬆</span>
                )}
                {/* v1.276.25 — the actual pixel size, on the card. "Is this big
                    enough to be a reference?" was previously unanswerable
                    without downloading the file. Amber = under the useful
                    threshold, because everything is scaled to ~1MP anyway and a
                    small file is scaled UP out of detail that never existed. */}
                {r.size && (
                  <span title={r.small
                    ? 'Smaller than is useful as a reference — everything is scaled to ~1MP before it reaches the model, so this gets scaled UP out of detail that is not in the file. Upscale it.'
                    : 'Pixel size of this reference'}
                        style={{ position: 'absolute', bottom: 3, left: 3, fontSize: 10,
                                 background: 'rgba(14,17,22,0.85)',
                                 color: r.small ? '#ffcf8a' : '#9aa4b2',
                                 border: `1px solid ${r.small ? '#5c4a22' : '#2a2f3a'}`,
                                 borderRadius: 8, padding: '0 5px' }}>
                    {r.small ? '⚠ ' : ''}{r.size[0]}×{r.size[1]}
                  </span>
                )}
              </div>
              <select style={{ ...input, padding: '2px 4px', fontSize: 11, marginTop: 3 }} value={r.tag}
                      onChange={(e) => void setTag(r.id, e.target.value)}>
                {TAGS.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
              <div style={{ display: 'flex', gap: 4, marginTop: 3 }}>
                <button style={{ ...btnSm, padding: '2px 5px', fontSize: 10 }} title="Fill description fields from this image"
                        disabled={analyzing} onClick={() => void analyzeRefs(r)}>🪄</button>
                <button style={{ ...btnSm, padding: '2px 5px', fontSize: 10 }} title="Use this image as the base directly"
                        onClick={() => void baseFromRef(r.id)}>⭐</button>
                <button style={{ ...btnSm, padding: '2px 5px', fontSize: 10 }}
                        title={`Upscale this reference with ${
                          upEngine === 'auto' ? 'auto (prefers SeedVR2)'
                          : upEngine === 'seedvr2' ? 'SeedVR2' : 'the GAN'
                        } to ${upTarget === 8192 ? 'full 4x' : `${upTarget}px`} — in place, `
                          + 'sharper source for every render that reads it. The original is '
                          + 'kept, so this is reversible and you can compare engines.'}
                        disabled={jobs.refup?.status === 'running'}
                        onClick={() => void upscaleRef(r.id)}>⬆</button>
                <div style={{ flex: 1 }} />
                <button style={{ ...btnSm, padding: '2px 5px', fontSize: 10, color: '#ff8a8a' }} onClick={() => void delRef(r.id)}>🗑</button>
              </div>
            </div>
          );
          return (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 2, flexWrap: 'wrap' }}>
                <b style={{ fontSize: 12.5, color: '#e6e9ee' }}>⭐ Core set</b>
                <span style={hint}>the images every render reads from</span>
                <div style={{ flex: 1 }} />
                <span style={hint}>⬆ engine</span>
                <select style={{ ...input, width: 'auto', padding: '2px 6px', fontSize: 11 }}
                        value={upEngine}
                        onChange={(e) => setUpEngine(e.target.value as 'auto' | 'seedvr2' | 'gan')}
                        title="SeedVR2 is a diffusion restorer — usually much better on faces and fabric, but slower. GAN is the fast 4x sharpener. Auto picks SeedVR2 when a worker has the node pack.">
                  <option value="auto">auto (prefer SeedVR2)</option>
                  <option value="seedvr2">SeedVR2 (best, slower)</option>
                  <option value="gan">GAN 4x (fast)</option>
                </select>
                <span style={hint}>to</span>
                <select style={{ ...input, width: 'auto', padding: '2px 6px', fontSize: 11 }}
                        value={upTarget} onChange={(e) => setUpTarget(Number(e.target.value))}
                        title="Long side after upscaling. The GAN is a fixed 4x; this is the size it is fitted back to. Bigger = sharper but a heavier upload on every render that reads this reference.">
                  <option value={1536}>1536 px</option>
                  <option value={2048}>2048 px (default)</option>
                  <option value={2560}>2560 px</option>
                  <option value={3072}>3072 px</option>
                  <option value={8192}>full 4x (no cap)</option>
                </select>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(104px, 1fr))', gap: 8 }}>
                {core.map(({ t, rs }) => (rs.length ? rs.map((r) => <RefCard key={r.id} r={r} />) : (
                  <div key={`missing-${t}`} style={{ border: '1px dashed #2a2f3a', borderRadius: 8, padding: 4,
                                                     background: '#0e1116', minHeight: 96, display: 'flex',
                                                     alignItems: 'center', justifyContent: 'center',
                                                     flexDirection: 'column', gap: 2 }}>
                    <span style={{ fontSize: 18, color: '#3a4250' }}>○</span>
                    <span style={{ ...hint, fontSize: 10 }}>no {t}</span>
                  </div>
                )))}
              </div>

              {other.length > 0 && (
                <>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8 }}>
                    <b style={{ fontSize: 12.5, color: '#cbd2dc' }}>Other references</b>
                    <span style={hint}>{other.length} — extras and unchosen close-ups</span>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(104px, 1fr))',
                                gap: 8, maxHeight: 240, overflowY: 'auto' }}>
                    {other.map((r) => <RefCard key={r.id} r={r} />)}
                  </div>
                </>
              )}

              {outfitCount > 0 && (
                <p style={{ ...hint, marginTop: 6 }}>
                  👗 {outfitCount} outfit image(s) are not listed here — they live in the
                  Outfits panel below, grouped by outfit and variant.
                </p>
              )}
              {!all.length && <p style={hint}>No references yet — upload some (tag the frontal one “front”).</p>}
            </>
          );
        })()}

        <div style={{ border: '1px solid #2a2f3a', borderRadius: 8, padding: 10, display: 'grid', gap: 8 }}>
          <b style={{ fontSize: 13, color: '#e6e9ee' }}>🧥 Identity source — dressed or stripped</b>
          <p style={{ ...hint, margin: 0 }}>
            Which image every render starts from. <b>Dressed</b> uses your uploaded references and
            generated views, so his own clothes are kept and nothing has to be stripped.
            <b> Stripped</b> uses the stripped base set (needed when Klein must replace the
            clothing outright). Stripping is an extra edit per view and adds its own drift, so
            skip it when the shot never needed a clothing change.
          </p>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {([
              ['dressed', '🧥 Dressed', 'His own clothes, from your references'],
              ['stripped', '👙 Stripped', 'The stripped base set'],
              ['auto', '🤖 Auto', 'Newest version of each view (pre-v1.217 behaviour)'],
            ] as const).map(([m, label, tip]) => (
              <button key={m} title={tip}
                      style={baseMode === m ? { ...btn, background: '#2b6cb0' } : btnGhost}
                      disabled={!cur || baseBusy}
                      onClick={() => void setMode(m)}>{label}</button>
            ))}
            {baseBusy && <span style={{ ...hint, alignSelf: 'center' }}>working…</span>}
          </div>
          {baseResolve && (
            <div style={{ display: 'grid', gap: 2 }}>
              <span style={{ ...hint, margin: 0 }}>What each view would use right now:</span>
              {VIEWS.map((v) => {
                const r = baseResolve[v];
                if (!r) return null;
                const stripped = /stripped/.test(r.source);
                const fallback = /fallback|no .* view yet|active base/.test(r.source);
                return (
                  <div key={v} style={{ display: 'flex', gap: 6, fontSize: 11, fontFamily: 'monospace' }}>
                    <span style={{ color: '#8fa6bd', width: 46 }}>{v}</span>
                    <span style={{ color: fallback ? '#e0b36a' : stripped ? '#cbd2dc' : '#5ee08a' }}>
                      {r.source}
                    </span>
                  </div>
                );
              })}
              <span style={{ ...hint, margin: '2px 0 0' }}>
                Green = a real reference for that view. Amber = falling back, generate that view
                or tag a reference for it.
              </span>
            </div>
          )}
        </div>

        <div style={{ border: '1px solid #2a2f3a', borderRadius: 8, padding: 10, display: 'grid', gap: 8 }}>
          <b style={{ fontSize: 13, color: '#e6e9ee' }}>👙 Strip → base set</b>
          <p style={{ ...hint, margin: 0 }}>
            Strips the FULL standing set (newest ref of each tagged view — front/back/left/right)
            in parallel across the workers; identity, pose and framing kept, all footwear removed.
            The front result becomes the active base; click any version in the strip to use a
            different one for pose generation. Pick a single source below to strip just one.
          </p>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
            <select style={{ ...input, width: 'auto' }} value={stripMode} onChange={(e) => setStripMode(e.target.value as 'underwear' | 'nude')}>
              <option value="underwear">Underwear</option>
              <option value="nude">Nude</option>
            </select>
            <select style={{ ...input, width: 'auto', maxWidth: 180 }} value={stripSrc} onChange={(e) => setStripSrc(e.target.value)}>
              <option value="">source: ALL tagged views (set)</option>
              {(cur?.refs || []).map((r) => <option key={r.id} value={r.id}>only {r.tag}: {r.name.slice(0, 18)}</option>)}
            </select>
            <button style={btn} disabled={!cur || jobs.strip?.status === 'running'} onClick={doStrip}>
              {jobs.strip?.status === 'running' ? '⏳ Stripping…' : stripSrc ? 'Strip selected ref' : 'Strip the whole set'}
            </button>
          </div>
        </div>
      </div>

      {/* ── 👗 Outfits: full-width row under the three columns (v1.276.7) ── */}
      {cur && (
        <div style={{ ...box, gridColumn: '1 / -1', display: 'grid', gap: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <h3 style={{ margin: 0, fontSize: 15 }}>👗 Outfits</h3>
            <span style={hint}>
              Dress this character across every view. Each view is its own image, usable as a
              reference anywhere. A <b style={{ color: '#cbd2dc' }}>variant</b> is one look
              within an outfit &mdash; "jacket off" &mdash; not a second outfit.
            </span>
            <div style={{ flex: 1 }} />
            <button style={{ ...btnGhost, borderColor: '#2f6d46', color: '#8fe0ac' }}
                    title="Start a new, empty outfit"
                    onClick={() => newOutfit()}>＋ New outfit</button>
          </div>

          {/* v1.276.17: the form is an EDITOR. This bar says what is loaded in
              it, because "am I editing Red Leather or making a new thing" was
              the confusing part. */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
                        background: oEdit ? '#12202c' : '#0e1116',
                        border: `1px solid ${oEdit ? '#22415c' : '#2a2f3a'}`,
                        borderRadius: 8, padding: '6px 10px' }}>
            <span style={{ fontSize: 12, color: oEdit ? '#8fd6ff' : '#9aa4b2' }}>
              {oEdit
                ? `✎ Editing "${oEdit.name}${oEdit.variant ? ` / ${oEdit.variant}` : ' (base look)'}"`
                : '＋ New outfit — nothing loaded'}
            </span>
            <div style={{ flex: 1 }} />
            {oEdit && (
              <>
                <button style={{ ...btnGhost, padding: '2px 10px', fontSize: 12 }}
                        title="Save the name, variant and slot text. No images are rendered — press ↻ regenerate for that."
                        disabled={oBusy || !oName.trim()}
                        onClick={() => void saveOutfit()}>💾 Save changes</button>
                <button style={{ ...btnGhost, padding: '2px 10px', fontSize: 12 }}
                        title="Stop editing and compose a new outfit"
                        onClick={() => newOutfit()}>✕ Done editing</button>
              </>
            )}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            <div>
              <label style={label}>Outfit name *</label>
              <input style={input} value={oName} placeholder="Red Leather"
                     onChange={(e) => setOName(e.target.value)} />
            </div>
            <div>
              <label style={label}>Variant <span style={hint}>(blank = the base look)</span></label>
              <input style={input} value={oVariant} placeholder="jacket off"
                     onChange={(e) => setOVariant(e.target.value)} />
            </div>
          </div>

          {/* ✍ Describe it and let the LLM fill the slots (v1.276.27) ----- */}
          <div style={{ background: '#0e1116', border: '1px solid #2a2f3a', borderRadius: 8, padding: 10,
                        display: 'grid', gap: 6 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <span style={{ fontSize: 12, color: '#cbd2dc', fontWeight: 600 }}>✍ Describe it</span>
              <span style={hint}>
                Write the outfit in a sentence and the LLM fills the slots below. Edit anything
                after &mdash; the slots are what actually get rendered.
              </span>
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <input style={{ ...input, flex: 1, minWidth: 260 }} value={oDesc}
                     placeholder='e.g. "a battered desert scavenger: long dusty canvas coat, goggles, wrapped boots, leather straps"'
                     onChange={(e) => setODesc(e.target.value)}
                     onKeyDown={(e) => { if (e.key === 'Enter') void draftOutfit(); }} />
              <button style={btnGhost} disabled={oDrafting || !oDesc.trim()}
                      onClick={() => void draftOutfit()}>
                {oDrafting ? '⏳ Thinking…' : '✨ Fill the slots'}
              </button>
              <button style={{ ...btnGhost, borderColor: '#3a6ea5', color: '#8fd6ff' }}
                      title="Design this costume as an image first — render it on a neutral mannequin with the model of your choice, then pick one as the reference"
                      onClick={() => void openCostumeStudio()}>🎨 Costume Studio</button>
            </div>
          </div>

          {/* 🖼 Build an outfit FROM A PHOTO (v1.276.17) ------------------- */}
          <div style={{ background: '#0e1116', border: '1px solid #2a2f3a', borderRadius: 8, padding: 10,
                        display: 'grid', gap: 6 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <span style={{ fontSize: 12, color: '#cbd2dc', fontWeight: 600 }}>🖼 From a photo</span>
              <span style={hint}>
                The vision model names what it sees into the slots below, and the photo itself
                rides along as a render reference so the cut and hardware are copied, not
                paraphrased.
              </span>
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <input style={{ ...input, width: 260 }} value={oScanKeep}
                     placeholder='only interested in one item? e.g. "just the hat"'
                     onChange={(e) => setOScanKeep(e.target.value)} />
              <input ref={garmentInput} type="file" accept="image/*" style={{ display: 'none' }}
                     onChange={(e) => {
                       const f = e.target.files?.[0];
                       e.target.value = '';
                       if (f) void scanGarment(f);
                     }} />
              <button style={btnGhost} disabled={oScanning}
                      onClick={() => garmentInput.current?.click()}>
                {oScanning ? '⏳ Scanning…' : '🖼 Scan a garment photo'}
              </button>
              {oGarment && (
                <>
                  <img src={oGarment.url} alt="garment reference"
                       title="Click to view large — this image is passed to the render as image 2"
                       onClick={() => setLightbox(oGarment.url)}
                       style={{ height: 54, borderRadius: 6, border: '1px solid #2a2f3a', cursor: 'zoom-in' }} />
                  <button style={{ ...btnGhost, padding: '2px 8px', fontSize: 11, color: '#ff8a8a', borderColor: '#4a2130' }}
                          title="Render from the text only — do not pass the photo to Klein"
                          onClick={() => setOGarment(null)}>✕ detach photo</button>
                </>
              )}
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: 8 }}>
            {oSlots.filter((sl) => sl.group === 'core' || oMore).map((sl) => (
              <div key={sl.key}>
                <label style={label}>
                  {sl.label}
                  {sl.group === 'more' && <span style={{ ...hint, marginLeft: 4 }}>optional</span>}
                </label>
                <input style={input} value={oVals[sl.key] || ''} placeholder={sl.example}
                       onChange={(e) => setOVals((v) => ({ ...v, [sl.key]: e.target.value }))} />
              </div>
            ))}
          </div>

          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <button style={btnGhost} onClick={() => setOMore(!oMore)}>
              {oMore ? '\u2212 fewer fields' : `\uFF0B more detail (${oSlots.filter((sl) => sl.group === 'more').length} more slots)`}
            </button>
            <span style={hint}>
              Leave a slot blank to leave it out entirely &mdash; Klein has no negative prompt,
              so "no hat" would put a hat on her.
            </span>
          </div>

          <div>
            <label style={label}>Anything else (free text, appended to the garments)</label>
            <input style={input} value={oExtra} placeholder="sleeves pushed up to the elbow"
                   onChange={(e) => setOExtra(e.target.value)} />
          </div>

          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={label}>Views:</span>
            {OUTFIT_VIEWS.map((v) => (
              <button key={v} style={chip(oViews.includes(v))}
                      onClick={() => setOViews((cs) => cs.includes(v)
                        ? cs.filter((x) => x !== v) : [...cs, v])}>{v}</button>
            ))}
            <label style={{ ...hint, display: 'flex', alignItems: 'center', gap: 5, cursor: 'pointer' }}
                   title="After each view renders, a vision model compares it against your garment list and re-renders it if something is missing, the wrong colour, or PRESENT BUT NEVER ASKED FOR.">
              <input type="checkbox" checked={oVerify} onChange={(e) => setOVerify(e.target.checked)} />
              ✅ verify against the list
            </label>
            <select style={{ ...input, width: 'auto', padding: '2px 6px' }} value={oTries}
                    disabled={!oVerify} onChange={(e) => setOTries(Number(e.target.value))}>
              {[1, 2, 3].map((n) => <option key={n} value={n}>up to {n} tries</option>)}
            </select>
            <div style={{ flex: 1 }} />
            <button style={btn}
                    disabled={oBusy || !oName.trim() || !oViews.length || jobs.outfit?.status === 'running'}
                    onClick={() => void genOutfit()}>
              {jobs.outfit?.status === 'running'
                ? `\u23F3 ${jobs.outfit.detail || 'rendering'}`
                : `\uD83D\uDC57 Generate outfit (${oViews.length} view${oViews.length === 1 ? '' : 's'})`}
            </button>
          </div>
          {jobLine('outfit', jobs.outfit) && jobs.outfit?.status !== 'done' && (
            <p style={jobs.outfit?.status === 'error' ? errTxt : hint}>{jobLine('outfit', jobs.outfit)}</p>
          )}

          {/* ⚠ MEASURED 2026-08-10: a franchise name in a garment slot drags that
              character's accessories in. "supergirl leotard" added glasses on
              5/5 renders and no correction removed them; describing the same
              garment literally, at the same seed, produced none. */}
          {(() => {
            const NAMEY = ['supergirl', 'superman', 'batman', 'batgirl', 'spiderman', 'spider-man',
              'wonder woman', 'harley quinn', 'catwoman', 'iron man', 'captain america', 'wolverine',
              'deadpool', 'jedi', 'sith', 'stormtrooper', 'hogwarts', 'gryffindor', 'sailor moon',
              'naruto', 'goku', 'mario', 'zelda', 'elsa'];
            const blob = (Object.values(oVals).join(' ') + ' ' + oExtra).toLowerCase();
            const hit = NAMEY.find((n) => blob.includes(n));
            return hit ? (
              <p style={{ ...hint, color: '#ffcf8a', margin: 0 }}>
                ⚠ "{hit}" is a character name, and naming a character brings that character's
                accessories with it &mdash; measured: "supergirl leotard" added glasses to 5 of 5
                renders, and no correction removed them. Describing the same garment literally
                ("a blue long-sleeved leotard with a red and yellow diamond shield emblem")
                produced none at the same seed.
              </p>
            ) : null;
          })()}

          {!outfits.length && <p style={hint}>No outfits yet &mdash; name one and hit Generate.</p>}

          {outfits.map((o) => (
            <div key={o.name} style={{ border: '1px solid #2a2f3a', borderRadius: 8, padding: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <span style={{ fontWeight: 700, color: '#e6e9ee', fontSize: 14 }}>{o.name}</span>
                <span style={hint}>{o.variants.length} variant(s)</span>
                <div style={{ flex: 1 }} />
                <button style={{ ...btnGhost, padding: '2px 8px', fontSize: 11, color: '#8fe0ac', borderColor: '#2f6d46' }}
                        title="Start a new variation of this outfit — the base look's slots are copied in, you name the change"
                        onClick={() => newOutfit({ o, v: o.variants[0] })}>＋ new variation</button>
                <button style={{ ...btnGhost, padding: '2px 8px', fontSize: 11, color: '#ff8a8a', borderColor: '#4a2130' }}
                        title="Delete this outfit and every variant of it"
                        onClick={() => void delOutfit(o.name, null)}>🗑 outfit</button>
              </div>
              {o.variants.map((v) => (
                <div key={v.variant || 'base'} style={{ marginBottom: 8 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4,
                                background: oEdit?.name === o.name && oEdit?.variant === (v.variant || '')
                                  ? '#12202c' : 'transparent',
                                border: `1px solid ${oEdit?.name === o.name && oEdit?.variant === (v.variant || '')
                                  ? '#22415c' : 'transparent'}`,
                                borderRadius: 6, padding: '3px 6px' }}>
                    {/* clicking the row loads it into the form above — Lorenzo:
                        "you should be able to click in the outfit area to bring
                        up its details, name and settings into the main outfits
                        area fields" */}
                    <span style={{ fontSize: 12, color: '#cbd2dc', fontWeight: 600, cursor: 'pointer' }}
                          title="Click to load this outfit into the form above"
                          onClick={() => editOutfit(o, v)}>{v.label}</span>
                    <span style={{ ...hint, cursor: 'pointer' }} onClick={() => editOutfit(o, v)}>
                      {Object.keys(v.slots || {}).length} slot(s)
                    </span>
                    {v.garment_url && (
                      <>
                        <span style={{ ...hint, fontSize: 10 }}>🖼 source photo</span>
                        <img src={v.garment_url} alt="garment reference"
                             title="The photo this outfit was scanned from — click to compare against the renders"
                             onClick={() => setLightbox(v.garment_url as string)}
                             style={{ height: 40, borderRadius: 4, border: '1px solid #3a6ea5', cursor: 'zoom-in' }} />
                      </>
                    )}
                    <button style={{ ...btnGhost, padding: '2px 8px', fontSize: 11 }}
                            title="Load this outfit into the form above to edit and save it"
                            onClick={() => editOutfit(o, v)}>✏️ edit</button>
                    <button style={{ ...btnGhost, padding: '2px 8px', fontSize: 11 }}
                            title="Re-render EVERY view of this variant against the CURRENT base images — replaces them in place"
                            disabled={oBusy || jobs.outfit?.status === 'running'}
                            onClick={() => void regenOutfit(o, v)}>↺ regenerate all</button>
                    {OUTFIT_VIEWS.some((vw) => !v.views[vw]) && (
                      <button style={{ ...btnGhost, padding: '2px 8px', fontSize: 11, color: '#8fd6ff', borderColor: '#22415c' }}
                              title="Render only the views this variant has no image for — the ones you deleted or never made. Everything already here is left alone."
                              disabled={oBusy || jobs.outfit?.status === 'running'}
                              onClick={() => void regenOutfit(o, v, undefined, true)}>
                        ＋ missing ({OUTFIT_VIEWS.filter((vw) => !v.views[vw]).length})
                      </button>
                    )}
                    <div style={{ flex: 1 }} />
                    <button style={{ ...btnGhost, padding: '2px 8px', fontSize: 11, color: '#ff8a8a', borderColor: '#4a2130' }}
                            title="Delete just this variant"
                            onClick={() => void delOutfit(o.name, v.variant || '')}>🗑</button>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))', gap: 6 }}>
                    {/* v1.276.16: every view of the SET gets a tile, present or
                        not. A missing view is a dashed placeholder you can fill
                        from here — the same shape as the ⭐ core set above, so a
                        gap looks like a gap instead of just not being there. */}
                    {OUTFIT_VIEWS.map((vw) => (v.views[vw] ? (
                      <div key={vw} style={{ background: '#0e1116', border: '1px solid #2a2f3a', borderRadius: 6, padding: 4 }}>
                        <img src={v.views[vw].url} alt={`${o.name} ${vw}`}
                             title="Click to view large (zoom + pan)"
                             onClick={() => setLightbox(v.views[vw].url)}
                             style={{ width: '100%', borderRadius: 4, display: 'block', cursor: 'zoom-in' }} />
                        {/* 🔍 v1.276.24 — the references this view came from,
                            side by side with the result. His ask: "if our
                            costume was based on a reference or multiple
                            reference images we should show them somehow so we
                            can compare the output with the reference costume
                            images." */}
                        {!!v.views[vw].built_from?.length && (
                          <div style={{ display: 'flex', gap: 3, marginTop: 3, alignItems: 'center' }}>
                            <span style={{ ...hint, fontSize: 10 }}>from</span>
                            {v.views[vw].built_from!.map((b) => (
                              <img key={b.id} src={b.url} alt="source reference"
                                   title="Reference this view was built from — click to compare"
                                   onClick={() => setLightbox(b.url)}
                                   style={{ height: 26, width: 20, objectFit: 'cover', borderRadius: 3,
                                            border: '1px solid #2a2f3a', cursor: 'zoom-in' }} />
                            ))}
                          </div>
                        )}
                        <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 3 }}>
                          <span style={{ ...hint, flex: 1 }}>{vw}</span>
                          <button style={{ ...btnGhost, padding: '1px 6px', fontSize: 11 }}
                                  title={`Re-render ONLY the ${vw} view — the other views are untouched`}
                                  disabled={oBusy || jobs.outfit?.status === 'running'}
                                  onClick={() => void regenOutfit(o, v, [vw])}>↺</button>
                          <a href={v.views[vw].download_url || `${v.views[vw].url}?download=1`} download
                             title="Download this single view"
                             style={{ ...btnGhost, padding: '1px 6px', fontSize: 11, textDecoration: 'none' }}>⬇</a>
                          <button style={{ ...btnGhost, padding: '1px 6px', fontSize: 11, color: '#ff8a8a', borderColor: '#4a2130' }}
                                  title={`Delete just the ${vw} view of this variant`}
                                  onClick={() => void delOutfitView(o, v, vw)}>🗑</button>
                        </div>
                      </div>
                    ) : (
                      <div key={vw} style={{ background: '#0b0e13', border: '1px dashed #2a2f3a', borderRadius: 6, padding: 4,
                                             display: 'grid', alignContent: 'center', justifyItems: 'center', gap: 6, minHeight: 120 }}>
                        <span style={{ ...hint, opacity: 0.8 }}>{vw} &mdash; missing</span>
                        <button style={{ ...btnGhost, padding: '2px 8px', fontSize: 11 }}
                                title={`Render the ${vw} view of this variant`}
                                disabled={oBusy || jobs.outfit?.status === 'running'}
                                onClick={() => void regenOutfit(o, v, [vw])}>＋ render</button>
                      </div>
                    )))}
                  </div>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {/* ── column 3: poses + generate ── */}
      <div style={{ ...box, display: 'grid', gap: 10, alignContent: 'start', position: 'sticky', top: 12, maxHeight: 'calc(100vh - 24px)', overflowY: 'auto' }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <h3 style={{ margin: 0, fontSize: 15 }}>🕺 Pose → 🚀 Generate</h3>
          <div style={{ flex: 1 }} />
          <button style={btnSm} onClick={() => void loadPoses()}>Refresh</button>
        </div>
        <p style={{ ...hint, margin: 0 }} title="Batches are threaded across every klein-capable worker">
          {workers.length
            ? <>workers: {workers.map((w) => (
                <span key={w.url} style={{ color: w.healthy ? (w.klein ? '#5ee08a' : '#cbd2dc') : '#ff8a8a', marginRight: 8 }}>
                  {w.url}{w.klein ? ' 🧪' : ''}{w.healthy ? '' : ' (down)'}{typeof w.in_flight === 'number' && w.in_flight > 0 ? ` ·${w.in_flight}` : ''}
                </span>
              ))}</>
            : 'workers: none detected'}
        </p>
        <button style={{ ...btn, background: '#334155' }} onClick={() => setLibOpen(true)}>
          🕺 Open Pose Library{poses.length ? ` (${poses.length} poses · ${cats.length} sets)` : ''}
        </button>
        {selectedSet && (
          <div style={{ border: '1px solid #3b82f6', borderRadius: 8, padding: 8, display: 'flex', gap: 8, alignItems: 'center' }}>
            <b style={{ fontSize: 12, color: '#9cc2ff' }}>📦 Set: {selectedSet}</b>
            <span style={hint}>
              {poses.filter((p) => (p.set || p.category) === selectedSet && p.has_image !== false).length} rendered poses — 🚀 runs the whole set (1 image per pose)
            </span>
            <div style={{ flex: 1 }} />
            <button style={btnSm} onClick={() => setSelectedSet('')}>✖</button>
          </div>
        )}
        {selectedTags.length > 0 && (
          <div style={{ border: '1px solid #a855f7', borderRadius: 8, padding: 8, display: 'flex', gap: 8, alignItems: 'center' }}>
            <b style={{ fontSize: 12, color: '#d8b4fe' }}>🏷 Tags: {selectedTags.join(' + ')}</b>
            <span style={hint}>
              {poses.filter((p) => p.has_image !== false && (p.tags || []).some((t) => selectedTags.includes(t))).length} rendered poses across all sets — 1 image per pose
            </span>
            <div style={{ flex: 1 }} />
            <button style={btnSm} onClick={() => setSelectedTags([])}>✖</button>
          </div>
        )}
        {jobs.set?.status && jobs.set.status !== 'done' && (
          <p style={jobs.set.status === 'error' ? errTxt : hint}>
            📦 set run: {jobs.set.status} {jobs.set.detail || ''}
            {jobs.set.tasks ? '  ·  ' + Object.entries(jobs.set.tasks).filter(([, t]) => t.status === 'running')
              .map(([k, t]) => `${poses.find((p) => p.id === k)?.name || k}${t.worker ? ` @ ${t.worker}` : ''} ⏳`).join(' · ') : ''}
            {jobs.set.error ? ` — ${jobs.set.error}` : ''}
          </p>
        )}
        {libOpen && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', zIndex: 9990, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}
             onClick={() => setLibOpen(false)}>
        <div style={{ ...box, width: 'min(1000px, 94vw)', maxHeight: '92vh', overflowY: 'auto', display: 'grid', gap: 10, alignContent: 'start' }}
             onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <h3 style={{ margin: 0, fontSize: 15 }}>🕺 Pose Library <span style={hint}>— shared across all characters</span></h3>
          <div style={{ flex: 1 }} />
          <button style={btnSm} onClick={() => setLibOpen(false)}>✖ Close</button>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '210px 1fr', gap: 12, alignItems: 'start', minHeight: 320 }}>
          <div style={{ display: 'grid', gap: 6, alignContent: 'start' }}>
            <div style={{ display: 'flex', gap: 4 }}>
              <input style={{ ...input, fontSize: 12 }} placeholder="new set name" value={newSetName}
                     onChange={(e) => setNewSetName(e.target.value)}
                     onKeyDown={(e) => { if (e.key === 'Enter') void createSet(); }} />
              <button style={btnSm} disabled={!newSetName.trim()} onClick={() => void createSet()}>➕</button>
            </div>
            <button style={{ ...chip(!catFilter), textAlign: 'left' }} onClick={() => setCatFilter('')}>
              🌐 All poses ({poses.length})
            </button>
            {setsInfo.map((si) => (
              <button key={si.name} style={{ ...chip(catFilter === si.name), textAlign: 'left' }}
                      onClick={() => setCatFilter(si.name)}>
                📦 {si.name} <span style={{ opacity: 0.7 }}>({si.rendered}/{si.count})</span>
              </button>
            ))}
            {!setsInfo.length && <p style={hint}>No sets yet — create one above.</p>}
          </div>
          <div style={{ display: 'grid', gap: 10, alignContent: 'start', minWidth: 0 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <b style={{ fontSize: 14, color: '#e6e9ee' }}>{catFilter ? `📦 ${catFilter}` : '🌐 All poses'}</b>
          <div style={{ flex: 1 }} />
          {catFilter && (
            <>
              <button style={{ ...btn, padding: '5px 10px' }}
                      onClick={() => { setSelectedSet(catFilter); setSelectedTags([]); setPoseId(''); setLibOpen(false); }}>
                ▶ Use set “{catFilter}”
              </button>
              <button style={btnSm} onClick={() => void renameSet()}>✏️ rename</button>
              <button style={btnSm} disabled={poseBusy} title="Remove poses in this set that have no rendered image"
                      onClick={() => void purgeImageless()}>🧹 purge image-less</button>
              <button style={{ ...btnSm, color: '#ff8a8a' }} onClick={() => void deleteSet()}>🗑 delete set</button>
            </>
          )}
        </div>
        {allTags.length > 0 && (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
            <span style={hint}>tags:</span>
            {allTags.map((t) => (
              <button key={t} style={chip(tagsSel.includes(t))}
                      onClick={() => setTagsSel((ts) => ts.includes(t) ? ts.filter((x) => x !== t) : [...ts, t])}>
                {t}
              </button>
            ))}
            {tagsSel.length > 0 && (
              <>
                <button style={btnSm} onClick={() => setTagsSel([])}>clear</button>
                <button style={{ ...btn, padding: '4px 10px' }}
                        title="Generate every rendered pose carrying any of these tags — across ALL sets"
                        onClick={() => { setSelectedTags(tagsSel); setSelectedSet(''); setPoseId(''); setLibOpen(false); }}>
                  ▶ Use {poses.filter((p) => p.has_image !== false && (p.tags || []).some((t) => tagsSel.includes(t))).length} tagged poses
                </button>
              </>
            )}
          </div>
        )}
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          {!catFilter && <span style={hint}>open a set on the left to import or create poses ·</span>}
          {catFilter && <button style={btnSm} onClick={() => setNewPoseOpen((o) => !o)}>➕ New (prompt)</button>}
          {catFilter && <label style={{ ...btnSm, display: 'inline-block' }}>
            ⬆ Upload (photo / openpose / depth)
            <input type="file" accept="image/*" style={{ display: 'none' }}
                   onChange={(e) => { const f = e.target.files?.[0]; if (f) void uploadPose(f); e.target.value = ''; }} />
          </label>}
          <button style={btnSm} disabled={seedRun?.status === 'running'} onClick={seedDefaults}
                  title="Generates the built-in starter poses into a 'Defaults' set">
            {seedRun?.status === 'running' ? `✨ ${seedRun.done}/${seedRun.total}…` : '✨ Defaults'}
          </button>
          {catFilter && <label style={{ ...btnSm, display: 'inline-block' }}
                 title={'Batch import pose definitions INTO this set.\nJSON: [{"name":"Hero landing","prompt":"crouched superhero landing...","category":"Action"}]\nRow category/tags become TAGS on each pose — the SET is this one.\nCSV headers: name,prompt[,category][,tags][,raw]'}>
            📥 Import poses (.json/.csv)
            <input type="file" accept=".json,.csv,application/json,text/csv" style={{ display: 'none' }}
                   onChange={(e) => { const f = e.target.files?.[0]; if (f) void importPoses(f); e.target.value = ''; }} />
          </label>}
          {catFilter && <label style={{ ...btnSm, display: 'inline-block' }}
                 title={'Import a pose PACK into this set:\n• .zip of control images — openpose skeletons, depth maps, DWpose renders\n• openpose keypoint .json files — rendered to skeleton images automatically\nPose names come from filenames.'}>
            📦 Import pack (.zip/openpose .json)
            <input type="file" accept=".zip,.json,application/zip,application/json" style={{ display: 'none' }}
                   onChange={(e) => { const f = e.target.files?.[0]; if (f) void importPack(f); e.target.value = ''; }} />
          </label>}
          <button style={btnSm}
                  title="Download instructions to hand any LLM: tell it what poses you want, paste this doc, get a ready-to-import file back"
                  onClick={() => {
                    const blob = new Blob([POSE_IMPORT_LLM_DOC], { type: 'text/markdown' });
                    const a = document.createElement('a');
                    a.href = URL.createObjectURL(blob);
                    a.download = 'pose_set_llm_instructions.md';
                    a.click();
                    URL.revokeObjectURL(a.href);
                  }}>📄 LLM guide</button>
          {(() => {
            const missing = poses.filter((p) => p.prompt && p.has_image === false
              && (!catFilter || (p.set || p.category) === catFilter)).length;
            return missing > 0 ? (
              <button style={{ ...btnSm, borderColor: '#3b82f6', color: '#9cc2ff' }}
                      disabled={batchRun?.status === 'running'} onClick={generateMissing}>
                {batchRun?.status === 'running' ? `🎨 ${batchRun.done}/${batchRun.total}…` : `🎨 Generate missing (${missing})`}
              </button>
            ) : null;
          })()}
        </div>
        {batchRun?.status === 'running' && batchRun.tasks && (
          <p style={{ ...hint, margin: 0 }}>
            {Object.values(batchRun.tasks).filter((t) => t.status === 'running')
              .map((t) => `${t.name}${t.worker ? ` @ ${t.worker}` : ''} ⏳`).join('  ·  ') || 'queueing…'}
          </p>
        )}
        {batchRun && batchRun.status !== 'running' && !!batchRun.errors?.length && (
          <p style={errTxt}>batch: {batchRun.errors.slice(0, 3).join('; ')}</p>
        )}
        {newPoseOpen && (
          <div style={{ border: '1px solid #2a2f3a', borderRadius: 8, padding: 8, display: 'grid', gap: 6 }}>
            <input style={input} value={npName} onChange={(e) => setNpName(e.target.value)} placeholder="pose name *" />
            <textarea style={{ ...input, minHeight: 48 }} value={npDesc} onChange={(e) => setNpDesc(e.target.value)}
                      placeholder="pose description (mannequin style added automatically)" />
            <button style={btn} disabled={poseBusy || !npName.trim() || !npDesc.trim()} onClick={createPose}>
              {poseBusy ? 'Generating…' : 'Generate pose image'}
            </button>
          </div>
        )}
        {poseMsg && <p style={poseMsg.startsWith('✓') ? okTxt : errTxt}>{poseMsg}</p>}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(84px, 1fr))', gap: 6, maxHeight: 260, overflowY: 'auto' }}>
          {shownPoses.map((p) => (
            <div key={p.id}
                 style={{ border: `2px solid ${poseId === p.id ? '#3b82f6' : '#2a2f3a'}`, borderRadius: 8, padding: 3,
                          cursor: p.has_image === false ? 'default' : 'pointer', background: '#0e1116',
                          opacity: p.has_image === false ? 0.75 : 1 }}
                 onClick={() => { if (p.has_image !== false) { setPoseId(p.id); setSelectedSet(''); setSelectedTags([]); setLibOpen(false); } }}>
              {p.has_image === false
                ? <div style={{ width: '100%', aspectRatio: '832/1216', borderRadius: 5, border: '1px dashed #3a4150',
                                display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, color: '#8d97a5' }}>
                    no image yet
                  </div>
                : <img src={p.url} alt={p.name} style={{ width: '100%', borderRadius: 5, display: 'block' }} />}
              <div style={{ fontSize: 10, color: '#cbd2dc', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name}</div>
              <button style={{ ...btnSm, padding: '1px 5px', fontSize: 10 }}
                      onClick={(e) => { e.stopPropagation(); setEditPose(p); setEditPrompt(p.prompt); }}>
                {p.prompt ? '✏️' : 'ℹ️'}
              </button>
            </div>
          ))}
          {!shownPoses.length && <p style={hint}>No poses — hit ✨ Defaults.</p>}
        </div>
        {editPose && (
          <div style={{ border: '1px solid #3b82f6', borderRadius: 8, padding: 8, display: 'grid', gap: 6 }}>
            <b style={{ fontSize: 12, color: '#e6e9ee' }}>✏️ {editPose.name} <span style={hint}>({editPose.source})</span></b>
            {editPose.prompt || editPose.source === 'generated' ? (
              <>
                <textarea style={{ ...input, minHeight: 70 }} value={editPrompt} onChange={(e) => setEditPrompt(e.target.value)} />
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  <button style={btnSm} disabled={poseBusy} onClick={() => void savePose(false)}>💾 Save</button>
                  <button style={btnGhost} disabled={poseBusy} onClick={() => void savePose(true)}>🔁 Save + regenerate</button>
                  <div style={{ flex: 1 }} />
                  <button style={{ ...btnSm, color: '#ff8a8a' }} disabled={poseBusy} onClick={() => void delPose(editPose.id)}>🗑</button>
                  <button style={btnSm} onClick={() => setEditPose(null)}>Close</button>
                </div>
              </>
            ) : (
              <div style={{ display: 'flex', gap: 6 }}>
                <span style={hint}>Uploaded pose — no prompt.</span>
                <div style={{ flex: 1 }} />
                <button style={{ ...btnSm, color: '#ff8a8a' }} disabled={poseBusy} onClick={() => void delPose(editPose.id)}>🗑</button>
                <button style={btnSm} onClick={() => setEditPose(null)}>Close</button>
              </div>
            )}
          </div>
        )}
          </div>
        </div>
        </div>
        </div>
        )}

        <div style={{ borderTop: '1px solid #2a2f3a', paddingTop: 10, display: 'grid', gap: 8 }}>
          <div style={{ display: 'flex', gap: 8 }}>
            <div style={{ flex: 1 }}>
              <label style={label}>Image 1 — base</label>
              {cur?.active_base_url
                ? <img src={`${cur.active_base_url}?t=${cur.updated_at || ''}`} alt="base"
                       style={{ width: '100%', borderRadius: 6, border: '1px solid #2a2f3a', cursor: 'zoom-in' }}
                       onClick={() => setLightbox(`${cur.active_base_url}?t=${cur.updated_at || ''}`)} />
                : <p style={{ ...hint, margin: 0 }}>no base</p>}
            </div>
            <div style={{ flex: 1 }}>
              <label style={label}>Image 2 — {selectedSet ? 'pose SET' : selectedTags.length ? 'tagged poses' : 'pose'}</label>
              {selectedSet || selectedTags.length
                ? <div style={{ border: '1px dashed #3b82f6', borderRadius: 6, padding: 10, textAlign: 'center' }}>
                    <div style={{ fontSize: 22 }}>{selectedSet ? '📦' : '🏷'}</div>
                    <div style={{ fontSize: 12, color: '#9cc2ff' }}>{selectedSet || selectedTags.join(' + ')}</div>
                    <div style={hint}>{(selectedSet
                      ? poses.filter((p) => (p.set || p.category) === selectedSet && p.has_image !== false)
                      : poses.filter((p) => p.has_image !== false && (p.tags || []).some((t) => selectedTags.includes(t)))
                    ).length} poses · 1 image each</div>
                  </div>
                : selPose ? <img src={selPose.url} alt="pose"
                              style={{ width: '100%', borderRadius: 6, border: '1px solid #2a2f3a', cursor: 'zoom-in' }}
                              onClick={() => setLightbox(selPose.url)} />
                : <p style={{ ...hint, margin: 0 }}>pick a pose or set in the library</p>}
            </div>
          </div>
          <div>
            <label style={label}>Extra prompt (outfit, scene, lighting — optional)</label>
            <textarea style={{ ...input, minHeight: 48 }} value={extra} onChange={(e) => setExtra(e.target.value)}
                      placeholder="wearing the red jacket, on a rainy street at night" />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            <div><label style={label}>{selectedSet || selectedTags.length ? 'Images (1 per pose)' : 'Images'}</label>
              <select style={input} value={String(count)} disabled={!!selectedSet || selectedTags.length > 0}
                      onChange={(e) => setCount(Number(e.target.value))}>
                {[1, 2, 3, 4, 6, 8].map((n) => <option key={n} value={n}>{n}</option>)}
              </select></div>
            <div><label style={label}>Seed (blank = random)</label>
              <input style={input} value={seed} onChange={(e) => setSeed(e.target.value)} placeholder="random" /></div>
          </div>
          <button style={{ ...btn, opacity: cur?.has_base && (poseId || selectedSet || selectedTags.length) ? 1 : 0.5 }}
                  disabled={!cur?.has_base || (!poseId && !selectedSet && !selectedTags.length) || jobs.set?.status === 'running'}
                  onClick={doGenerate}>
            {selectedSet ? `🚀 Generate SET “${selectedSet}”`
              : selectedTags.length ? `🚀 Generate ${selectedTags.join('+')} poses` : '🚀 Generate'}
          </button>
          {genErr && <p style={errTxt}>{genErr}</p>}
          {gen && (
            <div style={{ display: 'grid', gap: 8 }}>
              <p style={{ ...hint, margin: 0 }}>
                {gen.status === 'running' ? `⏳ ${gen.done}/${gen.total}…`
                  : gen.status === 'done' ? `✓ done (${gen.images.length})` : `⚠ ${gen.error || 'error'}`}
                {gen.workers?.length ? `  ·  threaded across ${gen.workers.length} worker${gen.workers.length > 1 ? 's' : ''}` : ''}
              </p>
              {gen.tasks && gen.status === 'running' && (
                <p style={{ ...hint, margin: 0 }}>
                  {Object.entries(gen.tasks).map(([k, t]) =>
                    `img ${Number(k) + 1}${t.worker ? ` @ ${t.worker}` : ''} ${taskIcon(t.status)}`).join('  ·  ')}
                </p>
              )}
              {!!gen.refs.length && (
                <div style={{ display: 'flex', gap: 6 }}>
                  {gen.refs.map((r) => (
                    <img key={r.name} src={r.url} alt={r.name} title={r.name}
                         style={{ width: 50, borderRadius: 4, border: '1px solid #2a2f3a', cursor: 'zoom-in' }}
                         onClick={() => setLightbox(r.url)} />
                  ))}
                  <span style={{ ...hint, alignSelf: 'center' }}>← exact refs used</span>
                </div>
              )}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                {gen.images.map((im) => (
                  <img key={im.id} src={im.url} alt={im.id}
                       style={{ width: '100%', borderRadius: 6, border: '1px solid #2a2f3a', cursor: 'zoom-in' }}
                       onClick={() => setLightbox(im.url)} />
                ))}
              </div>
            </div>
          )}
        </div>

        <div style={{ borderTop: '1px solid #2a2f3a', paddingTop: 10, display: 'grid', gap: 8 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <b style={{ fontSize: 13, color: '#e6e9ee' }}>📚 Saved results</b>
            <button style={chip(gensPoseOnly)} onClick={() => setGensPoseOnly((o) => !o)}
                    title="Only show batches made with the currently selected pose">
              {gensPoseOnly ? 'this pose ✓' : 'this pose'}
            </button>
            <div style={{ flex: 1 }} />
            <button style={btnSm} onClick={() => void loadGens()}>Refresh</button>
          </div>
          {(() => {
            const shown = gens.filter((g) => g.gen_id !== gen?.gen_id)
              .filter((g) => !gensPoseOnly || !poseId || g.pose_id === poseId);
            if (!shown.length) return <p style={{ ...hint, margin: 0 }}>No saved batches{gensPoseOnly ? ' for this pose' : ''} yet — every 🚀 run is kept here, linked to its pose.</p>;
            return shown.map((g) => (
              <div key={g.gen_id} style={{ border: '1px solid #2a2f3a', borderRadius: 8, padding: 6, display: 'grid', gap: 4 }}>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                  <span style={{ fontSize: 11, color: '#cbd2dc' }}>🕺 {g.pose || 'pose'}</span>
                  <span style={{ ...hint, fontSize: 10 }}>{(g.created_at || '').slice(0, 16).replace('T', ' ')}</span>
                  {g.status !== 'done' && <span style={g.status === 'error' ? errTxt : hint}>{g.status}</span>}
                  <div style={{ flex: 1 }} />
                  {g.pose_id && (
                    <button style={{ ...btnSm, padding: '1px 6px', fontSize: 10 }} title="Select this batch's pose"
                            onClick={() => setPoseId(g.pose_id!)}>use pose</button>
                  )}
                  <button style={{ ...btnSm, padding: '1px 6px', fontSize: 10, color: '#ff8a8a' }}
                          onClick={async () => {
                            try { await j(await fetch(`${BASE}/gen/${g.gen_id}/delete`, { method: 'POST' })); await loadGens(); }
                            catch (e) { setGenErr((e as Error).message); }
                          }}>🗑</button>
                </div>
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                  {g.refs.map((r) => (
                    <img key={r.name} src={r.url} alt={r.name} title={`ref: ${r.name}`}
                         style={{ width: 34, borderRadius: 3, border: '1px dashed #2a2f3a', cursor: 'zoom-in', opacity: 0.75 }}
                         onClick={() => setLightbox(r.url)} />
                  ))}
                  {g.images.map((im) => (
                    <img key={im.id} src={im.url} alt={im.id} title={`seed ${im.seed ?? '?'}`}
                         style={{ width: 64, borderRadius: 4, border: '1px solid #2a2f3a', cursor: 'zoom-in' }}
                         onClick={() => setLightbox(im.url)} />
                  ))}
                </div>
              </div>
            ));
          })()}
        </div>
      </div>
      {lightbox && <ImageLightbox url={lightbox} onClose={() => setLightbox('')} />}

      {/* ── 🎨 COSTUME STUDIO (v1.276.27) ─────────────────────────────────
          Design a costume as an IMAGE before dressing anyone in it. Renders on
          a neutral matte-grey mannequin (Lorenzo's call): a dress form carries
          the garment and nothing else, where a person would carry a face, a
          body and a facing into whatever it is later used as a reference for.
          Costumes live in a SHARED library, so one design dresses a cast. */}
      {csOpen && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(6,8,12,0.82)', zIndex: 60,
                      display: 'grid', placeItems: 'center', padding: 16 }}
             onClick={() => setCsOpen(false)}>
          <div style={{ ...box, width: 'min(1080px, 96vw)', maxHeight: '92vh', overflowY: 'auto',
                        display: 'grid', gap: 10 }}
               onClick={(e) => e.stopPropagation()}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <h3 style={{ margin: 0, fontSize: 16 }}>🎨 Costume Studio</h3>
              <span style={hint}>
                Rendered on a plain mannequin so the reference carries the garment and nothing
                else. Pick one and it becomes this outfit's costume reference.
              </span>
              <div style={{ flex: 1 }} />
              <button style={btnGhost} onClick={() => setCsOpen(false)}>✕ Close</button>
            </div>

            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <label style={label}>Model</label>
              <select style={{ ...input, width: 'auto' }} value={csModel}
                      onChange={(e) => setCsModel(e.target.value)}>
                {(csModels.length ? csModels : [{ key: 'krea2', label: 'Krea 2 Turbo', note: '' }])
                  .map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
              </select>
              <label style={label}>Costume name</label>
              <input style={{ ...input, width: 190 }} value={csName}
                     placeholder="e.g. Desert scavenger"
                     title="What these designs are called in the library. Defaults to the outfit name; without one they were all saved as 'untitled costume'."
                     onChange={(e) => setCsName(e.target.value)} />
              <label style={label}>Cut for</label>
              <select style={{ ...input, width: 'auto' }} value={csWearer}
                      title="Garments are cut differently, and a swimsuit on the wrong form is simply wrong. Only the mannequin's PROPORTIONS change — it stays blank-headed and matte grey."
                      onChange={(e) => setCsWearer(e.target.value)}>
                <option value="woman">a woman</option>
                <option value="man">a man</option>
                <option value="unisex">unisex</option>
              </select>
              {csRefs.length > 0 && csRefCap === 0 && (
                <span style={{ ...hint, color: '#ffcf8a' }}>
                  ⚠ {csModels.find((m) => m.key === csModel)?.label} is text-only — your
                  references will be ignored. Pick Klein or Qwen-Image-Edit to use them.
                </span>
              )}
              <label style={label}>How many</label>
              <select style={{ ...input, width: 'auto' }} value={csCount}
                      onChange={(e) => setCsCount(Number(e.target.value))}>
                {[1, 2, 3, 4, 6, 8].map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
              <div style={{ flex: 1 }} />
              <button style={btn} disabled={csBusy || csJob?.status === 'running'}
                      onClick={() => void designCostume()}>
                {csJob?.status === 'running'
                  ? `⏳ ${csJob.done || 0}/${csJob.total || csCount}`
                  : '🎨 Generate designs'}
              </button>
            </div>

            {/* 🖼 v1.276.33 — only for models that can actually use a reference.
                Krea 2 and Z-Image are pure text-to-image, so showing an upload
                box for them would just be a lie. */}
            {csRefCap > 0 && (
              <div style={{ background: '#0e1116', border: '1px solid #22415c', borderRadius: 8,
                            padding: 10, display: 'grid', gap: 6 }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 12, color: '#8fd6ff', fontWeight: 600 }}>
                    🖼 Reference images
                  </span>
                  <span style={hint}>
                    {csModels.find((m) => m.key === csModel)?.label} can edit from up to {csRefCap}.
                    Photograph a real garment and the design copies its cut, colour and
                    fastenings instead of guessing them from words.
                  </span>
                </div>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                  <input ref={csRefInput} type="file" accept="image/*" multiple
                         style={{ display: 'none' }}
                         onChange={async (e) => {
                           const files = Array.from(e.target.files || []);
                           e.target.value = '';
                           for (const f of files) {
                             if (csRefs.length >= csRefCap) break;
                             try {
                               const fd = new FormData();
                               fd.append('file', f);
                               const r = await j<{ id: string; url: string; name: string }>(
                                 await fetch(`${BASE_COS}/refs`, { method: 'POST', body: fd }));
                               setCsRefs((cur) => (cur.length >= csRefCap ? cur : [...cur, r]));
                             } catch (err) { setMsg((err as Error).message); }
                           }
                         }} />
                  <button style={btnGhost} disabled={csRefs.length >= csRefCap}
                          onClick={() => csRefInput.current?.click()}>
                    ⬆ Add reference ({csRefs.length}/{csRefCap})
                  </button>
                  {csRefs.map((r) => (
                    <div key={r.id} style={{ position: 'relative' }}>
                      <img src={r.url} alt={r.name} title={r.name}
                           onClick={() => setLightbox(r.url)}
                           style={{ height: 60, borderRadius: 5, border: '1px solid #2a2f3a',
                                    cursor: 'zoom-in', display: 'block' }} />
                      <button style={{ position: 'absolute', top: -6, right: -6, fontSize: 10,
                                       background: '#1a1d24', color: '#ff8a8a',
                                       border: '1px solid #4a2130', borderRadius: 10,
                                       cursor: 'pointer', padding: '0 5px' }}
                              title="Remove this reference"
                              onClick={() => setCsRefs((cur) => cur.filter((x) => x.id !== r.id))}>✕</button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div>
              <label style={label}>
                Custom prompt <span style={hint}>
                  (blank = built from the slots you filled in. This describes the GARMENTS; the
                  mannequin framing is added for you.)
                </span>
              </label>
              <textarea style={{ ...input, minHeight: 60, fontFamily: 'inherit' }} value={csPrompt}
                        placeholder="Describe the garments — e.g. 'a long dusty canvas trench coat with frayed edges, wrapped leather boots'"
                        onChange={(e) => setCsPrompt(e.target.value)} />
              <label style={{ ...hint, display: 'flex', alignItems: 'center', gap: 5, cursor: 'pointer', marginTop: 4 }}
                     title="Send your text verbatim with NO mannequin framing. You will get whatever you describe, including a person, a background, or a cropped shot — which is exactly what a costume reference should not carry.">
                <input type="checkbox" checked={csRaw} onChange={(e) => setCsRaw(e.target.checked)} />
                raw prompt (no mannequin framing — advanced)
              </label>
            </div>

            {csJob?.error && <p style={errTxt}>{csJob.error}</p>}
            {!!csJob?.workers?.length && (
              <p style={{ ...hint, margin: 0 }}>
                spread across {csJob.workers.length} worker(s): {csJob.workers.join(' · ')}
              </p>
            )}
            {/* v1.276.30 — per-image progress, so a run can be watched */}
            {!!csJob?.items?.length && csJob?.status !== 'idle' && (
              <div style={{ display: 'grid', gap: 2 }}>
                {csJob.items.map((it: any) => (
                  <div key={it.i} style={{ fontSize: 11, color:
                    it.status === 'done' ? '#8fe0ac'
                      : it.status === 'error' ? '#ff8a8a'
                        : it.status === 'running' ? '#8fd6ff' : '#9aa4b2' }}>
                    {it.status === 'done' ? '✅' : it.status === 'error' ? '❌'
                      : it.status === 'running' ? '⏳' : '•'} image {it.i}/{csJob.total}
                    {it.worker ? ` — ${it.worker}` : ''}
                    {it.status === 'running' ? ' — rendering…' : ''}
                    {it.error ? ` — ${it.error}` : ''}
                  </div>
                ))}
              </div>
            )}

            {/* 🧪 STAGING — nothing reaches the library until it is approved */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 13, color: '#ffcf8a', fontWeight: 600 }}>🧪 Candidates</span>
              <span style={hint}>
                {csCand.length} awaiting approval — these are NOT in the library yet
              </span>
              <div style={{ flex: 1 }} />
              {!!csCand.length && (
                <button style={{ ...btnSm, color: '#ff8a8a', borderColor: '#4a2130' }}
                        title="Throw away every unapproved candidate"
                        onClick={async () => {
                          if (!window.confirm(`Discard all ${csCand.length} unapproved candidate(s)?`)) return;
                          try {
                            await j(await fetch(`${BASE_COS}/candidates/clear`, { method: 'POST' }));
                            await loadCostumes();
                          } catch (e) { setMsg((e as Error).message); }
                        }}>🗑 discard all</button>
              )}
            </div>
            {!csCand.length && <p style={hint}>No candidates — generate some designs above.</p>}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(170px, 1fr))', gap: 8 }}>
              {csCand.map((cst) => (
                <div key={cst.id} style={{ background: '#0e1116', border: '1px dashed #5c4a22',
                                           borderRadius: 8, padding: 5 }}>
                  <img src={cst.url} alt={cst.name} onClick={() => setLightbox(cst.url)}
                       style={{ width: '100%', borderRadius: 5, display: 'block', cursor: 'zoom-in' }} />
                  <div style={{ ...hint, margin: '3px 0' }}>{cst.name} · {cst.wearer || 'unisex'}</div>
                  <div style={{ display: 'flex', gap: 4 }}>
                    <button style={{ ...btnSm, flex: 1, fontSize: 11, color: '#8fe0ac', borderColor: '#2f6d46' }}
                            title="Approve into the costume library, where it can be used on any character"
                            onClick={async () => {
                              const nm = window.prompt('Name this costume for the library:', cst.name || '');
                              if (nm === null) return;
                              try {
                                await j(await fetch(`${BASE_COS}/${cst.id}/approve`, {
                                  method: 'POST', headers: { 'Content-Type': 'application/json' },
                                  body: JSON.stringify({ name: nm, approved: true }),
                                }));
                                await loadCostumes();
                              } catch (e) { setMsg((e as Error).message); }
                            }}>✅ Approve</button>
                    <button style={{ ...btnSm, fontSize: 11, color: '#ff8a8a', borderColor: '#4a2130' }}
                            onClick={async () => {
                              try {
                                await j(await fetch(`${BASE_COS}/${cst.id}/delete`, { method: 'POST' }));
                                await loadCostumes();
                              } catch (e) { setMsg((e as Error).message); }
                            }}>🗑</button>
                  </div>
                </div>
              ))}
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 13, color: '#cbd2dc', fontWeight: 600 }}>Costume library</span>
              <span style={hint}>{csLib.length} shown — shared across every character</span>
              <div style={{ flex: 1 }} />
              {/* 🔎 v1.276.35 — filter by cut and search, because a wardrobe
                  gets unusable fast once it fills up */}
              {[['', 'all'], ['woman', 'women'], ['man', 'men'], ['unisex', 'unisex']]
                .map(([key, lbl]) => (
                  <button key={key || 'all'} style={chip(csFilter === key)}
                          onClick={() => setCsFilter(key)}>
                    {lbl}{key && csCounts[key] != null ? ` (${csCounts[key]})` : ''}
                  </button>
                ))}
              <input style={{ ...input, width: 180 }} value={csQuery}
                     placeholder="search name, prompt, garments…"
                     onChange={(e) => setCsQuery(e.target.value)} />
              {(csFilter || csQuery) && (
                <button style={{ ...btnSm, fontSize: 11 }}
                        onClick={() => { setCsFilter(''); setCsQuery(''); }}>✕ clear</button>
              )}
            </div>
            {!csLib.length && <p style={hint}>Nothing yet — generate some designs above.</p>}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(170px, 1fr))', gap: 8 }}>
              {csLib.map((cst) => (
                <div key={cst.id} style={{ background: '#0e1116', border: '1px solid #2a2f3a',
                                           borderRadius: 8, padding: 5 }}>
                  <img src={cst.url} alt={cst.name}
                       title="Click to view large"
                       onClick={() => setLightbox(cst.url)}
                       style={{ width: '100%', borderRadius: 5, display: 'block', cursor: 'zoom-in' }} />
                  <div style={{ ...hint, margin: '3px 0' }}>
                    {cst.name} <span style={{ opacity: 0.7 }}>· {cst.wearer}</span>
                  </div>
                  {/* ℹ v1.276.35 — everything that made this image, so it can be
                      reused or copied elsewhere */}
                  {csInfo === cst.id && (
                    <div style={{ background: '#0b0e13', border: '1px solid #2a2f3a',
                                  borderRadius: 6, padding: 6, marginBottom: 4,
                                  fontSize: 10, color: '#9aa4b2', display: 'grid', gap: 4 }}>
                      <div><b style={{ color: '#cbd2dc' }}>model</b> {cst.model} · seed {cst.seed} · cut for {cst.wearer}</div>
                      {!!Object.keys(cst.slots || {}).length && (
                        <div>
                          <b style={{ color: '#cbd2dc' }}>garments</b>
                          {Object.entries(cst.slots).map(([k, v]) => (
                            <div key={k} style={{ marginLeft: 6 }}>· {k}: {String(v)}</div>
                          ))}
                        </div>
                      )}
                      {!!cst.ref_images?.length && (
                        <div>
                          <b style={{ color: '#cbd2dc' }}>built from</b>
                          <div style={{ display: 'flex', gap: 3, marginTop: 2 }}>
                            {cst.ref_images.map((r: any) => (
                              <img key={r.id} src={r.url} alt="source"
                                   onClick={() => setLightbox(r.url)}
                                   style={{ height: 44, borderRadius: 3, cursor: 'zoom-in',
                                            border: '1px solid #2a2f3a' }} />
                            ))}
                          </div>
                        </div>
                      )}
                      <div>
                        <b style={{ color: '#cbd2dc' }}>prompt</b>
                        <div style={{ maxHeight: 90, overflowY: 'auto', marginTop: 2,
                                      whiteSpace: 'pre-wrap' }}>{cst.prompt}</div>
                      </div>
                      <button style={{ ...btnSm, fontSize: 10, padding: '1px 6px' }}
                              onClick={() => { void navigator.clipboard?.writeText(cst.prompt || ''); setMsg('Prompt copied.'); }}>
                        📋 copy prompt
                      </button>
                    </div>
                  )}
                  <div style={{ display: 'flex', gap: 4, marginBottom: 4 }}>
                    <button style={{ ...btnSm, flex: 1, fontSize: 11 }}
                            title="Rename this costume"
                            onClick={async () => {
                              const nm = window.prompt('Rename this costume:', cst.name || '');
                              if (nm === null || !nm.trim()) return;
                              try {
                                await j(await fetch(`${BASE_COS}/${cst.id}/rename`, {
                                  method: 'POST', headers: { 'Content-Type': 'application/json' },
                                  body: JSON.stringify({ name: nm.trim() }),
                                }));
                                await loadCostumes();
                              } catch (e) { setMsg((e as Error).message); }
                            }}>✏️ rename</button>
                    <button style={{ ...btnSm, fontSize: 11 }}
                            title="Show the prompt, garments and reference images used to make this"
                            onClick={() => setCsInfo(csInfo === cst.id ? '' : cst.id)}>
                      {csInfo === cst.id ? 'ℹ hide' : 'ℹ info'}
                    </button>
                  </div>
                  <div style={{ display: 'flex', gap: 4 }}>
                    <button style={{ ...btnSm, flex: 1, fontSize: 11 }}
                            disabled={csBusy || !slug}
                            title="Attach this as the costume reference for the outfit you are editing, and rescan it into the slots"
                            onClick={() => void useCostume(cst.id)}>✅ Use this</button>
                    <button style={{ ...btnSm, fontSize: 11, color: '#ff8a8a', borderColor: '#4a2130' }}
                            title="Delete this design from the library"
                            onClick={async () => {
                              if (!window.confirm(`Delete "${cst.name}" from the costume library?`)) return;
                              try {
                                await j(await fetch(`${BASE_COS}/${cst.id}/delete`, { method: 'POST' }));
                                await loadCostumes();
                              } catch (e) { setMsg((e as Error).message); }
                            }}>🗑</button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
