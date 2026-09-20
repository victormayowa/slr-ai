import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { errorMessage, requestJson } from '../api/client';

export function ResetPasswordPage() {
  const [params] = useSearchParams();
  const token = params.get('token') ?? '';
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (password !== confirm) {
      setProblem('The passwords do not match.');
      return;
    }
    setBusy(true);
    setProblem(null);
    try {
      await requestJson('POST', '/api/auth/password-reset/confirm', { token, password });
      setDone(true);
    } catch (err) {
      setProblem(errorMessage(err, 'The password could not be changed.'));
    }
    setBusy(false);
  };

  return (
    <div className="app-container" style={{ justifyContent: 'center', alignItems: 'center', minHeight: '100vh' }}>
      <div className="glass-panel" style={{ width: '100%', maxWidth: '400px', padding: '40px' }}>
        <h2 style={{ marginTop: 0 }}>Choose a new password</h2>
        {done ? (
          <>
            <p role="status">Your password has been changed, and any other signed-in sessions have ended.</p>
            <Link to="/login" className="btn-primary" style={{ display: 'inline-block', padding: '10px 24px', textDecoration: 'none' }}>Sign in</Link>
          </>
        ) : !token ? (
          <p role="alert">This link is incomplete. Request a new one from the sign-in page.</p>
        ) : (
          <form onSubmit={submit}>
            {problem && <p role="alert" style={{ color: '#C62828' }}>{problem}</p>}
            <input aria-label="New password" type="password" required minLength={8} placeholder="New password (at least 8 characters)" className="search-input" style={{ width: '100%', marginBottom: '12px' }} value={password} onChange={e => setPassword(e.target.value)} />
            <input aria-label="Confirm new password" type="password" required minLength={8} placeholder="Confirm new password" className="search-input" style={{ width: '100%', marginBottom: '20px' }} value={confirm} onChange={e => setConfirm(e.target.value)} />
            <button type="submit" className="btn-primary" style={{ width: '100%' }} disabled={busy}>Change password</button>
          </form>
        )}
      </div>
    </div>
  );
}
