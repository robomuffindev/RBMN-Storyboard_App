/**
 * ImportAafModal — import an ElevenLabs (Dubbing Studio) AAF timeline.
 *
 * Parses the AAF's clip boundaries into scenes (REPLACING the project's current
 * scenes), and optionally attaches audio (use the project's existing audio, or
 * upload a new file) which is then sliced per-scene to match.
 */
import { useState, useRef, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { X, FileUp, Music, AlertTriangle, Loader2, CheckCircle } from 'lucide-react';
import { importAaf, getAssets } from '@/api/client';

interface ImportAafModalProps {
  projectId: string;
  onClose: () => void;
  onImported: () => void;
}

export default function ImportAafModal({ projectId, onClose, onImported }: ImportAafModalProps) {
  const [aafFile, setAafFile] = useState<File | null>(null);
  const [audioMode, setAudioMode] = useState<'existing' | 'upload'>('existing');
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [currentAudio, setCurrentAudio] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const aafRef = useRef<HTMLInputElement>(null);
  const audioRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    getAssets(projectId, 'music')
      .then((r) => {
        const m = (r.data || []).find((a: any) => !(a.rel_path || '').includes('stems/'));
        setCurrentAudio(m ? m.filename : null);
      })
      .catch(() => setCurrentAudio(null));
  }, [projectId]);

  const doImport = async () => {
    setError(null);
    if (!aafFile) { setError('Choose an .aaf file to import.'); return; }
    if (audioMode === 'upload' && !audioFile) { setError('Choose an audio file to upload, or switch to "use current audio".'); return; }
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append('file', aafFile);
      if (audioMode === 'upload' && audioFile) fd.append('audio', audioFile);
      const res = await importAaf(projectId, fd);
      const d = res.data;
      setResult(`${d.message}${d.audio_attached ? ' Audio attached + sliced.' : ''}${d.chapter_count ? ` ${d.chapter_count} chapter(s).` : ''}`);
      onImported();
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || 'Import failed.');
    } finally {
      setBusy(false);
    }
  };

  return createPortal(
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', zIndex: 9994, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
      onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div className="bg-gray-900 border border-gray-700 rounded-xl w-full max-w-md p-5">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-base font-bold text-gray-100 flex items-center gap-2"><FileUp size={17} className="text-blue-400" /> Import AAF (ElevenLabs)</h2>
          <button onClick={() => !busy && onClose()} className="text-gray-400 hover:text-gray-200"><X size={16} /></button>
        </div>

        {result ? (
          <div className="space-y-4">
            <div className="flex items-start gap-2 text-sm text-emerald-300"><CheckCircle size={18} className="shrink-0 mt-0.5" /><span>{result}</span></div>
            <button onClick={onClose} className="w-full px-3 py-2 rounded bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium">Done</button>
          </div>
        ) : (
          <div className="space-y-4">
            <p className="text-[12px] text-gray-400">
              Parses the AAF's clip timings into scenes. Use this to match your timeline to what you built in ElevenLabs Dubbing Studio (export → AAF).
            </p>

            {/* AAF file */}
            <div>
              <label className="text-[11px] font-semibold text-gray-400">AAF file</label>
              <input ref={aafRef} type="file" accept=".aaf" className="hidden" onChange={(e) => setAafFile(e.target.files?.[0] || null)} />
              <button onClick={() => aafRef.current?.click()} className="w-full mt-1 px-3 py-2 rounded bg-gray-800 hover:bg-gray-700 text-gray-200 text-sm text-left truncate">
                {aafFile ? aafFile.name : 'Choose .aaf file…'}
              </button>
            </div>

            {/* Audio */}
            <div className="p-2.5 bg-gray-800/60 border border-gray-700 rounded space-y-2">
              <div className="text-[11px] font-semibold text-gray-300 flex items-center gap-1.5"><Music size={13} className="text-emerald-400" /> Audio</div>
              <label className="flex items-center gap-2 text-[12px] text-gray-300">
                <input type="radio" checked={audioMode === 'existing'} onChange={() => setAudioMode('existing')} />
                Use current project audio {currentAudio ? <span className="text-gray-500">({currentAudio})</span> : <span className="text-amber-400">(none yet)</span>}
              </label>
              <label className="flex items-center gap-2 text-[12px] text-gray-300">
                <input type="radio" checked={audioMode === 'upload'} onChange={() => setAudioMode('upload')} />
                Upload new audio file
              </label>
              {audioMode === 'upload' && (
                <>
                  <input ref={audioRef} type="file" accept="audio/*" className="hidden" onChange={(e) => setAudioFile(e.target.files?.[0] || null)} />
                  <button onClick={() => audioRef.current?.click()} className="w-full px-3 py-1.5 rounded bg-gray-800 hover:bg-gray-700 text-gray-200 text-[12px] text-left truncate">
                    {audioFile ? audioFile.name : 'Choose audio file…'}
                  </button>
                </>
              )}
              <p className="text-[10px] text-gray-500">After import, the audio is sliced per scene to match the new boundaries.</p>
            </div>

            {/* Replace warning */}
            <div className="flex items-start gap-2 text-[11px] text-amber-300 bg-amber-950/30 border border-amber-800/40 rounded p-2">
              <AlertTriangle size={14} className="shrink-0 mt-0.5" />
              This replaces ALL scenes in this project with the AAF's timeline. Existing scene work (prompts, images) on the old scenes will be lost.
            </div>

            {error && <div className="text-[12px] text-red-400">{error}</div>}

            <div className="flex gap-2">
              <button onClick={() => !busy && onClose()} className="px-3 py-2 rounded bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm">Cancel</button>
              <button onClick={doImport} disabled={busy || !aafFile}
                className="flex-1 px-3 py-2 rounded bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium flex items-center justify-center gap-1.5 disabled:opacity-50">
                {busy ? <><Loader2 size={15} className="animate-spin" /> Importing…</> : 'Replace scenes from AAF'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
