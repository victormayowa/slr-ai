import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { LIMIT_ORDER, limitText, price, type BillingAccount, type BillingAccountSummary, type PublicPlans } from '../api/account';
import { errorMessage } from '../api/client';
import { useAuth } from '../auth/authContext';
import { AMBER, BLUE, GREEN, RED, chip, muted, panel, row, smallButton } from '../components/ui';

const STATUS_COLORS: Record<string, string> = { active: GREEN, trialing: BLUE, past_due: AMBER, canceled: RED, incomplete: AMBER };

function Meter({ label, used, limit }: { label: string; used: number; limit: number | null | undefined }) {
  const unlimited = limit === null || limit === undefined;
  const share = unlimited || limit === 0 ? (unlimited ? 0 : 1) : Math.min(1, used / limit);
  const color = share >= 1 ? RED : share >= 0.8 ? AMBER : GREEN;
  return (
    <div style={{ marginBottom: '10px' }}>
      <div style={{ ...row, justifyContent: 'space-between', fontSize: '0.85rem' }}>
        <span>{label}</span>
        <span style={muted}>
          {used.toLocaleString()} / {limitText(limit)}
        </span>
      </div>
      {!unlimited && (
        <div role="meter" aria-label={label} aria-valuenow={used} aria-valuemin={0} aria-valuemax={limit} style={{ height: '6px', background: 'rgba(255,255,255,0.08)', borderRadius: '3px', overflow: 'hidden' }}>
          <div style={{ width: `${share * 100}%`, height: '100%', background: color }} />
        </div>
      )}
    </div>
  );
}

