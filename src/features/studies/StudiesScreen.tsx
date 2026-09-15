import { useCallback, useEffect, useState } from 'react';
import { errorMessage } from '../../api/client';
import type { ArmInfo, LinkCandidate, StudyInfo } from '../../api/extraction';
import { useAuth } from '../../auth/authContext';
import { GREEN, chip, muted, panel, percent, row, smallButton } from '../../components/ui';
import { useWorkspace } from '../project/workspaceContext';

type ArmDraft = { id: number | null; label: string; description: string };

export function StudiesScreen() {
  const { projectId, goTo } = useWorkspace();
  const { apiRequest } = useAuth();
  const base = `/api/projects/${projectId}`;
  const [studies, setStudies] = useState<StudyInfo[]>([]);
  const [candidates, setCandidates] = useState<LinkCandidate[]>([]);
  const [arms, setArms] = useState<Record<number, ArmDraft[]>>({});
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [studyList, candidateList] = await Promise.all([apiRequest('GET', `${base}/studies`), apiRequest('GET', `${base}/studies/link-candidates`)]);
    return { studyList: studyList as StudyInfo[], candidateList: candidateList as LinkCandidate[] };
  }, [apiRequest, base]);

  const apply = ({ studyList, candidateList }: Awaited<ReturnType<typeof load>>) => {
    setStudies(studyList);
    setCandidates(candidateList);
    setArms(Object.fromEntries(studyList.map(study => [study.id, study.arms.map((arm: ArmInfo) => ({ ...arm }))])));
  };

  useEffect(() => {
    let cancelled = false;
    load()
      .then(data => {
        if (!cancelled) apply(data);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Could not load studies.'));
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
      if (message) setNotice(message);
    } catch (err) {
      setNotice(errorMessage(err, failure));
    }
    setBusy(false);
  };

  const merge = (candidate: LinkCandidate) =>
    act(async () => {
      await apiRequest('POST', `${base}/studies/${candidate.study_id}/merge`, { other_study_id: candidate.other_study_id });
      return 'Linked the reports into one study.';
    }, 'Could not link the reports.');
  const different = (candidate: LinkCandidate) =>
    act(async () => {
      await apiRequest('POST', `${base}/study-link-decisions`, { record_id: candidate.record.id, other_record_id: candidate.other.id });
    }, 'Could not record the decision.');
  const split = (study: StudyInfo, recordId: number) =>
    act(async () => {
      await apiRequest('POST', `${base}/studies/${study.id}/split`, { record_id: recordId });
      return 'Moved the report into a study of its own.';
    }, 'Could not split the report.');
  const edit = (study: StudyInfo) => {
    const label = window.prompt('Study label (for example first author and year):', study.label);
    if (label === null) return;
    const registry = window.prompt('Trial registration numbers, separated by commas:', study.registry_ids.join(', '));
    if (registry === null) return;
    act(async () => {
      await apiRequest('PATCH', `${base}/studies/${study.id}`, { label, registry_ids: registry.split(',').map(item => item.trim()).filter(Boolean) });
    }, 'Could not update the study.');
  };
  const saveArms = (study: StudyInfo) =>
    act(async () => {
      const items = (arms[study.id] ?? []).filter(arm => arm.label.trim());
      await apiRequest('PUT', `${base}/studies/${study.id}/arms`, { arms: items.map(arm => ({ ...(arm.id ? { id: arm.id } : {}), label: arm.label, description: arm.description })) });
      return `Saved the arms of ${study.label}.`;
    }, 'Could not save the arms.');
  const armsFromAi = (study: StudyInfo) =>
    act(async () => {
      const saved: StudyInfo = await apiRequest('POST', `${base}/studies/${study.id}/arms/from-ai`);
      return saved.arms.length === study.arms.length ? 'The AI extraction named no new arms. Run AI extraction first.' : 'Added the arms the AI extraction named.';
    }, 'Could not add arms.');

  const updateArm = (studyId: number, index: number, changes: Partial<ArmDraft>) =>
    setArms(prev => ({ ...prev, [studyId]: (prev[studyId] ?? []).map((arm, i) => (i === index ? { ...arm, ...changes } : arm)) }));

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
      <h3 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>Studies and Reports</h3>
      <p style={{ color: 'var(--text-secondary)' }}>
        A study can be described by several reports, such as its protocol, main results, and follow-up. Each included report starts as its own study; link reports of the same study so its data are extracted, and counted in PRISMA, once. Shared trial registration numbers and similar titles and authors are suggested below.
      </p>
      {notice && <p role="status" style={muted}>{notice}</p>}

      {candidates.length > 0 && (
        <div style={{ ...panel, marginBottom: '20px' }}>
          <h4 style={{ marginTop: 0 }}>Reports that may describe the same study</h4>
          {candidates.map(candidate => (
            <div key={`${candidate.record.id}-${candidate.other.id}`} style={{ borderTop: '1px solid rgba(255,255,255,0.08)', padding: '8px 0' }}>
              <div>{candidate.record.title} <span style={muted}>({candidate.record.year})</span></div>
              <div>{candidate.other.title} <span style={muted}>({candidate.other.year})</span></div>
              <div style={{ ...row, marginTop: '4px' }}>
                <span style={chip(GREEN)}>{percent(candidate.score)} match</span>
                <span style={muted}>{candidate.reasons.join(' · ')}</span>
                <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => merge(candidate)}>Same study: link</button>
                <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => different(candidate)}>Different studies</button>
              </div>
            </div>
          ))}
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
        {studies.map(study => (
          <article key={study.id} style={panel} aria-label={study.label}>
            <div style={{ ...row, justifyContent: 'space-between' }}>
              <strong>{study.label}</strong>
              <div style={row}>
                {study.registry_ids.map(id => <span key={id} style={chip('#3b82f6')}>{id}</span>)}
                <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => edit(study)}>Edit</button>
              </div>
            </div>
            <ul style={{ margin: '8px 0', paddingLeft: '20px', fontSize: '0.9rem' }}>
              {study.reports.map(report => (
                <li key={report.record_id}>
                  {report.title} <span style={muted}>{report.year}{report.is_primary && ' · primary report'}</span>
                  {study.reports.length > 1 && <button className="btn-glass" style={{ ...smallButton, marginLeft: '8px' }} disabled={busy} onClick={() => split(study, report.record_id)}>Split off</button>}
                </li>
              ))}
            </ul>
            <div>
              <span style={muted}>Arms (groups compared):</span>
              {(arms[study.id] ?? []).map((arm, index) => (
                <div key={arm.id ?? `new-${index}`} style={{ ...row, marginTop: '4px' }}>
                  <input aria-label={`Arm ${index + 1} of ${study.label}`} className="search-input" style={{ maxWidth: '220px' }} value={arm.label} placeholder="Arm name" onChange={e => updateArm(study.id, index, { label: e.target.value })} />
                  <input aria-label={`Arm ${index + 1} description`} className="search-input" style={{ maxWidth: '320px' }} value={arm.description} placeholder="Description (optional)" onChange={e => updateArm(study.id, index, { description: e.target.value })} />
                  <button className="btn-glass" style={smallButton} onClick={() => setArms(prev => ({ ...prev, [study.id]: (prev[study.id] ?? []).filter((_, i) => i !== index) }))}>Remove</button>
                </div>
              ))}
              <div style={{ ...row, marginTop: '6px' }}>
                <button className="btn-glass" style={smallButton} onClick={() => setArms(prev => ({ ...prev, [study.id]: [...(prev[study.id] ?? []), { id: null, label: '', description: '' }] }))}>Add arm</button>
                <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => saveArms(study)}>Save arms</button>
                <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => armsFromAi(study)}>Add arms from AI extraction</button>
              </div>
            </div>
          </article>
        ))}
      </div>
      {studies.length === 0 && <p style={muted}>No studies yet: they appear once reports are included at full-text screening.</p>}

      <div style={{ textAlign: 'right', marginTop: '24px' }}>
        <button className="btn-primary" onClick={() => goTo('extraction-fields')}>Proceed to Extraction Form →</button>
      </div>
    </section>
  );
}
