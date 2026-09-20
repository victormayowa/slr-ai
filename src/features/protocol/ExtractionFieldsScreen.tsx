import { useCallback, useEffect, useState } from 'react';
import { errorMessage } from '../../api/client';
import type { ExtractionForm, FieldDef } from '../../api/extraction';
import { useAuth } from '../../auth/authContext';
import { fieldLabel, muted, panel, row, smallButton } from '../../components/ui';
import { WorkspaceStageGate } from '../project/WorkspaceStageGate';
import { useWorkspace } from '../project/workspaceContext';

type DraftField = Omit<FieldDef, 'id' | 'position'> & { id: number | null; key: string; optionsText: string };

let nextKey = 0;
const toDraft = (field: FieldDef): DraftField => ({ ...field, key: `field-${field.id}`, optionsText: field.options.join('; ') });
const blankField = (): DraftField => ({
  id: null,
  key: `new-${(nextKey += 1)}`,
  name: '',
  section: 'General',
  field_type: 'text',
  options: [],
  optionsText: '',
  unit: '',
  required: false,
  help_text: '',
  per_arm: false,
  outcome: '',
  timepoint: '',
  settings: {},
});

// The extraction form: typed fields grouped in sections, extracted per study or per arm, linked to planned outcomes.
export function ExtractionFieldsScreen() {
  const { projectId, goTo, stageInfo, refreshWorkflow } = useWorkspace();
  const { apiRequest } = useAuth();
  const base = `/api/projects/${projectId}`;
  const [form, setForm] = useState<ExtractionForm | null>(null);
  const [fields, setFields] = useState<DraftField[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const protocolLocked = stageInfo('protocol')?.status === 'completed';

  const apply = useCallback((data: ExtractionForm) => {
    setForm(data);
    setFields(data.fields.map(toDraft));
  }, []);

  useEffect(() => {
    let cancelled = false;
    apiRequest('GET', `${base}/extraction/form`)
      .then(data => {
        if (!cancelled) apply(data);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Could not load the extraction form.'));
      });
    return () => {
      cancelled = true;
    };
  }, [apiRequest, base, apply]);

  const changeNote = () => {
    if (!protocolLocked) return null;
    return window.prompt('The protocol is locked, so this change is recorded as an amendment. Why is the form changing? (at least 10 characters)');
  };

  const save = async () => {
    const note = changeNote();
    if (protocolLocked && note === null) return;
    setBusy(true);
    setNotice(null);
    try {
      const payload = fields.map(field => ({
        ...(field.id ? { id: field.id } : {}),
        name: field.name,
        section: field.section || 'General',
        field_type: field.field_type,
        options: field.optionsText.split(';').map(option => option.trim()).filter(Boolean),
        unit: field.unit,
        required: field.required,
        help_text: field.help_text,
        per_arm: field.per_arm,
        outcome: field.outcome,
        timepoint: field.timepoint,
        settings: field.settings,
      }));
      apply(await apiRequest('PUT', `${base}/extraction/form`, { fields: payload, note }));
      await refreshWorkflow();
      setNotice('Extraction form saved.');
    } catch (err) {
      setNotice(errorMessage(err, 'Could not save the form.'));
    }
    setBusy(false);
  };

  const applyTemplate = async (template: string) => {
    const note = changeNote();
    if (protocolLocked && note === null) return;
    setBusy(true);
    setNotice(null);
    try {
      apply(await apiRequest('POST', `${base}/extraction/form/templates`, { template, note }));
      await refreshWorkflow();
      setNotice('Template fields added. Remove any you don\'t need and save.');
    } catch (err) {
      setNotice(errorMessage(err, 'Could not add the template.'));
    }
    setBusy(false);
  };

  const update = (index: number, changes: Partial<DraftField>) => setFields(prev => prev.map((field, i) => (i === index ? { ...field, ...changes } : field)));
  const move = (index: number, offset: number) =>
    setFields(prev => {
      const next = [...prev];
      const target = index + offset;
      if (target < 0 || target >= next.length) return prev;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
      <h3 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>Extraction Form</h3>
      <WorkspaceStageGate stage="protocol" />
      <p style={{ color: 'var(--text-secondary)' }}>
        Define what is extracted from each study. Structured types (continuous, dichotomous, effect estimates, 2×2 tables) keep the numbers synthesis needs; arm-level fields are extracted once per arm. After the protocol is locked, changes are recorded as amendments with a reason.
      </p>
      {notice && <p role="status" style={muted}>{notice}</p>}

      {form && (
        <div style={{ ...panel, marginBottom: '16px' }}>
          <h4 style={{ marginTop: 0 }}>Templates</h4>
          <div style={row}>
            {form.templates.map(template => (
              <button key={template.key} className="btn-glass" style={smallButton} disabled={busy} title={template.description} onClick={() => applyTemplate(template.key)}>
                + {template.label}
              </button>
            ))}
          </div>
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
        {fields.map((field, index) => (
          <div key={field.key} style={panel}>
            <div style={row}>
              <label style={{ ...fieldLabel, flex: '2 1 220px' }}>
                Field
                <input className="search-input" value={field.name} onChange={e => update(index, { name: e.target.value })} />
              </label>
              <label style={{ ...fieldLabel, flex: '1 1 140px' }}>
                Section
                <input className="search-input" value={field.section} onChange={e => update(index, { section: e.target.value })} />
              </label>
              <label style={{ ...fieldLabel, flex: '1 1 200px' }}>
                Type
                <select aria-label={`Type of ${field.name || 'new field'}`} className="search-input" value={field.field_type} onChange={e => update(index, { field_type: e.target.value })}>
                  {form?.field_types.map(type => <option key={type.key} value={type.key}>{type.label}</option>)}
                </select>
              </label>
              <label style={{ ...fieldLabel, width: '100px' }}>
                Unit
                <input className="search-input" value={field.unit} onChange={e => update(index, { unit: e.target.value })} />
              </label>
            </div>
            <div style={{ ...row, marginTop: '8px' }}>
              <label style={{ ...fieldLabel, flexDirection: 'row', alignItems: 'center' }}>
                <input type="checkbox" checked={field.required} onChange={e => update(index, { required: e.target.checked })} /> Required
              </label>
              <label style={{ ...fieldLabel, flexDirection: 'row', alignItems: 'center' }}>
                <input type="checkbox" checked={field.per_arm} onChange={e => update(index, { per_arm: e.target.checked })} /> Per arm
              </label>
              {(field.field_type === 'categorical' || field.field_type === 'multi_select') && (
                <label style={{ ...fieldLabel, flex: '1 1 260px' }}>
                  Options (separated by ;)
                  <input className="search-input" value={field.optionsText} onChange={e => update(index, { optionsText: e.target.value })} />
                </label>
              )}
              <label style={{ ...fieldLabel, flex: '1 1 200px' }}>
                Planned outcome
                <select className="search-input" value={field.outcome} onChange={e => update(index, { outcome: e.target.value })}>
                  <option value="">None</option>
                  {form?.analysis_outcomes.map(outcome => <option key={outcome} value={outcome}>{outcome}</option>)}
                  {field.outcome && !form?.analysis_outcomes.includes(field.outcome) && <option value={field.outcome}>{field.outcome}</option>}
                </select>
              </label>
              <label style={{ ...fieldLabel, width: '140px' }}>
                Timepoint
                <input className="search-input" value={field.timepoint} onChange={e => update(index, { timepoint: e.target.value })} />
              </label>
            </div>
            <div style={{ ...row, marginTop: '8px' }}>
              <input aria-label={`Guidance for ${field.name || 'new field'}`} className="search-input" style={{ flex: '1 1 300px' }} placeholder="Guidance for extractors (optional)" value={field.help_text} onChange={e => update(index, { help_text: e.target.value })} />
              <button className="btn-glass" style={smallButton} onClick={() => move(index, -1)} aria-label={`Move ${field.name} up`}>↑</button>
              <button className="btn-glass" style={smallButton} onClick={() => move(index, 1)} aria-label={`Move ${field.name} down`}>↓</button>
              <button className="btn-glass" style={{ ...smallButton, color: '#C62828' }} onClick={() => setFields(prev => prev.filter((_, i) => i !== index))}>Remove</button>
            </div>
          </div>
        ))}
      </div>

      <div style={{ ...row, marginTop: '16px' }}>
        <button className="btn-glass" style={{ padding: '8px 16px' }} onClick={() => setFields(prev => [...prev, blankField()])}>Add field</button>
        <button className="btn-primary" style={{ padding: '8px 16px' }} disabled={busy || fields.some(field => !field.name.trim())} onClick={save}>{busy ? 'Saving…' : 'Save form'}</button>
        <button className="btn-primary" style={{ marginLeft: 'auto' }} onClick={() => goTo('extraction')}>Proceed to Data Extraction →</button>
      </div>
    </section>
  );
}
