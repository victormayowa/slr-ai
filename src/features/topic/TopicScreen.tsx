import { useEffect, useState } from 'react';
import { errorMessage } from '../../api/client';
import type { ProtocolSuggestion, ReviewQuestion } from '../../api/protocol';
import {
  DEFAULT_ASSUMPTIONS, GAP_LABELS,
  type ExplorationSummary, type TopicExploration, type TopicQuestion, type TopicQuestions, type WorkloadAssumptions, type WorkloadFigures,
} from '../../api/topic';
import { useAuth } from '../../auth/authContext';
import { useWorkspace } from '../project/workspaceContext';

const panel = { background: 'var(--surface-muted)', borderRadius: '12px', padding: '16px' } as const;
const FEASIBILITY_COLORS = { likely: '#137A47', possible: '#9A5B00', unlikely: '#C62828', unknown: 'var(--text-secondary)' } as const;

const ASSUMPTION_FIELDS: { key: keyof WorkloadAssumptions; label: string; step: number }[] = [
  { key: 'reviewers', label: 'Reviewers screening each record', step: 1 },
  { key: 'minutes_per_abstract', label: 'Minutes per title and abstract', step: 0.1 },
  { key: 'full_text_fraction', label: 'Share of records reaching full text (0–1)', step: 0.01 },
  { key: 'minutes_per_full_text', label: 'Minutes per full text', step: 1 },
  { key: 'include_fraction', label: 'Share of full texts included (0–1)', step: 0.05 },
  { key: 'hours_per_included_study', label: 'Extraction hours per included study', step: 0.5 },
];

function YearChart({ counts }: { counts: Record<string, number> }) {
  const years = Object.keys(counts);
  if (years.length === 0) return null;
  const max = Math.max(1, ...Object.values(counts));
  return (
    <div role="img" aria-label={`OpenAlex works per year from ${years[0]} to ${years[years.length - 1]}`} style={{ display: 'flex', alignItems: 'flex-end', gap: '3px', height: '120px', marginTop: '8px' }}>
      {years.map(year => (
        <div key={year} title={`${year}: ${counts[year]}`} style={{ flex: 1, background: 'var(--accent-primary)', opacity: 0.8, height: `${Math.max(2, (counts[year] / max) * 100)}%`, borderRadius: '2px 2px 0 0' }} />
      ))}
    </div>
  );
}

function WorkloadTable({ low, high }: { low: WorkloadFigures; high: WorkloadFigures }) {
  const rows: [string, keyof WorkloadFigures][] = [
    ['Records to screen', 'records'], ['Full texts', 'full_texts'], ['Included studies', 'included_studies'],
    ['Screening hours', 'screening_hours'], ['Full-text hours', 'full_text_hours'], ['Extraction hours', 'extraction_hours'], ['Total hours', 'total_hours'],
  ];
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.9rem' }}>
      <thead><tr><th style={{ textAlign: 'left' }}>Estimate</th><th style={{ textAlign: 'right' }}>Low</th><th style={{ textAlign: 'right' }}>High</th></tr></thead>
      <tbody>
        {rows.map(([label, key]) => (
          <tr key={key}><td>{label}</td><td style={{ textAlign: 'right' }}>{low[key].toLocaleString()}</td><td style={{ textAlign: 'right' }}>{high[key].toLocaleString()}</td></tr>
        ))}
      </tbody>
    </table>
  );
}

