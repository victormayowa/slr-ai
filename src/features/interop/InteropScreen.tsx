import { useCallback, useEffect, useRef, useState } from 'react';
import type { WebhookDeliveryInfo, WebhookInfo } from '../../api/collaboration';
import { errorMessage } from '../../api/client';
import { downloadFile } from '../../api/download';
import { useAuth } from '../../auth/authContext';
import { AMBER, BLUE, GREEN, GREY, RED, chip, fieldLabel, muted, panel, row, smallButton } from '../../components/ui';
import { useWorkspace } from '../project/workspaceContext';

const RECORD_FORMATS = [
  { value: 'ris', label: 'RIS', extension: 'ris' },
  { value: 'bibtex', label: 'BibTeX', extension: 'bib' },
  { value: 'endnote_xml', label: 'EndNote XML', extension: 'xml' },
  { value: 'csv', label: 'CSV', extension: 'csv' },
  { value: 'json', label: 'JSON', extension: 'json' },
];
const DELIVERY_COLORS: Record<string, string> = { delivered: GREEN, pending: AMBER, failed: RED };

type ImportSummary = { rows: number; applied: number; skipped: number; unmatched: string[]; unmatched_count: number; dry_run: boolean };

// Getting records and results in and out: reference formats, the decision files other screening tools write, the
// spreadsheets RevMan and GRADEpro read, JATS, FHIR, and webhooks for other systems.
export function InteropScreen() {
  const { projectId } = useWorkspace();
  const { apiRequest, token } = useAuth();
  const base = `/api/projects/${projectId}`;
  const [exportChoice, setExportChoice] = useState({ format: 'ris', scope: 'unique' });
  const [decisionStage, setDecisionStage] = useState('title_abstract');
  const [structuredFormat, setStructuredFormat] = useState('json');
  const [summary, setSummary] = useState<ImportSummary | null>(null);
  const [webhooks, setWebhooks] = useState<WebhookInfo[]>([]);
  const [deliveries, setDeliveries] = useState<Record<number, WebhookDeliveryInfo[]>>({});
  const [newHook, setNewHook] = useState({ url: '', events: 'stage.*, task.*' });
  const [secret, setSecret] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const decisionInput = useRef<HTMLInputElement>(null);
  const structuredInput = useRef<HTMLInputElement>(null);

  // Only people who manage the workflow may list webhooks; for everyone else the section stays empty.
  const fetchWebhooks = useCallback(
    (): Promise<WebhookInfo[]> => apiRequest('GET', `${base}/webhooks`).catch(() => []),
    [apiRequest, base],
  );

  useEffect(() => {
    let cancelled = false;
    fetchWebhooks().then(result => {
      if (!cancelled) setWebhooks(result);
    });
    return () => {
      cancelled = true;
    };
  }, [fetchWebhooks]);

  const loadWebhooks = async () => setWebhooks(await fetchWebhooks());

  const act = async (action: () => Promise<string | void>, failure: string) => {
    setBusy(true);
    setNotice(null);
    try {
      const message = await action();
      if (message) setNotice(message);
    } catch (err) {
      setNotice(errorMessage(err, failure));
    }
    setBusy(false);
  };

  const download = (path: string, fileName: string, failure: string) =>
    act(async () => {
      await downloadFile(token, path, fileName);
    }, failure);

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
      <h3 style={{ marginBottom: '8px' }}>Export & Integrations</h3>
      <p style={muted}>
        Everything here is a documented file format or a public API, so the review isn't locked inside OmniReview. The
        RevMan and GRADEpro sheets follow those tools' documented layouts; check them after importing.
      </p>
      {notice && <p role="status" style={{ ...panel, margin: '12px 0' }}>{notice}</p>}

      <h4 style={{ marginTop: '20px' }}>Records</h4>
      <div style={{ ...panel, ...row }}>
        <label style={fieldLabel}>
          Format
          <select className="search-input" style={{ width: 'auto' }} value={exportChoice.format} onChange={e => setExportChoice({ ...exportChoice, format: e.target.value })}>
            {RECORD_FORMATS.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
        </label>
        <label style={fieldLabel}>
          Which records
          <select className="search-input" style={{ width: 'auto' }} value={exportChoice.scope} onChange={e => setExportChoice({ ...exportChoice, scope: e.target.value })}>
            <option value="unique">All, without duplicates</option>
            <option value="all">Everything, including duplicates</option>
            <option value="included">Included studies only</option>
          </select>
        </label>
        <button className="btn-primary" disabled={busy} onClick={() => {
          const chosen = RECORD_FORMATS.find(item => item.value === exportChoice.format);
          return download(
            `${base}/records/export?format=${exportChoice.format}&scope=${exportChoice.scope}`,
            `records-${exportChoice.scope}.${chosen?.extension ?? 'txt'}`,
            'Could not export the records.',
          );
        }}>
          Download
        </button>
      </div>

      <h4 style={{ marginTop: '24px' }}>Other screening tools</h4>
      <div style={{ ...panel, ...row }}>
        <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => download(`${base}/records/export/rayyan`, 'rayyan.csv', 'Could not export for Rayyan.')}>
          Export for Rayyan
        </button>
        <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => download(`${base}/records/export/covidence`, 'covidence.csv', 'Could not export for Covidence.')}>
          Export for Covidence
        </button>
      </div>
      <div style={{ ...panel, ...row, marginTop: '8px' }}>
        <label style={fieldLabel}>
          Import decisions into
          <select className="search-input" style={{ width: 'auto' }} value={decisionStage} onChange={e => setDecisionStage(e.target.value)}>
            <option value="title_abstract">Title and abstract screening</option>
            <option value="full_text">Full-text screening</option>
          </select>
        </label>
        <input ref={decisionInput} aria-label="Decision file" type="file" accept=".csv" className="search-input" style={{ flex: '1 1 200px' }} />
        {['Preview', 'Import'].map(label => (
          <button key={label} className={label === 'Import' ? 'btn-primary' : 'btn-glass'} style={label === 'Import' ? undefined : smallButton} disabled={busy} onClick={() => act(async () => {
            const file = decisionInput.current?.files?.[0];
            if (!file) return 'Choose a Rayyan or Covidence file first.';
            const form = new FormData();
            form.append('file', file);
            const dryRun = label === 'Preview';
            const result: ImportSummary = await apiRequest(
              'POST',
              `${base}/screening/import-decisions?stage=${decisionStage}&dry_run=${dryRun}`,
              form,
            );
            setSummary(result);
            return dryRun
              ? `${result.applied} of ${result.rows} decisions would be recorded.`
              : `${result.applied} decisions recorded as yours.`;
          }, 'Could not read that decision file.')}>
            {label}
          </button>
        ))}
      </div>
      {summary && summary.unmatched_count > 0 && (
        <p style={{ ...muted, marginTop: '6px' }}>
          {summary.unmatched_count} rows matched no record in this project, for example: {summary.unmatched.slice(0, 3).join('; ')}
        </p>
      )}

      <h4 style={{ marginTop: '24px' }}>Import records from JSON or JATS</h4>
      <div style={{ ...panel, ...row }}>
        <select aria-label="Structured import format" className="search-input" style={{ width: 'auto' }} value={structuredFormat} onChange={e => setStructuredFormat(e.target.value)}>
          <option value="json">JSON</option>
          <option value="jats">JATS XML</option>
        </select>
        <input ref={structuredInput} aria-label="Records file" type="file" accept=".json,.xml" className="search-input" style={{ flex: '1 1 200px' }} />
        <button className="btn-primary" disabled={busy} onClick={() => act(async () => {
          const file = structuredInput.current?.files?.[0];
          if (!file) return 'Choose a file first.';
          const form = new FormData();
          form.append('file', file);
          const result = await apiRequest('POST', `${base}/records/import/structured?format=${structuredFormat}`, form);
          return `${result.records} records imported.`;
        }, 'Could not import those records.')}>
          Import
        </button>
      </div>

      <h4 style={{ marginTop: '24px' }}>Results for other evidence tools</h4>
      <div style={{ ...panel, ...row }}>
        <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => download(`${base}/exports/revman.csv`, 'revman.csv', 'No analysis has been run yet.')}>
          RevMan-style data
        </button>
        <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => download(`${base}/exports/gradepro.csv`, 'gradepro-sof.csv', 'No outcome has been graded yet.')}>
          GRADEpro-style summary of findings
        </button>
        <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => download(`${base}/manuscript/export/jats`, 'manuscript.xml', 'There is no manuscript yet.')}>
          Manuscript as JATS XML
        </button>
        <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => download(`${base}/fhir/bundle`, 'evidence-bundle.json', 'Could not build the FHIR bundle.')}>
          EBMonFHIR bundle
        </button>
      </div>

      <h4 style={{ marginTop: '24px' }}>Public API</h4>
      <p style={muted}>
        The API is documented at <code>/docs</code>. Create a personal access token in Settings, then send it as
        <code> Authorization: Bearer omr_…</code>. Read tokens can't change anything, and no token can create tokens.
      </p>

      <h4 style={{ marginTop: '24px' }}>Webhooks</h4>
      <div style={{ ...panel, ...row }}>
        <input aria-label="Webhook URL" className="search-input" style={{ flex: '1 1 240px' }} placeholder="https://example.org/omnireview" value={newHook.url} onChange={e => setNewHook({ ...newHook, url: e.target.value })} />
        <input aria-label="Webhook events" className="search-input" style={{ flex: '1 1 180px' }} value={newHook.events} onChange={e => setNewHook({ ...newHook, events: e.target.value })} />
        <button className="btn-primary" disabled={busy || !newHook.url.trim()} onClick={() => act(async () => {
          const created: WebhookInfo = await apiRequest('POST', `${base}/webhooks`, {
            url: newHook.url.trim(),
            events: newHook.events.split(',').map(event => event.trim()).filter(Boolean),
          });
          setSecret(created.secret ?? null);
          setNewHook({ url: '', events: 'stage.*, task.*' });
          await loadWebhooks();
          return 'Webhook created.';
        }, 'Could not create the webhook.')}>
          Add webhook
        </button>
      </div>
      {secret && (
        <p style={{ ...panel, marginTop: '8px', fontSize: '0.8rem', overflowWrap: 'anywhere' }}>
          Signing secret, shown once: <code>{secret}</code>. Each delivery carries
          <code> X-OmniReview-Signature: sha256=…</code>, an HMAC of the exact body.
        </p>
      )}
      {webhooks.length === 0 && <p style={muted}>No webhooks yet.</p>}
      {webhooks.map(hook => (
        <div key={hook.id} style={{ ...panel, marginTop: '8px', fontSize: '0.85rem' }}>
          <div style={row}>
            <span style={{ flex: '1 1 220px', overflowWrap: 'anywhere' }}>{hook.url}</span>
            <span style={chip(hook.active ? GREEN : GREY)}>{hook.active ? 'active' : 'off'}</span>
            <span style={chip(BLUE)}>{hook.events.join(', ')}</span>
            {hook.failure_count > 0 && <span style={chip(AMBER)}>{hook.failure_count} failures</span>}
            <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => act(async () => {
              await apiRequest('POST', `${base}/webhooks/${hook.id}/test`);
              setDeliveries({ ...deliveries, [hook.id]: await apiRequest('GET', `${base}/webhooks/${hook.id}/deliveries`) });
              return 'A test delivery was queued.';
            }, 'Could not queue a test delivery.')}>
              Send test
            </button>
            <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => act(async () => {
              setDeliveries({ ...deliveries, [hook.id]: await apiRequest('GET', `${base}/webhooks/${hook.id}/deliveries`) });
            }, 'Could not load the deliveries.')}>
              Deliveries
            </button>
            <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => act(async () => {
              await apiRequest('DELETE', `${base}/webhooks/${hook.id}`);
              await loadWebhooks();
            }, 'Could not delete the webhook.')}>
              Delete
            </button>
          </div>
          {(deliveries[hook.id] ?? []).slice(0, 5).map(delivery => (
            <div key={delivery.id} style={{ ...row, fontSize: '0.78rem', marginTop: '4px' }}>
              <span style={chip(DELIVERY_COLORS[delivery.status] ?? GREY)}>{delivery.status}</span>
              <span>{delivery.action}</span>
              <span style={muted}>{delivery.attempts} attempts{delivery.response_status ? ` · HTTP ${delivery.response_status}` : ''}</span>
            </div>
          ))}
        </div>
      ))}
    </section>
  );
}
