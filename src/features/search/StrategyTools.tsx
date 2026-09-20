import { useEffect, useState, type ReactNode } from 'react';
import { errorMessage } from '../../api/client';
import {
  PRESS_RATING_LABELS,
  type MeshHeading, type PressRating, type PressReview, type RecallCheck, type SearchQualityCatalog, type StrategyVersion, type Translation, type ValidationResult,
} from '../../api/searchQuality';
import { useAuth } from '../../auth/authContext';

const small = { padding: '4px 10px', fontSize: '0.8rem' } as const;
const labelStyle = { display: 'block', fontSize: '0.8rem', color: 'var(--text-secondary)' } as const;

export function Tool({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details style={{ marginTop: '8px' }}>
      <summary style={{ cursor: 'pointer', color: 'var(--text-secondary)', fontSize: '0.9rem' }}>{title}</summary>
      <div style={{ padding: '8px 0 0 12px' }}>{children}</div>
    </details>
  );
}

function Messages({ errors = [], warnings = [] }: { errors?: string[]; warnings?: string[] }) {
  return (
    <ul style={{ margin: '6px 0', paddingLeft: '18px', fontSize: '0.85rem' }}>
      {errors.map(error => <li key={error} style={{ color: '#C62828' }}>{error}</li>)}
      {warnings.map(warning => <li key={warning} style={{ color: '#9A5B00' }}>{warning}</li>)}
    </ul>
  );
}

export function SyntaxCheck({ projectId, query, syntax }: { projectId: number; query: string; syntax: string | null }) {
  const { apiRequest } = useAuth();
  const [result, setResult] = useState<ValidationResult | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  if (!syntax) return <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>No syntax checker for this database.</p>;
  const check = async () => {
    setNotice(null);
    try {
      setResult(await apiRequest('POST', `/api/projects/${projectId}/search-query/validate`, { query, syntax }));
    } catch (err) {
      setNotice(errorMessage(err, 'Could not check the syntax.'));
    }
  };
  return (
    <div>
      <button className="btn-glass" style={small} onClick={check}>Check syntax</button>
      {notice && <p role="status">{notice}</p>}
      {result && (result.valid && result.warnings.length === 0
        ? <p style={{ color: '#137A47', fontSize: '0.85rem' }}>No syntax problems found{result.term_count ? ` (${result.term_count} terms)` : ''}.</p>
        : <Messages errors={result.errors} warnings={result.warnings} />)}
    </div>
  );
}

export function TranslatePanel({ projectId, strategyId, catalog, locked, onAdded }: {
  projectId: number; strategyId: string; catalog: SearchQualityCatalog; locked: boolean; onAdded: () => Promise<void>;
}) {
  const { apiRequest } = useAuth();
  const targets = catalog.syntaxes.filter(syntax => syntax.key !== 'pubmed');
  const [target, setTarget] = useState(targets[0]?.key ?? '');
  const [translation, setTranslation] = useState<Translation | null>(null);
  const [database, setDatabase] = useState('');
  const [note, setNote] = useState('');
  const [notice, setNotice] = useState<string | null>(null);

  const translate = async () => {
    setNotice(null);
    try {
      const result: Translation = await apiRequest('POST', `/api/projects/${projectId}/search-strategies/${strategyId}/translate`, { target });
      setTranslation(result);
      setDatabase(result.target_label);
    } catch (err) {
      setNotice(errorMessage(err, 'Could not translate the strategy.'));
    }
  };

  const add = async () => {
    if (!translation) return;
    try {
      await apiRequest('POST', `/api/projects/${projectId}/search-strategies`, { database, query: translation.query, note: note || null });
      setTranslation(null);
      setNote('');
      await onAdded();
      setNotice(`Added a strategy for ${database}.`);
    } catch (err) {
      setNotice(errorMessage(err, 'Could not add the strategy.'));
    }
  };

  return (
    <div>
      <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
        <select aria-label="Translate for" className="search-input" value={target} onChange={e => setTarget(e.target.value)} style={{ width: 'auto' }}>
          {targets.map(syntax => <option key={syntax.key} value={syntax.key}>{syntax.label} ({syntax.databases})</option>)}
        </select>
        <button className="btn-glass" style={small} onClick={translate}>Translate</button>
      </div>
      {notice && <p role="status" style={{ fontSize: '0.85rem' }}>{notice}</p>}
      {translation && (
        <div style={{ marginTop: '8px' }}>
          <pre style={{ whiteSpace: 'pre-wrap', background: 'var(--surface-muted)', padding: '8px', borderRadius: '6px', fontSize: '0.85rem' }}>{translation.query}</pre>
          <Messages warnings={translation.warnings} />
          <label style={labelStyle}>Database for the new strategy
            <input className="search-input" value={database} onChange={e => setDatabase(e.target.value)} />
          </label>
          {locked && (
            <label style={labelStyle}>Why this strategy is added after the protocol was locked
              <input className="search-input" value={note} onChange={e => setNote(e.target.value)} />
            </label>
          )}
          <button className="btn-glass" style={{ ...small, marginTop: '6px' }} onClick={add} disabled={!database.trim() || (locked && note.trim().length < 10)}>Add as a new strategy</button>
        </div>
      )}
    </div>
  );
}

