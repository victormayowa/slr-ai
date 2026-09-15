import { modelDisplayName } from '../../api/ai';
import type { RecordBrief } from '../../api/review';
import { ProgressBar } from '../../components/ProgressBar';
import { WorkspaceStageGate } from '../project/WorkspaceStageGate';
import { useWorkspace } from '../project/workspaceContext';

function RecordSummary({ record }: { record: RecordBrief }) {
  const details = [record.authors, record.year, record.source, record.doi].filter(Boolean).join(' · ');
  return (
    <div style={{ marginTop: '8px' }}>
      <strong>{record.title}</strong>
      <div style={{ color: 'var(--text-secondary)', fontSize: '0.8rem' }}>{details}</div>
    </div>
  );
}

export function DeduplicationScreen() {
  const {
    handleRunDedup, dedupLoading, prisma, currentProject, similar, similarLoading, similarProgress, handleFindSimilar, handleMarkDuplicate,
  } = useWorkspace();
  const embeddingModel = currentProject?.embedding_model ?? null;

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
      <h3 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>Deduplication</h3>
      <WorkspaceStageGate stage="search" />
      <button className="btn-primary" onClick={handleRunDedup} disabled={dedupLoading} style={{ marginTop: '24px', padding: '16px 32px' }}>
        {dedupLoading ? 'Analyzing IDs...' : 'Run Automated Deduplication'}
      </button>
      {prisma !== null && prisma.duplicates_removed > 0 && (
        <div style={{ marginTop: '24px', color: '#10b981', fontSize: '1.2rem' }}>
          ✓ Removed {prisma.duplicates_removed} duplicates. Ready for Abstract Screening.
        </div>
      )}

      <div style={{ marginTop: '40px', borderTop: '1px solid rgba(255,255,255,0.1)', paddingTop: '24px' }}>
        <h4 style={{ margin: '0 0 8px', color: 'var(--text-primary)' }}>Possible duplicates by meaning</h4>
        <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginTop: 0 }}>
          Finds records whose titles and abstracts say nearly the same thing, such as one study indexed differently by two
          databases. You decide which records are duplicates.
          {embeddingModel && ` Titles and abstracts are sent to ${modelDisplayName(embeddingModel)}${embeddingModel.data_location ? ` (${embeddingModel.data_location})` : ''}.`}
        </p>
        <button className="btn-glass" onClick={handleFindSimilar} disabled={similarLoading || !embeddingModel} style={{ padding: '12px 24px' }}>
          {similarLoading ? 'Comparing records...' : 'Find possible duplicates'}
        </button>
        {similarProgress !== null && <ProgressBar progress={similarProgress} label="Comparing records..." />}

        {similar && similar.pairs.length === 0 && (
          <p style={{ color: 'var(--text-secondary)', marginTop: '16px' }}>No likely duplicates among {similar.embedded_records} records.</p>
        )}
        {similar && similar.pairs.length > 0 && (
          <ul aria-label="Possible duplicate pairs" style={{ listStyle: 'none', padding: 0, marginTop: '16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {similar.pairs.map(pair => (
              <li key={`${pair.record.id}-${pair.other.id}`} style={{ background: 'rgba(0,0,0,0.3)', borderRadius: '12px', padding: '16px' }}>
                <div style={{ color: '#f59e0b', fontSize: '0.85rem' }}>{Math.round(pair.similarity * 100)}% similar</div>
                <RecordSummary record={pair.record} />
                <RecordSummary record={pair.other} />
                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginTop: '12px' }}>
                  <button className="btn-glass" style={{ padding: '6px 12px', fontSize: '0.8rem' }} onClick={() => handleMarkDuplicate(pair.other.id, pair.record.id)}>
                    Keep the first, mark the second as a duplicate
                  </button>
                  <button className="btn-glass" style={{ padding: '6px 12px', fontSize: '0.8rem' }} onClick={() => handleMarkDuplicate(pair.record.id, pair.other.id)}>
                    Keep the second, mark the first as a duplicate
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
