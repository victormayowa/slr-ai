import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import type { LegalDocument } from '../api/account';
import { errorMessage, requestJson } from '../api/client';
import { LegalLinks } from '../components/LegalLinks';
import { Markdown } from '../components/Markdown';

export function LegalPage() {
  const { slug } = useParams();
  const [document, setDocument] = useState<LegalDocument | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    requestJson('GET', `/api/legal/${slug}`)
      .then(result => {
        if (!cancelled) {
          setDocument(result);
          setProblem(null);
        }
      })
      .catch(err => {
        if (!cancelled) setProblem(errorMessage(err, 'That document could not be loaded.'));
      });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  return (
    <div className="app-container" style={{ flexDirection: 'column', alignItems: 'center', minHeight: '100vh', padding: '48px 16px' }}>
      <div className="glass-panel" style={{ width: '100%', maxWidth: '820px', padding: '40px', lineHeight: 1.6 }}>
        <Link to="/" style={{ color: 'var(--text-secondary)', textDecoration: 'none', fontSize: '0.9rem' }}>← OmniReview</Link>
        {problem && <p role="alert">{problem}</p>}
        {!document && !problem && <p>Loading…</p>}
        {document?.content && <Markdown text={document.content} />}
        <div style={{ marginTop: '32px' }}>
          <LegalLinks />
        </div>
      </div>
    </div>
  );
}
