import { useCallback, useEffect, useState } from 'react';
import { errorMessage } from '../../api/client';
import type { AuthorContact, FieldDef } from '../../api/extraction';
import { useAuth } from '../../auth/authContext';
import { AMBER, GREEN, RED, chip, fieldLabel, muted, panel, row, smallButton } from '../../components/ui';
import { useWorkspace } from '../project/workspaceContext';

const STATUS_COLOR = { draft: '#9ca3af', sent: AMBER, replied: GREEN, no_response: RED, closed: '#9ca3af' } as const;

// Requests to a study's authors for missing data, with a drafted request and a log of the correspondence.
export function AuthorContacts({ studyId, fields }: { studyId: number; fields: FieldDef[] }) {
  const { projectId } = useWorkspace();
  const { apiRequest } = useAuth();
  const base = `/api/projects/${projectId}`;
  const [contacts, setContacts] = useState<AuthorContact[]>([]);
  const [form, setForm] = useState({ contact_name: '', email: '', questions: '', reminder_due: '', field_ids: [] as number[] });
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async (): Promise<AuthorContact[]> => {
    const all: AuthorContact[] = await apiRequest('GET', `${base}/author-contacts`);
    return all.filter(contact => contact.study_id === studyId);
  }, [apiRequest, base, studyId]);

  useEffect(() => {
    let cancelled = false;
    load()
      .then(items => {
        if (!cancelled) setContacts(items);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [load]);

  const run = async (action: () => Promise<void>, failure: string) => {
    setNotice(null);
    try {
      await action();
      setContacts(await load());
    } catch (err) {
      setNotice(errorMessage(err, failure));
    }
  };

  const draft = () =>
    run(async () => {
      const params = new URLSearchParams({ field_ids: form.field_ids.join(','), contact_name: form.contact_name });
      const drafted: { body: string } = await apiRequest('GET', `${base}/studies/${studyId}/author-contacts/draft?${params}`);
      setForm(prev => ({ ...prev, questions: drafted.body }));
    }, 'Could not draft the request.');

  const create = () =>
    run(async () => {
      await apiRequest('POST', `${base}/studies/${studyId}/author-contacts`, { ...form, reminder_due: form.reminder_due || null });
      setForm({ contact_name: '', email: '', questions: '', reminder_due: '', field_ids: [] });
      setNotice('Saved. Send the request from your own email, then log it here.');
    }, 'Could not save the request.');

  const log = (contact: AuthorContact, direction: 'outgoing' | 'incoming') => {
    const body = window.prompt(direction === 'outgoing' ? 'Paste the message you sent:' : 'Paste or summarize the reply:', direction === 'outgoing' ? contact.questions : '');
    if (!body) return;
    run(async () => {
      await apiRequest('POST', `${base}/author-contacts/${contact.id}/messages`, { direction, body });
    }, 'Could not log the message.');
  };

  const setStatus = (contact: AuthorContact, changes: Partial<Pick<AuthorContact, 'status' | 'reminder_due'>>) =>
    run(async () => {
      await apiRequest('PATCH', `${base}/author-contacts/${contact.id}`, changes);
    }, 'Could not update the request.');

  return (
    <div style={{ ...panel, marginTop: '16px' }}>
      <h4 style={{ marginTop: 0 }}>Contacting authors</h4>
      {notice && <p role="status" style={muted}>{notice}</p>}
      {contacts.map(contact => (
        <div key={contact.id} style={{ borderTop: '1px solid rgba(255,255,255,0.08)', padding: '8px 0' }}>
          <div style={row}>
            <strong>{contact.contact_name}</strong> <span style={muted}>{contact.email}</span>
            <span style={chip(STATUS_COLOR[contact.status])}>{contact.status.replace('_', ' ')}</span>
            {contact.reminder_overdue && <span style={chip(RED)}>follow-up due {contact.reminder_due}</span>}
          </div>
          <div style={{ ...row, marginTop: '4px' }}>
            <button className="btn-glass" style={smallButton} onClick={() => log(contact, 'outgoing')}>Log sent message</button>
            <button className="btn-glass" style={smallButton} onClick={() => log(contact, 'incoming')}>Log reply</button>
            <label style={{ ...muted, display: 'flex', gap: '4px', alignItems: 'center' }}>
              Follow up on
              <input type="date" className="search-input" style={{ width: '160px' }} value={contact.reminder_due ?? ''} onChange={e => setStatus(contact, { reminder_due: e.target.value })} />
            </label>
            <button className="btn-glass" style={smallButton} onClick={() => setStatus(contact, { status: 'no_response' })}>No response</button>
            <button className="btn-glass" style={smallButton} onClick={() => setStatus(contact, { status: 'closed' })}>Close</button>
          </div>
          {contact.messages.map(message => (
            <p key={message.id} style={{ ...muted, margin: '4px 0', whiteSpace: 'pre-wrap' }}>
              {message.direction === 'outgoing' ? 'Sent' : 'Received'} {message.occurred_at.slice(0, 10)}: {message.body.slice(0, 300)}{message.body.length > 300 ? '…' : ''}
            </p>
          ))}
        </div>
      ))}
      <div style={{ ...row, marginTop: '8px' }}>
        <label style={fieldLabel}>
          Author
          <input className="search-input" value={form.contact_name} onChange={e => setForm({ ...form, contact_name: e.target.value })} />
        </label>
        <label style={fieldLabel}>
          Email
          <input type="email" className="search-input" value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} />
        </label>
        <label style={fieldLabel}>
          Follow up on
          <input type="date" className="search-input" value={form.reminder_due} onChange={e => setForm({ ...form, reminder_due: e.target.value })} />
        </label>
      </div>
      <div style={{ ...row, marginTop: '6px' }}>
        <span style={muted}>Ask about:</span>
        {fields.map(field => (
          <label key={field.id} style={{ ...muted, display: 'flex', gap: '4px', alignItems: 'center' }}>
            <input type="checkbox" checked={form.field_ids.includes(field.id)} onChange={e => setForm({ ...form, field_ids: e.target.checked ? [...form.field_ids, field.id] : form.field_ids.filter(id => id !== field.id) })} />
            {field.name}
          </label>
        ))}
      </div>
      <textarea aria-label="Request to the authors" className="search-input" style={{ width: '100%', minHeight: '120px', marginTop: '6px' }} value={form.questions} onChange={e => setForm({ ...form, questions: e.target.value })} />
      <div style={{ ...row, marginTop: '6px' }}>
        <button className="btn-glass" style={smallButton} onClick={draft}>Draft the request</button>
        <button className="btn-primary" style={smallButton} disabled={!form.contact_name.trim() || !form.email.includes('@') || !form.questions.trim()} onClick={create}>Save request</button>
      </div>
    </div>
  );
}
