import { useCallback, useEffect, useState } from 'react';
import { LIMIT_ORDER, limitText, price, type PlanInfo } from '../../api/account';
import { errorMessage } from '../../api/client';
import { useAuth } from '../../auth/authContext';
import { GREEN, GREY, chip, fieldLabel, muted, panel, row, smallButton } from '../../components/ui';

type AdminPlan = PlanInfo & { id: number; provider_prices: Record<string, Record<string, string>>; public: boolean; active: boolean; position: number; subscribers: number };

// Plans, their limits, and the payment provider prices they link to. Limits apply to accounts straight away.
export function PlansTab() {
  const { apiRequest } = useAuth();
  const [plans, setPlans] = useState<AdminPlan[] | null>(null);
  const [editing, setEditing] = useState<{ id: number; json: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const fetchPlans = useCallback((): Promise<AdminPlan[]> => apiRequest('GET', '/api/admin/plans'), [apiRequest]);

  useEffect(() => {
    let cancelled = false;
    fetchPlans()
      .then(result => {
        if (!cancelled) setPlans(result);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Plans could not be loaded.'));
      });
    return () => {
      cancelled = true;
    };
  }, [fetchPlans]);

  const save = async (plan: AdminPlan) => {
    if (!editing) return;
    setBusy(true);
    setNotice(null);
    try {
      const body = JSON.parse(editing.json);
      await apiRequest('PUT', `/api/admin/plans/${plan.id}`, body);
      setPlans(await fetchPlans());
      setEditing(null);
      setNotice(`${body.name} saved.`);
    } catch (err) {
      setNotice(err instanceof SyntaxError ? 'That isn\'t valid JSON.' : errorMessage(err, 'The plan could not be saved.'));
    }
    setBusy(false);
  };

  const editable = (plan: AdminPlan) =>
    JSON.stringify(
      {
        code: plan.code,
        name: plan.name,
        description: plan.description,
        monthly_price_cents: plan.monthly_price_cents,
        yearly_price_cents: plan.yearly_price_cents,
        currency: plan.currency,
        limits: plan.limits,
        provider_prices: plan.provider_prices,
        public: plan.public,
        active: plan.active,
        position: plan.position,
      },
      null,
      2,
    );

  if (!plans) return <p>{notice ?? 'Loading…'}</p>;

  return (
    <div>
      <p style={muted}>
        Limits are numbers or null for unlimited. For Stripe, set provider_prices to {'{"stripe": {"month": "price_...", "year": "price_..."}}'}. Prices shown here are
        what the pricing page displays; what customers are charged is set in the payment provider.
      </p>
      {notice && <p role="status" style={{ ...panel, margin: '12px 0' }}>{notice}</p>}
      {plans.map(plan => (
        <div key={plan.id} style={{ ...panel, marginBottom: '8px', fontSize: '0.85rem' }}>
          <div style={row}>
            <strong style={{ flex: '1 1 160px' }}>{plan.name}</strong>
            <span style={muted}>{price(plan.monthly_price_cents, plan.currency) ?? 'Contact us'} / month</span>
            <span style={chip(plan.active ? GREEN : GREY)}>{plan.active ? 'active' : 'inactive'}</span>
            {!plan.public && <span style={chip(GREY)}>hidden</span>}
            <span style={muted}>{plan.subscribers} subscribers</span>
            <button className="btn-glass" style={smallButton} onClick={() => setEditing(editing?.id === plan.id ? null : { id: plan.id, json: editable(plan) })}>
              {editing?.id === plan.id ? 'Close' : 'Edit'}
            </button>
          </div>
          <div style={{ ...row, marginTop: '6px' }}>
            {LIMIT_ORDER.map(limit => (
              <span key={limit} style={muted}>
                {limit.replace(/_/g, ' ')}: {limitText(plan.limits[limit])}
              </span>
            ))}
          </div>
          {editing?.id === plan.id && (
            <label style={{ ...fieldLabel, marginTop: '8px' }}>
              Plan settings (JSON)
              <textarea aria-label={`Settings for ${plan.name}`} className="search-input" rows={18} style={{ fontFamily: 'ui-monospace, monospace', fontSize: '0.8rem' }} value={editing.json} onChange={e => setEditing({ id: plan.id, json: e.target.value })} />
              <button className="btn-primary" style={{ alignSelf: 'flex-start' }} disabled={busy} onClick={() => save(plan)}>Save plan</button>
            </label>
          )}
        </div>
      ))}
    </div>
  );
}
