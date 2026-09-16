import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { errorMessage } from '../api/client';
import { useAuth } from '../auth/authContext';
import { AMBER, muted, panel, row } from '../components/ui';

// Development only: stands in for the payment provider's checkout page. Choosing an outcome sends the same signed
// webhook a real provider would, through the server's webhook path.
export function DevCheckoutPage() {
  const { apiRequest } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const session = params.get('session') ?? '';
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  let summary: { plan?: string; interval?: string; account?: string } = {};
  try {
    summary = JSON.parse(atob(session.split('.')[0].replace(/-/g, '+').replace(/_/g, '/')));
  } catch {
    summary = {};
  }

  const complete = async (outcome: 'paid' | 'failed') => {
    setBusy(true);
    setProblem(null);
    try {
      await apiRequest('POST', '/api/billing/dev/complete', { session, outcome });
      navigate(`/billing?checkout=${outcome === 'paid' ? 'success' : 'cancelled'}`);
    } catch (err) {
      setProblem(errorMessage(err, 'The simulated payment failed.'));
      setBusy(false);
    }
  };

  return (
    <div className="app-container" style={{ justifyContent: 'center', alignItems: 'center', minHeight: '100vh' }}>
      <div className="glass-panel" style={{ maxWidth: '480px', padding: '32px' }}>
        <p style={{ ...panel, borderLeft: `3px solid ${AMBER}` }}>Simulated checkout for development. No real payment is taken.</p>
        <h2>Pay for the {summary.plan ?? 'selected'} plan</h2>
        <p style={muted}>
          Billed per {summary.interval ?? 'period'} to account {summary.account ?? ''}
        </p>
        {problem && <p role="alert">{problem}</p>}
        <div style={row}>
          <button className="btn-primary" disabled={busy || !session} onClick={() => complete('paid')}>Simulate successful payment</button>
          <button className="btn-glass" disabled={busy || !session} onClick={() => complete('failed')}>Simulate failed payment</button>
          <button className="btn-glass" disabled={busy} onClick={() => navigate('/billing?checkout=cancelled')}>Cancel</button>
        </div>
      </div>
    </div>
  );
}