export function BillingPage() {
  const { apiRequest } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [accounts, setAccounts] = useState<BillingAccountSummary[]>([]);
  const [selected, setSelected] = useState<string>('');
  const [account, setAccount] = useState<BillingAccount | null>(null);
  const [plans, setPlans] = useState<PublicPlans | null>(null);
  const [interval, setInterval] = useState<'month' | 'year'>('month');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(
    params.get('checkout') === 'success' ? 'Thanks. Your plan updates as soon as the payment is confirmed.' : params.get('checkout') === 'cancelled' ? 'Checkout was cancelled; nothing was charged.' : null,
  );

  useEffect(() => {
    let cancelled = false;
    Promise.all([apiRequest('GET', '/api/billing/accounts'), apiRequest('GET', '/api/billing/plans')])
      .then(([list, planList]: [BillingAccountSummary[], PublicPlans]) => {
        if (cancelled) return;
        setAccounts(list);
        setPlans(planList);
        if (list.length) setSelected(current => current || `${list[0].kind}/${list[0].id}`);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Billing could not be loaded.'));
      });
    return () => {
      cancelled = true;
    };
  }, [apiRequest]);

  const fetchAccount = useCallback((key: string): Promise<BillingAccount> => apiRequest('GET', `/api/billing/accounts/${key}`), [apiRequest]);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    fetchAccount(selected)
      .then(result => {
        if (!cancelled) setAccount(result);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'The account could not be loaded.'));
      });
    return () => {
      cancelled = true;
    };
  }, [selected, fetchAccount]);

  const act = async (action: () => Promise<string | void>, failure: string) => {
    setBusy(true);
    setNotice(null);
    try {
      const message = await action();
      setAccount(await fetchAccount(selected));
      if (message) setNotice(message);
    } catch (err) {
      setNotice(errorMessage(err, failure));
    }
    setBusy(false);
  };

  const goTo = (url: string) => {
    // Checkout and the portal are pages on the payment provider's site (or, in development, the simulated checkout).
    const target = new URL(url, window.location.origin);
    if (target.origin === window.location.origin) navigate(`${target.pathname}${target.search}`);
    else window.location.assign(url);
  };

  const subscription = account?.subscription;
  const paying = subscription && subscription.status !== 'canceled' && subscription.provider !== 'manual';

  return (
    <div className="app-container" style={{ flexDirection: 'column', alignItems: 'center', minHeight: '100vh', padding: '48px 16px' }}>
      <div className="glass-panel" style={{ width: '100%', maxWidth: '900px', padding: '32px' }}>
        <div style={{ ...row, justifyContent: 'space-between' }}>
          <h1 style={{ margin: 0, fontSize: '1.8rem' }}>Billing</h1>
          <button className="btn-glass" style={smallButton} onClick={() => navigate('/')}>Back to projects</button>
        </div>
        {notice && <p role="status" style={{ ...panel, margin: '12px 0' }}>{notice}</p>}
        {account && !account.enabled && <p style={muted}>Plan limits aren't enforced on this server, so every account is unlimited.</p>}

        {accounts.length > 1 && (
          <label style={{ ...row, margin: '16px 0', fontSize: '0.9rem' }}>
            Account
            <select className="search-input" style={{ width: 'auto' }} value={selected} onChange={e => setSelected(e.target.value)}>
              {accounts.map(item => (
                <option key={`${item.kind}/${item.id}`} value={`${item.kind}/${item.id}`}>
                  {item.label} {item.kind === 'organization' ? '(organization)' : '(personal)'}
                </option>
              ))}
            </select>
          </label>
        )}

        {account && (
          <>
            <section aria-label="Current plan" style={{ ...panel, marginTop: '16px' }}>
              <div style={{ ...row, justifyContent: 'space-between' }}>
                <div>
                  <div style={muted}>{account.label}</div>
                  <h2 style={{ margin: '4px 0' }}>{account.plan?.name ?? 'No plan'}</h2>
                </div>
                {subscription && (
                  <span style={chip(STATUS_COLORS[subscription.status] ?? BLUE)}>
                    {subscription.status.replace('_', ' ')}
                    {subscription.cancel_at_period_end ? ', ends at period end' : ''}
                  </span>
                )}
              </div>
              {subscription?.status === 'past_due' && <p style={{ color: AMBER }}>A payment is overdue. Update your payment details to keep this plan.</p>}
              {subscription?.current_period_end && <p style={muted}>Paid until {new Date(subscription.current_period_end).toLocaleDateString()}</p>}
              <div style={{ ...row, marginTop: '8px' }}>
                {paying && (
                  <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => act(async () => { goTo((await apiRequest('POST', `/api/billing/accounts/${selected}/portal`)).url); }, 'The billing portal could not be opened.')}>
                    Manage billing
                  </button>
                )}
                {paying && !subscription?.cancel_at_period_end && (
                  <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => {
                    if (!window.confirm('Cancel the subscription? The plan stays until the end of the paid period, then the account returns to Free.')) return;
                    act(async () => { await apiRequest('POST', `/api/billing/accounts/${selected}/cancel`); return 'Subscription cancelled.'; }, 'The subscription could not be cancelled.');
                  }}>
                    Cancel subscription
                  </button>
                )}
              </div>
            </section>

            <section aria-label="Usage" style={{ ...panel, marginTop: '16px' }}>
              <h3 style={{ marginTop: 0 }}>Usage</h3>
              {LIMIT_ORDER.map(limit => (
                <Meter key={limit} label={account.limit_labels[limit]} used={account.usage[limit] ?? 0} limit={account.plan?.limits[limit]} />
              ))}
              <p style={muted}>Monthly allowances reset on {new Date(account.usage_resets_on).toLocaleDateString()}.</p>
            </section>

            <section aria-label="Change plan" style={{ marginTop: '24px' }}>
              <div style={{ ...row, justifyContent: 'space-between' }}>
                <h3 style={{ margin: 0 }}>Change plan</h3>
                <div role="group" aria-label="Billing period" style={row}>
                  {(['month', 'year'] as const).map(option => (
                    <button key={option} className={interval === option ? 'btn-primary' : 'btn-glass'} style={smallButton} onClick={() => setInterval(option)}>
                      {option === 'month' ? 'Monthly' : 'Yearly'}
                    </button>
                  ))}
                </div>
              </div>
              {!account.checkout_available && <p style={muted}>Plans on this server are arranged with us directly. Contact support to change plan.</p>}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '12px', marginTop: '12px' }}>
                {plans?.plans.map(plan => {
                  const cents = interval === 'month' ? plan.monthly_price_cents : plan.yearly_price_cents;
                  const current = account.plan?.code === plan.code;
                  const label = price(cents, plan.currency);
                  return (
                    <div key={plan.code} style={{ ...panel, flex: '1 1 180px' }}>
                      <strong>{plan.name}</strong>
                      <div style={{ margin: '6px 0' }}>{label === null ? 'Contact us' : label === 'Free' ? 'Free' : `${label} / ${interval}`}</div>
                      {current ? (
                        <span style={chip(GREEN)}>current plan</span>
                      ) : cents === null ? (
                        <span style={muted}>Arranged with us</span>
                      ) : cents === 0 ? (
                        <span style={muted}>Cancel to return</span>
                      ) : (
                        <button className="btn-primary" style={smallButton} disabled={busy || !account.checkout_available} onClick={() => act(async () => {
                          goTo((await apiRequest('POST', `/api/billing/accounts/${selected}/checkout`, { plan_code: plan.code, interval })).url);
                        }, 'Checkout could not be started.')}>
                          Choose {plan.name}
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            </section>
          </>
        )}
      </div>
    </div>
  );
}
