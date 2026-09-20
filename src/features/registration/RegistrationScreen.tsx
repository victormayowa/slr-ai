import { useCallback, useEffect, useState } from 'react';
import { API_BASE, ApiError, errorDetail, errorMessage } from '../../api/client';
import { REGISTRATION_STATUS_LABELS, type ProsperoField, type ProtocolRegistration } from '../../api/registration';
import { useAuth } from '../../auth/authContext';
import { useWorkspace } from '../project/workspaceContext';

const panel = { background: 'var(--surface-muted)', borderRadius: '12px', padding: '16px' } as const;
const labelStyle = { display: 'block', fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '4px' } as const;

type RegistrationForm = { registry: 'PROSPERO' | 'OSF' | 'other'; registry_name: string; status: 'submitted' | 'registered'; registration_id: string; url: string };
const EMPTY_FORM: RegistrationForm = { registry: 'PROSPERO', registry_name: '', status: 'submitted', registration_id: '', url: '' };

export function RegistrationScreen() {
  const { projectId, stageInfo, refreshWorkflow } = useWorkspace();
  const { apiRequest, token } = useAuth();
  const [fields, setFields] = useState<ProsperoField[] | null>(null);
  const [registrations, setRegistrations] = useState<ProtocolRegistration[]>([]);
  const [form, setForm] = useState<RegistrationForm>(EMPTY_FORM);
  const [waiverReason, setWaiverReason] = useState('');
  const [osfToken, setOsfToken] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const locked = stageInfo('protocol')?.status === 'completed';
  const path = `/api/projects/${projectId}`;

  const load = useCallback(async () => {
    const base = `/api/projects/${projectId}`;
    const [fieldList, registrationList] = await Promise.all([
      apiRequest('GET', `${base}/prospero-fields`).catch(() => null),
      apiRequest('GET', `${base}/registrations`),
    ]);
    return { fieldList: fieldList as ProsperoField[] | null, registrationList: registrationList as ProtocolRegistration[] };
  }, [apiRequest, projectId]);

  useEffect(() => {
    let cancelled = false;
    load()
      .then(({ fieldList, registrationList }) => {
        if (cancelled) return;
        setFields(fieldList);
        setRegistrations(registrationList);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Could not load registrations.'));
      });
    return () => {
      cancelled = true;
    };
  }, [load]);

  const act = async (name: string, action: () => Promise<string>) => {
    setBusy(name);
    setNotice(null);
    try {
      const message = await action();
      const { fieldList, registrationList } = await load();
      setFields(fieldList);
      setRegistrations(registrationList);
      await refreshWorkflow();
      setNotice(message);
    } catch (err) {
      setNotice(errorMessage(err, 'Something went wrong. Please try again.'));
    }
    setBusy(null);
  };

  const download = (format: 'docx' | 'markdown') =>
    act(`download-${format}`, async () => {
      const response = await fetch(`${API_BASE}${path}/protocol-export?format=${format}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
      if (!response.ok) throw new ApiError(errorDetail(await response.json().catch(() => ({})), response.status), response.status);
      const name = /filename="([^"]+)"/.exec(response.headers.get('content-disposition') ?? '')?.[1] ?? `protocol.${format === 'docx' ? 'docx' : 'md'}`;
      const link = document.createElement('a');
      link.href = URL.createObjectURL(await response.blob());
      link.download = name;
      link.click();
      URL.revokeObjectURL(link.href);
      return `Downloaded ${name}.`;
    });

  const copy = (value: string) => {
    navigator.clipboard?.writeText(value).then(() => setNotice('Copied to the clipboard.'), () => setNotice('Copy failed; select the text instead.'));
  };

  const record = () =>
    act('record', async () => {
      await apiRequest('POST', `${path}/registrations`, {
        ...form,
        registry_name: form.registry === 'other' ? form.registry_name : null,
        url: form.url || null,
      });
      setForm(EMPTY_FORM);
      return 'Registration recorded.';
    });

  const waive = () =>
    act('waive', async () => {
      await apiRequest('POST', `${path}/registrations/waiver`, { reason: waiverReason });
      setWaiverReason('');
      return 'Registration waiver recorded.';
    });

  const depositOnOsf = () =>
    act('osf', async () => {
      try {
        const deposit: ProtocolRegistration = await apiRequest('POST', `${path}/registrations/osf`, { token: osfToken });
        return `Protocol deposited in a private OSF project: ${deposit.url}. Register it on OSF, then record the registration here.`;
      } finally {
        setOsfToken('');
      }
    });

  const update = (registration: ProtocolRegistration, changes: Record<string, unknown>, message: string) =>
    act(`update-${registration.id}`, async () => {
      await apiRequest('PATCH', `${path}/registrations/${registration.id}`, changes);
      return message;
    });

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px', maxWidth: '1000px', margin: '0 auto' }}>
      <h3 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>Registration & Export</h3>
      <p style={{ color: 'var(--text-secondary)' }}>
        Export the protocol, register it, and record the registration. OmniReview never submits a registration for you:
        PROSPERO has no submission API, and registrations are public and permanent.
      </p>
      {notice && <p role="status" style={{ color: 'var(--text-secondary)' }}>{notice}</p>}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
        <div style={panel}>
          <h4 style={{ marginTop: 0 }}>Export the protocol</h4>
          <p style={{ fontSize: '0.9rem' }}>
            {locked ? 'The protocol is locked, so exports carry its version number.' : 'The protocol isn’t locked yet, so exports are marked as drafts.'} Missing parts are marked [TO COMPLETE].
          </p>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            <button className="btn-primary" onClick={() => download('docx')} disabled={busy !== null} style={{ padding: '8px 16px' }}>Download Word document</button>
            <button className="btn-glass" onClick={() => download('markdown')} disabled={busy !== null} style={{ padding: '8px 16px' }}>Download Markdown</button>
          </div>
        </div>

        <div style={panel}>
          <h4 style={{ marginTop: 0 }}>Registrations</h4>
          {registrations.length === 0 ? <p>No registration or waiver recorded yet. Search sign-off needs one.</p> : (
            <ul aria-label="Registrations" style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: '10px' }}>
              {registrations.map(registration => {
                const outdated = registration.status !== 'waived' && registration.protocol_version < registration.latest_protocol_version;
                return (
                  <li key={registration.id} style={{ borderLeft: '3px solid var(--accent-primary)', paddingLeft: '10px' }}>
                    <strong>{registration.status === 'waived' ? 'Waiver' : registration.registry}</strong>
                    {registration.registration_id && ` · ${registration.registration_id}`} · {REGISTRATION_STATUS_LABELS[registration.status]} · protocol version {registration.protocol_version}
                    {registration.url && <> · <a href={registration.url} target="_blank" rel="noreferrer" style={{ color: 'var(--accent-primary)' }}>open</a></>}
                    {registration.waiver_reason && <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>Reason: {registration.waiver_reason}</div>}
                    {outdated && <div style={{ fontSize: '0.85rem', color: '#9A5B00' }}>The protocol is now version {registration.latest_protocol_version}. Update the registry record, then confirm it here.</div>}
                    <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginTop: '6px' }}>
                      {registration.status === 'submitted' && (
                        <button className="btn-glass" onClick={() => update(registration, { status: 'registered' }, 'Marked as registered.')} disabled={busy !== null} style={{ padding: '4px 10px', fontSize: '0.8rem' }}>Mark as registered</button>
                      )}
                      {outdated && (
                        <button className="btn-glass" onClick={() => update(registration, { covers_latest_version: true }, 'Registry record confirmed as up to date.')} disabled={busy !== null} style={{ padding: '4px 10px', fontSize: '0.8rem' }}>
                          Registry updated to version {registration.latest_protocol_version}
                        </button>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {!locked && <p role="note" style={{ color: '#9A5B00', margin: 0 }}>Sign off the protocol before registering it, depositing it on OSF, or waiving registration.</p>}

        <div style={panel}>
          <h4 style={{ marginTop: 0 }}>Record a registration</h4>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '12px' }}>
            <label style={labelStyle}>Registry
              <select className="search-input" value={form.registry} onChange={e => setForm({ ...form, registry: e.target.value as RegistrationForm['registry'] })}>
                <option value="PROSPERO">PROSPERO</option>
                <option value="OSF">OSF Registries</option>
                <option value="other">Other registry</option>
              </select>
            </label>
            {form.registry === 'other' && (
              <label style={labelStyle}>Registry name
                <input className="search-input" value={form.registry_name} onChange={e => setForm({ ...form, registry_name: e.target.value })} />
              </label>
            )}
            <label style={labelStyle}>Status
              <select className="search-input" value={form.status} onChange={e => setForm({ ...form, status: e.target.value as RegistrationForm['status'] })}>
                <option value="submitted">Submitted</option>
                <option value="registered">Registered</option>
              </select>
            </label>
            <label style={labelStyle}>Registration number
              <input className="search-input" placeholder="For example CRD420261234" value={form.registration_id} onChange={e => setForm({ ...form, registration_id: e.target.value })} />
            </label>
            <label style={labelStyle}>Link
              <input className="search-input" type="url" value={form.url} onChange={e => setForm({ ...form, url: e.target.value })} />
            </label>
          </div>
          <button className="btn-primary" onClick={record} disabled={!locked || busy !== null} style={{ padding: '8px 16px', marginTop: '12px' }}>Record registration</button>
        </div>

        {fields && (
          <div style={panel}>
            <h4 style={{ marginTop: 0 }}>PROSPERO form fields</h4>
            <p style={{ fontSize: '0.9rem' }}>Copy each field into the PROSPERO form. Field names may differ slightly from the current form.</p>
            <dl style={{ margin: 0 }}>
              {fields.map(item => (
                <div key={item.field} style={{ padding: '10px 0', borderBottom: '1px solid var(--border)' }}>
                  <dt style={{ display: 'flex', justifyContent: 'space-between', gap: '8px', flexWrap: 'wrap' }}>
                    <strong>{item.field}</strong>
                    <span style={{ fontSize: '0.8rem', color: item.ready ? '#137A47' : '#9A5B00' }}>{item.ready ? 'Ready' : 'Needs input'}</span>
                  </dt>
                  <dd style={{ margin: '4px 0 0' }}>
                    <div style={{ whiteSpace: 'pre-wrap', fontSize: '0.9rem' }}>{item.value || <em style={{ color: 'var(--text-secondary)' }}>Nothing recorded</em>}</div>
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>From: {item.source}</div>
                    {item.value && <button className="btn-glass" onClick={() => copy(item.value)} style={{ padding: '2px 8px', fontSize: '0.75rem', marginTop: '4px' }}>Copy</button>}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        )}

        <div style={panel}>
          <h4 style={{ marginTop: 0 }}>Deposit on OSF</h4>
          <p style={{ fontSize: '0.9rem' }}>
            Creates a private OSF project containing the locked protocol as a Word document, ready for you to register on OSF.
            Use an OSF personal access token with the osf.full_write scope. The token is used for this request only and isn't stored.
          </p>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            <input aria-label="OSF personal access token" type="password" autoComplete="off" className="search-input" style={{ flex: '1 1 260px' }} value={osfToken} onChange={e => setOsfToken(e.target.value)} />
            <button className="btn-glass" onClick={depositOnOsf} disabled={!locked || busy !== null || osfToken.length < 10} style={{ padding: '8px 16px' }}>
              {busy === 'osf' ? 'Depositing…' : 'Deposit on OSF'}
            </button>
          </div>
        </div>

        <div style={panel}>
          <h4 style={{ marginTop: 0 }}>Waive registration</h4>
          <p style={{ fontSize: '0.9rem' }}>Only when registration isn't appropriate, such as a teaching exercise. The reason is kept in the audit trail and reported.</p>
          <textarea aria-label="Reason for not registering" className="search-input" style={{ width: '100%', minHeight: '70px' }} value={waiverReason} onChange={e => setWaiverReason(e.target.value)} />
          <button className="btn-glass" onClick={waive} disabled={!locked || busy !== null || waiverReason.trim().length < 20} style={{ padding: '8px 16px', marginTop: '8px' }}>Record waiver</button>
        </div>
      </div>
    </section>
  );
}
