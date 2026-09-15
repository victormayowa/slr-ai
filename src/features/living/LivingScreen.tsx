import { useCallback, useEffect, useState } from 'react';
import { errorMessage } from '../../api/client';
import type { AnalysisInfo } from '../../api/evidence';
import { jobProblem, waitForJob } from '../../api/jobs';
import type { AlertInfo, CandidateInfo, EvidenceMap, FeedInfo, ImpactInfo, ReleaseInfo, ScheduleInfo, SurveillanceRunInfo } from '../../api/publishing';
import type { StrategyInfo } from '../../api/review';
import { useAuth } from '../../auth/authContext';
import { AMBER, BLUE, GREEN, GREY, RED, chip, fieldLabel, fmt, muted, panel, row, smallButton } from '../../components/ui';
import { useWorkspace } from '../project/workspaceContext';

const CERTAINTY_COLORS: Record<string, string> = { high: GREEN, moderate: '#84cc16', low: AMBER, very_low: RED };
const COVERAGE_COLORS: Record<string, string> = { reported: GREEN, not_reported: AMBER, missing: 'rgba(255,255,255,0.08)' };
const ALERT_COLORS: Record<string, string> = { retraction: RED, large_trial: AMBER, new_eligible: BLUE, new_records: GREY, feed_update: GREY };

type Loaded = {
  schedules: { schedules: ScheduleInfo[]; connectors: Record<string, string> };
  runs: SurveillanceRunInfo[];
  alerts: AlertInfo[];
  candidates: CandidateInfo[];
  feeds: FeedInfo[];
  releases: ReleaseInfo[];
  impact: ImpactInfo[];
  map: EvidenceMap;
  strategies: StrategyInfo[];
  analyses: AnalysisInfo[];
};

