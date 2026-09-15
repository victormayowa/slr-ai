import { useRef } from 'react';
import { WorkspaceStageGate } from '../project/WorkspaceStageGate';
import { useWorkspace } from '../project/workspaceContext';

const downloadTemplate = () => {
  const headers = "Title,Authors,Year,DOI,Venue,Abstract\n";
  const blob = new Blob([headers], { type: 'text/csv' });
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'OmniReview_Upload_Template.csv';
  a.click();
  window.URL.revokeObjectURL(url);
};

export function SearchScreen() {
  const {
    searchItems, setSearchItems, searchDBLoading, handleRunDatabaseSearch, saveSearchString, uploadLoading, importCsvFile,
    literatureResults, handleResetSearch, goTo,
  } = useWorkspace();
  const fileInput = useRef<HTMLInputElement>(null);

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
      <h3 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>Database Search & Manual Import</h3>
      <WorkspaceStageGate stage="search" />

      <div style={{ marginBottom: '32px' }}>
        <h4 style={{ color: 'var(--accent-primary)', marginBottom: '16px' }}>AI-Generated Database Search Strings</h4>
        {searchItems.length === 0 && <p style={{ color: 'var(--text-secondary)' }}>No automated searches generated.</p>}

        {searchItems.map(item => (
          <div key={item.id} style={{ background: 'rgba(0,0,0,0.2)', padding: '16px', borderRadius: '8px', marginBottom: '12px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
              <strong style={{ fontSize: '1.1rem' }}>{item.database}</strong>
              <button className="btn-primary" onClick={() => handleRunDatabaseSearch(item)} disabled={searchDBLoading === item.database}>
                {searchDBLoading === item.database ? 'Querying API...' : `Search ${item.database}`}
              </button>
            </div>
            <textarea className="search-input" aria-label={`${item.database} search string`} style={{ width: '100%', height: '60px', resize: 'vertical' }} value={item.string} onChange={e => setSearchItems(prev => prev.map(s => s.id === item.id ? { ...s, string: e.target.value } : s))} onBlur={() => saveSearchString(item.id, item.string)} />
          </div>
        ))}
      </div>

      <div style={{ background: 'rgba(16, 185, 129, 0.05)', border: '1px dashed rgba(16,185,129,0.4)', padding: '24px', borderRadius: '12px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h4 style={{ color: '#10b981', margin: 0 }}>Manual CSV Upload</h4>
        </div>
        <div style={{ display: 'flex', gap: '12px' }}>
          <button className="btn-glass" onClick={downloadTemplate} style={{ fontSize: '0.9rem' }}>↓ Download Template</button>
          <input ref={fileInput} type="file" accept=".csv" style={{ display: 'none' }} onChange={e => { const file = e.target.files?.[0]; if (file) importCsvFile(file); e.target.value = ''; }} />
          <button className="btn-primary" onClick={() => fileInput.current?.click()} disabled={uploadLoading}>
            {uploadLoading ? 'Parsing CSV...' : '↑ Upload CSV'}
          </button>
        </div>
      </div>

      {literatureResults.length > 0 && (
        <div style={{ marginTop: '32px' }}>
          <h4 style={{ color: 'var(--accent-primary)', marginBottom: '16px' }}>Search Results Preview ({literatureResults.length} Papers)</h4>
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
        <button className="btn-glass" onClick={handleResetSearch} style={{ color: '#ef4444', borderColor: '#ef4444' }}>
          ✕ Clear All Searches
        </button>
        <button className="btn-primary" onClick={() => goTo('deduplication')}>
          Proceed to Deduplication →
        </button>
      </div>
    </section>
  );
}
