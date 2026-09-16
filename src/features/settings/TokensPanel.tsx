import { useCallback, useEffect, useState } from 'react';
import type { ApiTokenInfo } from '../../api/collaboration';
import { errorMessage } from '../../api/client';
import { useAuth } from '../../auth/authContext';
import { BLUE, GREEN, GREY, chip, fieldLabel, muted, panel, row, smallButton } from '../../components/ui';

// Personal access tokens for the public API, and whether notifications are also sent by email.
export function TokensPanel() {
  const { apiRequest } = useAuth();
  const [tokens, setTokens] = useState<ApiTokenInfo[]>([]);
  const [emailNotifications, setEmailNotifications] = useState(true);
  const [emailConfigured, setEmailConfigured] = useState(true);
  const [draft, setDraft] = useState({ name: '', write: false, expires_in_days: '' });
  const [created, setCreated] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const fetchSettings = useCallback(async () => {
    const [list, preferences] = await Promise.all([
      apiRequest('GET', '/api/me/tokens'),
      apiRequest('GET', '/api/me/notification-preferences'),
    ]);
    return { list, preferences } as {
      list: ApiTokenInfo[];
      preferences: { email_notifications: boolean; email_configured: boolean };
    };
  }, [apiRequest]);

  const apply = (result: { list: ApiTokenInfo[]; preferences: { email_notifications: boolean; email_configured: boolean } }) => {
    setTokens(result.list);
    setEmailNotifications(result.preferences.email_notifications);
    setEmailConfigured(result.preferences.email_configured);
  };

  useEffect(() => {
    let cancelled = false;
    fetchSettings()
      .then(result => {
        if (!cancelled) apply(result);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Could not load your tokens.'));
      });
    return () => {
      cancelled = true;
    };
  }, [fetchSettings]);

  const load = async () => apply(await fetchSettings());

  const act = async (action: () => Promise<string | void>, failure: string) => {
    setBusy(true);
    setNotice(null);
    try {
      const message = await action();
      await load();
      if (message) setNotice(message);
    } catch (err) {
      setNotice(errorMessage(err, failure));
    }
    setBusy(false);
  };

  const active = tokens.filter(token => !token.revoked_at);

  return (
    <div style={{ marginTop: '40px' }}>
      <h3 style={{ marginBottom: '8px' }}>Notifications</h3>
      <label style={{ ...row, fontSize: '0.9rem' }}>
        <input
          type="checkbox"
          checked={emailNotifications}
          disabled={busy}
          onChange={e => act(async () => {
            await apiRequest('PUT', '/api/me/notification-preferences', { email_notifications: e.target.checked });
          }, 'Could not save your preference.')}
        />
        Also email me about mentions, replies, and tasks
      </label>
      {!emailConfigured && <p style={muted}>This server has no mail set up yet, so notifications stay in the app.</p>}

      <h3 style={{ marginTop: '32px', marginBottom: '8px' }}>API tokens</h3>
      <p style={muted}>
        Tokens let scripts and other systems use the API as you. A read token can't change anything, and no token can
        create tokens or reach administration. The API is documented at <code>/docs</code>.
      </p>
      {notice && <p role="status" style={{ ...panel, margin: '12px 0' }}>{notice}</p>}

      <div style={{ ...panel, ...row }}>
        <label style={fieldLabel}>
          Name
          <input className="search-input" style={{ width: '180px' }} placeholder="Reporting script" value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} />
        </label>
        <label style={{ ...row, fontSize: '0.85rem' }}>
          <input type="checkbox" checked={draft.write} onChange={e => setDraft({ ...draft, write: e.target.checked })} />
          Allow writing
        </label>
        <label style={fieldLabel}>
          Expires in days
          <input className="search-input" style={{ width: '120px' }} type="number" min={1} max={365} placeholder="never" value={draft.expires_in_days} onChange={e => setDraft({ ...draft, expires_in_days: e.target.value })} />
        </label>
        <button className="btn-primary" disabled={busy || !draft.name.trim()} onClick={() => act(async () => {
          const token: ApiTokenInfo = await apiRequest('POST', '/api/me/tokens', {
            name: draft.name.trim(),
            scopes: draft.write ? ['read', 'write'] : ['read'],
            expires_in_days: draft.expires_in_days ? Number(draft.expires_in_days) : null,
          });
          setCreated(token.token ?? null);
          setDraft({ name: '', write: false, expires_in_days: '' });
          return 'Token created. Copy it now: it is shown only once.';
        }, 'Could not create the token.')}>
          Create token
        </button>
      </div>

      {created && (
        <p style={{ ...panel, marginTop: '8px', fontSize: '0.8rem', overflowWrap: 'anywhere' }}>
          <code>{created}</code>
        </p>
      )}

      {active.length === 0 && <p style={muted}>You have no active tokens.</p>}
      {active.map(token => (
        <div key={token.id} style={{ ...row, fontSize: '0.85rem', marginTop: '8px' }}>
          <span style={{ minWidth: '160px' }}>{token.name}</span>
          <code style={{ color: 'var(--text-secondary)' }}>{token.prefix}…</code>
          {token.scopes.map(scope => <span key={scope} style={chip(scope === 'write' ? BLUE : GREEN)}>{scope}</span>)}
          {token.project_ids.length > 0 && <span style={chip(GREY)}>{token.project_ids.length} projects</span>}
          <span style={muted}>{token.last_used_at ? `last used ${new Date(token.last_used_at).toLocaleDateString()}` : 'never used'}</span>
          <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => act(async () => {
            await apiRequest('DELETE', `/api/me/tokens/${token.id}`);
            return 'Token revoked.';
          }, 'Could not revoke the token.')}>
            Revoke
          </button>
        </div>
      ))}
    </div>
  );
}
