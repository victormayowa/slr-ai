import { useCallback, useEffect, useRef, useState } from 'react';
import { errorMessage } from '../../api/client';
import {
  ORIGIN_LABELS,
  downloadDocument,
  formatBytes,
  hasFullText,
  type DocumentDetail,
  type DocumentInfo,
  type FullTextOverview,
  type FullTextRow,
  type RetrievalInfo,
} from '../../api/documents';
import { jobProblem, jobProgress, waitForJob, type AiJob } from '../../api/jobs';
import { useAuth } from '../../auth/authContext';
import { useWorkspace } from '../project/workspaceContext';
import { PassageViewer } from './PassageViewer';

const panel = { background: 'rgba(0,0,0,0.25)', borderRadius: '12px', padding: '16px 20px' } as const;
const muted = { color: 'var(--text-secondary)', fontSize: '0.85rem' } as const;
const smallButton = { padding: '4px 10px', fontSize: '0.8rem' } as const;

const STATUS_TEXT: Record<DocumentInfo['parse_status'], string> = {
  pending: 'Not read yet',
  parsed: 'Read into passages',
  failed: 'Could not be read',
  unsupported: 'Stored, not read',
};

function retrievalSummary(retrieval: RetrievalInfo): string {
  const when = retrieval.created_at.slice(0, 10);
  const steps = retrieval.attempts.map(attempt => `${ORIGIN_LABELS[attempt.source] ?? attempt.source}: ${attempt.detail}`);
  return `Last search ${when}${retrieval.requested_by ? ` by ${retrieval.requested_by}` : ''}. ${steps.join('; ')}`;
}

