import type { DiffOp } from '../api/publishing';

// Word-level tracked changes: insertions underlined in green, deletions struck through in red.
export function DiffView({ diff, label = 'Changes' }: { diff: DiffOp[]; label?: string }) {
  if (!diff.some(op => op.op !== 'equal')) return <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>No changes.</p>;
  return (
    <div aria-label={label} style={{ whiteSpace: 'pre-wrap', fontFamily: 'var(--body)', fontSize: '0.85rem', lineHeight: 1.6, background: 'var(--surface-muted)', borderRadius: '8px', padding: '12px', maxHeight: '360px', overflowY: 'auto' }}>
      {diff.map((op, index) =>
        op.op === 'equal' ? (
          <span key={index}>{op.text}</span>
        ) : op.op === 'insert' ? (
          <ins key={index} style={{ color: '#137A47', textDecoration: 'underline' }}>
            {op.text}
          </ins>
        ) : (
          <del key={index} style={{ color: '#C62828' }}>
            {op.text}
          </del>
        ),
      )}
    </div>
  );
}
