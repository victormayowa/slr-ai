import { ProgressBar } from '../../components/ProgressBar';
import { WorkspaceStageGate } from '../project/WorkspaceStageGate';
import { useWorkspace } from '../project/workspaceContext';

const judgmentColor = (value: string) => {
  const v = value.toLowerCase();
  if (v.includes('high')) return '#ef4444';
  if (v.includes('low')) return '#10b981';
  return '#f59e0b';
};

export function RiskOfBiasScreen() {
  const { robTool, handleRobToolChange, handleRunRob, robLoading, robProgress, robComplete, literatureResults, aiModelName, goTo } = useWorkspace();
  const assessed = literatureResults.filter(p => p.rob_data);
  const domains = assessed[0]?.rob_data ? Object.keys(assessed[0].rob_data).filter(k => k !== 'Overall') : [];

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
      <h3 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>Quality Assessment & Risk of Bias</h3>
      <WorkspaceStageGate stage="appraisal" />
      <div style={{ display: 'flex', gap: '16px', marginBottom: '32px' }}>
        <div style={{ flex: 1 }}>
          <label style={{ display: 'block', marginBottom: '8px', color: 'var(--text-secondary)' }}>Select Assessment Rubric</label>
          <select className="search-input" value={robTool} onChange={e => handleRobToolChange(e.target.value)}>
            <option value="ROB-2">Cochrane RoB 2 (Randomized Trials)</option>
            <option value="ROBINS-I">ROBINS-I (Non-randomized Interventions)</option>
            <option value="Newcastle-Ottawa">Newcastle-Ottawa Scale (Observational)</option>
            <option value="QUADAS-2">QUADAS-2 (Diagnostic Accuracy)</option>
            <option value="PROBAST">PROBAST (Prediction Model Risk of Bias)</option>
            <option value="PROBAST+AI">PROBAST+AI (Prediction Model Risk of Bias with AI extension)</option>
          </select>
        </div>
        <div style={{ display: 'flex', alignItems: 'flex-end' }}>
          <button className="btn-primary" onClick={handleRunRob} disabled={robLoading} style={{ height: '42px', padding: '0 24px' }}>
            {robLoading ? `Running ${aiModelName}...` : `Run AI Assessment`}
          </button>
        </div>
      </div>

      {robProgress !== null && <ProgressBar progress={robProgress} label={`Running ${robTool} Assessment...`} />}

      {robComplete && (
        <div className="animate-fade-in">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginBottom: '16px', marginTop: '32px' }}>
            <h4 style={{ margin: 0, color: 'var(--text-primary)' }}>Risk of Bias Assessment Results</h4>
          </div>
          <div style={{ overflowX: 'auto', background: 'rgba(0,0,0,0.3)', borderRadius: '12px' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.9rem' }}>
              <thead style={{ background: 'rgba(255,255,255,0.05)', textAlign: 'left' }}>
                <tr>
                  <th style={{ padding: '12px', borderBottom: '1px solid rgba(255,255,255,0.1)' }}>Study</th>
                  {domains.map(domain => (
                    <th key={domain} style={{ padding: '12px', borderBottom: '1px solid rgba(255,255,255,0.1)', textAlign: 'center' }}>{domain}</th>
                  ))}
                  <th style={{ padding: '12px', borderBottom: '1px solid rgba(255,255,255,0.1)', textAlign: 'center' }}>Overall Risk</th>
                </tr>
              </thead>
              <tbody>
                {assessed.slice(0, 10).map(p => {
                  const judgments = p.rob_data ?? {};
                  const overall = judgments['Overall'] ?? '';
                  return (
                    <tr key={p.id} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                      <td style={{ padding: '12px' }}>{p.title}</td>
                      {Object.keys(judgments).filter(k => k !== 'Overall').map(dom => (
                        <td key={dom} style={{ padding: '12px', textAlign: 'center' }}>
                          <span style={{ color: judgmentColor(judgments[dom]) }}>{judgments[dom]}</span>
                        </td>
                      ))}
                      <td style={{ padding: '12px', textAlign: 'center', fontWeight: 'bold' }}>
                        <span style={{ color: judgmentColor(overall), background: `${judgmentColor(overall)}15`, padding: '4px 8px', borderRadius: '4px' }}>
                          {overall}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div style={{ textAlign: 'right', marginTop: '24px' }}>
            <button className="btn-primary" onClick={() => goTo('synthesis')}>Proceed to Meta-Analysis →</button>
          </div>
        </div>
      )}
    </section>
  );
}
