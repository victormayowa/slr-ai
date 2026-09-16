import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { errorMessage, requestJson } from '../api/client';

export function VerifyEmailPage() {
  const [params] = useSearchParams();
  const token = params.get('token') ?? '';
  const [state, setState] = useState<{ done: boolean; message: string }>({ done: false, message: 'Confirming your email address…' });
  // Development mode mounts twice; the link works once, so it is only sent once.
  const sent = useRef(false);

  useEffect(() => {
    if (sent.current || !token) return;
    sent.current = true;
    requestJson('POST', '/api/auth/verify-email/confirm', { token })
      .then(result => setState({ done: true, message: `${result.email} is confirmed.` }))
      .catch(err => setState({ done: false, message: errorMessage(err, 'That link could not be used.') }));
  }, [token]);

  const message = token ? state.message : 'This link is incomplete.';

  return (
    <div className="app-container" style={{ justifyContent: 'center', alignItems: 'center', minHeight: '100vh' }}>
      <div className="glass-panel" style={{ maxWidth: '420px', padding: '40px', textAlign: 'center' }}>
        <h2 style={{ marginTop: 0 }}>{state.done ? 'Email address confirmed' : 'Confirm your email address'}</h2>
        <p role="status">{message}</p>
        <Link to="/login" className="btn-primary" style={{ display: 'inline-block', padding: '10px 24px', textDecoration: 'none' }}>
          {state.done ? 'Sign in' : 'Back to sign in'}
        </Link>
      </div>
    </div>
  );
}
