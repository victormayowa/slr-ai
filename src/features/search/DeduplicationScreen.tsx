import { WorkspaceStageGate } from '../project/WorkspaceStageGate';
import { useWorkspace } from '../project/workspaceContext';

export function DeduplicationScreen() {
  const { handleRunDedup, dedupLoading, prisma } = useWorkspace();
  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
      <h3 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>4. Deduplication</h3>
      <WorkspaceStageGate stage="search" />
      <button className="btn-primary" onClick={handleRunDedup} disabled={dedupLoading} style={{ marginTop: '24px', padding: '16px 32px' }}>
        {dedupLoading ? 'Analyzing IDs...' : 'Run Automated Deduplication'}
      </button>
      {prisma !== null && prisma.duplicates_removed > 0 && (
        <div style={{ marginTop: '24px', color: '#10b981', fontSize: '1.2rem' }}>
          ✓ Removed {prisma.duplicates_removed} duplicates. Ready for Abstract Screening.
        </div>
      )}
    </section>
  );
}
