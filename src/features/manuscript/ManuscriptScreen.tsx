import { useCallback, useEffect, useRef, useState } from 'react';
import { errorMessage } from '../../api/client';
import { downloadFile } from '../../api/download';
import {
  CLAIM_LABELS,
  type AssetInfo,
  type EvidenceEntry,
  type ManuscriptAuthor,
  type ManuscriptChecklist,
  type ManuscriptInfo,
  type ReferenceInfo,
  type RevisionInfo,
  type SentenceCheck,
  type SuggestionInfo,
} from '../../api/publishing';
import { useAuth } from '../../auth/authContext';
import { CommentThread } from '../../components/CommentThread';
import { DiffView } from '../../components/DiffView';
import { AMBER, GREEN, GREY, RED, chip, fieldLabel, muted, panel, row, smallButton } from '../../components/ui';
import { WorkspaceStageGate } from '../project/WorkspaceStageGate';
import { useWorkspace } from '../project/workspaceContext';

const GENERATED = new Set(['methods', 'results', 'discussion', 'other_information', 'ai_use']);
const LANGUAGE_KINDS: Record<string, string> = { academic_tone: 'Academic tone', grammar: 'Grammar only', journal_style: 'Journal style', plain_language: 'Plain language', concise: 'More concise' };
const STATUS_OPTIONS = ['reported', 'partially_reported', 'not_reported', 'not_applicable'];
const REFERENCE_COLORS: Record<string, string> = { verified: GREEN, mismatch: AMBER, not_found: RED, error: RED, unchecked: GREY };
const claimColor = (check: SentenceCheck) => (check.status === 'verified' ? GREEN : check.acknowledged ? GREY : ['unsupported', 'unverifiable_numbers'].includes(check.status) ? AMBER : RED);

type Member = { user_id: number; name?: string; full_name?: string; email: string };

