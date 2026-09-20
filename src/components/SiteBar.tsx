import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

// The fixed top bar on pages outside a project: the wordmark on the left, page actions on the right.
export function SiteBar({ children }: { children?: ReactNode }) {
  return (
    <header className="site-bar">
      <Link to="/" className="wordmark">OmniReview</Link>
      {children && <nav aria-label="Account">{children}</nav>}
    </header>
  );
}
