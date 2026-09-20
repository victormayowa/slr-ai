import { useCallback, useEffect, useRef, useState } from 'react';
import { errorMessage } from '../../api/client';
import type { DocumentDetail } from '../../api/documents';
import { downloadFile } from '../../api/download';
import type { CellInfo, Conversion, ExtractionForm, ExtractionProgress, FieldDef, StudyExtraction, SuggestionInfo, ValueInfo } from '../../api/extraction';
import { jobProblem, jobProgress, waitForJob, type AiJob } from '../../api/jobs';
import { useAuth } from '../../auth/authContext';
import { ProgressBar } from '../../components/ProgressBar';
import { AMBER, GREEN, RED, chip, fieldLabel, fmt, muted, panel, row, smallButton } from '../../components/ui';
import { PassageViewer } from '../fulltext/PassageViewer';
import { WorkspaceStageGate } from '../project/WorkspaceStageGate';
import { useWorkspace } from '../project/workspaceContext';
import { AuthorContacts } from './AuthorContacts';
import { Calculator } from './Calculator';
import { CellCard, type CellDraft } from './CellCard';

type Extra = { source?: string; derivation?: Record<string, unknown>; ai_suggestion_id?: number; value?: unknown; not_reported?: boolean; unit?: string };
type Imputation = { cell: CellInfo; suggestions: { method: string; sd: number; description: string }[]; candidates: { study: string; sd: number; n: number }[]; reference: string };

const cellKey = (cell: CellInfo) => `${cell.field_id}:${cell.arm_id ?? 'study'}`;

