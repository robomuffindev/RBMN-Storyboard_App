import {
  Loader2,
  CheckCircle,
  XCircle,
  Eye,
  Minimize2,
  Maximize2,
  X,
} from 'lucide-react';

interface AutoGenStatusBarProps {
  projectId: string;
  batchRunId: string | null;
  status: string; // 'running' | 'done' | 'failed' | 'cancelled' | 'idle'
  mode: string;
  completedScenes: number;
  totalScenes: number;
  currentStep: string | null;
  currentSceneName: string | null;
  onNavigateToDetail: () => void;
  onDismiss: () => void;
  minimized: boolean;
  onToggleMinimize: () => void;
}

function StatusIcon({ status, size = 16 }: { status: string; size?: number }) {
  switch (status) {
    case 'running':
      return <Loader2 size={size} className="text-blue-400 animate-spin" />;
    case 'done':
    case 'completed':
      return <CheckCircle size={size} className="text-green-400" />;
    case 'failed':
      return <XCircle size={size} className="text-red-400" />;
    case 'cancelled':
      return <XCircle size={size} className="text-yellow-400" />;
    default:
      return null;
  }
}

export default function AutoGenStatusBar({
  projectId: _projectId,
  batchRunId,
  status,
  mode,
  completedScenes,
  totalScenes,
  currentStep,
  currentSceneName,
  onNavigateToDetail,
  onDismiss,
  minimized,
  onToggleMinimize,
}: AutoGenStatusBarProps) {
  if (status === 'idle') return null;

  const pct = totalScenes > 0 ? Math.round((completedScenes / totalScenes) * 100) : 0;
  const isRunning = status === 'running';
  const isTerminal = status === 'done' || status === 'completed' || status === 'failed' || status === 'cancelled';

  const terminalMessage = (() => {
    switch (status) {
      case 'done': case 'completed': return `Completed ${completedScenes} scenes`;
      case 'failed': return `Failed after ${completedScenes}/${totalScenes} scenes`;
      case 'cancelled': return `Cancelled at ${completedScenes}/${totalScenes} scenes`;
      default: return '';
    }
  })();

  /* ─── Minimized Pill ─── */
  if (minimized) {
    return (
      <button
        onClick={onToggleMinimize}
        className="fixed bottom-4 right-4 z-[9000] flex items-center gap-2 px-3 py-2 rounded-full
          bg-gray-900/95 border border-purple-500/30 shadow-lg shadow-purple-500/10
          backdrop-blur hover:border-purple-500/50 transition-all duration-300 cursor-pointer group"
      >
        <StatusIcon status={status} size={14} />
        <span className="text-xs font-mono text-gray-300 font-medium">{pct}%</span>
        <Maximize2 size={12} className="text-gray-500 group-hover:text-gray-300 transition-colors" />
      </button>
    );
  }

  /* ─── Expanded Bar ─── */
  return (
    <div
      className="fixed bottom-0 left-0 right-0 z-[9000] transition-all duration-300"
    >
      <div
        className="mx-auto max-w-5xl px-4 pb-4"
      >
        <div
          className="bg-gray-900/95 backdrop-blur border border-purple-500/25 rounded-xl shadow-lg shadow-purple-500/10
            px-4 py-3 flex items-center gap-4 flex-wrap sm:flex-nowrap"
        >
          {/* Left: Status icon + mode */}
          <div className="flex items-center gap-2 shrink-0">
            <StatusIcon status={status} size={18} />
            <span className="text-sm font-medium text-gray-300 capitalize whitespace-nowrap">
              {mode.replace(/_/g, ' ')}
            </span>
          </div>

          {/* Center: Progress */}
          <div className="flex-1 min-w-0 flex flex-col gap-1">
            {isRunning ? (
              <>
                <div className="flex items-center gap-3">
                  <div className="flex-1 h-1.5 bg-gray-800 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-blue-500 rounded-full transition-all duration-700 ease-out"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <span className="text-xs font-mono text-gray-400 shrink-0">
                    Scene {completedScenes + 1} of {totalScenes}
                  </span>
                </div>
                <div className="flex items-center gap-1.5 min-w-0">
                  {currentSceneName && (
                    <span className="text-xs text-purple-400 shrink-0">{currentSceneName}:</span>
                  )}
                  {currentStep && (
                    <span className="text-xs text-gray-500 truncate">{currentStep}</span>
                  )}
                </div>
              </>
            ) : isTerminal ? (
              <span className="text-sm text-gray-400">{terminalMessage}</span>
            ) : null}
          </div>

          {/* Right: Actions */}
          <div className="flex items-center gap-1.5 shrink-0">
            {batchRunId && (
              <button
                onClick={onNavigateToDetail}
                className="px-3 py-1.5 bg-purple-600/20 hover:bg-purple-600/30 border border-purple-500/30 hover:border-purple-500/50
                  text-purple-300 rounded-lg text-xs font-medium transition-colors flex items-center gap-1.5"
              >
                <Eye size={12} />
                View Details
              </button>
            )}
            <button
              onClick={onToggleMinimize}
              className="p-1.5 hover:bg-gray-800 rounded-lg transition-colors text-gray-500 hover:text-gray-300"
              title="Minimize"
            >
              <Minimize2 size={14} />
            </button>
            <button
              onClick={onDismiss}
              className="p-1.5 hover:bg-gray-800 rounded-lg transition-colors text-gray-500 hover:text-gray-300"
              title="Dismiss"
            >
              <X size={14} />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
