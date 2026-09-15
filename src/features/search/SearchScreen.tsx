import { useCallback, useEffect, useState } from 'react';
import { errorMessage } from '../../api/client';
import { matchConnector, matchImportOnly, type SearchRunInfo, type SearchSources } from '../../api/search';
import { PRESS_STATUS_LABELS, syntaxForDatabase, type PressStatus, type PressStrategyStatus, type SearchQualityCatalog } from '../../api/searchQuality';
import { useAuth } from '../../auth/authContext';
import { WorkspaceStageGate } from '../project/WorkspaceStageGate';
import type { SearchItem } from '../project/types';
import { useWorkspace } from '../project/workspaceContext';
import { MeshLookup, PressReviewPanel, RecallCheckPanel, SyntaxCheck, Tool, TranslatePanel, VersionHistory } from './StrategyTools';

const panel = { background: 'rgba(0,0,0,0.2)', padding: '16px', borderRadius: '8px' } as const;
const labelStyle = { display: 'block', fontSize: '0.85rem', color: 'var(--text-secondary)' } as const;
const today = () => new Date().toISOString().slice(0, 10);

type UploadForm = { database: string; source_type: 'database' | 'register' | 'other'; interface: string; searched_on: string; query: string };

function StrategyCard({ item, sources, quality, press, onSearched, onQualityChanged }: {
  item: SearchItem; sources: SearchSources | null; quality: SearchQualityCatalog | null; press: PressStrategyStatus | undefined;
  onSearched: (message: string) => void; onQualityChanged: () => Promise<void>;
}) {
  const { projectId, setSearchItems, saveSearchString, reloadStrategies, stageInfo } = useWorkspace();
  const locked = stageInfo('protocol')?.status === 'completed';
  const [savedString, setSavedString] = useState(item.string);
  const [note, setNote] = useState('');
  const changed = item.string !== savedString;
  const syntax = syntaxForDatabase(quality, item.database);

  const saveChange = async () => {
    if (await saveSearchString(item.id, item.string, note || undefined)) {
      setSavedString(item.string);
      setNote('');
      await onQualityChanged();
    }
  };

  const insert = (snippet: string) =>
    setSearchItems(prev => prev.map(s => (s.id === item.id ? { ...s, string: s.string.trim() ? `${s.string.trim()} OR ${snippet}` : snippet } : s)));
  const { apiRequest } = useAuth();
  const matched = matchConnector(sources, item.database);
  const importOnly = matchImportOnly(sources, item.database);
  const [connector, setConnector] = useState(matched?.key ?? '');
  const [limit, setLimit] = useState(200);
  const [busy, setBusy] = useState(false);
  const chosen = sources?.connectors.find(c => c.key === connector);

  const run = async () => {
    setBusy(true);
    try {
      if (changed) {
        onSearched('Save the change to the search string before running it.');
        setBusy(false);
        return;
      }
      const result: SearchRunInfo = await apiRequest('POST', `/api/projects/${projectId}/searches`, {
        strategy_id: Number(item.id),
        connector: connector || null,
        limit,
      });
      const total = result.total_available !== null ? ` of ${result.total_available.toLocaleString()}` : '';
      onSearched(`Retrieved ${result.result_count.toLocaleString()}${total} records from ${result.source}.`);
    } catch (err) {
      onSearched(errorMessage(err, `The ${item.database} search failed.`));
    }
    setBusy(false);
  };

  return (
    <div style={{ ...panel, marginBottom: '12px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '8px', flexWrap: 'wrap' }}>
        <strong style={{ fontSize: '1.1rem' }}>{item.database}</strong>
        <span style={{ fontSize: '0.8rem', color: press?.status === 'approved' ? '#10b981' : '#f59e0b' }}>
          Version {item.version}{press ? ` · ${PRESS_STATUS_LABELS[press.status]}` : ''}
        </span>
      </div>
      <textarea className="search-input" aria-label={`${item.database} search string`} style={{ width: '100%', height: '70px', resize: 'vertical', marginTop: '8px' }} value={item.string} onChange={e => setSearchItems(prev => prev.map(s => (s.id === item.id ? { ...s, string: e.target.value } : s)))} />
      {changed && (
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center', margin: '4px 0' }}>
          {locked && <input aria-label={`Why the ${item.database} strategy changed`} className="search-input" style={{ flex: '1 1 260px' }} value={note} onChange={e => setNote(e.target.value)} placeholder="The protocol is locked: explain this change" />}
          <button className="btn-glass" onClick={saveChange} disabled={locked && note.trim().length < 10} style={{ padding: '6px 12px' }}>Save change</button>
          <button className="btn-glass" onClick={() => setSearchItems(prev => prev.map(s => (s.id === item.id ? { ...s, string: savedString } : s)))} style={{ padding: '6px 12px' }}>Undo</button>
        </div>
      )}
      {!matched && importOnly && (
        <p style={{ fontSize: '0.85rem', color: '#f59e0b', margin: '6px 0' }}>
          {importOnly.label} can't be searched from OmniReview. Run this string on {importOnly.interface}, export the results as {importOnly.export_hint}, and import the file below.
        </p>
      )}
      <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'flex-end', marginTop: '8px' }}>
        <label style={labelStyle}>Run on
          <select className="search-input" value={connector} onChange={e => setConnector(e.target.value)}>
            <option value="">{matched ? matched.label : 'Choose a connector'}</option>
            {sources?.connectors.filter(c => c.key !== matched?.key).map(c => <option key={c.key} value={c.key}>{c.label}</option>)}
          </select>
        </label>
        <label style={labelStyle}>Maximum records
          <input type="number" className="search-input" min={1} max={sources?.max_results ?? 2000} value={limit} onChange={e => setLimit(Number(e.target.value))} style={{ width: '120px' }} />
        </label>
        <button className="btn-primary" onClick={run} disabled={busy || !(connector || matched)} style={{ padding: '8px 16px' }}>
          {busy ? 'Searching…' : 'Run search'}
        </button>
      </div>
      {(chosen ?? matched) && <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', margin: '6px 0 0' }}>{(chosen ?? matched)!.syntax_note}</p>}
      {chosen && matched?.key !== chosen.key && <p style={{ fontSize: '0.8rem', color: '#f59e0b', margin: '4px 0 0' }}>This string was written for {item.database}; the run is labelled as {chosen.label} so PRISMA reports stay accurate.</p>}
      <Tool title="Check syntax"><SyntaxCheck projectId={projectId} query={item.string} syntax={syntax} /></Tool>
      <Tool title="Look up MeSH headings"><MeshLookup onInsert={insert} /></Tool>
      {quality && syntax === 'pubmed' && (
        <Tool title="Translate for another database">
          <TranslatePanel projectId={projectId} strategyId={item.id} catalog={quality} locked={locked} onAdded={async () => { await reloadStrategies(); await onQualityChanged(); }} />
        </Tool>
      )}
      {syntax && ['pubmed', 'europepmc', 'openalex'].includes(syntax) && <Tool title="Recall check with known articles"><RecallCheckPanel projectId={projectId} strategyId={item.id} /></Tool>}
      {quality && <Tool title="PRESS peer review"><PressReviewPanel projectId={projectId} strategyId={item.id} catalog={quality} onSubmitted={onQualityChanged} /></Tool>}
      <Tool title="Version history"><VersionHistory projectId={projectId} strategyId={item.id} version={item.version} /></Tool>
    </div>
  );
}

export function SearchScreen() {
  const { projectId, searchItems, literatureResults, handleResetSearch, goTo, refreshRecords, refreshWorkflow } = useWorkspace();
  const { apiRequest } = useAuth();
  const [sources, setSources] = useState<SearchSources | null>(null);
  const [runs, setRuns] = useState<SearchRunInfo[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [upload, setUpload] = useState<UploadForm>({ database: '', source_type: 'database', interface: '', searched_on: today(), query: '' });
  const [importing, setImporting] = useState(false);
  const [quality, setQuality] = useState<SearchQualityCatalog | null>(null);
  const [press, setPress] = useState<PressStatus | null>(null);
  const [waiverReason, setWaiverReason] = useState('');

  const loadRuns = useCallback(() => apiRequest('GET', `/api/projects/${projectId}/search-runs`), [apiRequest, projectId]);

  useEffect(() => {
    let cancelled = false;
    const base = `/api/projects/${projectId}`;
    Promise.all([apiRequest('GET', `${base}/search-sources`), loadRuns(), apiRequest('GET', `${base}/search-quality`), apiRequest('GET', `${base}/press-status`)])
      .then(([sourceCatalog, runList, qualityCatalog, pressStatus]) => {
        if (cancelled) return;
        setSources(sourceCatalog);
        setRuns(runList);
        setQuality(qualityCatalog);
        setPress(pressStatus);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Could not load the search sources.'));
      });
    return () => {
      cancelled = true;
    };
  }, [apiRequest, projectId, loadRuns]);

  const afterChange = async (message: string) => {
    setNotice(message);
    try {
      setRuns(await loadRuns());
      await refreshRecords();
    } catch (err) {
      setNotice(errorMessage(err, message));
    }
  };

  const reloadPress = async () => {
    setPress(await apiRequest('GET', `/api/projects/${projectId}/press-status`));
    await refreshWorkflow();
  };

  const waivePress = async () => {
    try {
      await apiRequest('POST', `/api/projects/${projectId}/press-waiver`, { reason: waiverReason });
      setWaiverReason('');
      await reloadPress();
      setNotice('PRESS peer review waived.');
    } catch (err) {
      setNotice(errorMessage(err, 'Could not record the waiver.'));
    }
  };

  const importFile = async () => {
    if (!file) return;
    setImporting(true);
    const form = new FormData();
    form.append('file', file);
    Object.entries(upload).forEach(([key, value]) => form.append(key, value));
    try {
      const result: SearchRunInfo & { skipped: number } = await apiRequest('POST', `/api/projects/${projectId}/imports/file`, form);
      const skipped = result.skipped ? ` ${result.skipped} entries without a title were skipped.` : '';
      setFile(null);
      await afterChange(`Imported ${result.result_count} records from ${file.name} (${result.file_format?.toUpperCase()}).${skipped}`);
    } catch (err) {
      setNotice(errorMessage(err, 'Could not import the file.'));
    }
    setImporting(false);
  };

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
      <h3 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>Database Search & Import</h3>
      <WorkspaceStageGate stage="search" />
      {notice && <p role="status" style={{ color: 'var(--text-secondary)' }}>{notice}</p>}

      <div style={{ marginBottom: '32px' }}>
        <h4 style={{ color: 'var(--accent-primary)', marginBottom: '16px' }}>Search strategies</h4>
        {searchItems.length === 0 && <p style={{ color: 'var(--text-secondary)' }}>No search strategies yet. Add them in the protocol.</p>}
        {press && (
          <div style={{ marginBottom: '12px', fontSize: '0.9rem' }}>
            {press.waived
              ? <span style={{ color: '#f59e0b' }}>PRESS peer review waived: {press.waiver_reason}</span>
              : press.met
                ? <span style={{ color: '#10b981' }}>Every current strategy has an approved PRESS peer review.</span>
                : (
                  <details>
                    <summary style={{ cursor: 'pointer', color: '#f59e0b' }}>Search sign-off needs an approved PRESS review of every strategy, or a waiver</summary>
                    <textarea aria-label="Reason for waiving PRESS peer review" className="search-input" style={{ width: '100%', minHeight: '50px', marginTop: '6px' }} value={waiverReason} onChange={e => setWaiverReason(e.target.value)} placeholder="For example: no second information specialist is available" />
                    <button className="btn-glass" onClick={waivePress} disabled={waiverReason.trim().length < 20} style={{ padding: '6px 12px', marginTop: '6px' }}>Waive PRESS peer review</button>
                  </details>
                )}
          </div>
        )}
        {searchItems.map(item => (
          <StrategyCard key={item.id} item={item} sources={sources} quality={quality} press={press?.strategies.find(p => String(p.strategy_id) === item.id)} onSearched={afterChange} onQualityChanged={reloadPress} />
        ))}
      </div>

      <div style={{ background: 'rgba(16, 185, 129, 0.05)', border: '1px dashed rgba(16,185,129,0.4)', padding: '24px', borderRadius: '12px' }}>
        <h4 style={{ color: '#10b981', marginTop: 0 }}>Import an export file</h4>
        <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
          For databases searched on their own platform. Accepted formats: {sources?.import_formats ?? 'RIS, MEDLINE, BibTeX, EndNote XML, Web of Science, or CSV'}.
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '12px' }}>
          <label style={labelStyle}>File
            <input type="file" accept=".ris,.nbib,.txt,.bib,.xml,.ciw,.csv" onChange={e => setFile(e.target.files?.[0] ?? null)} />
          </label>
          <label style={labelStyle}>Database the file came from
            <input className="search-input" list="import-databases" value={upload.database} onChange={e => setUpload({ ...upload, database: e.target.value })} placeholder="For example Embase" />
            <datalist id="import-databases">
              {sources?.import_only.map(source => <option key={source.label} value={source.label} />)}
            </datalist>
          </label>
          <label style={labelStyle}>Source type
            <select className="search-input" value={upload.source_type} onChange={e => setUpload({ ...upload, source_type: e.target.value as UploadForm['source_type'] })}>
              <option value="database">Bibliographic database</option>
              <option value="register">Trial register</option>
              <option value="other">Other source (grey literature, websites)</option>
            </select>
          </label>
          <label style={labelStyle}>Interface or platform
            <input className="search-input" value={upload.interface} onChange={e => setUpload({ ...upload, interface: e.target.value })} placeholder="For example Ovid" />
          </label>
          <label style={labelStyle}>Date searched
            <input type="date" className="search-input" value={upload.searched_on} onChange={e => setUpload({ ...upload, searched_on: e.target.value })} />
          </label>
        </div>
        <label style={{ ...labelStyle, marginTop: '12px' }}>Search string as run
          <textarea className="search-input" style={{ width: '100%', minHeight: '60px' }} value={upload.query} onChange={e => setUpload({ ...upload, query: e.target.value })} />
        </label>
        <button className="btn-primary" onClick={importFile} disabled={!file || !upload.database.trim() || importing} style={{ marginTop: '12px', padding: '8px 16px' }}>
          {importing ? 'Importing…' : 'Import file'}
        </button>
      </div>

      {runs.length > 0 && (
        <div style={{ marginTop: '32px' }}>
          <h4 style={{ color: 'var(--accent-primary)' }}>Search log (PRISMA-S)</h4>
          <div style={{ overflowX: 'auto', background: 'rgba(0,0,0,0.3)', borderRadius: '12px' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '0.85rem' }}>
              <thead style={{ background: '#1e293b' }}>
                <tr>
                  {['Date', 'Source', 'Interface', 'Search string', 'Records', 'Format'].map(heading => <th key={heading} style={{ padding: '10px' }}>{heading}</th>)}
                </tr>
              </thead>
              <tbody>
                {runs.map(run => (
                  <tr key={run.id} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                    <td style={{ padding: '10px', whiteSpace: 'nowrap' }}>{run.searched_on ?? run.executed_at.slice(0, 10)}</td>
                    <td style={{ padding: '10px' }}>{run.source}</td>
                    <td style={{ padding: '10px' }}>{run.interface ?? '—'}</td>
                    <td style={{ padding: '10px', maxWidth: '320px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={run.query ?? ''}>{run.query ?? '—'}</td>
                    <td style={{ padding: '10px', whiteSpace: 'nowrap' }}>
                      {run.result_count.toLocaleString()}{run.total_available !== null && run.total_available > run.result_count ? ` of ${run.total_available.toLocaleString()}` : ''}
                    </td>
                    <td style={{ padding: '10px' }}>{run.file_format?.toUpperCase() ?? 'API'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {literatureResults.length > 0 && (
        <div style={{ marginTop: '32px' }}>
          <h4 style={{ color: 'var(--accent-primary)', marginBottom: '16px' }}>Records ({literatureResults.length})</h4>
          <div style={{ overflowX: 'auto', background: 'rgba(0,0,0,0.3)', borderRadius: '12px', maxHeight: '300px' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left' }}>
              <thead style={{ position: 'sticky', top: 0, background: '#1e293b' }}>
                <tr>
                  <th style={{ padding: '12px' }}>Source</th>
                  <th style={{ padding: '12px' }}>Title</th>
                  <th style={{ padding: '12px' }}>Authors</th>
                  <th style={{ padding: '12px' }}>Year</th>
                  <th style={{ padding: '12px' }}>DOI</th>
                </tr>
              </thead>
              <tbody>
                {literatureResults.map(p => (
                  <tr key={p.id} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                    <td style={{ padding: '12px' }}>{p.source}</td>
                    <td style={{ padding: '12px', maxWidth: '300px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.title}</td>
                    <td style={{ padding: '12px', maxWidth: '200px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.authors}</td>
                    <td style={{ padding: '12px' }}>{p.year}</td>
                    <td style={{ padding: '12px' }}>{p.doi}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '24px' }}>
        <button className="btn-glass" onClick={handleResetSearch} style={{ color: '#ef4444', borderColor: '#ef4444' }}>✕ Clear All Searches</button>
        <button className="btn-primary" onClick={() => goTo('deduplication')}>Proceed to Deduplication →</button>
      </div>
    </section>
  );
}
