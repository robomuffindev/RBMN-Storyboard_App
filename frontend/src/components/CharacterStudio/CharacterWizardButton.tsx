/**
 * Character Studio P2 — Character Wizard button for the create-character form.
 *
 * Calls POST /wizards/character with {description} and hands the returned
 * character_info dict back to the parent form via onApply, which merges it
 * onto whatever local character_info state the create form already tracks
 * (Phase 1's CreateCharacterForm doesn't currently have character_info
 * fields at all — those only exist on the Sheet tab's edit form — so this
 * component is designed to be dropped into either: it just needs an
 * onApply(info) callback).
 *
 * The clone-from-image wizard (POST /wizards/clone) is explicitly out of
 * scope per the build instructions (file-upload wizard is not trivial) and
 * is intentionally not implemented here.
 */
import { useState, useEffect, useRef } from 'react';
import { Wand2, X } from 'lucide-react';
import { p2Api, WizardCharacterInfoT } from './characterStudioP2Api';
import { Spinner, ErrorText } from './p2Shared';

export function CharacterWizardButton({ onApply, style }: { onApply: (info: WizardCharacterInfoT) => void; style?: string }) {
  const [open, setOpen] = useState(false);
  const [description, setDescription] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Live reassurance: Ollama /api/chat is a single blocking call with no
  // native progress, so we surface an elapsed-seconds timer + staged text
  // while it runs so the wizard never looks frozen/broken.
  useEffect(() => {
    if (busy) {
      setElapsed(0);
      timerRef.current = setInterval(() => setElapsed((e) => e + 1), 1000);
    } else if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [busy]);

  const stageMsg =
    elapsed < 2
      ? 'Contacting Ollama\u2026'
      : elapsed < 6
      ? 'Model is generating your tag sheet\u2026'
      : 'Still working \u2014 larger models can take a bit\u2026';

  const run = async () => {
    if (!description.trim()) {
      setError('Describe the character first.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await p2Api.wizardCharacter(description.trim(), style);
      onApply(res.character_info || {});
      setOpen(false);
      setDescription('');
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        type="button"
        className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-md text-sm font-medium flex items-center gap-2 text-gray-300"
      >
        <Wand2 size={14} className="text-purple-400" />
        Wizard
      </button>
    );
  }

  return (
    <div className="bg-gray-800/60 border border-gray-700 rounded-md p-3 flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-xs uppercase tracking-wide text-gray-400 flex items-center gap-1.5">
          <Wand2 size={13} className="text-purple-400" />
          Character Wizard
        </span>
        <button type="button" onClick={() => setOpen(false)} className="text-gray-500 hover:text-gray-300">
          <X size={14} />
        </button>
      </div>
      <textarea
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="Describe your character, e.g. a stoic elf ranger with silver hair"
        rows={2}
        className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 text-sm focus:outline-none focus:border-purple-600 resize-y"
      />
      <ErrorText msg={error} />
      <button
        type="button"
        onClick={run}
        disabled={busy}
        className="px-3 py-1.5 bg-purple-700 hover:bg-purple-600 disabled:opacity-50 rounded-md text-sm font-medium flex items-center gap-2 self-start"
      >
        {busy && <Spinner size={13} />}
        Generate Tag Sheet
      </button>
      {busy && (
        <span className="text-xs text-gray-400 flex items-center gap-1.5">
          <Spinner size={11} />
          {stageMsg} <span className="text-gray-600">({elapsed}s)</span>
        </span>
      )}
    </div>
  );
}
