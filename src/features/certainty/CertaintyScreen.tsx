import { useCallback, useEffect, useState } from 'react';
import { errorMessage } from '../../api/client';
import { downloadFile } from '../../api/download';
import {
  CERTAINTY_SYMBOLS,
  type CertaintyCatalog,
  type EtdInfo,
  type GradeInfo,
  type InterpretationInfo,
  type OutcomeToGrade,
  type PriorComparison,
  type PriorReviewInfo,
  type SofRow,
} from '../../api/evidence';
import { useAuth } from '../../auth/authContext';
import { AMBER, GREEN, GREY, RED, chip, fieldLabel, muted, panel, row, smallButton } from '../../components/ui';
import { WorkspaceStageGate } from '../project/WorkspaceStageGate';
import { useWorkspace } from '../project/workspaceContext';

type Sof = { intervention: string; columns: string[]; rows: SofRow[]; table: string[][] };
type TopicReview = { title: string; doi: string; year: string; source: string };
const RATING_LABELS: Record<number, string> = { [-2]: 'Very serious (−2)', [-1]: 'Serious (−1)', 0: 'Not serious (0)', 1: 'Rate up (+1)', 2: 'Rate up (+2)' };
const CERTAINTY_COLORS: Record<string, string> = { high: GREEN, moderate: GREEN, low: AMBER, very_low: RED };
const certaintyColor = (level: string) => CERTAINTY_COLORS[level] ?? GREY;