export function FullTextScreen() {
  const { projectId, refreshRecords, goTo } = useWorkspace();
  const { apiRequest, token } = useAuth();
  const [overview, setOverview] = useState<FullTextOverview | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [onlyMissing, setOnlyMissing] = useState(false);
  const [openDocument, setOpenDocument] = useState<DocumentDetail | null>(null);
  const unmounted = useRef(false);
  const base = `/api/projects/${projectId}`;

  const load = useCallback((): Promise<FullTextOverview> => apiRequest('GET', `${base}/full-texts`), [apiRequest, base]);

  useEffect(() => {
    unmounted.current = false;
    let cancelled = false;
    load()
      .then(data => {
        if (!cancelled) setOverview(data);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Could not load full texts.'));
      });
    return () => {
      cancelled = true;
      unmounted.current = true;
    };
  }, [load]);

  // Runs an action, shows its message or error, then reloads full texts and the PRISMA counts.
  const run = async (key: string, action: () => Promise<string | void>, failure: string) => {
    setBusy(key);
    setNotice(null);
    try {
      const message = await action();
      if (unmounted.current) return;
      if (message) setNotice(message);
    } catch (err) {
      setNotice(errorMessage(err, failure));
    }
    try {
      setOverview(await load());
      await refreshRecords();
    } catch {
      // The action's own message is more useful than a failed reload.
    }
    setBusy(null);
    setProgress(null);
  };

  const retrieveMissing = () =>
    run('all', async () => {
      const job: AiJob = await apiRequest('POST', `${base}/full-texts/retrieve`, {});
      const finished = await waitForJob(job, id => apiRequest('GET', `${base}/jobs/${id}`), update => setProgress(jobProgress(update)), {
        stopped: () => unmounted.current,
      });
      return jobProblem(finished, 'Full-text retrieval') ?? 'Retrieval finished. Records still without a full text need a file uploaded.';
    }, 'Full-text retrieval could not be started.');

  const retrieveOne = (row: FullTextRow) =>
    run(`retrieve-${row.record.id}`, async () => {
      const result: { retrieval: RetrievalInfo } = await apiRequest('POST', `${base}/records/${row.record.id}/full-text/retrieve`);
      if (result.retrieval.status === 'found') return `Found an open-access full text of "${row.record.title}".`;
      if (result.retrieval.status === 'already_stored') return 'That full text is already stored.';
      return `No open-access full text of "${row.record.title}" was found. Upload a copy if you have access.`;
    }, 'The search for a full text failed.');

  const upload = (row: FullTextRow, file: File, role: DocumentInfo['role']) =>
    run(`upload-${row.record.id}`, async () => {
      if (overview && file.size > overview.max_document_bytes) {
        return `${file.name} is larger than the ${formatBytes(overview.max_document_bytes)} limit.`;
      }
      const form = new FormData();
      form.append('file', file);
      form.append('role', role);
      const stored: DocumentInfo = await apiRequest('POST', `${base}/records/${row.record.id}/documents`, form);
      if (stored.parse_status === 'parsed') return `Stored ${stored.file_name} and read ${stored.span_count ?? 0} passages.`;
      return `Stored ${stored.file_name}, but it couldn't be read: ${stored.parse_error ?? 'unknown reason'}`;
    }, 'The upload failed.');

  const view = (doc: DocumentInfo) =>
    run(`view-${doc.id}`, async () => {
      setOpenDocument(await apiRequest('GET', `${base}/documents/${doc.id}`));
    }, 'Could not open the passages.');

  const reparse = (doc: DocumentInfo) =>
    run(`parse-${doc.id}`, async () => {
      const detail: DocumentDetail = await apiRequest('POST', `${base}/documents/${doc.id}/parse`);
      return detail.parse_status === 'parsed' ? `Read ${detail.spans.length} passages.` : `Still couldn't read it: ${detail.parse_error ?? 'unknown reason'}`;
    }, 'Could not read the file again.');

  const remove = (doc: DocumentInfo) => {
    if (!window.confirm(`Delete ${doc.file_name} and its passages? This can't be undone.`)) return;
    run(`delete-${doc.id}`, async () => {
      await apiRequest('DELETE', `${base}/documents/${doc.id}`);
      if (openDocument?.id === doc.id) setOpenDocument(null);
      return `Deleted ${doc.file_name}.`;
    }, 'Could not delete the file.');
  };

  const download = (doc: DocumentInfo) =>
    downloadDocument(token, projectId, doc).catch(err => setNotice(errorMessage(err, 'Could not download the file.')));

  const counts = overview?.counts;
  const rows = (overview?.records ?? []).filter(row => !onlyMissing || (row.final_decision === 'include' && !hasFullText(row)));

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
      <h3 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>Full Texts</h3>
      <p style={{ color: 'var(--text-secondary)' }}>
        Full texts of records included at abstract screening. OmniReview looks for open-access copies (Europe PMC XML first,
        then PDFs found by Unpaywall); upload a copy for anything else. Files stay private to this project and are read into
        passages with their section and page. Scanned PDFs need OCR, which isn't available yet.
      </p>
      {counts && (
        <p>
          Reports sought for retrieval: <strong>{counts.sought}</strong> · with a full text: <strong>{counts.retrieved}</strong> · not retrieved: <strong>{counts.not_retrieved}</strong>
        </p>
      )}
      {overview && !overview.unpaywall_configured && (
        <p style={muted}>Unpaywall isn't configured on the server (UNPAYWALL_EMAIL), so only Europe PMC is searched.</p>
      )}
      {notice && <p role="status" style={{ color: 'var(--text-secondary)' }}>{notice}</p>}

      <div style={{ display: 'flex', gap: '16px', alignItems: 'center', flexWrap: 'wrap', margin: '12px 0 20px' }}>
        <button className="btn-primary" onClick={retrieveMissing} disabled={busy !== null || !counts?.not_retrieved} style={{ padding: '8px 16px' }}>
          {busy === 'all' ? `Finding full texts… ${progress ?? 0}%` : 'Find open-access full texts for included records'}
        </button>
        <label style={muted}>
          <input type="checkbox" checked={onlyMissing} onChange={e => setOnlyMissing(e.target.checked)} /> Only records without a full text
        </label>
      </div>

      {overview && overview.records.length === 0 && <p style={muted}>No records are included yet. Include records at abstract screening first.</p>}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
        {rows.map(row => (
          <article key={row.record.id} style={panel} aria-label={row.record.title}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap' }}>
              <div style={{ minWidth: 0, flex: '1 1 320px' }}>
                <strong>{row.record.title}</strong> {row.record.year && <span style={muted}>({row.record.year})</span>}
                <div style={muted}>
                  {row.record.doi ? `DOI ${row.record.doi}` : 'No DOI'}
                  {row.final_decision !== 'include' && ' · not included at screening'}
                  {!hasFullText(row) && row.final_decision === 'include' && ' · no full text yet'}
                </div>
              </div>
              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
                <button className="btn-glass" style={smallButton} disabled={busy !== null} onClick={() => retrieveOne(row)}>
                  {busy === `retrieve-${row.record.id}` ? 'Searching…' : 'Find open-access copy'}
                </button>
                {(['full_text', 'supplement'] as const).map(role => (
                  <label key={role} className="btn-glass" style={{ ...smallButton, cursor: busy ? 'not-allowed' : 'pointer' }}>
                    {role === 'full_text' ? 'Upload full text' : 'Upload supplement'}
                    <input
                      type="file"
                      hidden
                      disabled={busy !== null}
                      aria-label={`${role === 'full_text' ? 'Upload full text' : 'Upload supplement'} for ${row.record.title}`}
                      accept={role === 'full_text' ? '.pdf,.xml,.docx,.txt' : undefined}
                      onChange={e => {
                        const file = e.target.files?.[0];
                        e.target.value = '';
                        if (file) upload(row, file, role);
                      }}
                    />
                  </label>
                ))}
              </div>
            </div>

            {row.documents.length > 0 && (
              <ul style={{ listStyle: 'none', padding: 0, margin: '12px 0 0', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                {row.documents.map(doc => (
                  <li key={doc.id} style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap', borderTop: '1px solid rgba(255,255,255,0.08)', paddingTop: '8px' }}>
                    <div style={{ minWidth: 0, flex: '1 1 320px', fontSize: '0.9rem' }}>
                      {doc.role === 'supplement' ? 'Supplement' : 'Full text'}: {doc.file_name}
                      <div style={muted}>
                        {ORIGIN_LABELS[doc.origin] ?? doc.origin}{doc.license && ` · ${doc.license}`}{doc.version && ` · ${doc.version}`} · {formatBytes(doc.size_bytes)}
                        {doc.page_count ? ` · ${doc.page_count} pages` : ''} · {STATUS_TEXT[doc.parse_status]}
                        {doc.parse_status === 'parsed' && ` (${doc.span_count ?? 0} passages)`}
                        {doc.parse_error && `: ${doc.parse_error}`}
                      </div>
                    </div>
                    <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', alignItems: 'center' }}>
                      {doc.parse_status === 'parsed' && <button className="btn-glass" style={smallButton} disabled={busy !== null} onClick={() => view(doc)}>View passages</button>}
                      <button className="btn-glass" style={smallButton} onClick={() => download(doc)}>Download</button>
                      {doc.parse_status !== 'parsed' && <button className="btn-glass" style={smallButton} disabled={busy !== null} onClick={() => reparse(doc)}>Read again</button>}
                      <button className="btn-glass" style={smallButton} disabled={busy !== null} onClick={() => remove(doc)}>Delete</button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
            {row.latest_retrieval && !hasFullText(row) && <p style={{ ...muted, margin: '8px 0 0' }}>{retrievalSummary(row.latest_retrieval)}</p>}
          </article>
        ))}
      </div>

      {openDocument && <PassageViewer doc={openDocument} onClose={() => setOpenDocument(null)} showEntities />}

      <div style={{ textAlign: 'right', marginTop: '24px' }}>
        <button className="btn-primary" onClick={() => goTo('full-text-screening')}>Proceed to Full-Text Screening →</button>
      </div>
    </section>
  );
}
