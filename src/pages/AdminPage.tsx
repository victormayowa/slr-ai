import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type { MeInfo } from '../api/account';
import { useAuth } from '../auth/authContext';
import { muted, row, smallButton } from '../components/ui';
import { CostsTab } from '../features/admin/CostsTab';
import { ModelsTab } from '../features/admin/ModelsTab';
import { PlansTab } from '../features/admin/PlansTab';
import { SubscriptionsTab } from '../features/admin/SubscriptionsTab';
import { SystemTab } from '../features/admin/SystemTab';
import { UsersTab } from '../features/admin/UsersTab';

const TABS = [
  { key: 'system', label: 'System & readiness', render: () => <SystemTab /> },
  { key: 'users', label: 'Users', render: () => <UsersTab /> },
  { key: 'subscriptions', label: 'Subscriptions & organizations', render: () => <SubscriptionsTab /> },
  { key: 'plans', label: 'Plans', render: () => <PlansTab /> },
  { key: 'models', label: 'Models & benchmarks', render: () => <ModelsTab /> },
  { key: 'costs', label: 'Costs & failures', render: () => <CostsTab /> },
] as const;

// Platform administration. Administrator rights are granted with backend/scripts/make_admin.py.
export function AdminPage() {
  const { apiRequest } = useAuth();
  const navigate = useNavigate();
  const [me, setMe] = useState<MeInfo | null>(null);
  const [tab, setTab] = useState<(typeof TABS)[number]['key']>('system');

  useEffect(() => {
    let cancelled = false;
    apiRequest('GET', '/api/auth/me')
      .then(result => {
        if (!cancelled) setMe(result);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [apiRequest]);

  if (me && !me.is_platform_admin) {
    return (
      <div className="app-container" style={{ justifyContent: 'center', alignItems: 'center', minHeight: '100vh' }}>
        <div className="glass-panel" style={{ padding: '40px', maxWidth: '520px', textAlign: 'center' }}>
          <h2 style={{ marginTop: 0 }}>Administrators only</h2>
          <p style={muted}>This page manages the whole platform. Rights are granted on the server.</p>
          <button className="btn-primary" style={{ padding: '10px 24px', borderRadius: '8px' }} onClick={() => navigate('/')}>Back</button>
        </div>
      </div>
    );
  }

  const active = TABS.find(item => item.key === tab) ?? TABS[0];

  return (
    <div className="app-container" style={{ flexDirection: 'column', padding: '48px 24px', minHeight: '100vh' }}>
      <div className="glass-panel" style={{ width: '100%', maxWidth: '1080px', margin: '0 auto', padding: '32px' }}>
        <div style={{ ...row, justifyContent: 'space-between' }}>
          <h1 style={{ margin: 0, fontSize: '1.8rem' }}>Administration</h1>
          <button className="btn-glass" style={smallButton} onClick={() => navigate('/')}>Back to projects</button>
        </div>
        <div role="tablist" aria-label="Administration" style={{ ...row, margin: '20px 0' }}>
          {TABS.map(item => (
            <button key={item.key} role="tab" aria-selected={tab === item.key} className={tab === item.key ? 'btn-primary' : 'btn-glass'} style={smallButton} onClick={() => setTab(item.key)}>
              {item.label}
            </button>
          ))}
        </div>
        {me ? <div role="tabpanel" aria-label={active.label}>{active.render()}</div> : <p style={muted}>Loading…</p>}
      </div>
    </div>
  );
}
