import { useCallback, useEffect, useState } from 'react';
import { modelDisplayName } from '../../api/ai';
import { errorMessage } from '../../api/client';
import type { RecordBrief } from '../../api/review';
import type { DuplicateCandidate } from '../../api/search';
import { useAuth } from '../../auth/authContext';
import { ProgressBar } from '../../components/ProgressBar';
import { WorkspaceStageGate } from '../project/WorkspaceStageGate';
import { useWorkspace } from '../project/workspaceContext';

const smallButton = { padding: '6px 12px', fontSize: '0.8rem' } as const;

function RecordSummary({ record }: { record: RecordBrief }) {
  const details = [record.authors, record.year, record.source, record.doi].filter(Boolean).join(' · ');
  return (
    <div style={{ marginTop: '8px' }}>
      <strong>{record.title}</strong>
      <div style={{ color: 'var(--text-secondary)', fontSize: '0.8rem' }}>{details}</div>
    </div>
  );
}

function PairActions({ first, second, onDuplicate, onDistinct, busy }: {
  first: RecordBrief; second: RecordBrief; busy: boolean;
  onDuplicate: (keep: RecordBrief, duplicate: RecordBrief) => void; onDistinct?: () => void;
}) {
  return (
    <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginTop: '12px' }}>
      <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => onDuplicate(first, second)}>Keep the first, mark the second as a duplicate</button>
      <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => onDuplicate(second, first)}>Keep the second, mark the first as a duplicate</button>
      {onDistinct && <button className="btn-glass" style={smallButton} disabled={busy} onClick={onDistinct}>Not duplicates</button>}
    </div>
  );
}

