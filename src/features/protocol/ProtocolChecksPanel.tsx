import { useCallback, useEffect, useState } from 'react';
import { errorMessage } from '../../api/client';
import type { ProtocolChecks, ProtocolIssue } from '../../api/protocol';
import { useAuth } from '../../auth/authContext';
import { useWorkspace } from '../project/workspaceContext';

const COLORS = { error: '#C62828', warning: '#9A5B00' } as const;

type IssueLike = Pick<ProtocolIssue, 'severity' | 'message' | 'criterion_ids'>;

// Checks the protocol before sign-off: rule-based checks always, and an optional AI consistency review.
export function ProtocolChecksPanel({ version }: { version: number }) {
  const { projectId, inclusionItems, exclusionItems, aiModelName } = useWorkspace();
  const { apiRequest } = useAuth();
  const [checks, setChecks] = useState<ProtocolChecks | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const criterionText = new Map([...inclusionItems, ...exclusionItems].map(item => [Number(item.id), item.text]));

  const load = useCallback(
    () => apiRequest('GET', `/api/projects/${projectId}/protocol-checks`),
    [apiRequest, projectId],
  );

  useEffect(() => {
    let cancelled = false;
    load()
      .then(data => {
        if (!cancelled) setChecks(data);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Could not load the protocol checks.'));
      });
    return () => {
      cancelled = true;
    };
  }, [load, version]);

  const review = async () => {
    setReviewing(true);
    setNotice(null);
    try {
      await apiRequest('POST', `/api/projects/${projectId}/protocol-checks/ai`);
      setChecks(await load());
    } catch (err) {
      setNotice(errorMessage(err, 'The AI review could not run.'));
    }
    setReviewing(false);
  };

  const renderIssues = (issues: IssueLike[], label: string) => (
    <ul aria-label={label} style={{ listStyle: 'none', padding: 0, margin: '8px 0', display: 'flex', flexDirection: 'column', gap: '6px' }}>
      {issues.map((issue, index) => (
        <li key={index} style={{ borderLeft: `3px solid ${COLORS[issue.severity]}`, paddingLeft: '10px' }}>
          <span style={{ color: COLORS[issue.severity], fontSize: '0.75rem', textTransform: 'uppercase', marginRight: '6px' }}>{issue.severity}</span>
          {issue.message}
          {issue.criterion_ids.length > 0 && (
            <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
              {issue.criterion_ids.map(id => criterionText.get(id) ?? `Criterion ${id}`).join(' · ')}
            </div>
          )}
        </li>
      ))}
    </ul>
  );

  return (
    <div role="region" aria-label="Protocol checks" style={{ background: 'var(--surface-muted)', borderRadius: '12px', padding: '16px', marginBottom: '24px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
        <h4 style={{ margin: 0 }}>Protocol checks</h4>
        <button className="btn-glass" onClick={review} disabled={reviewing} style={{ padding: '6px 12px' }}>
          {reviewing ? 'Reviewing…' : `Check consistency with ${aiModelName}`}
        </button>
      </div>
      {notice && <p role="status" style={{ color: 'var(--text-secondary)' }}>{notice}</p>}
      {checks && checks.issues.length === 0 && <p style={{ color: '#137A47' }}>No problems found by the rule-based checks.</p>}
      {checks && checks.issues.length > 0 && renderIssues(checks.issues, 'Rule-based checks')}
      {checks?.ai_review && (
        <div style={{ marginTop: '12px' }}>
          <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
            AI consistency review by {checks.ai_review.provider} {checks.ai_review.model} on {new Date(checks.ai_review.created_at).toLocaleString()}. These are suggestions; check each one.
          </div>
          {checks.ai_review.content.issues.length === 0
            ? <p style={{ margin: '8px 0' }}>The AI found no inconsistencies.</p>
            : renderIssues(checks.ai_review.content.issues, 'AI consistency review')}
        </div>
      )}
    </div>
  );
}
