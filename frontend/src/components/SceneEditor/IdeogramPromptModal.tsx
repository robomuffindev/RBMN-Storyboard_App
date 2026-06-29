import { useState } from 'react';
import { createPortal } from 'react-dom';
import { buildJsonPrompt } from '@/api/client';

interface IdeogramPromptModalProps {
  projectId: string;
  sceneId: string;
  /** Existing caption object for this scene (or null/empty). */
  initial: any;
  onClose: () => void;
  /** Persist the edited caption onto the scene. */
  onSave: (caption: any) => void;
}

// Minimal template shown when a scene has no caption yet.
const TEMPLATE = {
  high_level_description: '',
  background: '',
  style: 'photo',
  style_detail: '',
  aesthetics: '',
  lighting: '',
  medium: 'photograph',
  style_palette: [],
  elements: [
    { type: 'obj', desc: '', palette: [], x: 0, y: 0, w: 1, h: 1 },
  ],
};

export default function IdeogramPromptModal({
  projectId, sceneId, initial, onClose, onSave,
}: IdeogramPromptModalProps) {
  const hasInitial = initial && typeof initial === 'object' && Object.keys(initial).length > 0;
  const [text, setText] = useState(() =>
    JSON.stringify(hasInitial ? initial : TEMPLATE, null, 2),
  );
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showHelp, setShowHelp] = useState(false);

  const handleGenerate = async () => {
    setBusy(true);
    setError(null);
    try {
      const resp = await buildJsonPrompt(projectId, { scene_id: sceneId });
      setText(JSON.stringify(resp.data.json_prompt, null, 2));
    } catch (e: any) {
      setError(
        e?.response?.data?.detail ||
        e?.message ||
        'Generation failed — make sure the scene has a prompt (Enhance it first) and an LLM key is set.',
      );
    } finally {
      setBusy(false);
    }
  };

  const handleSave = () => {
    let parsed: any;
    try {
      parsed = JSON.parse(text);
    } catch {
      setError('Invalid JSON — fix the syntax, or click "Generate with AI".');
      return;
    }
    if (!parsed || typeof parsed !== 'object') {
      setError('The prompt must be a JSON object.');
      return;
    }
    onSave(parsed);
    onClose();
  };

  return createPortal(
    <div
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 9600,
      }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="bg-gray-900 border border-gray-700 rounded-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto p-5">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-bold text-gray-100">
            Ideogram JSON Prompt
            <span className="text-[10px] text-purple-300/70 ml-2 font-normal">Krea 2 structured caption</span>
          </h2>
          <div className="flex gap-2">
            <button
              onClick={() => setShowHelp((h) => !h)}
              className="text-xs px-2.5 py-1 rounded bg-gray-800 hover:bg-gray-700 text-gray-300"
            >
              {showHelp ? 'Hide' : 'Instructions'}
            </button>
            <button
              onClick={onClose}
              className="text-xs px-2.5 py-1 rounded bg-gray-800 hover:bg-gray-700 text-gray-400"
            >
              Close
            </button>
          </div>
        </div>

        {showHelp && (
          <div className="text-[11px] leading-relaxed text-gray-400 bg-gray-800/50 border border-gray-700 rounded p-3 mb-3 space-y-1.5">
            <p>
              This is a <span className="text-gray-200">structured caption</span> for Krea 2. Instead of one prose
              sentence, you describe the image as a layout: a global summary, a style block, a background, and a list of
              positioned <span className="text-gray-200">elements</span>.
            </p>
            <p>
              <span className="text-gray-200">Positioning:</span> each element has{' '}
              <code className="text-emerald-400">x, y, w, h</code> as fractions of the frame (0–1).{' '}
              <code className="text-emerald-400">x,y</code> = top-left corner, <code className="text-emerald-400">w,h</code> = size.
              Full frame = <code>x:0, y:0, w:1, h:1</code>.
            </p>
            <p>
              <span className="text-gray-200">Colors:</span> uppercase hex like <code>#1A1A1A</code>.{' '}
              <code className="text-emerald-400">style_palette</code> (up to 16) is the overall palette; each element can
              have its own <code className="text-emerald-400">palette</code> (up to 5).
            </p>
            <p>
              <span className="text-gray-200">Tip:</span> click <span className="text-purple-300">Generate with AI</span> to
              draft this from the scene's prompt, then tweak descriptions, boxes, and colors. Add a{' '}
              <code>{'{ "type":"text", "text":"WORDS" }'}</code> element only if you want literal text in the image.
            </p>
          </div>
        )}

        <textarea
          value={text}
          onChange={(e) => { setText(e.target.value); setError(null); }}
          spellCheck={false}
          className="w-full h-72 px-3 py-2 bg-gray-950 border border-gray-700 rounded font-mono text-[11px] text-gray-100 focus:outline-none focus:border-blue-500 resize-y"
        />
        {error && <p className="text-xs text-red-400 mt-2">{error}</p>}

        <div className="flex items-center justify-between mt-3">
          <button
            onClick={handleGenerate}
            disabled={busy}
            className="text-sm px-3 py-1.5 rounded bg-purple-700 hover:bg-purple-600 text-white disabled:opacity-50"
          >
            {busy ? 'Generating…' : '✨ Generate with AI'}
          </button>
          <div className="flex gap-2">
            <button
              onClick={onClose}
              className="text-sm px-3 py-1.5 rounded bg-gray-800 hover:bg-gray-700 text-gray-300"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              className="text-sm px-3 py-1.5 rounded bg-blue-700 hover:bg-blue-600 text-white"
            >
              Save
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
