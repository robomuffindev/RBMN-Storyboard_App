/**
 * Klein 2.0 — statue-reference posing (v1.200.0). Self-contained panel, mounted
 * as its own tab on the Klein create page; the classic Klein pipeline is
 * untouched.
 *
 * The loop:
 *   1. Pick a character. Its 3D mesh (textured "statue" when generated, else
 *      the untextured clay mesh) loads into a rotatable three.js viewer.
 *   2. Orbit to EXACTLY the angle the shot needs → 📸 "Use this angle" grabs a
 *      deterministic fixed-resolution snapshot = identity reference (image 1).
 *   3. Pick a pose from Pose Library 2.0 — plain IMAGES of poses. Generated
 *      poses carry their PROMPT (view/edit/regenerate right in the library);
 *      photos can be uploaded too. That's the pose reference (image 2).
 *   4. 🚀 Generate: Klein multi-ref — "the person from image 1 in the pose
 *      from image 2" (+ optional photoreal front base as image 3 for likeness).
 *
 * Requires `three` (npm i) for the viewer. Backend: /api/klein2/*.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';

import useLightbox from '../shared/useLightbox';
const BASE = '/api/klein2';

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
interface CharT {
  name: string; character_id: string; hero_url?: string | null;
  has_mesh: boolean; has_rig: boolean; has_statue: boolean;
  statue?: { created_at?: string; host?: string } | null; views: number;
}
interface PoseT {
  id: string; name: string; category: string; prompt: string;
  source: string; seed?: number | null; url: string;
}
interface GenStatusT {
  gen_id: string; status: 'running' | 'done' | 'error'; done: number; total: number;
  character?: string; pose?: string; prompt: string;
  images: Array<{ id: string; url: string; seed?: number }>;
  refs: Array<{ name: string; url: string }>;
  error?: string | null;
}
interface StatueRunT { status?: string; phase?: string; error?: string | null; host?: string | null }

// Snapshot render size — matches the default generation canvas so the identity
// ref has the same framing Klein renders at.
const SNAP_W = 832;
const SNAP_H = 1216;

// ── 3D statue viewer ────────────────────────────────────────────────────────
function StatueViewer({ character, statueStamp, onSnapshot, onMeshState }: {
  character: string;
  statueStamp: string;                       // changes → reload the GLB
  onSnapshot: (dataUrl: string) => void;
  onMeshState: (s: { loaded: boolean; error?: string }) => void;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const stateRef = useRef<{
    renderer?: any; scene?: any; camera?: any; controls?: any; raf?: number;
    disposed?: boolean; three?: any;
  }>({});
  const [status, setStatus] = useState('');

  useEffect(() => {
    const st = stateRef.current;
    st.disposed = false;
    let cancelled = false;
    (async () => {
      if (!character) return;
      setStatus('Loading 3D engine…');
      let THREE: any, GLTFLoader: any, OrbitControls: any, RoomEnvironment: any;
      try {
        THREE = await import('three');
        ({ GLTFLoader } = await import('three/examples/jsm/loaders/GLTFLoader.js'));
        ({ OrbitControls } = await import('three/examples/jsm/controls/OrbitControls.js'));
        ({ RoomEnvironment } = await import('three/examples/jsm/environments/RoomEnvironment.js'));
      } catch (e) {
        const msg = '3D viewer needs the "three" package — run `npm install` in frontend/ and reload.';
        setStatus(msg); onMeshState({ loaded: false, error: msg });
        return;
      }
      if (cancelled) return;
      const host = hostRef.current;
      if (!host) return;
      host.innerHTML = '';
      const w = host.clientWidth || 480;
      const h = Math.round(w * 1.25);
      const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: false });
      renderer.setSize(w, h, false);
      // v1.200.11: hi-DPI rendering (pixelRatio 1 looked blurry/"PS1" on retina)
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      // v1.200.11: PBR GLBs render DARK without an environment — add a neutral
      // IBL room + filmic tone mapping (this was the "very dark statue" cause)
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      renderer.toneMappingExposure = 1.15;
      host.appendChild(renderer.domElement);
      renderer.domElement.style.width = '100%';
      renderer.domElement.style.height = 'auto';
      renderer.domElement.style.borderRadius = '8px';

      const scene = new THREE.Scene();
      scene.background = new THREE.Color(0x14171e);
      try {
        const pmrem = new THREE.PMREMGenerator(renderer);
        scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
      } catch { /* env optional — lights below still apply */ }
      // soft supporting lights on top of the IBL
      scene.add(new THREE.HemisphereLight(0xffffff, 0x666677, 0.5));
      const key = new THREE.DirectionalLight(0xffffff, 0.9); key.position.set(2, 4, 3); scene.add(key);
      const fill = new THREE.DirectionalLight(0xffffff, 0.35); fill.position.set(-3, 2, -2); scene.add(fill);

      const camera = new THREE.PerspectiveCamera(35, w / h, 0.01, 100);
      const controls = new OrbitControls(camera, renderer.domElement);
      controls.enableDamping = true;

      Object.assign(st, { renderer, scene, camera, controls, three: THREE });

      setStatus('Loading mesh…');
      const url = `${BASE}/statue/${encodeURIComponent(character)}/glb?cb=${statueStamp}`;
      new GLTFLoader().load(url, (gltf: any) => {
        if (st.disposed) return;
        const obj = gltf.scene || gltf.scenes?.[0];
        if (!obj) { setStatus('Empty GLB'); onMeshState({ loaded: false, error: 'empty glb' }); return; }
        // untextured meshes come in with no material worth keeping — give them clay
        obj.traverse((n: any) => {
          if (n.isMesh && (!n.material || (!n.material.map && n.material.color))) {
            if (!n.material?.map) {
              n.material = new THREE.MeshStandardMaterial({ color: 0xb9bec9, roughness: 0.85, metalness: 0.0 });
            }
          }
        });
        scene.add(obj);
        // frame it
        const bb = new THREE.Box3().setFromObject(obj);
        const size = bb.getSize(new THREE.Vector3());
        const center = bb.getCenter(new THREE.Vector3());
        const maxDim = Math.max(size.x, size.y, size.z) || 1;
        controls.target.copy(center);
        camera.position.set(center.x, center.y + size.y * 0.05, center.z + maxDim * 2.1);
        camera.near = maxDim / 100; camera.far = maxDim * 40;
        camera.updateProjectionMatrix();
        controls.update();
        setStatus('');
        onMeshState({ loaded: true });
      }, undefined, (e: any) => {
        if (st.disposed) return;
        const msg = `Mesh failed to load: ${e?.message || 'no mesh for this character yet'}`;
        setStatus(msg); onMeshState({ loaded: false, error: msg });
      });

      const loop = () => {
        if (st.disposed) return;
        st.raf = requestAnimationFrame(loop);
        controls.update();
        renderer.render(scene, camera);
      };
      loop();
    })();
    return () => {
      cancelled = true;
      const st2 = stateRef.current;
      st2.disposed = true;
      if (st2.raf) cancelAnimationFrame(st2.raf);
      try { st2.controls?.dispose?.(); } catch { /* ignore */ }
      try { st2.renderer?.dispose?.(); } catch { /* ignore */ }
      if (hostRef.current) hostRef.current.innerHTML = '';
      stateRef.current = {};
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [character, statueStamp]);

  const snap = useCallback(() => {
    const st = stateRef.current;
    const { renderer, scene, camera } = st;
    if (!renderer || !scene || !camera) return;
    // deterministic fixed-resolution capture: pin pixelRatio 1 + resize →
    // render → read → restore (hi-DPI ratio would change the captured size)
    const prevRatio = renderer.getPixelRatio ? renderer.getPixelRatio() : 1;
    const prevW = renderer.domElement.width / prevRatio, prevH = renderer.domElement.height / prevRatio;
    const prevAspect = camera.aspect;
    renderer.setPixelRatio(1);
    renderer.setSize(SNAP_W, SNAP_H, false);
    camera.aspect = SNAP_W / SNAP_H;
    camera.updateProjectionMatrix();
    renderer.render(scene, camera);            // render-then-read in the same task
    const dataUrl = renderer.domElement.toDataURL('image/png');
    renderer.setPixelRatio(prevRatio);
    renderer.setSize(prevW, prevH, false);
    camera.aspect = prevAspect;
    camera.updateProjectionMatrix();
    renderer.render(scene, camera);
    onSnapshot(dataUrl);
  }, [onSnapshot]);

  return (
    <div>
      <div ref={hostRef} style={{ width: '100%', background: '#0e1116', borderRadius: 8, minHeight: 200 }} />
      {status && <p style={{ ...hint, marginTop: 6 }}>{status}</p>}
      <div style={{ display: 'flex', gap: 8, marginTop: 8, alignItems: 'center' }}>
        <button style={btn} onClick={snap}>📸 Use this angle</button>
        <span style={hint}>drag = orbit · wheel = zoom · right-drag = pan</span>
      </div>
    </div>
  );
}

