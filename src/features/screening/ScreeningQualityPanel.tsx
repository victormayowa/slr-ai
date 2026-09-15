import { useCallback, useEffect, useState } from 'react';
import { errorMessage } from '../../api/client';
import type { Agreement, AgreementStats, AiPerformance, QaSample, ScreeningStage, StoppingEvaluation } from '../../api/screening';
import { useAuth } from '../../auth/authContext';
import { AMBER, GREEN, RED, chip, fieldLabel, fmt, muted, panel, percent, row } from '../../components/ui';
import { useWorkspace } from '../project/workspaceContext';

function AgreementLine({ stats }: { stats: AgreementStats }) {
  if (stats.n === 0) return <span style={muted}>No records decided by two reviewers yet.</span>;
  const ci = stats.kappa_ci ? ` (95% CI ${fmt(stats.kappa_ci[0])} to ${fmt(stats.kappa_ci[1])})` : '';
  return (
    <span>
      κ = {fmt(stats.kappa)}{ci} · PABAK {fmt(stats.pabak)} · observed agreement {percent(stats.observed_agreement)} · {stats.n} records
    </span>
  );
}

// Agreement between reviewers, AI accuracy, and (at title and abstract) the stopping rule and quality-assurance sample.
export function ScreeningQualityPanel({ stage }: { stage: ScreeningStage }) {
  const { projectId, refreshRecords } = useWorkspace();
  const { apiRequest } = useAuth();
  const base = `/api/projects/${projectId}`;
  const [agreement, setAgreement] = useState<Agreement | null>(null);
  const [performance, setPerformance] = useState<AiPerformance | null>(null);
  const [stopping, setStopping] = useState<StoppingEvaluation | null>(null);
  const [qa, setQa] = useState<QaSample | null>(null);
  const [sampleSize, setSampleSize] = useState(100);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const titleAbstract = stage === 'title_abstract';

  const load = useCallback(async () => {
    const [agreementData, performanceData, stoppingData, qaData] = await Promise.all([
      apiRequest('GET', `${base}/screening/agreement?stage=${stage}`),
      apiRequest('GET', `${base}/screening/ai-performance?stage=${stage}`),
      titleAbstract ? apiRequest('GET', `${base}/screening/stopping`) : Promise.resolve(null),
      titleAbstract ? apiRequest('GET', `${base}/screening/qa`) : Promise.resolve(null),
    ]);
    return { agreementData, performanceData, stoppingData, qaData };
  }, [apiRequest, base, stage, titleAbstract]);

  const apply = (data: Awaited<ReturnType<typeof load>>) => {
    setAgreement(data.agreementData);
    setPerformance(data.performanceData);
    setStopping(data.stoppingData);
    setQa(data.qaData);
  };

  useEffect(() => {
    let cancelled = false;
    load()
      .then(data => {
        if (!cancelled) apply(data);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Could not load screening quality.'));
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
      apply(await load());
      await refreshRecords();
      if (message) setNotice(message);
    } catch (err) {
      setNotice(errorMessage(err, failure));
    }
    setBusy(false);
  };

  const evaluate = () => act(async () => void (await apiRequest('POST', `${base}/screening/stopping`, {})), 'Could not run the stopping test.');
  const accept = (evaluation: StoppingEvaluation) => {
    const rationale = window.prompt('Why is it appropriate to stop screening now? (at least 10 characters)');
    if (rationale === null) return;
    act(async () => {
      await apiRequest('POST', `${base}/screening/stopping/${evaluation.id}/accept`, { rationale });
      return 'Stopping accepted. Records nobody screened will be reported as excluded by automation.';
    }, 'Could not accept the stopping rule.');
  };
  const drawSample = () =>
    act(async () => {
      const sample: QaSample = await apiRequest('POST', `${base}/screening/qa-samples`, { size: sampleSize });
      return `Drew ${sample.size} of ${sample.pool_size} unscreened records. Screen them from the queue to estimate recall.`;
    }, 'Could not draw a sample.');

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      {notice && <p role="status" style={muted}>{notice}</p>}
      <section style={panel}>
        <h4 style={{ marginTop: 0 }}>Agreement between reviewers</h4>
        {agreement ? (
          <>
            <p style={{ margin: '4px 0' }}>Overall: <AgreementLine stats={agreement.overall} /></p>
            {agreement.pairs.map(pair => (
              <p key={pair.reviewers.join('|')} style={{ ...muted, margin: '4px 0' }}>{pair.reviewers.join(' and ')}: <AgreementLine stats={pair} /></p>
            ))}
          </>
        ) : (
          <p style={muted}>Loading…</p>
        )}
      </section>

      <section style={panel}>
        <h4 style={{ marginTop: 0 }}>AI suggestions compared with reviewers' decisions</h4>
        {performance && performance.compared > 0 ? (
          <>
            <p style={{ margin: '4px 0' }}>
              Sensitivity {percent(performance.sensitivity)}
              {performance.sensitivity_ci && ` (95% CI ${percent(performance.sensitivity_ci[0])} to ${percent(performance.sensitivity_ci[1])})`} · specificity {percent(performance.specificity)} · {performance.compared} records compared
            </p>
            <p style={muted}>Included records the AI suggested excluding: {performance.false_negatives}. "Maybe" counts as include.</p>
            {performance.below_target && <p style={{ color: RED }}>Sensitivity is below the recall target of {percent(performance.recall_target)}: don't rely on AI suggestions to exclude records.</p>}
          </>
        ) : (
          <p style={muted}>No records have both an AI suggestion and a reviewer's final decision yet.</p>
        )}
      </section>

      {titleAbstract && (
        <section style={panel}>
          <h4 style={{ marginTop: 0 }}>Stopping rule</h4>
          <p style={muted}>
            With prioritized screening, the hypergeometric test (Callaghan & Müller-Hansen 2020) shows when recall has reached the target with the chosen confidence, so the remaining records can be left unscreened. Accepting it is a signed decision in the audit trail.
          </p>
          <button className="btn-glass" style={{ padding: '8px 16px' }} disabled={busy} onClick={evaluate}>Run the stopping test</button>
          {stopping && (
            <div style={{ marginTop: '12px' }}>
              <span style={chip(stopping.result.can_stop ? GREEN : AMBER)}>{stopping.result.can_stop ? 'Screening can stop' : 'Keep screening'}</span>{' '}
              <span>{stopping.result.explanation}</span>
              <p style={muted}>
                {stopping.result.screened} screened · {stopping.result.relevant_found} relevant · {stopping.result.unscreened} unscreened · {stopping.result.consecutive_irrelevant} irrelevant in a row
                {stopping.result.heuristic_met && ` (at least ${stopping.result.heuristic_threshold}: the heuristic rule is met, but it gives no statistical guarantee)`}
              </p>
              {stopping.accepted_at ? (
                <p style={{ color: stopping.still_applies ? GREEN : AMBER }}>
                  Accepted by {stopping.accepted_by}: {stopping.acceptance_rationale}
                  {!stopping.still_applies && ' Records were added since, so it no longer applies.'}
                </p>
              ) : (
                stopping.result.can_stop && stopping.still_applies && <button className="btn-primary" style={{ padding: '8px 16px' }} disabled={busy} onClick={() => accept(stopping)}>Accept and stop screening</button>
              )}
            </div>
          )}
        </section>
      )}

      {titleAbstract && (
        <section style={panel}>
          <h4 style={{ marginTop: 0 }}>Quality-assurance sample</h4>
          <p style={muted}>A random sample of records nobody screened, screened by reviewers to estimate how many relevant records remain.</p>
          <div style={row}>
            <label style={fieldLabel}>
              Sample size
              <input type="number" min={1} max={5000} className="search-input" style={{ width: '120px' }} value={sampleSize} onChange={e => setSampleSize(Number(e.target.value))} />
            </label>
            <button className="btn-glass" style={{ padding: '8px 16px', alignSelf: 'flex-end' }} disabled={busy} onClick={drawSample}>Draw a sample</button>
          </div>
          {qa && (
            <p style={{ marginBottom: 0 }}>
              Sample of {qa.size} from {qa.pool_size}: {qa.screened} screened, {qa.includes_found_in_sample} relevant found.
              {qa.recall != null && ` Estimated recall ${percent(qa.recall)} (${percent(qa.recall_low)} to ${percent(qa.recall_high)}).`}
              {qa.below_target && <span style={{ color: RED }}> Below the recall target of {percent(qa.recall_target)}: keep screening.</span>}
            </p>
          )}
        </section>
      )}
    </div>
  );
}
