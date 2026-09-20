import { useCallback, useEffect, useRef, useState } from 'react';
import { errorMessage } from '../../api/client';
import { downloadFile, fetchBlob } from '../../api/download';
import type { AnalysisInfo, DatasetPreview, EngineInfo, IpdInfo, RunDetail } from '../../api/evidence';
import type { FieldDef, StudyInfo } from '../../api/extraction';
import { jobProblem, waitForJob } from '../../api/jobs';
import { useAuth } from '../../auth/authContext';
import { AMBER, BLUE, GREEN, GREY, RED, chip, fieldLabel, fmt, muted, panel, row, smallButton } from '../../components/ui';
import { WorkspaceStageGate } from '../project/WorkspaceStageGate';
import { useWorkspace } from '../project/workspaceContext';

const MEASURES: Record<string, string[]> = {
  dichotomous: ['RR', 'OR', 'RD'],
  continuous: ['MD', 'SMD', 'ROM'],
  effect_estimate: ['RR', 'OR', 'HR', 'RD', 'MD', 'SMD'],
  dta_2x2: [],
};
const FIELD_TYPES: Record<string, string[]> = {
  pairwise: ['dichotomous', 'continuous', 'effect_estimate'],
  bayesian: ['dichotomous', 'continuous', 'effect_estimate'],
  rve: ['dichotomous', 'continuous', 'effect_estimate'],
  nma: ['dichotomous', 'continuous'],
  dta: ['dta_2x2'],
  ipd: [],
  swim: [],
};
const PUBLICATION_BIAS = { funnel: 'Funnel plot', contour: 'Contour-enhanced', egger: "Egger's test", begg: "Begg's test", trim_fill: 'Trim and fill', pet_peese: 'PET-PEESE', selection_model: 'Selection model' };
const DIRECTIONS = { benefit: 'Benefit', harm: 'Harm', no_clear_difference: 'No clear difference', conflicting: 'Conflicting' };

type Draft = {
  id: number | null;
  title: string;
  outcome: string;
  analysis_type: string;
  prespecified: boolean;
  plan_reference: string;
  justification: string;
  spec: Record<string, any>;
};

const emptyDraft = (): Draft => ({
  id: null,
  title: '',
  outcome: '',
  analysis_type: 'pairwise',
  prespecified: true,
  plan_reference: '',
  justification: '',
  spec: { measure: 'RR', model: 'random', tau_method: 'REML', hksj: true, prediction_interval: true, arms: [], swim: [], treatments: {}, sensitivity: {}, publication_bias: {}, plot_formats: ['svg', 'png', 'pdf'] },
});

const number = (value: unknown, digits = 2) => (typeof value === 'number' ? fmt(value, digits) : '–');

