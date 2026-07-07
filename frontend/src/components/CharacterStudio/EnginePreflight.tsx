/**
 * Character Studio P2 — Engine selector + read-only preflight badges.
 *
 * Rendered in the character-detail header. `engine` state is lifted to the
 * parent (CharacterDetail) since Poses/Costumes/Emotions/Process/GenerateAll
 * all need to know the currently selected engine.
 */
import { useEffect, useState, useCallback } from 'react';
import { AlertTriangle, CheckCircle2 } from 'lucide-react';
import { EngineT, p2Api, PreflightResponseT } from './characterStudioP2Api';
import { Spinner, ErrorText } from './p2Shared';

export function EngineSelector({
  engine,
  onChange,
}: {
  engine: EngineT;
  onChange: (e: EngineT) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <span className="text-gray-400">Engine</span>
      <select
        value={engine}
        onChange={(e) => onChange(e.target.value as EngineT)}
        className="bg-gray-800 border border-gray-700 rounded-md px-3 py-1.5 text-gray-100 focus:outline-none focus:border-purple-600 text-sm"
      >
        <option value="auto">auto</option>
        <option value="qwen">qwen (VNCCS worker)</option>
        <option value="klein">klein</option>
      </select>
    </label>
  );
}

function EngineChip({ label, online }: { label: string; online: boolean }) {
  return (
    <span
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full ${
        online ? 'text-emerald-400 bg-emerald-950/30' : 'text-gray-500 bg-gray-800/60'
      }`}
      title={online ? `${label} engine available` : `${label} engine unavailable (no worker advertises it)`}
    >
      {online ? <CheckCircle2 size={11} /> : <AlertTriangle size={11} />}
      {label}
    </span>
  );
}

export function PreflightBadges({ characterId, engine }: { characterId: string; engine: EngineT }) {
  const [result, setResult] = useState<PreflightResponseT | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await p2Api.preflight(characterId, engine);
      setResult(r);
    } catch (e: any) {
      setError(e.message);
      setResult(null);
    } finally {
      setLoading(false);
    }
  }, [characterId, engine]);

  useEffect(() => {
    load();
  }, [load]);

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-xs text-gray-500">
        <Spinner size={12} /> Checking preflight...
      </div>
    );
  }

  if (error) {
    return <ErrorText msg={error} />;
  }

  if (!result) return null;

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2 text-xs flex-wrap">
        {result.ok ? (
          <span className="inline-flex items-center gap-1 text-emerald-400">
            <CheckCircle2 size={13} /> Engine ready{result.engine_resolved ? ` (${result.engine_resolved})` : ''}
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 text-red-400">
            <AlertTriangle size={13} /> Engine unavailable
          </span>
        )}
        {typeof result.klein_online === 'boolean' && (
          <EngineChip label="Klein" online={result.klein_online} />
        )}
        {typeof result.qwen_online === 'boolean' && (
          <EngineChip label="Qwen (VNCCS)" online={result.qwen_online} />
        )}
        {typeof result.facedetailer_online === 'boolean' && (
          <EngineChip label="FaceDetailer" online={result.facedetailer_online} />
        )}
        {typeof result.seedvr2_online === 'boolean' && (
          <span
            className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full ${
              result.seedvr2_online ? 'text-emerald-400 bg-emerald-950/30' : 'text-gray-500 bg-gray-800/60'
            }`}
          >
            {result.seedvr2_online ? <CheckCircle2 size={11} /> : <AlertTriangle size={11} />}
            SeedVR2 {result.seedvr2_online ? 'online' : 'offline'}
          </span>
        )}
        {typeof result.gan_upscale_online === 'boolean' && (
          <span
            className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full ${
              result.gan_upscale_online ? 'text-emerald-400 bg-emerald-950/30' : 'text-gray-500 bg-gray-800/60'
            }`}
          >
            {result.gan_upscale_online ? <CheckCircle2 size={11} /> : <AlertTriangle size={11} />}
            GAN upscale {result.gan_upscale_online ? 'online' : 'offline'}
          </span>
        )}
      </div>
      {!!result.warnings?.length && (
        <div className="flex flex-col gap-1">
          {result.warnings.map((w, i) => (
            <div
              key={i}
              className="flex items-start gap-1.5 text-xs text-amber-300 bg-amber-950/20 border border-amber-900/40 rounded px-2 py-1"
            >
              <AlertTriangle size={12} className="shrink-0 mt-0.5" />
              <span>{w}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
