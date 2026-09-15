import { useEffect, useRef } from 'react';
import type { DocumentDetail } from '../../api/documents';
import { muted, panel, smallButton } from '../../components/ui';
import { EntitiesPanel } from './EntitiesPanel';

type Props = { doc: DocumentDetail; highlight?: number[]; onClose: () => void; showEntities?: boolean };

// A parsed document's passages, with evidence passages highlighted and scrolled into view.
export function PassageViewer({ doc, highlight = [], onClose, showEntities = false }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const marked = new Set(highlight);
  const first = highlight[0];

  useEffect(() => {
    if (first == null) return;
    const element = container.current?.querySelector(`[data-span="${first}"]`);
    if (element && typeof element.scrollIntoView === 'function') element.scrollIntoView({ block: 'center' });
  }, [first, doc.id]);

  const background = (id: number) => (marked.has(id) ? 'rgba(250, 204, 21, 0.22)' : undefined);

  return (
    <div style={{ ...panel, marginTop: '20px' }} aria-label={`Passages of ${doc.file_name}`} role="region">
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', alignItems: 'center' }}>
        <h4 style={{ margin: 0 }}>{doc.file_name}</h4>
        <button className="btn-glass" style={smallButton} onClick={onClose}>Close</button>
      </div>
      <p style={muted}>
        {doc.spans.length} passages · read with {doc.parser || 'no parser'}{doc.page_count ? ` · ${doc.page_count} pages` : ''}
        {highlight.length > 0 && ' · evidence highlighted'}
      </p>
      <div ref={container} style={{ maxHeight: '480px', overflowY: 'auto', paddingRight: '8px' }}>
        {doc.spans.map(span => {
          const where = span.page ? `p. ${span.page}` : '';
          const common = { 'data-span': span.id, style: { background: background(span.id), borderRadius: '6px' } };
          if (span.kind === 'title') return <h4 key={span.id} {...common}>{span.text}</h4>;
          if (span.kind === 'heading') return <h5 key={span.id} {...common} style={{ ...common.style, margin: '16px 0 4px' }}>{span.text} <span style={muted}>{where}</span></h5>;
          if (span.kind === 'table') {
            return (
              <div key={span.id} {...common} style={{ ...common.style, overflowX: 'auto' }}>
                <pre style={{ fontSize: '0.8rem', whiteSpace: 'pre', margin: '8px 0' }}>{span.label ? `${span.label}\n` : ''}{span.text}</pre>
              </div>
            );
          }
          return (
            <p key={span.id} {...common} style={{ ...common.style, margin: '6px 0', fontSize: span.kind === 'reference' ? '0.8rem' : '0.9rem', fontStyle: span.kind === 'caption' ? 'italic' : 'normal' }}>
              {span.label && <strong>{span.label}. </strong>}
              {span.text} {where && <span style={muted}>({where})</span>}
            </p>
          );
        })}
      </div>
      {showEntities && <EntitiesPanel documentId={doc.id} spans={doc.spans} />}
    </div>
  );
}
