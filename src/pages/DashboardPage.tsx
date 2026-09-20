import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type { BillingAccountSummary, MeInfo } from '../api/account';
import { errorMessage } from '../api/client';
import { PROJECT_ROLE_LABELS, canManageMembers, type ProjectSummary } from '../api/projects';
import { useAuth } from '../auth/authContext';
import { LegalLinks } from '../components/LegalLinks';
import { SiteBar } from '../components/SiteBar';
import { NotificationBell } from '../components/NotificationBell';
import { ShareModal } from '../components/ShareModal';

const headerButton = { background: 'transparent', border: '1px solid var(--border-strong)', color: 'var(--navy)', fontFamily: 'var(--nav-font)', fontWeight: 500, padding: '8px 16px', borderRadius: '8px', cursor: 'pointer', fontSize: '0.85rem' } as const;

export function DashboardPage() {
  const { apiRequest, userName, signOut } = useAuth();
  const navigate = useNavigate();
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [me, setMe] = useState<MeInfo | null>(null);
  const [plan, setPlan] = useState<string | null>(null);
  const [sharing, setSharing] = useState<ProjectSummary | null>(null);
  const [reloadCount, setReloadCount] = useState(0);
  const [problem, setProblem] = useState<string | null>(null);
  const name = userName ?? '';

  useEffect(() => {
    let cancelled = false;
    apiRequest('GET', '/api/projects')
      .then(data => {
        if (!cancelled) setProjects(data);
      })
      .catch(err => {
        if (!cancelled) setProblem(errorMessage(err, 'Could not load your projects.'));
      });
    return () => {
      cancelled = true;
    };
  }, [apiRequest, reloadCount]);

  useEffect(() => {
    let cancelled = false;
    apiRequest('GET', '/api/auth/me')
      .then(data => {
        if (!cancelled) setMe(data);
      })
      .catch(() => undefined);
    apiRequest('GET', '/api/billing/accounts')
      .then((accounts: BillingAccountSummary[]) => {
        if (!cancelled) setPlan(accounts.find(account => account.kind === 'user')?.plan ?? null);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [apiRequest]);

  const handleCreateProject = async () => {
    const title = window.prompt('Name your new review project');
    if (!title?.trim()) return;
    try {
      const project: ProjectSummary = await apiRequest('POST', '/api/projects', { title: title.trim() });
      navigate(`/projects/${project.id}/setup`);
    } catch (err) {
      setProblem(errorMessage(err, 'Could not create the project.'));
    }
  };

  return (
    <div className="app-container" style={{ justifyContent: 'center', alignItems: 'center', flexDirection: 'column', minHeight: '100vh', position: 'relative', padding: '96px 16px 48px' }}>
      <SiteBar>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <div style={{ width: '40px', height: '40px', borderRadius: '50%', background: 'var(--gold)', color: 'var(--navy)', fontFamily: 'var(--sans)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1rem', fontWeight: 'bold' }}>
            {name.charAt(0).toUpperCase()}
          </div>
          <div>
            <div style={{ fontSize: '0.95rem', fontWeight: 600 }}>{name}</div>
            {plan && <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>{plan} plan</div>}
          </div>
        </div>
        <NotificationBell />
        <button style={headerButton} onClick={() => navigate('/billing')}>Billing</button>
        {me?.is_platform_admin && <button style={headerButton} onClick={() => navigate('/admin')}>Admin</button>}
        <button style={headerButton} onClick={() => navigate('/settings')}>⚙️ Settings</button>
        <button style={{ ...headerButton, border: '1px solid rgba(198, 40, 40, 0.3)', color: '#C62828' }} onClick={signOut}>Logout</button>
      </SiteBar>
      <div style={{ textAlign: 'center', marginBottom: '40px' }}>
        <div className="eyebrow" style={{ marginBottom: '12px' }}>Evidence synthesis workspace</div>
        <h1 style={{ fontSize: '2.75rem', marginBottom: '16px' }}>Welcome back!</h1>
        {me && !me.email_verified && (
          <p role="status" style={{ color: '#9A5B00' }}>
            Confirm your email address: open the link we sent to {me.email}, or send a new one from Settings.
          </p>
        )}
      </div>
      <div className="glass-panel" style={{ width: '100%', maxWidth: '800px', padding: '40px', border: '1px solid var(--border)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '32px' }}>
          <h2 style={{ margin: 0 }}>Your Active Projects</h2>
          <button className="btn-primary" style={{ padding: '10px 20px', borderRadius: '8px' }} onClick={handleCreateProject}>+ New Project</button>
        </div>
        {problem && (
          <p role="alert" style={{ color: '#C62828' }}>
            {problem}{' '}
            {problem.includes('Billing') && <button className="btn-glass" style={{ padding: '2px 10px', fontSize: '0.8rem' }} onClick={() => navigate('/billing')}>Open Billing</button>}
          </p>
        )}
        {projects.length === 0 && (
          <p style={{ color: 'var(--text-secondary)', margin: 0 }}>You aren't a member of any projects yet. Create one to get started.</p>
        )}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          {projects.map(project => (
            <div key={project.id} style={{ background: 'var(--surface)', padding: '24px', borderRadius: '12px', border: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '16px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '20px', minWidth: 0 }}>
                <div style={{ width: '48px', height: '48px', flexShrink: 0, background: 'rgba(30, 106, 224, 0.1)', borderRadius: '12px', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--accent-primary)', fontSize: '1.2rem' }}>📄</div>
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
                <button style={{ background: 'transparent', border: '1px solid var(--border)', color: 'var(--text-primary)', padding: '8px 16px', borderRadius: '8px', cursor: 'pointer' }} onClick={() => setSharing(project)}>{canManageMembers(project.role) ? 'Share' : 'Members'}</button>
                <button className="btn-primary" style={{ padding: '8px 24px', borderRadius: '8px' }} onClick={() => navigate(`/projects/${project.id}/setup`)}>Open</button>
              </div>
            </div>
          ))}
        </div>
      </div>
      <div style={{ marginTop: '32px' }}>
        <LegalLinks />
      </div>
      {sharing && <ShareModal project={sharing} onClose={() => setSharing(null)} onMembersChanged={() => setReloadCount(count => count + 1)} />}
    </div>
  );
}
