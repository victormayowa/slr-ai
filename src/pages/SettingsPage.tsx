import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import type { BillingAccountSummary } from '../api/account';
import { useAuth } from '../auth/authContext';
import { LegalLinks } from '../components/LegalLinks';
import { ApiKeysPanel } from '../features/settings/ApiKeysPanel';
import { PrivacyPanel } from '../features/settings/PrivacyPanel';
import { SecurityPanel } from '../features/settings/SecurityPanel';
import { StoragePanel } from '../features/settings/StoragePanel';
import { TokensPanel } from '../features/settings/TokensPanel';

export function SettingsPage() {
  const { apiRequest, userName } = useAuth();
  const navigate = useNavigate();
  const [plan, setPlan] = useState<string | null>(null);
  const [userId, setUserId] = useState<number | null>(null);
  const name = userName ?? '';

  useEffect(() => {
    let cancelled = false;
    apiRequest('GET', '/api/auth/me')
      .then((me: { id: number }) => {
        if (!cancelled) setUserId(me.id);
      })
      .catch(() => undefined);
    apiRequest('GET', '/api/billing/accounts')
      .then((accounts: BillingAccountSummary[]) => {
        if (!cancelled) setPlan(accounts.find(account => account.kind === 'user')?.plan ?? null);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [apiRequest]);

  return (
    <div className="app-container" style={{ alignItems: 'center', flexDirection: 'column', minHeight: '100vh', position: 'relative', padding: '96px 16px 48px' }}>
      <button style={{ position: 'absolute', top: '32px', left: '40px', background: 'transparent', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer', fontSize: '1rem', display: 'flex', alignItems: 'center', gap: '8px' }} onClick={() => navigate('/')}>
        ← Back to Dashboard
      </button>
      <div style={{ textAlign: 'center', marginBottom: '32px' }}>
        <h1 style={{ fontSize: '2.5rem', margin: 0 }}>Account Settings</h1>
      </div>
      <div className="glass-panel" style={{ width: '100%', maxWidth: '720px', padding: '40px', border: '1px solid var(--border)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '20px', marginBottom: '40px' }}>
          <div style={{ width: '80px', height: '80px', borderRadius: '50%', background: 'var(--gold)', color: 'var(--navy)', fontFamily: 'var(--sans)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '2.5rem', fontWeight: 'bold' }}>
            {name.charAt(0).toUpperCase()}
          </div>
          <div>
            <h2 style={{ margin: '0 0 8px 0', fontSize: '1.5rem' }}>{name}</h2>
            <div style={{ color: 'var(--text-secondary)' }}>
              Personal plan: <span style={{ color: 'var(--accent-primary)', fontWeight: 'bold' }}>{plan ?? '…'}</span>{' '}
              <Link to="/billing" style={{ color: 'var(--accent-primary)', marginLeft: '8px' }}>Billing</Link>
            </div>
          </div>
        </div>

        <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '32px' }}>
          Each project chooses its AI model in Project Setup, so everyone on a review uses the same model.
        </p>

        <ApiKeysPanel />

        {userId !== null && <StoragePanel kind="user" accountId={userId} />}

        <SecurityPanel />

        <TokensPanel />

        <PrivacyPanel />

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '40px' }}>
          <LegalLinks align="left" />
          <button className="btn-primary" style={{ padding: '12px 32px', borderRadius: '8px' }} onClick={() => navigate('/')}>Done</button>
        </div>
      </div>
    </div>
  );
}
