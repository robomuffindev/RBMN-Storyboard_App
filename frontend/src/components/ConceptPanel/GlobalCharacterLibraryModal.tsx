/**
 * Global Character Library — browse + import dialog.
 *
 * Renders as a portal overlay with a grid of saved characters.  Search,
 * tag filter, and source-project filter narrow the list.  Clicking
 * "Add to project" calls /api/global-characters/{id}/import which copies
 * the entry into the target project's `settings.characters`.  After a
 * successful import, the parent ConceptPanel re-fetches concept data so
 * the new character shows up immediately.
 *
 * Copy semantics — the imported character is independent from the
 * library entry; editing one does NOT mutate the other.
 */
import { useState, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { handleImgError } from '@/utils/brokenImage';
import {
  listGlobalCharacters,
  listGlobalCharacterTags,
  importGlobalCharacterToProject,
  deleteGlobalCharacter,
  type GlobalCharacter,
} from '@/api/client';

interface Props {
  projectId: string;
  onClose: () => void;
  onImported?: (character_index: number) => void;
}

export function GlobalCharacterLibraryModal({ projectId, onClose, onImported }: Props) {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState('');
  const [activeTag, setActiveTag] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const { data: characters = [], isLoading } = useQuery({
    queryKey: ['global-characters', search, activeTag],
    queryFn: () =>
      listGlobalCharacters({
        search: search || undefined,
        tag: activeTag || undefined,
      }).then((r) => r.data),
  });

  const { data: tags = [] } = useQuery({
    queryKey: ['global-character-tags'],
    queryFn: () => listGlobalCharacterTags().then((r) => r.data),
  });

  const importMutation = useMutation({
    mutationFn: async (id: string) => {
      setBusyId(id);
      try {
        const r = await importGlobalCharacterToProject(id, projectId);
        return r.data;
      } finally {
        setBusyId(null);
      }
    },
    onSuccess: (data) => {
      // Tell the parent so it can refresh concept characters in the UI.
      onImported?.(data.character_index);
      queryClient.invalidateQueries({ queryKey: ['concept', projectId] });
      queryClient.invalidateQueries({ queryKey: ['project', projectId] });
    },
    onError: (err: any) => {
      alert(`Import failed: ${err?.message || err}`);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      if (!confirm('Delete this character from the global library? Projects that already imported it are not affected.')) {
        throw new Error('cancelled');
      }
      await deleteGlobalCharacter(id);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['global-characters'] });
      queryClient.invalidateQueries({ queryKey: ['global-character-tags'] });
    },
    onError: (err: any) => {
      if (err?.message !== 'cancelled') alert(`Delete failed: ${err?.message || err}`);
    },
  });

  const visibleCount = characters.length;

  // Group by source project for a small left-hand summary
  const projectGroups = useMemo(() => {
    const groups = new Map<string, number>();
    for (const c of characters) {
      const key = c.source_project_name || '(unknown project)';
      groups.set(key, (groups.get(key) || 0) + 1);
    }
    return Array.from(groups.entries()).sort((a, b) => b[1] - a[1]);
  }, [characters]);

  return createPortal(
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.75)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 9000,
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: '#0f172a',
          border: '1px solid #334155',
          borderRadius: '0.75rem',
          width: '900px',
          maxWidth: '95vw',
          maxHeight: '90vh',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div
          style={{
            padding: '1rem 1.25rem',
            borderBottom: '1px solid #334155',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '1rem',
          }}
        >
          <div>
            <div style={{ fontSize: '1.1rem', fontWeight: 600, color: '#f8fafc' }}>
              🎭 Global Character Library
            </div>
            <div style={{ fontSize: '0.75rem', color: '#94a3b8', marginTop: '0.125rem' }}>
              Reusable characters saved across projects · {visibleCount} {visibleCount === 1 ? 'entry' : 'entries'}
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              background: 'transparent',
              border: '1px solid #475569',
              color: '#cbd5e1',
              padding: '0.375rem 0.75rem',
              borderRadius: '0.375rem',
              cursor: 'pointer',
              fontSize: '0.875rem',
            }}
          >
            Close
          </button>
        </div>

        {/* Filter bar */}
        <div
          style={{
            padding: '0.75rem 1.25rem',
            borderBottom: '1px solid #334155',
            display: 'flex',
            gap: '0.5rem',
            alignItems: 'center',
            flexWrap: 'wrap',
          }}
        >
          <input
            type="text"
            placeholder="Search by name or description…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{
              flex: 1,
              minWidth: '220px',
              padding: '0.5rem 0.625rem',
              background: '#1e293b',
              border: '1px solid #475569',
              borderRadius: '0.375rem',
              color: '#f1f5f9',
              fontSize: '0.875rem',
            }}
          />
          <div style={{ display: 'flex', gap: '0.25rem', flexWrap: 'wrap', alignItems: 'center' }}>
            <button
              onClick={() => setActiveTag(null)}
              style={{
                fontSize: '0.75rem',
                padding: '0.25rem 0.5rem',
                borderRadius: '0.25rem',
                border: '1px solid #475569',
                background: activeTag === null ? '#7c3aed' : '#1e293b',
                color: activeTag === null ? '#fff' : '#cbd5e1',
                cursor: 'pointer',
              }}
            >
              All
            </button>
            {tags.map((t) => (
              <button
                key={t}
                onClick={() => setActiveTag(activeTag === t ? null : t)}
                style={{
                  fontSize: '0.75rem',
                  padding: '0.25rem 0.5rem',
                  borderRadius: '0.25rem',
                  border: '1px solid #475569',
                  background: activeTag === t ? '#7c3aed' : '#1e293b',
                  color: activeTag === t ? '#fff' : '#cbd5e1',
                  cursor: 'pointer',
                }}
              >
                {t}
              </button>
            ))}
          </div>
        </div>

        {/* Body: project group sidebar + grid */}
        <div style={{ display: 'flex', flex: 1, minHeight: 0, overflow: 'hidden' }}>
          {/* Project group sidebar (visible when 2+ source projects) */}
          {projectGroups.length > 1 && (
            <div
              style={{
                width: '180px',
                borderRight: '1px solid #334155',
                padding: '0.75rem 0.5rem',
                overflowY: 'auto',
                background: '#0a1224',
              }}
            >
              <div style={{ fontSize: '0.65rem', textTransform: 'uppercase', color: '#64748b', marginBottom: '0.5rem', letterSpacing: '0.05em' }}>
                By source project
              </div>
              {projectGroups.map(([proj, count]) => (
                <div
                  key={proj}
                  style={{
                    fontSize: '0.75rem',
                    color: '#cbd5e1',
                    padding: '0.25rem 0.375rem',
                    display: 'flex',
                    justifyContent: 'space-between',
                    gap: '0.5rem',
                  }}
                >
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{proj}</span>
                  <span style={{ color: '#64748b' }}>{count}</span>
                </div>
              ))}
            </div>
          )}

          {/* Grid */}
          <div
            style={{
              flex: 1,
              padding: '0.75rem 1rem',
              overflowY: 'auto',
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
              gap: '0.75rem',
              alignContent: 'start',
            }}
          >
            {isLoading && (
              <div style={{ color: '#94a3b8', padding: '1rem', gridColumn: '1 / -1' }}>Loading library…</div>
            )}
            {!isLoading && characters.length === 0 && (
              <div style={{ color: '#94a3b8', padding: '1rem', gridColumn: '1 / -1', textAlign: 'center' }}>
                No characters in the library yet. Open a character on the Concept tab and click <strong>💾 Save As Asset</strong> to add one.
              </div>
            )}
            {characters.map((c: GlobalCharacter) => (
              <div
                key={c.id}
                style={{
                  border: '1px solid #334155',
                  borderRadius: '0.5rem',
                  background: '#1e293b',
                  overflow: 'hidden',
                  display: 'flex',
                  flexDirection: 'column',
                }}
              >
                <div
                  style={{
                    aspectRatio: '1 / 1',
                    background: '#0a1224',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    overflow: 'hidden',
                  }}
                >
                  {c.image_path ? (
                    <img
                      src={`/api/files/${c.image_path}`}
                      alt={c.name}
                      style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                      onError={handleImgError}
                    />
                  ) : (
                    <span style={{ color: '#475569', fontSize: '0.75rem' }}>(no image)</span>
                  )}
                </div>
                <div style={{ padding: '0.5rem 0.625rem', display: 'flex', flexDirection: 'column', gap: '0.25rem', flex: 1 }}>
                  <div style={{ fontSize: '0.85rem', fontWeight: 600, color: '#f8fafc', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {c.name}
                  </div>
                  {c.tags.length > 0 && (
                    <div style={{ display: 'flex', gap: '0.25rem', flexWrap: 'wrap' }}>
                      {c.tags.slice(0, 4).map((t) => (
                        <span
                          key={t}
                          style={{ fontSize: '0.625rem', padding: '0.063rem 0.375rem', borderRadius: '999px', background: '#334155', color: '#cbd5e1' }}
                        >
                          {t}
                        </span>
                      ))}
                    </div>
                  )}
                  <div style={{ fontSize: '0.65rem', color: '#64748b', marginTop: 'auto' }}>
                    from {c.source_project_name || '(unknown)'}
                  </div>
                  <div style={{ display: 'flex', gap: '0.25rem', marginTop: '0.25rem' }}>
                    <button
                      onClick={() => importMutation.mutate(c.id)}
                      disabled={busyId === c.id}
                      style={{
                        flex: 1,
                        fontSize: '0.75rem',
                        padding: '0.375rem 0.5rem',
                        borderRadius: '0.25rem',
                        border: '1px solid #7c3aed',
                        background: busyId === c.id ? '#5b21b6' : '#7c3aed',
                        color: '#fff',
                        cursor: busyId === c.id ? 'wait' : 'pointer',
                      }}
                    >
                      {busyId === c.id ? 'Adding…' : '+ Add to project'}
                    </button>
                    <button
                      onClick={() => deleteMutation.mutate(c.id)}
                      title="Delete from library (does NOT affect projects already using this character)"
                      style={{
                        fontSize: '0.75rem',
                        padding: '0.375rem 0.5rem',
                        borderRadius: '0.25rem',
                        border: '1px solid #475569',
                        background: '#1e293b',
                        color: '#cbd5e1',
                        cursor: 'pointer',
                      }}
                    >
                      🗑
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>,
    document.body
  );
}
