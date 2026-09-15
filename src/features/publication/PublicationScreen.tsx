import { useCallback, useEffect, useState } from 'react';
import { errorMessage } from '../../api/client';
import { downloadFile } from '../../api/download';
import type { DepositInfo, GuidelineInfo, JournalCandidate, PackageInfo, ReviewRound, ReviewerComment } from '../../api/publishing';
import { useAuth } from '../../auth/authContext';
import { DiffView } from '../../components/DiffView';
import { AMBER, GREEN, GREY, RED, chip, fieldLabel, muted, panel, row, smallButton } from '../../components/ui';
import { WorkspaceStageGate } from '../project/WorkspaceStageGate';
import { useWorkspace } from '../project/workspaceContext';

const TARGETS: Record<string, string> = {
  zenodo: 'Zenodo (DOI, versions)',
  figshare: 'Figshare',
  osf: 'OSF',
  github: 'GitHub',
  gitlab: 'GitLab',
  dryad: 'Dryad (guided package)',
  medrxiv: 'medRxiv preprint (guided package)',
  osf_preprints: 'OSF Preprints (guided package)',
};
const STAGES = ['protocol', 'search', 'screening', 'full_text_screening', 'extraction', 'appraisal', 'synthesis', 'certainty', 'manuscript', 'submission'];
const checkColor = (status: string, severity: string) => (status === 'pass' ? GREEN : status === 'not_applicable' ? GREY : severity === 'error' ? RED : AMBER);

