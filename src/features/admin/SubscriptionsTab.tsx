import { useCallback, useEffect, useState } from 'react';
import { errorMessage } from '../../api/client';
import { useAuth } from '../../auth/authContext';
import { AMBER, BLUE, GREEN, RED, chip, fieldLabel, muted, panel, row, smallButton } from '../../components/ui';

type SubscriptionRow = {
  id: number;
  account_kind: 'user' | 'organization';
  account_id: number;
  account: string;
  plan: string;
  plan_code: string;
  status: string;
  provider: string;
  current_period_end: string | null;
  note: string;
};

type OrganizationRow = {
  id: number;
  name: string;
  plan: string | null;
  projects: number;
  members: { user_id: number; name: string; email: string; role: string }[];
};

const STATUS_COLORS: Record<string, string> = { active: GREEN, trialing: BLUE, past_due: AMBER, canceled: RED };

// Organizations, and assigning plans by arrangement (invoiced customers).
export function SubscriptionsTab() {
  const { apiRequest } = useAuth();
  const [subscriptions, setSubscriptions] = useState<SubscriptionRow[]>([]);
  const [organizations, setOrganizations] = useState<OrganizationRow[]>([]);
  const [assignment, setAssignment] = useState({ account_kind: 'organization', account_id: 0, plan_code: 'institution', current_period_end: '', note: '' });
  const [organization, setOrganization] = useState('');
  const [member, setMember] = useState({ organization_id: 0, email: '', role: 'member' });
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const fetchAll = useCallback(
    async () => Promise.all([apiRequest('GET', '/api/admin/subscriptions'), apiRequest('GET', '/api/admin/organizations')]) as Promise<[SubscriptionRow[], OrganizationRow[]]>,
    [apiRequest],
  );

  useEffect(() => {
    let cancelled = false;
    fetchAll()
      .then(([subs, orgs]) => {
        if (cancelled) return;
        setSubscriptions(subs);
        setOrganizations(orgs);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Subscriptions could not be loaded.'));
      });
    return () => {
      cancelled = true;
    };
  }, [fetchAll]);

  const act = async (action: () => Promise<string | void>, failure: string) => {
    setBusy(true);
    setNotice(null);
    try {
      const message = await action();
      const [subs, orgs] = await fetchAll();
      setSubscriptions(subs);
      setOrganizations(orgs);
      if (message) setNotice(message);
    } catch (err) {
      setNotice(errorMessage(err, failure));
    }
    setBusy(false);
  };

  return (
    <div>
      {notice && <p role="status" style={{ ...panel, margin: '12px 0' }}>{notice}</p>}

      <h4>Organizations</h4>
      <div style={{ ...panel, ...row }}>
        <input aria-label="New organization name" className="search-input" style={{ flex: '1 1 240px' }} placeholder="University of Example" value={organization} onChange={e => setOrganization(e.target.value)} />
        <button className="btn-primary" disabled={busy || organization.trim().length < 2} onClick={() => act(async () => {
          await apiRequest('POST', '/api/admin/organizations', { name: organization.trim() });
          setOrganization('');
          return 'Organization created.';
        }, 'The organization could not be created.')}>
          Create organization
        </button>
      </div>
      {organizations.map(item => (
        <div key={item.id} style={{ ...panel, marginTop: '8px', fontSize: '0.85rem' }}>
          <div style={row}>
            <strong style={{ flex: '1 1 200px' }}>{item.name}</strong>
            <span style={muted}>#{item.id}</span>
            <span style={chip(BLUE)}>{item.plan ?? 'no plan'}</span>
            <span style={muted}>{item.projects} projects</span>
          </div>
          <div style={muted}>{item.members.map(m => `${m.name} (${m.role})`).join(', ') || 'No members'}</div>
        </div>
      ))}
      <div style={{ ...panel, ...row, marginTop: '8px' }}>
        <select aria-label="Organization for member" className="search-input" style={{ width: 'auto' }} value={member.organization_id} onChange={e => setMember({ ...member, organization_id: Number(e.target.value) })}>
          <option value={0}>Organization…</option>
          {organizations.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
        <input aria-label="Member email" className="search-input" style={{ flex: '1 1 200px' }} placeholder="person@example.org" value={member.email} onChange={e => setMember({ ...member, email: e.target.value })} />
        <select aria-label="Organization role" className="search-input" style={{ width: 'auto' }} value={member.role} onChange={e => setMember({ ...member, role: e.target.value })}>
          <option value="member">Member</option>
          <option value="admin">Admin (manages billing)</option>
          <option value="owner">Owner</option>
        </select>
        <button className="btn-glass" style={smallButton} disabled={busy || !member.organization_id || !member.email.trim()} onClick={() => act(async () => {
          await apiRequest('PUT', `/api/admin/organizations/${member.organization_id}/members`, { email: member.email.trim(), role: member.role });
          setMember({ ...member, email: '' });
          return 'Member saved.';
        }, 'The member could not be added.')}>
          Add or update member
        </button>
      </div>

      <h4 style={{ marginTop: '24px' }}>Assign a plan</h4>
      <div style={{ ...panel, ...row }}>
        <label style={fieldLabel}>
          Account type
          <select className="search-input" style={{ width: 'auto' }} value={assignment.account_kind} onChange={e => setAssignment({ ...assignment, account_kind: e.target.value })}>
            <option value="organization">Organization</option>
            <option value="user">User</option>
          </select>
        </label>
        <label style={fieldLabel}>
          Account id
          <input type="number" min={1} className="search-input" style={{ width: '100px' }} value={assignment.account_id || ''} onChange={e => setAssignment({ ...assignment, account_id: Number(e.target.value) })} />
        </label>
        <label style={fieldLabel}>
          Plan code
          <input className="search-input" style={{ width: '120px' }} value={assignment.plan_code} onChange={e => setAssignment({ ...assignment, plan_code: e.target.value })} />
        </label>
        <label style={fieldLabel}>
          Paid until
          <input type="date" className="search-input" style={{ width: 'auto' }} value={assignment.current_period_end} onChange={e => setAssignment({ ...assignment, current_period_end: e.target.value })} />
        </label>
        <label style={{ ...fieldLabel, flex: '1 1 200px' }}>
          Note (for example the invoice number)
          <input className="search-input" value={assignment.note} onChange={e => setAssignment({ ...assignment, note: e.target.value })} />
        </label>
        <button className="btn-primary" style={{ alignSelf: 'flex-end' }} disabled={busy || !assignment.account_id} onClick={() => act(async () => {
          await apiRequest('PUT', '/api/admin/subscriptions', {
            ...assignment,
            current_period_end: assignment.current_period_end ? `${assignment.current_period_end}T23:59:59Z` : null,
          });
          return 'Plan assigned.';
        }, 'The plan could not be assigned.')}>
          Assign
        </button>
      </div>

      <h4 style={{ marginTop: '24px' }}>Subscriptions</h4>
      {subscriptions.length === 0 && <p style={muted}>No subscriptions yet; every account is on the free plan.</p>}
      {subscriptions.map(item => (
        <div key={item.id} style={{ ...row, fontSize: '0.85rem', marginTop: '4px' }}>
          <span style={{ flex: '1 1 200px' }}>{item.account} <span style={muted}>({item.account_kind} #{item.account_id})</span></span>
          <span>{item.plan}</span>
          <span style={chip(STATUS_COLORS[item.status] ?? BLUE)}>{item.status.replace('_', ' ')}</span>
          <span style={muted}>{item.provider}</span>
          {item.current_period_end && <span style={muted}>until {new Date(item.current_period_end).toLocaleDateString()}</span>}
          {item.note && <span style={muted}>{item.note}</span>}
        </div>
      ))}
    </div>
  );
}
