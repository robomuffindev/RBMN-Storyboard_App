import { useState, useEffect, useRef, useCallback } from 'react';
import { Minimize2, Maximize2, X, Loader2, CheckCircle2, AlertCircle } from 'lucide-react';
import { getFlowProgress } from '@/api/client';

interface FlowGenerationStatusProps {
  projectId: string;
  isGenerating: boolean;
  isNarration: boolean;
  onDismiss: () => void;
}

export default function FlowGenerationStatus({
  projectId,
  isGenerating,
  isNarration,
  onDismiss,
}: FlowGenerationStatusProps) {
  const [minimized, setMinimized] = useState(false);
  const [status, setStatus] = useState<string>('idle');
  const [totalScenes, setTotalScenes] = useState(0);
  const [totalBatches, setTotalBatches] = useState(0);
  const [completedBatches, setCompletedBatches] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const prevIsGenerating = useRef(false);

  // Stable poll function — does NOT depend on isGenerating (uses ref instead)
  const isGeneratingRef = useRef(isGenerating);
  isGeneratingRef.current = isGenerating;

  const poll = useCallback(async () => {
    try {
      const res = await getFlowProgress(projectId);
      const d = res.data;

      // If backend returns "idle" but we know generation is in progress,
      // it means the POST handler hasn't initialized progress yet — keep
      // our local "running" status and keep polling.
      if (d.status === 'idle' && isGeneratingRef.current) {
        return; // don't update state, don't stop polling
      }

      setStatus(d.status);
      setTotalScenes(d.total_scenes);
      setTotalBatches(d.total_batches);
      setCompletedBatches(d.completed_batches);
      setMessage(d.current_message);
      setError(d.error);
      if (d.started_at) setStartedAt(d.started_at);

      // Only stop polling on terminal states (done or failed)
      if (d.status === 'done' || d.status === 'failed') {
        if (pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
      }
    } catch {
      // ignore transient errors
    }
  }, [projectId]);

  // Start polling when generation begins
  useEffect(() => {
    if (isGenerating && !prevIsGenerating.current) {
      // Transition: not generating → generating — start fresh
      setStatus('running');
      setError(null);
      setMessage('Starting flow generation...');
      setStartedAt(Date.now() / 1000);
      // Delay the first poll slightly to give backend time to init progress
      const initDelay = setTimeout(() => {
        poll();
        pollRef.current = setInterval(poll, 1500);
      }, 500);
      prevIsGenerating.current = true;
      return () => {
        clearTimeout(initDelay);
        if (pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
      };
    }

    if (!isGenerating && prevIsGenerating.current) {
      // Transition: generating → done — the POST has returned.
      // Do one final poll to pick up the terminal status from backend.
      prevIsGenerating.current = false;
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
      poll();
    }
    return undefined;
  }, [isGenerating, poll]);

  // Elapsed timer — run while status is running OR idle+isGenerating (starting up)
  const isActive = status === 'running' || (status === 'idle' && isGenerating);
  useEffect(() => {
    if (isActive && startedAt) {
      const tick = () => setElapsed(Math.floor(Date.now() / 1000 - startedAt));
      tick();
      timerRef.current = setInterval(tick, 1000);
    } else if (!isActive) {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [isActive, startedAt]);

  // Don't render if idle and not generating
  if (status === 'idle' && !isGenerating) return null;

  const flowLabel = isNarration ? 'Story Flow' : 'Video Flow';
  const mins = Math.floor(elapsed / 60);
  const secs = elapsed % 60;
  const timeStr = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
  const pct = totalBatches > 0 ? Math.round((completedBatches / totalBatches) * 100) : 0;

  // Effective display status: treat idle+isGenerating as "running" (starting up)
  const displayStatus = (status === 'idle' && isGenerating) ? 'running' : status;

  // Minimized pill
  if (minimized) {
    const pillColor =
      displayStatus === 'running'
        ? 'bg-purple-600/90'
        : displayStatus === 'done'
          ? 'bg-green-600/90'
          : 'bg-red-600/90';

    return (
      <div
        className={`fixed bottom-4 right-4 z-50 ${pillColor} backdrop-blur-sm rounded-full px-4 py-2 shadow-lg border border-white/10 flex items-center gap-2 cursor-pointer hover:scale-105 transition-transform`}
        onClick={() => setMinimized(false)}
      >
        {displayStatus === 'running' && <Loader2 size={14} className="animate-spin text-white" />}
        {displayStatus === 'done' && <CheckCircle2 size={14} className="text-green-200" />}
        {displayStatus === 'failed' && <AlertCircle size={14} className="text-red-200" />}
        <span className="text-xs font-medium text-white">
          {flowLabel} {displayStatus === 'running' ? `${pct}%` : displayStatus === 'done' ? 'Done' : 'Failed'}
        </span>
        {displayStatus === 'running' && (
          <span className="text-[10px] text-white/60">{timeStr}</span>
        )}
        <Maximize2 size={12} className="text-white/60" />
      </div>
    );
  }

  // Full status window
  return (
    <div className="fixed bottom-4 right-4 z-50 w-80 bg-gray-900/95 backdrop-blur-sm border border-gray-700 rounded-lg shadow-2xl overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 bg-gray-800/80 border-b border-gray-700">
        <div className="flex items-center gap-2">
          {displayStatus === 'running' && <Loader2 size={14} className="animate-spin text-purple-400" />}
          {displayStatus === 'done' && <CheckCircle2 size={14} className="text-green-400" />}
          {displayStatus === 'failed' && <AlertCircle size={14} className="text-red-400" />}
          <span className="text-xs font-semibold text-gray-200">
            {flowLabel} Generation
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setMinimized(true)}
            className="p-1 text-gray-400 hover:text-white transition-colors rounded hover:bg-gray-700"
            title="Minimize"
          >
            <Minimize2 size={14} />
          </button>
          {displayStatus !== 'running' && (
            <button
              onClick={onDismiss}
              className="p-1 text-gray-400 hover:text-white transition-colors rounded hover:bg-gray-700"
              title="Close"
            >
              <X size={14} />
            </button>
          )}
        </div>
      </div>

      {/* Body */}
      <div className="p-3 space-y-2.5">
        {/* Progress bar */}
        <div className="relative h-2 bg-gray-800 rounded-full overflow-hidden">
          <div
            className={`absolute inset-y-0 left-0 rounded-full transition-all duration-500 ${
              displayStatus === 'done'
                ? 'bg-green-500'
                : displayStatus === 'failed'
                  ? 'bg-red-500'
                  : 'bg-purple-500'
            }`}
            style={{ width: `${displayStatus === 'done' ? 100 : pct}%` }}
          />
          {displayStatus === 'running' && pct < 100 && (
            <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/10 to-transparent animate-shimmer" />
          )}
        </div>

        {/* Stats row */}
        <div className="flex items-center justify-between text-[11px]">
          <span className="text-gray-400">
            {totalScenes > 0 && `${totalScenes} scenes`}
            {totalBatches > 1 && ` · ${completedBatches}/${totalBatches} batches`}
          </span>
          <span className="text-gray-500 font-mono">{timeStr}</span>
        </div>

        {/* Status message */}
        {message && (
          <div className={`text-[11px] leading-relaxed ${
            displayStatus === 'failed' ? 'text-red-400' : 'text-gray-300'
          }`}>
            {message}
          </div>
        )}

        {/* Error detail */}
        {error && displayStatus === 'failed' && (
          <div className="text-[10px] text-red-400/80 bg-red-900/20 rounded px-2 py-1.5 border border-red-800/30 max-h-16 overflow-y-auto">
            {error}
          </div>
        )}
      </div>
    </div>
  );
}
