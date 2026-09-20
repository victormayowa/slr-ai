import { useState } from 'react';
import { errorMessage } from '../../api/client';
import type { StrategyInfo } from '../../api/review';
import { useAuth } from '../../auth/authContext';
import { useWorkspace } from '../project/workspaceContext';

// One source the AI recommends. Whether OmniReview can search it is decided by the server, not the model.
type SuggestedDatabase = {
  database: string;
  reason: string;
  searchable: boolean;
  interface: string | null;
  export_hint: string | null;
  note: string;
};

type Suggestion = { id: number; provider: string; model: string; content: { databases: SuggestedDatabase[] } };

const panel = { background: 'var(--surface-muted)', padding: '16px', borderRadius: '8px' } as const;

// Step one of the search: agree which sources to search, then draft a string for each.
export function DatabasePlanner({ onChanged }: { onChanged: (message: string) => void }) {
  const { projectId, searchItems, reloadStrategies, aiModelName } = useWorkspace();
  const { apiRequest } = useAuth();
  const [suggestion, setSuggestion] = useState<Suggestion | null>(null);
  const [chosen, setChosen] = useState<Record<string, boolean>>({});
  const [extra, setExtra] = useState('');
  const [busy, setBusy] = useState<'suggesting' | 'drafting' | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const already = new Set(searchItems.map(item => item.database.toLowerCase()));
  const picked = Object.entries(chosen).filter(([, on]) => on).map(([name]) => name);

  const suggest = async () => {
    setBusy('suggesting');
    setProblem(null);
    try {
      const result: Suggestion = await apiRequest('POST', `/api/projects/${projectId}/search-databases/ai`);
      setSuggestion(result);
      setChosen(Object.fromEntries(result.content.databases.map(item => [item.database, !already.has(item.database.toLowerCase())])));
    } catch (err) {
      setProblem(errorMessage(err, 'Could not suggest databases.'));
    }
    setBusy(null);
  };

  const draft = async () => {
    const databases = [...picked, ...extra.split(',').map(name => name.trim()).filter(Boolean)];
    if (!databases.length) return;
    setBusy('drafting');
    setProblem(null);
    try {
      const result: { strategies: StrategyInfo[]; missing: string[] } = await apiRequest('POST', `/api/projects/${projectId}/search-strategies/ai`, { databases });
      await reloadStrategies();
      setExtra('');
      setChosen({});
      const missing = result.missing.length ? ` No string was written for ${result.missing.join(', ')}; add it yourself.` : '';
      onChanged(`Added a search strategy for ${result.strategies.map(s => s.database).join(', ')}.${missing}`);
    } catch (err) {
      setProblem(errorMessage(err, 'Could not draft the search strings.'));
    }
    setBusy(null);
  };

  return (
    <div style={{ ...panel, marginBottom: '20px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap', alignItems: 'center' }}>
        <strong>Which databases should this review search?</strong>
        <button className="btn-glass" onClick={suggest} disabled={busy !== null} style={{ padding: '8px 16px' }}>
          {busy === 'suggesting' ? 'Asking the AI…' : `Suggest databases with ${aiModelName}`}
        </button>
      </div>
      <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', margin: '6px 0 0' }}>
        The AI proposes sources for your question. Choose the ones you agree with, add any of your own, and a search
        string is then drafted for each in that database's syntax.
      </p>
      {problem && <p role="alert" style={{ color: '#C62828', fontSize: '0.85rem' }}>{problem}</p>}

      {suggestion && (
        <div role="region" aria-label="Suggested databases" style={{ marginTop: '12px' }}>
          <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginBottom: '8px' }}>
            Suggested by {suggestion.provider} {suggestion.model}. Nothing is added until you choose.
          </div>
          {suggestion.content.databases.map(item => {
            const have = already.has(item.database.toLowerCase());
            return (
              <label key={item.database} style={{ display: 'flex', gap: '10px', alignItems: 'flex-start', padding: '8px 0', borderTop: '1px solid var(--border)', fontSize: '0.9rem' }}>
                <input type="checkbox" checked={!!chosen[item.database]} disabled={have} onChange={e => setChosen(prev => ({ ...prev, [item.database]: e.target.checked }))} style={{ marginTop: '4px' }} />
                <span>
                  <strong>{item.database}</strong>{' '}
                  <span style={{ fontSize: '0.78rem', color: item.searchable ? '#137A47' : '#9A5B00' }}>
                    {have ? 'already added' : item.searchable ? 'searched from OmniReview' : `run on ${item.interface ?? 'its own platform'}, then import`}
                  </span>
                  <span style={{ display: 'block', color: 'var(--text-secondary)' }}>{item.reason}</span>
                </span>
              </label>
            );
          })}
        </div>
      )}

      <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'flex-end', marginTop: '12px' }}>
        <label style={{ display: 'block', fontSize: '0.85rem', color: 'var(--text-secondary)', flex: '1 1 260px' }}>
          Databases of your own (comma separated)
          <input className="search-input" value={extra} onChange={e => setExtra(e.target.value)} placeholder="For example CINAHL, LILACS" />
        </label>
        <button className="btn-primary" onClick={draft} disabled={busy !== null || (!picked.length && !extra.trim())} style={{ padding: '8px 16px' }}>
          {busy === 'drafting' ? 'Writing search strings…' : 'Add these databases'}
        </button>
      </div>
    </div>
  );
}
