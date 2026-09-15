import { useCallback, useEffect, useState } from 'react';
import { ApiError, errorMessage } from '../../api/client';
import { DECISION_LABELS, type Conflict, type ExclusionReason, type ScreeningStage } from '../../api/screening';
import { useAuth } from '../../auth/authContext';
import { AMBER, GREEN, chip, fieldLabel, muted, panel, row } from '../../components/ui';
import { useWorkspace } from '../project/workspaceContext';

type Form = { decision: string; reason: string; rationale: string };

// Records whose reviewers disagree, settled by a third reviewer with a rationale.
export function ConflictsPanel({ stage, reasons }: { stage: ScreeningStage; reasons: ExclusionReason[] }) {
  const { projectId, refreshRecords } = useWorkspace();
  const { apiRequest } = useAuth();
  const base = `/api/projects/${projectId}`;
  const [conflicts, setConflicts] = useState<Conflict[] | null>(null);
  const [forms, setForms] = useState<Record<number, Form>>({});
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback((): Promise<Conflict[]> => apiRequest('GET', `${base}/screening/conflicts?stage=${stage}`), [apiRequest, base, stage]);

  useEffect(() => {
    let cancelled = false;
    load()
      .then(items => {
        if (!cancelled) setConflicts(items);
      })
      .catch(err => {
        if (cancelled) return;
        setNotice(err instanceof ApiError && err.status === 403 ? 'Only owners, lead reviewers, and methodologists adjudicate disagreements.' : errorMessage(err, 'Could not load disagreements.'));
        setConflicts([]);
      });
    return () => {
      cancelled = true;
    };
  }, [load]);

  const form = (id: number): Form => forms[id] ?? { decision: 'include', reason: '', rationale: '' };
  const decisions = stage === 'full_text' ? ['include', 'exclude', 'not_retrieved'] : ['include', 'exclude'];

  const adjudicate = async (conflict: Conflict) => {
    const values = form(conflict.record.id);
    setBusy(true);
    setNotice(null);
    try {
      await apiRequest('PUT', `${base}/records/${conflict.record.id}/adjudication`, {
        stage,
        decision: values.decision,
        reason_code: values.decision === 'exclude' && stage === 'full_text' ? values.reason || null : null,
        rationale: values.rationale,
      });
      setConflicts(await load());
      await refreshRecords();
      setNotice(`Recorded the final decision on "${conflict.record.title}".`);
    } catch (err) {
      setNotice(errorMessage(err, 'Could not record the decision.'));
    }
    setBusy(false);
  };

  return (
    <div>
      {notice && <p role="status" style={muted}>{notice}</p>}
      {conflicts?.length === 0 && !notice && <p style={muted}>No disagreements at this stage.</p>}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
        {conflicts?.map(conflict => {
          const values = form(conflict.record.id);
          const update = (changes: Partial<Form>) => setForms(prev => ({ ...prev, [conflict.record.id]: { ...values, ...changes } }));
          return (
            <article key={conflict.record.id} style={panel} aria-label={conflict.record.title}>
              <div style={{ ...row, justifyContent: 'space-between' }}>
                <strong>{conflict.record.title}</strong>
                <span style={chip(conflict.state === 'adjudicated' ? GREEN : AMBER)}>{conflict.state === 'adjudicated' ? 'Adjudicated' : 'Reviewers disagree'}</span>
              </div>
              {stage === 'title_abstract' && <p style={{ ...muted, maxHeight: '100px', overflowY: 'auto' }}>{conflict.record.abstract}</p>}
              <ul style={{ margin: '8px 0', paddingLeft: '20px', fontSize: '0.9rem' }}>
                {conflict.decisions.map((decision, index) => (
                  <li key={index}>
                    {decision.reviewer}: <strong>{DECISION_LABELS[decision.decision] ?? decision.decision}</strong>
                    {decision.reason && ` (${decision.reason})`}
                    {decision.note && <span style={muted}> · {decision.note}</span>}
                  </li>
                ))}
              </ul>
              {conflict.adjudication && (
                <p style={muted}>
                  Final: {DECISION_LABELS[conflict.adjudication.decision]} by {conflict.adjudication.adjudicator} · {conflict.adjudication.rationale}
                </p>
              )}
              <div style={row}>
                <label style={fieldLabel}>
                  Final decision
                  <select className="search-input" value={values.decision} onChange={e => update({ decision: e.target.value })}>
                    {decisions.map(decision => <option key={decision} value={decision}>{DECISION_LABELS[decision]}</option>)}
                  </select>
                </label>
                {stage === 'full_text' && values.decision === 'exclude' && (
                  <label style={fieldLabel}>
                    Reason
                    <select className="search-input" value={values.reason} onChange={e => update({ reason: e.target.value })}>
                      <option value="">Choose a reason…</option>
                      {reasons.map(reason => <option key={reason.code} value={reason.code}>{reason.label}</option>)}
                    </select>
                  </label>
                )}
                <label style={{ ...fieldLabel, flex: '1 1 280px' }}>
                  Rationale (at least 10 characters)
                  <input className="search-input" value={values.rationale} onChange={e => update({ rationale: e.target.value })} />
                </label>
                <button className="btn-primary" style={{ padding: '8px 16px', alignSelf: 'flex-end' }} disabled={busy || values.rationale.trim().length < 10} onClick={() => adjudicate(conflict)}>
                  {conflict.adjudication ? 'Change final decision' : 'Record final decision'}
                </button>
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}
