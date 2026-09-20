import { useCallback, useEffect, useState } from 'react';
import type { CommentInfo, DeclarationInfo, InvitationInfo, TaskInfo, TeamMetrics, WorkloadRow } from '../../api/collaboration';
import { errorMessage } from '../../api/client';
import { PROJECT_ROLE_LABELS, canManageMembers, type ProjectMemberInfo } from '../../api/projects';
import { useAuth } from '../../auth/authContext';
import { AMBER, BLUE, GREEN, GREY, RED, chip, fieldLabel, fmt, muted, panel, row, smallButton } from '../../components/ui';
import { useWorkspace } from '../project/workspaceContext';

const PRIORITY_COLORS: Record<string, string> = { high: RED, normal: BLUE, low: GREY };
const STATUS_COLORS: Record<string, string> = { open: AMBER, in_progress: BLUE, done: GREEN };
const PROJECT_ANCHOR = 'project';

type Loaded = {
  members: ProjectMemberInfo[];
  invitations: InvitationInfo[];
  declarations: DeclarationInfo[];
  tasks: TaskInfo[];
  comments: CommentInfo[];
  workload: WorkloadRow[];
  metrics: TeamMetrics;
};

// The team: who is on the review, what they have declared, who is doing what, and the project's discussion.
export function TeamScreen() {
  const { projectId, currentProject } = useWorkspace();
  const { apiRequest } = useAuth();
  const base = `/api/projects/${projectId}`;
  const [data, setData] = useState<Loaded | null>(null);
  const [invitation, setInvitation] = useState({ email: '', role: 'screener' });
  const [invitationLink, setInvitationLink] = useState<string | null>(null);
  const [task, setTask] = useState({ title: '', assignee_id: 0, due_on: '', priority: 'normal' });
  const [declaration, setDeclaration] = useState({ has_competing_interests: false, statement: '', funding: '' });
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const manages = canManageMembers(currentProject?.role ?? 'viewer');

  const load = useCallback(async (): Promise<Loaded> => {
    const [members, declarations, tasks, comments, workload, metrics] = await Promise.all([
      apiRequest('GET', `${base}/members`),
      apiRequest('GET', `${base}/declarations`),
      apiRequest('GET', `${base}/tasks`),
      apiRequest('GET', `${base}/comments?anchor_key=${PROJECT_ANCHOR}`),
      apiRequest('GET', `${base}/team/workload`),
      apiRequest('GET', `${base}/team/metrics`),
    ]);
    // Only people who manage members may list invitations.
    const invitations = manages ? await apiRequest('GET', `${base}/invitations`) : [];
    return { members, invitations, declarations, tasks, comments, workload, metrics };
  }, [apiRequest, base, manages]);

  useEffect(() => {
    let cancelled = false;
    load()
      .then(result => {
        if (cancelled) return;
        setData(result);
        const mine = result.declarations.find(item => item.has_competing_interests !== null);
        if (mine) setDeclaration({ has_competing_interests: !!mine.has_competing_interests, statement: mine.statement, funding: mine.funding });
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Could not load the team.'));
      });
    return () => {
      cancelled = true;
    };
  }, [load]);

  const act = async (action: () => Promise<string | void>, failure: string) => {
    setBusy(true);
    setNotice(null);
    try {
      const message = await action();
      setData(await load());
      if (message) setNotice(message);
    } catch (err) {
      setNotice(errorMessage(err, failure));
    }
    setBusy(false);
  };

  if (!data) return <section className="glass-panel" style={{ padding: '32px' }}>{notice ?? 'Loading the team…'}</section>;

  const openTasks = data.tasks.filter(item => item.status !== 'done');
  const doneTasks = data.tasks.filter(item => item.status === 'done');

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
      <h3 style={{ marginBottom: '8px' }}>Team</h3>
      <p style={muted}>Invitations, competing-interest declarations, tasks, and discussion. Reviewer agreement and workload come from the decisions people actually recorded.</p>
      {notice && <p role="status" style={{ ...panel, margin: '12px 0' }}>{notice}</p>}

      <h4 style={{ marginTop: '20px' }}>Members</h4>
      {data.members.map(member => (
        <div key={member.user_id} style={{ ...row, fontSize: '0.85rem', marginBottom: '4px' }}>
          <span style={{ minWidth: '220px' }}>{member.name} <span style={muted}>{member.email}</span></span>
          <span style={chip(BLUE)}>{PROJECT_ROLE_LABELS[member.role] ?? member.role}</span>
        </div>
      ))}

      {manages && (
        <>
          <h4 style={{ marginTop: '20px' }}>Invite someone</h4>
          <p style={muted}>An invitation is valid for 14 days and can only be accepted by the address it was sent to.</p>
          <div style={{ ...panel, ...row }}>
            <input aria-label="Invitation email" className="search-input" style={{ flex: '1 1 240px' }} type="email" placeholder="colleague@university.edu" value={invitation.email} onChange={e => setInvitation({ ...invitation, email: e.target.value })} />
            <select aria-label="Invitation role" className="search-input" style={{ width: 'auto' }} value={invitation.role} onChange={e => setInvitation({ ...invitation, role: e.target.value })}>
              {Object.entries(PROJECT_ROLE_LABELS).map(([role, label]) => <option key={role} value={role}>{label}</option>)}
            </select>
            <button className="btn-primary" disabled={busy || !invitation.email.trim()} onClick={() => act(async () => {
              const created: InvitationInfo = await apiRequest('POST', `${base}/invitations`, { email: invitation.email.trim(), role: invitation.role });
              setInvitation({ ...invitation, email: '' });
              setInvitationLink(created.link ?? null);
              return 'Invitation created. Send the link below to your colleague.';
            }, 'Could not create the invitation.')}>
              Invite
            </button>
          </div>
          {invitationLink && (
            <p style={{ ...panel, marginTop: '8px', fontSize: '0.8rem', overflowWrap: 'anywhere' }}>
              This link is shown once: <code>{invitationLink}</code>
            </p>
          )}
          {data.invitations.map(item => (
            <div key={item.id} style={{ ...row, fontSize: '0.85rem', marginTop: '6px' }}>
              <span style={{ minWidth: '220px' }}>{item.email}</span>
              <span style={chip(item.status === 'open' ? AMBER : item.status === 'accepted' ? GREEN : GREY)}>{item.status}</span>
              <span style={muted}>{PROJECT_ROLE_LABELS[item.role] ?? item.role}</span>
              {item.status === 'open' && (
                <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => act(async () => {
                  await apiRequest('DELETE', `${base}/invitations/${item.id}`);
                }, 'Could not withdraw the invitation.')}>
                  Withdraw
                </button>
              )}
            </div>
          ))}
        </>
      )}

      <h4 style={{ marginTop: '24px' }}>Your declaration</h4>
      <div style={{ ...panel, display: 'flex', flexDirection: 'column', gap: '8px' }}>
        <label style={{ ...row, fontSize: '0.85rem' }}>
          <input type="checkbox" checked={declaration.has_competing_interests} onChange={e => setDeclaration({ ...declaration, has_competing_interests: e.target.checked })} />
          I have a competing interest to declare
        </label>
        <label style={fieldLabel}>
          Competing interest
          <input className="search-input" value={declaration.statement} onChange={e => setDeclaration({ ...declaration, statement: e.target.value })} placeholder="Describe it, or leave blank if you have none" />
        </label>
        <label style={fieldLabel}>
          Funding
          <input className="search-input" value={declaration.funding} onChange={e => setDeclaration({ ...declaration, funding: e.target.value })} placeholder="Any funding for your work on this review" />
        </label>
        <div>
          <button className="btn-primary" disabled={busy} onClick={() => act(async () => {
            await apiRequest('PUT', `${base}/declarations/me`, declaration);
            return 'Declaration saved.';
          }, 'Could not save your declaration.')}>
            Save declaration
          </button>
        </div>
      </div>
      <div style={{ marginTop: '8px' }}>
        {data.declarations.map(item => (
          <div key={item.user_id} style={{ ...row, fontSize: '0.82rem' }}>
            <span style={{ minWidth: '200px' }}>{item.name}</span>
            {item.has_competing_interests === null ? (
              <span style={chip(GREY)}>not yet declared</span>
            ) : item.has_competing_interests ? (
              <span style={chip(AMBER)}>declared: {item.statement}</span>
            ) : (
              <span style={chip(GREEN)}>none</span>
            )}
          </div>
        ))}
      </div>

      <h4 style={{ marginTop: '24px' }}>Tasks</h4>
      <div style={{ ...panel, ...row }}>
        <input aria-label="Task title" className="search-input" style={{ flex: '1 1 220px' }} placeholder="What needs doing?" value={task.title} onChange={e => setTask({ ...task, title: e.target.value })} />
        <select aria-label="Task assignee" className="search-input" style={{ width: 'auto' }} value={task.assignee_id} onChange={e => setTask({ ...task, assignee_id: Number(e.target.value) })}>
          <option value={0}>Unassigned</option>
          {data.members.map(member => <option key={member.user_id} value={member.user_id}>{member.name}</option>)}
        </select>
        <input aria-label="Task due date" className="search-input" style={{ width: 'auto' }} type="date" value={task.due_on} onChange={e => setTask({ ...task, due_on: e.target.value })} />
        <select aria-label="Task priority" className="search-input" style={{ width: 'auto' }} value={task.priority} onChange={e => setTask({ ...task, priority: e.target.value })}>
          <option value="low">Low</option>
          <option value="normal">Normal</option>
          <option value="high">High</option>
        </select>
        <button className="btn-primary" disabled={busy || !task.title.trim()} onClick={() => act(async () => {
          await apiRequest('POST', `${base}/tasks`, {
            title: task.title.trim(),
            assignee_id: task.assignee_id || null,
            due_on: task.due_on || null,
            priority: task.priority,
          });
          setTask({ title: '', assignee_id: 0, due_on: '', priority: 'normal' });
        }, 'Could not add the task.')}>
          Add task
        </button>
      </div>
      {openTasks.length === 0 && <p style={muted}>No open tasks.</p>}
      {openTasks.map(item => (
        <div key={item.id} style={{ ...row, fontSize: '0.85rem', marginTop: '6px' }}>
          <span style={chip(PRIORITY_COLORS[item.priority] ?? GREY)}>{item.priority}</span>
          <span style={{ flex: '1 1 200px' }}>{item.title}</span>
          <span style={muted}>{item.assignee ?? 'Unassigned'}</span>
          {item.due_on && <span style={chip(STATUS_COLORS[item.status] ?? GREY)}>due {item.due_on}</span>}
          <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => act(async () => {
            await apiRequest('PATCH', `${base}/tasks/${item.id}`, { status: item.status === 'open' ? 'in_progress' : 'done' });
          }, 'Could not update the task.')}>
            {item.status === 'open' ? 'Start' : 'Mark done'}
          </button>
        </div>
      ))}
      {doneTasks.length > 0 && <p style={{ ...muted, marginTop: '8px' }}>{doneTasks.length} done.</p>}

      <h4 style={{ marginTop: '24px' }}>Discussion</h4>
      <p style={muted}>Mention someone with @ and their email address to notify them.</p>
      <div style={{ ...panel, ...row }}>
        <input aria-label="New comment" className="search-input" style={{ flex: '1 1 320px' }} placeholder="Add a comment for the team" value={comment} onChange={e => setComment(e.target.value)} />
        <button className="btn-primary" disabled={busy || !comment.trim()} onClick={() => act(async () => {
          await apiRequest('POST', `${base}/comments`, { anchor_key: PROJECT_ANCHOR, anchor_label: 'Project', body: comment.trim() });
          setComment('');
        }, 'Could not add the comment.')}>
          Comment
        </button>
      </div>
      {data.comments.filter(item => item.parent_id === null).map(item => (
        <div key={item.id} style={{ ...panel, marginTop: '8px', fontSize: '0.85rem' }}>
          <div style={{ ...row, justifyContent: 'space-between' }}>
            <strong>{item.author ?? 'Former member'}</strong>
            {item.resolved ? <span style={chip(GREEN)}>resolved</span> : (
              <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => act(async () => {
                await apiRequest('POST', `${base}/comments/${item.id}/resolve`);
              }, 'Could not resolve the thread.')}>
                Resolve
              </button>
            )}
          </div>
          <p style={{ margin: '6px 0 0 0' }}>{item.deleted ? <em style={muted}>deleted</em> : item.body}</p>
          {data.comments.filter(reply => reply.parent_id === item.id).map(reply => (
            <p key={reply.id} style={{ margin: '6px 0 0 16px', borderLeft: '2px solid var(--border)', paddingLeft: '8px' }}>
              <strong>{reply.author ?? 'Former member'}:</strong> {reply.deleted ? <em style={muted}>deleted</em> : reply.body}
            </p>
          ))}
        </div>
      ))}

      <h4 style={{ marginTop: '24px' }}>Workload</h4>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
          <thead>
            <tr style={{ textAlign: 'left', color: 'var(--text-secondary)' }}>
              <th>Reviewer</th><th>Title/abstract</th><th>Full text</th><th>Extractions</th><th>Appraisals</th><th>Open tasks</th>
            </tr>
          </thead>
          <tbody>
            {data.workload.map(item => (
              <tr key={item.user_id} style={{ borderTop: '1px solid var(--border)' }}>
                <td>{item.name}</td>
                <td>{item.title_abstract_decisions}</td>
                <td>{item.full_text_decisions}</td>
                <td>{item.extraction_values}</td>
                <td>{item.appraisals_signed_off}</td>
                <td>{item.open_tasks}{item.overdue_tasks > 0 && <span style={{ ...chip(RED), marginLeft: '6px' }}>{item.overdue_tasks} overdue</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h4 style={{ marginTop: '24px' }}>Agreement between reviewers</h4>
      {data.metrics.pairwise_agreement.length === 0 ? (
        <p style={muted}>Agreement is reported once two reviewers have screened the same records.</p>
      ) : (
        data.metrics.pairwise_agreement.map((pair, index) => (
          <div key={index} style={{ ...row, fontSize: '0.85rem' }}>
            <span>{pair.reviewers.join(' and ')}</span>
            <span style={chip(pair.kappa !== null && pair.kappa >= 0.6 ? GREEN : AMBER)}>kappa {fmt(pair.kappa)}</span>
            <span style={muted}>over {pair.n} records</span>
          </div>
        ))
      )}
    </section>
  );
}