// Living review: scheduled surveillance, alerts, candidate records promoted into living updates, provisional impact
// assessments, versioned releases, and evidence and gap maps.
export function LivingScreen() {
  const { projectId, refreshWorkflow, aiModelName } = useWorkspace();
  const { apiRequest } = useAuth();
  const base = `/api/projects/${projectId}`;
  const [data, setData] = useState<Loaded | null>(null);
  const [schedule, setSchedule] = useState({ strategy_id: 0, connector: 'pubmed', frequency_days: 30 });
  const [feed, setFeed] = useState({ label: '', url: '' });
  const [impactAnalysis, setImpactAnalysis] = useState<number | null>(null);
  const [impactRow, setImpactRow] = useState({ label: '', values: '' });
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async (): Promise<Loaded> => {
    const [schedules, runs, alerts, candidates, feeds, releases, impact, map, strategies, analyses] = await Promise.all([
      apiRequest('GET', `${base}/surveillance/schedules`),
      apiRequest('GET', `${base}/surveillance/runs`),
      apiRequest('GET', `${base}/surveillance/alerts`),
      apiRequest('GET', `${base}/surveillance/candidates`),
      apiRequest('GET', `${base}/surveillance/feeds`),
      apiRequest('GET', `${base}/releases`),
      apiRequest('GET', `${base}/living/impact`),
      apiRequest('GET', `${base}/living/evidence-map`),
      apiRequest('GET', `${base}/search-strategies`),
      apiRequest('GET', `${base}/analyses`),
    ]);
    return { schedules, runs, alerts, candidates, feeds, releases, impact, map, strategies, analyses: analyses.analyses };
  }, [apiRequest, base]);

  useEffect(() => {
    let cancelled = false;
    load()
      .then(result => {
        if (!cancelled) setData(result);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Could not load the living review.'));
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
      await refreshWorkflow();
      if (message) setNotice(message);
    } catch (err) {
      setNotice(errorMessage(err, failure));
    }
    setBusy(false);
  };

  if (!data) return <section className="glass-panel" style={{ padding: '32px' }}>{notice ?? 'Loading the living review…'}</section>;

  const pending = data.candidates.filter(c => c.status === 'pending');
  const promoted = data.candidates.filter(c => c.status === 'promoted');
  const finalAnalyses = data.analyses.filter(a => a.final_run_id && ['pairwise', 'bayesian'].includes(a.analysis_type));

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
      <h3 style={{ marginBottom: '8px' }}>Living Review</h3>
      <p style={muted}>Saved searches rerun on a schedule. New records wait here, deduplicated and ranked, until a reviewer promotes them into a living update, which reopens the workflow so they pass through the same gates.</p>
      {notice && <p role="status" style={{ ...panel, margin: '12px 0' }}>{notice}</p>}

      <h4>Alerts</h4>
      {data.alerts.filter(a => a.status === 'open').length === 0 && <p style={muted}>No open alerts.</p>}
      {data.alerts.filter(a => a.status === 'open').map(alert => (
        <div key={alert.id} style={{ ...row, fontSize: '0.85rem', marginBottom: '4px' }}>
          <span style={chip(ALERT_COLORS[alert.kind] ?? GREY)}>{alert.kind.replace(/_/g, ' ')}</span>
          {alert.title}
          <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => act(async () => { await apiRequest('PUT', `${base}/surveillance/alerts/${alert.id}`, { status: 'acknowledged', note: '' }); }, 'Could not update the alert.')}>
            Acknowledge
          </button>
          <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => act(async () => { await apiRequest('PUT', `${base}/surveillance/alerts/${alert.id}`, { status: 'dismissed', note: '' }); }, 'Could not update the alert.')}>
            Dismiss
          </button>
        </div>
      ))}

      <h4 style={{ marginTop: '20px' }}>Surveillance schedules</h4>
      <div style={{ ...panel, ...row }}>
        <select aria-label="Search strategy" className="search-input" style={{ width: 'auto' }} value={schedule.strategy_id} onChange={e => setSchedule({ ...schedule, strategy_id: Number(e.target.value) })}>
          <option value={0}>Strategy…</option>
          {data.strategies.map(s => (
            <option key={s.id} value={s.id}>
              {s.database} (version {s.version})
            </option>
          ))}
        </select>
        <select aria-label="Connector" className="search-input" style={{ width: 'auto' }} value={schedule.connector} onChange={e => setSchedule({ ...schedule, connector: e.target.value })}>
          {Object.entries(data.schedules.connectors).map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
        <label style={row}>
          every
          <input aria-label="Frequency in days" type="number" min={1} max={365} className="search-input" style={{ width: '80px' }} value={schedule.frequency_days} onChange={e => setSchedule({ ...schedule, frequency_days: Number(e.target.value) })} />
          days
        </label>
        <button className="btn-primary" style={smallButton} disabled={busy || !schedule.strategy_id} onClick={() => act(async () => { await apiRequest('POST', `${base}/surveillance/schedules`, schedule); return 'Surveillance scheduled.'; }, 'Could not schedule surveillance.')}>
          Schedule
        </button>
        <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => act(async () => { const run = await apiRequest('POST', `${base}/surveillance/retractions/check`); return `Checked ${run.retrieved} included studies; ${run.new_candidates} retracted.`; }, 'The retraction check failed.')}>
          Check retractions
        </button>
      </div>
      {data.schedules.schedules.map(s => (
        <div key={s.id} style={{ ...row, fontSize: '0.85rem', marginTop: '4px' }}>
          <span style={chip(s.active ? GREEN : GREY)}>{s.active ? 'Active' : 'Stopped'}</span>
          {s.database} via {data.schedules.connectors[s.connector] ?? s.connector}, every {s.frequency_days} days · next {new Date(s.next_run_at).toLocaleDateString()}
          <button className="btn-glass" style={smallButton} disabled={busy || !s.active} onClick={() => act(async () => { const run: SurveillanceRunInfo = await apiRequest('POST', `${base}/surveillance/schedules/${s.id}/run`); return run.error ?? `${run.new_candidates} new records, ${run.duplicates} already known.`; }, 'The search failed.')}>
            Run now
          </button>
        </div>
      ))}
      <details style={{ marginTop: '8px' }}>
        <summary>Run history ({data.runs.length})</summary>
        {data.runs.map(r => (
          <div key={r.id} style={{ ...muted, margin: '2px 0' }}>
            {new Date(r.started_at).toLocaleString()} · {r.kind} · {r.database} · {r.status} · {r.retrieved} retrieved, {r.new_candidates} new{r.error ? ` · ${r.error}` : ''}
          </div>
        ))}
      </details>

      <h4 style={{ marginTop: '20px' }}>New records ({pending.length} pending, {promoted.length} promoted)</h4>
      <div style={row}>
        <button className="btn-glass" style={smallButton} disabled={busy || pending.length === 0} onClick={() => act(async () => { const job = await apiRequest('POST', `${base}/surveillance/candidates/ai`, { candidate_ids: pending.map(c => c.id) }); const done = await waitForJob(job, id => apiRequest('GET', `${base}/jobs/${id}`), () => undefined); return jobProblem(done, 'AI screening') ?? 'AI suggestions added; reviewers decide.'; }, 'Could not get AI suggestions.')}>
          Suggest with {aiModelName}
        </button>
        <button className="btn-primary" style={smallButton} disabled={busy || promoted.length === 0} onClick={() => { const rationale = window.prompt('Why start a living update now? The search stage and every later stage reopen.'); if (!rationale) return; act(async () => { const result = await apiRequest('POST', `${base}/living/updates`, { rationale }); return `Imported ${result.imported} records; reopened ${result.reopened.length} stages.`; }, 'Could not start the living update.'); }}>
          Start living update
        </button>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', fontSize: '0.8rem', borderCollapse: 'collapse' }}>
          <tbody>
            {data.candidates.filter(c => c.status !== 'imported').map(candidate => (
              <tr key={candidate.id} style={{ borderTop: '1px solid rgba(255,255,255,0.06)' }}>
                <td style={{ padding: '6px' }}>
                  <strong>{candidate.title}</strong>
                  <div style={muted}>
                    {candidate.authors} {candidate.year} {candidate.venue} {candidate.doi}
                  </div>
                  {candidate.ai_reasoning && <div style={muted}>AI: {candidate.ai_decision} — {candidate.ai_reasoning}</div>}
                </td>
                <td style={{ padding: '6px', whiteSpace: 'nowrap' }}>
                  {candidate.relevance != null && <span style={chip(candidate.relevance >= 0.5 ? BLUE : GREY)}>relevance {fmt(candidate.relevance)}</span>}
                  {candidate.sample_size != null && <span style={chip(GREY)}>{candidate.sample_size} participants</span>}
                </td>
                <td style={{ padding: '6px', whiteSpace: 'nowrap' }}>
                  <span style={chip(candidate.status === 'promoted' ? GREEN : candidate.status === 'dismissed' ? GREY : AMBER)}>{candidate.status}</span>{' '}
                  <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => act(async () => { await apiRequest('PUT', `${base}/surveillance/candidates/${candidate.id}`, { decision: 'promote', reason: '' }); }, 'Could not promote the record.')}>
                    Promote
                  </button>
                  <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => { const reason = window.prompt('Reason for dismissing:'); if (!reason) return; act(async () => { await apiRequest('PUT', `${base}/surveillance/candidates/${candidate.id}`, { decision: 'dismiss', reason }); }, 'Could not dismiss the record.'); }}>
                    Dismiss
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h4 style={{ marginTop: '20px' }}>Watched feeds</h4>
      <div style={row}>
        <input aria-label="Feed label" className="search-input" placeholder="Label (for example NICE guidance)" value={feed.label} onChange={e => setFeed({ ...feed, label: e.target.value })} />
        <input aria-label="Feed URL" className="search-input" style={{ flex: 1 }} placeholder="RSS or Atom URL" value={feed.url} onChange={e => setFeed({ ...feed, url: e.target.value })} />
        <button className="btn-glass" style={smallButton} disabled={busy || !feed.label || !feed.url} onClick={() => act(async () => { await apiRequest('POST', `${base}/surveillance/feeds`, feed); setFeed({ label: '', url: '' }); }, 'Could not add the feed.')}>
          Watch
        </button>
        <button className="btn-glass" style={smallButton} disabled={busy || data.feeds.length === 0} onClick={() => act(async () => { const run = await apiRequest('POST', `${base}/surveillance/feeds/check`); return `${run.new_candidates} new feed items.`; }, 'The feed check failed.')}>
          Check feeds
        </button>
      </div>
      {data.feeds.map(f => (
        <div key={f.id} style={muted}>
          {f.label} · {f.url} · {f.items_seen} items seen{f.last_error ? ` · ${f.last_error}` : ''}
        </div>
      ))}

      <h4 style={{ marginTop: '20px' }}>Impact assessment</h4>
      <p style={muted}>Rerun a final analysis provisionally with candidate study data to see how the result and suggested GRADE ratings might change. Nothing in the review changes.</p>
      <div style={{ ...panel, ...row }}>
        <select aria-label="Analysis" className="search-input" style={{ width: 'auto' }} value={impactAnalysis ?? ''} onChange={e => setImpactAnalysis(e.target.value ? Number(e.target.value) : null)}>
          <option value="">Analysis…</option>
          {finalAnalyses.map(a => (
            <option key={a.id} value={a.id}>
              {a.title}
            </option>
          ))}
        </select>
        <label style={fieldLabel}>
          Candidate study
          <input className="search-input" value={impactRow.label} onChange={e => setImpactRow({ ...impactRow, label: e.target.value })} />
        </label>
        <label style={{ ...fieldLabel, flex: 1 }}>
          Data (for example ai=20, n1i=600, ci=35, n2i=600)
          <input className="search-input" value={impactRow.values} onChange={e => setImpactRow({ ...impactRow, values: e.target.value })} />
        </label>
        <button
          className="btn-primary"
          style={smallButton}
          disabled={busy || !impactAnalysis || !impactRow.label}
          onClick={() =>
            act(async () => {
              const values = Object.fromEntries(impactRow.values.split(',').map(pair => pair.split('=').map(part => part.trim())).filter(parts => parts.length === 2 && parts[0] && !Number.isNaN(Number(parts[1]))).map(([key, value]) => [key, Number(value)]));
              await apiRequest('POST', `${base}/living/impact`, { analysis_id: impactAnalysis, rows: [{ label: impactRow.label, values }] });
              return 'Provisional analysis finished.';
            }, 'The impact assessment failed.')
          }
        >
          Assess impact
        </button>
      </div>
      {data.impact.slice(0, 5).map(i => (
        <div key={i.id} style={{ ...row, fontSize: '0.85rem', marginTop: '4px' }}>
          <span style={chip(i.status === 'succeeded' ? GREEN : RED)}>{i.status}</span>
          {i.status === 'succeeded' ? (
            <span>
              {fmt(i.baseline.estimate)} ({fmt(i.baseline.ci_lower)} to {fmt(i.baseline.ci_upper)}), k={i.baseline.k} → {fmt(i.provisional.estimate)} ({fmt(i.provisional.ci_lower)} to {fmt(i.provisional.ci_upper)}), k={i.provisional.k}
            </span>
          ) : (
            <span>{i.error}</span>
          )}
          {i.grade_changes.map((change, index) => (
            <span key={index} style={muted}>
              {'domain' in change ? `${String(change.domain).replace(/_/g, ' ')}: ${String(change.before)} → ${String(change.after)}` : `certainty ${String(change.current_certainty)} → ${String(change.projected_certainty)}`}
            </span>
          ))}
        </div>
      ))}

      <h4 style={{ marginTop: '20px' }}>Releases</h4>
      <button className="btn-primary" style={smallButton} disabled={busy} onClick={() => { const title = window.prompt('Release title:'); if (!title) return; act(async () => { const release: ReleaseInfo = await apiRequest('POST', `${base}/releases`, { title, notes: '' }); return `Released version ${release.version}.`; }, 'Could not create the release.'); }}>
        New release
      </button>
      {data.releases.map(release => (
        <details key={release.id} style={{ ...panel, marginTop: '8px' }}>
          <summary>
            Version {release.version}: {release.title} · {new Date(release.created_at).toLocaleDateString()}
            {release.deposits.filter(d => d.doi).map(d => ` · DOI ${d.doi}`)}
          </summary>
          <ul>
            {release.changelog.map(line => (
              <li key={line} style={{ fontSize: '0.85rem' }}>
                {line}
              </li>
            ))}
          </ul>
          <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => { const zenodoToken = window.prompt('Zenodo access token (used once, not stored):'); if (!zenodoToken) return; const sandbox = window.confirm('Use the Zenodo sandbox? (Cancel deposits on zenodo.org as a draft)'); act(async () => { const deposit = await apiRequest('POST', `${base}/releases/${release.id}/deposit`, { token: zenodoToken, sandbox, publish: false }); return `Draft deposit ${deposit.external_id} created on Zenodo; publish it there to mint the DOI version.`; }, 'The Zenodo deposit failed.'); }}>
            Deposit on Zenodo
          </button>
        </details>
      ))}

      <EvidenceMapView map={data.map} />
    </section>
  );
}

