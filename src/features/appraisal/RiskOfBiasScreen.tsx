import { useCallback, useEffect, useRef, useState } from 'react';
import { errorMessage } from '../../api/client';
import { downloadFile } from '../../api/download';
import {
  JUDGMENT_COLORS,
  type AppraisalOverviewRow,
  type AppraisalSummaryTool,
  type AppraisalTool,
  type AssessmentDetail,
  type ChecklistInfo,
  type ReportingDetail,
} from '../../api/evidence';
import { jobProblem, jobProgress, waitForJob, type AiJob } from '../../api/jobs';
import { useAuth } from '../../auth/authContext';
import { ProgressBar } from '../../components/ProgressBar';
import { GREEN, GREY, chip, fieldLabel, muted, panel, row, smallButton } from '../../components/ui';
import { WorkspaceStageGate } from '../project/WorkspaceStageGate';
import { useWorkspace } from '../project/workspaceContext';

type Draft = { tool: string; outcome: string; reason: string };

const judgmentChip = (key: string | null | undefined, label?: string) => (
  <span style={chip(JUDGMENT_COLORS[key ?? ''] ?? GREY)}>{label ?? key?.replace(/_/g, ' ') ?? 'not judged'}</span>
);

// Risk of bias and quality appraisal: recommended tools, signalling questions with AI suggestions, domain and overall
// sign-off with rationales, summary plots, and reporting checklists.
export function RiskOfBiasScreen() {
  const { projectId, refreshWorkflow, aiModelName, goTo } = useWorkspace();
  const { apiRequest, token } = useAuth();
  const base = `/api/projects/${projectId}`;
  const [overview, setOverview] = useState<AppraisalOverviewRow[]>([]);
  const [tools, setTools] = useState<AppraisalTool[]>([]);
  const [checklists, setChecklists] = useState<ChecklistInfo[]>([]);
  const [summary, setSummary] = useState<AppraisalSummaryTool[]>([]);
  const [drafts, setDrafts] = useState<Record<number, Draft>>({});
  const [assessment, setAssessment] = useState<AssessmentDetail | null>(null);
  const [answers, setAnswers] = useState<Record<string, { answer: string; source: string }>>({});
  const [judgments, setJudgments] = useState<Record<string, { judgment: string; rationale: string }>>({});
  const [reporting, setReporting] = useState<ReportingDetail | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const unmounted = useRef(false);

  const load = useCallback(async () => {
    const [rows, catalog, plots] = await Promise.all([
      apiRequest('GET', `${base}/appraisal/overview`),
      apiRequest('GET', `${base}/appraisal/tools`),
      apiRequest('GET', `${base}/appraisal/summary`),
    ]);
    return { rows: rows as AppraisalOverviewRow[], catalog, plots: plots as AppraisalSummaryTool[] };
  }, [apiRequest, base]);

  const apply = (data: Awaited<ReturnType<typeof load>>) => {
    setOverview(data.rows);
    setTools(data.catalog.tools);
    setChecklists(data.catalog.checklists);
    setSummary(data.plots);
  };

  useEffect(() => {
    unmounted.current = false;
    let cancelled = false;
    load()
      .then(data => {
        if (!cancelled) apply(data);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Could not load the appraisal overview.'));
      });
    return () => {
      cancelled = true;
      unmounted.current = true;
    };
  }, [load]);

  const showAssessment = (detail: AssessmentDetail) => {
    setAssessment(detail);
    setAnswers(Object.fromEntries(Object.entries(detail.answers).map(([id, a]) => [id, { answer: a.answer, source: a.source }])));
    setJudgments(Object.fromEntries(Object.entries(detail.domain_judgments).map(([key, d]) => [key, { judgment: d.judgment, rationale: d.rationale }])));
  };

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

  const toolOf = (key: string) => tools.find(tool => tool.key === key);
  const draftFor = (study: AppraisalOverviewRow): Draft => drafts[study.study_id] ?? { tool: study.recommended_tools[0]?.tool ?? 'rob2', outcome: '', reason: '' };
  const setDraft = (studyId: number, draft: Draft) => setDrafts(prev => ({ ...prev, [studyId]: draft }));

  const create = (study: AppraisalOverviewRow) =>
    act(async () => {
      const draft = draftFor(study);
      const detail = await apiRequest('POST', `${base}/appraisal/assessments`, {
        study_id: study.study_id,
        tool: draft.tool,
        outcome: draft.outcome,
        selection_reason: draft.reason,
      });
      showAssessment(detail);
      return `Started a ${toolOf(draft.tool)?.label ?? draft.tool} assessment of ${study.label}.`;
    }, 'Could not start the assessment.');

  const open = (id: number) => act(async () => showAssessment(await apiRequest('GET', `${base}/appraisal/assessments/${id}`)), 'Could not open the assessment.');

  const saveAnswers = () =>
    act(async () => {
      if (!assessment) return;
      const changed = Object.entries(answers)
        .filter(([id, value]) => assessment.answers[id]?.answer !== value.answer)
        .map(([question_id, value]) => ({ question_id, answer: value.answer, source: value.source }));
      if (changed.length === 0) return 'No answers changed.';
      showAssessment(await apiRequest('PUT', `${base}/appraisal/assessments/${assessment.id}/answers`, { answers: changed }));
      return 'Answers saved. Changed domains need to be signed off again.';
    }, 'Could not save the answers.');

  const signDomain = (domainKey: string) =>
    act(async () => {
      if (!assessment) return;
      const entry = judgments[domainKey];
      showAssessment(await apiRequest('PUT', `${base}/appraisal/assessments/${assessment.id}/domains/${domainKey}`, entry));
      return 'Domain signed off.';
    }, 'Could not sign off the domain.');

  const signOverall = () =>
    act(async () => {
      if (!assessment) return;
      showAssessment(await apiRequest('PUT', `${base}/appraisal/assessments/${assessment.id}/overall`, judgments.__overall));
      return 'Assessment signed off.';
    }, 'Could not sign off the assessment.');

  const remove = () =>
    act(async () => {
      if (!assessment || !window.confirm('Delete this assessment and its answers?')) return;
      await apiRequest('DELETE', `${base}/appraisal/assessments/${assessment.id}`);
      setAssessment(null);
      return 'Assessment deleted.';
    }, 'Could not delete the assessment.');

  const followJob = async (job: AiJob, label: string) => {
    const finished = await waitForJob(job, id => apiRequest('GET', `${base}/jobs/${id}`), update => setProgress(jobProgress(update)), { stopped: () => unmounted.current });
    setTimeout(() => setProgress(null), 1500);
    return jobProblem(finished, label);
  };

  const suggest = () =>
    act(async () => {
      if (!assessment) return;
      const problem = await followJob(await apiRequest('POST', `${base}/appraisal/ai`, { assessment_ids: [assessment.id] }), 'AI appraisal');
      showAssessment(await apiRequest('GET', `${base}/appraisal/assessments/${assessment.id}`));
      return problem ?? `${aiModelName} suggested answers. They are suggestions only: accept or change each one, then save.`;
    }, 'Could not get AI suggestions.');

  const openChecklist = (study: AppraisalOverviewRow) =>
    act(async () => {
      const existing = study.reporting[0];
      if (existing) {
        setReporting(await apiRequest('GET', `${base}/reporting/assessments/${existing.id}`));
        return;
      }
      const checklist = study.recommended_checklist;
      if (!checklist) return 'No reporting checklist is recommended for this study design.';
      setReporting(await apiRequest('POST', `${base}/reporting/assessments`, { study_id: study.study_id, checklist }));
    }, 'Could not open the reporting checklist.');

  const saveChecklist = (items: ReportingDetail['items']) =>
    act(async () => {
      if (!reporting) return;
      const body = { items: items.map(({ item_id, status, location, note, span_ids }) => ({ item_id, status, location, note, span_ids })) };
      setReporting(await apiRequest('PUT', `${base}/reporting/assessments/${reporting.id}/items`, body));
      return 'Checklist saved.';
    }, 'Could not save the checklist.');

  const suggestChecklist = () =>
    act(async () => {
      if (!reporting) return;
      const problem = await followJob(await apiRequest('POST', `${base}/reporting/ai`, { assessment_ids: [reporting.id] }), 'AI reporting check');
      setReporting(await apiRequest('GET', `${base}/reporting/assessments/${reporting.id}`));
      return problem ?? 'AI flagged reporting gaps; review each item.';
    }, 'Could not get AI suggestions.');

  const signChecklist = () =>
    act(async () => {
      if (!reporting) return;
      setReporting(await apiRequest('POST', `${base}/reporting/assessments/${reporting.id}/sign-off`));
      return 'Checklist signed off.';
    }, 'Could not sign off the checklist.');

  const download = (tool: string, kind: string, format: string) =>
    downloadFile(token, `${base}/appraisal/plots/${tool}?kind=${kind}&format=${format}`, `${tool}-${kind}.${format}`).catch(err => setNotice(errorMessage(err, 'The plot could not be drawn.')));

  const tool = assessment ? toolOf(assessment.tool) : undefined;

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
      <h3 style={{ marginBottom: '8px', color: 'var(--text-primary)' }}>Risk of Bias & Quality Appraisal</h3>
      <p style={{ ...muted, marginBottom: '16px' }}>
        Each included study is appraised with the tool suited to its design. Answer the signalling questions; the tool's algorithm suggests judgments, and a reviewer signs off every domain with a rationale. AI suggestions never sign anything off.
      </p>
      <WorkspaceStageGate stage="appraisal" />
      {notice && <p role="status" style={{ ...panel, marginBottom: '16px' }}>{notice}</p>}
      {progress !== null && <ProgressBar progress={progress} label={`Running ${aiModelName}...`} />}

      <h4>Included studies</h4>
      {overview.length === 0 && <p style={muted}>No included studies yet. Sign off extraction first.</p>}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', marginBottom: '24px' }}>
        {overview.map(study => {
          const draft = draftFor(study);
          const recommended = study.recommended_tools.some(r => r.tool === draft.tool);
          const draftTool = toolOf(draft.tool);
          return (
            <div key={study.study_id} style={panel}>
              <div style={{ ...row, justifyContent: 'space-between' }}>
                <strong>{study.label}</strong>
                <span style={muted}>{study.design || 'Study design not extracted'}</span>
              </div>
              {study.recommended_tools.map(r => (
                <p key={r.tool} style={{ ...muted, margin: '4px 0' }}>
                  Recommended: {toolOf(r.tool)?.label ?? r.tool}. {r.reason}
                </p>
              ))}
              <div style={{ ...row, margin: '8px 0' }}>
                {study.assessments.map(a => (
                  <button key={a.id} className="btn-glass" style={smallButton} onClick={() => open(a.id)} disabled={busy}>
                    {toolOf(a.tool)?.label ?? a.tool}
                    {a.outcome ? ` · ${a.outcome}` : ''} · {a.status === 'signed_off' ? '✓ ' : ''}
                    {a.overall_judgment?.replace(/_/g, ' ') ?? 'in progress'}
                  </button>
                ))}
              </div>
              <div style={row}>
                <label style={fieldLabel}>
                  Tool
                  <select className="search-input" value={draft.tool} onChange={e => setDraft(study.study_id, { ...draft, tool: e.target.value })}>
                    {tools.map(t => (
                      <option key={t.key} value={t.key}>
                        {t.label}
                      </option>
                    ))}
                  </select>
                </label>
                {draftTool?.per_outcome && (
                  <label style={fieldLabel}>
                    Outcome (result assessed)
                    <input className="search-input" value={draft.outcome} onChange={e => setDraft(study.study_id, { ...draft, outcome: e.target.value })} />
                  </label>
                )}
                {!recommended && (
                  <label style={fieldLabel}>
                    Why this tool?
                    <input className="search-input" value={draft.reason} onChange={e => setDraft(study.study_id, { ...draft, reason: e.target.value })} />
                  </label>
                )}
                <button className="btn-primary" style={smallButton} disabled={busy} onClick={() => create(study)}>
                  New assessment
                </button>
                <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => openChecklist(study)}>
                  {study.reporting[0] ? `${study.reporting[0].checklist.toUpperCase()} checklist` : 'Reporting checklist'}
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {assessment && tool && (
        <section aria-label="Assessment" style={{ ...panel, marginBottom: '24px' }}>
          <div style={{ ...row, justifyContent: 'space-between' }}>
            <div>
              <h4 style={{ margin: 0 }}>
                {tool.label}: {assessment.study?.label}
                {assessment.outcome ? ` · ${assessment.outcome}` : ''}
              </h4>
              <p style={muted}>
                {tool.version}. {assessment.selection_reason}{' '}
                <a href={tool.guidance_url} target="_blank" rel="noreferrer">
                  Official guidance
                </a>
              </p>
            </div>
            <div style={row}>
              <button className="btn-glass" style={smallButton} disabled={busy} onClick={suggest}>
                Suggest answers with {aiModelName}
              </button>
              <button className="btn-glass" style={smallButton} disabled={busy} onClick={saveAnswers}>
                Save answers
              </button>
              {assessment.status !== 'signed_off' && (
                <button className="btn-glass" style={smallButton} disabled={busy} onClick={remove}>
                  Delete
                </button>
              )}
              <button className="btn-glass" style={smallButton} onClick={() => setAssessment(null)}>
                Close
              </button>
            </div>
          </div>
          {tool.notes.map(note => (
            <p key={note} style={muted}>
              {note}
            </p>
          ))}
          {tool.domains.map(domain => {
            const applicable = assessment.applicable_questions[domain.key] ?? [];
            const suggestion = assessment.suggested.domains[domain.key];
            const signed = assessment.domain_judgments[domain.key];
            const entry = judgments[domain.key] ?? { judgment: suggestion ?? '', rationale: '' };
            return (
              <div key={domain.key} style={{ borderTop: '1px solid var(--border)', paddingTop: '12px', marginTop: '12px' }}>
                <div style={{ ...row, justifyContent: 'space-between' }}>
                  <strong>{domain.label}</strong>
                  <span style={row}>
                    {suggestion && <span style={muted}>Algorithm suggests {judgmentChip(suggestion)}</span>}
                    {signed ? <span style={chip(GREEN)}>Signed off: {signed.judgment.replace(/_/g, ' ')}</span> : <span style={chip(GREY)}>Not signed off</span>}
                  </span>
                </div>
                {domain.questions
                  .filter(question => applicable.includes(question.id))
                  .map(question => {
                    const ai = assessment.ai_suggestions.find(s => s.question_id === question.id);
                    const current = answers[question.id]?.answer ?? '';
                    return (
                      <div key={question.id} style={{ margin: '8px 0' }}>
                        <label style={{ ...row, justifyContent: 'space-between' }}>
                          <span>
                            {question.id} {question.text}
                            {question.critical ? ' (critical)' : ''}
                          </span>
                          <select
                            aria-label={`Answer to ${question.id}`}
                            className="search-input"
                            style={{ width: 'auto' }}
                            value={current}
                            onChange={e => setAnswers(prev => ({ ...prev, [question.id]: { answer: e.target.value, source: 'manual' } }))}
                          >
                            <option value="">—</option>
                            {question.answers.map(a => (
                              <option key={a.key} value={a.key}>
                                {a.label}
                              </option>
                            ))}
                          </select>
                        </label>
                        {ai && (
                          <p style={{ ...muted, margin: '4px 0 0 16px' }}>
                            AI: <strong>{ai.answer}</strong> — {ai.rationale}
                            {ai.quote && ` “${ai.quote}”`} {ai.quote && <span style={chip(ai.grounded ? GREEN : '#C62828')}>{ai.grounded ? 'quote found' : 'quote not found'}</span>}{' '}
                            {ai.answer !== current && (
                              <button className="btn-glass" style={smallButton} onClick={() => setAnswers(prev => ({ ...prev, [question.id]: { answer: ai.answer, source: 'ai_accepted' } }))}>
                                Accept
                              </button>
                            )}
                          </p>
                        )}
                      </div>
                    );
                  })}
                <div style={{ ...row, marginTop: '8px' }}>
                  <select aria-label={`Judgment for ${domain.label}`} className="search-input" style={{ width: 'auto' }} value={entry.judgment} onChange={e => setJudgments(prev => ({ ...prev, [domain.key]: { ...entry, judgment: e.target.value } }))}>
                    <option value="">Judgment…</option>
                    {domain.judgments.map(j => (
                      <option key={j.key} value={j.key}>
                        {j.label}
                      </option>
                    ))}
                  </select>
                  <input
                    aria-label={`Rationale for ${domain.label}`}
                    className="search-input"
                    style={{ flex: 1, minWidth: '240px' }}
                    placeholder="Rationale (required)"
                    value={entry.rationale}
                    onChange={e => setJudgments(prev => ({ ...prev, [domain.key]: { ...entry, rationale: e.target.value } }))}
                  />
                  <button className="btn-primary" style={smallButton} disabled={busy || !entry.judgment || entry.rationale.trim().length < 10} onClick={() => signDomain(domain.key)}>
                    Sign off domain
                  </button>
                </div>
              </div>
            );
          })}
          <div style={{ borderTop: '1px solid var(--border-strong)', paddingTop: '12px', marginTop: '16px' }}>
            <strong>Overall judgment</strong>
            {assessment.suggested.overall && <span style={muted}> · the tool suggests {judgmentChip(assessment.suggested.overall)}</span>}
            {assessment.status === 'signed_off' && (
              <p style={muted}>
                Signed off by {assessment.signed_off_by}: {assessment.overall_judgment?.replace(/_/g, ' ')} — {assessment.overall_rationale}
              </p>
            )}
            <div style={{ ...row, marginTop: '8px' }}>
              <select
                aria-label="Overall judgment"
                className="search-input"
                style={{ width: 'auto' }}
                value={judgments.__overall?.judgment ?? assessment.suggested.overall ?? ''}
                onChange={e => setJudgments(prev => ({ ...prev, __overall: { judgment: e.target.value, rationale: prev.__overall?.rationale ?? '' } }))}
              >
                <option value="">Judgment…</option>
                {tool.overall_judgments.map(j => (
                  <option key={j.key} value={j.key}>
                    {j.label}
                  </option>
                ))}
              </select>
              <input
                aria-label="Overall rationale"
                className="search-input"
                style={{ flex: 1, minWidth: '240px' }}
                placeholder="Rationale (required)"
                value={judgments.__overall?.rationale ?? ''}
                onChange={e => setJudgments(prev => ({ ...prev, __overall: { judgment: prev.__overall?.judgment ?? assessment.suggested.overall ?? '', rationale: e.target.value } }))}
              />
              <button className="btn-primary" style={smallButton} disabled={busy || (judgments.__overall?.rationale ?? '').trim().length < 10} onClick={signOverall}>
                Sign off assessment
              </button>
            </div>
          </div>
        </section>
      )}

      {reporting && (
        <ChecklistEditor
          key={reporting.id}
          detail={reporting}
          checklist={checklists.find(c => c.key === reporting.checklist)}
          busy={busy}
          onSave={saveChecklist}
          onSuggest={suggestChecklist}
          onSignOff={signChecklist}
          onClose={() => setReporting(null)}
        />
      )}

      <h4>Summary</h4>
      {summary.length === 0 && <p style={muted}>Assessments will be summarized here.</p>}
      {summary.map(entry => (
        <div key={entry.tool} style={{ ...panel, marginBottom: '16px', overflowX: 'auto' }}>
          <div style={{ ...row, justifyContent: 'space-between' }}>
            <strong>{entry.label}</strong>
            <span style={row}>
              {['traffic_light', 'summary'].map(kind =>
                ['svg', 'png', 'pdf'].map(format => (
                  <button key={`${kind}-${format}`} className="btn-glass" style={smallButton} onClick={() => download(entry.tool, kind, format)}>
                    {kind === 'traffic_light' ? 'Traffic light' : 'Summary'} {format.toUpperCase()}
                  </button>
                )),
              )}
            </span>
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem', marginTop: '8px' }}>
            <thead>
              <tr>
                <th style={{ textAlign: 'left', padding: '6px' }}>Study</th>
                {entry.domains.map((d, i) => (
                  <th key={d.key} title={d.label} style={{ padding: '6px' }}>
                    D{i + 1}
                  </th>
                ))}
                <th style={{ padding: '6px' }}>Overall</th>
              </tr>
            </thead>
            <tbody>
              {entry.rows.map(r => (
                <tr key={r.assessment_id}>
                  <td style={{ padding: '6px' }}>
                    {r.study}
                    {r.outcome ? ` (${r.outcome})` : ''}
                    {r.status !== 'signed_off' && <span style={muted}> · in progress</span>}
                  </td>
                  {entry.domains.map(d => (
                    <td key={d.key} style={{ padding: '6px', textAlign: 'center' }}>
                      {r.domains[d.key] ? judgmentChip(r.domains[d.key], entry.judgments[r.domains[d.key]]) : '–'}
                    </td>
                  ))}
                  <td style={{ padding: '6px', textAlign: 'center' }}>{r.overall ? judgmentChip(r.overall, entry.judgments[r.overall]) : '–'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p style={muted}>{entry.domains.map((d, i) => `D${i + 1}: ${d.label}`).join(' · ')}</p>
        </div>
      ))}
      <div style={{ textAlign: 'right', marginTop: '24px' }}>
        <button className="btn-primary" onClick={() => goTo('synthesis')}>
          Proceed to synthesis →
        </button>
      </div>
    </section>
  );
}

type ChecklistEditorProps = {
  detail: ReportingDetail;
  checklist: ChecklistInfo | undefined;
  busy: boolean;
  onSave: (items: ReportingDetail['items']) => void;
  onSuggest: () => void;
  onSignOff: () => void;
  onClose: () => void;
};

const STATUS_LABELS: Record<string, string> = { reported: 'Reported', partially_reported: 'Partially reported', not_reported: 'Not reported', not_applicable: 'Not applicable' };

function ChecklistEditor({ detail, checklist, busy, onSave, onSuggest, onSignOff, onClose }: ChecklistEditorProps) {
  const [items, setItems] = useState(detail.items);
  const update = (itemId: string, change: Partial<ReportingDetail['items'][number]>) => setItems(prev => prev.map(item => (item.item_id === itemId ? { ...item, ...change } : item)));
  return (
    <section aria-label="Reporting checklist" style={{ ...panel, marginBottom: '24px', overflowX: 'auto' }}>
      <div style={{ ...row, justifyContent: 'space-between' }}>
        <h4 style={{ margin: 0 }}>
          {checklist?.label ?? detail.checklist} · {detail.status === 'signed_off' ? 'signed off' : 'in progress'}
        </h4>
        <span style={row}>
          <button className="btn-glass" style={smallButton} disabled={busy} onClick={onSuggest}>
            AI check
          </button>
          <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => onSave(items)}>
            Save
          </button>
          <button className="btn-primary" style={smallButton} disabled={busy} onClick={onSignOff}>
            Sign off
          </button>
          <button className="btn-glass" style={smallButton} onClick={onClose}>
            Close
          </button>
        </span>
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem', marginTop: '8px' }}>
        <tbody>
          {items.map(item => (
            <tr key={item.item_id} style={{ borderBottom: '1px solid var(--border)' }}>
              <td style={{ padding: '6px', width: '48px' }}>{item.item_id}</td>
              <td style={{ padding: '6px' }}>
                <div>{item.topic}</div>
                <div style={muted}>{item.section}</div>
                {item.ai_status && (
                  <div style={muted}>
                    AI: {STATUS_LABELS[item.ai_status] ?? item.ai_status} — {item.ai_rationale}
                    {item.ai_quote && ` “${item.ai_quote}”`}
                    {item.ai_quote && <span style={chip(item.ai_grounded ? GREEN : '#C62828')}>{item.ai_grounded ? 'quote found' : 'quote not found'}</span>}
                  </div>
                )}
              </td>
              <td style={{ padding: '6px' }}>
                <select aria-label={`Status of item ${item.item_id}`} className="search-input" value={item.status} onChange={e => update(item.item_id, { status: e.target.value })}>
                  <option value="">—</option>
                  {Object.entries(STATUS_LABELS).map(([key, label]) => (
                    <option key={key} value={key}>
                      {label}
                    </option>
                  ))}
                </select>
              </td>
              <td style={{ padding: '6px' }}>
                <input aria-label={`Location of item ${item.item_id}`} className="search-input" placeholder="Page or section" value={item.location} onChange={e => update(item.item_id, { location: e.target.value })} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
