/**
 * SceneDeleteModal — confirms scene deletion + lets the user pick how
 * the deleted time slot gets redistributed across neighboring scenes.
 *
 * Three options:
 *   • merge into previous (default)  — prev.end_time extends to deleted.end_time
 *   • merge into next                — next.start_time moves back to deleted.start_time
 *   • leave a gap                    — no neighbor change; export gets a silent stretch
 *
 * First scene auto-disables "previous"; last scene auto-disables "next".
 * Solo scene shows only "leave a gap" + a notice that timing is preserved.
 *
 * The dialog also surfaces the lyrics in the deleted range (when narration
 * mode has word-level timings available) so the user can see what's about
 * to be absorbed.
 */
import { useState, useMemo } from 'react';
import { createPortal } from 'react-dom';
import type { SceneMergeTarget } from '@/api/client';

interface SceneLike {
  id: string;
  name: string;
  start_time: number;
  end_time: number;
}

interface WordLike {
  word: string;
  start: number;
  end: number;
}

interface Props {
  scene: SceneLike;
  prevScene: SceneLike | null;
  nextScene: SceneLike | null;
  /** Whisper words for the entire project; we'll filter to the deleted range. */
  allWords?: WordLike[];
  onConfirm: (target: SceneMergeTarget) => Promise<void> | void;
  onCancel: () => void;
}

function fmtTime(t: number): string {
  const m = Math.floor(t / 60);
  const s = (t % 60).toFixed(2);
  return `${m.toString().padStart(2, '0')}:${s.padStart(5, '0')}`;
}

