import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { errorMessage } from '../api/client';
import { PROJECT_ROLE_LABELS, canManageMembers, type ProjectSummary } from '../api/projects';
import { useAuth } from '../auth/authContext';
import { ShareModal } from '../components/ShareModal';

export function DashboardPage() {
  const { apiRequest, userName, signOut } = useAuth();
  const navigate = useNavigate();
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [sharing, setSharing] = useState<ProjectSummary | null>(null);
  const [reloadCount, setReloadCount] = useState(0);
  const name = userName ?? '';

  useEffect(() => {
    let cancelled = false;
    apiRequest('GET', '/api/projects')
      .then(data => {
        if (!cancelled) setProjects(data);
      })
      .catch(err => {
        if (!cancelled) alert(errorMessage(err, 'Could not load your projects.'));
      });
    return () => {
      cancelled = true;
    };
  }, [apiRequest, reloadCount]);

  const handleCreateProject = async () => {
    const title = window.prompt('Name your new review project');
    if (!title?.trim()) return;
    try {
      const project: ProjectSummary = await apiRequest('POST', '/api/projects', { title: title.trim() });
      navigate(`/projects/${project.id}/setup`);
    } catch (err) {
      alert(errorMessage(err, 'Could not create the project.'));
    }
  };

  return (
    <div className="app-container" style={{ justifyContent: 'center', alignItems: 'center', flexDirection: 'column', height: '100vh', position: 'relative' }}>
      <div style={{ position: 'absolute', top: '24px', right: '40px', display: 'flex', alignItems: 'center', gap: '20px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <div style={{ width: '40px', height: '40px', borderRadius: '50%', background: 'linear-gradient(135deg, var(--accent-primary), var(--accent-secondary))', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1rem', fontWeight: 'bold', boxShadow: '0 4px 12px rgba(59,130,246,0.2)' }}>
            {name.charAt(0).toUpperCase()}
          </div>
          <div>
            <div style={{ fontSize: '0.95rem', fontWeight: 600 }}>{name}</div>
            <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>Pro Researcher</div>
          </div>
        </div>
        <button style={{ background: 'transparent', border: '1px solid rgba(255,255,255,0.1)', color: 'var(--text-secondary)', padding: '8px 16px', borderRadius: '8px', cursor: 'pointer', fontSize: '0.85rem', transition: 'all 0.2s', display: 'flex', alignItems: 'center', gap: '8px' }} onClick={() => navigate('/settings')} onMouseOver={e => e.currentTarget.style.color = '#fff'} onMouseOut={e => e.currentTarget.style.color = 'var(--text-secondary)'}>
          ⚙️ Settings
        </button>
        <button style={{ background: 'transparent', border: '1px solid rgba(239, 68, 68, 0.3)', color: '#ef4444', padding: '8px 16px', borderRadius: '8px', cursor: 'pointer', fontSize: '0.85rem', transition: 'all 0.2s' }} onClick={signOut} onMouseOver={e => e.currentTarget.style.background = 'rgba(239, 68, 68, 0.1)'} onMouseOut={e => e.currentTarget.style.background = 'transparent'}>
          Logout
        </button>
      </div>
      <div style={{ textAlign: 'center', marginBottom: '40px' }}>
        <div style={{ width: '80px', height: '80px', borderRadius: '24px', background: 'linear-gradient(135deg, var(--accent-primary), var(--accent-secondary))', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 'bold', fontSize: '2.5rem', margin: '0 auto 24px', boxShadow: '0 8px 32px rgba(59,130,246,0.4)' }}>O</div>
        <h1 style={{ fontSize: '3rem', marginBottom: '16px', letterSpacing: '-1px' }}>Welcome back!</h1>
      </div>
      <div className="glass-panel" style={{ width: '100%', maxWidth: '800px', padding: '40px', border: '1px solid rgba(255,255,255,0.05)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '32px' }}>
          <h2 style={{ margin: 0 }}>Your Active Projects</h2>
          <button className="btn-primary" style={{ padding: '10px 20px', borderRadius: '8px' }} onClick={handleCreateProject}>+ New Project</button>
        </div>
        {projects.length === 0 && (
          <p style={{ color: 'var(--text-secondary)', margin: 0 }}>You aren't a member of any projects yet. Create one to get started.</p>
        )}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          {projects.map(project => (
            <div key={project.id} style={{ background: 'linear-gradient(145deg, rgba(255,255,255,0.03), rgba(0,0,0,0.2))', padding: '24px', borderRadius: '12px', border: '1px solid rgba(255,255,255,0.1)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '16px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '20px', minWidth: 0 }}>
                <div style={{ width: '48px', height: '48px', flexShrink: 0, background: 'rgba(59,130,246,0.1)', borderRadius: '12px', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--accent-primary)', fontSize: '1.2rem' }}>📄</div>
                <div style={{ minWidth: 0 }}>
                  <h3 style={{ margin: '0 0 6px 0', fontSize: '1.1rem' }}>{project.title}</h3>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '12px', color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
                    {project.organization && <span>{project.organization.name}</span>}
                    <span>{project.member_count} {project.member_count === 1 ? 'member' : 'members'}</span>
                    <span style={{ color: 'var(--accent-primary)' }}>Your role: {PROJECT_ROLE_LABELS[project.role] ?? project.role}</span>
                  </div>
                </div>
              </div>
              <div style={{ display: 'flex', gap: '12px', flexShrink: 0 }}>
                <button style={{ background: 'transparent', border: '1px solid rgba(255,255,255,0.1)', color: 'var(--text-primary)', padding: '8px 16px', borderRadius: '8px', cursor: 'pointer' }} onClick={() => setSharing(project)}>{canManageMembers(project.role) ? 'Share' : 'Members'}</button>
                <button className="btn-primary" style={{ padding: '8px 24px', borderRadius: '8px' }} onClick={() => navigate(`/projects/${project.id}/setup`)}>Open</button>
              </div>
            </div>
          ))}
        </div>
      </div>
      {sharing && <ShareModal project={sharing} onClose={() => setSharing(null)} onMembersChanged={() => setReloadCount(count => count + 1)} />}
    </div>
  );
}
