import { useEffect, useState } from 'react';
import type { InvitationInfo } from '../api/collaboration';
import { errorMessage } from '../api/client';
import { PROJECT_ROLE_LABELS, canManageMembers, type ProjectMemberInfo, type ProjectSummary } from '../api/projects';
import { useAuth } from '../auth/authContext';

type ShareModalProps = {
  project: ProjectSummary;
  onClose: () => void;
  onMembersChanged?: () => void;
};

// A project's members, invitations by email, and handing over ownership. An invitation can only be accepted by the
// address it was sent to, so the person needs an OmniReview account under that address.
export function ShareModal({ project, onClose, onMembersChanged }: ShareModalProps) {
  const { apiRequest } = useAuth();
  const [members, setMembers] = useState<ProjectMemberInfo[]>([]);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState('viewer');
  const [link, setLink] = useState<string | null>(null);
  const [successor, setSuccessor] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);

  const isOwner = project.role === 'owner';

  useEffect(() => {
    let cancelled = false;
    apiRequest('GET', `/api/projects/${project.id}/members`)
      .then(data => {
        if (!cancelled) setMembers(data);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Could not load project members.'));
      });
    return () => {
      cancelled = true;
    };
  }, [apiRequest, project.id]);

  const handleInvite = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inviteEmail.trim()) return;
    try {
      const invitation: InvitationInfo = await apiRequest('POST', `/api/projects/${project.id}/invitations`, {
        email: inviteEmail.trim(),
        role: inviteRole,
      });
      setInviteEmail('');
      setLink(invitation.link ?? null);
      setNotice('Invitation created. Send the link below; it is valid for 14 days.');
      onMembersChanged?.();
    } catch (err) {
      setNotice(errorMessage(err, 'Could not create that invitation.'));
    }
  };

  const handleTransfer = async () => {
    if (!successor) return;
    try {
      const updated = await apiRequest('POST', `/api/projects/${project.id}/ownership`, {
        user_id: successor,
        keep_role: 'lead_reviewer',
      });
      setMembers(updated);
      setNotice('Ownership handed over. You are now a lead reviewer on this review.');
      onMembersChanged?.();
    } catch (err) {
      setNotice(errorMessage(err, 'Could not hand over ownership.'));
    }
  };

  return (
    <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10000 }}>
      <div className="glass-panel" role="dialog" aria-label={`Members of ${project.title}`} style={{ width: '100%', maxWidth: '540px', padding: '32px' }}>
        <h3 style={{ marginBottom: '16px' }}>Members of {project.title}</h3>
        <ul style={{ listStyle: 'none', padding: 0, margin: '0 0 24px 0', maxHeight: '240px', overflowY: 'auto' }}>
          {members.map(member => (
            <li key={member.user_id} style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', padding: '8px 0', borderBottom: '1px solid rgba(255,255,255,0.05)', fontSize: '0.9rem' }}>
              <span style={{ minWidth: 0, overflowWrap: 'anywhere' }}>{member.name} <span style={{ color: 'var(--text-secondary)' }}>{member.email}</span></span>
              <span style={{ color: 'var(--accent-primary)', whiteSpace: 'nowrap' }}>{PROJECT_ROLE_LABELS[member.role] ?? member.role}</span>
            </li>
          ))}
        </ul>
        {notice && <p role="status" style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '12px' }}>{notice}</p>}
        {link && (
          <p style={{ background: 'rgba(0,0,0,0.25)', borderRadius: '8px', padding: '10px', fontSize: '0.78rem', overflowWrap: 'anywhere', marginBottom: '16px' }}>
            <code>{link}</code>
          </p>
        )}
        {canManageMembers(project.role) ? (
          <form onSubmit={handleInvite}>
            <label style={{ display: 'block', fontSize: '0.9rem', marginBottom: '8px' }}>Invite someone by email</label>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', marginBottom: '16px' }}>
              <input type="email" required value={inviteEmail} onChange={e => setInviteEmail(e.target.value)} placeholder="colleague@university.edu" className="search-input" style={{ flex: '1 1 220px' }} />
              <select aria-label="Invitation role" className="search-input" value={inviteRole} onChange={e => setInviteRole(e.target.value)}>
                {Object.entries(PROJECT_ROLE_LABELS)
                  .filter(([role]) => role !== 'owner' || isOwner)
                  .map(([role, label]) => <option key={role} value={role}>{label}</option>)}
              </select>
            </div>
            {isOwner && members.length > 1 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', marginBottom: '24px', alignItems: 'center' }}>
                <label style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>Hand over ownership to</label>
                <select aria-label="New owner" className="search-input" style={{ flex: '1 1 180px' }} value={successor} onChange={e => setSuccessor(Number(e.target.value))}>
                  <option value={0}>Choose a member…</option>
                  {members.filter(member => member.role !== 'owner').map(member => (
                    <option key={member.user_id} value={member.user_id}>{member.name}</option>
                  ))}
                </select>
                <button type="button" className="btn-glass" style={{ padding: '8px 14px', fontSize: '0.8rem' }} disabled={!successor} onClick={handleTransfer}>
                  Hand over
                </button>
              </div>
            )}
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '12px' }}>
              <button type="button" style={{ background: 'transparent', border: '1px solid rgba(255,255,255,0.1)', color: 'var(--text-secondary)', padding: '10px 20px', borderRadius: '8px', cursor: 'pointer', fontWeight: 500 }} onClick={onClose}>Close</button>
              <button type="submit" className="btn-primary" style={{ padding: '10px 24px', borderRadius: '8px', fontWeight: 600 }}>Send invitation</button>
            </div>
          </form>
        ) : (
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <button type="button" className="btn-primary" style={{ padding: '10px 24px', borderRadius: '8px' }} onClick={onClose}>Close</button>
          </div>
        )}
      </div>
    </div>
  );
}