export default function SceneDeleteModal({
  scene,
  prevScene,
  nextScene,
  allWords,
  onConfirm,
  onCancel,
}: Props) {
  // Default the radio to whatever option is available, preferring "previous"
  const initialTarget: SceneMergeTarget = prevScene
    ? 'previous'
    : nextScene
      ? 'next'
      : 'gap';
  const [target, setTarget] = useState<SceneMergeTarget>(initialTarget);
  const [busy, setBusy] = useState(false);

  const duration = Math.max(0, scene.end_time - scene.start_time);

  const lyricsInRange = useMemo(() => {
    if (!allWords || allWords.length === 0) return '';
    return allWords
      .filter(
        (w) =>
          w.start >= scene.start_time && w.end <= scene.end_time + 0.0001,
      )
      .map((w) => w.word)
      .join(' ')
      .trim();
  }, [allWords, scene.start_time, scene.end_time]);

  const handleConfirm = async () => {
    setBusy(true);
    try {
      await onConfirm(target);
    } finally {
      setBusy(false);
    }
  };

  return createPortal(
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.75)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 9500,
      }}
      onClick={() => !busy && onCancel()}
    >
      <div
        style={{
          background: '#0f172a',
          border: '1px solid #334155',
          borderRadius: '0.75rem',
          width: '520px',
          maxWidth: '95vw',
          maxHeight: '90vh',
          overflow: 'auto',
          padding: '1.25rem 1.5rem',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ fontSize: '1.05rem', fontWeight: 600, color: '#f8fafc', marginBottom: '0.25rem' }}>
          Delete <span style={{ color: '#fbbf24' }}>{scene.name || 'Untitled'}</span>?
        </div>
        <div style={{ fontSize: '0.8rem', color: '#94a3b8', marginBottom: '0.875rem' }}>
          {fmtTime(scene.start_time)} → {fmtTime(scene.end_time)} ·{' '}
          <strong style={{ color: '#cbd5e1' }}>{duration.toFixed(2)}s</strong>
        </div>

        {/* Lyrics preview */}
        {lyricsInRange && (
          <div
            style={{
              background: '#1e293b',
              border: '1px solid #334155',
              borderRadius: '0.375rem',
              padding: '0.5rem 0.75rem',
              marginBottom: '0.875rem',
            }}
          >
            <div style={{ fontSize: '0.65rem', textTransform: 'uppercase', letterSpacing: '0.05em', color: '#64748b', marginBottom: '0.25rem' }}>
              Lyrics / narration in this range
            </div>
            <div style={{ fontSize: '0.8rem', color: '#cbd5e1', fontStyle: 'italic', lineHeight: 1.4 }}>
              "{lyricsInRange.length > 280 ? lyricsInRange.slice(0, 280) + '…' : lyricsInRange}"
            </div>
          </div>
        )}

        {/* Merge target options */}
        {(prevScene || nextScene) && (
          <>
            <div style={{ fontSize: '0.8rem', color: '#e2e8f0', fontWeight: 500, marginBottom: '0.5rem' }}>
              Where should the <strong>{duration.toFixed(2)}s</strong> of timeline go?
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.375rem', marginBottom: '1rem' }}>
              {/* Previous */}
              <label
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: '0.5rem',
                  padding: '0.5rem 0.625rem',
                  background: target === 'previous' ? '#1e3a8a' : '#1e293b',
                  border: `1px solid ${target === 'previous' ? '#3b82f6' : '#334155'}`,
                  borderRadius: '0.375rem',
                  cursor: prevScene ? 'pointer' : 'not-allowed',
                  opacity: prevScene ? 1 : 0.45,
                }}
              >
                <input
                  type="radio"
                  name="merge"
                  value="previous"
                  checked={target === 'previous'}
                  onChange={() => setTarget('previous')}
                  disabled={!prevScene}
                  style={{ marginTop: '0.25rem' }}
                />
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: '0.85rem', color: '#f1f5f9', fontWeight: 500 }}>
                    Add to previous scene {!prevScene && '(no previous scene)'}
                  </div>
                  {prevScene && (
                    <div style={{ fontSize: '0.7rem', color: '#94a3b8', marginTop: '0.125rem' }}>
                      <strong style={{ color: '#cbd5e1' }}>{prevScene.name || 'Untitled'}</strong>{' '}
                      extends to <strong style={{ color: '#cbd5e1' }}>{fmtTime(scene.end_time)}</strong>{' '}
                      ({(scene.end_time - prevScene.start_time).toFixed(2)}s total)
                    </div>
                  )}
                </div>
              </label>

              {/* Next */}
              <label
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: '0.5rem',
                  padding: '0.5rem 0.625rem',
                  background: target === 'next' ? '#1e3a8a' : '#1e293b',
                  border: `1px solid ${target === 'next' ? '#3b82f6' : '#334155'}`,
                  borderRadius: '0.375rem',
                  cursor: nextScene ? 'pointer' : 'not-allowed',
                  opacity: nextScene ? 1 : 0.45,
                }}
              >
                <input
                  type="radio"
                  name="merge"
                  value="next"
                  checked={target === 'next'}
                  onChange={() => setTarget('next')}
                  disabled={!nextScene}
                  style={{ marginTop: '0.25rem' }}
                />
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: '0.85rem', color: '#f1f5f9', fontWeight: 500 }}>
                    Add to next scene {!nextScene && '(no next scene)'}
                  </div>
                  {nextScene && (
                    <div style={{ fontSize: '0.7rem', color: '#94a3b8', marginTop: '0.125rem' }}>
                      <strong style={{ color: '#cbd5e1' }}>{nextScene.name || 'Untitled'}</strong>{' '}
                      now starts at <strong style={{ color: '#cbd5e1' }}>{fmtTime(scene.start_time)}</strong>{' '}
                      ({(nextScene.end_time - scene.start_time).toFixed(2)}s total)
                    </div>
                  )}
                </div>
              </label>

              {/* Gap */}
              <label
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: '0.5rem',
                  padding: '0.5rem 0.625rem',
                  background: target === 'gap' ? '#1e3a8a' : '#1e293b',
                  border: `1px solid ${target === 'gap' ? '#3b82f6' : '#334155'}`,
                  borderRadius: '0.375rem',
                  cursor: 'pointer',
                }}
              >
                <input
                  type="radio"
                  name="merge"
                  value="gap"
                  checked={target === 'gap'}
                  onChange={() => setTarget('gap')}
                  style={{ marginTop: '0.25rem' }}
                />
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: '0.85rem', color: '#f1f5f9', fontWeight: 500 }}>
                    Just delete — leave a {duration.toFixed(2)}s gap
                  </div>
                  <div style={{ fontSize: '0.7rem', color: '#94a3b8', marginTop: '0.125rem' }}>
                    Other scenes don't move. Export shows a silent freeze-frame on the previous scene during this stretch.
                  </div>
                </div>
              </label>
            </div>
          </>
        )}

        {/* Single-scene case */}
        {!prevScene && !nextScene && (
          <div
            style={{
              background: '#1e293b',
              border: '1px solid #fbbf24',
              borderRadius: '0.375rem',
              padding: '0.5rem 0.75rem',
              marginBottom: '0.875rem',
              fontSize: '0.8rem',
              color: '#fde68a',
            }}
          >
            This is the only scene in the project. Deleting it will leave the project scene-less.
          </div>
        )}

        {/* Asset note */}
        <div
          style={{
            fontSize: '0.7rem',
            color: '#64748b',
            background: '#0a1224',
            border: '1px solid #1e293b',
            borderRadius: '0.375rem',
            padding: '0.5rem 0.625rem',
            marginBottom: '1rem',
            lineHeight: 1.4,
          }}
        >
          ℹ️ Generated images / videos for this scene stay in the asset library — you can reuse them.
          {(target === 'previous' || target === 'next') && (
            <>
              <br />
              The absorbing scene's existing video (if any) won't be re-rendered automatically — its duration may not match the new range exactly. You can regenerate it from the Video tab afterward.
            </>
          )}
        </div>

        {/* Action buttons */}
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button
            onClick={onCancel}
            disabled={busy}
            style={{
              flex: 1,
              padding: '0.5rem 0.75rem',
              background: '#1e293b',
              border: '1px solid #475569',
              borderRadius: '0.375rem',
              color: '#cbd5e1',
              fontSize: '0.875rem',
              cursor: busy ? 'wait' : 'pointer',
            }}
          >
            Cancel
          </button>
          <button
            onClick={handleConfirm}
            disabled={busy}
            style={{
              flex: 1,
              padding: '0.5rem 0.75rem',
              background: busy ? '#7f1d1d' : '#dc2626',
              border: '1px solid #dc2626',
              borderRadius: '0.375rem',
              color: '#fff',
              fontSize: '0.875rem',
              fontWeight: 500,
              cursor: busy ? 'wait' : 'pointer',
            }}
          >
            {busy ? 'Deleting…' : 'Delete Scene'}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