function EvidenceMapView({ map }: { map: EvidenceMap }) {
  const cell = (intervention: string, outcome: string) => map.bubbles.find(b => b.intervention === intervention && b.outcome === outcome);
  const maxStudies = Math.max(1, ...map.bubbles.map(b => b.studies));
  const years = Array.from(new Set([...Object.keys(map.trends.records_by_year), ...Object.keys(map.trends.included_by_year)])).sort();
  const maxRecords = Math.max(1, ...Object.values(map.trends.records_by_year));
  return (
    <section aria-label="Evidence and gap map" style={{ marginTop: '24px' }}>
      <h4>Evidence and gap map</h4>
      {map.bubbles.length === 0 ? (
        <p style={muted}>The map fills in once outcome data are extracted.</p>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ borderCollapse: 'collapse', fontSize: '0.8rem' }}>
            <thead>
              <tr>
                <th />
                {map.outcomes.map(outcome => (
                  <th key={outcome} style={{ padding: '6px' }}>
                    {outcome}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {map.interventions.map(intervention => (
                <tr key={intervention}>
                  <th style={{ padding: '6px', textAlign: 'left' }}>{intervention}</th>
                  {map.outcomes.map(outcome => {
                    const bubble = cell(intervention, outcome);
                    const size = bubble ? 14 + 30 * (bubble.studies / maxStudies) : 0;
                    return (
                      <td key={outcome} style={{ padding: '6px', textAlign: 'center' }}>
                        {bubble ? (
                          <span title={`${bubble.studies} studies${bubble.certainty ? `, ${bubble.certainty.replace('_', ' ')} certainty` : ''}`} style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: size, height: size, borderRadius: '50%', background: CERTAINTY_COLORS[bubble.certainty ?? ''] ?? BLUE, color: '#fff', fontSize: '0.7rem' }}>
                            {bubble.studies}
                          </span>
                        ) : (
                          <span style={muted}>gap</span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          <p style={muted}>Bubble size shows the number of studies; colour shows GRADE certainty (blue when not assessed).</p>
        </div>
      )}
      {map.coverage.length > 0 && (
        <details>
          <summary>Data coverage by study and field</summary>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ borderCollapse: 'collapse', fontSize: '0.7rem' }}>
              <thead>
                <tr>
                  <th />
                  {map.fields.map(f => (
                    <th key={f.id} style={{ padding: '2px 4px', writingMode: 'vertical-rl' }}>
                      {f.name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {map.coverage.map(study => (
                  <tr key={study.study_id}>
                    <th style={{ textAlign: 'left', padding: '2px 6px' }}>{study.study}</th>
                    {map.fields.map(f => (
                      <td key={f.id} title={study.fields[String(f.id)]} style={{ width: '14px', height: '14px', background: COVERAGE_COLORS[study.fields[String(f.id)]] }} />
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
      {years.length > 0 && (
        <div aria-label="Records by publication year" style={{ display: 'flex', alignItems: 'flex-end', gap: '4px', height: '100px', marginTop: '12px' }}>
          {years.map(year => (
            <div key={year} title={`${year}: ${map.trends.records_by_year[year] ?? 0} records, ${map.trends.included_by_year[year] ?? 0} included`} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', fontSize: '0.6rem' }}>
              <div style={{ width: '14px', height: `${80 * ((map.trends.records_by_year[year] ?? 0) / maxRecords)}px`, background: BLUE, position: 'relative' }}>
                <div style={{ position: 'absolute', bottom: 0, width: '14px', height: `${80 * ((map.trends.included_by_year[year] ?? 0) / maxRecords)}px`, background: GREEN }} />
              </div>
              {year.slice(2)}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
