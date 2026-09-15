import { useState } from 'react';
import { WorkspaceStageGate } from '../project/WorkspaceStageGate';
import { useWorkspace } from '../project/workspaceContext';

const SUGGESTED_FIELDS = ['Study Design', 'Intervention Details', 'Country', 'Funding Source'];

export function ExtractionFieldsScreen() {
  const { extractionColumns, saveExtractionColumns, goTo } = useWorkspace();
  const [newColumn, setNewColumn] = useState('');

  const handleAddColumn = () => {
    const name = newColumn.trim();
    if (name && !extractionColumns.includes(name)) {
      saveExtractionColumns([...extractionColumns, name]);
      setNewColumn('');
    }
  };

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
      <h3 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>Define Full-Text Extraction Rules</h3>
      <WorkspaceStageGate stage="protocol" />
      <p style={{ color: 'var(--text-secondary)', marginBottom: '24px' }}>Remove any variables you do not need, and add standard or custom ones.</p>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '12px', marginBottom: '24px' }}>
        {extractionColumns.map(col => (
          <div key={col} style={{ display: 'flex', alignItems: 'center', gap: '8px', background: 'rgba(59, 130, 246, 0.2)', border: '1px solid rgba(59, 130, 246, 0.4)', padding: '6px 12px', borderRadius: '20px' }}>
            <span>{col}</span>
            <button onClick={() => saveExtractionColumns(extractionColumns.filter(c => c !== col))} aria-label={`Remove ${col}`} style={{ background: 'transparent', border: 'none', color: '#ef4444', cursor: 'pointer', fontWeight: 'bold' }}>✕</button>
          </div>
        ))}
        <div style={{ display: 'flex', gap: '8px' }}>
          <input type="text" className="search-input" style={{ borderRadius: '20px', padding: '6px 16px', width: '200px' }} placeholder="+ Custom Variable..." value={newColumn} onChange={e => setNewColumn(e.target.value)} onKeyDown={e => e.key === 'Enter' && handleAddColumn()} />
        </div>
      </div>

      <div style={{ marginBottom: '32px' }}>
        <h4 style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginBottom: '8px' }}>Common AI Suggestions:</h4>
        <div style={{ display: 'flex', gap: '8px' }}>
          {SUGGESTED_FIELDS.map(sugg => (
            <button key={sugg} onClick={() => { if (!extractionColumns.includes(sugg)) saveExtractionColumns([...extractionColumns, sugg]); }} className="btn-glass" style={{ padding: '4px 12px', fontSize: '0.8rem' }}>
              + {sugg}
            </button>
          ))}
        </div>
      </div>

      <button className="btn-primary" onClick={() => goTo('extraction')}>Lock Schema & Proceed to Full Text →</button>
    </section>
  );
}
