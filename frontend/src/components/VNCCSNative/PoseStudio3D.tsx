/**
 * VNCCS 3D Pose Studio — thin React host over the portable, ComfyUI-independent
 * PoseViewerCore (vendored to /vnccs-pose/). Mounts the Three.js poseable rig,
 * loads the MakeHuman joint topology + morphed vertices from the pinned VNCCS
 * host (via the morph worker → our proxy), and lets you author a pose and save it
 * to the shared VNCCS pose library (which the generation pipeline consumes).
 *
 * The heavy 3D engine (camera orbit, FK/IK chains, hand presets, gizmos) lives in
 * the vendored core; this host only wires controls + the pose-library round-trip.
 *
 * NOTE: this is the one phase that needs a live VNCCS host to fully exercise
 * (it fetches morph_data.bin through the proxy). It fails gracefully otherwise.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import * as api from './vnccsNativeApi';

const CORE_URL = '/vnccs-pose/vnccs_pose_studio_core.js';
const WORKER_URL = '/vnccs-pose/vnccs_pose_morph_worker.js';

// Default MakeHuman body params (worker.solveMorph reads these).
const DEFAULT_BODY = { age: 0.5, gender: 0, weight: 0.5, muscle: 0.5, height: 0.5, breast_size: 0.5 };

type BodyParams = typeof DEFAULT_BODY;
type Viewer = {
  init: () => Promise<void>;
  loadData: (d: unknown, keepCamera?: boolean) => void;
  updateBodyVertices: (v: Float32Array, bones?: Float32Array | null) => boolean;
  setIKMode: (on: boolean) => void;
  capture: (w: number, h: number, zoom?: number, bg?: string) => string | null;
  getPose: () => unknown;
  dispose: () => void;
  requestRender?: () => void;
};

const box: React.CSSProperties = { background: '#161a22', border: '1px solid #2a2f3a', borderRadius: 10, padding: 16 };
const label: React.CSSProperties = { fontSize: 12, color: '#9aa4b2', marginBottom: 4, display: 'block' };
const input: React.CSSProperties = {
  width: '100%', background: '#0e1116', border: '1px solid #2a2f3a', borderRadius: 6, color: '#e6e9ee',
  padding: '7px 9px', fontSize: 13,
};
const btn: React.CSSProperties = {
  background: '#3b82f6', color: '#fff', border: 'none', borderRadius: 6, padding: '8px 14px',
  fontSize: 13, cursor: 'pointer', fontWeight: 600,
};
const btnGhost: React.CSSProperties = { ...btn, background: 'transparent', border: '1px solid #2a2f3a', color: '#cbd2dc' };

export default function PoseStudio3D() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const viewerRef = useRef<Viewer | null>(null);
  const workerRef = useRef<Worker | null>(null);
  const topoRef = useRef<{ bones: unknown[]; joints: Record<string, number[]> } | null>(null);
  const seqRef = useRef(0);
  const loadedRef = useRef(false);

  const [status, setStatus] = useState('Loading 3D engine…');
  const [ready, setReady] = useState(false);
  const [ikMode, setIkMode] = useState(true);
  const [body, setBody] = useState<BodyParams>({ ...DEFAULT_BODY });
  const [preview, setPreview] = useState<string>('');
  const [poseName, setPoseName] = useState('');
  const [category, setCategory] = useState('Custom');
  const [saveMsg, setSaveMsg] = useState('');
  const [err, setErr] = useState('');

  const solve = useCallback((params: BodyParams, initial: boolean) => {
    const w = workerRef.current;
    if (!w) return;
    const seq = ++seqRef.current;
    const onMsg = (ev: MessageEvent) => {
      const m = ev.data || {};
      if (m.type === 'result' && m.seq === seq) {
        w.removeEventListener('message', onMsg);
        const verts: Float32Array = m.vertices;
        const viewer = viewerRef.current;
        const topo = topoRef.current;
        if (!viewer || !topo) return;
        if (initial || !loadedRef.current) {
          viewer.loadData({ vertices: verts, bones: topo.bones, joints: topo.joints });
          loadedRef.current = true;
          setReady(true);
          setStatus('');
        } else {
          viewer.updateBodyVertices(verts, m.bonePositions || null);
        }
      } else if (m.type === 'error') {
        w.removeEventListener('message', onMsg);
        setErr(m.message || 'morph worker error');
        setStatus('');
      }
    };
    w.addEventListener('message', onMsg);
    w.postMessage({ type: 'solve', seq, params });
  }, []);

  useEffect(() => {
    let disposed = false;
    (async () => {
      try {
        const canvas = canvasRef.current;
        if (!canvas) return;
        // dynamic import of the vendored static ES module (not bundled by Vite)
        const mod = await import(/* @vite-ignore */ CORE_URL) as { PoseViewerCore: new (c: HTMLCanvasElement, o?: unknown) => Viewer };
        if (disposed) return;
        const viewer = new mod.PoseViewerCore(canvas, { ikEnabled: true, onError: (e: unknown) => setErr(String(e)) });
        viewerRef.current = viewer;
        await viewer.init();
        if (disposed) { viewer.dispose(); return; }

        setStatus('Loading body mesh from the host…');
        const worker = new Worker(WORKER_URL, { type: 'module' });
        workerRef.current = worker;
        // fetch topology (bones/joints) once, then the initial morphed vertices
        const onInit = (ev: MessageEvent) => {
          const m = ev.data || {};
          if (m.type === 'meshinfo') {
            worker.removeEventListener('message', onInit);
            topoRef.current = { bones: m.bones || [], joints: m.joints || {} };
            solve({ ...DEFAULT_BODY }, true);
          } else if (m.type === 'error') {
            worker.removeEventListener('message', onInit);
            setErr(m.message || 'Failed to load morph data (needs a live VNCCS host).');
            setStatus('');
          }
        };
        worker.addEventListener('message', onInit);
        worker.postMessage({ type: 'init' });
      } catch (e) {
        setErr(`3D engine failed to load: ${(e as Error).message}`);
        setStatus('');
      }
    })();
    return () => {
      disposed = true;
      try { viewerRef.current?.dispose(); } catch { /* ignore */ }
      try { workerRef.current?.terminate(); } catch { /* ignore */ }
      viewerRef.current = null;
      workerRef.current = null;
    };
  }, [solve]);

  const toggleIK = () => {
    const next = !ikMode;
    setIkMode(next);
    viewerRef.current?.setIKMode(next);
  };

  const onBody = (k: keyof BodyParams, v: number) => {
    const next = { ...body, [k]: v };
    setBody(next);
    if (ready) solve(next, false);
  };

  const doCapture = () => {
    const url = viewerRef.current?.capture(512, 1536, 1.0, '#111318');
    if (url) setPreview(url);
  };

  const savePose = async () => {
    setSaveMsg(''); setErr('');
    const viewer = viewerRef.current;
    if (!viewer || !poseName.trim()) return;
    const pose = viewer.getPose();
    const previewUrl = viewer.capture(384, 384, 1.0, '#111318') || preview;
    try {
      const res = await api.relay('pose_library/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: poseName.trim(), category: category.trim() || 'Custom',
          pose, preview: previewUrl, tags: [],
        }),
      });
      if (!res.ok) throw new Error(`save -> ${res.status}`);
      setSaveMsg(`Saved “${poseName.trim()}” to the pose library.`);
    } catch (e) {
      setErr(`Save failed: ${(e as Error).message}`);
    }
  };

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 320px', gap: 16 }}>
      <div style={box}>
        <div style={{ position: 'relative', width: '100%', aspectRatio: '2 / 3', background: '#0e1116', borderRadius: 8, overflow: 'hidden' }}>
          <canvas ref={canvasRef} width={512} height={768} style={{ width: '100%', height: '100%', display: 'block' }} />
          {(status || err) && (
            <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
                          textAlign: 'center', padding: 20, color: err ? '#ff8a8a' : '#9aa4b2', fontSize: 13 }}>
              {err ? `⚠ ${err}` : status}
            </div>
          )}
        </div>
        <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
          <button style={btnGhost} onClick={toggleIK} disabled={!ready}>{ikMode ? 'IK mode' : 'FK mode'}</button>
          <button style={btnGhost} onClick={doCapture} disabled={!ready}>Capture preview</button>
        </div>
        <p style={{ color: '#6b7280', fontSize: 12, marginBottom: 0 }}>
          Drag to orbit · click a joint to rotate (FK) or drag a hand/foot/head handle (IK). Author a pose, then save it to the library.
        </p>
      </div>

      <div style={{ display: 'grid', gap: 16 }}>
        <div style={box}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Body</h3>
          {(['age', 'gender', 'weight', 'muscle', 'height', 'breast_size'] as Array<keyof BodyParams>).map((k) => (
            <div key={k} style={{ marginBottom: 8 }}>
              <label style={label}>{k.replace('_', ' ')} — {body[k].toFixed(2)}</label>
              <input type="range" min={0} max={1} step={0.01} value={body[k]} disabled={!ready}
                     onChange={(e) => onBody(k, parseFloat(e.target.value))} style={{ width: '100%' }} />
            </div>
          ))}
        </div>

        <div style={box}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Save to Pose Library</h3>
          {preview && <img src={preview} alt="pose preview" style={{ width: '100%', borderRadius: 6, marginBottom: 8, border: '1px solid #2a2f3a' }} />}
          <label style={label}>Pose name *</label>
          <input style={input} value={poseName} onChange={(e) => setPoseName(e.target.value)} placeholder="e.g. Hero Stance" />
          <label style={{ ...label, marginTop: 8 }}>Category</label>
          <input style={input} value={category} onChange={(e) => setCategory(e.target.value)} />
          <button style={{ ...btn, marginTop: 10, width: '100%', opacity: ready && poseName.trim() ? 1 : 0.5,
                           cursor: ready && poseName.trim() ? 'pointer' : 'not-allowed' }}
                  disabled={!ready || !poseName.trim()} onClick={savePose}>Save pose</button>
          {saveMsg && <p style={{ color: '#5ee08a', fontSize: 12 }}>✓ {saveMsg}</p>}
        </div>
      </div>
    </div>
  );
}
