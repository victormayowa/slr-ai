import { batchLimit } from '../../app/plan';
import { ProgressBar } from '../../components/ProgressBar';
import { WorkspaceStageGate } from '../project/WorkspaceStageGate';
import { useWorkspace } from '../project/workspaceContext';

export function ExtractionScreen() {
  const { handleRunFullTextPipeline, fullTextLoading, fullTextProgress, literatureResults, extractionColumns, goTo } = useWorkspace();
  const extracted = literatureResults.filter(p => p.extracted_data);

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px', border: '1px solid var(--accent-primary)' }}>
      <h3 style={{ marginBottom: '16px', color: 'var(--accent-primary)' }}>Full-Text Screening & Batch Extraction</h3>
      <WorkspaceStageGate stage="extraction" />
      <p style={{ color: 'var(--text-secondary)' }}>Full-text PDF retrieval is not available yet. Extraction currently reads only the title and abstract of each paper you accepted, so verify every value against the full article.</p>
      <button className="btn-primary" onClick={handleRunFullTextPipeline} disabled={fullTextLoading} style={{ marginTop: '24px', background: 'linear-gradient(135deg, #8b5cf6, #6d28d9)', padding: '16px 32px', marginBottom: '24px' }}>
        {fullTextLoading ? 'Extracting from abstracts...' : `Run AI Extraction on Accepted Papers (up to ${batchLimit('fulltext')})`}
      </button>

      {fullTextProgress !== null && <ProgressBar progress={fullTextProgress} label="Extracting Data & Screening..." />}

      {extracted.length > 0 && (
        <>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', marginTop: '24px' }}>Hover over a value to see the passage the AI took it from. "Unverified" means that passage couldn't be found in the record.</p>
          <div style={{ overflowX: 'auto', background: 'rgba(0,0,0,0.3)', borderRadius: '12px', marginTop: '12px' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '0.85rem' }}>
              <thead style={{ background: '#1e293b' }}>
                <tr>
                  <th style={{ padding: '12px', whiteSpace: 'nowrap' }}>Study</th>
                  {extractionColumns.map(col => <th key={col} style={{ padding: '12px' }}>{col}</th>)}
                </tr>
              </thead>
              <tbody>
                {extracted.slice(0, 10).map(p => (
                  <tr key={p.id} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                    <td style={{ padding: '12px', fontWeight: 'bold' }}>{p.title}</td>
                    {extractionColumns.map(col => {
                      const evidence = p.extraction_evidence?.[col];
                      return (
                        <td key={col} style={{ padding: '12px', color: 'var(--text-secondary)' }} title={evidence?.quote ? `Source: “${evidence.quote}”` : undefined}>
                          {p.extracted_data?.[col] || 'Not Reported'}
                          {evidence?.verified === false && <span style={{ display: 'block', color: '#ef4444', fontSize: '0.75rem' }}>Unverified</span>}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button className="btn-primary" onClick={() => goTo('prisma')} style={{ marginTop: '24px' }}>Proceed to PRISMA →</button>
        </>
      )}
    </section>
  );
}
