import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { LIMIT_ORDER, limitText, price, type PublicPlans } from '../api/account';
import { errorMessage, requestJson } from '../api/client';
import { useAuth } from '../auth/authContext';
import { LegalLinks } from '../components/LegalLinks';
import { GREEN, GREY, muted } from '../components/ui';

export function PricingPage() {
  const { token } = useAuth();
  const navigate = useNavigate();
  const [data, setData] = useState<PublicPlans | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    requestJson('GET', '/api/billing/plans')
      .then(result => {
        if (!cancelled) setData(result);
      })
      .catch(err => {
        if (!cancelled) setProblem(errorMessage(err, 'Plans could not be loaded.'));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="app-container" style={{ flexDirection: 'column', alignItems: 'center', minHeight: '100vh', padding: '48px 16px' }}>
      <div style={{ textAlign: 'center', marginBottom: '32px' }}>
        <Link to="/" style={{ color: 'var(--text-secondary)', textDecoration: 'none', fontSize: '0.9rem' }}>← OmniReview</Link>
        <h1 style={{ fontSize: '2.5rem', margin: '12px 0 8px' }}>Plans</h1>
        <p style={muted}>Every plan includes the full review workflow. AI work with your own API key never uses credits.</p>
      </div>
      {problem && <p role="alert">{problem}</p>}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '16px', justifyContent: 'center', width: '100%', maxWidth: '1200px' }}>
        {data?.plans.map(plan => {
          const monthly = price(plan.monthly_price_cents, plan.currency);
          const yearly = price(plan.yearly_price_cents, plan.currency);
          return (
            <section key={plan.code} aria-label={`${plan.name} plan`} className="glass-panel" style={{ flex: '1 1 240px', maxWidth: '280px', padding: '24px' }}>
              <h2 style={{ margin: '0 0 4px 0' }}>{plan.name}</h2>
              <p style={{ ...muted, minHeight: '40px' }}>{plan.description}</p>
              <div style={{ fontSize: '1.6rem', fontWeight: 700, margin: '12px 0' }}>
                {monthly === null ? 'Contact us' : monthly === 'Free' ? 'Free' : `${monthly} / month`}
              </div>
              {yearly && yearly !== 'Free' && <div style={muted}>or {yearly} / year</div>}
              <ul style={{ listStyle: 'none', padding: 0, margin: '16px 0', fontSize: '0.88rem' }}>
                {LIMIT_ORDER.map(limit => (
                  <li key={limit} style={{ padding: '3px 0' }}>
                    <strong>{limitText(plan.limits[limit])}</strong> {data.limit_labels[limit]}
                  </li>
                ))}
                {Object.entries(data.feature_labels).map(([feature, label]) => (
                  <li key={feature} style={{ padding: '3px 0', color: plan.limits[feature as 'api_access' | 'webhooks'] ? GREEN : GREY }}>
                    {plan.limits[feature as 'api_access' | 'webhooks'] ? '✓' : '—'} {label}
                  </li>
                ))}
              </ul>
              <button className="btn-primary" style={{ width: '100%' }} onClick={() => navigate(token ? '/billing' : '/login')}>
                {monthly === null ? 'Talk to us' : token ? 'Choose in Billing' : 'Create an account'}
              </button>
            </section>
          );
        })}
      </div>
      <div style={{ marginTop: '32px' }}>
        <LegalLinks />
      </div>
    </div>
  );
}
