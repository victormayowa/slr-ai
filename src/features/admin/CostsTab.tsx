import { useCallback, useEffect, useState } from 'react';
import { errorMessage } from '../../api/client';
import { useAuth } from '../../auth/authContext';
import { muted, panel, row, smallButton } from '../../components/ui';

type Costs = {
  days: number;
  runs: number;
  tokens: number;
  cost_usd: number;
  runs_without_prices: number;
  by_model: { provider: string; model: string; runs: number; tokens: number; cost_usd: number }[];
  by_project: { project_id: number; title: string; runs: number; tokens: number; cost_usd: number }[];
  by_day: { day: string; tokens: number; cost_usd: number }[];
};

type Failures = {
  ai_jobs: { id: number; project: string; task: string; error: string | null; created_at: string }[];
  webhook_deliveries: { id: number; action: string; error: string | null; attempts: number; created_at: string }[];
  billing_events: { id: number; provider: string; type: string; error: string | null; received_at: string }[];
};

const money = (value: number) => `$${value.toFixed(2)}`;
const cell = { padding: '4px 8px', borderBottom: '1px solid rgba(255,255,255,0.05)' } as const;

// AI spend on the server's own keys (what the operator pays for), and recent failures.
export function CostsTab() {
  const { apiRequest } = useAuth();
  const [days, setDays] = useState(30);
  const [costs, setCosts] = useState<Costs | null>(null);
  const [failures, setFailures] = useState<Failures | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const fetchData = useCallback(
    async (window: number) => Promise.all([apiRequest('GET', `/api/admin/costs?days=${window}`), apiRequest('GET', '/api/admin/failures')]) as Promise<[Costs, Failures]>,
    [apiRequest],
  );

  useEffect(() => {
    let cancelled = false;
    fetchData(days)
      .then(([costData, failureData]) => {
        if (cancelled) return;
        setCosts(costData);
        setFailures(failureData);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Costs could not be loaded.'));
      });
    return () => {
      cancelled = true;
    };
  }, [fetchData, days]);

  const maxDay = Math.max(1, ...(costs?.by_day.map(day => day.cost_usd) ?? [0]));

  return (
    <div>
      {notice && <p role="status" style={{ ...panel, margin: '12px 0' }}>{notice}</p>}
      <div style={{ ...row, justifyContent: 'space-between' }}>
        <h4 style={{ margin: 0 }}>AI on the server's keys</h4>
        <div role="group" aria-label="Period" style={row}>
          {[7, 30, 90].map(option => (
            <button key={option} className={days === option ? 'btn-primary' : 'btn-glass'} style={smallButton} onClick={() => setDays(option)}>
              {option} days
            </button>
          ))}
        </div>
      </div>
      {costs && (
        <>
          <p style={{ fontSize: '1.4rem', margin: '12px 0 4px' }}>{money(costs.cost_usd)}</p>
          <p style={muted}>
            {costs.runs.toLocaleString()} runs · {costs.tokens.toLocaleString()} tokens
            {costs.runs_without_prices > 0 && ` · ${costs.runs_without_prices} runs have no catalog price, so the total is a lower bound`}
          </p>
          {costs.by_day.length > 0 && (
            <div aria-label="Spend by day" style={{ display: 'flex', alignItems: 'flex-end', gap: '2px', height: '80px', margin: '12px 0' }}>
              {costs.by_day.map(day => (
                <div key={day.day} title={`${day.day}: ${money(day.cost_usd)}`} style={{ flex: 1, background: 'var(--accent-primary)', height: `${Math.max(2, (day.cost_usd / maxDay) * 100)}%`, opacity: 0.8 }} />
              ))}
            </div>
          )}
          <div style={{ overflowX: 'auto' }}>
            <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: '0.85rem' }}>
              <thead>
                <tr style={{ textAlign: 'left', color: 'var(--text-secondary)' }}>
                  <th style={cell}>Model</th><th style={cell}>Runs</th><th style={cell}>Tokens</th><th style={cell}>Cost</th>
                </tr>
              </thead>
              <tbody>
                {costs.by_model.map(item => (
                  <tr key={`${item.provider}/${item.model}`}>
                    <td style={cell}>{item.provider}/{item.model}</td><td style={cell}>{item.runs}</td><td style={cell}>{item.tokens.toLocaleString()}</td><td style={cell}>{money(item.cost_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <h4>Top projects</h4>
          {costs.by_project.map(item => (
            <div key={item.project_id} style={{ ...row, fontSize: '0.85rem' }}>
              <span style={{ flex: '1 1 260px' }}>{item.title}</span>
              <span style={muted}>{item.tokens.toLocaleString()} tokens</span>
              <span>{money(item.cost_usd)}</span>
            </div>
          ))}
        </>
      )}

      <h4 style={{ marginTop: '24px' }}>Recent failures</h4>
      {failures && (
        <div style={{ fontSize: '0.85rem' }}>
          {failures.ai_jobs.length + failures.webhook_deliveries.length + failures.billing_events.length === 0 && <p style={muted}>No recent failures.</p>}
          {failures.ai_jobs.map(job => <div key={`job-${job.id}`}>AI job #{job.id} ({job.task}) in {job.project}: <span style={muted}>{job.error}</span></div>)}
          {failures.webhook_deliveries.map(d => <div key={`hook-${d.id}`}>Webhook delivery #{d.id} ({d.action}) after {d.attempts} attempts: <span style={muted}>{d.error}</span></div>)}
          {failures.billing_events.map(e => <div key={`bill-${e.id}`}>Billing event {e.type} from {e.provider}: <span style={muted}>{e.error}</span></div>)}
        </div>
      )}
    </div>
  );
}