// The manuscript: PRISMA sections drafted from the locked evidence base, every sentence verified against recorded
// results, references, checklists, author approvals, and export.
export function ManuscriptScreen() {
  const { projectId, refreshWorkflow, aiModelName } = useWorkspace();
  const { apiRequest, token } = useAuth();
  const base = `/api/projects/${projectId}`;
  const [manuscript, setManuscript] = useState<ManuscriptInfo | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [evidence, setEvidence] = useState<EvidenceEntry[]>([]);
  const [assets, setAssets] = useState<AssetInfo[]>([]);
  const [references, setReferences] = useState<ReferenceInfo[]>([]);
  const [checklists, setChecklists] = useState<ManuscriptChecklist[]>([]);
  const [suggestions, setSuggestions] = useState<SuggestionInfo[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [sectionKey, setSectionKey] = useState('introduction');
  const [draft, setDraft] = useState('');
  const [revisions, setRevisions] = useState<RevisionInfo[] | null>(null);
  const [panelView, setPanelView] = useState<'claims' | 'evidence' | 'references' | 'checklists' | 'authors' | 'approval'>('claims');
  const [style, setStyle] = useState('vancouver');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const editor = useRef<HTMLTextAreaElement>(null);

  const load = useCallback(async () => {
    const data: ManuscriptInfo | null = await apiRequest('GET', `${base}/manuscript`);
    if (!data) return { data, extra: null };
    const [evidenceList, assetList, referenceList, checklistList, suggestionList, memberList] = await Promise.all([
      apiRequest('GET', `${base}/manuscript/evidence`),
      apiRequest('GET', `${base}/manuscript/assets`),
      apiRequest('GET', `${base}/manuscript/references`),
      apiRequest('GET', `${base}/manuscript/checklists`),
      apiRequest('GET', `${base}/manuscript/suggestions`),
      apiRequest('GET', `${base}/members`),
    ]);
    return { data, extra: { evidenceList, assetList, referenceList, checklistList, suggestionList, memberList } };
  }, [apiRequest, base]);

  const apply = ({ data, extra }: Awaited<ReturnType<typeof load>>) => {
    setManuscript(data);
    setLoaded(true);
    if (!data || !extra) return;
    setStyle(data.citation_style);
    setEvidence(extra.evidenceList);
    setAssets(extra.assetList);
    setReferences(extra.referenceList.references);
    setChecklists(extra.checklistList);
    setSuggestions(extra.suggestionList);
    setMembers(extra.memberList);
    setDraft(data.sections.find(s => s.key === sectionKey)?.content ?? '');
  };

  useEffect(() => {
    let cancelled = false;
    load()
      .then(result => {
        if (!cancelled) apply(result);
      })
      .catch(err => {
        if (!cancelled) {
          setLoaded(true);
          setNotice(errorMessage(err, 'Could not load the manuscript.'));
        }
      });
    return () => {
      cancelled = true;
    };
    // apply reads the selected section once, at load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

  const selectSection = (key: string) => {
    setSectionKey(key);
    setRevisions(null);
    setDraft(manuscript?.sections.find(s => s.key === key)?.content ?? '');
  };

  const insert = (text: string) => {
    const area = editor.current;
    if (!area) {
      setDraft(current => `${current}${text}`);
      return;
    }
    const start = area.selectionStart;
    const end = area.selectionEnd;
    setDraft(current => current.slice(0, start) + text + current.slice(end));
  };

  if (!loaded) return <section className="glass-panel" style={{ padding: '32px' }}>Loading the manuscript…</section>;

  if (!manuscript) {
    return (
      <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
        <h3>Manuscript</h3>
        <WorkspaceStageGate stage="manuscript" />
        {notice && <p role="status" style={panel}>{notice}</p>}
        <p style={muted}>The manuscript is drafted from the locked evidence base once certainty of evidence is signed off. Methods, results, other information, and the AI-use disclosure are written from the review's records, with every sentence linked to its evidence.</p>
        <div style={row}>
          <label style={fieldLabel}>
            Citation style
            <select className="search-input" value={style} onChange={e => setStyle(e.target.value)}>
              <option value="vancouver">Vancouver</option>
              <option value="ama">AMA</option>
              <option value="apa">APA 7</option>
              <option value="harvard">Harvard</option>
            </select>
          </label>
          <button className="btn-primary" disabled={busy} onClick={() => act(async () => { await apiRequest('POST', `${base}/manuscript`, { citation_style: style }); return 'Manuscript started from the locked evidence base.'; }, 'Could not start the manuscript.')}>
            Start the manuscript
          </button>
        </div>
      </section>
    );
  }

  const section = manuscript.sections.find(s => s.key === sectionKey) ?? manuscript.sections[0];
  const checks = manuscript.verification.sections.find(s => s.key === section.key)?.sentences ?? [];
  const sectionSuggestions = suggestions.filter(s => s.section_key === section.key);
  const dirty = draft !== section.content;

  const saveSection = () => act(async () => { await apiRequest('PUT', `${base}/manuscript/sections/${section.key}`, { content: draft }); return 'Section saved and verified.'; }, 'Could not save the section.');
  const suggest = (path: string, body: unknown, label: string) =>
    act(async () => {
      if (dirty && !window.confirm('Save your edits first? Unsaved edits are discarded when a suggestion is accepted.')) return;
      if (dirty) await apiRequest('PUT', `${base}/manuscript/sections/${section.key}`, { content: draft });
      const created: SuggestionInfo = await apiRequest('POST', `${base}/manuscript/sections/${section.key}/${path}`, body);
      return `${label} ready for review${created.problems.length ? ` (${created.problems.length} problems)` : ''}.`;
    }, `Could not create the ${label.toLowerCase()}.`);
  const decide = (suggestion: SuggestionInfo, accept: boolean) =>
    act(async () => {
      if (!accept) {
        await apiRequest('POST', `${base}/manuscript/suggestions/${suggestion.id}/reject`);
        return 'Suggestion rejected.';
      }
      if (suggestion.problems.length && !window.confirm(`Accept despite these problems?\n${suggestion.problems.join('\n')}`)) return;
      await apiRequest('POST', `${base}/manuscript/suggestions/${suggestion.id}/accept`, { acknowledge_problems: suggestion.problems.length > 0 });
      return 'Suggestion accepted as a new revision.';
    }, 'Could not decide the suggestion.');
  const acknowledge = (check: SentenceCheck) => {
    const note = window.prompt('Why does this sentence need no linked evidence? (at least 10 characters)');
    if (note === null) return;
    act(async () => { await apiRequest('POST', `${base}/manuscript/claims/acknowledgements`, { section_key: section.key, sentence_hash: check.hash, note }); }, 'Could not acknowledge the sentence.');
  };
  const loadRevisions = () => apiRequest('GET', `${base}/manuscript/sections/${section.key}/revisions`).then(setRevisions).catch(err => setNotice(errorMessage(err, 'Could not load revisions.')));
  const download = (path: string, name: string) => downloadFile(token, `${base}/${path}`, name).catch(err => setNotice(errorMessage(err, 'The download failed.')));

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
      <h3 style={{ marginBottom: '8px' }}>Manuscript</h3>
      <WorkspaceStageGate stage="manuscript" />
      {notice && <p role="status" style={{ ...panel, marginBottom: '12px' }}>{notice}</p>}
      <div style={{ ...row, marginBottom: '12px' }}>
        <span style={chip(manuscript.verification.unresolved ? AMBER : GREEN)}>{manuscript.verification.unresolved ? `${manuscript.verification.unresolved} sentences need attention` : 'Every sentence verified or acknowledged'}</span>
        <span style={chip(manuscript.approvals.all_approved ? GREEN : GREY)}>{manuscript.approvals.all_approved ? 'Approved by every author' : manuscript.approvals.version_number ? `Version ${manuscript.approvals.version_number}${manuscript.approvals.current ? '' : ' (changed since)'}` : 'No version yet'}</span>
        {!manuscript.tools.pandoc && <span style={chip(AMBER)}>Pandoc not installed: Word export is simplified; LaTeX and PDF unavailable</span>}
      </div>

      <ManuscriptSettings manuscript={manuscript} busy={busy} onSave={body => act(async () => { await apiRequest('PATCH', `${base}/manuscript`, body); return 'Manuscript details saved.'; }, 'Could not save the details.')} />

      <div style={{ ...row, margin: '16px 0 8px' }}>
        {manuscript.sections.map(s => {
          const unresolved = manuscript.verification.sections.find(v => v.key === s.key)?.unresolved ?? 0;
          return (
            <button key={s.key} className={s.key === section.key ? 'btn-primary' : 'btn-glass'} style={smallButton} onClick={() => selectSection(s.key)}>
              {s.title}
              {unresolved ? ` (${unresolved})` : ''}
            </button>
          );
        })}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 3fr) minmax(0, 2fr)', gap: '16px' }}>
        <div>
          <p style={muted}>{section.guidance}</p>
          <textarea ref={editor} aria-label={`${section.title} text`} className="search-input" style={{ width: '100%', minHeight: '360px', fontFamily: 'ui-monospace, monospace', fontSize: '0.85rem' }} value={draft} onChange={e => setDraft(e.target.value)} />
          <div style={{ ...row, marginTop: '8px' }}>
            <button className="btn-primary" style={smallButton} disabled={busy || !dirty} onClick={saveSection}>
              Save and verify
            </button>
            {GENERATED.has(section.key) && (
              <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => suggest('regenerate', {}, 'Regenerated text')}>
                Regenerate from records
              </button>
            )}
            <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => suggest('draft', {}, 'AI draft')}>
              Draft with {aiModelName}
            </button>
            <select aria-label="Language edit" className="search-input" style={{ width: 'auto' }} defaultValue="" onChange={e => { if (e.target.value) suggest('language', { kind: e.target.value }, 'Language edit'); e.target.value = ''; }}>
              <option value="">Language edit…</option>
              {Object.entries(LANGUAGE_KINDS).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
            <button className="btn-glass" style={smallButton} onClick={loadRevisions}>
              Tracked changes
            </button>
          </div>
          {revisions && <Revisions base={base} revisions={revisions} busy={busy} onRestore={id => act(async () => { await apiRequest('POST', `${base}/manuscript/revisions/${id}/restore`); return 'Revision restored.'; }, 'Could not restore the revision.')} />}
          {sectionSuggestions.map(suggestion => (
            <div key={suggestion.id} style={{ ...panel, marginTop: '12px' }}>
              <div style={{ ...row, justifyContent: 'space-between' }}>
                <strong>{suggestion.kind === 'draft' ? 'AI draft' : suggestion.kind === 'generated' ? 'Regenerated from records' : LANGUAGE_KINDS[suggestion.kind] ?? suggestion.kind}</strong>
                <span style={row}>
                  <span style={chip(suggestion.verification.unverified ? AMBER : GREEN)}>{suggestion.verification.unverified} sentences unverified</span>
                  <button className="btn-primary" style={smallButton} disabled={busy} onClick={() => decide(suggestion, true)}>
                    Accept
                  </button>
                  <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => decide(suggestion, false)}>
                    Reject
                  </button>
                </span>
              </div>
              {suggestion.problems.map(problem => (
                <p key={problem} style={{ color: RED, fontSize: '0.85rem' }}>
                  {problem}
                </p>
              ))}
              <DiffView diff={suggestion.diff} label="Suggested changes" />
            </div>
          ))}
        </div>

        <div>
          <div style={{ ...row, marginBottom: '8px' }}>
            {(['claims', 'evidence', 'references', 'checklists', 'authors', 'approval'] as const).map(view => (
              <button key={view} className={panelView === view ? 'btn-primary' : 'btn-glass'} style={smallButton} onClick={() => setPanelView(view)}>
                {{ claims: 'Verification', evidence: 'Evidence', references: 'References', checklists: 'Checklists', authors: 'Authors', approval: 'Versions & export' }[view]}
              </button>
            ))}
          </div>

          {panelView === 'claims' && (
            <div style={{ ...panel, maxHeight: '560px', overflowY: 'auto' }}>
              {checks.length === 0 && <p style={muted}>No sentences yet.</p>}
              {checks.map(check => (
                <div key={`${check.hash}-${check.index}`} style={{ borderBottom: '1px solid rgba(255,255,255,0.06)', padding: '6px 0' }}>
                  <div style={{ fontSize: '0.85rem' }}>{check.text}</div>
                  <div style={row}>
                    <span style={chip(claimColor(check))}>{check.acknowledged ? 'Acknowledged' : CLAIM_LABELS[check.status]}</span>
                    {check.confidence && check.status === 'verified' && <span style={muted}>confidence {check.confidence}</span>}
                    {check.issues.map(issue => (
                      <span key={issue} style={muted}>
                        {issue}
                      </span>
                    ))}
                    {!check.acknowledged && ['unsupported', 'unverifiable_numbers'].includes(check.status) && (
                      <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => acknowledge(check)}>
                        Acknowledge
                      </button>
                    )}
                  </div>
                  <CommentThread
                    projectId={projectId}
                    anchorKey={`sentence:${section.key}:${check.hash}`}
                    anchorLabel={`${section.title}: ${check.text.slice(0, 100)}`}
                  />
                </div>
              ))}
            </div>
          )}

          {panelView === 'evidence' && (
            <div style={{ ...panel, maxHeight: '560px', overflowY: 'auto' }}>
              <p style={muted}>Insert a marker at the cursor to link a sentence to its evidence, a citation, or a table or figure.</p>
              {evidence.map(item => (
                <details key={item.key}>
                  <summary>
                    <button className="btn-glass" style={smallButton} onClick={() => insert(` ${item.marker}`)}>
                      Insert
                    </button>{' '}
                    {item.label} <span style={muted}>{item.marker}</span>
                  </summary>
                  <ul style={{ ...muted, margin: '4px 0 8px 16px' }}>
                    {item.facts.slice(0, 40).map(fact => (
                      <li key={fact}>{fact}</li>
                    ))}
                  </ul>
                </details>
              ))}
              <h4>Tables and figures</h4>
              {assets.map(asset => (
                <div key={asset.marker} style={row}>
                  <button className="btn-glass" style={smallButton} onClick={() => insert(`\n\n${asset.marker}\n\n`)}>
                    Insert
                  </button>
                  {asset.title}
                </div>
              ))}
            </div>
          )}

          {panelView === 'references' && (
            <ReferencesPanel
              references={references}
              busy={busy}
              onInsert={id => insert(` [@${id}]`)}
              onAdd={body => act(async () => { await apiRequest('POST', `${base}/manuscript/references`, body); return 'Reference added and checked.'; }, 'Could not add the reference.')}
              onCheck={() => act(async () => { await apiRequest('POST', `${base}/manuscript/references/check`, {}); return 'References checked against Crossref and PubMed.'; }, 'Could not check the references.')}
              onExport={format => download(`manuscript/references/export?format=${format}`, `references.${format === 'bibtex' ? 'bib' : format === 'ris' ? 'ris' : 'json'}`)}
              onZotero={direction => {
                const apiKey = window.prompt('Zotero API key (used for this request only):');
                const libraryId = apiKey ? window.prompt('Zotero user library id (a number):') : null;
                if (!apiKey || !libraryId) return;
                act(async () => {
                  const result = await apiRequest('POST', `${base}/manuscript/references/zotero/${direction}`, { api_key: apiKey, library_type: 'user', library_id: libraryId });
                  return direction === 'import' ? `Added ${result.added} of ${result.items} Zotero items.` : `Created ${result.created} Zotero items.`;
                }, 'The Zotero request failed.');
              }}
            />
          )}

          {panelView === 'checklists' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {checklists.filter(c => c.applicable).map(checklist => (
                <ChecklistEditor key={checklist.key} checklist={checklist} busy={busy} onSave={items => act(async () => { await apiRequest('PUT', `${base}/manuscript/checklists/${checklist.key}`, { items }); return `${checklist.label} saved.`; }, 'Could not save the checklist.')} onExport={() => download(`manuscript/checklists/${checklist.key}/export?format=docx`, `${checklist.key}-checklist.docx`)} />
              ))}
            </div>
          )}

          {panelView === 'authors' && (
            <AuthorsEditor key={manuscript.authors.map(a => a.id).join('-')} authors={manuscript.authors} roles={manuscript.credit_roles} members={members} busy={busy} onSave={authors => act(async () => { await apiRequest('PUT', `${base}/manuscript/authors`, { authors }); return 'Authors saved. Changing authors withdraws approvals of the current version.'; }, 'Could not save the authors.')} />
          )}

          {panelView === 'approval' && (
            <div style={panel}>
              <button className="btn-primary" style={smallButton} disabled={busy} onClick={() => { const note = window.prompt('Note for this version (optional):') ?? ''; act(async () => { await apiRequest('POST', `${base}/manuscript/versions`, { note }); return 'Version created for author approval.'; }, 'Could not create a version.'); }}>
                Create version for approval
              </button>
              <ul style={{ listStyle: 'none', padding: 0 }}>
                {manuscript.approvals.authors.map(author => (
                  <li key={author.author_id} style={{ ...row, margin: '6px 0' }}>
                    <span style={chip(author.approved ? GREEN : GREY)}>{author.approved ? 'Approved' : 'Awaiting approval'}</span>
                    {author.name}
                    {!author.approved && manuscript.approvals.version_id && manuscript.approvals.current && (
                      <button
                        className="btn-glass"
                        style={smallButton}
                        disabled={busy}
                        onClick={() => {
                          const body = author.user_id === null ? { author_id: author.author_id, note: window.prompt(`How did ${author.name} approve (for example by email, with the date)?`) ?? '' } : {};
                          act(async () => { await apiRequest('POST', `${base}/manuscript/versions/${manuscript.approvals.version_id}/approve`, body); return 'Approval recorded.'; }, 'Could not record the approval.');
                        }}
                      >
                        {author.user_id === null ? 'Record approval' : 'Approve as this author'}
                      </button>
                    )}
                  </li>
                ))}
              </ul>
              <div style={row}>
                {(['docx', 'pdf', 'tex', 'md'] as const).map(format => (
                  <button key={format} className="btn-glass" style={smallButton} disabled={!manuscript.approvals.all_approved} onClick={() => download(`manuscript/export?format=${format}`, `manuscript.${format === 'tex' || format === 'md' ? 'zip' : format}`)}>
                    Export {format.toUpperCase()}
                  </button>
                ))}
              </div>
              {!manuscript.approvals.all_approved && <p style={muted}>Export needs every author's approval of the current version.</p>}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

function ManuscriptSettings({ manuscript, busy, onSave }: { manuscript: ManuscriptInfo; busy: boolean; onSave: (body: unknown) => void }) {
  const [title, setTitle] = useState(manuscript.title);
  const [style, setStyle] = useState(manuscript.citation_style);
  const [keywords, setKeywords] = useState(manuscript.keywords.join(', '));
  const [statements, setStatements] = useState(manuscript.statements);
  return (
    <details style={panel}>
      <summary>Title, citation style, keywords, and statements</summary>
      <div style={{ ...row, marginTop: '8px' }}>
        <label style={{ ...fieldLabel, flex: 2 }}>
          Title
          <input className="search-input" value={title} onChange={e => setTitle(e.target.value)} />
        </label>
        <label style={fieldLabel}>
          Citation style
          <input className="search-input" list="built-in-styles" value={style} onChange={e => setStyle(e.target.value)} />
          <datalist id="built-in-styles">
            {Object.entries(manuscript.built_in_styles).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </datalist>
        </label>
        <label style={{ ...fieldLabel, flex: 2 }}>
          Keywords (comma separated)
          <input className="search-input" value={keywords} onChange={e => setKeywords(e.target.value)} />
        </label>
      </div>
      <div style={{ ...row, marginTop: '8px' }}>
        {manuscript.statement_keys.map(key => (
          <label key={key} style={{ ...fieldLabel, flex: 1, minWidth: '220px' }}>
            {key.replace(/_/g, ' ')}
            <textarea className="search-input" rows={2} value={statements[key] ?? ''} onChange={e => setStatements({ ...statements, [key]: e.target.value })} />
          </label>
        ))}
      </div>
      <p style={muted}>Style ids other than Vancouver, AMA, APA, and Harvard are CSL styles (for example "nature"), applied by Pandoc on export.</p>
      <button className="btn-primary" style={smallButton} disabled={busy} onClick={() => onSave({ title, citation_style: style, keywords: keywords.split(',').map(k => k.trim()).filter(Boolean), statements })}>
        Save details
      </button>
    </details>
  );
}

function Revisions({ base, revisions, busy, onRestore }: { base: string; revisions: RevisionInfo[]; busy: boolean; onRestore: (id: number) => void }) {
  const { apiRequest } = useAuth();
  const [diff, setDiff] = useState<{ id: number; diff: SuggestionInfo['diff'] } | null>(null);
  return (
    <div style={{ ...panel, marginTop: '12px' }}>
      {revisions.map(revision => (
        <div key={revision.id} style={row}>
          <span style={muted}>
            {new Date(revision.created_at).toLocaleString()} · {revision.source.replace('_', ' ')} · {revision.created_by ?? 'unknown'} {revision.note && `· ${revision.note}`}
          </span>
          <button className="btn-glass" style={smallButton} onClick={() => apiRequest('GET', `${base}/manuscript/revisions/${revision.id}/diff`).then(data => setDiff({ id: revision.id, diff: data.diff }))}>
            Changes
          </button>
          <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => onRestore(revision.id)}>
            Restore
          </button>
        </div>
      ))}
      {diff && <DiffView diff={diff.diff} label={`Changes in revision ${diff.id}`} />}
    </div>
  );
}

type ReferencesProps = {
  references: ReferenceInfo[];
  busy: boolean;
  onInsert: (id: number) => void;
  onAdd: (body: { doi?: string; pmid?: string }) => void;
  onCheck: () => void;
  onExport: (format: 'bibtex' | 'ris' | 'csljson') => void;
  onZotero: (direction: 'import' | 'export') => void;
};

function ReferencesPanel({ references, busy, onInsert, onAdd, onCheck, onExport, onZotero }: ReferencesProps) {
  const [identifier, setIdentifier] = useState('');
  return (
    <div style={{ ...panel, maxHeight: '560px', overflowY: 'auto' }}>
      <div style={row}>
        <input aria-label="DOI or PMID" className="search-input" style={{ flex: 1 }} placeholder="DOI or PMID" value={identifier} onChange={e => setIdentifier(e.target.value)} />
        <button className="btn-glass" style={smallButton} disabled={busy || !identifier.trim()} onClick={() => { onAdd(/^\d+$/.test(identifier.trim()) ? { pmid: identifier.trim() } : { doi: identifier.trim() }); setIdentifier(''); }}>
          Add
        </button>
        <button className="btn-glass" style={smallButton} disabled={busy} onClick={onCheck}>
          Check DOIs and retractions
        </button>
      </div>
      <div style={{ ...row, margin: '8px 0' }}>
        <button className="btn-glass" style={smallButton} onClick={() => onExport('bibtex')}>BibTeX</button>
        <button className="btn-glass" style={smallButton} onClick={() => onExport('ris')}>RIS</button>
        <button className="btn-glass" style={smallButton} onClick={() => onExport('csljson')}>CSL JSON</button>
        <button className="btn-glass" style={smallButton} onClick={() => onZotero('import')}>Import from Zotero</button>
        <button className="btn-glass" style={smallButton} onClick={() => onZotero('export')}>Send to Zotero</button>
      </div>
      {references.map(ref => (
        <div key={ref.id} style={{ borderBottom: '1px solid rgba(255,255,255,0.06)', padding: '6px 0', fontSize: '0.85rem' }}>
          <div style={row}>
            <button className="btn-glass" style={smallButton} onClick={() => onInsert(ref.id)}>
              Cite
            </button>
            {ref.number != null && <strong>[{ref.number}]</strong>}
            <span style={chip(ref.retracted ? RED : REFERENCE_COLORS[ref.verification_status] ?? GREY)}>{ref.retracted ? 'Retracted' : ref.verification_status.replace('_', ' ')}</span>
            {!ref.cited && <span style={muted}>not cited</span>}
          </div>
          <div>{ref.formatted}</div>
        </div>
      ))}
    </div>
  );
}

function ChecklistEditor({ checklist, busy, onSave, onExport }: { checklist: ManuscriptChecklist; busy: boolean; onSave: (items: { item_id: string; status: string; location: string; note: string }[]) => void; onExport: () => void }) {
  const [changes, setChanges] = useState<Record<string, { status: string; location: string; note: string }>>({});
  return (
    <details style={panel}>
      <summary>
        {checklist.label} <span style={chip(checklist.complete ? GREEN : AMBER)}>{checklist.complete ? 'Complete' : `${checklist.items.filter(i => !['reported', 'not_applicable'].includes(i.status)).length} items open`}</span>
      </summary>
      {checklist.note && <p style={muted}>{checklist.note}</p>}
      <table style={{ width: '100%', fontSize: '0.8rem', borderCollapse: 'collapse' }}>
        <tbody>
          {checklist.items.map(item => {
            const change = changes[item.item_id] ?? { status: item.overridden ? item.status : '', location: item.location, note: item.note };
            return (
              <tr key={item.item_id} style={{ borderTop: '1px solid rgba(255,255,255,0.06)' }}>
                <td style={{ padding: '4px', width: '40px' }}>{item.item_id}</td>
                <td style={{ padding: '4px' }}>
                  {item.topic}
                  <div style={muted}>Automatic: {item.auto_status.replace(/_/g, ' ')}{item.auto_location && ` (${item.auto_location})`}</div>
                </td>
                <td style={{ padding: '4px' }}>
                  <select aria-label={`Status of ${checklist.label} item ${item.item_id}`} className="search-input" value={change.status} onChange={e => setChanges({ ...changes, [item.item_id]: { ...change, status: e.target.value } })}>
                    <option value="">Automatic</option>
                    {STATUS_OPTIONS.map(status => (
                      <option key={status} value={status}>
                        {status.replace(/_/g, ' ')}
                      </option>
                    ))}
                  </select>
                </td>
                <td style={{ padding: '4px' }}>
                  <input aria-label={`Location of ${checklist.label} item ${item.item_id}`} className="search-input" value={change.location} onChange={e => setChanges({ ...changes, [item.item_id]: { ...change, location: e.target.value } })} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div style={{ ...row, marginTop: '8px' }}>
        <button className="btn-primary" style={smallButton} disabled={busy || !Object.keys(changes).length} onClick={() => onSave(Object.entries(changes).map(([item_id, value]) => ({ item_id, ...value })))}>
          Save
        </button>
        <button className="btn-glass" style={smallButton} onClick={onExport}>
          Word
        </button>
      </div>
    </details>
  );
}

function AuthorsEditor({ authors, roles, members, busy, onSave }: { authors: ManuscriptAuthor[]; roles: string[]; members: Member[]; busy: boolean; onSave: (authors: ManuscriptAuthor[]) => void }) {
  const [rows, setRows] = useState<ManuscriptAuthor[]>(authors);
  const update = (index: number, change: Partial<ManuscriptAuthor>) => setRows(prev => prev.map((author, i) => (i === index ? { ...author, ...change } : change.corresponding ? { ...author, corresponding: false } : author)));
  const blank: ManuscriptAuthor = { user_id: null, name: '', email: '', affiliation: '', orcid: '', corresponding: rows.length === 0, credit_roles: [], competing_interests: '' };
  return (
    <div style={panel}>
      {rows.map((author, index) => (
        <div key={index} style={{ borderBottom: '1px solid rgba(255,255,255,0.08)', padding: '8px 0' }}>
          <div style={row}>
            <select aria-label={`Account for author ${index + 1}`} className="search-input" style={{ width: 'auto' }} value={author.user_id ?? ''} onChange={e => update(index, { user_id: e.target.value ? Number(e.target.value) : null })}>
              <option value="">No OmniReview account</option>
              {members.map(member => (
                <option key={member.user_id} value={member.user_id}>
                  {member.full_name ?? member.name ?? member.email}
                </option>
              ))}
            </select>
            <input aria-label={`Name of author ${index + 1}`} className="search-input" placeholder="Name" value={author.name} onChange={e => update(index, { name: e.target.value })} />
            <input aria-label={`Email of author ${index + 1}`} className="search-input" placeholder="Email" value={author.email} onChange={e => update(index, { email: e.target.value })} />
            <input aria-label={`Affiliation of author ${index + 1}`} className="search-input" placeholder="Affiliation" value={author.affiliation} onChange={e => update(index, { affiliation: e.target.value })} />
            <input aria-label={`ORCID of author ${index + 1}`} className="search-input" placeholder="ORCID" value={author.orcid} onChange={e => update(index, { orcid: e.target.value })} />
            <label style={row}>
              <input type="radio" checked={author.corresponding} onChange={() => update(index, { corresponding: true })} /> Corresponding
            </label>
            <button className="btn-glass" style={smallButton} onClick={() => setRows(prev => prev.filter((_, i) => i !== index))}>
              Remove
            </button>
          </div>
          <div style={{ ...row, fontSize: '0.75rem' }}>
            {roles.map(role => (
              <label key={role} style={row}>
                <input type="checkbox" checked={author.credit_roles.includes(role)} onChange={e => update(index, { credit_roles: e.target.checked ? [...author.credit_roles, role] : author.credit_roles.filter(r => r !== role) })} /> {role}
              </label>
            ))}
          </div>
        </div>
      ))}
      <div style={{ ...row, marginTop: '8px' }}>
        <button className="btn-glass" style={smallButton} onClick={() => setRows([...rows, blank])}>
          Add author
        </button>
        <button className="btn-primary" style={smallButton} disabled={busy || rows.some(r => !r.name.trim())} onClick={() => onSave(rows.map(({ id: _id, ...rest }) => rest))}>
          Save authors
        </button>
      </div>
    </div>
  );
}
