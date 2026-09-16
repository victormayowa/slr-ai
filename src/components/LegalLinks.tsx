import { Link } from 'react-router-dom';

const LINKS = [
  ['terms', 'Terms of Service'],
  ['privacy', 'Privacy Policy'],
  ['cookies', 'Cookies'],
  ['subprocessors', 'Sub-processors'],
] as const;

export function LegalLinks({ align = 'center' }: { align?: 'center' | 'left' }) {
  return (
    <nav aria-label="Legal" style={{ display: 'flex', flexWrap: 'wrap', justifyContent: align === 'center' ? 'center' : 'flex-start', gap: '16px', fontSize: '0.8rem' }}>
      {LINKS.map(([slug, label]) => (
        <Link key={slug} to={`/legal/${slug}`} style={{ color: 'var(--text-secondary)', textDecoration: 'none' }}>
          {label}
        </Link>
      ))}
      <Link to="/pricing" style={{ color: 'var(--text-secondary)', textDecoration: 'none' }}>Pricing</Link>
    </nav>
  );
}
