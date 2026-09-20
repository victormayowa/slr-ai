import { useState } from 'react';
import { criterionElements } from '../../api/protocol';
import { WorkspaceStageGate } from '../project/WorkspaceStageGate';
import type { ProtocolItem } from '../project/types';
import { useWorkspace } from '../project/workspaceContext';

function CriteriaColumn({ title, color, kind, items }: { title: string; color: string; kind: 'inclusion' | 'exclusion'; items: ProtocolItem[] }) {
  const { handleAcceptAll, handleCriterionStatus, handleCriterionElement, handleAddCriterion, protocolCatalog, framework } = useWorkspace();
  const elements = criterionElements(protocolCatalog, framework);
  const [text, setText] = useState('');
  const [element, setElement] = useState('');
  const noun = kind === 'inclusion' ? 'inclusion' : 'exclusion';

  const add = async () => {
    if (!text.trim()) return;
    if (await handleAddCriterion(kind, text.trim(), element || null)) {
      setText('');
      setElement('');
    }
  };

  return (
    <div style={{ flex: '1 1 380px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
        <h4 style={{ color, margin: 0 }}>{title}</h4>
        <button className="btn-glass" onClick={() => handleAcceptAll(kind)} style={{ padding: '4px 8px', fontSize: '0.8rem' }}>Accept All ✓</button>
      </div>
      {items.map(item => (
        <div key={item.id} style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', padding: '8px', background: 'var(--surface-muted)', marginBottom: '4px', alignItems: 'center', opacity: item.status === 'rejected' ? 0.5 : 1 }}>
          <div style={{ flex: '1 1 200px', textDecoration: item.status === 'rejected' ? 'line-through' : 'none' }}>
            {item.text}
            {item.source === 'reviewer' && <span style={{ fontSize: '0.7rem', color: 'var(--text-secondary)', marginLeft: '6px' }}>(added by a reviewer)</span>}
          </div>
          <select aria-label={`Question element for: ${item.text}`} className="search-input" style={{ width: '150px', padding: '4px' }} value={item.element ?? ''} onChange={e => handleCriterionElement(item.id, e.target.value)}>
            <option value="">Not linked</option>
            {elements.map(option => <option key={option.key} value={option.key}>{option.label}</option>)}
          </select>
          <div style={{ display: 'flex', gap: '4px' }}>
            <button onClick={() => handleCriterionStatus(item.id, 'accepted')} aria-label={`Accept: ${item.text}`} className="btn-primary" style={{ padding: '4px', background: item.status === 'accepted' ? '#137A47' : undefined }}>✓</button>
            <button onClick={() => handleCriterionStatus(item.id, 'rejected')} aria-label={`Reject: ${item.text}`} className="btn-glass" style={{ padding: '4px', color: '#C62828', borderColor: item.status === 'rejected' ? '#C62828' : undefined }}>✕</button>
          </div>
        </div>
      ))}
      <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginTop: '12px' }}>
        <input aria-label={`New ${noun} criterion`} className="search-input" style={{ flex: '1 1 200px' }} placeholder={`Add an ${noun} criterion`} value={text} onChange={e => setText(e.target.value)} />
        <select aria-label={`Question element for the new ${noun} criterion`} className="search-input" style={{ width: '150px' }} value={element} onChange={e => setElement(e.target.value)}>
          <option value="">Not linked</option>
          {elements.map(option => <option key={option.key} value={option.key}>{option.label}</option>)}
        </select>
        <button className="btn-glass" onClick={add} disabled={!text.trim()} style={{ padding: '6px 12px' }}>Add</button>
      </div>
    </div>
  );
}

export function ProtocolScreen() {
  const { inclusionItems, exclusionItems, goTo } = useWorkspace();
  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
      <h3 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>Eligibility Criteria</h3>
      <WorkspaceStageGate stage="protocol" />
      <p style={{ color: 'var(--text-secondary)' }}>
        Accept or reject suggested criteria, add your own, and link each one to the part of the review question it
        restricts so screening can be checked against the question.
      </p>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '24px', marginBottom: '24px' }}>
        <CriteriaColumn title="Inclusion Criteria" color="#137A47" kind="inclusion" items={inclusionItems} />
        <CriteriaColumn title="Exclusion Criteria" color="#C62828" kind="exclusion" items={exclusionItems} />
      </div>
      <button className="btn-primary" onClick={() => goTo('analysis-plan')}>Proceed to the Analysis Plan →</button>
    </section>
  );
}