// Publication: journal finder, author guidelines, submission packages with readiness checks (OmniReview never
// submits), repository deposits, and responses to peer review.
export function PublicationScreen() {
  const { projectId, refreshWorkflow, aiModelName } = useWorkspace();
  const { apiRequest, token } = useAuth();
  const base = `/api/projects/${projectId}`;
  const [journals, setJournals] = useState<{ candidates: JournalCandidate[]; note: string }>({ candidates: [], note: '' });
  const [guidelines, setGuidelines] = useState<GuidelineInfo[]>([]);
  const [packages, setPackages] = useState<PackageInfo[]>([]);
  const [deposits, setDeposits] = useState<DepositInfo[]>([]);
  const [rounds, setRounds] = useState<ReviewRound[]>([]);
  const [query, setQuery] = useState('');
  const [guideline, setGuideline] = useState({ journal_name: '', url: '', text: '' });
  const [deposit, setDeposit] = useState({ target: 'zenodo', token: '', sandbox: true, publish: false, repository: '' });
  const [report, setReport] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(
    () =>
      Promise.all([
        apiRequest('GET', `${base}/journals`),
        apiRequest('GET', `${base}/journal-guidelines`),
        apiRequest('GET', `${base}/submission-packages`),
        apiRequest('GET', `${base}/deposits`),
        apiRequest('GET', `${base}/peer-review/rounds`),
      ]),
    [apiRequest, base],
  );

  const apply = ([journalData, guidelineData, packageData, depositList, roundList]: Awaited<ReturnType<typeof load>>) => {
    setJournals(journalData);
    setGuidelines(guidelineData.guidelines);
    setPackages(packageData.packages);
    setDeposits(depositList);
    setRounds(roundList);
  };

  useEffect(() => {
    let cancelled = false;
    load()
      .then(data => {
        if (!cancelled) apply(data);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Could not load publication details.'));
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
      await refreshWorkflow();
      if (message) setNotice(message);
    } catch (err) {
      setNotice(errorMessage(err, failure));
    }
    setBusy(false);
  };

  const download = (path: string, name: string) => downloadFile(token, `${base}/${path}`, name).catch(err => setNotice(errorMessage(err, 'The download failed.')));
  const latest = packages[0];

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
      <h3 style={{ marginBottom: '8px' }}>Submission & Publication</h3>
      <WorkspaceStageGate stage="submission" />
      <p style={muted}>OmniReview prepares everything a journal submission needs and checks it; the corresponding author confirms the package and submits it on the journal's own system. Nothing is submitted automatically.</p>
      {notice && <p role="status" style={{ ...panel, margin: '12px 0' }}>{notice}</p>}

      <h4>Journal finder</h4>
      <div style={row}>
        <input aria-label="Journal search" className="search-input" style={{ flex: 1 }} placeholder="Topic (defaults to the review question)" value={query} onChange={e => setQuery(e.target.value)} />
        <button className="btn-primary" style={smallButton} disabled={busy} onClick={() => act(async () => { await apiRequest('POST', `${base}/journals/find`, { query }); }, 'The journal search failed.')}>
          Find journals
        </button>
      </div>
      {journals.note && journals.candidates.length > 0 && <p style={muted}>{journals.note}</p>}
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', fontSize: '0.8rem', borderCollapse: 'collapse' }}>
          <tbody>
            {journals.candidates.slice(0, 25).map(journal => (
              <tr key={journal.id} style={{ borderTop: '1px solid rgba(255,255,255,0.06)' }}>
                <td style={{ padding: '6px' }}>
                  <strong>{journal.name}</strong>
                  <div style={muted}>{journal.publisher} {journal.issns.join(', ')}</div>
                  <div style={muted}>{journal.reasons.join('; ')}</div>
                </td>
                <td style={{ padding: '6px' }}>
                  <div style={row}>
                    {journal.is_oa && <span style={chip(GREEN)}>Open access</span>}
                    {journal.in_doaj && <span style={chip(GREEN)}>DOAJ</span>}
                    {journal.medline_indexed && <span style={chip(GREEN)}>MEDLINE</span>}
                    {journal.apc_usd != null && <span style={chip(GREY)}>APC ${journal.apc_usd}</span>}
                    {journal.h_index != null && <span style={chip(GREY)}>h-index {journal.h_index}</span>}
                    {journal.warnings.map(warning => (
                      <span key={warning} style={chip(AMBER)}>
                        {warning}
                      </span>
                    ))}
                  </div>
                </td>
                <td style={{ padding: '6px' }}>
                  <label style={row}>
                    <input type="checkbox" checked={journal.shortlisted} onChange={e => act(async () => { await apiRequest('PUT', `${base}/journals/${journal.id}`, { shortlisted: e.target.checked }); }, 'Could not update the shortlist.')} /> Shortlist
                  </label>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h4 style={{ marginTop: '24px' }}>Author guidelines</h4>
      <div style={{ ...panel, display: 'flex', flexDirection: 'column', gap: '8px' }}>
        <div style={row}>
          <input aria-label="Journal name" className="search-input" placeholder="Journal name" value={guideline.journal_name} onChange={e => setGuideline({ ...guideline, journal_name: e.target.value })} />
          <input aria-label="Guidelines URL" className="search-input" style={{ flex: 1 }} placeholder="Link to the author guidelines" value={guideline.url} onChange={e => setGuideline({ ...guideline, url: e.target.value })} />
        </div>
        <textarea aria-label="Guidelines text" className="search-input" rows={3} placeholder="Or paste the guidelines" value={guideline.text} onChange={e => setGuideline({ ...guideline, text: e.target.value })} />
        <button
          className="btn-primary"
          style={smallButton}
          disabled={busy || !guideline.journal_name || (!guideline.url && guideline.text.length < 200)}
          onClick={() =>
            act(async () => {
              const form = new FormData();
              Object.entries(guideline).forEach(([key, value]) => form.append(key, value));
              await apiRequest('POST', `${base}/journal-guidelines`, form);
              setGuideline({ journal_name: '', url: '', text: '' });
              return `${aiModelName} read the guidelines; check each requirement against its quote.`;
            }, 'Could not read the guidelines.')
          }
        >
          Read guidelines with {aiModelName}
        </button>
      </div>
      {guidelines.map(g => (
        <details key={g.id} style={{ ...panel, marginTop: '8px' }}>
          <summary>
            {g.journal_name} · {Object.keys(g.requirements).length} requirements
          </summary>
          <table style={{ width: '100%', fontSize: '0.8rem' }}>
            <tbody>
              {Object.entries(g.requirements).map(([name, requirement]) => (
                <tr key={name}>
                  <td style={{ padding: '4px' }}>{name.replace(/_/g, ' ')}</td>
                  <td style={{ padding: '4px' }}>{requirement.value}</td>
                  <td style={{ padding: '4px' }}>
                    <span style={chip(requirement.source === 'reviewer' ? GREY : requirement.grounded ? GREEN : RED)}>{requirement.source === 'reviewer' ? 'entered by reviewer' : requirement.grounded ? 'quote found' : 'quote not found; not used'}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      ))}

      <h4 style={{ marginTop: '24px' }}>Submission package</h4>
      <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => { const journal = window.prompt('Journal for this package:') ?? ''; act(async () => { await apiRequest('POST', `${base}/submission-packages`, { journal_name: journal, guideline_id: guidelines.find(g => g.journal_name === journal)?.id ?? null }); return 'Package started from the approved manuscript version.'; }, 'Could not start a package.'); }}>
        New package
      </button>
      {latest && (
        <PackageEditor
          key={`${latest.id}-${latest.status}-${latest.sha256}`}
          pkg={latest}
          guidelines={guidelines}
          busy={busy}
          aiModelName={aiModelName}
          onSave={body => act(async () => { await apiRequest('PUT', `${base}/submission-packages/${latest.id}`, body); return 'Package saved; readiness rechecked.'; }, 'Could not save the package.')}
          onCoverLetter={() => act(async () => { await apiRequest('POST', `${base}/submission-packages/${latest.id}/cover-letter`); return 'Cover letter drafted; edit it before confirming.'; }, 'Could not draft the cover letter.')}
          onBuild={() => act(async () => { const built = await apiRequest('POST', `${base}/submission-packages/${latest.id}/build`); return built.notes?.length ? built.notes.join(' ') : 'Package built.'; }, 'Could not build the package.')}
          onDownload={() => download(`submission-packages/${latest.id}/download`, `submission-package-${latest.id}.zip`)}
          onConfirm={() => act(async () => { await apiRequest('POST', `${base}/submission-packages/${latest.id}/confirm`); return 'Package confirmed. Submit it on the journal’s system.'; }, 'Could not confirm the package.')}
        />
      )}

      <h4 style={{ marginTop: '24px' }}>Repository deposits</h4>
      <div style={{ ...panel, ...row }}>
        <select aria-label="Repository" className="search-input" style={{ width: 'auto' }} value={deposit.target} onChange={e => setDeposit({ ...deposit, target: e.target.value })}>
          {Object.entries(TARGETS).map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
        {!['dryad', 'medrxiv', 'osf_preprints'].includes(deposit.target) && (
          <input aria-label="Access token" type="password" autoComplete="off" className="search-input" placeholder="Access token (not stored)" value={deposit.token} onChange={e => setDeposit({ ...deposit, token: e.target.value })} />
        )}
        {['github', 'gitlab'].includes(deposit.target) && <input aria-label="Repository name" className="search-input" placeholder="new-repo or owner/repo" value={deposit.repository} onChange={e => setDeposit({ ...deposit, repository: e.target.value })} />}
        {deposit.target === 'zenodo' && (
          <label style={row}>
            <input type="checkbox" checked={deposit.sandbox} onChange={e => setDeposit({ ...deposit, sandbox: e.target.checked })} /> Sandbox
          </label>
        )}
        {['zenodo', 'figshare'].includes(deposit.target) && (
          <label style={row}>
            <input type="checkbox" checked={deposit.publish} onChange={e => setDeposit({ ...deposit, publish: e.target.checked })} /> Publish now (permanent)
          </label>
        )}
        <button className="btn-primary" style={smallButton} disabled={busy} onClick={() => act(async () => { const created: DepositInfo = await apiRequest('POST', `${base}/deposits`, deposit); setDeposit({ ...deposit, token: '' }); return created.doi ? `Deposited: DOI ${created.doi}` : `Deposit ${created.status}.`; }, 'The deposit failed.')}>
          Deposit data, code, and materials
        </button>
      </div>
      {deposits.map(d => (
        <div key={d.id} style={{ ...row, fontSize: '0.85rem', marginTop: '4px' }}>
          <span style={chip(d.status === 'failed' ? RED : d.status === 'published' ? GREEN : GREY)}>{d.status}</span>
          {TARGETS[d.target] ?? d.target}
          {d.sandbox && d.target === 'zenodo' && <span style={muted}>sandbox</span>}
          {d.doi && <span>DOI {d.doi}</span>}
          {d.url && (
            <a href={d.url} target="_blank" rel="noreferrer">
              Open
            </a>
          )}
          {d.status === 'packaged' && (
            <button className="btn-glass" style={smallButton} onClick={() => download(`deposits/${d.id}/package`, `${d.target}-package.zip`)}>
              Download package
            </button>
          )}
          {d.error && <span style={{ color: RED }}>{d.error}</span>}
        </div>
      ))}

      <h4 style={{ marginTop: '24px' }}>Peer review</h4>
      <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => { const journal = window.prompt('Journal:') ?? ''; const decision = window.prompt('Decision (for example Minor revision):') ?? ''; act(async () => { await apiRequest('POST', `${base}/peer-review/rounds`, { journal, decision }); }, 'Could not add the review round.'); }}>
        New review round
      </button>
      {rounds.map(round => (
        <ReviewRoundPanel
          key={round.id}
          base={base}
          round={round}
          busy={busy}
          report={report}
          setReport={setReport}
          aiModelName={aiModelName}
          act={act}
          onDownload={() => download(`peer-review/rounds/${round.id}/response-letter.docx`, `response-round-${round.round_number}.docx`)}
        />
      ))}
    </section>
  );
}

type PackageProps = {
  pkg: PackageInfo;
  guidelines: GuidelineInfo[];
  busy: boolean;
  aiModelName: string;
  onSave: (body: { journal_name: string; guideline_id: number | null; cover_letter: string }) => void;
  onCoverLetter: () => void;
  onBuild: () => void;
  onDownload: () => void;
  onConfirm: () => void;
};

function PackageEditor({ pkg, guidelines, busy, aiModelName, onSave, onCoverLetter, onBuild, onDownload, onConfirm }: PackageProps) {
  const [journal, setJournal] = useState(pkg.journal_name);
  const [guidelineId, setGuidelineId] = useState<number | null>(pkg.guideline_id);
  const [letter, setLetter] = useState(pkg.cover_letter);
  return (
    <div style={{ ...panel, marginTop: '8px' }}>
      <div style={row}>
        <span style={chip(pkg.status === 'confirmed' ? GREEN : pkg.status === 'built' ? AMBER : GREY)}>{pkg.status}</span>
        <label style={fieldLabel}>
          Journal
          <input className="search-input" value={journal} onChange={e => setJournal(e.target.value)} />
        </label>
        <label style={fieldLabel}>
          Guidelines
          <select className="search-input" value={guidelineId ?? ''} onChange={e => setGuidelineId(e.target.value ? Number(e.target.value) : null)}>
            <option value="">None</option>
            {guidelines.map(g => (
              <option key={g.id} value={g.id}>
                {g.journal_name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label style={{ ...fieldLabel, marginTop: '8px' }}>
        Cover letter
        <textarea className="search-input" rows={6} value={letter} onChange={e => setLetter(e.target.value)} />
      </label>
      <div style={{ ...row, marginTop: '8px' }}>
        <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => onSave({ journal_name: journal, guideline_id: guidelineId, cover_letter: letter })}>
          Save
        </button>
        <button className="btn-glass" style={smallButton} disabled={busy} onClick={onCoverLetter}>
          Draft cover letter with {aiModelName}
        </button>
        <button className="btn-primary" style={smallButton} disabled={busy} onClick={onBuild}>
          Build package
        </button>
        {pkg.files.length > 0 && (
          <button className="btn-glass" style={smallButton} onClick={onDownload}>
            Download ({pkg.files.length} files)
          </button>
        )}
        <button className="btn-primary" style={smallButton} disabled={busy || pkg.status !== 'built' || pkg.blocking > 0} onClick={onConfirm}>
          Confirm as corresponding author
        </button>
      </div>
      <ul style={{ listStyle: 'none', padding: 0, marginTop: '12px' }}>
        {pkg.readiness.map(check => (
          <li key={check.key} style={{ ...row, fontSize: '0.85rem' }}>
            <span style={chip(checkColor(check.status, check.severity))}>{check.status === 'fail' ? (check.severity === 'error' ? 'Fails' : 'Warning') : check.status === 'pass' ? 'Passes' : 'N/A'}</span>
            {check.label}
            {check.detail && <span style={muted}>{check.detail}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}

type RoundProps = {
  base: string;
  round: ReviewRound;
  busy: boolean;
  report: string;
  setReport: (value: string) => void;
  aiModelName: string;
  act: (action: () => Promise<string | void>, failure: string) => Promise<void>;
  onDownload: () => void;
};

function ReviewRoundPanel({ base, round, busy, report, setReport, aiModelName, act, onDownload }: RoundProps) {
  const { apiRequest } = useAuth();
  const [changes, setChanges] = useState<{ key: string; title: string; diff: import('../../api/publishing').DiffOp[] }[] | null>(null);
  const [letter, setLetter] = useState(round.response_letter);
  return (
    <details style={{ ...panel, marginTop: '8px' }} open={round.status === 'open'}>
      <summary>
        Round {round.round_number} · {round.journal} · {round.decision} · {round.open_comments} open comments
      </summary>
      <textarea aria-label="Reviewer report" className="search-input" rows={4} style={{ width: '100%', marginTop: '8px' }} placeholder="Paste the decision letter and reviewer reports" value={report} onChange={e => setReport(e.target.value)} />
      <div style={row}>
        <button className="btn-glass" style={smallButton} disabled={busy || report.length < 10} onClick={() => act(async () => { const result = await apiRequest('POST', `${base}/peer-review/rounds/${round.id}/comments/import`, { text: report, use_ai: false }); setReport(''); return `Imported ${result.imported} comments.`; }, 'Could not import the comments.')}>
          Split into comments
        </button>
        <button className="btn-glass" style={smallButton} disabled={busy || report.length < 10} onClick={() => act(async () => { const result = await apiRequest('POST', `${base}/peer-review/rounds/${round.id}/comments/import`, { text: report, use_ai: true }); setReport(''); return `Imported ${result.imported} comments${result.dropped ? `; ${result.dropped} weren't verbatim and were dropped` : ''}.`; }, 'Could not import the comments.')}>
          Split with {aiModelName}
        </button>
        <button className="btn-glass" style={smallButton} onClick={() => apiRequest('GET', `${base}/peer-review/rounds/${round.id}/changes`).then(setChanges)}>
          Changes since the reviewed version
        </button>
      </div>
      {changes && changes.map(change => (
        <div key={change.key}>
          <strong>{change.title}</strong>
          <DiffView diff={change.diff} label={`Changes in ${change.title}`} />
        </div>
      ))}
      {round.comments.map(comment => (
        <CommentEditor key={comment.id} base={base} comment={comment} busy={busy} act={act} />
      ))}
      <label style={{ ...fieldLabel, marginTop: '8px' }}>
        Response letter
        <textarea className="search-input" rows={6} value={letter} onChange={e => setLetter(e.target.value)} />
      </label>
      <div style={row}>
        <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => act(async () => { const updated = await apiRequest('POST', `${base}/peer-review/rounds/${round.id}/response-letter`); setLetter(updated.response_letter); }, 'Could not draft the letter.')}>
          Draft with {aiModelName}
        </button>
        <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => act(async () => { await apiRequest('PUT', `${base}/peer-review/rounds/${round.id}`, { journal: round.journal, decision: round.decision, received_on: round.received_on, response_letter: letter, status: round.status }); return 'Letter saved.'; }, 'Could not save the letter.')}>
          Save letter
        </button>
        <button className="btn-glass" style={smallButton} onClick={onDownload}>
          Word
        </button>
      </div>
    </details>
  );
}

function CommentEditor({ base, comment, busy, act }: { base: string; comment: ReviewerComment; busy: boolean; act: RoundProps['act'] }) {
  const { apiRequest } = useAuth();
  const [response, setResponse] = useState(comment.response);
  const [status, setStatus] = useState(comment.status);
  return (
    <div style={{ borderTop: '1px solid rgba(255,255,255,0.08)', padding: '8px 0' }}>
      <div style={{ fontSize: '0.85rem' }}>
        <strong>
          {comment.reviewer} {comment.number}
        </strong>{' '}
        {comment.body}
      </div>
      <textarea aria-label={`Response to ${comment.reviewer} ${comment.number}`} className="search-input" rows={2} style={{ width: '100%' }} value={response} onChange={e => setResponse(e.target.value)} />
      <div style={row}>
        <select aria-label={`Status of ${comment.reviewer} ${comment.number}`} className="search-input" style={{ width: 'auto' }} value={status} onChange={e => setStatus(e.target.value as ReviewerComment['status'])}>
          <option value="open">Open</option>
          <option value="addressed">Addressed</option>
          <option value="rebutted">Rebutted</option>
        </select>
        <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => act(async () => { await apiRequest('PUT', `${base}/peer-review/comments/${comment.id}`, { response, status, category: comment.category, changes: comment.changes }); }, 'Could not save the response.')}>
          Save
        </button>
        <select
          aria-label={`Reopen a stage for ${comment.reviewer} ${comment.number}`}
          className="search-input"
          style={{ width: 'auto' }}
          defaultValue=""
          onChange={e => {
            const stage = e.target.value;
            e.target.value = '';
            if (!stage) return;
            const rationale = window.prompt(`Reopen ${stage.replace(/_/g, ' ')} and every later stage. Why?`);
            if (!rationale) return;
            act(async () => { await apiRequest('POST', `${base}/peer-review/comments/${comment.id}/reanalysis`, { stage, rationale }); return 'Stage reopened for re-analysis.'; }, 'Could not reopen the stage.');
          }}
        >
          <option value="">Re-analysis…</option>
          {STAGES.map(stage => (
            <option key={stage} value={stage}>
              Reopen {stage.replace(/_/g, ' ')}
            </option>
          ))}
        </select>
        {comment.reanalysis && <span style={chip(AMBER)}>Reopened {comment.reanalysis.stage.replace(/_/g, ' ')}</span>}
      </div>
    </div>
  );
}