export function MeshLookup({ onInsert }: { onInsert: (snippet: string) => void }) {
  const { apiRequest } = useAuth();
  const [term, setTerm] = useState('');
  const [headings, setHeadings] = useState<MeshHeading[] | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const search = async () => {
    setNotice(null);
    try {
      setHeadings(await apiRequest('GET', `/api/vocabulary/mesh?term=${encodeURIComponent(term)}`));
    } catch (err) {
      setNotice(errorMessage(err, 'The MeSH lookup failed.'));
    }
  };

  return (
    <div>
      <div style={{ display: 'flex', gap: '8px' }}>
        <input aria-label="MeSH term" className="search-input" value={term} onChange={e => setTerm(e.target.value)} placeholder="For example aspirin" />
        <button className="btn-glass" style={small} onClick={search} disabled={term.trim().length < 2}>Look up</button>
      </div>
      {notice && <p role="status">{notice}</p>}
      {headings?.length === 0 && <p style={{ fontSize: '0.85rem' }}>No MeSH headings match.</p>}
      {headings?.map(item => (
        <div key={item.ui} style={{ borderLeft: '3px solid var(--accent-primary)', padding: '4px 10px', margin: '8px 0' }}>
          <strong>{item.heading}</strong> <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>{item.ui} · {item.tree_numbers.join(', ')}</span>
          {item.scope_note && <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>{item.scope_note}</div>}
          {item.entry_terms.length > 0 && <div style={{ fontSize: '0.8rem' }}>Entry terms: {item.entry_terms.join('; ')}</div>}
          <div style={{ display: 'flex', gap: '6px', marginTop: '4px', flexWrap: 'wrap' }}>
            <button className="btn-glass" style={small} onClick={() => onInsert(`"${item.heading}"[mh]`)}>Insert heading</button>
            <button className="btn-glass" style={small} onClick={() => onInsert(`(${[item.heading, ...item.entry_terms].map(t => `"${t}"[tiab]`).join(' OR ')})`)}>Insert as text words</button>
          </div>
        </div>
      ))}
    </div>
  );
}

export function RecallCheckPanel({ projectId, strategyId }: { projectId: number; strategyId: string }) {
  const { apiRequest } = useAuth();
  const [seeds, setSeeds] = useState('');
  const [checks, setChecks] = useState<RecallCheck[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    apiRequest('GET', `/api/projects/${projectId}/search-strategies/${strategyId}/recall-checks`)
      .then(data => {
        if (!cancelled) setChecks(data);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [apiRequest, projectId, strategyId]);

  const run = async () => {
    setBusy(true);
    setNotice(null);
    try {
      const check: RecallCheck = await apiRequest('POST', `/api/projects/${projectId}/search-strategies/${strategyId}/recall-checks`, {
        seeds: seeds.split(/[\s,;]+/).filter(Boolean),
      });
      setChecks(prev => [check, ...prev]);
    } catch (err) {
      setNotice(errorMessage(err, 'The recall check failed.'));
    }
    setBusy(false);
  };

  return (
    <div>
      <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginTop: 0 }}>Enter PMIDs or DOIs of articles the search must find. Missed articles suggest missing terms.</p>
      <textarea aria-label="Known relevant articles" className="search-input" style={{ width: '100%', minHeight: '60px' }} value={seeds} onChange={e => setSeeds(e.target.value)} />
      <button className="btn-glass" style={small} onClick={run} disabled={busy || !seeds.trim()}>{busy ? 'Checking…' : 'Run recall check'}</button>
      {notice && <p role="status">{notice}</p>}
      {checks.map(check => (
        <div key={check.id} style={{ fontSize: '0.85rem', marginTop: '8px' }}>
          Version {check.strategy_version} on {check.connector}: found {check.found.length} of {check.seeds.length}
          {check.recall !== null && ` (${Math.round(check.recall * 100)}%)`}
          {check.missed.length > 0 && <div style={{ color: '#9A5B00' }}>Missed: {check.missed.join(', ')}</div>}
        </div>
      ))}
    </div>
  );
}

