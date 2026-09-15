import { useEffect, useState } from 'react';
import { errorMessage } from '../../api/client';
import type { DocumentDetail, FullTextOverview } from '../../api/documents';
import type { ExclusionReason } from '../../api/screening';
import { useAuth } from '../../auth/authContext';
import { Tabs } from '../../components/Tabs';
import { muted } from '../../components/ui';
import { PassageViewer } from '../fulltext/PassageViewer';
import { WorkspaceStageGate } from '../project/WorkspaceStageGate';
import { useWorkspace } from '../project/workspaceContext';
import { ConflictsPanel } from './ConflictsPanel';
import { ScreeningQualityPanel } from './ScreeningQualityPanel';
import { ScreeningQueue, type DocumentRef } from './ScreeningQueue';

type View = 'queue' | 'conflicts' | 'quality';

export function FullTextScreeningScreen() {
  const { projectId, goTo, prisma } = useWorkspace();
  const { apiRequest } = useAuth();
  const base = `/api/projects/${projectId}`;
  const [view, setView] = useState<View>('queue');
  const [reasons, setReasons] = useState<ExclusionReason[]>([]);
  const [documents, setDocuments] = useState<Record<number, DocumentRef>>({});
  const [viewer, setViewer] = useState<{ doc: DocumentDetail; highlight: number[] } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([apiRequest('GET', `${base}/review-settings`), apiRequest('GET', `${base}/full-texts`)])
      .then(([settings, overview]: [{ exclusion_reasons: ExclusionReason[] }, FullTextOverview]) => {
        if (cancelled) return;
        setReasons(settings.exclusion_reasons);
        const refs: Record<number, DocumentRef> = {};
        for (const row of overview.records) {
          const doc = row.documents.find(item => item.role === 'full_text' && item.parse_status === 'parsed');
          if (doc) refs[row.record.id] = { documentId: doc.id, fileName: doc.file_name };
        }
        setDocuments(refs);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Could not load full texts.'));
      });
    return () => {
      cancelled = true;
    };
  }, [apiRequest, base]);

  const openPassages = async (documentId: number, spanIds: number[]) => {
    try {
      setViewer({ doc: await apiRequest('GET', `${base}/documents/${documentId}`), highlight: spanIds });
    } catch (err) {
      setNotice(errorMessage(err, 'Could not open the full text.'));
    }
  };

  const excluded = prisma?.reports_excluded ?? {};

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
      <h3 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>Full-Text Screening</h3>
      <WorkspaceStageGate stage="full_text_screening" />
      <p style={{ color: 'var(--text-secondary)' }}>
        Assess each report included at title and abstract against the criteria using its full text. Exclusions need a reason for the PRISMA flow diagram; reports that couldn't be obtained are recorded as not retrieved.
      </p>
      {prisma && (
        <p style={muted}>
          Sought {prisma.reports_sought_for_retrieval ?? 0} · assessed {prisma.reports_assessed ?? 0} · not retrieved {prisma.reports_not_retrieved ?? 0} · included {prisma.reports_of_included_studies ?? 0}
          {Object.keys(excluded).length > 0 && ` · excluded: ${Object.entries(excluded).map(([reason, count]) => `${reason} (${count})`).join(', ')}`}
        </p>
      )}
      {notice && <p role="status" style={muted}>{notice}</p>}
      <Tabs
        tabs={[
          { key: 'queue', label: 'My queue' },
          { key: 'conflicts', label: 'Disagreements' },
          { key: 'quality', label: 'Agreement and AI accuracy' },
        ]}
        active={view}
        onChange={setView}
      />
      {view === 'queue' && <ScreeningQueue stage="full_text" reasons={reasons} documents={documents} onOpenPassages={openPassages} />}
      {view === 'conflicts' && <ConflictsPanel stage="full_text" reasons={reasons} />}
      {view === 'quality' && <ScreeningQualityPanel stage="full_text" />}
      {viewer && <PassageViewer doc={viewer.doc} highlight={viewer.highlight} onClose={() => setViewer(null)} />}
      <div style={{ marginTop: '24px' }}>
        <button className="btn-primary" onClick={() => goTo('studies')}>Proceed to Studies →</button>
      </div>
    </section>
  );
}