// Certainty of evidence (GRADE), summary of findings, Evidence to Decision, comparison with prior reviews, and
// interpretive text approved by a clinical expert.
export function CertaintyScreen() {
  const { projectId, refreshWorkflow, aiModelName } = useWorkspace();
  const { apiRequest, token } = useAuth();
  const base = `/api/projects/${projectId}`;
  const [catalog, setCatalog] = useState<CertaintyCatalog | null>(null);
  const [outcomes, setOutcomes] = useState<OutcomeToGrade[]>([]);
  const [grades, setGrades] = useState<GradeInfo[]>([]);
  const [sof, setSof] = useState<Sof | null>(null);
  const [etds, setEtds] = useState<EtdInfo[]>([]);
  const [priors, setPriors] = useState<{ reviews: PriorReviewInfo[]; from_topic_exploration: TopicReview[] }>({ reviews: [], from_topic_exploration: [] });
  const [comparison, setComparison] = useState<PriorComparison | null>(null);
  const [texts, setTexts] = useState<InterpretationInfo[]>([]);
  const [editing, setEditing] = useState<GradeInfo | null>(null);
  const [etd, setEtd] = useState<EtdInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    const results = await Promise.all([
      apiRequest('GET', `${base}/certainty/catalog`),
      apiRequest('GET', `${base}/certainty/outcomes`),
      apiRequest('GET', `${base}/grade`),
      apiRequest('GET', `${base}/summary-of-findings`),
      apiRequest('GET', `${base}/etd`),
      apiRequest('GET', `${base}/prior-reviews`),
      apiRequest('GET', `${base}/interpretation`),
    ]);
    return results;
  }, [apiRequest, base]);

  const apply = ([catalogData, outcomeList, gradeList, sofData, etdList, priorData, textList]: Awaited<ReturnType<typeof load>>) => {
    setCatalog(catalogData);
    setOutcomes(outcomeList);
    setGrades(gradeList);
    setSof(sofData);
    setEtds(etdList);
    setPriors(priorData);
    setTexts(textList);
  };

  useEffect(() => {
    let cancelled = false;
    load()
      .then(data => {
        if (!cancelled) apply(data);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Could not load the certainty assessments.'));
      });
    return () => {
      cancelled = true;
    };
  }, [load]);

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

  const gradeBody = (grade: GradeInfo) => ({
    outcome: grade.outcome,
    comparison: grade.comparison,
    analysis_id: grade.analysis_id,
    importance: grade.importance,
    starting_certainty: grade.starting_certainty,
    domains: grade.domains,
    mid: grade.mid,
    mid_scale: grade.mid_scale,
    outcome_direction: grade.outcome_direction,
    baseline_risks: grade.baseline_risks,
  });

  const startGrade = (outcome: OutcomeToGrade) =>
    act(async () => {
      setEditing(await apiRequest('POST', `${base}/grade`, { outcome: outcome.outcome, analysis_id: outcome.analysis_id }));
      return 'GRADE assessment started with suggested ratings from the analysis.';
    }, 'Could not start the GRADE assessment.');

  const saveGrade = (grade: GradeInfo) =>
    act(async () => {
      setEditing(await apiRequest('PUT', `${base}/grade/${grade.id}`, gradeBody(grade)));
      return 'Ratings saved.';
    }, 'Could not save the ratings.');

  const signGrade = (grade: GradeInfo) => {
    const note = window.prompt('Sign off this GRADE assessment. Note for the audit trail:');
    if (note === null) return;
    act(async () => {
      setEditing(await apiRequest('POST', `${base}/grade/${grade.id}/sign-off`, { note }));
      return 'GRADE assessment signed off.';
    }, 'Could not sign off.');
  };

  const saveEtd = (framework: EtdInfo) =>
    act(async () => {
      const body = { title: framework.title, question: framework.question, perspective: framework.perspective, criteria: framework.criteria, conclusions: framework.conclusions, grade_assessment_ids: framework.grade_assessment_ids };
      setEtd(framework.id ? await apiRequest('PUT', `${base}/etd/${framework.id}`, body) : await apiRequest('POST', `${base}/etd`, body));
      return 'Evidence to Decision framework saved.';
    }, 'Could not save the framework.');

  const signEtd = (framework: EtdInfo) => {
    const note = window.prompt('Sign off this Evidence to Decision framework. Note for the audit trail:');
    if (note === null) return;
    act(async () => {
      setEtd(await apiRequest('POST', `${base}/etd/${framework.id}/sign-off`, { note }));
      return 'Framework signed off.';
    }, 'Could not sign off the framework.');
  };

  const addPrior = (review: { title: string; doi: string; year: string; outcome?: string; conclusion?: string; conclusion_direction?: string }) =>
    act(async () => {
      const added = await apiRequest('POST', `${base}/prior-reviews`, { outcome: '', conclusion: '', conclusion_direction: '', ...review });
      return added.warning ?? `Added "${added.title}" with ${added.references} references.`;
    }, 'Could not add the review.');

  const compare = () =>
    act(async () => {
      setComparison(await apiRequest('GET', `${base}/prior-reviews/comparison`));
    }, 'Could not compare with prior reviews.');

  const generate = (kind: 'informative_statement' | 'limitations') =>
    act(async () => {
      await apiRequest('POST', `${base}/interpretation/rules`, { kind });
      return kind === 'limitations' ? 'Limitations drafted from the GRADE ratings and analysis notes.' : 'Informative statements drafted with GRADE wording.';
    }, 'Could not draft the text.');

  const plainLanguage = () =>
    act(async () => {
      const text: InterpretationInfo = await apiRequest('POST', `${base}/interpretation/plain-language`);
      return text.unverified_numbers.length ? `Drafted. Check these numbers, which aren't in the summary of findings: ${text.unverified_numbers.join(', ')}` : 'Plain-language summary drafted; every number matches the summary of findings.';
    }, 'Could not draft the plain-language summary.');

  const saveText = (text: InterpretationInfo, content: string) =>
    act(async () => {
      await apiRequest('PUT', `${base}/interpretation/${text.id}`, { content });
      return 'Text saved as a draft.';
    }, 'Could not save the text.');

  const approveText = (text: InterpretationInfo) =>
    act(async () => {
      await apiRequest('POST', `${base}/interpretation/${text.id}/approve`);
      return 'Text approved.';
    }, 'Could not approve the text.');

  const download = (format: 'csv' | 'docx') => downloadFile(token, `${base}/summary-of-findings?format=${format}`, `summary-of-findings.${format}`).catch(err => setNotice(errorMessage(err, 'The download failed.')));

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
      <h3 style={{ marginBottom: '8px', color: 'var(--text-primary)' }}>Certainty of Evidence & Interpretation</h3>
      <p style={{ ...muted, marginBottom: '16px' }}>
        Rate each outcome's certainty with GRADE. Suggested ratings come from the analyses and risk of bias assessments; a methodologist sets and justifies each rating. Interpretive text needs a clinical expert's approval.
      </p>
      <WorkspaceStageGate stage="certainty" />
      {notice && <p role="status" style={{ ...panel, marginBottom: '16px' }}>{notice}</p>}

      <h4>Outcomes</h4>
      {outcomes.length === 0 && <p style={muted}>Outcomes appear here once their analyses are approved and have final results.</p>}
      <div style={{ ...row, marginBottom: '16px' }}>
        {outcomes.map(outcome => {
          const grade = grades.find(g => g.id === outcome.grade_assessment_id);
          return grade ? (
            <button key={outcome.analysis_id} className="btn-glass" style={smallButton} onClick={() => setEditing(grade)}>
              {outcome.outcome} · {CERTAINTY_SYMBOLS[grade.certainty]} {grade.status === 'signed_off' ? '✓' : ''}
            </button>
          ) : (
            <button key={outcome.analysis_id} className="btn-primary" style={smallButton} disabled={busy} onClick={() => startGrade(outcome)}>
              Assess {outcome.outcome}
            </button>
          );
        })}
      </div>

      {editing && catalog && <GradeEditor key={`${editing.id}-${editing.status}-${editing.certainty}`} grade={editing} catalog={catalog} busy={busy} onSave={saveGrade} onSignOff={signGrade} onClose={() => setEditing(null)} />}

      <div style={{ ...row, justifyContent: 'space-between', marginTop: '24px' }}>
        <h4>Summary of findings</h4>
        <span style={row}>
          <button className="btn-glass" style={smallButton} onClick={() => download('csv')}>
            CSV
          </button>
          <button className="btn-glass" style={smallButton} onClick={() => download('docx')}>
            Word
          </button>
        </span>
      </div>
      {sof && sof.rows.length > 0 ? (
        <div style={{ overflowX: 'auto', ...panel }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
            <thead>
              <tr>
                {sof.columns.map(c => (
                  <th key={c} style={{ textAlign: 'left', padding: '6px', borderBottom: '1px solid var(--border)' }}>
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sof.table.map((cells, i) => (
                <tr key={i}>
                  {cells.map((cell, j) => (
                    <td key={j} style={{ padding: '6px', verticalAlign: 'top' }}>
                      {sof.columns[j] === 'Certainty' ? <span style={chip(certaintyColor(cell.toLowerCase().replace(' ', '_')))}>{CERTAINTY_SYMBOLS[cell.toLowerCase().replace(' ', '_')]} {cell}</span> : cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p style={muted}>No GRADE assessments yet.</p>
      )}

      <div style={{ ...row, justifyContent: 'space-between', marginTop: '24px' }}>
        <h4>Evidence to Decision</h4>
        <button className="btn-glass" style={smallButton} onClick={() => setEtd({ id: 0, title: 'Should the intervention be used?', question: '', perspective: 'clinical_population', criteria: {}, conclusions: {}, grade_assessment_ids: grades.map(g => g.id), suggested_certainty: null, status: 'draft', signed_off_at: null })}>
          New framework
        </button>
      </div>
      <div style={row}>
        {etds.map(framework => (
          <button key={framework.id} className="btn-glass" style={smallButton} onClick={() => setEtd(framework)}>
            {framework.title} · {framework.status === 'signed_off' ? '✓ signed off' : 'draft'}
          </button>
        ))}
      </div>
      {etd && catalog && <EtdEditor key={`${etd.id}-${etd.status}`} framework={etd} catalog={catalog} grades={grades} busy={busy} onSave={saveEtd} onSignOff={signEtd} onClose={() => setEtd(null)} />}

      <h4 style={{ marginTop: '24px' }}>Prior reviews</h4>
      <PriorReviews priors={priors} comparison={comparison} busy={busy} onAdd={addPrior} onCompare={compare} />

      <h4 style={{ marginTop: '24px' }}>Interpretation</h4>
      <div style={{ ...row, marginBottom: '12px' }}>
        <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => generate('informative_statement')}>
          Draft informative statements
        </button>
        <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => generate('limitations')}>
          Draft limitations
        </button>
        <button className="btn-glass" style={smallButton} disabled={busy} onClick={plainLanguage}>
          Draft plain-language summary with {aiModelName}
        </button>
      </div>
      {texts.map(text => (
        <InterpretationCard key={`${text.id}-${text.updated_at}`} text={text} busy={busy} onSave={saveText} onApprove={approveText} />
      ))}
    </section>
  );
}

type GradeEditorProps = { grade: GradeInfo; catalog: CertaintyCatalog; busy: boolean; onSave: (grade: GradeInfo) => void; onSignOff: (grade: GradeInfo) => void; onClose: () => void };

function GradeEditor({ grade, catalog, busy, onSave, onSignOff, onClose }: GradeEditorProps) {
  const [draft, setDraft] = useState(grade);
  const setDomain = (key: string, change: Partial<{ rating: number; rationale: string }>) =>
    setDraft(prev => ({ ...prev, domains: { ...prev.domains, [key]: { ...(prev.domains[key] ?? { rating: 0, rationale: '' }), ...change } } }));
  const domains = [...Object.entries(catalog.downgrade_domains).map(([key, label]) => ({ key, label, ratings: [0, -1, -2] })), ...(draft.starting_certainty === 'low' ? Object.entries(catalog.upgrade_domains).map(([key, label]) => ({ key, label, ratings: [0, 1, 2] })) : [])];
  const sof = grade.summary_of_findings;
  return (
    <section aria-label={`GRADE assessment of ${grade.outcome}`} style={{ ...panel, marginBottom: '16px' }}>
      <div style={{ ...row, justifyContent: 'space-between' }}>
        <h4 style={{ margin: 0 }}>
          {grade.outcome} <span style={chip(certaintyColor(grade.certainty))}>{CERTAINTY_SYMBOLS[grade.certainty]} {grade.certainty.replace('_', ' ')}</span> {grade.status === 'signed_off' && <span style={chip(GREEN)}>Signed off by {grade.signed_off_by}</span>}
        </h4>
        <span style={row}>
          <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => onSave(draft)}>
            Save
          </button>
          <button className="btn-primary" style={smallButton} disabled={busy || grade.status === 'signed_off'} onClick={() => onSignOff(grade)}>
            Sign off
          </button>
          <button className="btn-glass" style={smallButton} onClick={onClose}>
            Close
          </button>
        </span>
      </div>
      <p style={muted}>
        {sof.relative_effect || 'No pooled estimate'} · {sof.studies ?? '–'} studies{sof.participants ? `, ${sof.participants} participants` : ''} · {sof.informative_statement}
      </p>
      <div style={row}>
        <label style={fieldLabel}>
          Importance
          <select className="search-input" value={draft.importance} onChange={e => setDraft({ ...draft, importance: e.target.value as GradeInfo['importance'] })}>
            <option value="critical">Critical</option>
            <option value="important">Important</option>
            <option value="not_important">Not important</option>
          </select>
        </label>
        <label style={fieldLabel}>
          Evidence starts at
          <select className="search-input" value={draft.starting_certainty} onChange={e => setDraft({ ...draft, starting_certainty: e.target.value as GradeInfo['starting_certainty'] })}>
            <option value="high">High (randomized trials)</option>
            <option value="low">Low (observational studies)</option>
          </select>
        </label>
        <label style={fieldLabel}>
          Lower values are
          <select className="search-input" value={draft.outcome_direction} onChange={e => setDraft({ ...draft, outcome_direction: e.target.value as GradeInfo['outcome_direction'] })}>
            <option value="lower_is_better">Better (for example deaths)</option>
            <option value="higher_is_better">Worse (for example recovery)</option>
          </select>
        </label>
        <label style={fieldLabel}>
          Minimal important difference
          <input className="search-input" type="number" value={draft.mid ?? ''} onChange={e => setDraft({ ...draft, mid: e.target.value ? Number(e.target.value) : null })} />
        </label>
        <label style={fieldLabel}>
          MID scale
          <select className="search-input" value={draft.mid_scale} onChange={e => setDraft({ ...draft, mid_scale: e.target.value as GradeInfo['mid_scale'] })}>
            <option value="">—</option>
            <option value="per_1000">Per 1000 (risk difference)</option>
            <option value="units">Outcome units</option>
          </select>
        </label>
      </div>
      <div style={{ ...row, marginTop: '8px' }}>
        <span style={muted}>Baseline risks for absolute effects:</span>
        {draft.baseline_risks.map((risk, i) => (
          <span key={i} style={row}>
            <input aria-label={`Baseline risk label ${i + 1}`} className="search-input" style={{ width: '160px' }} value={risk.label} onChange={e => setDraft({ ...draft, baseline_risks: draft.baseline_risks.map((r, j) => (j === i ? { ...r, label: e.target.value } : r)) })} />
            <input aria-label={`Baseline risk ${i + 1}`} className="search-input" style={{ width: '90px' }} type="number" step="0.01" min="0" max="1" value={risk.risk} onChange={e => setDraft({ ...draft, baseline_risks: draft.baseline_risks.map((r, j) => (j === i ? { ...r, risk: Number(e.target.value) } : r)) })} />
          </span>
        ))}
        <button className="btn-glass" style={smallButton} onClick={() => setDraft({ ...draft, baseline_risks: [...draft.baseline_risks, { label: 'Study population', risk: 0.1 }] })}>
          Add baseline risk
        </button>
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem', marginTop: '12px' }}>
        <tbody>
          {domains.map(domain => {
            const suggestion = grade.suggestions[domain.key];
            const entry = draft.domains[domain.key];
            return (
              <tr key={domain.key} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '6px', width: '180px' }}>
                  <strong>{domain.label}</strong>
                  {suggestion && <div style={muted}>Suggested: {RATING_LABELS[suggestion.suggested_rating]}</div>}
                </td>
                <td style={{ padding: '6px' }}>
                  {suggestion && <div style={muted}>{suggestion.reason}</div>}
                  <input aria-label={`Rationale for ${domain.label}`} className="search-input" placeholder="Rationale (required when rating up or down)" value={entry?.rationale ?? ''} onChange={e => setDomain(domain.key, { rationale: e.target.value })} />
                </td>
                <td style={{ padding: '6px', width: '180px' }}>
                  <select aria-label={`Rating for ${domain.label}`} className="search-input" value={entry ? String(entry.rating) : ''} onChange={e => setDomain(domain.key, { rating: Number(e.target.value) })}>
                    <option value="">Not rated</option>
                    {domain.ratings.map(r => (
                      <option key={r} value={r}>
                        {RATING_LABELS[r]}
                      </option>
                    ))}
                  </select>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

type EtdEditorProps = { framework: EtdInfo; catalog: CertaintyCatalog; grades: GradeInfo[]; busy: boolean; onSave: (framework: EtdInfo) => void; onSignOff: (framework: EtdInfo) => void; onClose: () => void };

function EtdEditor({ framework, catalog, grades, busy, onSave, onSignOff, onClose }: EtdEditorProps) {
  const [draft, setDraft] = useState(framework);
  const setCriterion = (key: string, change: Partial<EtdInfo['criteria'][string]>) =>
    setDraft(prev => ({ ...prev, criteria: { ...prev.criteria, [key]: { ...(prev.criteria[key] ?? { judgment: '', research_evidence: '', additional_considerations: '' }), ...change } } }));
  const setConclusion = (key: string, value: string) => setDraft(prev => ({ ...prev, conclusions: { ...prev.conclusions, [key]: value } }));
  return (
    <section aria-label="Evidence to Decision framework" style={{ ...panel, margin: '12px 0' }}>
      <div style={{ ...row, justifyContent: 'space-between' }}>
        <input aria-label="Framework title" className="search-input" style={{ flex: 1 }} value={draft.title} onChange={e => setDraft({ ...draft, title: e.target.value })} />
        <span style={row}>
          <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => onSave(draft)}>
            Save
          </button>
          {draft.id > 0 && (
            <button className="btn-primary" style={smallButton} disabled={busy || framework.status === 'signed_off'} onClick={() => onSignOff(draft)}>
              Sign off
            </button>
          )}
          <button className="btn-glass" style={smallButton} onClick={onClose}>
            Close
          </button>
        </span>
      </div>
      <div style={{ ...row, margin: '8px 0' }}>
        <span style={muted}>Outcomes:</span>
        {grades.map(g => (
          <label key={g.id} style={row}>
            <input type="checkbox" checked={draft.grade_assessment_ids.includes(g.id)} onChange={e => setDraft({ ...draft, grade_assessment_ids: e.target.checked ? [...draft.grade_assessment_ids, g.id] : draft.grade_assessment_ids.filter(id => id !== g.id) })} /> {g.outcome}
          </label>
        ))}
        {framework.suggested_certainty && <span style={muted}>Overall certainty (lowest critical outcome): {framework.suggested_certainty}</span>}
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
        <tbody>
          {catalog.etd_criteria.map(criterion => (
            <tr key={criterion.key} style={{ borderTop: '1px solid var(--border)' }}>
              <td style={{ padding: '6px', width: '30%' }}>{criterion.label}</td>
              <td style={{ padding: '6px', width: '22%' }}>
                <select aria-label={criterion.label} className="search-input" value={draft.criteria[criterion.key]?.judgment ?? ''} onChange={e => setCriterion(criterion.key, { judgment: e.target.value })}>
                  <option value="">Judgment…</option>
                  {criterion.options.map(option => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </td>
              <td style={{ padding: '6px' }}>
                <input className="search-input" placeholder="Research evidence" value={draft.criteria[criterion.key]?.research_evidence ?? ''} onChange={e => setCriterion(criterion.key, { research_evidence: e.target.value })} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ ...row, marginTop: '12px' }}>
        <label style={fieldLabel}>
          Type of recommendation
          <select className="search-input" value={draft.conclusions.recommendation_type ?? ''} onChange={e => setConclusion('recommendation_type', e.target.value)}>
            <option value="">Choose…</option>
            {catalog.recommendation_types.map(type => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
        </label>
        {['recommendation', 'justification', 'subgroup_considerations', 'implementation', 'monitoring', 'research_priorities'].map(key => (
          <label key={key} style={{ ...fieldLabel, flex: 1, minWidth: '220px' }}>
            {key.replace(/_/g, ' ')}
            <textarea className="search-input" rows={2} value={draft.conclusions[key] ?? ''} onChange={e => setConclusion(key, e.target.value)} />
          </label>
        ))}
      </div>
    </section>
  );
}

type PriorProps = {
  priors: { reviews: PriorReviewInfo[]; from_topic_exploration: TopicReview[] };
  comparison: PriorComparison | null;
  busy: boolean;
  onAdd: (review: { title: string; doi: string; year: string; outcome?: string; conclusion?: string; conclusion_direction?: string }) => void;
  onCompare: () => void;
};

const DIRECTION_LABELS: Record<string, string> = { favours_intervention: 'Favours intervention', favours_comparator: 'Favours comparator', no_difference: 'No difference', uncertain: 'Uncertain' };

function PriorReviews({ priors, comparison, busy, onAdd, onCompare }: PriorProps) {
  const [form, setForm] = useState({ title: '', doi: '', year: '', outcome: '', conclusion: '', conclusion_direction: '' });
  const known = new Set(priors.reviews.map(r => r.doi.toLowerCase()).filter(Boolean));
  return (
    <div style={panel}>
      {priors.reviews.map(review => (
        <p key={review.id} style={{ margin: '4px 0' }}>
          {review.title} {review.year && `(${review.year})`} <span style={muted}>· {review.references} references{review.outcome ? ` · ${review.outcome}: ${DIRECTION_LABELS[review.conclusion_direction] ?? 'no conclusion recorded'}` : ''}</span>
        </p>
      ))}
      {priors.from_topic_exploration.filter(r => r.doi && !known.has(r.doi.toLowerCase())).length > 0 && (
        <div style={{ margin: '8px 0' }}>
          <span style={muted}>Found during topic exploration:</span>
          {priors.from_topic_exploration
            .filter(r => r.doi && !known.has(r.doi.toLowerCase()))
            .map(r => (
              <div key={r.doi} style={row}>
                {r.title} ({r.year}){' '}
                <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => onAdd({ title: r.title, doi: r.doi, year: r.year })}>
                  Add
                </button>
              </div>
            ))}
        </div>
      )}
      <div style={{ ...row, marginTop: '8px' }}>
        {(['title', 'doi', 'year', 'outcome', 'conclusion'] as const).map(key => (
          <input key={key} aria-label={`Prior review ${key}`} className="search-input" style={{ width: key === 'title' || key === 'conclusion' ? '260px' : '140px' }} placeholder={key.toUpperCase() === 'DOI' ? 'DOI' : key[0].toUpperCase() + key.slice(1)} value={form[key]} onChange={e => setForm({ ...form, [key]: e.target.value })} />
        ))}
        <select aria-label="Prior review conclusion" className="search-input" style={{ width: 'auto' }} value={form.conclusion_direction} onChange={e => setForm({ ...form, conclusion_direction: e.target.value })}>
          <option value="">Conclusion…</option>
          {Object.entries(DIRECTION_LABELS).map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
        <button className="btn-glass" style={smallButton} disabled={busy || !form.title} onClick={() => onAdd(form)}>
          Add review
        </button>
        <button className="btn-primary" style={smallButton} disabled={busy || priors.reviews.length === 0} onClick={onCompare}>
          Compare overlap and conclusions
        </button>
      </div>
      {comparison && (
        <div style={{ marginTop: '12px', overflowX: 'auto' }}>
          <p>
            Corrected covered area: {comparison.corrected_covered_area == null ? '–' : `${(comparison.corrected_covered_area * 100).toFixed(1)}%`} ({comparison.overlap})
          </p>
          <p style={muted}>{comparison.lookup_error ?? comparison.note}</p>
          <table style={{ borderCollapse: 'collapse', fontSize: '0.8rem' }}>
            <thead>
              <tr>
                <th style={{ textAlign: 'left', padding: '4px' }}>Included study</th>
                {comparison.reviews.map((r, i) => (
                  <th key={r.id} title={r.title} style={{ padding: '4px' }}>
                    R{i + 1}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {comparison.matrix.map(m => (
                <tr key={m.study_id}>
                  <td style={{ padding: '4px' }}>
                    {m.label}
                    {!m.identified && <span style={muted}> (not found in OpenAlex)</span>}
                  </td>
                  {m.cited_by.map((cited, i) => (
                    <td key={i} style={{ padding: '4px', textAlign: 'center' }}>
                      {cited ? '●' : ''}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {comparison.conclusions.map(c => (
            <p key={c.prior_review_id} style={{ color: c.changed ? AMBER : 'var(--text-secondary)' }}>
              {c.outcome}: prior review {DIRECTION_LABELS[c.prior]}, this review {DIRECTION_LABELS[c.this_review]}
              {c.changed ? ' — conclusion changed' : ''}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

const KIND_LABELS: Record<string, string> = { informative_statement: 'Informative statement', limitations: 'Limitations', plain_language_summary: 'Plain-language summary' };

function InterpretationCard({ text, busy, onSave, onApprove }: { text: InterpretationInfo; busy: boolean; onSave: (text: InterpretationInfo, content: string) => void; onApprove: (text: InterpretationInfo) => void }) {
  const [content, setContent] = useState(text.content);
  return (
    <div style={{ ...panel, marginBottom: '8px' }}>
      <div style={{ ...row, justifyContent: 'space-between' }}>
        <strong>
          {KIND_LABELS[text.kind]}
          {text.outcome ? `: ${text.outcome}` : ''}
        </strong>
        <span style={row}>
          <span style={chip(GREY)}>{text.generated_by === 'ai' ? 'AI draft' : text.generated_by === 'rules' ? 'From ratings' : 'Reviewer'}</span>
          <span style={chip(text.status === 'approved' ? GREEN : AMBER)}>{text.status === 'approved' ? 'Approved' : 'Draft'}</span>
        </span>
      </div>
      <textarea aria-label={`${KIND_LABELS[text.kind]} text`} className="search-input" rows={Math.min(8, Math.max(2, content.split('\n').length + 1))} style={{ width: '100%', marginTop: '8px' }} value={content} onChange={e => setContent(e.target.value)} />
      {text.unverified_numbers.length > 0 && <p style={{ color: RED, fontSize: '0.85rem' }}>Not in the summary of findings: {text.unverified_numbers.join(', ')}</p>}
      <div style={row}>
        <button className="btn-glass" style={smallButton} disabled={busy || content === text.content} onClick={() => onSave(text, content)}>
          Save edit
        </button>
        <button className="btn-primary" style={smallButton} disabled={busy || text.status === 'approved' || content !== text.content} onClick={() => onApprove(text)}>
          Approve
        </button>
      </div>
    </div>
  );
}