export function PressReviewPanel({ projectId, strategyId, catalog, onSubmitted }: {
  projectId: number; strategyId: string; catalog: SearchQualityCatalog; onSubmitted: () => Promise<void>;
}) {
  const { apiRequest } = useAuth();
  const elements = catalog.press.elements;
  const [reviews, setReviews] = useState<PressReview[]>([]);
  const [answers, setAnswers] = useState<Record<string, { rating: PressRating; comment: string }>>(
    Object.fromEntries(elements.map(element => [element.key, { rating: 'no_revision' as PressRating, comment: '' }])),
  );
  const [comment, setComment] = useState('');
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiRequest('GET', `/api/projects/${projectId}/search-strategies/${strategyId}/press-reviews`)
      .then(data => {
        if (!cancelled) setReviews(data);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [apiRequest, projectId, strategyId]);

  const submit = async () => {
    setNotice(null);
    try {
      const review: PressReview = await apiRequest('POST', `/api/projects/${projectId}/search-strategies/${strategyId}/press-reviews`, { answers, comment });
      setReviews(prev => [review, ...prev]);
      await onSubmitted();
      setNotice(review.overall === 'approved' ? 'Review saved: approved.' : 'Review saved: revisions required.');
    } catch (err) {
      setNotice(errorMessage(err, 'Could not save the review.'));
    }
  };

  return (
    <div>
      {reviews.map(review => (
        <div key={review.id} style={{ fontSize: '0.85rem', marginBottom: '6px' }}>
          Version {review.strategy_version} reviewed by {review.reviewer ?? 'a former member'}: {review.overall === 'approved' ? 'approved' : 'revisions required'}
          {review.comment && ` — ${review.comment}`}
        </div>
      ))}
      <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>PRESS 2015 peer review. The strategy's author can't review their own version.</p>
      {elements.map(element => (
        <div key={element.key} style={{ marginBottom: '8px' }}>
          <strong style={{ fontSize: '0.9rem' }}>{element.label}</strong>
          <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>{element.guidance}</div>
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginTop: '4px' }}>
            <select aria-label={`${element.label} rating`} className="search-input" style={{ width: 'auto' }} value={answers[element.key].rating} onChange={e => setAnswers({ ...answers, [element.key]: { ...answers[element.key], rating: e.target.value as PressRating } })}>
              {catalog.press.ratings.map(rating => <option key={rating} value={rating}>{PRESS_RATING_LABELS[rating]}</option>)}
            </select>
            <input aria-label={`${element.label} comment`} className="search-input" style={{ flex: '1 1 220px' }} value={answers[element.key].comment} onChange={e => setAnswers({ ...answers, [element.key]: { ...answers[element.key], comment: e.target.value } })} placeholder="Suggested changes" />
          </div>
        </div>
      ))}
      <input aria-label="Overall comment" className="search-input" value={comment} onChange={e => setComment(e.target.value)} placeholder="Overall comment" />
      <button className="btn-glass" style={{ ...small, marginTop: '6px' }} onClick={submit}>Submit PRESS review</button>
      {notice && <p role="status" style={{ fontSize: '0.85rem' }}>{notice}</p>}
    </div>
  );
}

export function VersionHistory({ projectId, strategyId, version }: { projectId: number; strategyId: string; version: number }) {
  const { apiRequest } = useAuth();
  const [versions, setVersions] = useState<StrategyVersion[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiRequest('GET', `/api/projects/${projectId}/search-strategies/${strategyId}/versions`)
      .then(data => {
        if (!cancelled) setVersions(data);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [apiRequest, projectId, strategyId, version]);

  return (
    <ol reversed style={{ fontSize: '0.85rem', paddingLeft: '20px', margin: 0 }}>
      {versions?.map(item => (
        <li key={item.version} style={{ marginBottom: '6px' }}>
          Version {item.version}{item.created_by ? ` by ${item.created_by}` : ''} on {new Date(item.created_at).toLocaleDateString()}
          {item.note && <span style={{ color: 'var(--text-secondary)' }}> — {item.note}</span>}
          <pre style={{ whiteSpace: 'pre-wrap', margin: '2px 0', fontSize: '0.8rem' }}>{item.query}</pre>
        </li>
      ))}
    </ol>
  );
}
