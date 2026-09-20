import { useCallback, useEffect, useState } from 'react';
import type { SecuritySummary } from '../../api/account';
import { errorMessage } from '../../api/client';
import { downloadFile } from '../../api/download';
import { useAuth } from '../../auth/authContext';
import { AMBER, muted, panel, row, smallButton } from '../../components/ui';

const CONFIRMATION = 'delete my account';

// A copy of your data, and deleting your account.
export function PrivacyPanel() {
  const { apiRequest, token } = useAuth();
  const [summary, setSummary] = useState<SecuritySummary | null>(null);
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const fetchSummary = useCallback((): Promise<SecuritySummary> => apiRequest('GET', '/api/me/security'), [apiRequest]);

  useEffect(() => {
    let cancelled = false;
    fetchSummary()
      .then(result => {
        if (!cancelled) setSummary(result);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Privacy settings could not be loaded.'));
      });
    return () => {
      cancelled = true;
    };
  }, [fetchSummary]);

  const act = async (action: () => Promise<string | void>, failure: string) => {
    setBusy(true);
    setNotice(null);
    try {
      const message = await action();
      setSummary(await fetchSummary());
      if (message) setNotice(message);
    } catch (err) {
      setNotice(errorMessage(err, failure));
    }
    setBusy(false);
  };

  return (
    <section aria-label="Privacy" style={{ marginTop: '40px' }}>
      <h3 style={{ marginBottom: '8px' }}>Privacy</h3>
      {notice && <p role="status" style={{ ...panel, margin: '12px 0' }}>{notice}</p>}

      <div style={panel}>
        <strong>Your data</strong>
        <p style={muted}>Download everything OmniReview holds about you as JSON. Passwords, keys, and token values are never included.</p>
        <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => act(async () => { await downloadFile(token, '/api/me/export', 'omnireview-my-data.json'); }, 'Your data could not be downloaded.')}>
          Download your data
        </button>
      </div>

      <div style={{ ...panel, marginTop: '12px', borderLeft: `3px solid ${AMBER}` }}>
        <strong>Delete your account</strong>
        {summary?.deletion_due_at ? (
          <>
            <p>Your account will be deleted on {new Date(summary.deletion_due_at).toLocaleDateString()}.</p>
            <button className="btn-primary" disabled={busy} onClick={() => act(async () => { await apiRequest('DELETE', '/api/me/deletion'); return 'Account deletion cancelled.'; }, 'The deletion could not be cancelled.')}>
              Cancel deletion
            </button>
          </>
        ) : (
          <>
            <p style={muted}>
              Deletion happens 14 days after you ask, and can be cancelled until then. Projects only you belong to are deleted with their files. Your
              contributions to reviews shared with others stay with those reviews, shown as "Deleted user". If you are the only owner of a shared
              project, hand over ownership first.
            </p>
            <div style={row}>
              <input aria-label="Password to delete your account" type="password" className="search-input" style={{ width: '180px' }} placeholder="Password" value={password} onChange={e => setPassword(e.target.value)} />
              <input aria-label="Type delete my account" className="search-input" style={{ width: '200px' }} placeholder={`Type "${CONFIRMATION}"`} value={confirmation} onChange={e => setConfirmation(e.target.value)} />
              <button className="btn-glass" style={{ ...smallButton, borderColor: '#C62828', color: '#C62828' }} disabled={busy || !password || confirmation.trim().toLowerCase() !== CONFIRMATION} onClick={() => act(async () => {
                await apiRequest('POST', '/api/me/deletion', { password, confirmation });
                setPassword('');
                setConfirmation('');
                return 'Your account is scheduled for deletion.';
              }, 'The account could not be scheduled for deletion.')}>
                Delete my account
              </button>
            </div>
          </>
        )}
      </div>
    </section>
  );
}
