import { useCallback, useEffect, useState } from 'react';
import { errorMessage } from '../../api/client';
import type { DocumentSpan } from '../../api/documents';
import { useAuth } from '../../auth/authContext';
import { AMBER, GREEN, GREY, chip, muted, panel, row, smallButton } from '../../components/ui';
import { useWorkspace } from '../project/workspaceContext';

// Mirrors backend/entities_routes.py.
type Candidate = { ontology: string; code: string; label: string };
type Entity = {
  id: number;
  span_id: number | null;
  entity_type: string;
  text: string;
  ontology: string;
  code: string;
  label: string;
  candidates: Candidate[];
  status: 'suggested' | 'confirmed' | 'rejected';
  source: 'ai' | 'reviewer';
};

const STATUS_COLOR = { suggested: AMBER, confirmed: GREEN, rejected: GREY } as const;
const TERMINOLOGIES = [
  { key: 'mesh', label: 'MeSH' },
  { key: 'rxnorm', label: 'RxNorm' },
  { key: 'atc', label: 'ATC' },
  { key: 'icd11', label: 'ICD-11' },
];

// Conditions, interventions, drugs, outcomes, and tests in a document, linked to terminology codes a reviewer confirms.
export function EntitiesPanel({ documentId, spans }: { documentId: number; spans: DocumentSpan[] }) {
  const { projectId } = useWorkspace();
  const { apiRequest } = useAuth();
  const base = `/api/projects/${projectId}`;
  const [entities, setEntities] = useState<Entity[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [search, setSearch] = useState<{ entityId: number; ontology: string; q: string; results: Candidate[] } | null>(null);

  const load = useCallback((): Promise<Entity[]> => apiRequest('GET', `${base}/documents/${documentId}/entities`), [apiRequest, base, documentId]);

  useEffect(() => {
    let cancelled = false;
    load()
      .then(items => {
        if (!cancelled) setEntities(items);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Could not load entities.'));
      });
    return () => {
      cancelled = true;
    };
  }, [load]);

  const suggest = async () => {
    setBusy(true);
    setNotice(null);
    try {
      const result: { entities: Entity[]; dropped_unverified: number; lookup_errors: string[] } = await apiRequest('POST', `${base}/documents/${documentId}/entities/suggest`);
      setEntities(result.entities);
      const dropped = result.dropped_unverified ? ` ${result.dropped_unverified} mention(s) weren't found in the text and were dropped.` : '';
      setNotice(`Suggestions ready: confirm or reject each one.${dropped} ${result.lookup_errors.join(' ')}`.trim());
    } catch (err) {
      setNotice(errorMessage(err, 'Could not suggest entities.'));
    }
    setBusy(false);
  };

  const update = async (entity: Entity, changes: Partial<Pick<Entity, 'status' | 'ontology' | 'code' | 'label'>>) => {
    try {
      const saved: Entity = await apiRequest('PATCH', `${base}/entities/${entity.id}`, changes);
      setEntities(prev => prev.map(item => (item.id === saved.id ? saved : item)));
    } catch (err) {
      setNotice(errorMessage(err, 'Could not update the entity.'));
    }
  };

  const runSearch = async () => {
    if (!search) return;
    try {
      const results: Candidate[] = await apiRequest('GET', `${base}/terminology/search?ontology=${search.ontology}&q=${encodeURIComponent(search.q)}`);
      setSearch({ ...search, results });
    } catch (err) {
      setNotice(errorMessage(err, 'The terminology search failed.'));
    }
  };

  const sectionOf = (spanId: number | null) => spans.find(span => span.id === spanId)?.section || '';

  return (
    <div style={{ ...panel, marginTop: '16px' }} aria-label="Entities">
      <div style={{ ...row, justifyContent: 'space-between' }}>
        <h4 style={{ margin: 0 }}>Entities and terminology codes</h4>
        <button className="btn-glass" style={smallButton} disabled={busy} onClick={suggest}>{busy ? 'Finding entities…' : 'Suggest entities with AI'}</button>
      </div>
      <p style={muted}>AI suggests mentions and codes from MeSH, RxNorm, ATC, and ICD-11 (when configured). Only confirmed codes are used.</p>
      {notice && <p role="status" style={muted}>{notice}</p>}
      <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: '8px' }}>
        {entities.map(entity => (
          <li key={entity.id} style={{ borderTop: '1px solid rgba(255,255,255,0.08)', paddingTop: '8px' }}>
            <div style={row}>
              <strong>{entity.text}</strong>
              <span style={muted}>{entity.entity_type}{sectionOf(entity.span_id) && ` · ${sectionOf(entity.span_id)}`}</span>
              <span style={chip(STATUS_COLOR[entity.status])}>{entity.status}</span>
            </div>
            <div style={{ ...row, marginTop: '4px' }}>
              <select
                aria-label={`Code for ${entity.text}`}
                className="search-input"
                style={{ maxWidth: '360px' }}
                value={entity.code ? `${entity.ontology}|${entity.code}` : ''}
                onChange={e => {
                  const chosen = entity.candidates.find(c => `${c.ontology}|${c.code}` === e.target.value);
                  if (chosen) update(entity, { ontology: chosen.ontology, code: chosen.code, label: chosen.label });
                }}
              >
                <option value="">No code chosen</option>
                {entity.code && !entity.candidates.some(c => c.code === entity.code) && (
                  <option value={`${entity.ontology}|${entity.code}`}>{entity.ontology.toUpperCase()} {entity.code} · {entity.label}</option>
                )}
                {entity.candidates.map(c => (
                  <option key={`${c.ontology}|${c.code}`} value={`${c.ontology}|${c.code}`}>{c.ontology.toUpperCase()} {c.code} · {c.label}</option>
                ))}
              </select>
              <button className="btn-glass" style={smallButton} disabled={!entity.code} onClick={() => update(entity, { status: 'confirmed' })}>Confirm</button>
              <button className="btn-glass" style={smallButton} onClick={() => update(entity, { status: 'rejected' })}>Reject</button>
              <button className="btn-glass" style={smallButton} onClick={() => setSearch({ entityId: entity.id, ontology: 'mesh', q: entity.text, results: [] })}>Search codes</button>
            </div>
            {search?.entityId === entity.id && (
              <div style={{ ...row, marginTop: '6px' }}>
                <select aria-label="Terminology" className="search-input" style={{ width: '120px' }} value={search.ontology} onChange={e => setSearch({ ...search, ontology: e.target.value })}>
                  {TERMINOLOGIES.map(t => <option key={t.key} value={t.key}>{t.label}</option>)}
                </select>
                <input aria-label="Search term" className="search-input" style={{ maxWidth: '240px' }} value={search.q} onChange={e => setSearch({ ...search, q: e.target.value })} />
                <button className="btn-glass" style={smallButton} onClick={runSearch}>Search</button>
                {search.results.map(result => (
                  <button key={`${result.ontology}|${result.code}`} className="btn-glass" style={smallButton} onClick={() => { update(entity, result); setSearch(null); }}>
                    {result.code} · {result.label}
                  </button>
                ))}
              </div>
            )}
          </li>
        ))}
      </ul>
      {entities.length === 0 && <p style={muted}>No entities yet.</p>}
    </div>
  );
}
