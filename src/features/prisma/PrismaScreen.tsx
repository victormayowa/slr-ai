import { useWorkspace } from '../project/workspaceContext';

export function PrismaScreen() {
  const { prisma, goTo } = useWorkspace();
  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
      <h3 style={{ marginBottom: '24px', color: 'var(--text-primary)' }}>PRISMA Flow Diagram</h3>
      <div style={{ padding: '32px', background: 'rgba(0,0,0,0.3)', borderRadius: '12px' }}>
        <p style={{ color: 'var(--text-secondary)', marginTop: 0 }}>Counts computed from the project's saved searches, imports, other methods, deduplication, and reviewer decisions. The PRISMA 2020 flow diagram itself is not generated yet.</p>
        <ul style={{ lineHeight: 1.8, margin: 0 }}>
          <li>Records from databases: {prisma?.identified_from_databases ?? 0}</li>
          <li>Records from trial registers: {prisma?.identified_from_registers ?? 0}</li>
          <li>Records from other methods: {prisma?.identified_from_other_methods ?? 0}
            {prisma?.other_methods && (
              <span style={{ color: 'var(--text-secondary)' }}> (citation searching {prisma.other_methods.citation_searching}; grey literature and websites {prisma.other_methods.grey_literature_and_websites})</span>
            )}
          </li>
          {(prisma?.identified_from_uploads ?? 0) > 0 && <li>Records from uploads with no source type: {prisma?.identified_from_uploads}</li>}
          {Object.entries(prisma?.by_source ?? {}).map(([source, count]) => (
            <li key={source} style={{ color: 'var(--text-secondary)', marginLeft: '16px' }}>{source}: {count}</li>
          ))}
          <li>Duplicates removed: {prisma?.duplicates_removed ?? 0}</li>
          <li>Records screened: {prisma?.screened ?? 0}</li>
          <li>Excluded by a reviewer at abstract screening: {prisma?.excluded ?? 0}</li>
          <li>Included by a reviewer: {prisma?.included ?? 0}</li>
          <li>Awaiting a reviewer decision: {prisma?.awaiting_decision ?? 0}</li>
          <li>Reports sought for retrieval: {prisma?.reports_sought_for_retrieval ?? 0}</li>
          <li>Reports not retrieved: {prisma?.reports_not_retrieved ?? 0}</li>
        </ul>
      </div>
      <div style={{ textAlign: 'center', marginTop: '32px' }}>
        <button className="btn-primary" onClick={() => goTo('risk-of-bias')}>Proceed to Quality Assessment →</button>
      </div>
    </section>
  );
}
