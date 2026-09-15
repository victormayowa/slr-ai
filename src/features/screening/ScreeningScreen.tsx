import { useEffect, useState } from 'react';
import type { ExclusionReason } from '../../api/screening';
import { useAuth } from '../../auth/authContext';
import { Tabs } from '../../components/Tabs';
import { GREEN, RED, chip, muted } from '../../components/ui';
import { WorkspaceStageGate } from '../project/WorkspaceStageGate';
import { useWorkspace } from '../project/workspaceContext';
import { ConflictsPanel } from './ConflictsPanel';
import { ReviewSettingsPanel } from './ReviewSettingsPanel';
import { ScreeningQualityPanel } from './ScreeningQualityPanel';
import { ScreeningQueue } from './ScreeningQueue';

type View = 'queue' | 'screened' | 'conflicts' | 'quality' | 'settings';

function ScreenedRecords() {
  const { literatureResults } = useWorkspace();
  const screened = literatureResults.filter(paper => paper.user_decision);
  return (
    <div>
      <p style={muted}>Records with a final title and abstract decision. Change a decision from the record's own decision in the queue by marking it undecided first.</p>
      <ul style={{ listStyle: 'none', padding: 0, display: 'flex', flexDirection: 'column', gap: '6px' }}>
        {screened.map(paper => (
          <li key={paper.id} style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
            <span style={chip(paper.user_decision === 'Include' ? GREEN : paper.user_decision === 'Exclude' ? RED : '#f59e0b')}>{paper.user_decision}</span>
            {paper.title}
          </li>
        ))}
      </ul>
      {screened.length === 0 && <p style={muted}>No final decisions yet.</p>}
    </div>
  );
}

export function ScreeningScreen() {
  const { projectId, goTo } = useWorkspace();
  const { apiRequest } = useAuth();
  const [view, setView] = useState<View>('queue');
  const [reasons, setReasons] = useState<ExclusionReason[]>([]);

  useEffect(() => {
    let cancelled = false;
    apiRequest('GET', `/api/projects/${projectId}/review-settings`)
      .then(settings => {
        if (!cancelled) setReasons(settings.exclusion_reasons ?? []);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [apiRequest, projectId]);

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
      <h3 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>Abstract Screening</h3>
      <WorkspaceStageGate stage="screening" />
      <p style={{ color: 'var(--text-secondary)' }}>
        Decide on each record's title and abstract. AI suggestions judge every criterion with quotes checked against the record, but only reviewers' decisions count.
      </p>
      <Tabs
        tabs={[
          { key: 'queue', label: 'My queue' },
          { key: 'screened', label: 'Screened records' },
          { key: 'conflicts', label: 'Disagreements' },
          { key: 'quality', label: 'Quality and stopping' },
          { key: 'settings', label: 'Settings' },
        ]}
        active={view}
        onChange={setView}
      />
      {view === 'queue' && <ScreeningQueue stage="title_abstract" reasons={reasons} />}
      {view === 'screened' && <ScreenedRecords />}
      {view === 'conflicts' && <ConflictsPanel stage="title_abstract" reasons={reasons} />}
      {view === 'quality' && <ScreeningQualityPanel stage="title_abstract" />}
      {view === 'settings' && <ReviewSettingsPanel onSaved={settings => setReasons(settings.exclusion_reasons)} />}
      <div style={{ marginTop: '24px' }}>
        <button className="btn-primary" onClick={() => goTo('full-texts')}>Proceed to Full Texts →</button>
      </div>
    </section>
  );
}
