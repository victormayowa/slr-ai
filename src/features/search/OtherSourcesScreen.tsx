import { useCallback, useEffect, useState } from 'react';
import { errorMessage } from '../../api/client';
import type { SearchRunInfo, SearchSources } from '../../api/search';
import { useAuth } from '../../auth/authContext';
import { WorkspaceStageGate } from '../project/WorkspaceStageGate';
import { useWorkspace } from '../project/workspaceContext';

const panel = { background: 'rgba(0,0,0,0.25)', borderRadius: '12px', padding: '20px' } as const;
const labelStyle = { display: 'block', fontSize: '0.85rem', color: 'var(--text-secondary)' } as const;
const today = () => new Date().toISOString().slice(0, 10);

type CitationResult = { run: SearchRunInfo; new_records: number; already_in_project: number; repeated_in_results: number; unresolved_seed_record_ids: number[] };

type GreyEntry = { source_type: string; source_name: string; url: string; accessed_on: string; search_terms: string; title: string; authors: string; year: string; abstract: string };

const EMPTY_ENTRY: GreyEntry = { source_type: 'thesis', source_name: '', url: '', accessed_on: today(), search_terms: '', title: '', authors: '', year: '', abstract: '' };

export function OtherSourcesScreen() {
  const { projectId, literatureResults, refreshRecords, goTo } = useWorkspace();
  const { apiRequest } = useAuth();
  const [sources, setSources] = useState<SearchSources | null>(null);
  const [runs, setRuns] = useState<SearchRunInfo[]>([]);
  const [seedMode, setSeedMode] = useState<'included' | 'chosen'>('included');
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState('');
  const [direction, setDirection] = useState<'backward' | 'forward' | 'both'>('both');
  const [limit, setLimit] = useState(200);
  const [result, setResult] = useState<CitationResult | null>(null);
  const [entry, setEntry] = useState<GreyEntry>(EMPTY_ENTRY);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const included = literatureResults.filter(paper => paper.user_decision === 'Include');

  const loadRuns = useCallback(() => apiRequest('GET', `/api/projects/${projectId}/search-runs`), [apiRequest, projectId]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([apiRequest('GET', `/api/projects/${projectId}/search-sources`), loadRuns()])
      .then(([catalog, runList]) => {
        if (cancelled) return;
        setSources(catalog);
        setRuns(runList);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Could not load other sources.'));
      });
    return () => {
      cancelled = true;
    };
  }, [apiRequest, projectId, loadRuns]);

  const afterChange = async () => {
    setRuns(await loadRuns());
    await refreshRecords();
  };

  const chaseCitations = async () => {
    setBusy('citations');
    setNotice(null);
    try {
      const body = { direction, limit_per_seed: limit, record_ids: seedMode === 'chosen' ? [...chosen].map(Number) : null };
      const outcome: CitationResult = await apiRequest('POST', `/api/projects/${projectId}/citation-searches`, body);
      setResult(outcome);
      await afterChange();
    } catch (err) {
      setNotice(errorMessage(err, 'Citation searching failed.'));
    }
    setBusy(null);
  };

  const addGreyLiterature = async () => {
    setBusy('grey');
    setNotice(null);
    try {
      const outcome = await apiRequest('POST', `/api/projects/${projectId}/grey-literature`, entry);
      setNotice(outcome.record.duplicate_of_id ? 'Added, and marked as a duplicate of a record already in the project.' : 'Added. The record needs screening.');
      setEntry({ ...EMPTY_ENTRY, source_type: entry.source_type, source_name: entry.source_name, accessed_on: entry.accessed_on, search_terms: entry.search_terms });
      await afterChange();
    } catch (err) {
      setNotice(errorMessage(err, 'Could not add the record.'));
    }
    setBusy(null);
  };

  const otherRuns = runs.filter(run => run.kind === 'citation' || run.kind === 'other');
  const visible = literatureResults.filter(paper => paper.title.toLowerCase().includes(filter.toLowerCase())).slice(0, 100);

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
      <h3 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>Other Sources</h3>
      <WorkspaceStageGate stage="search" />
      <p style={{ color: 'var(--text-secondary)' }}>
        Citation searching and grey literature, reported under "other methods" in PRISMA 2020. They can also be added while
        screening is open; new records are checked against the project and need screening.
      </p>
      {notice && <p role="status" style={{ color: 'var(--text-secondary)' }}>{notice}</p>}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
        <div style={panel}>
          <h4 style={{ marginTop: 0 }}>Citation searching</h4>
          <p style={{ fontSize: '0.9rem', color: 'var(--text-secondary)' }}>
            Uses OpenAlex to find the references of chosen records (backward) and the works citing them (forward). Records OpenAlex can't identify from their DOI or PMID are listed.
          </p>
          <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap', alignItems: 'flex-end' }}>
            <label style={labelStyle}>Search from
              <select className="search-input" value={seedMode} onChange={e => setSeedMode(e.target.value as 'included' | 'chosen')}>
                <option value="included">Records a reviewer included ({included.length})</option>
                <option value="chosen">Records I choose ({chosen.size})</option>
              </select>
            </label>
            <label style={labelStyle}>Direction
              <select className="search-input" value={direction} onChange={e => setDirection(e.target.value as typeof direction)}>
                <option value="both">References and citing works</option>
                <option value="backward">References (backward)</option>
                <option value="forward">Citing works (forward)</option>
              </select>
            </label>
            <label style={labelStyle}>Citing works per record
              <input type="number" className="search-input" min={1} max={1000} value={limit} onChange={e => setLimit(Number(e.target.value))} style={{ width: '110px' }} />
            </label>
            <button className="btn-primary" onClick={chaseCitations} disabled={busy !== null || (seedMode === 'chosen' ? chosen.size === 0 : included.length === 0)} style={{ padding: '8px 16px' }}>
              {busy === 'citations' ? 'Searching citations…' : 'Run citation search'}
            </button>
          </div>
          {seedMode === 'chosen' && (
            <div style={{ marginTop: '12px' }}>
              <input aria-label="Filter records" className="search-input" value={filter} onChange={e => setFilter(e.target.value)} placeholder="Filter records by title" />
              <ul style={{ listStyle: 'none', padding: 0, maxHeight: '220px', overflowY: 'auto', margin: '8px 0' }}>
                {visible.map(paper => (
                  <li key={paper.id}>
                    <label style={{ display: 'flex', gap: '8px', fontSize: '0.85rem' }}>
                      <input type="checkbox" checked={chosen.has(paper.id)} onChange={e => setChosen(prev => {
                        const next = new Set(prev);
                        if (e.target.checked) next.add(paper.id);
                        else next.delete(paper.id);
                        return next;
                      })} />
                      {paper.title} {paper.year && `(${paper.year})`}
                    </label>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {result && (
            <p role="status" style={{ fontSize: '0.9rem' }}>
              Found {result.run.result_count} works: {result.new_records} new records to screen (studies the database searches missed), {result.already_in_project} already in the project.
              {result.unresolved_seed_record_ids.length > 0 && ` OpenAlex couldn't identify ${result.unresolved_seed_record_ids.length} of the chosen records.`}
            </p>
          )}
        </div>

        <div style={panel}>
          <h4 style={{ marginTop: 0 }}>Add grey literature</h4>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '12px' }}>
            <label style={labelStyle}>Type
              <select className="search-input" value={entry.source_type} onChange={e => setEntry({ ...entry, source_type: e.target.value })}>
                {sources?.grey_literature_types?.map(type => <option key={type.key} value={type.key}>{type.label}</option>)}
              </select>
            </label>
            <label style={labelStyle}>Source searched
              <input className="search-input" value={entry.source_name} onChange={e => setEntry({ ...entry, source_name: e.target.value })} placeholder="For example WHO IRIS" />
            </label>
            <label style={labelStyle}>Date accessed
              <input type="date" className="search-input" value={entry.accessed_on} onChange={e => setEntry({ ...entry, accessed_on: e.target.value })} />
            </label>
            <label style={labelStyle}>Link
              <input type="url" className="search-input" value={entry.url} onChange={e => setEntry({ ...entry, url: e.target.value })} placeholder="https://" />
            </label>
            <label style={labelStyle}>Title
              <input className="search-input" value={entry.title} onChange={e => setEntry({ ...entry, title: e.target.value })} />
            </label>
            <label style={labelStyle}>Authors or organization
              <input className="search-input" value={entry.authors} onChange={e => setEntry({ ...entry, authors: e.target.value })} />
            </label>
            <label style={labelStyle}>Year
              <input className="search-input" value={entry.year} onChange={e => setEntry({ ...entry, year: e.target.value })} />
            </label>
            <label style={labelStyle}>Search terms used at the source
              <input className="search-input" value={entry.search_terms} onChange={e => setEntry({ ...entry, search_terms: e.target.value })} />
            </label>
          </div>
          <label style={{ ...labelStyle, marginTop: '12px' }}>Summary
            <textarea className="search-input" style={{ width: '100%', minHeight: '60px' }} value={entry.abstract} onChange={e => setEntry({ ...entry, abstract: e.target.value })} />
          </label>
          <button className="btn-primary" onClick={addGreyLiterature} disabled={busy !== null || !entry.title.trim() || !entry.source_name.trim() || !/^https?:\/\//.test(entry.url)} style={{ marginTop: '12px', padding: '8px 16px' }}>
            {busy === 'grey' ? 'Adding…' : 'Add record'}
          </button>
        </div>

        {otherRuns.length > 0 && (
          <div style={panel}>
            <h4 style={{ marginTop: 0 }}>Other methods log</h4>
            <ul style={{ margin: 0, paddingLeft: '20px', fontSize: '0.9rem' }}>
              {otherRuns.map(run => (
                <li key={run.id}>{run.searched_on ?? run.executed_at.slice(0, 10)}: {run.source} · {run.result_count} records{run.query ? ` · ${run.query}` : ''}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <div style={{ textAlign: 'right', marginTop: '24px' }}>
        <button className="btn-primary" onClick={() => goTo('deduplication')}>Proceed to Deduplication →</button>
      </div>
    </section>
  );
}
