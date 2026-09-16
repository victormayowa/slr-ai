import { useCallback, useEffect, useState } from 'react';
import type { BiasReport, CalibrationList, CalibrationReportInfo, ReproducibilityCheckInfo, SopReport } from '../../api/collaboration';
import { errorMessage } from '../../api/client';
import { useAuth } from '../../auth/authContext';
import { AMBER, BLUE, GREEN, GREY, RED, chip, fieldLabel, fmt, muted, panel, percent, row, smallButton } from '../../components/ui';
import { useWorkspace } from '../project/workspaceContext';

const CHECK_COLORS: Record<string, string> = { identical: GREEN, within_tolerance: GREEN, different: AMBER, failed: RED };

type Loaded = {
  calibration: CalibrationList;
  bias: BiasReport;
  sop: SopReport;
  checks: ReproducibilityCheckInfo[];
};

// AI governance: whether the model was calibrated against this project's own reviewers before its suggestions were
// trusted, what the evidence base looks like next to what was retrieved, and the quality reports.
export function GovernanceScreen() {
  const { projectId, aiModelName } = useWorkspace();
  const { apiRequest } = useAuth();
  const base = `/api/projects/${projectId}`;
  const [data, setData] = useState<Loaded | null>(null);
  const [sampleSize, setSampleSize] = useState(30);
  const [note, setNote] = useState('');
  const [runId, setRunId] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async (): Promise<Loaded> => {
    const [calibration, bias, sop, checks] = await Promise.all([
      apiRequest('GET', `${base}/calibration`),
      apiRequest('GET', `${base}/bias-report`),
      apiRequest('GET', `${base}/reports/sop`),
      apiRequest('GET', `${base}/reproducibility-checks`),
    ]);
    return { calibration, bias, sop, checks };
  }, [apiRequest, base]);

  useEffect(() => {
    let cancelled = false;
    load()
      .then(result => {
        if (!cancelled) setData(result);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Could not load governance.'));
      });
    return () => {
      cancelled = true;
    };
  }, [load]);

  const act = async (action: () => Promise<string | void>, failure: string) => {
    setBusy(true);
    setNotice(null);
    try {
      const message = await action();
      setData(await load());
      if (message) setNotice(message);
    } catch (err) {
      setNotice(errorMessage(err, failure));
    }
    setBusy(false);
  };

  if (!data) return <section className="glass-panel" style={{ padding: '32px' }}>{notice ?? 'Loading governance…'}</section>;

  const latest: CalibrationReportInfo | undefined = data.calibration.reports[0];
  const accepted = data.calibration.reports.find(report => report.accepted_at !== null);

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
      <h3 style={{ marginBottom: '8px' }}>AI Governance</h3>
      <p style={muted}>
        Calibration runs {aiModelName ?? 'the project’s model'} over records your reviewers have already decided, so you can see how it would have performed before you rely on it. No AI suggestion is ever a decision.
      </p>
      {notice && <p role="status" style={{ ...panel, margin: '12px 0' }}>{notice}</p>}

      <h4 style={{ marginTop: '20px' }}>Calibration</h4>
      <div style={{ ...panel, ...row }}>
        <label style={fieldLabel}>
          Records to sample
          <input className="search-input" style={{ width: '120px' }} type="number" min={10} max={100} value={sampleSize} onChange={e => setSampleSize(Number(e.target.value))} />
        </label>
        <button className="btn-primary" disabled={busy} onClick={() => act(async () => {
          const report: CalibrationReportInfo = await apiRequest('POST', `${base}/calibration`, { sample_size: sampleSize });
          return report.passed ? 'Calibration passed the recall target.' : 'Calibration is below the recall target.';
        }, 'Could not run calibration.')}>
          {busy ? 'Running…' : 'Run calibration'}
        </button>
        <span style={muted}>Recall target {percent(data.calibration.recall_target)} · prompt {data.calibration.current_prompt_version}</span>
      </div>

      {data.calibration.require_ai_calibration && !accepted && (
        <p role="alert" style={{ ...panel, marginTop: '8px', borderLeft: `3px solid ${AMBER}` }}>
          This project requires calibration, so AI screening stays switched off until a report is accepted.
        </p>
      )}

      {!latest && <p style={muted}>No calibration has been run yet.</p>}
      {data.calibration.reports.map(report => (
        <div key={report.id} style={{ ...panel, marginTop: '8px', fontSize: '0.85rem' }}>
          <div style={{ ...row }}>
            <span style={chip(report.passed ? GREEN : RED)}>{report.passed ? 'passed' : 'below target'}</span>
            <span>{report.model}</span>
            <span style={muted}>{report.sample_size} records · {report.prompt_version}</span>
            {report.accepted_at && <span style={chip(BLUE)}>accepted</span>}
          </div>
          <div style={{ ...row, marginTop: '6px' }}>
            <span>Recall {percent(report.metrics.recall)}</span>
            {report.metrics.recall_ci && <span style={muted}>95% CI {percent(report.metrics.recall_ci[0])}–{percent(report.metrics.recall_ci[1])}</span>}
            <span>Specificity {percent(report.metrics.specificity)}</span>
            <span>Agreement {percent(report.metrics.agreement)}</span>
            {report.metrics.failed > 0 && <span style={chip(AMBER)}>{report.metrics.failed} failed calls</span>}
          </div>
          {report.error && <p style={{ ...muted, color: RED }}>{report.error}</p>}
          {!report.accepted_at && report.status === 'completed' && (
            <div style={{ ...row, marginTop: '8px' }}>
              <input aria-label={`Note for calibration ${report.id}`} className="search-input" style={{ flex: '1 1 260px' }} placeholder={report.passed ? 'Optional note' : 'Explain why you are accepting a report below target'} value={note} onChange={e => setNote(e.target.value)} />
              <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => act(async () => {
                await apiRequest('POST', `${base}/calibration/${report.id}/accept`, { note });
                setNote('');
                return 'Calibration accepted.';
              }, 'Could not accept the report.')}>
                Accept
              </button>
            </div>
          )}
          {report.acceptance_note && <p style={muted}>Accepted with a note: {report.acceptance_note}</p>}
        </div>
      ))}

      <h4 style={{ marginTop: '24px' }}>What reached the review</h4>
      <p style={muted}>
        {data.bias.records} records retrieved, {data.bias.included} included. {data.bias.note}
      </p>
      {data.bias.groups.map(group => (
        <div key={group.field} style={{ ...panel, marginTop: '8px', fontSize: '0.82rem' }}>
          <strong style={{ textTransform: 'capitalize' }}>{group.field}</strong>
          <div style={{ ...row, marginTop: '6px' }}>
            {group.retrieved.slice(0, 6).map(item => {
              const included = group.included.find(other => other.value === item.value);
              return (
                <span key={item.value} style={chip(GREY)}>
                  {item.value}: {percent(item.share)} retrieved → {percent(included?.share ?? 0)} included
                </span>
              );
            })}
          </div>
        </div>
      ))}
      {data.bias.ai_errors_by_year.length > 0 && (
        <>
          <h4 style={{ marginTop: '16px' }}>Where the AI disagreed with reviewers</h4>
          {data.bias.ai_errors_by_year.map(item => (
            <div key={item.year} style={{ ...row, fontSize: '0.82rem' }}>
              <span style={{ minWidth: '80px' }}>{item.year}</span>
              <span style={chip(item.missed > 0 ? RED : GREEN)}>{item.missed} missed includes</span>
              <span style={chip(GREY)}>{item.over_included} over-included</span>
              <span style={muted}>of {item.compared} compared</span>
            </div>
          ))}
        </>
      )}
      {data.bias.mean_ai_confidence !== null && <p style={muted}>Mean AI confidence {fmt(data.bias.mean_ai_confidence)}.</p>}

      <h4 style={{ marginTop: '24px' }}>How this review was run</h4>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
          <thead>
            <tr style={{ textAlign: 'left', color: 'var(--text-secondary)' }}>
              <th>Stage</th><th>Signed off</th><th>By</th>
            </tr>
          </thead>
          <tbody>
            {data.sop.stages.map(stage => (
              <tr key={stage.stage} style={{ borderTop: '1px solid rgba(255,255,255,0.06)' }}>
                <td>{stage.label}</td>
                <td>{stage.completed_at ? new Date(stage.completed_at).toLocaleDateString() : <span style={muted}>open</span>}</td>
                <td>{stage.completed_by ?? '–'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h4 style={{ marginTop: '24px' }}>Reproducibility checks</h4>
      <p style={muted}>Rerun a finished analysis from its stored specification and data, and compare every number with the original run.</p>
      <div style={{ ...panel, ...row }}>
        <label style={fieldLabel}>
          Analysis run number
          <input aria-label="Analysis run number" type="number" min={1} className="search-input" style={{ width: '140px' }} value={runId} onChange={e => setRunId(e.target.value)} />
        </label>
        <button className="btn-primary" style={{ alignSelf: 'flex-end' }} disabled={busy || !runId} onClick={() => act(async () => {
          const check: ReproducibilityCheckInfo = await apiRequest('POST', `${base}/analysis-runs/${runId}/reproduce`);
          return check.status === 'failed' ? `The rerun failed: ${check.error ?? 'see the check below'}` : `Rerun ${check.status.replace(/_/g, ' ')}.`;
        }, 'The analysis could not be rerun.')}>
          {busy ? 'Rerunning…' : 'Rerun'}
        </button>
      </div>
      {data.checks.length === 0 ? (
        <p style={muted}>No analysis has been rerun yet.</p>
      ) : (
        data.checks.map(check => (
          <div key={check.id} style={{ ...row, fontSize: '0.85rem', marginTop: '4px' }}>
            <span style={chip(CHECK_COLORS[check.status] ?? GREY)}>{check.status.replace(/_/g, ' ')}</span>
            <span>Run {check.run_id}</span>
            <span style={muted}>largest difference {fmt(check.max_abs_difference, 8)}</span>
            {check.error && <span style={{ ...muted, color: RED }}>{check.error}</span>}
          </div>
        ))
      )}
    </section>
  );
}
