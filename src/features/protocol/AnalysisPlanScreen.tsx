import { useEffect, useState } from 'react';
import { errorMessage } from '../../api/client';
import type { AnalysisPlan, AnalysisPlanSuggestion, PlannedAnalysis, PlannedOutcome, ProtocolSuggestion } from '../../api/protocol';
import { useAuth } from '../../auth/authContext';
import { WorkspaceStageGate } from '../project/WorkspaceStageGate';
import { useWorkspace } from '../project/workspaceContext';

const labelStyle = { display: 'block', marginBottom: '8px', color: 'var(--text-secondary)' } as const;
const rowStyle = { display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center', marginBottom: '8px' } as const;

function AnalysisList({ title, items, onChange }: { title: string; items: PlannedAnalysis[]; onChange: (items: PlannedAnalysis[]) => void }) {
  const set = (index: number, changes: Partial<PlannedAnalysis>) => onChange(items.map((item, i) => (i === index ? { ...item, ...changes } : item)));
  return (
    <fieldset style={{ border: '1px solid var(--border)', borderRadius: '12px', padding: '16px' }}>
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
  const { projectId, protocolCatalog: catalog, refreshWorkflow, aiModelName } = useWorkspace();
  const { apiRequest } = useAuth();
  const [plan, setPlan] = useState<AnalysisPlan | null>(null);
  const [suggestion, setSuggestion] = useState<ProtocolSuggestion<AnalysisPlanSuggestion> | null>(null);
  const [busy, setBusy] = useState<'saving' | 'suggesting' | null>(null);
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

  const suggest = async () => {
    setBusy('suggesting');
    setNotice(null);
    try {
      setSuggestion(await apiRequest('POST', `/api/projects/${projectId}/analysis-plan/ai`));
    } catch (err) {
      setNotice(errorMessage(err, 'Could not get a suggestion.'));
    }
    setBusy(null);
  };

  // Fills the form only: the plan is saved when the reviewer presses Save.
  const applySuggestion = () => {
    if (!suggestion) return;
    const { synthesis_approach, outcomes, subgroups, sensitivity_analyses, heterogeneity } = suggestion.content;
    const keep = <T extends { name: string }>(items: T[]) => items.filter(item => item.name.trim());
    update({
      synthesis_approach,
      outcomes: [...keep(plan.outcomes), ...outcomes],
      subgroups: [...keep(plan.subgroups), ...subgroups],
      sensitivity_analyses: [...keep(plan.sensitivity_analyses), ...sensitivity_analyses],
      heterogeneity: plan.heterogeneity.trim() ? plan.heterogeneity : heterogeneity,
    });
    setNotice('Suggestion added to the form. Edit what you need, then save.');
  };

  const save = async () => {
    setBusy('saving');
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
    setBusy(null);
  };

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px', maxWidth: '960px', margin: '0 auto' }}>
      <h3 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>Analysis Plan</h3>
      <WorkspaceStageGate stage="protocol" />
      <p style={{ color: 'var(--text-secondary)' }}>
        Pre-specify outcomes and analyses before the protocol is locked. Analyses added later are reported as post hoc.
        The AI can propose a plan from your question and criteria; it is only a suggestion until you save it.
      </p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
        <div>
          <label htmlFor="synthesis-approach" style={labelStyle}>Planned synthesis</label>
          <select id="synthesis-approach" className="search-input" value={plan.synthesis_approach} onChange={e => update({ synthesis_approach: e.target.value as AnalysisPlan['synthesis_approach'] })}>
            {catalog.synthesis_approaches.map(option => <option key={option.key} value={option.key}>{option.label}</option>)}
          </select>
        </div>

        <fieldset style={{ border: '1px solid var(--border)', borderRadius: '12px', padding: '16px' }}>
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

        {suggestion && (
          <div role="region" aria-label="AI analysis plan suggestion" style={{ background: 'var(--surface-tint)', border: '1px solid rgba(30, 106, 224, 0.3)', borderRadius: '12px', padding: '16px' }}>
            <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
              Suggested by {suggestion.provider} {suggestion.model}. Nothing changes until you add it and save.
            </div>
            <p style={{ margin: '8px 0' }}>
              <strong>Planned synthesis:</strong>{' '}
              {catalog.synthesis_approaches.find(option => option.key === suggestion.content.synthesis_approach)?.label ?? suggestion.content.synthesis_approach}
            </p>
            <ul style={{ margin: '0 0 12px', paddingLeft: '20px' }}>
              {suggestion.content.outcomes.map((outcome, index) => (
                <li key={`outcome-${index}`}>
                  {outcome.priority}: {outcome.name}
                  {outcome.timepoint ? ` at ${outcome.timepoint}` : ''}
                  {outcome.measure ? ` (${outcome.measure})` : ''}
                </li>
              ))}
              {suggestion.content.subgroups.map((item, index) => (
                <li key={`subgroup-${index}`}>Subgroup: {item.name}{item.rationale ? ` — ${item.rationale}` : ''}</li>
              ))}
              {suggestion.content.sensitivity_analyses.map((item, index) => (
                <li key={`sensitivity-${index}`}>Sensitivity: {item.name}{item.rationale ? ` — ${item.rationale}` : ''}</li>
              ))}
              {suggestion.content.heterogeneity && <li>Heterogeneity: {suggestion.content.heterogeneity}</li>}
            </ul>
            <button className="btn-glass" onClick={applySuggestion} style={{ padding: '8px 16px' }}>Add to the plan</button>
          </div>
        )}

        {notice && <p role="status" style={{ color: 'var(--text-secondary)', margin: 0 }}>{notice}</p>}
        <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
          <button className="btn-glass" onClick={suggest} disabled={busy !== null} style={{ padding: '12px 20px' }}>
            {busy === 'suggesting' ? 'Asking the AI…' : `Suggest with ${aiModelName}`}
          </button>
          <button className="btn-primary" onClick={save} disabled={busy !== null} style={{ padding: '12px 24px' }}>
            {busy === 'saving' ? 'Saving…' : 'Save analysis plan'}
          </button>
        </div>
      </div>
    </section>
  );
}
