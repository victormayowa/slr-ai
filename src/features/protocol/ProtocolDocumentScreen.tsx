import { useEffect, useState } from 'react';
import { errorMessage } from '../../api/client';
import type { ProtocolSectionInfo, ProtocolSuggestion, SectionDraft } from '../../api/protocol';
import { useAuth } from '../../auth/authContext';
import { WorkspaceStageGate } from '../project/WorkspaceStageGate';
import { useWorkspace } from '../project/workspaceContext';
import { ProtocolChecksPanel } from './ProtocolChecksPanel';

// A draft is worth showing when nothing is saved yet or it was made after the last save.
const showDraft = (section: ProtocolSectionInfo) =>
  section.draft !== null && (!section.content || !section.updated_at || section.draft.created_at > section.updated_at);

export function ProtocolDocumentScreen() {
  const { projectId, refreshWorkflow, aiModelName } = useWorkspace();
  const { apiRequest } = useAuth();
  const [sections, setSections] = useState<ProtocolSectionInfo[] | null>(null);
  const [texts, setTexts] = useState<Record<string, string>>({});
  const [acceptedDrafts, setAcceptedDrafts] = useState<Record<string, number>>({});
  const [dismissed, setDismissed] = useState<Set<number>>(new Set());
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [notices, setNotices] = useState<Record<string, string>>({});
  const [checksVersion, setChecksVersion] = useState(0);
  const path = `/api/projects/${projectId}`;

  useEffect(() => {
    let cancelled = false;
    apiRequest('GET', `/api/projects/${projectId}/protocol-sections`)
      .then((data: ProtocolSectionInfo[]) => {
        if (cancelled) return;
        setSections(data);
        setTexts(Object.fromEntries(data.map(section => [section.key, section.content])));
      })
      .catch(err => {
        if (!cancelled) setNotices({ page: errorMessage(err, 'Could not load the protocol document.') });
      });
    return () => {
      cancelled = true;
    };
  }, [apiRequest, projectId]);

  if (!sections) return <p style={{ color: 'var(--text-secondary)' }}>{notices.page ?? 'Loading the protocol document…'}</p>;

  const replaceSection = (updated: ProtocolSectionInfo) => setSections(prev => prev?.map(s => (s.key === updated.key ? updated : s)) ?? prev);
  const note = (key: string, text: string) => setNotices(prev => ({ ...prev, [key]: text }));

  const save = async (section: ProtocolSectionInfo) => {
    setBusyKey(section.key);
    try {
      const saved: ProtocolSectionInfo = await apiRequest('PUT', `${path}/protocol-sections/${section.key}`, {
        content: texts[section.key] ?? '',
        based_on_draft_id: acceptedDrafts[section.key] ?? null,
      });
      replaceSection(saved);
      setTexts(prev => ({ ...prev, [section.key]: saved.content }));
      setAcceptedDrafts(prev => {
        const next = { ...prev };
        delete next[section.key];
        return next;
      });
      setChecksVersion(v => v + 1);
      await refreshWorkflow();
      note(section.key, 'Saved.');
    } catch (err) {
      note(section.key, errorMessage(err, 'Could not save this section.'));
    }
    setBusyKey(null);
  };

  const draft = async (section: ProtocolSectionInfo) => {
    setBusyKey(section.key);
    note(section.key, '');
    try {
      const suggestion: ProtocolSuggestion<SectionDraft> = await apiRequest('POST', `${path}/protocol-sections/${section.key}/ai-draft`);
      replaceSection({ ...section, draft: suggestion, updated_at: section.content ? section.updated_at : null });
    } catch (err) {
      note(section.key, errorMessage(err, 'Could not draft this section.'));
    }
    setBusyKey(null);
  };

  const acceptDraft = (section: ProtocolSectionInfo, suggestion: ProtocolSuggestion<SectionDraft>) => {
    setTexts(prev => ({ ...prev, [section.key]: suggestion.content.content }));
    setAcceptedDrafts(prev => ({ ...prev, [section.key]: suggestion.id }));
    setDismissed(prev => new Set(prev).add(suggestion.id));
    note(section.key, 'Draft copied into the editor. Complete any placeholders, then save.');
  };

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px', maxWidth: '960px', margin: '0 auto' }}>
      <h3 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>Protocol Document</h3>
      <WorkspaceStageGate stage="protocol" />
      <p style={{ color: 'var(--text-secondary)' }}>
        The sections of a PRISMA-P protocol. AI drafts use only what the project already records and mark missing
        information as [TO COMPLETE: …]; nothing is saved until you save it.
      </p>
      <ProtocolChecksPanel version={checksVersion} />

      <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
        {sections.map(section => {
          const text = texts[section.key] ?? '';
          const busy = busyKey === section.key;
          const visibleDraft = section.draft && showDraft(section) && !dismissed.has(section.draft.id) ? section.draft : null;
          return (
            <article key={section.key} aria-label={section.label} style={{ background: 'rgba(0,0,0,0.25)', borderRadius: '12px', padding: '16px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap' }}>
                <h4 style={{ margin: 0 }}>
                  {section.label}
                  {section.required && <span style={{ color: '#f59e0b', fontSize: '0.75rem', marginLeft: '8px' }}>Required</span>}
                </h4>
                <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>PRISMA-P item {section.prisma_p_item}</span>
              </div>
              <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', margin: '6px 0 10px' }}>{section.guidance}</p>
              <textarea
                aria-label={`${section.label} text`}
                className="search-input"
                style={{ minHeight: '100px', resize: 'vertical', width: '100%' }}
                value={text}
                onChange={e => setTexts(prev => ({ ...prev, [section.key]: e.target.value }))}
              />
              {visibleDraft && (
                <div role="region" aria-label={`AI draft of ${section.label}`} style={{ marginTop: '10px', background: 'rgba(59,130,246,0.08)', border: '1px solid rgba(59,130,246,0.3)', borderRadius: '8px', padding: '12px' }}>
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>AI draft by {visibleDraft.provider} {visibleDraft.model}</div>
                  <p style={{ whiteSpace: 'pre-wrap', margin: '8px 0' }}>{visibleDraft.content.content}</p>
                  {visibleDraft.content.missing_information.length > 0 && (
                    <div style={{ fontSize: '0.85rem', color: '#f59e0b' }}>Missing information: {visibleDraft.content.missing_information.join('; ')}</div>
                  )}
                  <div style={{ display: 'flex', gap: '8px', marginTop: '8px' }}>
                    <button className="btn-glass" onClick={() => acceptDraft(section, visibleDraft)} style={{ padding: '6px 12px' }}>Use this draft</button>
                    <button className="btn-glass" onClick={() => setDismissed(prev => new Set(prev).add(visibleDraft.id))} style={{ padding: '6px 12px' }}>Dismiss</button>
                  </div>
                </div>
              )}
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap', marginTop: '10px' }}>
                <button className="btn-primary" onClick={() => save(section)} disabled={busy || (text === section.content && !acceptedDrafts[section.key])} style={{ padding: '8px 16px' }}>Save</button>
                <button className="btn-glass" onClick={() => draft(section)} disabled={busy} style={{ padding: '8px 16px' }}>
                  {busy ? 'Working…' : `Draft with ${aiModelName}`}
                </button>
                {section.ai_assisted && <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Includes accepted AI draft text</span>}
                {section.updated_by && <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Last saved by {section.updated_by}</span>}
                {notices[section.key] && <span role="status" style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>{notices[section.key]}</span>}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