// Statistical synthesis in R: analyses specified from the locked extraction data set, approved by a statistician,
// run reproducibly, with results, plots, and exportable code.
export function SynthesisScreen() {
  const { projectId, refreshWorkflow, handleRunMetaAnalysis, metaLoading, metaReport, aiModelName } = useWorkspace();
  const { apiRequest, token } = useAuth();
  const base = `/api/projects/${projectId}`;
  const [engine, setEngine] = useState<EngineInfo | null>(null);
  const [analyses, setAnalyses] = useState<AnalysisInfo[]>([]);
  const [plan, setPlan] = useState<Record<string, string>>({});
  const [fields, setFields] = useState<FieldDef[]>([]);
  const [studies, setStudies] = useState<StudyInfo[]>([]);
  const [ipd, setIpd] = useState<IpdInfo[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [preview, setPreview] = useState<{ analysisId: number; data: DatasetPreview } | null>(null);
  const [run, setRun] = useState<RunDetail | null>(null);
  const [plotUrls, setPlotUrls] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const unmounted = useRef(false);

  const load = useCallback(async () => {
    const [engineInfo, analysisList, form, studyList] = await Promise.all([
      apiRequest('GET', `${base}/statistics/engine`),
      apiRequest('GET', `${base}/analyses`),
      apiRequest('GET', `${base}/extraction/form`),
      apiRequest('GET', `${base}/studies`),
    ]);
    const participantData = await apiRequest('GET', `${base}/ipd`).catch(() => []);
    return { engineInfo, analysisList, form, studyList, participantData };
  }, [apiRequest, base]);

  const apply = (data: Awaited<ReturnType<typeof load>>) => {
    setEngine(data.engineInfo);
    setAnalyses(data.analysisList.analyses);
    setPlan(data.analysisList.plan);
    setFields(data.form.fields);
    setStudies(data.studyList);
    setIpd(data.participantData);
  };

  useEffect(() => {
    unmounted.current = false;
    let cancelled = false;
    load()
      .then(data => {
        if (!cancelled) apply(data);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Could not load the synthesis.'));
      });
    return () => {
      cancelled = true;
      unmounted.current = true;
    };
  }, [load]);

  useEffect(() => () => Object.values(plotUrls).forEach(url => URL.revokeObjectURL(url)), [plotUrls]);

  const act = async (action: () => Promise<string | void>, failure: string) => {
    setBusy(true);
    setNotice(null);
    try {
      const message = await action();
      apply(await load());
      await refreshWorkflow();
      if (message) setNotice(message);
    } catch (err) {
      setNotice(errorMessage(err, failure));
    }
    setBusy(false);
  };

  const showRun = async (runId: number) => {
    const detail: RunDetail = await apiRequest('GET', `${base}/analysis-runs/${runId}`);
    setRun(detail);
    const urls: Record<string, string> = {};
    for (const plot of detail.plots.filter(p => p.format === 'svg' || p.format === 'png')) {
      if (urls[plot.name]) continue;
      const blob = await fetchBlob(token, `${base}/analysis-runs/${runId}/plots/${plot.name}.${plot.format}`);
      urls[plot.name] = URL.createObjectURL(blob);
    }
    setPlotUrls(urls);
  };

  const save = () =>
    act(async () => {
      if (!draft) return;
      const body = { title: draft.title, outcome: draft.outcome, analysis_type: draft.analysis_type, spec: draft.spec, prespecified: draft.prespecified, plan_reference: draft.plan_reference, justification: draft.justification };
      if (draft.id) await apiRequest('PUT', `${base}/analyses/${draft.id}`, body);
      else await apiRequest('POST', `${base}/analyses`, body);
      setDraft(null);
      return draft.id ? 'Analysis revised. An approved analysis returns to draft and needs approval again.' : 'Analysis saved as a draft.';
    }, 'Could not save the analysis.');

  const showDataset = (analysis: AnalysisInfo) =>
    act(async () => {
      setPreview({ analysisId: analysis.id, data: await apiRequest('GET', `${base}/analyses/${analysis.id}/dataset`) });
    }, 'Could not build the data set.');

  const start = (analysis: AnalysisInfo) =>
    act(async () => {
      const started = await apiRequest('POST', `${base}/analyses/${analysis.id}/runs`);
      const job = await waitForJob(started.job, id => apiRequest('GET', `${base}/jobs/${id}`), () => undefined, { stopped: () => unmounted.current });
      await showRun(started.run.id);
      return jobProblem(job, 'The analysis') ?? (started.run.is_final ? 'Final analysis run finished.' : 'Exploratory run finished. Runs become final once the analysis is approved and extraction is locked.');
    }, 'Could not run the analysis.');

  const approve = (analysis: AnalysisInfo) => {
    const note = window.prompt('Approve this model choice. Note for the audit trail (why this model suits the data):');
    if (note === null) return;
    act(async () => {
      await apiRequest('POST', `${base}/analyses/${analysis.id}/approve`, { note });
      return 'Analysis approved. Run it again for final results.';
    }, 'Could not approve the analysis.');
  };

  const remove = (analysis: AnalysisInfo) =>
    act(async () => {
      if (!window.confirm(`Delete "${analysis.title}"?`)) return;
      await apiRequest('DELETE', `${base}/analyses/${analysis.id}`);
      return 'Analysis deleted.';
    }, 'Could not delete the analysis.');

  const uploadIpd = (studyId: number, file: File) =>
    act(async () => {
      const form = new FormData();
      form.append('file', file);
      await apiRequest('POST', `${base}/studies/${studyId}/ipd`, form);
      return 'Participant data uploaded and encrypted. Map its columns next.';
    }, 'Could not upload the participant data.');

  const mapIpd = (dataset: IpdInfo, mapping: { treatment: string; outcome: string; outcome_type: string }) =>
    act(async () => {
      await apiRequest('PUT', `${base}/ipd/${dataset.id}/mapping`, { ...mapping, covariates: {} });
      return 'Columns mapped and checked.';
    }, 'Could not map the columns.');

  const edit = (analysis: AnalysisInfo) =>
    setDraft({ id: analysis.id, title: analysis.title, outcome: analysis.outcome, analysis_type: analysis.analysis_type, prespecified: analysis.prespecified, plan_reference: analysis.plan_reference, justification: analysis.justification, spec: { ...analysis.spec } });

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
      <h3 style={{ marginBottom: '8px', color: 'var(--text-primary)' }}>Statistical Synthesis</h3>
      <p style={{ ...muted, marginBottom: '16px' }}>
        Every analysis runs in R on the extraction data set with validated packages (metafor, meta, netmeta, mada, clubSandwich, lme4, bayesmeta). Results are final only when a statistician has approved the model and extraction is locked. Each run keeps its script, data, seed, and R session so it can be reproduced.
      </p>
      <WorkspaceStageGate stage="synthesis" />
      {notice && <p role="status" style={{ ...panel, marginBottom: '16px' }}>{notice}</p>}

      {engine && (
        <div style={{ ...panel, marginBottom: '16px' }}>
          {engine.available ? (
            <span style={chip(GREEN)}>{engine.r_version}</span>
          ) : (
            <span style={chip(RED)}>Statistics engine unavailable</span>
          )}
          {engine.message && <p style={muted}>{engine.message}</p>}
          {engine.available && (
            <p style={muted}>
              {Object.entries(engine.analysis_types)
                .filter(([, info]) => !info.available)
                .map(([key, info]) => `${engine.analysis_type_labels[key] ?? key} needs ${info.missing_packages.join(', ')}`)
                .join('; ')}
            </p>
          )}
        </div>
      )}

      <div style={{ ...row, justifyContent: 'space-between' }}>
        <h4>Analyses</h4>
        <button className="btn-primary" style={smallButton} onClick={() => setDraft(emptyDraft())} disabled={busy}>
          New analysis
        </button>
      </div>
      {analyses.length === 0 && <p style={muted}>No analyses yet. Specify one for each outcome in the analysis plan.</p>}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', marginBottom: '24px' }}>
        {analyses.map(analysis => (
          <div key={analysis.id} style={panel}>
            <div style={{ ...row, justifyContent: 'space-between' }}>
              <div>
                <strong>{analysis.title}</strong> <span style={muted}>· {analysis.analysis_type_label} · {analysis.outcome}</span>
              </div>
              <span style={row}>
                <span style={chip(analysis.status === 'approved' ? GREEN : AMBER)}>{analysis.status === 'approved' ? `Approved by ${analysis.approved_by}` : 'Draft'}</span>
                <span style={chip(analysis.prespecified ? BLUE : AMBER)}>{analysis.prespecified ? 'Pre-specified' : 'Post hoc'}</span>
                {analysis.final_run_id && <span style={chip(GREEN)}>Final results</span>}
              </span>
            </div>
            {!analysis.prespecified && <p style={muted}>Justification: {analysis.justification}</p>}
            {analysis.approval_note && <p style={muted}>Approval note: {analysis.approval_note}</p>}
            <div style={{ ...row, marginTop: '8px' }}>
              <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => showDataset(analysis)}>
                Data set
              </button>
              <button className="btn-primary" style={smallButton} disabled={busy || !engine?.available} onClick={() => start(analysis)}>
                Run in R
              </button>
              {analysis.status !== 'approved' && (
                <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => approve(analysis)}>
                  Approve model
                </button>
              )}
              <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => edit(analysis)}>
                Revise
              </button>
              {!analysis.final_run_id && (
                <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => remove(analysis)}>
                  Delete
                </button>
              )}
            </div>
            {analysis.runs.length > 0 && (
              <div style={{ ...row, marginTop: '8px' }}>
                <span style={muted}>Runs:</span>
                {analysis.runs.map(r => (
                  <button key={r.id} className="btn-glass" style={smallButton} onClick={() => showRun(r.id).catch(err => setNotice(errorMessage(err, 'Could not load the run.')))}>
                    #{r.id} {r.status === 'succeeded' ? (r.is_final ? '✓ final' : 'exploratory') : r.status}
                  </button>
                ))}
              </div>
            )}
            {preview?.analysisId === analysis.id && <DatasetView data={preview.data} />}
          </div>
        ))}
      </div>

      {draft && <AnalysisBuilder draft={draft} setDraft={setDraft} plan={plan} fields={fields} studies={studies} engine={engine} busy={busy} onSave={save} onCancel={() => setDraft(null)} />}

      {draft?.analysis_type === 'ipd' && <IpdPanel studies={studies} datasets={ipd} busy={busy} onUpload={uploadIpd} onMap={mapIpd} />}

      {run && <RunView run={run} plotUrls={plotUrls} onDownload={(path, name) => downloadFile(token, `${base}/${path}`, name).catch(err => setNotice(errorMessage(err, 'The download failed.')))} />}

      <h4 style={{ marginTop: '32px' }}>Narrative summary (AI)</h4>
      <p style={muted}>An AI-written narrative of the extracted data and signed-off risk of bias. It computes no statistics; use the analyses above for pooled results.</p>
      <button className="btn-glass" onClick={handleRunMetaAnalysis} disabled={metaLoading}>
        {metaLoading ? `Writing with ${aiModelName}...` : 'Write narrative summary'}
      </button>
      {metaReport && (
        <pre style={{ ...panel, whiteSpace: 'pre-wrap', fontFamily: 'var(--body)', marginTop: '16px' }}>{metaReport}</pre>
      )}
    </section>
  );
}

