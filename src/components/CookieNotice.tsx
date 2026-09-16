import { useState } from 'react';
import { Link } from 'react-router-dom';

const STORAGE_KEY = 'omnireview-cookie-notice';

function dismissedBefore(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === 'dismissed';
  } catch {
    return false;
  }
}

// OmniReview uses no optional cookies, so this is a notice rather than a consent request (see docs/legal/cookies.md).
export function CookieNotice() {
  const [visible, setVisible] = useState(() => !dismissedBefore());
  if (!visible) return null;

  const dismiss = () => {
    try {
      window.localStorage.setItem(STORAGE_KEY, 'dismissed');
    } catch {
      // Private browsing can block storage; the notice simply shows again next time.
    }
    setVisible(false);
  };

  return (
    <div role="region" aria-label="Cookie notice" className="glass-panel" style={{ position: 'fixed', left: '24px', bottom: '24px', maxWidth: '380px', padding: '16px', zIndex: 9000, fontSize: '0.85rem' }}>
      <p style={{ margin: '0 0 10px 0', color: 'var(--text-secondary)' }}>
        OmniReview uses no advertising or analytics cookies. Your browser only remembers that you've read this.{' '}
        <Link to="/legal/cookies" style={{ color: 'var(--accent-primary)' }}>Cookie Notice</Link>
      </p>
      <button className="btn-primary" style={{ padding: '6px 16px' }} onClick={dismiss}>
        Got it
      </button>
    </div>
  );
}
