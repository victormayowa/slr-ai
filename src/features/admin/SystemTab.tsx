import { useCallback, useEffect, useState } from 'react';
import type { HealthCheck } from '../../api/account';
import { errorMessage } from '../../api/client';
import { useAuth } from '../../auth/authContext';
import { AMBER, GREEN, RED, chip, muted, panel, row, smallButton } from '../../components/ui';

const COLORS: Record<string, string> = { ok: GREEN, warn: AMBER, fail: RED };

function CheckList({ checks }: { checks: HealthCheck[] }) {
  return (
    <div>
      {checks.map(check => (
        <div key={check.name} style={{ ...row, fontSize: '0.85rem', padding: '4px 0', borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
          <span style={chip(COLORS[check.status])}>{check.status === 'ok' ? 'pass' : check.status}</span>
          <strong style={{ minWidth: '160px' }}>{check.name.replace(/_/g, ' ')}</strong>
          <span style={muted}>{check.detail}</span>
        </div>
      ))}
    </div>
  );
}

// Service health, and the production readiness checklist (scripts/production_check.py).
export function SystemTab() {
  const { apiRequest } = useAuth();
  const [system, setSystem] = useState<{ status: string; checks: HealthCheck[] } | null>(null);
  const [readiness, setReadiness] = useState<{ ready: boolean; checks: HealthCheck[] } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const fetchSystem = useCallback(() => apiRequest('GET', '/api/admin/system'), [apiRequest]);

  useEffect(() => {
    let cancelled = false;
    fetchSystem()
      .then(result => {
        if (!cancelled) setSystem(result);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'System status could not be loaded.'));
      });
    return () => {
      cancelled = true;
    };
  }, [fetchSystem]);

  const loadReadiness = async () => {
    setNotice(null);
    try {
      setReadiness(await apiRequest('GET', '/api/admin/readiness'));
    } catch (err) {
      setNotice(errorMessage(err, 'The readiness check could not run.'));
    }
  };

  return (
    <div>
      {notice && <p role="status" style={{ ...panel, margin: '12px 0' }}>{notice}</p>}
      <div style={{ ...row, justifyContent: 'space-between' }}>
        <h4 style={{ margin: 0 }}>Health</h4>
        <button className="btn-glass" style={smallButton} onClick={async () => setSystem(await fetchSystem())}>Refresh</button>
      </div>
      {system ? <CheckList checks={system.checks} /> : <p style={muted}>Loading…</p>}

      <div style={{ ...row, justifyContent: 'space-between', marginTop: '24px' }}>
        <h4 style={{ margin: 0 }}>Production readiness</h4>
        <button className="btn-primary" style={smallButton} onClick={loadReadiness}>Run readiness check</button>
      </div>
      <p style={muted}>Every configuration and service check before launch. Items no software can check are in docs/production-readiness.md.</p>
      {readiness && (
        <>
          <p style={{ color: readiness.ready ? GREEN : RED }}>{readiness.ready ? 'No failing checks.' : `${readiness.checks.filter(c => c.status === 'fail').length} failing checks.`}</p>
          <CheckList checks={[...readiness.checks].sort((a, b) => ['fail', 'warn', 'ok'].indexOf(a.status) - ['fail', 'warn', 'ok'].indexOf(b.status))} />
        </>
      )}
    </div>
  );
}