export function TopicScreen() {
  const { projectId, currentProject, aiModelName, goTo, setFramework, refreshWorkflow } = useWorkspace();
  const { apiRequest } = useAuth();
  const [query, setQuery] = useState(currentProject?.title ?? '');
  const [assumptions, setAssumptions] = useState<WorkloadAssumptions>(DEFAULT_ASSUMPTIONS);
  const [exploration, setExploration] = useState<TopicExploration | null>(null);
  const [busy, setBusy] = useState<'exploring' | 'suggesting' | 'applying' | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const path = `/api/projects/${projectId}`;

  useEffect(() => {
    let cancelled = false;
    const base = `/api/projects/${projectId}`;
    apiRequest('GET', `${base}/topic-explorations`)
      .then(async (list: ExplorationSummary[]) => {
        if (cancelled || list.length === 0) return;
        const latest: TopicExploration = await apiRequest('GET', `${base}/topic-explorations/${list[0].id}`);
        if (cancelled) return;
        setExploration(latest);
        setQuery(latest.query);
        setAssumptions(latest.assumptions);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Could not load earlier topic explorations.'));
      });
    return () => {
      cancelled = true;
    };
  }, [apiRequest, projectId]);

  const explore = async () => {
    setBusy('exploring');
    setNotice(null);
    try {
      setExploration(await apiRequest('POST', `${path}/topic-explorations`, { query, ...assumptions }));
    } catch (err) {
      setNotice(errorMessage(err, 'The topic exploration failed.'));
    }
    setBusy(null);
  };

  const suggest = async () => {
    if (!exploration) return;
    setBusy('suggesting');
    setNotice(null);
    try {
      const suggestion: ProtocolSuggestion<TopicQuestions> = await apiRequest('POST', `${path}/topic-explorations/${exploration.id}/ai-questions`);
      setExploration({ ...exploration, ai_questions: suggestion });
    } catch (err) {
      setNotice(errorMessage(err, 'Could not suggest questions.'));
    }
    setBusy(null);
  };

  const applyQuestion = async (item: TopicQuestion) => {
    setBusy('applying');
    setNotice(null);
    try {
      const current: ReviewQuestion = await apiRequest('GET', `${path}/question`);
      const saved: ReviewQuestion = await apiRequest('PUT', `${path}/question`, {
        ...current,
        framework: item.framework,
        question: item.question,
        elements: current.framework === item.framework ? current.elements : {},
      });
      setFramework(saved.framework);
      await refreshWorkflow();
      goTo('question');
    } catch (err) {
      setNotice(errorMessage(err, 'Could not use this question.'));
      setBusy(null);
    }
  };

  const results = exploration?.results;
  const reviewTitles = new Map(results?.existing_reviews.map(review => [review.ref, `${review.title} (${review.year})`]) ?? []);

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px', maxWidth: '1000px', margin: '0 auto' }}>
      <h3 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>Topic & Feasibility</h3>
      <p style={{ color: 'var(--text-secondary)' }}>
        Check what is already published and registered before committing to a question: publication trends, existing reviews,
        registered protocols, and a rough estimate of the work involved.
      </p>

      <div style={{ ...panel, display: 'flex', flexDirection: 'column', gap: '12px' }}>
        <label htmlFor="topic-query" style={{ color: 'var(--text-secondary)' }}>Search terms</label>
        <input id="topic-query" className="search-input" value={query} onChange={e => setQuery(e.target.value)} placeholder="For example: aspirin primary prevention cardiovascular" />
        <details>
          <summary style={{ cursor: 'pointer', color: 'var(--text-secondary)' }}>Workload assumptions</summary>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '12px', marginTop: '12px' }}>
            {ASSUMPTION_FIELDS.map(field => (
              <label key={field.key} style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                {field.label}
                <input type="number" min={0} step={field.step} className="search-input" value={assumptions[field.key]} onChange={e => setAssumptions({ ...assumptions, [field.key]: Number(e.target.value) })} />
              </label>
            ))}
          </div>
        </details>
        <button className="btn-primary" onClick={explore} disabled={busy !== null || query.trim().length < 2} style={{ alignSelf: 'flex-start', padding: '10px 20px' }}>
          {busy === 'exploring' ? 'Searching PubMed, OpenAlex, ClinicalTrials.gov, and OSF…' : 'Explore topic'}
        </button>
      </div>

      {notice && <p role="status" style={{ color: 'var(--text-secondary)' }}>{notice}</p>}

      {exploration && results && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', marginTop: '20px' }}>
          <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
            Searched for “{exploration.query}” on {new Date(exploration.created_at).toLocaleString()}{exploration.created_by ? ` by ${exploration.created_by}` : ''}.
          </div>

          <div style={panel}>
            <h4 style={{ marginTop: 0 }}>How much is published</h4>
            <ul style={{ margin: 0, paddingLeft: '20px' }}>
              {Object.entries(results.sources).map(([key, source]) => (
                <li key={key}>{source.label}: {source.error ? <span style={{ color: '#C62828' }}>{source.error}</span> : source.count?.toLocaleString()}</li>
              ))}
            </ul>
            <YearChart counts={results.publications_by_year} />
          </div>

          <div style={panel}>
            <h4 style={{ marginTop: 0 }}>Existing reviews</h4>
            {results.review_errors.map(error => <p key={error} style={{ color: '#C62828' }}>{error}</p>)}
            {results.existing_reviews.length === 0 ? (
              <p>No reviews were found in these searches. That doesn't prove none exist; search PROSPERO and the Cochrane Library too.</p>
            ) : (
              <ul aria-label="Existing reviews" style={{ margin: 0, paddingLeft: '20px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
                {results.existing_reviews.map(review => (
                  <li key={review.ref}>
                    <a href={review.url} target="_blank" rel="noreferrer" style={{ color: 'var(--accent-primary)' }}>{review.title}</a>
                    <span style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}> · {review.year} · {review.venue || review.source}</span>
                    {review.possibly_outdated && (
                      <span style={{ marginLeft: '8px', color: '#9A5B00', fontSize: '0.8rem' }}>
                        Possibly outdated: {review.newer_randomized_trials} newer randomized trial{review.newer_randomized_trials === 1 ? '' : 's'} in PubMed
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div style={panel}>
            <h4 style={{ marginTop: 0 }}>Registered protocols</h4>
            {results.registration_error && <p style={{ color: '#C62828' }}>{results.registration_error}</p>}
            {results.registrations.length === 0 ? <p>No OSF registrations with these words in the title.</p> : (
              <ul style={{ margin: 0, paddingLeft: '20px' }}>
                {results.registrations.map(registration => (
                  <li key={registration.id}><a href={registration.url} target="_blank" rel="noreferrer" style={{ color: 'var(--accent-primary)' }}>{registration.title}</a> <span style={{ color: 'var(--text-secondary)' }}>· registered {registration.registered}</span></li>
                ))}
              </ul>
            )}
            <p style={{ marginBottom: 0, fontSize: '0.9rem' }}>
              PROSPERO can't be searched automatically. <a href={results.prospero_search_url} target="_blank" rel="noreferrer" style={{ color: 'var(--accent-primary)' }}>Search PROSPERO</a> before registering.
            </p>
          </div>

          <div style={panel}>
            <h4 style={{ marginTop: 0 }}>Feasibility</h4>
            <p>
              Meta-analysis: <strong style={{ color: FEASIBILITY_COLORS[results.meta_analysis_feasibility.level] }}>{results.meta_analysis_feasibility.level}</strong>. {results.meta_analysis_feasibility.explanation}
            </p>
            {results.workload ? (
              <>
                <WorkloadTable low={results.workload.low} high={results.workload.high} />
                <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: 0 }}>{results.workload.explanation}</p>
              </>
            ) : <p>No record counts were available to estimate the workload.</p>}
          </div>

          <div style={panel}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap', alignItems: 'center' }}>
              <h4 style={{ margin: 0 }}>Questions that address the gaps</h4>
              <button className="btn-glass" onClick={suggest} disabled={busy !== null} style={{ padding: '8px 16px' }}>
                {busy === 'suggesting' ? 'Asking the AI…' : `Suggest questions with ${aiModelName}`}
              </button>
            </div>
            {exploration.ai_questions && (
              <>
                <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                  Suggested by {exploration.ai_questions.provider} {exploration.ai_questions.model} from the evidence above. Check the cited reviews yourself.
                </p>
                <ol style={{ paddingLeft: '20px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
                  {exploration.ai_questions.content.questions.map(item => (
                    <li key={item.question}>
                      <strong>{item.question}</strong> <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>({item.framework} · {GAP_LABELS[item.gap] ?? item.gap})</span>
                      <div style={{ fontSize: '0.9rem' }}>{item.rationale}</div>
                      {item.based_on_review_ids.length > 0 && (
                        <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Based on: {item.based_on_review_ids.map(ref => reviewTitles.get(ref) ?? ref).join('; ')}</div>
                      )}
                      <button className="btn-glass" onClick={() => applyQuestion(item)} disabled={busy !== null} style={{ padding: '4px 10px', marginTop: '6px', fontSize: '0.8rem' }}>Use as review question</button>
                    </li>
                  ))}
                </ol>
                {exploration.ai_questions.content.evidence_limitations && (
                  <p style={{ fontSize: '0.85rem', color: '#9A5B00' }}>Limitations: {exploration.ai_questions.content.evidence_limitations}</p>
                )}
              </>
            )}
          </div>

          <ul style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', paddingLeft: '20px' }}>
            {results.notes.map(note => <li key={note}>{note}</li>)}
          </ul>
        </div>
      )}
    </section>
  );
}
