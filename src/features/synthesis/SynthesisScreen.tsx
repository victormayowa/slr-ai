import { WorkspaceStageGate } from '../project/WorkspaceStageGate';
import { useWorkspace } from '../project/workspaceContext';

export function SynthesisScreen() {
  const { handleRunMetaAnalysis, metaLoading, metaReport, aiModelName } = useWorkspace();
  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px', border: '1px solid var(--accent-primary)' }}>
      <h3 style={{ marginBottom: '16px', color: 'var(--accent-primary)' }}>10. AI Narrative Synthesis</h3>
      <WorkspaceStageGate stage="synthesis" />
      <p style={{ color: 'var(--text-secondary)', marginBottom: '16px' }}>An AI-written summary of the extracted data and risk of bias for the papers you accepted. No statistical meta-analysis is run: there are no pooled estimates or heterogeneity statistics.</p>
      <button className="btn-primary" onClick={handleRunMetaAnalysis} disabled={metaLoading} style={{ background: 'linear-gradient(135deg, #10b981, #059669)', padding: '16px 32px' }}>
        {metaLoading ? `Synthesizing with ${aiModelName}...` : 'Synthesize Outcomes & Generate Report'}
      </button>
      {metaReport && (
        <div className="animate-fade-in" style={{ padding: '24px', background: 'rgba(0,0,0,0.3)', marginTop: '24px', borderRadius: '12px' }}>
          <h4 style={{ color: 'var(--accent-secondary)' }}>AI Narrative Synthesis</h4>
          <pre style={{ whiteSpace: 'pre-wrap', fontFamily: 'system-ui, -apple-system, sans-serif', fontSize: '0.95rem', lineHeight: '1.6', color: 'var(--text-secondary)' }}>
            {metaReport}
          </pre>
        </div>
      )}
    </section>
  );
}
