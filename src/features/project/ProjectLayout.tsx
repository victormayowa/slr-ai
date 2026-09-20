import { useState } from 'react';
import { Navigate, NavLink, Outlet, useNavigate, useParams } from 'react-router-dom';
import { useAuth } from '../../auth/authContext';
import { ChatWidget } from '../../components/ChatWidget';
import { LegalLinks } from '../../components/LegalLinks';
import { NotificationBell } from '../../components/NotificationBell';
import { ShareModal } from '../../components/ShareModal';
import { PROJECT_TABS } from './tabs';
import { useProjectWorkspaceState } from './useProjectWorkspaceState';
import { WorkspaceContext } from './workspaceContext';

// Keyed by project so switching projects starts from fresh workspace state.
export function ProjectRoute() {
  const { projectId } = useParams();
  const id = Number(projectId);
  if (!Number.isInteger(id) || id <= 0) return <Navigate to="/" replace />;
  return <ProjectLayout key={id} projectId={id} />;
}

function ProjectLayout({ projectId }: { projectId: number }) {
  const workspace = useProjectWorkspaceState(projectId);
  const { userName } = useAuth();
  const navigate = useNavigate();
  const [sharing, setSharing] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const name = userName ?? '';

  return (
    <WorkspaceContext.Provider value={workspace}>
      <div className="app-container">
        <aside className={`sidebar${menuOpen ? ' open' : ''}`} aria-label="Project navigation">
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px', marginBottom: '24px' }}>
            <NavLink to="/" className="sidebar-brand" style={{ textDecoration: 'none' }}>OmniReview</NavLink>
            <button className="btn-glass sidebar-toggle" aria-expanded={menuOpen} onClick={() => setMenuOpen(open => !open)} style={{ padding: '6px 12px' }}>
              {menuOpen ? 'Close' : 'Menu'}
            </button>
          </div>

          <div className="sidebar-body" style={{ overflowY: 'auto', flex: 1 }}>
            <div style={{ marginBottom: '20px', paddingBottom: '20px', borderBottom: '1px solid var(--border)' }}>
              <NavLink to="/" className="side-link" end style={{ marginBottom: '12px', paddingLeft: '0' }}>← All projects</NavLink>
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '14px' }}>
                <div aria-hidden style={{ width: '34px', height: '34px', borderRadius: '50%', background: 'var(--gold)', color: 'var(--navy)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--sans)', fontWeight: 800 }}>
                  {name.charAt(0).toUpperCase()}
                </div>
                <div style={{ flex: 1, fontFamily: 'var(--nav-font)', fontSize: '0.9rem', fontWeight: 600, color: '#ffffff' }}>{name}</div>
                <NotificationBell align="start" />
              </div>
              <button className="btn-accent" disabled={!workspace.currentProject} style={{ width: '100%', padding: '9px' }} onClick={() => setSharing(true)}>
                Share and collaborate
              </button>
            </div>

            <nav style={{ display: 'flex', flexDirection: 'column', gap: '2px' }} onClick={() => setMenuOpen(false)}>
              {PROJECT_TABS.map(tab => (
                <NavLink key={tab.path} to={tab.path} className="side-link">
                  {tab.label}
                </NavLink>
              ))}
            </nav>
          </div>
        </aside>

        <main className="main-content">
          {workspace.loadError ? (
            <section className="glass-panel" role="alert" style={{ padding: '32px', maxWidth: '640px', margin: '0 auto' }}>
              <h3 style={{ marginTop: 0 }}>This project couldn't be opened</h3>
              <p style={{ color: 'var(--text-secondary)' }}>{workspace.loadError}</p>
              <button className="btn-primary" onClick={() => navigate('/')}>Back to your projects</button>
            </section>
          ) : workspace.currentProject ? (
            <Outlet />
          ) : (
            <p style={{ color: 'var(--text-secondary)', textAlign: 'center' }}>Loading project…</p>
          )}
          <footer style={{ textAlign: 'center', padding: '40px 24px', color: 'var(--text-secondary)', fontSize: '0.85rem', marginTop: '40px', borderTop: '1px solid var(--border)' }}>
            <div style={{ marginBottom: '16px' }}>
              <span style={{ fontFamily: 'var(--sans)', fontWeight: 700, color: 'var(--navy)' }}>OmniReview research platform</span>
            </div>
            <div style={{ marginBottom: '16px' }}>
              <LegalLinks />
            </div>
            <p style={{ margin: 0 }}>&copy; {new Date().getFullYear()} OmniReview AI. All rights reserved.</p>
          </footer>
        </main>

        <ChatWidget />
        {sharing && workspace.currentProject && <ShareModal project={workspace.currentProject} onClose={() => setSharing(false)} />}
      </div>
    </WorkspaceContext.Provider>
  );
}
