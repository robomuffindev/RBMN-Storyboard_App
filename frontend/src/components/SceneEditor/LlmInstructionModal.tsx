import { useState } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';

interface LlmInstructionModalProps {
  title: string;
  /** The current prompt being enhanced, shown read-only for context. */
  promptText: string;
  /** Current saved instruction. */
  value: string;
  /** Persist the instruction (empty string = clear). */
  onSave: (text: string) => void;
  onClose: () => void;
}

export default function LlmInstructionModal({
  title, promptText, value, onSave, onClose,
}: LlmInstructionModalProps) {
  const [text, setText] = useState(value || '');

  return createPortal(
    <div
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 9600,
      }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="bg-gray-900 border border-gray-700 rounded-xl w-full max-w-lg p-5">
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-base font-bold text-gray-100">{title}</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-200"><X size={16} /></button>
        </div>
        <p className="text-[11px] text-gray-500 mb-3">
          Extra direction passed to the LLM whenever you Enhance this prompt — use it to keep the model on
          track when it drifts (e.g. "she must stay seated", "wide establishing shot only", "no text or logos").
          It's saved on this scene and applied every time you Enhance until you clear it.
        </p>

        {promptText ? (
          <div className="mb-3">
            <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">Prompt being enhanced</div>
            <div className="text-[11px] text-gray-400 bg-gray-800/50 border border-gray-700 rounded p-2 max-h-24 overflow-y-auto whitespace-pre-wrap">
              {promptText || '(empty — Enhance will generate from scratch)'}
            </div>
          </div>
        ) : null}

        <label className="text-xs font-medium text-gray-300">Your instruction to the LLM</label>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          autoFocus
          placeholder="e.g. Keep the character seated at the table. Do not change the time of day. Emphasize the red door."
          className="w-full h-28 mt-1 px-3 py-2 bg-gray-950 border border-gray-700 rounded text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 resize-y"
        />

        <div className="flex items-center justify-between mt-3">
          <button
            onClick={() => { onSave(''); onClose(); }}
            className="text-xs px-3 py-1.5 rounded bg-gray-800 hover:bg-gray-700 text-gray-400"
          >
            Clear
          </button>
          <div className="flex gap-2">
            <button
              onClick={onClose}
              className="text-sm px-3 py-1.5 rounded bg-gray-800 hover:bg-gray-700 text-gray-300"
            >
              Cancel
            </button>
            <button
              onClick={() => { onSave(text.trim()); onClose(); }}
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
