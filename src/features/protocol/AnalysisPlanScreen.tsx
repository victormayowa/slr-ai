import { useEffect, useState } from 'react';
import { errorMessage } from '../../api/client';
import type { AnalysisPlan, PlannedAnalysis, PlannedOutcome } from '../../api/protocol';
import { useAuth } from '../../auth/authContext';
import { WorkspaceStageGate } from '../project/WorkspaceStageGate';
import { useWorkspace } from '../project/workspaceContext';

const labelStyle = { display: 'block', marginBottom: '8px', color: 'var(--text-secondary)' } as const;
const rowStyle = { display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center', marginBottom: '8px' } as const;

function AnalysisList({ title, items, onChange }: { title: string; items: PlannedAnalysis[]; onChange: (items: PlannedAnalysis[]) => void }) {
  const set = (index: number, changes: Partial<PlannedAnalysis>) => onChange(items.map((item, i) => (i === index ? { ...item, ...changes } : item)));
  return (
    <fieldset style={{ border: '1px solid rgba(255,255,255,0.1)', borderRadius: '12px', padding: '16px' }}>
      <legend style={{ padding: '0 8px' }}>{title}</legend>
      {items.map((item, index) => (
        <div key={index} style={rowStyle}>
          <input aria-label={`${title} ${index + 1} name`} className="search-input" style={{ flex: '1 1 200px' }} placeholder="Name" value={item.name} onChange={e => set(index, { name: e.target.value })} />
          <input aria-label={`${title} ${index + 1} rationale`} className="search-input" style={{ flex: '2 1 280px' }} placeholder="Rationale" value={item.rationale} onChange={e => set(index, { rationale: e.target.value })} />
          <button className="btn-glass" aria-label={`Remove ${title.toLowerCase()} ${index + 1}`} onClick={() => onChange(items.filter((_, i) => i !== index))} style={{ padding: '6px 10px' }}>✕</button>
        </div>
      ))}
      <button className="btn-glass" onClick={() => onChange([...items, { name: '', rationale: '' }])} style={{ padding: '6px 12px' }}>Add</button>
    </fieldset>
  );
}

export function AnalysisPlanScreen() {
  const { projectId, protocolCatalog: catalog, refreshWorkflow } = useWorkspace();
  const { apiRequest } = useAuth();
  const [plan, setPlan] = useState<AnalysisPlan | null>(null);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiRequest('GET', `/api/projects/${projectId}/analysis-plan`)
      .then(data => {
        if (!cancelled) setPlan(data);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Could not load the analysis plan.'));
      });
    return () => {
      cancelled = true;
    };
  }, [apiRequest, projectId]);

  if (!plan || !catalog) return <p style={{ color: 'var(--text-secondary)' }}>{notice ?? 'Loading the analysis plan…'}</p>;

  const update = (changes: Partial<AnalysisPlan>) => setPlan({ ...plan, ...changes });
  const setOutcome = (index: number, changes: Partial<PlannedOutcome>) =>
    update({ outcomes: plan.outcomes.map((outcome, i) => (i === index ? { ...outcome, ...changes } : outcome)) });

  const save = async () => {
    setSaving(true);
    setNotice(null);
    const named = <T extends { name: string }>(items: T[]) => items.filter(item => item.name.trim());
    try {
      const saved: AnalysisPlan = await apiRequest('PUT', `/api/projects/${projectId}/analysis-plan`, {
        ...plan,
        outcomes: named(plan.outcomes),
        subgroups: named(plan.subgroups),
        sensitivity_analyses: named(plan.sensitivity_analyses),
      });
      setPlan(saved);
      await refreshWorkflow();
      setNotice('Analysis plan saved.');
    } catch (err) {
      setNotice(errorMessage(err, 'Could not save the analysis plan.'));
    }
    setSaving(false);
  };

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px', maxWidth: '960px', margin: '0 auto' }}>
      <h3 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>Analysis Plan</h3>
      <WorkspaceStageGate stage="protocol" />
      <p style={{ color: 'var(--text-secondary)' }}>
        Pre-specify outcomes and analyses before the protocol is locked. Analyses added later are reported as post hoc.
      </p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
        <div>
          <label htmlFor="synthesis-approach" style={labelStyle}>Planned synthesis</label>
          <select id="synthesis-approach" className="search-input" value={plan.synthesis_approach} onChange={e => update({ synthesis_approach: e.target.value as AnalysisPlan['synthesis_approach'] })}>
            {catalog.synthesis_approaches.map(option => <option key={option.key} value={option.key}>{option.label}</option>)}
          </select>
        </div>

        <fieldset style={{ border: '1px solid rgba(255,255,255,0.1)', borderRadius: '12px', padding: '16px' }}>
          <legend style={{ padding: '0 8px' }}>Outcomes</legend>
          {plan.outcomes.map((outcome, index) => (
            <div key={index} style={rowStyle}>
              <input aria-label={`Outcome ${index + 1} name`} className="search-input" style={{ flex: '2 1 220px' }} placeholder="Outcome" value={outcome.name} onChange={e => setOutcome(index, { name: e.target.value })} />
              <select aria-label={`Outcome ${index + 1} priority`} className="search-input" style={{ flex: '0 1 150px' }} value={outcome.priority} onChange={e => setOutcome(index, { priority: e.target.value as PlannedOutcome['priority'] })}>
                {catalog.outcome_priorities.map(option => <option key={option.key} value={option.key}>{option.label}</option>)}
              </select>
              <input aria-label={`Outcome ${index + 1} timepoint`} className="search-input" style={{ flex: '1 1 140px' }} placeholder="Timepoint" value={outcome.timepoint} onChange={e => setOutcome(index, { timepoint: e.target.value })} />
              <input aria-label={`Outcome ${index + 1} effect measure`} className="search-input" style={{ flex: '1 1 140px' }} placeholder="Effect measure" value={outcome.measure} onChange={e => setOutcome(index, { measure: e.target.value })} />
              <button className="btn-glass" aria-label={`Remove outcome ${index + 1}`} onClick={() => update({ outcomes: plan.outcomes.filter((_, i) => i !== index) })} style={{ padding: '6px 10px' }}>✕</button>
            </div>
          ))}
          <button className="btn-glass" onClick={() => update({ outcomes: [...plan.outcomes, { name: '', priority: plan.outcomes.length ? 'secondary' : 'primary', timepoint: '', measure: '' }] })} style={{ padding: '6px 12px' }}>
            Add outcome
          </button>
        </fieldset>

        <AnalysisList title="Subgroup analyses" items={plan.subgroups} onChange={subgroups => update({ subgroups })} />
        <AnalysisList title="Sensitivity analyses" items={plan.sensitivity_analyses} onChange={sensitivity_analyses => update({ sensitivity_analyses })} />

        <div>
          <label htmlFor="heterogeneity" style={labelStyle}>Heterogeneity and model choice</label>
          <textarea id="heterogeneity" className="search-input" style={{ height: '80px', resize: 'vertical' }} placeholder="For example: I² and prediction intervals; random-effects model by default" value={plan.heterogeneity} onChange={e => update({ heterogeneity: e.target.value })} />
        </div>

        {notice && <p role="status" style={{ color: 'var(--text-secondary)', margin: 0 }}>{notice}</p>}
        <button className="btn-primary" onClick={save} disabled={saving} style={{ padding: '12px 24px', alignSelf: 'flex-start' }}>
          {saving ? 'Saving…' : 'Save analysis plan'}
        </button>
      </div>
    </section>
  );
}