type BuilderProps = {
  draft: Draft;
  setDraft: (draft: Draft) => void;
  plan: Record<string, string>;
  fields: FieldDef[];
  studies: StudyInfo[];
  engine: EngineInfo | null;
  busy: boolean;
  onSave: () => void;
  onCancel: () => void;
};

function AnalysisBuilder({ draft, setDraft, plan, fields, studies, engine, busy, onSave, onCancel }: BuilderProps) {
  const spec = draft.spec;
  const setSpec = (change: Record<string, unknown>) => setDraft({ ...draft, spec: { ...spec, ...change } });
  const type = draft.analysis_type;
  const eligible = fields.filter(f => (FIELD_TYPES[type] ?? []).includes(f.field_type));
  const field = fields.find(f => f.id === spec.field_id);
  const measures = field ? MEASURES[field.field_type] ?? [] : [];
  const needsArms = ['pairwise', 'bayesian', 'rve'].includes(type) && field?.per_arm;
  const pairFor = (studyId: number) => (spec.arms as { study_id: number; treatment_arm_id: number; control_arm_id: number }[]).find(p => p.study_id === studyId);
  const setPair = (studyId: number, key: 'treatment_arm_id' | 'control_arm_id', armId: number) => {
    const others = (spec.arms as { study_id: number }[]).filter(p => p.study_id !== studyId);
    const current = pairFor(studyId) ?? { study_id: studyId, treatment_arm_id: 0, control_arm_id: 0 };
    setSpec({ arms: [...others, { ...current, [key]: armId }] });
  };
  const swimFor = (studyId: number) => (spec.swim as { study_id: number; direction: string; outcome_domain: string }[]).find(s => s.study_id === studyId);
  const setSwim = (studyId: number, direction: string) => {
    const others = (spec.swim as { study_id: number }[]).filter(s => s.study_id !== studyId);
    setSpec({ swim: direction ? [...others, { study_id: studyId, direction, outcome_domain: draft.outcome || 'Primary outcome' }] : others });
  };
  const toggle = (group: 'sensitivity' | 'publication_bias', key: string, value: boolean) => setSpec({ [group]: { ...(spec[group] ?? {}), [key]: value } });

  return (
    <section aria-label="Analysis builder" style={{ ...panel, marginBottom: '24px' }}>
      <h4 style={{ marginTop: 0 }}>{draft.id ? 'Revise analysis' : 'New analysis'}</h4>
      <div style={row}>
        <label style={fieldLabel}>
          Title
          <input className="search-input" value={draft.title} onChange={e => setDraft({ ...draft, title: e.target.value })} />
        </label>
        <label style={fieldLabel}>
          Outcome
          <input className="search-input" list="plan-outcomes" value={draft.outcome} onChange={e => setDraft({ ...draft, outcome: e.target.value })} />
          <datalist id="plan-outcomes">
            {Object.entries(plan)
              .filter(([, kind]) => kind === 'outcome')
              .map(([name]) => (
                <option key={name} value={name} />
              ))}
          </datalist>
        </label>
        <label style={fieldLabel}>
          Analysis type
          <select className="search-input" value={type} onChange={e => setDraft({ ...draft, analysis_type: e.target.value })}>
            {Object.entries(engine?.analysis_type_labels ?? { pairwise: 'Pairwise meta-analysis' }).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {(FIELD_TYPES[type] ?? []).length > 0 && (
        <div style={{ ...row, marginTop: '12px' }}>
          <label style={fieldLabel}>
            Data field
            <select className="search-input" value={spec.field_id ?? ''} onChange={e => setSpec({ field_id: Number(e.target.value) || null, measure: MEASURES[fields.find(f => f.id === Number(e.target.value))?.field_type ?? '']?.[0] ?? spec.measure })}>
              <option value="">Choose…</option>
              {eligible.map(f => (
                <option key={f.id} value={f.id}>
                  {f.name}
                </option>
              ))}
            </select>
          </label>
          {measures.length > 0 && (
            <label style={fieldLabel}>
              Effect measure
              <select className="search-input" value={spec.measure} onChange={e => setSpec({ measure: e.target.value })}>
                {measures.map(m => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </label>
          )}
          {['pairwise', 'nma', 'rve'].includes(type) && (
            <label style={fieldLabel}>
              Model
              <select className="search-input" value={spec.model} onChange={e => setSpec({ model: e.target.value })}>
                <option value="random">Random effects</option>
                <option value="fixed">Fixed effect (inverse variance)</option>
                {type === 'pairwise' && <option value="mantel_haenszel">Mantel-Haenszel</option>}
                {type === 'pairwise' && <option value="peto">Peto</option>}
                {type === 'pairwise' && <option value="glmm">GLMM (rare events)</option>}
              </select>
            </label>
          )}
          {type === 'pairwise' && spec.model === 'random' && (
            <>
              <label style={fieldLabel}>
                Heterogeneity estimator
                <select className="search-input" value={spec.tau_method} onChange={e => setSpec({ tau_method: e.target.value })}>
                  {['REML', 'DL', 'PM', 'SJ', 'ML', 'EB', 'HE', 'HS'].map(m => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </label>
              <label style={row}>
                <input type="checkbox" checked={spec.hksj} onChange={e => setSpec({ hksj: e.target.checked })} /> Hartung-Knapp-Sidik-Jonkman
              </label>
              <label style={row}>
                <input type="checkbox" checked={spec.prediction_interval} onChange={e => setSpec({ prediction_interval: e.target.checked })} /> Prediction interval
              </label>
            </>
          )}
          {type === 'bayesian' && (
            <label style={fieldLabel}>
              Half-normal prior scale for τ
              <input className="search-input" type="number" step="0.1" value={spec.tau_prior_scale ?? 0.5} onChange={e => setSpec({ tau_prior_scale: Number(e.target.value) })} />
            </label>
          )}
          {type === 'nma' && (
            <>
              <label style={fieldLabel}>
                Reference treatment
                <input className="search-input" value={spec.reference_treatment ?? ''} onChange={e => setSpec({ reference_treatment: e.target.value })} />
              </label>
              <label style={fieldLabel}>
                Smaller values are
                <select className="search-input" value={spec.small_values ?? 'desirable'} onChange={e => setSpec({ small_values: e.target.value })}>
                  <option value="desirable">Desirable</option>
                  <option value="undesirable">Undesirable</option>
                </select>
              </label>
            </>
          )}
        </div>
      )}

      {needsArms && (
        <table style={{ width: '100%', fontSize: '0.85rem', marginTop: '12px' }}>
          <thead>
            <tr>
              <th style={{ textAlign: 'left' }}>Study</th>
              <th style={{ textAlign: 'left' }}>Intervention arm</th>
              <th style={{ textAlign: 'left' }}>Comparator arm</th>
            </tr>
          </thead>
          <tbody>
            {studies.map(study => (
              <tr key={study.id}>
                <td>{study.label}</td>
                {(['treatment_arm_id', 'control_arm_id'] as const).map(key => (
                  <td key={key}>
                    <select aria-label={`${key === 'treatment_arm_id' ? 'Intervention' : 'Comparator'} arm for ${study.label}`} className="search-input" value={pairFor(study.id)?.[key] ?? ''} onChange={e => setPair(study.id, key, Number(e.target.value))}>
                      <option value="">—</option>
                      {study.arms.map(arm => (
                        <option key={arm.id} value={arm.id ?? ''}>
                          {arm.label}
                        </option>
                      ))}
                    </select>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {type === 'nma' && (
        <p style={muted}>Each arm's label is used as its treatment name; label arms consistently across studies (for example "Placebo").</p>
      )}

      {type === 'swim' && (
        <table style={{ width: '100%', fontSize: '0.85rem', marginTop: '12px' }}>
          <tbody>
            {studies.map(study => (
              <tr key={study.id}>
                <td>{study.label}</td>
                <td>
                  <select aria-label={`Direction of effect for ${study.label}`} className="search-input" value={swimFor(study.id)?.direction ?? ''} onChange={e => setSwim(study.id, e.target.value)}>
                    <option value="">Not included</option>
                    {Object.entries(DIRECTIONS).map(([key, label]) => (
                      <option key={key} value={key}>
                        {label}
                      </option>
                    ))}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {type === 'pairwise' && (
        <div style={{ ...row, marginTop: '12px', alignItems: 'flex-start' }}>
          <fieldset style={{ border: 'none', padding: 0 }}>
            <legend style={muted}>Subgroup and sensitivity analyses</legend>
            <label style={row}>
              <input type="checkbox" checked={spec.subgroup?.source === 'risk_of_bias'} onChange={e => setSpec({ subgroup: e.target.checked ? { source: 'risk_of_bias', label: 'Risk of bias', type: 'categorical', prespecified: draft.prespecified } : null })} /> Subgroups by risk of bias
            </label>
            {[
              ['leave_one_out', 'Leave-one-out'],
              ['influence', 'Influence diagnostics'],
              ['exclude_high_risk_of_bias', 'Excluding high risk of bias'],
            ].map(([key, label]) => (
              <label key={key} style={row}>
                <input type="checkbox" checked={Boolean(spec.sensitivity?.[key])} onChange={e => toggle('sensitivity', key, e.target.checked)} /> {label}
              </label>
            ))}
          </fieldset>
          <fieldset style={{ border: 'none', padding: 0 }}>
            <legend style={muted}>Small-study effects and publication bias</legend>
            {Object.entries(PUBLICATION_BIAS).map(([key, label]) => (
              <label key={key} style={row}>
                <input type="checkbox" checked={Boolean(spec.publication_bias?.[key])} onChange={e => toggle('publication_bias', key, e.target.checked)} /> {label}
              </label>
            ))}
          </fieldset>
        </div>
      )}

      <div style={{ ...row, marginTop: '12px' }}>
        <label style={row}>
          <input type="checkbox" checked={draft.prespecified} onChange={e => setDraft({ ...draft, prespecified: e.target.checked })} /> Pre-specified in the protocol
        </label>
        {draft.prespecified ? (
          <label style={fieldLabel}>
            Analysis plan item
            <select className="search-input" value={draft.plan_reference} onChange={e => setDraft({ ...draft, plan_reference: e.target.value })}>
              <option value="">Choose…</option>
              {Object.entries(plan).map(([name, kind]) => (
                <option key={name} value={name}>
                  {name} ({kind})
                </option>
              ))}
            </select>
          </label>
        ) : (
          <label style={{ ...fieldLabel, flex: 1 }}>
            Why is this post hoc analysis needed?
            <input className="search-input" value={draft.justification} onChange={e => setDraft({ ...draft, justification: e.target.value })} />
          </label>
        )}
      </div>
      <div style={{ ...row, marginTop: '16px' }}>
        <button className="btn-primary" disabled={busy || !draft.title || !draft.outcome} onClick={onSave}>
          Save analysis
        </button>
        <button className="btn-glass" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </section>
  );
}

function DatasetView({ data }: { data: DatasetPreview }) {
  const columns = Array.from(new Set(data.rows.flatMap(r => Object.keys(r)))).filter(c => !['study_id'].includes(c));
  const color = { meta_analysis: GREEN, swim: AMBER, not_possible: RED }[data.pooling.recommendation];
  return (
    <div style={{ marginTop: '12px', overflowX: 'auto' }}>
      <p style={muted}>
        {data.source === 'locked' ? 'Locked extraction data set.' : 'Current extraction values (extraction is not locked, so runs are exploratory).'}{' '}
        <span style={chip(color)}>{{ meta_analysis: 'Pooling looks appropriate', swim: 'Consider SWiM', not_possible: 'Pooling not possible' }[data.pooling.recommendation]}</span>
      </p>
      {data.pooling.reasons.map(reason => (
        <p key={reason} style={muted}>
          {reason}
        </p>
      ))}
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
        <thead>
          <tr>
            {columns.map(c => (
              <th key={c} style={{ textAlign: 'left', padding: '4px' }}>
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.rows.map((r, i) => (
            <tr key={i}>
              {columns.map(c => (
                <td key={c} style={{ padding: '4px' }}>
                  {r[c] == null ? '' : String(r[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {data.excluded.length > 0 && (
        <ul style={muted}>
          {data.excluded.map(e => (
            <li key={`${e.study_id}-${e.reason}`}>
              {e.study}: {e.reason}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function RunView({ run, plotUrls, onDownload }: { run: RunDetail; plotUrls: Record<string, string>; onDownload: (path: string, name: string) => void }) {
  const results = run.results ?? {};
  const summary = results.summary ?? results.two_stage ?? null;
  const ratio = summary && summary.exp_estimate != null;
  const studies: Record<string, any>[] = results.studies ?? [];
  return (
    <section aria-label={`Analysis run ${run.id}`} style={{ ...panel, marginBottom: '24px' }}>
      <div style={{ ...row, justifyContent: 'space-between' }}>
        <h4 style={{ margin: 0 }}>
          Run #{run.id} <span style={chip(run.status === 'succeeded' ? (run.is_final ? GREEN : BLUE) : run.status === 'failed' ? RED : GREY)}>{run.status === 'succeeded' ? (run.is_final ? 'Final' : 'Exploratory') : run.status}</span>
        </h4>
        <span style={row}>
          <button className="btn-glass" style={smallButton} onClick={() => onDownload(`analysis-runs/${run.id}/export`, `analysis-run-${run.id}.zip`)}>
            Export code and data
          </button>
        </span>
      </div>
      {run.error && <p style={{ color: RED }}>{run.error}</p>}
      <p style={muted}>
        {run.r_version} · seed {run.seed} · data set {run.dataset_sha256.slice(0, 12)} · {run.duration_ms != null ? `${(run.duration_ms / 1000).toFixed(1)} s` : ''}
      </p>
      {summary && (
        <div style={{ ...row, margin: '8px 0' }}>
          {summary.k != null && <span>k = {summary.k}</span>}
          {summary.estimate != null && (
            <span>
              Estimate {ratio ? number(summary.exp_estimate) : number(summary.estimate)} ({ratio ? number(summary.exp_ci_lower) : number(summary.ci_lower)} to {ratio ? number(summary.exp_ci_upper) : number(summary.ci_upper)})
            </span>
          )}
          {summary.p_value != null && <span>p = {number(summary.p_value, 4)}</span>}
          {summary.I2 != null && <span>I² = {number(summary.I2, 1)}%</span>}
          {summary.tau2 != null && <span>τ² = {number(summary.tau2, 4)}</span>}
          {summary.pi_lower != null && (
            <span>
              Prediction interval {ratio ? number(summary.exp_pi_lower) : number(summary.pi_lower)} to {ratio ? number(summary.exp_pi_upper) : number(summary.pi_upper)}
            </span>
          )}
        </div>
      )}
      {[...(results.warnings ?? []), ...(results.notes ?? [])].map((note: string) => (
        <p key={note} style={{ ...muted, color: AMBER }}>
          {note}
        </p>
      ))}
      {studies.length > 0 && (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem', marginBottom: '12px' }}>
          <thead>
            <tr>
              <th style={{ textAlign: 'left' }}>Study</th>
              <th>Estimate (95% CI)</th>
              <th>Weight</th>
            </tr>
          </thead>
          <tbody>
            {studies.map(s => (
              <tr key={s.study}>
                <td>{s.study}</td>
                <td style={{ textAlign: 'center' }}>
                  {s.exp_yi != null ? `${number(s.exp_yi)} (${number(s.exp_ci_lower)} to ${number(s.exp_ci_upper)})` : `${number(s.yi)} (${number(s.ci_lower)} to ${number(s.ci_upper)})`}
                </td>
                <td style={{ textAlign: 'center' }}>{typeof s.weight === 'number' ? `${fmt(s.weight, 1)}%` : '–'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '16px' }}>
        {Object.entries(plotUrls).map(([name, url]) => (
          <figure key={name} style={{ margin: 0, background: '#ffffff', border: '1px solid var(--border)', borderRadius: '8px', padding: '8px', maxWidth: '100%' }}>
            <img src={url} alt={`${name.replace(/_/g, ' ')} plot`} style={{ maxWidth: '640px', width: '100%' }} />
            <figcaption style={{ ...row, color: '#111', fontSize: '0.8rem' }}>
              {name.replace(/_/g, ' ')}
              {run.plots
                .filter(p => p.name === name)
                .map(p => (
                  <button key={p.format} className="btn-glass" style={{ ...smallButton, color: '#111' }} onClick={() => onDownload(`analysis-runs/${run.id}/plots/${p.name}.${p.format}`, `${p.name}.${p.format}`)}>
                    {p.format.toUpperCase()}
                  </button>
                ))}
            </figcaption>
          </figure>
        ))}
      </div>
      <details style={{ marginTop: '12px' }}>
        <summary>All results (JSON)</summary>
        <pre style={{ whiteSpace: 'pre-wrap', fontSize: '0.75rem' }}>{JSON.stringify(results, null, 2)}</pre>
      </details>
      <details>
        <summary>R script</summary>
        <pre style={{ whiteSpace: 'pre-wrap', fontSize: '0.75rem' }}>{run.script}</pre>
      </details>
      <details>
        <summary>R session</summary>
        <pre style={{ whiteSpace: 'pre-wrap', fontSize: '0.75rem' }}>{run.session_info || run.log}</pre>
      </details>
    </section>
  );
}

type IpdProps = {
  studies: StudyInfo[];
  datasets: IpdInfo[];
  busy: boolean;
  onUpload: (studyId: number, file: File) => void;
  onMap: (dataset: IpdInfo, mapping: { treatment: string; outcome: string; outcome_type: string }) => void;
};

function IpdPanel({ studies, datasets, busy, onUpload, onMap }: IpdProps) {
  const [mappings, setMappings] = useState<Record<number, { treatment: string; outcome: string; outcome_type: string }>>({});
  return (
    <section aria-label="Individual participant data" style={{ ...panel, marginBottom: '24px' }}>
      <h4 style={{ marginTop: 0 }}>Individual participant data</h4>
      <p style={muted}>Upload one CSV per study (one row per participant). Files are encrypted at rest, never exported, and every access is recorded in the audit trail. Code treatment as 1/0.</p>
      {studies.map(study => {
        const dataset = datasets.find(d => d.study_id === study.id);
        const mapping = mappings[study.id] ?? { treatment: dataset?.mapping.treatment ?? '', outcome: dataset?.mapping.outcome ?? '', outcome_type: dataset?.validation.outcome_type ?? 'binary' };
        const setMapping = (change: Partial<typeof mapping>) => setMappings(prev => ({ ...prev, [study.id]: { ...mapping, ...change } }));
        return (
          <div key={study.id} style={{ ...row, borderTop: '1px solid var(--border)', padding: '8px 0' }}>
            <strong style={{ minWidth: '160px' }}>{study.label}</strong>
            <input aria-label={`Participant data for ${study.label}`} type="file" accept=".csv,text/csv" disabled={busy} onChange={e => e.target.files?.[0] && onUpload(study.id, e.target.files[0])} />
            {dataset && (
              <>
                <span style={muted}>
                  {dataset.file_name}: {dataset.rows} participants
                </span>
                {(['treatment', 'outcome'] as const).map(key => (
                  <select key={key} aria-label={`${key} column for ${study.label}`} className="search-input" style={{ width: 'auto' }} value={mapping[key]} onChange={e => setMapping({ [key]: e.target.value })}>
                    <option value="">{key} column…</option>
                    {dataset.columns.map(c => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                ))}
                <select className="search-input" style={{ width: 'auto' }} value={mapping.outcome_type} onChange={e => setMapping({ outcome_type: e.target.value })}>
                  <option value="binary">Binary outcome</option>
                  <option value="continuous">Continuous outcome</option>
                </select>
                <button className="btn-glass" style={smallButton} disabled={busy || !mapping.treatment || !mapping.outcome} onClick={() => onMap(dataset, mapping)}>
                  Map and check
                </button>
                {(dataset.validation.problems ?? []).map(problem => (
                  <span key={problem} style={chip(AMBER)}>
                    {problem}
                  </span>
                ))}
              </>
            )}
          </div>
        );
      })}
    </section>
  );
}
