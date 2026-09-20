import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { limitText, money, price, shownLimits, type AiPriceList, type PublicPlans } from '../api/account';
import { errorMessage, requestJson } from '../api/client';
import { useAuth } from '../auth/authContext';
import { LegalLinks } from '../components/LegalLinks';
import { SiteBar } from '../components/SiteBar';
import { GREEN, GREY, muted } from '../components/ui';

export function PricingPage() {
  const { token } = useAuth();
  const navigate = useNavigate();
  const [data, setData] = useState<PublicPlans | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [prices, setPrices] = useState<AiPriceList | null>(null);

  useEffect(() => {
    let cancelled = false;
    requestJson('GET', '/api/billing/plans')
      .then(result => {
        if (!cancelled) setData(result);
      })
      .catch(err => {
        if (!cancelled) setProblem(errorMessage(err, 'Plans could not be loaded.'));
      });
    requestJson('GET', '/api/billing/ai-prices')
      .then((data: AiPriceList) => {
        if (!cancelled) setPrices(data);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="app-container" style={{ flexDirection: 'column', alignItems: 'center', minHeight: '100vh', padding: '104px 16px 48px' }}>
      <SiteBar>
        <Link to="/login" className="btn-glass" style={{ textDecoration: 'none' }}>Sign in</Link>
      </SiteBar>
      <div style={{ textAlign: 'center', marginBottom: '32px' }}>
        <h1 style={{ fontSize: '2.5rem', margin: '12px 0 8px' }}>Plans</h1>
        <p style={muted}>
          Every plan includes the full review workflow.{' '}
          {data?.platform_ai_keys === false ? 'AI features use your own provider API keys.' : 'AI work with your own API key never uses credits.'}
        </p>
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
                {shownLimits(data).map(limit => (
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
        {prices && prices.engines.length > 0 && (
        <section aria-label="AI engine prices" style={{ width: '100%', maxWidth: '900px', marginTop: '40px' }}>
          <h2 style={{ fontSize: '1.4rem' }}>AI engine prices</h2>
          <p style={muted}>
            What each engine costs when it runs on OmniReview's keys, {prices.unit}, paid from your AI balance.
            {prices.own_keys_free ? ' Add your own provider key in Settings and that work is never charged here.' : ''}
          </p>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.9rem' }}>
            <thead>
              <tr>
                <th style={{ padding: '8px' }}>Engine</th>
                <th style={{ padding: '8px' }}>Processed by</th>
                <th style={{ padding: '8px', textAlign: 'right' }}>Input</th>
                <th style={{ padding: '8px', textAlign: 'right' }}>Output</th>
              </tr>
            </thead>
            <tbody>
              {prices.engines.map(engine => (
                <tr key={`${engine.provider}/${engine.model}`}>
                  <td style={{ padding: '8px' }}>
                    {engine.label}
                    {engine.purpose === 'embedding' ? ' (similarity)' : ''}
                  </td>
                  <td style={{ padding: '8px', color: 'var(--text-secondary)' }}>{engine.data_location ?? engine.provider_label}</td>
                  <td style={{ padding: '8px', textAlign: 'right' }}>{money(engine.input_per_mtok)}</td>
                  <td style={{ padding: '8px', textAlign: 'right' }}>{money(engine.output_per_mtok)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <LegalLinks />
      </div>
    </div>
  );
}
