import { useCallback, useEffect, useState } from 'react';
import type { SecuritySummary } from '../../api/account';
import { errorMessage } from '../../api/client';
import { useAuth } from '../../auth/authContext';
import { AMBER, GREEN, chip, fieldLabel, muted, panel, row, smallButton } from '../../components/ui';

// Password, email verification, and two-factor sign-in.
export function SecurityPanel() {
  const { apiRequest, signIn, userName } = useAuth();
  const [summary, setSummary] = useState<SecuritySummary | null>(null);
  const [passwords, setPasswords] = useState({ current_password: '', new_password: '' });
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [code, setCode] = useState('');
  const [disablePassword, setDisablePassword] = useState('');
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);
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
        if (!cancelled) setNotice(errorMessage(err, 'Security settings could not be loaded.'));
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
    <section aria-label="Security" style={{ marginTop: '40px' }}>
      <h3 style={{ marginBottom: '8px' }}>Security</h3>
      {notice && <p role="status" style={{ ...panel, margin: '12px 0' }}>{notice}</p>}

      {summary && (
        <div style={{ ...row, marginBottom: '16px' }}>
          <span style={chip(summary.email_verified ? GREEN : AMBER)}>{summary.email_verified ? 'Email confirmed' : 'Email not confirmed'}</span>
          <span style={chip(summary.mfa_enabled ? GREEN : AMBER)}>{summary.mfa_enabled ? 'Two-factor on' : 'Two-factor off'}</span>
          {!summary.email_verified && (
            <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => act(async () => (await apiRequest('POST', '/api/auth/verify-email/request')).message, 'The link could not be sent.')}>
              Send confirmation link
            </button>
          )}
        </div>
      )}

      <h4>Change password</h4>
      <div style={{ ...panel, ...row }}>
        <label style={fieldLabel}>
          Current password
          <input type="password" className="search-input" value={passwords.current_password} onChange={e => setPasswords({ ...passwords, current_password: e.target.value })} />
        </label>
        <label style={fieldLabel}>
          New password
          <input type="password" minLength={8} className="search-input" value={passwords.new_password} onChange={e => setPasswords({ ...passwords, new_password: e.target.value })} />
        </label>
        <button className="btn-primary" style={{ alignSelf: 'flex-end' }} disabled={busy || !passwords.current_password || passwords.new_password.length < 8} onClick={() => act(async () => {
          const session = await apiRequest('POST', '/api/me/password', passwords);
          // Other sessions end; this one continues with the new session token.
          signIn(session.access_token, userName ?? session.user.name);
          setPasswords({ current_password: '', new_password: '' });
          return 'Password changed. Other signed-in sessions have ended.';
        }, 'The password could not be changed.')}>
          Change password
        </button>
      </div>

      <h4>Two-factor sign-in</h4>
      {recoveryCodes && (
        <div style={{ ...panel, borderLeft: `3px solid ${AMBER}`, marginBottom: '12px' }}>
          <p style={{ marginTop: 0 }}>Save these recovery codes somewhere safe. Each works once, and they are not shown again.</p>
          <pre aria-label="Recovery codes" style={{ fontSize: '0.95rem', columns: 2 }}>{recoveryCodes.join('\n')}</pre>
          <button className="btn-glass" style={smallButton} onClick={() => setRecoveryCodes(null)}>I've saved them</button>
        </div>
      )}
      {summary && !summary.mfa_enabled && !setup && (
        <div style={panel}>
          <p style={muted}>Sign in with a code from an authenticator app as well as your password.</p>
          <button className="btn-primary" disabled={busy} onClick={() => act(async () => { setSetup(await apiRequest('POST', '/api/me/mfa/setup')); }, 'Two-factor setup could not start.')}>
            Set up two-factor sign-in
          </button>
        </div>
      )}
      {summary && !summary.mfa_enabled && setup && (
        <div style={panel}>
          <p style={{ marginTop: 0 }}>Add this key to your authenticator app, or open the link on the device with the app:</p>
          <code aria-label="Setup key" style={{ display: 'block', fontSize: '1rem', letterSpacing: '0.1em', overflowWrap: 'anywhere', marginBottom: '8px' }}>{setup.secret}</code>
          <a href={setup.otpauth_uri} style={{ fontSize: '0.8rem', color: 'var(--accent-primary)', overflowWrap: 'anywhere' }}>{setup.otpauth_uri}</a>
          <div style={{ ...row, marginTop: '12px' }}>
            <input aria-label="Code from your authenticator app" className="search-input" style={{ width: '140px' }} inputMode="numeric" placeholder="123456" value={code} onChange={e => setCode(e.target.value)} />
            <button className="btn-primary" disabled={busy || code.trim().length < 6} onClick={() => act(async () => {
              const result = await apiRequest('POST', '/api/me/mfa/enable', { code: code.trim() });
              setRecoveryCodes(result.recovery_codes);
              setSetup(null);
              setCode('');
              return 'Two-factor sign-in is on.';
            }, 'That code was not accepted.')}>
              Turn on
            </button>
          </div>
        </div>
      )}
      {summary?.mfa_enabled && (
        <div style={panel}>
          <p style={muted}>{summary.recovery_codes_left} recovery codes left.</p>
          <div style={row}>
            <input aria-label="Current code" className="search-input" style={{ width: '160px' }} placeholder="Code or recovery code" value={code} onChange={e => setCode(e.target.value)} />
            <button className="btn-glass" style={smallButton} disabled={busy || !code.trim()} onClick={() => act(async () => {
              const result = await apiRequest('POST', '/api/me/mfa/recovery-codes', { code: code.trim() });
              setRecoveryCodes(result.recovery_codes);
              setCode('');
              return 'New recovery codes created; the old ones no longer work.';
            }, 'New recovery codes could not be created.')}>
              New recovery codes
            </button>
            <input aria-label="Password to turn off two-factor" type="password" className="search-input" style={{ width: '160px' }} placeholder="Password" value={disablePassword} onChange={e => setDisablePassword(e.target.value)} />
            <button className="btn-glass" style={smallButton} disabled={busy || !code.trim() || !disablePassword} onClick={() => act(async () => {
              await apiRequest('POST', '/api/me/mfa/disable', { password: disablePassword, code: code.trim() });
              setCode('');
              setDisablePassword('');
              return 'Two-factor sign-in is off.';
            }, 'Two-factor sign-in could not be turned off.')}>
              Turn off
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