export function ExtractionScreen() {
  const { projectId, refreshWorkflow, goTo } = useWorkspace();
  const { apiRequest, token } = useAuth();
  const base = `/api/projects/${projectId}`;
  const [form, setForm] = useState<ExtractionForm | null>(null);
  const [progress, setProgress] = useState<ExtractionProgress | null>(null);
  const [studyId, setStudyId] = useState<number | null>(null);
  const [data, setData] = useState<StudyExtraction | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [aiProgress, setAiProgress] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [viewer, setViewer] = useState<{ doc: DocumentDetail; highlight: number[] } | null>(null);
  const [calculating, setCalculating] = useState<CellInfo | null>(null);
  const [imputation, setImputation] = useState<Imputation | null>(null);
  const [exportSource, setExportSource] = useState<'locked' | 'current'>('locked');
  const documents = useRef<Record<number, DocumentDetail>>({});
  const unmounted = useRef(false);

  const loadProgress = useCallback((): Promise<ExtractionProgress> => apiRequest('GET', `${base}/extraction/progress`), [apiRequest, base]);

  useEffect(() => {
    unmounted.current = false;
    let cancelled = false;
    Promise.all([apiRequest('GET', `${base}/extraction/form`), loadProgress()])
      .then(([formData, progressData]: [ExtractionForm, ExtractionProgress]) => {
        if (cancelled) return;
        setForm(formData);
        setProgress(progressData);
        setStudyId(current => current ?? progressData.studies[0]?.study_id ?? null);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Could not load extraction.'));
      });
    return () => {
      cancelled = true;
      unmounted.current = true;
    };
  }, [apiRequest, base, loadProgress]);

  useEffect(() => {
    if (studyId == null) return;
    let cancelled = false;
    apiRequest('GET', `${base}/studies/${studyId}/extraction`)
      .then(result => {
        if (!cancelled) setData(result);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Could not load the study.'));
      });
    return () => {
      cancelled = true;
    };
  }, [apiRequest, base, studyId]);

  const refresh = async () => {
    if (studyId != null) setData(await apiRequest('GET', `${base}/studies/${studyId}/extraction`));
    setProgress(await loadProgress());
    await refreshWorkflow();
  };

  const act = async (key: string, action: () => Promise<string | void>, failure: string) => {
    setBusy(key);
    setNotice(null);
    try {
      const message = await action();
      if (unmounted.current) return;
      await refresh();
      if (message) setNotice(message);
    } catch (err) {
      setNotice(errorMessage(err, failure));
    }
    setBusy(null);
    setAiProgress(null);
  };

  const components = (field: FieldDef) => form?.field_types.find(type => type.key === field.field_type)?.components ?? [];

  const putValue = (cell: CellInfo, body: Record<string, unknown>) =>
    act(cellKey(cell), async () => {
      let reason: string | null = null;
      if (cell.final && cell.my_value) {
        reason = window.prompt('This cell already has a final value. Why are you changing your value?');
        if (reason === null) return;
      }
      await apiRequest('PUT', `${base}/studies/${studyId}/extraction/values`, { field_id: cell.field_id, arm_id: cell.arm_id, reason, ...body });
    }, 'Could not save the value.');

  const save = (cell: CellInfo, draft: CellDraft, extra: Extra = {}) =>
    putValue(cell, { value: draft.not_reported ? null : draft.value, not_reported: draft.not_reported, unit: draft.unit, note: draft.note, ...extra });

  const accept = (cell: CellInfo, suggestion: SuggestionInfo) => putValue(cell, { source: 'ai_accepted', ai_suggestion_id: suggestion.id });

  const reconcile = (cell: CellInfo, value: ValueInfo) => {
    const rationale = window.prompt(`Make "${value.display}" the final value. Rationale (at least 10 characters):`);
    if (!rationale) return;
    act(cellKey(cell), async () => {
      await apiRequest('PUT', `${base}/studies/${studyId}/extraction/finals`, { field_id: cell.field_id, arm_id: cell.arm_id, from_value_id: value.id, rationale });
    }, 'Could not set the final value.');
  };

  const approve = (value: ValueInfo) =>
    act(`approve-${value.id}`, async () => {
      await apiRequest('POST', `${base}/extraction/values/${value.id}/approve`);
      return 'Imputation approved.';
    }, 'Could not approve the imputed value.');

  const showEvidence = async (spanIds: number[]) => {
    if (!data) return;
    try {
      for (const report of data.study.reports) {
        if (report.document_id == null) continue;
        const doc = documents.current[report.document_id] ?? (await apiRequest('GET', `${base}/documents/${report.document_id}`));
        documents.current[report.document_id] = doc;
        if (spanIds.length === 0 || doc.spans.some((span: { id: number }) => spanIds.includes(span.id))) {
          setViewer({ doc, highlight: spanIds });
          return;
        }
      }
      setNotice('The evidence passage isn\'t in this study\'s current full texts.');
    } catch (err) {
      setNotice(errorMessage(err, 'Could not open the evidence.'));
    }
  };

  const runAi = (studyIds: number[]) =>
    act('ai', async () => {
      const job: AiJob = await apiRequest('POST', `${base}/extraction/ai`, { study_ids: studyIds });
      const finished = await waitForJob(job, id => apiRequest('GET', `${base}/jobs/${id}`), update => setAiProgress(jobProgress(update)), { stopped: () => unmounted.current });
      return jobProblem(finished, 'AI extraction') ?? 'AI suggestions are ready. Values whose quotes weren\'t found in the reports can\'t be accepted.';
    }, 'AI extraction could not be started.');

  const applyConversion = (cell: CellInfo, conversion: Conversion) => {
    const field = data?.fields.find(item => item.id === cell.field_id);
    if (!field) return;
    const value: Record<string, number> = { ...(cell.my_value?.value as Record<string, number> | null ?? {}) };
    for (const component of components(field)) {
      if (component in conversion.values) value[component] = conversion.values[component];
      else if (component === 'n' && conversion.inputs.n) value.n = conversion.inputs.n;
    }
    setCalculating(null);
    save(cell, { value, not_reported: false, unit: cell.my_value?.unit ?? field.unit, note: cell.my_value?.note ?? '' }, {
      source: 'calculated',
      derivation: { method: conversion.method, inputs: conversion.inputs, formula: conversion.formula, reference: conversion.reference, values: conversion.values },
    });
  };

  const openImputation = (cell: CellInfo) =>
    act(`impute-${cellKey(cell)}`, async () => {
      const options = await apiRequest('GET', `${base}/studies/${studyId}/extraction/imputation?field_id=${cell.field_id}`);
      setImputation({ cell, ...options });
      return options.suggestions.length ? undefined : 'No other included study reports an SD for this outcome yet.';
    }, 'Could not load imputation options.');

  const impute = (option: { method: string; sd: number; description: string }) => {
    if (!imputation) return;
    const { cell } = imputation;
    const base_value = (cell.my_value?.value ?? cell.final?.value ?? {}) as Record<string, number>;
    setImputation(null);
    save(cell, { value: { ...base_value, sd: option.sd }, not_reported: false, unit: cell.my_value?.unit ?? '', note: cell.my_value?.note ?? '' }, {
      source: 'imputed',
      derivation: { method: option.method, description: option.description, sd: option.sd, candidates: imputation.candidates, reference: imputation.reference },
    });
  };

  const exportDataset = (format: string) =>
    downloadFile(token, `${base}/extraction/export?format=${format}&source=${exportSource}`, `extraction-${exportSource}.${format === 'bundle' ? 'zip' : format}`).catch(err =>
      setNotice(errorMessage(err, 'Could not export the dataset.')),
    );

  const totals = progress?.totals;
  const sections = new Map<string, FieldDef[]>();
  for (const field of data?.fields ?? []) sections.set(field.section, [...(sections.get(field.section) ?? []), field]);

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
      <h3 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>Data Extraction</h3>
      <WorkspaceStageGate stage="extraction" />
      <p style={{ color: 'var(--text-secondary)' }}>
        Extract each study's data with evidence from its full texts. AI suggestions are checked against the reports and never become final by themselves. Signing off extraction locks the dataset.
      </p>
      {totals && (
        <p style={muted}>
          {progress?.mode === 'dual' ? 'Dual extraction' : progress?.mode === 'human_and_ai' ? 'Extraction checked against AI' : 'Single extraction'} · {totals.studies} studies · {totals.settled}/{totals.cells} cells final · {totals.discrepancies} discrepancies · {totals.missing_required} required cells missing
          {totals.unapproved_imputations > 0 && ' · imputed values need approval'}
        </p>
      )}
      {notice && <p role="status" style={muted}>{notice}</p>}
      {aiProgress !== null && <ProgressBar progress={aiProgress} label="AI extraction" />}

      <div style={{ ...row, margin: '12px 0' }}>
        <label style={fieldLabel}>
          Study
          <select aria-label="Study" className="search-input" value={studyId ?? ''} onChange={e => setStudyId(Number(e.target.value))}>
            {progress?.studies.map(study => (
              <option key={study.study_id} value={study.study_id}>{study.label} ({study.settled}/{study.cells}{study.discrepancies ? `, ${study.discrepancies} discrepancies` : ''})</option>
            ))}
          </select>
        </label>
        <button className="btn-primary" style={{ padding: '8px 16px', alignSelf: 'flex-end' }} disabled={busy !== null || studyId == null} onClick={() => studyId != null && runAi([studyId])}>AI extraction for this study</button>
        <button className="btn-glass" style={{ padding: '8px 16px', alignSelf: 'flex-end' }} disabled={busy !== null || !progress?.studies.length} onClick={() => runAi((progress?.studies ?? []).map(study => study.study_id))}>AI extraction for all studies</button>
      </div>

      {(progress?.discrepancies.length ?? 0) > 0 && (
        <div style={{ ...panel, marginBottom: '12px' }}>
          <strong style={{ color: RED }}>Discrepancies to reconcile</strong>
          <ul style={{ margin: '6px 0 0', paddingLeft: '20px' }}>
            {progress?.discrepancies.map(item => (
              <li key={`${item.study_id}-${item.field_id}-${item.arm_id}`}>
                <button className="btn-glass" style={smallButton} onClick={() => setStudyId(item.study_id)}>{item.study}</button> {item.field}{item.arm && ` (${item.arm})`}
              </li>
            ))}
          </ul>
        </div>
      )}

      {data && (
        <>
          <div style={panel}>
            <div style={{ ...row, justifyContent: 'space-between' }}>
              <strong>{data.study.label}</strong>
              <div style={row}>{data.study.registry_ids.map(id => <span key={id} style={chip('#1E5FCC')}>{id}</span>)}</div>
            </div>
            <ul style={{ margin: '6px 0', paddingLeft: '20px', fontSize: '0.9rem' }}>
              {data.study.reports.map(report => (
                <li key={report.record_id}>
                  {report.title}{report.is_primary && <span style={muted}> · primary</span>}{' '}
                  {report.document_id ? (
                    <button className="btn-glass" style={smallButton} onClick={async () => {
                      const doc = await apiRequest('GET', `${base}/documents/${report.document_id}`);
                      setViewer({ doc, highlight: [] });
                    }}>Read {report.file_name}</button>
                  ) : (
                    <span style={muted}>no readable full text</span>
                  )}
                </li>
              ))}
            </ul>
            <p style={muted}>
              Arms: {data.study.arms.length ? data.study.arms.map(arm => arm.label).join(', ') : 'none defined'}{' '}
              <button className="btn-glass" style={smallButton} onClick={() => goTo('studies')}>Edit arms</button>
            </p>
          </div>

          {viewer && <PassageViewer doc={viewer.doc} highlight={viewer.highlight} onClose={() => setViewer(null)} showEntities />}

          {[...sections.entries()].map(([section, fields]) => (
            <div key={section} style={{ ...panel, marginTop: '12px' }}>
              <h4 style={{ marginTop: 0 }}>{section}</h4>
              {fields.map(field => {
                const cells = data.cells.filter(cell => cell.field_id === field.id);
                return (
                  <div key={field.id} style={{ marginBottom: '12px' }}>
                    <div style={row}>
                      <strong>{field.name}</strong>
                      <span style={muted}>{form?.field_types.find(type => type.key === field.field_type)?.label}{field.unit && ` · ${field.unit}`}{field.per_arm && ' · per arm'}{field.outcome && ` · outcome: ${field.outcome}`}</span>
                      {field.required && <span style={chip(AMBER)}>required</span>}
                    </div>
                    {field.help_text && <p style={{ ...muted, margin: '2px 0' }}>{field.help_text}</p>}
                    {cells.map(cell => (
                      <div key={`${cellKey(cell)}:${cell.my_value?.updated_at ?? ''}:${cell.final?.decided_at ?? ''}`}>
                        <CellCard
                          cell={cell}
                          field={field}
                          components={components(field)}
                          canExtract={data.can_extract}
                          canReconcile={data.can_reconcile}
                          projectId={projectId}
                          anchorKey={`cell:${studyId}:${cell.field_id}:${cell.arm_id ?? 'study'}`}
                          onSave={draft => save(cell, draft)}
                          onAccept={suggestion => accept(cell, suggestion)}
                          onReconcile={value => reconcile(cell, value)}
                          onApprove={approve}
                          onEvidence={showEvidence}
                          onCalculate={() => setCalculating(cell)}
                          onImpute={() => openImputation(cell)}
                        />
                        {calculating && cellKey(calculating) === cellKey(cell) && form && (
                          <Calculator conversions={form.conversions} target={`${field.name}${cell.arm_label ? ` (${cell.arm_label})` : ''}`} onUse={conversion => applyConversion(cell, conversion)} onClose={() => setCalculating(null)} />
                        )}
                        {imputation && cellKey(imputation.cell) === cellKey(cell) && (
                          <div style={{ ...panel, border: `1px solid ${AMBER}` }}>
                            <p style={{ marginTop: 0 }}>Impute the SD from other studies. Imputed values need a second reviewer's approval; test the choice in a sensitivity analysis.</p>
                            {imputation.candidates.map(candidate => <p key={candidate.study} style={muted}>{candidate.study}: SD {fmt(candidate.sd)} (n = {candidate.n})</p>)}
                            {imputation.suggestions.map(option => (
                              <button key={option.method} className="btn-glass" style={{ ...smallButton, marginRight: '6px' }} onClick={() => impute(option)}>{option.description}: SD {fmt(option.sd)}</button>
                            ))}
                            <button className="btn-glass" style={smallButton} onClick={() => setImputation(null)}>Cancel</button>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                );
              })}
            </div>
          ))}
          {data.fields.length === 0 && <p style={muted}>The extraction form has no fields yet.</p>}

          <AuthorContacts studyId={data.study.id} fields={data.fields} />
        </>
      )}
      {progress && progress.studies.length === 0 && <p style={muted}>No included studies yet. Include reports at full-text screening first.</p>}

      <div style={{ ...panel, marginTop: '16px' }}>
        <h4 style={{ marginTop: 0 }}>Export the dataset</h4>
        <div style={row}>
          <label style={fieldLabel}>
            Data
            <select className="search-input" value={exportSource} onChange={e => setExportSource(e.target.value as 'locked' | 'current')}>
              <option value="locked">Locked at extraction sign-off</option>
              <option value="current">Current values (not locked)</option>
            </select>
          </label>
          {[['csv', 'CSV'], ['xlsx', 'Excel'], ['json', 'JSON'], ['bundle', 'ZIP with R and Python loaders']].map(([format, label]) => (
            <button key={format} className="btn-glass" style={{ ...smallButton, alignSelf: 'flex-end' }} onClick={() => exportDataset(format)}>{label}</button>
          ))}
        </div>
      </div>

      <div style={{ textAlign: 'right', marginTop: '24px' }}>
        <span style={{ ...chip(GREEN), marginRight: '12px' }}>{totals ? `${totals.settled}/${totals.cells} final` : ''}</span>
        <button className="btn-primary" onClick={() => goTo('prisma')}>Proceed to PRISMA →</button>
      </div>
    </section>
  );
}
