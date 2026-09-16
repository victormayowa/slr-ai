import { useCallback, useEffect, useRef, useState } from 'react';
import { errorMessage } from '../../api/client';
import { jobProblem, jobProgress, waitForJob, type AiJob } from '../../api/jobs';
import { STATE_LABELS, type ExclusionReason, type QueueResponse, type ScreeningRecord, type ScreeningStage } from '../../api/screening';
import { useAuth } from '../../auth/authContext';
import { CommentThread } from '../../components/CommentThread';
import { ProgressBar } from '../../components/ProgressBar';
import { AMBER, BLUE, GREEN, RED, chip, fieldLabel, muted, panel, row, smallButton } from '../../components/ui';
import { useWorkspace } from '../project/workspaceContext';
import { AiSuggestion } from './AiSuggestion';

export type DocumentRef = { documentId: number; fileName: string };

type Props = {
  stage: ScreeningStage;
  reasons: ExclusionReason[];
  documents?: Record<number, DocumentRef>;
  onOpenPassages?: (documentId: number, spanIds: number[]) => void;
};

// The records still needing this reviewer's decision at a stage, most likely includes first once ranked.
export function ScreeningQueue({ stage, reasons, documents = {}, onOpenPassages }: Props) {
  const { projectId, refreshRecords } = useWorkspace();
  const { apiRequest } = useAuth();
  const base = `/api/projects/${projectId}`;
  const [queue, setQueue] = useState<QueueResponse | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<number, { reason: string; note: string }>>({});
  const unmounted = useRef(false);
  const fullText = stage === 'full_text';

  const load = useCallback((): Promise<QueueResponse> => apiRequest('GET', `${base}/screening/queue?stage=${stage}&limit=25`), [apiRequest, base, stage]);

  useEffect(() => {
    unmounted.current = false;
    let cancelled = false;
    load()
      .then(data => {
        if (!cancelled) setQueue(data);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Could not load the screening queue.'));
      });
    return () => {
      cancelled = true;
      unmounted.current = true;
    };
  }, [load]);

  const act = async (key: string, action: () => Promise<string | void>, failure: string) => {
    setBusy(key);
    setNotice(null);
    try {
      const message = await action();
      if (unmounted.current) return;
      if (message) setNotice(message);
      setQueue(await load());
      await refreshRecords();
    } catch (err) {
      setNotice(errorMessage(err, failure));
    }
    setBusy(null);
    setProgress(null);
  };

  const draft = (id: number) => drafts[id] ?? { reason: '', note: '' };
  const setDraft = (id: number, changes: Partial<{ reason: string; note: string }>) => setDrafts(prev => ({ ...prev, [id]: { ...draft(id), ...changes } }));

  const decide = (record: ScreeningRecord, decision: string) =>
    act(`decide-${record.id}`, async () => {
      const { reason, note } = draft(record.id);
      await apiRequest('PUT', `${base}/records/${record.id}/decision`, {
        decision,
        stage,
        reason_code: fullText && decision === 'exclude' ? reason || null : null,
        note: note.trim() || null,
      });
      return undefined;
    }, 'Could not save your decision.');

  const runAi = () =>
    act('ai', async () => {
      const ids = (queue?.records ?? []).filter(record => !fullText || documents[record.id]).map(record => record.id);
      if (ids.length === 0) return fullText ? 'None of these reports has a readable full text yet (see Full Texts).' : 'There is nothing to screen.';
      const job: AiJob = await apiRequest('POST', `${base}/${fullText ? 'full-text-screening/ai' : 'screening/ai'}`, { record_ids: ids });
      const finished = await waitForJob(job, id => apiRequest('GET', `${base}/jobs/${id}`), update => setProgress(jobProgress(update)), {
        stopped: () => unmounted.current,
      });
      return jobProblem(finished, 'AI screening') ?? `AI suggestions are ready for ${ids.length} record(s). They never decide: record your own decision.`;
    }, 'AI screening could not be started.');

  const rank = () =>
    act('rank', async () => {
      const model = await apiRequest('POST', `${base}/screening/rank`);
      return `Ranked ${model.ranked} records from ${model.includes} included and ${model.excludes} excluded.`;
    }, 'Could not prioritize the queue.');

  const model = queue?.model;

  return (
    <div>
      <div style={{ ...row, marginBottom: '12px' }}>
        <strong>{queue ? `${queue.remaining} record(s) need your decision` : 'Loading…'}</strong>
        <button className="btn-primary" style={{ padding: '8px 16px' }} disabled={busy !== null || !queue?.records.length} onClick={runAi}>
          {busy === 'ai' ? 'Getting AI suggestions…' : `Get AI suggestions for the next ${queue?.records.length ?? 0}`}
        </button>
        {!fullText && (
          <button className="btn-glass" style={{ padding: '8px 16px' }} disabled={busy !== null} onClick={rank}>
            {busy === 'rank' ? 'Prioritizing…' : model ? 'Retrain prioritization' : 'Prioritize the queue'}
          </button>
        )}
      </div>
      {model && (
        <p style={muted}>
          Prioritized by {model.algorithm} trained on {model.includes} included and {model.excludes} excluded records.
          {model.retrain_due ? ` ${model.decisions_since} decisions since: retrain to keep the order current.` : ''} Likely-relevant terms: {model.top_terms.slice(0, 8).join(', ')}.
        </p>
      )}
      {progress !== null && <ProgressBar progress={progress} label="AI suggestions" />}
      {notice && <p role="status" style={muted}>{notice}</p>}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
        {queue?.records.map(record => {
          const view = record.screening[stage];
          const suggestion = fullText ? record.ai_full_text_screening : record.ai_screening;
          const document = documents[record.id];
          const stateColor = view.state === 'conflict' ? RED : view.state === 'awaiting_second_reviewer' ? AMBER : BLUE;
          return (
            <article key={record.id} style={panel} aria-label={record.title}>
              <div style={{ ...row, justifyContent: 'space-between' }}>
                <strong style={{ flex: '1 1 320px' }}>{record.title}</strong>
                <div style={row}>
                  {record.priority && <span style={chip(GREEN)}>priority #{record.priority.rank}</span>}
                  {view.state !== 'unscreened' && <span style={chip(stateColor)}>{STATE_LABELS[view.state]}</span>}
                  {view.reviewers_required > 1 && <span style={muted}>{view.reviewers_decided}/{view.reviewers_required} reviewers</span>}
                </div>
              </div>
              <div style={muted}>{[record.authors, record.year, record.venue].filter(Boolean).join(' · ')}{record.doi && ` · DOI ${record.doi}`}</div>
              {!fullText && <p style={{ color: 'var(--text-secondary)', maxHeight: '140px', overflowY: 'auto' }}>{record.abstract || 'No abstract.'}</p>}
              {fullText && (
                <p style={muted}>
                  {document ? (
                    <button className="btn-glass" style={smallButton} onClick={() => onOpenPassages?.(document.documentId, [])}>Read full text: {document.fileName}</button>
                  ) : (
                    'No readable full text: add one in Full Texts, or record the report as not retrieved.'
                  )}
                </p>
              )}
              {suggestion && <AiSuggestion suggestion={suggestion} onShowPassage={document && onOpenPassages ? spans => onOpenPassages(document.documentId, spans) : undefined} />}
              {!suggestion && record.ai_screening_hidden && !fullText && <p style={muted}>The AI suggestion is hidden until you record your decision (blinded dual screening).</p>}

              <div style={{ ...row, marginTop: '12px' }}>
                <button className="btn-glass" style={{ ...smallButton, borderColor: GREEN }} disabled={busy !== null} onClick={() => decide(record, 'include')}>✓ Include</button>
                {fullText && (
                  <label style={{ ...fieldLabel, flexDirection: 'row', alignItems: 'center' }}>
                    Reason
                    <select aria-label={`Exclusion reason for ${record.title}`} className="search-input" style={{ width: '220px' }} value={draft(record.id).reason} onChange={e => setDraft(record.id, { reason: e.target.value })}>
                      <option value="">Choose a reason…</option>
                      {reasons.map(reason => <option key={reason.code} value={reason.code}>{reason.label}</option>)}
                    </select>
                  </label>
                )}
                <button className="btn-glass" style={{ ...smallButton, borderColor: RED }} disabled={busy !== null || (fullText && !draft(record.id).reason)} onClick={() => decide(record, 'exclude')}>✕ Exclude</button>
                {fullText && !document && <button className="btn-glass" style={smallButton} disabled={busy !== null} onClick={() => decide(record, 'not_retrieved')}>Not retrieved</button>}
                {!fullText && <button className="btn-glass" style={smallButton} disabled={busy !== null} onClick={() => decide(record, 'undecided')}>? Undecided</button>}
                <input aria-label={`Note for ${record.title}`} className="search-input" style={{ maxWidth: '260px' }} placeholder="Note (optional)" value={draft(record.id).note} onChange={e => setDraft(record.id, { note: e.target.value })} />
              </div>
              <CommentThread projectId={projectId} anchorKey={`record:${record.id}`} anchorLabel={record.title} />
            </article>
          );
        })}
      </div>
      {queue && queue.records.length === 0 && <p style={muted}>Nothing is waiting for your decision at this stage.</p>}
    </div>
  );
}