// ── main panel ──────────────────────────────────────────────────────────────
export default function Klein2Panel() {
  const lb = useLightbox();
  const [chars, setChars] = useState<CharT[]>([]);
  const [charName, setCharName] = useState('');
  const [charErr, setCharErr] = useState('');
  const cur = chars.find((c) => c.name === charName) || null;

  // statue
  const [statueRun, setStatueRun] = useState<StatueRunT | null>(null);
  const [statueMode, setStatueMode] = useState<'generate' | 'texture'>('generate');
  const [statueQuality, setStatueQuality] = useState<'standard' | 'high'>('standard');
  const [hasStatue, setHasStatue] = useState(false);
  const [statueStamp, setStatueStamp] = useState('0');
  const [meshState, setMeshState] = useState<{ loaded: boolean; error?: string }>({ loaded: false });
  const [statueMsg, setStatueMsg] = useState('');

  // snapshot
  const [snapshot, setSnapshot] = useState('');

  // pose library
  const [poses, setPoses] = useState<PoseT[]>([]);
  const [cats, setCats] = useState<string[]>([]);
  const [catFilter, setCatFilter] = useState('');
  const [poseId, setPoseId] = useState('');
  const [editPose, setEditPose] = useState<PoseT | null>(null);
  const [editPrompt, setEditPrompt] = useState('');
  const [poseBusy, setPoseBusy] = useState(false);
  const [poseMsg, setPoseMsg] = useState('');
  const [seedRun, setSeedRun] = useState<{ status: string; done: number; total: number } | null>(null);
  const [newOpen, setNewOpen] = useState(false);
  const [newName, setNewName] = useState('');
  const [newCat, setNewCat] = useState('Custom');
  const [newDesc, setNewDesc] = useState('');
  const [newRaw, setNewRaw] = useState(false);

  // generate
  const [extra, setExtra] = useState('');
  const [includeFront, setIncludeFront] = useState(true);
  const [count, setCount] = useState(2);
  const [seed, setSeed] = useState('');
  const [genBusy, setGenBusy] = useState(false);
  const [gen, setGen] = useState<GenStatusT | null>(null);
  const [genErr, setGenErr] = useState('');

  const loadChars = useCallback(async () => {
    try {
      const r = await j<{ characters: CharT[] }>(await fetch(`${BASE}/characters`));
      setChars(r.characters);
      setCharErr('');
      if (!charName && r.characters.length) setCharName(r.characters[0].name);
    } catch (e) { setCharErr(`characters: ${(e as Error).message}`); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [charName]);

  const loadPoses = useCallback(async () => {
    try {
      const r = await j<{ poses: PoseT[]; categories: string[]; seed_run: any }>(await fetch(`${BASE}/poses`));
      setPoses(r.poses); setCats(r.categories);
      setSeedRun(r.seed_run && r.seed_run.status ? r.seed_run : null);
    } catch (e) { setPoseMsg(`pose library: ${(e as Error).message}`); }
  }, []);

  useEffect(() => { void loadChars(); void loadPoses(); }, [loadChars, loadPoses]);

  // statue status poll (while running)
  useEffect(() => {
    if (!charName) return;
    let stop = false;
    const tick = async () => {
      try {
        const r = await j<{ run: StatueRunT | null; has_statue: boolean }>(
          await fetch(`${BASE}/statue/status/${encodeURIComponent(charName)}`));
        if (stop) return;
        setStatueRun(r.run);
        if (r.has_statue !== hasStatue) {
          setHasStatue(r.has_statue);
          setStatueStamp(String(Date.now()));   // statue landed → reload viewer
        }
      } catch { /* character without mesh etc. */ }
    };
    void tick();
    const iv = window.setInterval(() => {
      if (statueRun?.status === 'running') void tick();
    }, 3000);
    return () => { stop = true; window.clearInterval(iv); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [charName, statueRun?.status, hasStatue]);

  // seed-defaults poll
  useEffect(() => {
    if (seedRun?.status !== 'running') return;
    const iv = window.setInterval(() => { void loadPoses(); }, 3500);
    return () => window.clearInterval(iv);
  }, [seedRun?.status, loadPoses]);

  // generation poll
  useEffect(() => {
    if (!gen || gen.status !== 'running') return;
    const iv = window.setInterval(async () => {
      try {
        const r = await j<GenStatusT>(await fetch(`${BASE}/gen/${gen.gen_id}`));
        setGen(r);
      } catch { /* keep last */ }
    }, 2500);
    return () => window.clearInterval(iv);
  }, [gen?.gen_id, gen?.status]);

  const startStatue = async () => {
    setStatueMsg('');
    try {
      await j(await fetch(`${BASE}/statue/generate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ character_name: charName, quality: statueQuality, mode: statueMode }),
      }));
      setStatueRun({ status: 'running', phase: 'scan' });
    } catch (e) { setStatueMsg((e as Error).message); }
  };

  const savePose = async (regenerate: boolean) => {
    if (!editPose) return;
    setPoseBusy(true); setPoseMsg('');
    try {
      await j(await fetch(`${BASE}/poses/${editPose.id}/update`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: editPrompt, regenerate }),
      }));
      setPoseMsg(regenerate ? '✓ prompt saved + image regenerated' : '✓ prompt saved');
      setEditPose(null);
      await loadPoses();
    } catch (e) { setPoseMsg(`save failed: ${(e as Error).message}`); }
    setPoseBusy(false);
  };

  const deletePose = async (id: string) => {
    setPoseBusy(true); setPoseMsg('');
    try {
      await j(await fetch(`${BASE}/poses/${id}/delete`, { method: 'POST' }));
      if (poseId === id) setPoseId('');
      setEditPose(null);
      await loadPoses();
    } catch (e) { setPoseMsg(`delete failed: ${(e as Error).message}`); }
    setPoseBusy(false);
  };

  const createPose = async () => {
    if (!newName.trim() || !newDesc.trim()) return;
    setPoseBusy(true); setPoseMsg('');
    try {
      await j(await fetch(`${BASE}/poses`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName.trim(), category: newCat.trim() || 'Custom', prompt: newDesc.trim(), raw: newRaw }),
      }));
      setNewOpen(false); setNewName(''); setNewDesc('');
      await loadPoses();
      setPoseMsg('✓ pose generated');
    } catch (e) { setPoseMsg(`create failed: ${(e as Error).message}`); }
    setPoseBusy(false);
  };

  const uploadPose = async (file: File) => {
    setPoseBusy(true); setPoseMsg('');
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('name', file.name.replace(/\.[^.]+$/, ''));
      await j(await fetch(`${BASE}/poses/upload`, { method: 'POST', body: fd }));
      await loadPoses();
      setPoseMsg('✓ pose uploaded');
    } catch (e) { setPoseMsg(`upload failed: ${(e as Error).message}`); }
    setPoseBusy(false);
  };

  const seedDefaults = async () => {
    setPoseMsg('');
    try {
      await j(await fetch(`${BASE}/poses/seed-defaults`, { method: 'POST' }));
      setSeedRun({ status: 'running', done: 0, total: 1 });
      await loadPoses();
    } catch (e) { setPoseMsg((e as Error).message); }
  };

  const doGenerate = async () => {
    setGenErr('');
    if (!snapshot) { setGenErr('Take a 📸 angle snapshot first.'); return; }
    if (!poseId) { setGenErr('Pick a pose from the library.'); return; }
    setGenBusy(true);
    try {
      const r = await j<{ gen_id: string }>(await fetch(`${BASE}/generate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          character_name: charName, angle_png_b64: snapshot, pose_id: poseId,
          prompt_extra: extra, include_front_ref: includeFront,
          count, width: SNAP_W, height: SNAP_H,
          seed: seed.trim() ? Number(seed.trim()) : null,
        }),
      }));
      setGen({ gen_id: r.gen_id, status: 'running', done: 0, total: count, prompt: '', images: [], refs: [] });
    } catch (e) { setGenErr((e as Error).message); }
    setGenBusy(false);
  };

  const shownPoses = catFilter ? poses.filter((p) => p.category === catFilter) : poses;
  const selPose = poses.find((p) => p.id === poseId) || null;

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(360px,1.1fr) minmax(360px,1.1fr) minmax(340px,1fr)', gap: 16, alignItems: 'start' }}>
      {/* ── column 1: character + statue viewer ── */}
      <div style={{ ...box, display: 'grid', gap: 12, alignContent: 'start' }}>
        <h3 style={{ margin: 0, fontSize: 15 }}>🗿 Character statue</h3>
        <p style={{ ...hint, margin: 0 }}>
          Rotate the 3D statue to <b>exactly</b> the angle your shot needs, then snapshot it.
          That snapshot — not a guessed description — is what Klein gets as the identity reference.
        </p>
        {charErr && <p style={errTxt}>{charErr}</p>}
        <div>
          <label style={label}>Character</label>
          <select style={input} value={charName} onChange={(e) => { setCharName(e.target.value); setSnapshot(''); setStatueStamp(String(Date.now())); }}>
            {chars.map((c) => (
              <option key={c.character_id} value={c.name}>
                {c.name}{c.has_statue ? ' — 🗿 statue' : c.has_mesh ? ' — clay mesh' : ' — (no 3D body)'}
              </option>
            ))}
          </select>
        </div>
        {cur && !cur.has_mesh && !cur.has_statue && (
          <p style={{ ...hint, margin: 0 }}>
            No rig mesh yet — fine for 🧊 Full statue mode (TRELLIS builds its own geometry
            from the turnaround views); 🎨 Paint rig mesh needs the 3D body first.
          </p>
        )}
        {cur && (
          <>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <button style={btnGhost} disabled={statueRun?.status === 'running'} onClick={startStatue}>
                {statueRun?.status === 'running'
                  ? `⏳ Texturing… (${statueRun.phase || ''})`
                  : cur.has_statue ? '🔁 Re-texture statue' : '🗿 Generate textured statue'}
              </button>
              <select style={{ ...input, width: 'auto', padding: '6px 8px' }} value={statueMode}
                      onChange={(e) => setStatueMode(e.target.value as 'generate' | 'texture')}
                      title="Full statue: TRELLIS.2 generates its OWN geometry from your views — much better faces/hands than the rig mesh (which fuses close geometry). Paint rig mesh: texture the existing Hunyuan mesh (matches the rig exactly).">
                <option value="generate">🧊 Full statue (TRELLIS geometry — best faces/hands)</option>
                <option value="texture">🎨 Paint rig mesh (matches rig geometry)</option>
              </select>
              <select style={{ ...input, width: 'auto', padding: '6px 8px' }} value={statueQuality}
                      onChange={(e) => setStatueQuality(e.target.value as 'standard' | 'high')}
                      title="High = texture_size 4096 + conditioning 1536 + more steps — sharper, but the 4096 UV bake may OOM a 16GB card">
                <option value="standard">Quality: Standard (2048)</option>
                <option value="high">Quality: High (4096 — may OOM 16GB)</option>
              </select>
              {cur.has_statue
                ? <span style={okTxt}>textured statue on disk</span>
                : <span style={hint}>untextured clay shown until the statue is generated</span>}
            </div>
            {statueRun?.status === 'error' && <p style={errTxt}>statue: {statueRun.error}</p>}
            {statueMsg && <p style={errTxt}>{statueMsg}</p>}
            {(cur.has_statue || cur.has_mesh) && (
              <StatueViewer character={charName} statueStamp={statueStamp}
                            onSnapshot={setSnapshot} onMeshState={setMeshState} />
            )}
            {meshState.error && <p style={errTxt}>{meshState.error}</p>}
          </>
        )}
        <div>
          <label style={label}>Identity reference (image 1) — from the 📸 snapshot</label>
          {snapshot
            ? <img src={snapshot} alt="angle snapshot" style={{ width: 130, borderRadius: 6, border: '1px solid #2a2f3a' }} />
            : <p style={{ ...hint, margin: 0 }}>no snapshot yet</p>}
        </div>
      </div>

      {/* ── column 2: pose library 2.0 ── */}
      <div style={{ ...box, display: 'grid', gap: 10, alignContent: 'start' }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <h3 style={{ margin: 0, fontSize: 15 }}>🕺 Pose Library 2.0</h3>
          <div style={{ flex: 1 }} />
          <button style={btnSm} onClick={() => void loadPoses()}>Refresh</button>
        </div>
        <p style={{ ...hint, margin: 0 }}>
          Poses are plain <b>images</b>. Generated ones remember their prompt — open one to view,
          edit and regenerate it. The neutral gray mannequin style keeps the pose ref from leaking
          clothing or identity into your character.
        </p>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          <button style={chip(!catFilter)} onClick={() => setCatFilter('')}>All</button>
          {cats.map((c) => (
            <button key={c} style={chip(catFilter === c)} onClick={() => setCatFilter(c)}>{c}</button>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <button style={btnSm} onClick={() => setNewOpen((o) => !o)}>➕ New pose (prompt)</button>
          <label style={{ ...btnSm, display: 'inline-block' }}>
            ⬆ Upload image
            <input type="file" accept="image/*" style={{ display: 'none' }}
                   onChange={(e) => { const f = e.target.files?.[0]; if (f) void uploadPose(f); e.target.value = ''; }} />
          </label>
          <button style={btnSm} disabled={seedRun?.status === 'running'} onClick={seedDefaults}>
            {seedRun?.status === 'running' ? `✨ Seeding ${seedRun.done}/${seedRun.total}…` : '✨ Generate default set'}
          </button>
        </div>
        {newOpen && (
          <div style={{ border: '1px solid #2a2f3a', borderRadius: 8, padding: 10, display: 'grid', gap: 8 }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              <div><label style={label}>Name *</label>
                <input style={input} value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="e.g. Hero landing" /></div>
              <div><label style={label}>Category</label>
                <input style={input} value={newCat} onChange={(e) => setNewCat(e.target.value)} /></div>
            </div>
            <div><label style={label}>{newRaw ? 'Full prompt (verbatim)' : 'Pose description (mannequin style is added automatically)'}</label>
              <textarea style={{ ...input, minHeight: 56 }} value={newDesc} onChange={(e) => setNewDesc(e.target.value)}
                        placeholder="crouched superhero landing, one fist on the ground, looking up" /></div>
            <label style={{ ...hint, display: 'flex', gap: 6, alignItems: 'center' }}>
              <input type="checkbox" checked={newRaw} onChange={(e) => setNewRaw(e.target.checked)} />
              raw prompt (skip the gray-mannequin wrapper)
            </label>
            <button style={btn} disabled={poseBusy || !newName.trim() || !newDesc.trim()} onClick={createPose}>
              {poseBusy ? 'Generating…' : 'Generate pose image'}
            </button>
          </div>
        )}
        {poseMsg && <p style={poseMsg.startsWith('✓') ? okTxt : errTxt}>{poseMsg}</p>}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(96px, 1fr))', gap: 8, maxHeight: 420, overflowY: 'auto' }}>
          {shownPoses.map((p) => (
            <div key={p.id}
                 style={{ border: `2px solid ${poseId === p.id ? '#3b82f6' : '#2a2f3a'}`, borderRadius: 8, padding: 4, cursor: 'pointer', background: '#0e1116' }}
                 onClick={() => setPoseId(p.id)}>
              <img src={p.url} alt={p.name} style={{ width: '100%', borderRadius: 5, display: 'block' }} />
              <div style={{ fontSize: 11, color: '#cbd2dc', marginTop: 3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name}</div>
              <button style={{ ...btnSm, padding: '2px 6px', fontSize: 11, marginTop: 3 }}
                      onClick={(e) => { e.stopPropagation(); setEditPose(p); setEditPrompt(p.prompt); }}>
                {p.prompt ? '✏️ prompt' : 'ℹ️'}
              </button>
            </div>
          ))}
          {!shownPoses.length && <p style={hint}>No poses yet — hit ✨ Generate default set.</p>}
        </div>
        {editPose && (
          <div style={{ border: '1px solid #3b82f6', borderRadius: 8, padding: 10, display: 'grid', gap: 8 }}>
            <b style={{ fontSize: 13, color: '#e6e9ee' }}>✏️ {editPose.name} <span style={hint}>({editPose.source}{editPose.seed ? `, seed ${editPose.seed}` : ''})</span></b>
            {editPose.prompt || editPose.source === 'generated' ? (
              <>
                <textarea style={{ ...input, minHeight: 90 }} value={editPrompt} onChange={(e) => setEditPrompt(e.target.value)} />
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  <button style={btnGhost} disabled={poseBusy} onClick={() => void savePose(false)}>💾 Save prompt</button>
                  <button style={btn} disabled={poseBusy} onClick={() => void savePose(true)}>
                    {poseBusy ? 'Working…' : '🔁 Save + regenerate image'}
                  </button>
                  <div style={{ flex: 1 }} />
                  <button style={{ ...btnSm, color: '#ff8a8a' }} disabled={poseBusy} onClick={() => void deletePose(editPose.id)}>🗑 Delete</button>
                  <button style={btnSm} onClick={() => setEditPose(null)}>Close</button>
                </div>
              </>
            ) : (
              <>
                <p style={{ ...hint, margin: 0 }}>Uploaded pose — no prompt attached.</p>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button style={{ ...btnSm, color: '#ff8a8a' }} disabled={poseBusy} onClick={() => void deletePose(editPose.id)}>🗑 Delete</button>
                  <button style={btnSm} onClick={() => setEditPose(null)}>Close</button>
                </div>
              </>
            )}
          </div>
        )}
      </div>

      {/* ── column 3: generate ── */}
      <div style={{ ...box, display: 'grid', gap: 10, alignContent: 'start', position: 'sticky', top: 12 }}>
        <h3 style={{ margin: 0, fontSize: 15 }}>🚀 Generate</h3>
        <div style={{ display: 'flex', gap: 8 }}>
          <div style={{ flex: 1 }}>
            <label style={label}>Image 1 — identity (angle)</label>
            {snapshot ? <img src={snapshot} alt="identity" style={{ width: '100%', borderRadius: 6, border: '1px solid #2a2f3a' }} />
              : <p style={{ ...hint, margin: 0 }}>📸 snapshot needed</p>}
          </div>
          <div style={{ flex: 1 }}>
            <label style={label}>Image 2 — pose</label>
            {selPose ? <img src={selPose.url} alt="pose" style={{ width: '100%', borderRadius: 6, border: '1px solid #2a2f3a' }} />
              : <p style={{ ...hint, margin: 0 }}>pick a pose</p>}
          </div>
        </div>
        <label style={{ ...hint, display: 'flex', gap: 6, alignItems: 'center' }}>
          <input type="checkbox" checked={includeFront} onChange={(e) => setIncludeFront(e.target.checked)} />
          add the photoreal front base as image 3 (face likeness, fights the "statue look")
        </label>
        <div>
          <label style={label}>Extra prompt (scene, outfit, lighting — optional)</label>
          <textarea style={{ ...input, minHeight: 56 }} value={extra} onChange={(e) => setExtra(e.target.value)}
                    placeholder="on a neon-lit rooftop at night, wearing the red jacket" />
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          <div><label style={label}>Images</label>
            <select style={input} value={String(count)} onChange={(e) => setCount(Number(e.target.value))}>
              {[1, 2, 3, 4, 6, 8].map((n) => <option key={n} value={n}>{n}</option>)}
            </select></div>
          <div><label style={label}>Seed (blank = random)</label>
            <input style={input} value={seed} onChange={(e) => setSeed(e.target.value)} placeholder="random" /></div>
        </div>
        <button style={{ ...btn, opacity: snapshot && poseId && !genBusy ? 1 : 0.5 }}
                disabled={!snapshot || !poseId || genBusy} onClick={doGenerate}>
          {genBusy ? 'Submitting…' : '🚀 Generate'}
        </button>
        {genErr && <p style={errTxt}>{genErr}</p>}
        {gen && (
          <div style={{ display: 'grid', gap: 8 }}>
            <p style={{ ...hint, margin: 0 }}>
              {gen.status === 'running' ? `⏳ ${gen.done}/${gen.total}…` : gen.status === 'done' ? `✓ done (${gen.images.length})` : `⚠ ${gen.error || 'error'}`}
              {gen.status !== 'running' && gen.error ? ` — ${gen.error}` : ''}
            </p>
            {!!gen.refs.length && (
              <div style={{ display: 'flex', gap: 6 }}>
                {gen.refs.map((r) => (
                  <a key={r.name} href={r.url} target="_blank" rel="noreferrer" title={r.name}>
                    <img src={r.url} alt={r.name} style={{ width: 54, borderRadius: 4, border: '1px solid #2a2f3a' }} />
                  </a>
                ))}
                <span style={{ ...hint, alignSelf: 'center' }}>← the exact refs this run was given</span>
              </div>
            )}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              {gen.images.map((im, ii) => (
                <img key={im.id} src={im.url} alt={im.id}
                     title="Click to view large (zoom + pan)"
                     onClick={() => lb.open(gen.images.map((x) => x.url), ii)}
                     style={{ width: '100%', borderRadius: 6, border: '1px solid #2a2f3a',
                              cursor: 'zoom-in' }} />
              ))}
            </div>
          </div>
        )}
        <p style={{ ...hint, margin: 0 }}>
          The baked prompt tells Klein: identity from image 1, pose ONLY from image 2, photoreal skin
          &amp; fabric (so the statue look doesn't leak). Your extra prompt is appended after it.
        </p>
      </div>
      {lb.node}
    </div>
  );
}
