import { useEffect, useState } from 'react';
import { errorMessage } from '../../api/client';
import { downloadFile, fetchBlob, saveBlob } from '../../api/download';
import { useAuth } from '../../auth/authContext';
import { muted, row, smallButton } from '../../components/ui';
import { useWorkspace } from '../project/workspaceContext';

export function PrismaScreen() {
  const { prisma, goTo, projectId } = useWorkspace();
  const { token } = useAuth();
  const base = `/api/projects/${projectId}`;
  const [svgUrl, setSvgUrl] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let url: string | null = null;
    let cancelled = false;
    fetchBlob(token, `${base}/prisma/flow.svg`)
      .then(blob => {
        if (cancelled || typeof URL.createObjectURL !== 'function') return;
        url = URL.createObjectURL(blob);
        setSvgUrl(url);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Could not draw the flow diagram.'));
      });
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [token, base, prisma]);

  const downloadPng = () => {
    if (!svgUrl) return;
    const image = new Image();
    image.onload = () => {
      const canvas = document.createElement('canvas');
      canvas.width = 1440 * 2;
      canvas.height = 920 * 2;
      const context = canvas.getContext('2d');
      if (!context) return;
      context.scale(2, 2);
      context.drawImage(image, 0, 0);
      canvas.toBlob(blob => blob && saveBlob(blob, 'prisma-2020-flow.png'), 'image/png');
    };
    image.src = svgUrl;
  };

  const download = (path: string, name: string) => downloadFile(token, `${base}/${path}`, name).catch(err => setNotice(errorMessage(err, 'The download failed.')));
  const excluded = Object.entries(prisma?.reports_excluded ?? {});

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
      <h3 style={{ marginBottom: '24px', color: 'var(--text-primary)' }}>PRISMA Flow Diagram</h3>
      <p style={{ color: 'var(--text-secondary)', marginTop: 0 }}>
        The PRISMA 2020 flow diagram, computed from the project's searches, other methods, deduplication, screening decisions, full texts, and studies. Download it as SVG or PNG, or as CSV for the PRISMA2020 flow diagram tool. For PDF, print the SVG.
      </p>
      {notice && <p role="status" style={muted}>{notice}</p>}
      <div style={{ ...row, marginBottom: '16px' }}>
        <button className="btn-glass" style={smallButton} onClick={() => download('prisma/flow.svg', 'prisma-2020-flow.svg')}>Download SVG</button>
        <button className="btn-glass" style={smallButton} disabled={!svgUrl} onClick={downloadPng}>Download PNG</button>
        <button className="btn-glass" style={smallButton} onClick={() => download('prisma/flow.csv', 'prisma-2020-flow.csv')}>Download CSV</button>
      </div>
      {svgUrl && (
        <div style={{ overflowX: 'auto', background: '#fff', borderRadius: '12px', padding: '8px' }}>
          <img src={svgUrl} alt="PRISMA 2020 flow diagram" style={{ maxWidth: 'none', width: '1100px' }} />
        </div>
      )}
      <div style={{ padding: '24px', background: 'rgba(0,0,0,0.3)', borderRadius: '12px', marginTop: '16px' }}>
        <ul style={{ lineHeight: 1.8, margin: 0 }}>
          <li>Records from databases: {prisma?.identified_from_databases ?? 0}</li>
          <li>Records from trial registers: {prisma?.identified_from_registers ?? 0}</li>
          <li>Records from other methods: {prisma?.identified_from_other_methods ?? 0}
            {prisma?.other_methods && (
              <span style={{ color: 'var(--text-secondary)' }}> (citation searching {prisma.other_methods.citation_searching}; grey literature and websites {prisma.other_methods.grey_literature_and_websites})</span>
            )}
          </li>
          {(prisma?.identified_from_uploads ?? 0) > 0 && <li>Records from uploads with no source type: {prisma?.identified_from_uploads}</li>}
          {Object.entries(prisma?.by_source ?? {}).map(([source, count]) => (
            <li key={source} style={{ color: 'var(--text-secondary)', marginLeft: '16px' }}>{source}: {count}</li>
          ))}
          <li>Duplicates removed: {prisma?.duplicates_removed ?? 0}</li>
          <li>Records screened: {prisma?.screened ?? 0}</li>
          <li>Excluded by a reviewer at abstract screening: {prisma?.excluded ?? 0}</li>
          {(prisma?.excluded_by_automation ?? 0) > 0 && <li>Not screened after an accepted stopping rule: {prisma?.excluded_by_automation}</li>}
          <li>Awaiting a reviewer decision: {prisma?.awaiting_decision ?? 0}</li>
          <li>Reports sought for retrieval: {prisma?.reports_sought_for_retrieval ?? 0}</li>
          <li>Reports not retrieved: {prisma?.reports_not_retrieved ?? 0}</li>
          <li>Reports assessed for eligibility: {prisma?.reports_assessed ?? 0}</li>
          {excluded.map(([reason, count]) => <li key={reason} style={{ color: 'var(--text-secondary)', marginLeft: '16px' }}>Excluded, {reason}: {count}</li>)}
          <li>Studies included: {prisma?.studies_included ?? 0} (reports: {prisma?.reports_of_included_studies ?? 0})</li>
        </ul>
      </div>
      <div style={{ textAlign: 'center', marginTop: '32px' }}>
        <button className="btn-primary" onClick={() => goTo('risk-of-bias')}>Proceed to Quality Assessment →</button>
      </div>
    </section>
  );
}