// Similar-title pairs that automatic deduplication couldn't decide. Search sign-off waits until every pair is decided.
function CandidatePairs({ version }: { version: number }) {
  const { projectId, refreshRecords } = useWorkspace();
  const { apiRequest } = useAuth();
  const [pairs, setPairs] = useState<DuplicateCandidate[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(() => apiRequest('GET', `/api/projects/${projectId}/duplicate-candidates`), [apiRequest, projectId]);

  useEffect(() => {
    let cancelled = false;
    load()
      .then(data => {
        if (!cancelled) setPairs(data.pairs);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Could not load possible duplicates.'));
      });
    return () => {
      cancelled = true;
    };
  }, [load, version]);

  // Every pair at once. Merging keeps the earlier record of each pair, as automatic deduplication does.
  const decideAll = async (decision: 'duplicate' | 'not_duplicate') => {
    const count = pairs?.length ?? 0;
    const question = decision === 'duplicate'
      ? `Merge all ${count} pair(s)? The earlier record of each pair is kept and the other is set aside as a duplicate.`
      : `Mark all ${count} pair(s) as separate studies? None of them will be set aside.`;
    if (!window.confirm(question)) return;
    setBusy(true);
    setNotice(null);
    try {
      const result = await apiRequest('POST', `/api/projects/${projectId}/duplicate-candidates/decide-all`, { decision });
      setPairs((await load()).pairs);
      await refreshRecords();
      setNotice(decision === 'duplicate'
        ? `${result.merged} record(s) set aside as duplicates from ${result.pairs} pair(s).`
        : `${result.pairs} pair(s) marked as separate studies.`);
    } catch (err) {
      setNotice(errorMessage(err, 'Could not decide the pairs.'));
    }
    setBusy(false);
  };

  const decide = async (pair: DuplicateCandidate, decision: 'duplicate' | 'not_duplicate', keep?: RecordBrief) => {
    setBusy(true);
    setNotice(null);
    try {
      await apiRequest('POST', `/api/projects/${projectId}/duplicate-candidates/decision`, {
        record_id: pair.record.id,
        other_record_id: pair.other.id,
        decision,
        keep_record_id: keep?.id ?? null,
      });
      setPairs((await load()).pairs);
      await refreshRecords();
    } catch (err) {
      setNotice(errorMessage(err, 'Could not save the decision.'));
    }
    setBusy(false);
  };

  if (pairs === null) return notice ? <p role="status">{notice}</p> : null;
  return (
    <div style={{ marginTop: '32px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap', alignItems: 'center', margin: '0 0 8px' }}>
        <h4 style={{ margin: 0, color: 'var(--text-primary)' }}>Possible duplicates to review ({pairs.length})</h4>
        {pairs.length > 0 && (
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            <button className="btn-primary" style={smallButton} disabled={busy} onClick={() => decideAll('duplicate')}>
              Drop all {pairs.length} as duplicates
            </button>
            <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => decideAll('not_duplicate')}>
              Keep all as separate studies
            </button>
          </div>
        )}
      </div>
      {notice && <p role="status" style={{ color: 'var(--text-secondary)' }}>{notice}</p>}
      {pairs.length === 0 ? (
        <p style={{ color: 'var(--text-secondary)' }}>No record pairs with near-identical titles are waiting for a decision.</p>
      ) : (
        <ul aria-label="Possible duplicates to review" style={{ listStyle: 'none', padding: 0, display: 'flex', flexDirection: 'column', gap: '12px' }}>
          {pairs.map(pair => (
            <li key={`${pair.record.id}-${pair.other.id}`} style={{ background: 'var(--surface-muted)', borderRadius: '12px', padding: '16px' }}>
              <div style={{ color: '#9A5B00', fontSize: '0.85rem' }}>{pair.reasons.join(' · ')}</div>
              <RecordSummary record={pair.record} />
              <RecordSummary record={pair.other} />
              <PairActions first={pair.record} second={pair.other} busy={busy} onDuplicate={(keep) => decide(pair, 'duplicate', keep)} onDistinct={() => decide(pair, 'not_duplicate')} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function DeduplicationScreen() {
  const {
    handleRunDedup, dedupLoading, dedupVersion, prisma, currentProject, similar, similarLoading, similarProgress, handleFindSimilar, handleMarkDuplicate,
  } = useWorkspace();
  const embeddingModel = currentProject?.embedding_model ?? null;

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
      <h3 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>Deduplication</h3>
      <WorkspaceStageGate stage="search" />
      <p style={{ color: 'var(--text-secondary)' }}>
        Automatic deduplication sets aside records that share a DOI, PMID, PMCID, or trial registration number, or have the
        same title and year. Records with near-identical titles are listed for you to decide.
      </p>
      <button className="btn-primary" onClick={handleRunDedup} disabled={dedupLoading} style={{ marginTop: '8px', padding: '16px 32px' }}>
        {dedupLoading ? 'Checking identifiers and titles…' : 'Run automatic deduplication'}
      </button>
      {prisma !== null && prisma.duplicates_removed > 0 && (
        <div style={{ marginTop: '24px', color: '#137A47', fontSize: '1.1rem' }}>✓ {prisma.duplicates_removed} duplicates set aside.</div>
      )}

      <CandidatePairs version={dedupVersion} />

      <div style={{ marginTop: '40px', borderTop: '1px solid var(--border)', paddingTop: '24px' }}>
        <h4 style={{ margin: '0 0 8px', color: 'var(--text-primary)' }}>Possible duplicates by meaning</h4>
        <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginTop: 0 }}>
          Finds records whose titles and abstracts say nearly the same thing even when the titles differ, such as one study
          indexed differently by two databases.
          {embeddingModel && ` Titles and abstracts are sent to ${modelDisplayName(embeddingModel)}${embeddingModel.data_location ? ` (${embeddingModel.data_location})` : ''}.`}
        </p>
        <button className="btn-glass" onClick={handleFindSimilar} disabled={similarLoading || !embeddingModel} style={{ padding: '12px 24px' }}>
          {similarLoading ? 'Comparing records...' : 'Find possible duplicates by meaning'}
        </button>
        {similarProgress !== null && <ProgressBar progress={similarProgress} label="Comparing records..." />}

        {similar && similar.pairs.length === 0 && (
          <p style={{ color: 'var(--text-secondary)', marginTop: '16px' }}>No likely duplicates among {similar.embedded_records} records.</p>
        )}
        {similar && similar.pairs.length > 0 && (
          <ul aria-label="Possible duplicate pairs by meaning" style={{ listStyle: 'none', padding: 0, marginTop: '16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {similar.pairs.map(pair => (
              <li key={`${pair.record.id}-${pair.other.id}`} style={{ background: 'var(--surface-muted)', borderRadius: '12px', padding: '16px' }}>
                <div style={{ color: '#9A5B00', fontSize: '0.85rem' }}>{Math.round(pair.similarity * 100)}% similar in meaning</div>
                <RecordSummary record={pair.record} />
                <RecordSummary record={pair.other} />
                <PairActions first={pair.record} second={pair.other} busy={similarLoading} onDuplicate={(keep, duplicate) => handleMarkDuplicate(duplicate.id, keep.id)} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
