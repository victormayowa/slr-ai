import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type { NotificationList } from '../api/collaboration';
import { errorMessage } from '../api/client';
import { useAuth } from '../auth/authContext';
import { BLUE, muted } from './ui';

const POLL_MS = 60_000;

// Unread mentions, replies, task assignments, and reminders, with a link to whatever each one is about.
// `align` sets which edge the list opens from: 'end' for bars on the right of the page, 'start' in the sidebar.
export function NotificationBell({ align = 'end' }: { align?: 'start' | 'end' }) {
  const { apiRequest } = useAuth();
  const navigate = useNavigate();
  const [data, setData] = useState<NotificationList | null>(null);
  const [open, setOpen] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const fetchNotifications = useCallback(
    (): Promise<NotificationList> => apiRequest('GET', '/api/notifications?limit=20'),
    [apiRequest],
  );

  useEffect(() => {
    let cancelled = false;
    const refresh = () =>
      fetchNotifications()
        .then(result => {
          if (cancelled) return;
          setData(result);
          setProblem(null);
        })
        .catch(err => {
          if (!cancelled) setProblem(errorMessage(err, 'Could not load your notifications.'));
        });
    refresh();
    const timer = window.setInterval(refresh, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [fetchNotifications]);

  const reload = () => fetchNotifications().then(setData).catch(() => undefined);

  const openNotification = async (id: number, link: string) => {
    try {
      await apiRequest('POST', `/api/notifications/${id}/read`);
    } catch {
      // Opening it still works even if marking it read fails.
    }
    await reload();
    setOpen(false);
    if (link) navigate(link);
  };

  const markAllRead = async () => {
    try {
      await apiRequest('POST', '/api/notifications/read-all');
      await reload();
    } catch (err) {
      setProblem(errorMessage(err, 'Could not mark them read.'));
    }
  };

  const unread = data?.unread ?? 0;

  return (
    <div style={{ position: 'relative' }}>
      <button
        aria-label={unread ? `Notifications (${unread} unread)` : 'Notifications'}
        onClick={() => setOpen(value => !value)}
        className="bell-button"
        style={{ background: 'transparent', border: '1px solid var(--border)', color: 'var(--text-secondary)', padding: '8px 14px', borderRadius: '8px', cursor: 'pointer', fontSize: '0.85rem', position: 'relative' }}
      >
        🔔
        {unread > 0 && (
          <span style={{ position: 'absolute', top: '-6px', right: '-6px', background: BLUE, color: '#fff', borderRadius: '999px', fontSize: '0.7rem', padding: '1px 6px', fontWeight: 700 }}>
            {unread > 99 ? '99+' : unread}
          </span>
        )}
      </button>
      {open && (
        <div
          role="dialog"
          aria-label="Notifications"
          className="glass-panel"
          style={{ position: 'absolute', ...(align === 'end' ? { right: 0 } : { left: 0 }), top: '44px', width: '340px', maxHeight: '420px', overflowY: 'auto', padding: '12px', zIndex: 200 }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
            <strong style={{ fontSize: '0.9rem' }}>Notifications</strong>
            {unread > 0 && (
              <button className="btn-glass" style={{ padding: '2px 8px', fontSize: '0.75rem' }} onClick={markAllRead}>
                Mark all read
              </button>
            )}
          </div>
          {problem && <p role="alert" style={muted}>{problem}</p>}
          {data && data.notifications.length === 0 && <p style={muted}>Nothing yet.</p>}
          {data?.notifications.map(notification => (
            <button
              key={notification.id}
              onClick={() => openNotification(notification.id, notification.link)}
              style={{ display: 'block', width: '100%', textAlign: 'left', background: notification.read_at ? 'transparent' : 'rgba(30, 106, 224, 0.08)', border: 'none', borderBottom: '1px solid var(--border)', color: 'var(--text-primary)', padding: '10px 8px', cursor: 'pointer', fontSize: '0.82rem' }}
            >
              <div style={{ fontWeight: notification.read_at ? 400 : 600 }}>{notification.title}</div>
              {notification.body && <div style={{ ...muted, fontSize: '0.78rem' }}>{notification.body.slice(0, 120)}</div>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
