import { useEffect, useState } from 'react';
import { errorMessage } from '../../api/client';
import type { FinerRating, ProtocolSuggestion, QuestionSuggestion, ReviewQuestion } from '../../api/protocol';
import { useAuth } from '../../auth/authContext';
import { WorkspaceStageGate } from '../project/WorkspaceStageGate';
import { useWorkspace } from '../project/workspaceContext';

const labelStyle = { display: 'block', marginBottom: '8px', color: 'var(--text-secondary)' } as const;
const RATING_LABELS: Record<FinerRating, string> = { yes: 'Yes', partly: 'Partly', no: 'No' };

export function QuestionScreen() {
  const { projectId, protocolCatalog: catalog, setFramework, refreshWorkflow, aiModelName } = useWorkspace();
  const { apiRequest } = useAuth();
  const [question, setQuestion] = useState<ReviewQuestion | null>(null);
  const [suggestion, setSuggestion] = useState<ProtocolSuggestion<QuestionSuggestion> | null>(null);
  const [busy, setBusy] = useState<'saving' | 'suggesting' | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const path = `/api/projects/${projectId}`;

  useEffect(() => {
    let cancelled = false;
    apiRequest('GET', `/api/projects/${projectId}/question`)
      .then(data => {
        if (!cancelled) setQuestion(data);
      })
      .catch(err => {
        if (!cancelled) setLoadError(errorMessage(err, 'Could not load the review question.'));
      });
    return () => {
      cancelled = true;
    };
  }, [apiRequest, projectId]);

  if (loadError) return <section className="glass-panel" role="alert" style={{ padding: '32px' }}>{loadError}</section>;
  if (!question || !catalog) return <p style={{ color: 'var(--text-secondary)' }}>Loading the review question…</p>;

  const framework = catalog.frameworks.find(item => item.key === question.framework) ?? catalog.frameworks[0];
  const update = (changes: Partial<ReviewQuestion>) => setQuestion(prev => (prev ? { ...prev, ...changes } : prev));

  const changeFramework = (key: string) => {
    const next = catalog.frameworks.find(item => item.key === key);
    if (!next) return;
    // Keep text for elements the frameworks share, such as population.
    update({ framework: key, elements: Object.fromEntries(next.elements.map(element => [element.key, question.elements[element.key] ?? ''])) });
  };

  const setRating = (key: string, rating: FinerRating | '') => {
    const finer = { ...question.finer };
    if (rating) finer[key] = { rating, note: finer[key]?.note ?? '' };
    else delete finer[key];
    update({ finer });
  };

  const save = async () => {
    setBusy('saving');
    setNotice(null);
    try {
      const saved: ReviewQuestion = await apiRequest('PUT', `${path}/question`, question);
      setQuestion(saved);
      setFramework(saved.framework);
      await refreshWorkflow();
      setNotice('Review question saved.');
    } catch (err) {
      setNotice(errorMessage(err, 'Could not save the review question.'));
    }
    setBusy(null);
  };

  const suggest = async () => {
    setBusy('suggesting');
    setNotice(null);
    try {
      setSuggestion(await apiRequest('POST', `${path}/question/ai`));
    } catch (err) {
      setNotice(errorMessage(err, 'Could not get a suggestion.'));
    }
    setBusy(null);
  };

  const applySuggestion = () => {
    if (!suggestion) return;
    const { framework: key, question: text, elements } = suggestion.content;
    update({ framework: key, question: text, elements });
    setNotice('Suggestion applied. Check each element, then save.');
  };

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px', maxWidth: '900px', margin: '0 auto' }}>
      <h3 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>Review Question</h3>
      <WorkspaceStageGate stage="protocol" />
      <p style={{ color: 'var(--text-secondary)' }}>
        Structure the question with a framework, then rate it against FINER. The eligibility criteria, analysis plan, and
        protocol document all build on it.
      </p>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
        <div>
          <label htmlFor="question-framework" style={labelStyle}>Question framework</label>
          <select id="question-framework" className="search-input" value={framework.key} onChange={e => changeFramework(e.target.value)}>
            {catalog.frameworks.map(item => (
              <option key={item.key} value={item.key}>{item.label}: {item.use_for}</option>
            ))}
          </select>
        </div>

        {framework.elements.map(element => (
          <div key={element.key}>
            <label htmlFor={`element-${element.key}`} style={labelStyle}>{element.label}</label>
            <input
              id={`element-${element.key}`}
              type="text"
              className="search-input"
              placeholder={element.hint}
              value={question.elements[element.key] ?? ''}
              onChange={e => update({ elements: { ...question.elements, [element.key]: e.target.value } })}
            />
          </div>
        ))}

        <div>
          <label htmlFor="review-question" style={labelStyle}>Review question (one sentence)</label>
          <textarea id="review-question" className="search-input" style={{ height: '72px', resize: 'vertical' }} value={question.question} onChange={e => update({ question: e.target.value })} />
        </div>

        <fieldset style={{ border: '1px solid rgba(255,255,255,0.1)', borderRadius: '12px', padding: '16px' }}>
          <legend style={{ color: 'var(--text-primary)', padding: '0 8px' }}>FINER assessment</legend>
          {catalog.finer_criteria.map(criterion => {
            const assessment = question.finer[criterion.key];
            const aiNote = suggestion?.content.finer_notes[criterion.key];
            return (
              <div key={criterion.key} style={{ display: 'grid', gridTemplateColumns: 'minmax(140px, 1fr) 130px 2fr', gap: '12px', alignItems: 'start', marginBottom: '12px' }}>
                <div>
                  <strong>{criterion.label}</strong>
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>{criterion.hint}</div>
                </div>
                <select aria-label={`${criterion.label} rating`} className="search-input" value={assessment?.rating ?? ''} onChange={e => setRating(criterion.key, e.target.value as FinerRating | '')}>
                  <option value="">Not rated</option>
                  {(catalog.finer_ratings as FinerRating[]).map(rating => <option key={rating} value={rating}>{RATING_LABELS[rating]}</option>)}
                </select>
                <div>
                  <input
                    aria-label={`${criterion.label} note`}
                    type="text"
                    className="search-input"
                    disabled={!assessment}
                    placeholder={assessment ? 'Why you rated it this way' : 'Rate it first'}
                    value={assessment?.note ?? ''}
                    onChange={e => update({ finer: { ...question.finer, [criterion.key]: { rating: assessment!.rating, note: e.target.value } } })}
                  />
                  {aiNote && <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '4px' }}>AI prompt to consider: {aiNote}</div>}
                </div>
              </div>
            );
          })}
        </fieldset>

        {suggestion && (
          <div role="region" aria-label="AI question suggestion" style={{ background: 'rgba(59,130,246,0.08)', border: '1px solid rgba(59,130,246,0.3)', borderRadius: '12px', padding: '16px' }}>
            <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Suggested by {suggestion.provider} {suggestion.model}. Nothing changes until you apply and save it.</div>
            <p style={{ margin: '8px 0' }}><strong>{suggestion.content.framework}:</strong> {suggestion.content.question}</p>
            <ul style={{ margin: '0 0 12px', paddingLeft: '20px' }}>
              {Object.entries(suggestion.content.elements).map(([key, text]) => (
                <li key={key}>{key.replace(/_/g, ' ')}: {text || <em>left for you to fill in</em>}</li>
              ))}
            </ul>
            <button className="btn-glass" onClick={applySuggestion} style={{ padding: '8px 16px' }}>Apply suggestion</button>
          </div>
        )}

        {notice && <p role="status" style={{ color: 'var(--text-secondary)', margin: 0 }}>{notice}</p>}
        <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
          <button className="btn-glass" onClick={suggest} disabled={busy !== null} style={{ padding: '12px 20px' }}>
            {busy === 'suggesting' ? 'Asking the AI…' : `Suggest with ${aiModelName}`}
          </button>
          <button className="btn-primary" onClick={save} disabled={busy !== null} style={{ padding: '12px 24px' }}>
            {busy === 'saving' ? 'Saving…' : 'Save review question'}
          </button>
        </div>
      </div>
    </section>
  );
}
