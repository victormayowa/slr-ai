import { useState } from 'react';
import { Navigate, NavLink, Outlet, useNavigate, useParams } from 'react-router-dom';
import { USER_TIER } from '../../app/plan';
import { useAuth } from '../../auth/authContext';
import { ChatWidget } from '../../components/ChatWidget';
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
  const name = userName ?? '';

  return (
    <WorkspaceContext.Provider value={workspace}>
      <div className="app-container">
        <aside className="sidebar" style={{ width: '320px', overflowY: 'auto' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '32px', cursor: 'pointer' }} onClick={() => navigate('/')}>
            <div style={{ width: '32px', height: '32px', borderRadius: '8px', background: 'linear-gradient(135deg, var(--accent-primary), var(--accent-secondary))', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 'bold' }}>O</div>
            <h2 style={{ fontSize: '1.2rem', margin: 0 }}>Back to Home</h2>
          </div>

          <div style={{ marginBottom: '24px', paddingBottom: '24px', borderBottom: '1px solid rgba(255,255,255,0.1)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '16px' }}>
              <div style={{ width: '36px', height: '36px', borderRadius: '50%', background: 'linear-gradient(135deg, var(--accent-primary), var(--accent-secondary))', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1rem', fontWeight: 'bold', boxShadow: '0 4px 12px rgba(59,130,246,0.3)' }}>
                {name.charAt(0).toUpperCase()}
              </div>
              <div>
                <div style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-primary)' }}>{name}</div>
                <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>{USER_TIER} Plan</div>
              </div>
            </div>
            <button disabled={!workspace.currentProject} style={{ width: '100%', background: 'rgba(59,130,246,0.1)', border: '1px solid rgba(59,130,246,0.3)', color: 'var(--accent-primary)', padding: '10px', borderRadius: '8px', cursor: 'pointer', fontSize: '0.85rem', fontWeight: 600, transition: 'all 0.2s', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px' }} onClick={() => setSharing(true)} onMouseOver={e => e.currentTarget.style.background = 'rgba(59,130,246,0.2)'} onMouseOut={e => e.currentTarget.style.background = 'rgba(59,130,246,0.1)'}>
              👥 Share / Collaborate
            </button>
          </div>

          <nav style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {PROJECT_TABS.map(tab => (
              <NavLink
                key={tab.path}
                to={tab.path}
                className="btn-glass"
                style={({ isActive }) => ({
                  textAlign: 'left', fontSize: '0.9rem', padding: '10px 12px', textDecoration: 'none', display: 'block',
                  border: isActive ? '1px solid var(--accent-primary)' : '1px solid transparent',
                  background: isActive ? 'rgba(59, 130, 246, 0.1)' : 'transparent',
                  color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)'
                })}
              >
                {tab.label}
              </NavLink>
            ))}
          </nav>
        </aside>

        <main className="main-content" style={{ overflowY: 'auto' }}>
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
          <footer style={{ textAlign: 'center', padding: '40px 24px', color: 'var(--text-secondary)', fontSize: '0.85rem', marginTop: '40px', borderTop: '1px solid rgba(255,255,255,0.1)' }}>
            <div style={{ marginBottom: '16px' }}>
              <span style={{ fontWeight: 'bold', color: 'var(--text-primary)' }}>OmniReview AI Research Platform</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'center', gap: '24px', marginBottom: '16px' }}>
              <a href="#" style={{ color: 'var(--text-secondary)', textDecoration: 'none' }}>Terms of Service</a>
              <a href="#" style={{ color: 'var(--text-secondary)', textDecoration: 'none' }}>Privacy Policy</a>
              <a href="#" style={{ color: 'var(--text-secondary)', textDecoration: 'none' }}>Documentation</a>
              <a href="mailto:support@omnireview.ai" style={{ color: 'var(--accent-primary)', textDecoration: 'none' }}>Contact Support</a>
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
