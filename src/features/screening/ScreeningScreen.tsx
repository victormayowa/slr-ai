import { batchLimit } from '../../app/plan';
import { ProgressBar } from '../../components/ProgressBar';
import { WorkspaceStageGate } from '../project/WorkspaceStageGate';
import { useWorkspace } from '../project/workspaceContext';

const DECISION_BUTTONS = [
  { decision: 'Include', label: '✓ Accept', color: '#10b981', background: 'rgba(16,185,129,0.2)' },
  { decision: 'Undecided', label: '? Undecided', color: '#f59e0b', background: 'rgba(245,158,11,0.2)' },
  { decision: 'Exclude', label: '✕ Reject', color: '#ef4444', background: 'rgba(239,68,68,0.2)' },
] as const;

// The AI's supporting quote, marked by whether it was actually found in the record's title and abstract.
function QuoteCheck({ quote, verified }: { quote: string; verified?: boolean }) {
  const color = verified ? '#10b981' : '#ef4444';
  return (
    <blockquote style={{ margin: '8px 0 0', paddingLeft: '8px', borderLeft: `3px solid ${color}` }}>
      <span style={{ fontStyle: 'italic' }}>“{quote}”</span>
      <div style={{ fontSize: '0.75rem', color }}>
        {verified ? 'Quote found in this record' : 'Quote not found in this record: check it before relying on the suggestion'}
      </div>
    </blockquote>
  );
}

export function ScreeningScreen() {
  const { handleRunAbstractScreening, abstractLoading, abstractProgress, literatureResults, handleUserDecision, goTo } = useWorkspace();
  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
      <h3 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>Abstract Screening</h3>
      <WorkspaceStageGate stage="screening" />

      <button className="btn-primary" onClick={handleRunAbstractScreening} disabled={abstractLoading} style={{ marginBottom: '24px', background: 'linear-gradient(135deg, #10b981, #059669)', padding: '12px 24px' }}>
        {abstractLoading ? 'AI Screening Abstracts...' : `Run Batch AI Abstract Screening (${batchLimit('abstract')} / page)`}
      </button>

      {abstractProgress !== null && <ProgressBar progress={abstractProgress} label="AI Processing Batch..." />}

      {literatureResults.length > 0 && (
        <div style={{ overflowX: 'auto', background: 'rgba(0,0,0,0.3)', borderRadius: '12px' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '0.9rem' }}>
            <thead style={{ background: '#1e293b' }}>
              <tr>
                <th style={{ padding: '12px', width: '30%' }}>Title / Abstract</th>
                <th style={{ padding: '12px', width: '30%' }}>AI Reasoning</th>
                <th style={{ padding: '12px', width: '15%' }}>AI Decision</th>
                <th style={{ padding: '12px', width: '25%' }}>Your Decision</th>
              </tr>
            </thead>
            <tbody>
              {literatureResults.slice(0, batchLimit('abstract')).map(p => (
                <tr key={p.id} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                  <td style={{ padding: '12px' }}>
                    <strong>{p.title}</strong>
                    <div style={{ color: 'var(--text-secondary)', marginTop: '8px', maxHeight: '80px', overflowY: 'auto' }}>
                      {p.abstract || "Abstract not provided in standard search feed."}
                    </div>
                  </td>
                  <td style={{ padding: '12px', color: 'var(--text-secondary)' }}>
                    {p.ai_error ? `AI error: ${p.ai_error}` : p.ai_reasoning || "-"}
                    {!p.ai_error && p.ai_quote && <QuoteCheck quote={p.ai_quote} verified={p.ai_quote_verified} />}
                  </td>
                  <td style={{ padding: '12px', fontWeight: 'bold', color: p.ai_decision === 'Include' ? '#10b981' : p.ai_decision === 'Exclude' ? '#ef4444' : '#f59e0b' }}>
                    {p.ai_error ? "Error" : p.ai_decision || "Pending"}
                  </td>
                  <td style={{ padding: '12px' }}>
                    <div style={{ display: 'flex', gap: '8px' }}>
                      {DECISION_BUTTONS.map(button => (
                        <button key={button.decision} onClick={() => handleUserDecision(p.id, button.decision)} className="btn-glass" style={{ padding: '6px', fontSize: '0.8rem', background: p.user_decision === button.decision ? button.background : undefined, borderColor: p.user_decision === button.decision ? button.color : undefined }}>
                          {button.label}
                        </button>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div style={{ marginTop: '24px' }}>
        <button className="btn-primary" onClick={() => goTo('extraction-fields')}>Proceed to Extraction Rules →</button>
      </div>
    </section>
  );
}
