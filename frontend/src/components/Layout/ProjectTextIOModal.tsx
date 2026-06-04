/**
 * ProjectTextIOModal — two-tab modal for exporting / importing project
 * text data as JSON.
 *
 * Export tab: pretty-printed JSON view of the project's editable text
 * data (concept, characters, chapters, scenes, prompts, story-flow
 * ideas, transitions, ...).  Copy to clipboard or download as .json.
 *
 * Import tab: paste / upload a JSON payload, choose Override-all vs
 * Fill-missing-only, optionally accept mode-mismatch, apply.  Backend
 * validation messages are surfaced verbatim.
 *
 * Static helpers (per project mode):
 *   /examples/<mode>.json           — fully-filled example for that mode
 *   /docs/<mode>_llm_instructions.md — agent instructions for that mode
 *
 * Designed to be the bridge that lets an external LLM agent do the
 * heavy lifting on a script-to-storyboard pass without us trying to
 * cram it into Cowork's prompt enhancer.
 */
import { useEffect, useState } from 'react';
import { exportProjectText, importProjectText } from '@/api/client';
import { X } from 'lucide-react';

interface Props {
  projectId: string;
  projectMode: string;
  projectName: string;
  onClose: () => void;
}

type Tab = 'export' | 'import';

export default function ProjectTextIOModal({ projectId, projectMode, projectName, onClose }: Props) {
  const [tab, setTab] = useState<Tab>('export');

  // ── Export state ──────────────────────────────────────────────────
  const [exportText, setExportText] = useState<string>('');
  const [exportLoading, setExportLoading] = useState(false);
  const [exportErr, setExportErr] = useState<string | null>(null);

  // ── Import state ──────────────────────────────────────────────────
  const [importText, setImportText] = useState<string>('');
  const [importMode, setImportMode] = useState<'override' | 'fill_missing'>('fill_missing');
  const [acceptMismatch, setAcceptMismatch] = useState(false);
  const [importBusy, setImportBusy] = useState(false);
  const [importErr, setImportErr] = useState<string | null>(null);
  const [importOk, setImportOk] = useState<string | null>(null);

  // ── Load the export when the modal mounts or the export tab opens ─
  useEffect(() => {
    if (tab !== 'export' || exportText) return;
    setExportLoading(true);
    setExportErr(null);
    exportProjectText(projectId)
      .then((res) => setExportText(JSON.stringify(res.data, null, 2)))
      .catch((e: any) => setExportErr(e?.response?.data?.detail || e?.message || String(e)))
      .finally(() => setExportLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  const exampleHref = `/examples/${projectMode}.json`;
  const docsHref = `/docs/${projectMode}_llm_instructions.md`;

  const handleDownloadExport = () => {
    if (!exportText) return;
    const blob = new Blob([exportText], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${projectName.replace(/[^\w.-]+/g, '_')}_text_export.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  const handleCopyExport = async () => {
    if (!exportText) return;
    try {
      await navigator.clipboard.writeText(exportText);
    } catch {
      // ignore — most browsers allow it
    }
  };

  const handleImportFileChange = (file: File | null) => {
    if (!file) return;
    file.text().then((t) => setImportText(t));
  };

  const handleApplyImport = async () => {
    setImportErr(null);
    setImportOk(null);
    let parsed: any;
    try {
      parsed = JSON.parse(importText);
    } catch (e: any) {
      setImportErr(`Invalid JSON: ${e?.message || e}`);
      return;
    }
    const confirmMsg =
      importMode === 'override'
        ? `OVERRIDE all matching fields in "${projectName}" with the imported data?\n\nThis cannot be undone via the UI.`
        : `Fill in only missing fields in "${projectName}" using the imported data?\n\nExisting filled-in values will be left alone.`;
    if (!window.confirm(confirmMsg)) return;
    setImportBusy(true);
    try {
      const res = await importProjectText(projectId, {
        json_payload: parsed,
        import_mode: importMode,
        accept_mode_mismatch: acceptMismatch,
      });
      const s = res.data.stats;
      setImportOk(
        `Imported: concept fields=${s.concept_fields_updated ?? 0}, `
        + `chapters=${s.chapters_updated ?? 0}, scenes=${s.scenes_updated ?? 0}, `
        + `characters added=${s.characters_added ?? 0}, characters updated=${s.characters_updated ?? 0}`
        + (s.scenes_skipped_out_of_range ? `, scenes skipped out of range=${s.scenes_skipped_out_of_range}` : '')
        + (s.video_fields_dropped ? `, video fields dropped=${s.video_fields_dropped}` : ''),
      );
    } catch (e: any) {
      setImportErr(e?.response?.data?.detail || e?.message || String(e));
    } finally {
      setImportBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-[9999] p-4"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="bg-gray-900 rounded-lg shadow-2xl border border-gray-700 w-full max-w-4xl flex flex-col" style={{ maxHeight: '90vh' }}>
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-700">
          <div>
            <h2 className="text-lg font-semibold">Import / Export Project Text Details</h2>
            <p className="text-xs text-gray-400 mt-0.5">
              Project: <span className="text-gray-200">{projectName}</span>
              <span className="ml-2 text-gray-500">· Mode: {projectMode}</span>
            </p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-white" title="Close">
            <X size={20} />
          </button>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-gray-700 px-3">
          {(['export', 'import'] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-2 text-sm font-medium transition-colors ${
                tab === t
                  ? 'text-purple-300 border-b-2 border-purple-500 -mb-px'
                  : 'text-gray-400 hover:text-gray-200'
              }`}
            >
              {t === 'export' ? 'Export' : 'Import'}
            </button>
          ))}
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5 text-sm">
          {tab === 'export' ? (
            <ExportTab
              loading={exportLoading}
              text={exportText}
              error={exportErr}
              onCopy={handleCopyExport}
              onDownload={handleDownloadExport}
            />
          ) : (
            <ImportTab
              text={importText}
              setText={setImportText}
              mode={importMode}
              setMode={setImportMode}
              acceptMismatch={acceptMismatch}
              setAcceptMismatch={setAcceptMismatch}
              busy={importBusy}
              err={importErr}
              ok={importOk}
              onApply={handleApplyImport}
              onFile={handleImportFileChange}
            />
          )}
        </div>

        {/* Footer — global helpers */}
        <div className="border-t border-gray-700 px-5 py-3 flex flex-wrap items-center gap-3 text-xs text-gray-400">
          <a
            href={exampleHref}
            download={`example_${projectMode}.json`}
            className="text-purple-300 hover:text-purple-200 underline"
            title="Download a fully-filled example JSON for this project mode"
          >
            📄 Download example JSON ({projectMode})
          </a>
          <a
            href={docsHref}
            target="_blank"
            rel="noreferrer"
            className="text-blue-300 hover:text-blue-200 underline"
            title="LLM instructions document — drag this into an AI agent"
          >
            📖 View LLM instructions ({projectMode})
          </a>
        </div>
      </div>
    </div>
  );
}

// ── Export tab ────────────────────────────────────────────────────────

function ExportTab({
  loading, text, error, onCopy, onDownload,
}: {
  loading: boolean; text: string; error: string | null;
  onCopy: () => void; onDownload: () => void;
}) {
  return (
    <div className="space-y-3">
      <div className="bg-gray-800/50 border border-gray-700 rounded p-3 text-xs leading-relaxed">
        <div className="font-semibold text-gray-200 mb-1">What this export includes</div>
        <ul className="list-disc pl-5 space-y-0.5 text-gray-400">
          <li>Concept (title, concept text, style, image direction, color palette)</li>
          <li>Characters (names + descriptions only — not character images themselves)</li>
          <li>Chapters (names, colors, descriptions, character focus, style notes, nesting)</li>
          <li>Scenes (timing, transcribed narration / lyrics, image prompt, video prompt, story flow idea, character references by name, transitions, image movement)</li>
          <li>Resolution settings (unified + per-job-type image/video overrides)</li>
          <li>Source script / lyrics initial text</li>
        </ul>
        <div className="font-semibold text-gray-200 mt-2 mb-1">What it does NOT include</div>
        <ul className="list-disc pl-5 space-y-0.5 text-gray-400">
          <li>Generated images and videos (those are files, not text)</li>
          <li>Internal IDs and file paths</li>
          <li>Job history, export history, batch runs</li>
        </ul>
      </div>

      {loading && <div className="text-gray-400 text-xs">Loading export…</div>}
      {error && (
        <div className="text-red-300 bg-red-900/30 border border-red-700/40 rounded px-2 py-1.5 text-xs">
          {error}
        </div>
      )}

      <textarea
        readOnly
        value={text}
        rows={20}
        className="w-full bg-gray-950 border border-gray-700 rounded text-[11px] font-mono text-gray-200 p-2 focus:outline-none focus:border-purple-500"
        spellCheck={false}
      />

      <div className="flex flex-wrap gap-2">
        <button
          onClick={onCopy}
          disabled={!text}
          className="px-3 py-1.5 rounded bg-purple-600 hover:bg-purple-700 disabled:opacity-50 text-white text-xs"
        >
          Copy to Clipboard
        </button>
        <button
          onClick={onDownload}
          disabled={!text}
          className="px-3 py-1.5 rounded bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-xs"
        >
          Download .json
        </button>
      </div>
    </div>
  );
}

// ── Import tab ────────────────────────────────────────────────────────

function ImportTab({
  text, setText, mode, setMode, acceptMismatch, setAcceptMismatch,
  busy, err, ok, onApply, onFile,
}: {
  text: string; setText: (t: string) => void;
  mode: 'override' | 'fill_missing'; setMode: (m: 'override' | 'fill_missing') => void;
  acceptMismatch: boolean; setAcceptMismatch: (b: boolean) => void;
  busy: boolean; err: string | null; ok: string | null;
  onApply: () => void; onFile: (f: File | null) => void;
}) {
  return (
    <div className="space-y-3">
      <div className="bg-gray-800/50 border border-gray-700 rounded p-3 text-xs leading-relaxed text-gray-400">
        Paste JSON below, or upload a .json file. Use the same format the Export
        tab produced; you can also start from the example file linked at the
        bottom of this dialog. Mode-specific fields (video fields for
        narration_images, etc.) are dropped silently.
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <label className="text-xs px-3 py-1.5 rounded bg-gray-800 border border-gray-700 hover:bg-gray-700 cursor-pointer">
          Upload .json
          <input
            type="file"
            accept="application/json,.json"
            onChange={(e) => onFile(e.target.files?.[0] ?? null)}
            className="hidden"
          />
        </label>
        <button
          onClick={() => setText('')}
          className="text-xs px-3 py-1.5 rounded bg-gray-800 border border-gray-700 hover:bg-gray-700"
        >
          Clear
        </button>
      </div>

      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Paste your edited JSON here…"
        rows={14}
        className="w-full bg-gray-950 border border-gray-700 rounded text-[11px] font-mono text-gray-200 p-2 focus:outline-none focus:border-purple-500"
        spellCheck={false}
      />

      <fieldset className="border border-gray-700 rounded p-3 space-y-2 text-xs">
        <legend className="px-1 text-gray-300 font-medium">Import mode</legend>
        <label className="flex items-start gap-2 cursor-pointer">
          <input
            type="radio"
            checked={mode === 'fill_missing'}
            onChange={() => setMode('fill_missing')}
            className="mt-0.5 accent-blue-500"
          />
          <div>
            <div className="text-gray-200 font-medium">Fill only missing fields</div>
            <div className="text-gray-500">
              Leaves anything you've already filled in alone; only writes to fields that are currently empty.
              Safer; use when an agent generated additions to a partly-populated project.
            </div>
          </div>
        </label>
        <label className="flex items-start gap-2 cursor-pointer">
          <input
            type="radio"
            checked={mode === 'override'}
            onChange={() => setMode('override')}
            className="mt-0.5 accent-purple-500"
          />
          <div>
            <div className="text-gray-200 font-medium">Override all matching fields</div>
            <div className="text-gray-500">
              Replaces concept, every chapter (matched by order), every scene (matched by order_index) and every character (matched by name) with the imported values. Use when an agent rewrote everything.
            </div>
          </div>
        </label>
        <label className="flex items-center gap-2 cursor-pointer pt-1 border-t border-gray-800 mt-2">
          <input
            type="checkbox"
            checked={acceptMismatch}
            onChange={(e) => setAcceptMismatch(e.target.checked)}
            className="accent-amber-500"
          />
          <span className="text-gray-300">
            Accept project-mode mismatch (e.g. import a music_video JSON into a narration_video project)
          </span>
        </label>
      </fieldset>

      {err && (
        <div className="text-red-300 bg-red-900/30 border border-red-700/40 rounded px-2 py-1.5 text-xs whitespace-pre-wrap">
          {err}
        </div>
      )}
      {ok && (
        <div className="text-emerald-300 bg-emerald-900/30 border border-emerald-700/40 rounded px-2 py-1.5 text-xs">
          {ok}
        </div>
      )}

      <button
        onClick={onApply}
        disabled={busy || !text.trim()}
        className="px-4 py-2 rounded bg-purple-600 hover:bg-purple-700 disabled:opacity-50 text-white text-xs font-medium"
      >
        {busy ? 'Applying…' : 'Apply Import'}
      </button>
    </div>
  );
}
