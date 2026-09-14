import { WorkspaceStageGate } from '../project/WorkspaceStageGate';
import type { ProtocolItem } from '../project/types';
import { useWorkspace } from '../project/workspaceContext';

function CriteriaColumn({ title, color, kind, items }: { title: string; color: string; kind: 'inclusion' | 'exclusion'; items: ProtocolItem[] }) {
  const { handleAcceptAll, handleCriterionStatus } = useWorkspace();
  return (
    <div style={{ flex: 1 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
        <h4 style={{ color, margin: 0 }}>{title}</h4>
        <button className="btn-glass" onClick={() => handleAcceptAll(kind)} style={{ padding: '4px 8px', fontSize: '0.8rem' }}>Accept All ✓</button>
      </div>
      {items.map(item => (
        <div key={item.id} style={{ display: 'flex', padding: '8px', background: 'rgba(0,0,0,0.2)', marginBottom: '4px', alignItems: 'center', opacity: item.status === 'rejected' ? 0.5 : 1 }}>
          <div style={{ flex: 1, textDecoration: item.status === 'rejected' ? 'line-through' : 'none' }}>{item.text}</div>
          <div style={{ display: 'flex', gap: '4px' }}>
            <button onClick={() => handleCriterionStatus(item.id, 'accepted')} aria-label={`Accept: ${item.text}`} className="btn-primary" style={{ padding: '4px', background: item.status === 'accepted' ? '#10b981' : undefined }}>✓</button>
            <button onClick={() => handleCriterionStatus(item.id, 'rejected')} aria-label={`Reject: ${item.text}`} className="btn-glass" style={{ padding: '4px', color: '#ef4444', borderColor: item.status === 'rejected' ? '#ef4444' : undefined }}>✕</button>
          </div>
        </div>
      ))}
    </div>
  );
}

export function ProtocolScreen() {
  const { inclusionItems, exclusionItems, goTo } = useWorkspace();
  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
      <h3 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>2. AI Protocol Builder</h3>
      <WorkspaceStageGate stage="protocol" />
      <div style={{ display: 'flex', gap: '24px', marginBottom: '24px' }}>
        <CriteriaColumn title="Inclusion Criteria" color="#34d399" kind="inclusion" items={inclusionItems} />
        <CriteriaColumn title="Exclusion Criteria" color="#f87171" kind="exclusion" items={exclusionItems} />
      </div>
      <button className="btn-primary" onClick={() => goTo('search')}>Proceed to Literature Search →</button>
    </section>
  );
}
