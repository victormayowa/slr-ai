import { useCallback, useEffect, useState } from 'react';
import { errorMessage } from '../../api/client';
import { useAuth } from '../../auth/authContext';
import { AMBER, BLUE, GREEN, GREY, RED, chip, muted, panel, row, smallButton } from '../../components/ui';

type UserRow = {
  id: number;
  name: string;
  email: string;
  institution: string | null;
  created_at: string;
  is_active: boolean;
  is_platform_admin: boolean;
  email_verified: boolean;
  mfa_enabled: boolean;
  deletion_requested_at: string | null;
  deleted_at: string | null;
  plan: string | null;
  projects: number;
};

export function UsersTab() {
  const { apiRequest } = useAuth();
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [data, setData] = useState<{ total: number; users: UserRow[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const fetchUsers = useCallback((q: string) => apiRequest('GET', `/api/admin/users?limit=100&q=${encodeURIComponent(q)}`), [apiRequest]);

  useEffect(() => {
    let cancelled = false;
    fetchUsers(search)
      .then(result => {
        if (!cancelled) setData(result);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Users could not be loaded.'));
      });
    return () => {
      cancelled = true;
    };
  }, [fetchUsers, search]);

  const update = async (user: UserRow, body: Record<string, unknown>, message: string) => {
    setBusy(true);
    setNotice(null);
    try {
      await apiRequest('PATCH', `/api/admin/users/${user.id}`, body);
      setData(await fetchUsers(search));
      setNotice(message);
    } catch (err) {
      setNotice(errorMessage(err, 'The user could not be updated.'));
    }
    setBusy(false);
  };

  return (
    <div>
      <form style={{ ...panel, ...row }} onSubmit={e => { e.preventDefault(); setSearch(query); }}>
        <input aria-label="Search users" className="search-input" style={{ flex: '1 1 260px' }} placeholder="Name, email, or institution" value={query} onChange={e => setQuery(e.target.value)} />
        <button type="submit" className="btn-primary">Search</button>
        {data && <span style={muted}>{data.total} users</span>}
      </form>
      {notice && <p role="status" style={{ ...panel, margin: '12px 0' }}>{notice}</p>}
      {data?.users.map(user => (
        <div key={user.id} style={{ ...panel, marginTop: '8px', fontSize: '0.85rem' }}>
          <div style={row}>
            <strong style={{ flex: '1 1 180px' }}>{user.name}</strong>
            <span style={{ flex: '1 1 200px' }}>{user.email}</span>
            <span style={muted}>#{user.id} · {user.plan ?? '—'} · {user.projects} projects</span>
          </div>
          <div style={{ ...row, marginTop: '6px' }}>
            <span style={chip(user.is_active ? GREEN : RED)}>{user.deleted_at ? 'deleted' : user.is_active ? 'active' : 'deactivated'}</span>
            {user.is_platform_admin && <span style={chip(BLUE)}>administrator</span>}
            <span style={chip(user.email_verified ? GREEN : AMBER)}>{user.email_verified ? 'email confirmed' : 'email unconfirmed'}</span>
            <span style={chip(user.mfa_enabled ? GREEN : GREY)}>{user.mfa_enabled ? 'two-factor' : 'no two-factor'}</span>
            {user.deletion_requested_at && !user.deleted_at && <span style={chip(AMBER)}>deletion requested</span>}
            {!user.deleted_at && (
              <>
                <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => update(user, { is_active: !user.is_active }, user.is_active ? 'Account deactivated.' : 'Account reactivated.')}>
                  {user.is_active ? 'Deactivate' : 'Reactivate'}
                </button>
                <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => update(user, { is_platform_admin: !user.is_platform_admin }, 'Administrator rights changed.')}>
                  {user.is_platform_admin ? 'Remove admin' : 'Make admin'}
                </button>
                {user.mfa_enabled && (
                  <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => {
                    if (window.confirm(`Reset two-factor sign-in for ${user.email}? Only do this after confirming their identity.`)) {
                      update(user, { reset_mfa: true }, 'Two-factor sign-in reset.');
                    }
                  }}>
                    Reset two-factor
                  </button>
                )}
              </>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
