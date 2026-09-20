import { useCallback, useEffect, useState } from 'react';
import { STORAGE_PROVIDERS, storagePath, type StorageProvider, type StorageSettings } from '../../api/storage';
import { errorMessage } from '../../api/client';
import { useAuth } from '../../auth/authContext';
import { GREEN, RED, muted, row, smallButton } from '../../components/ui';

type Draft = {
  provider: StorageProvider;
  endpoint_url: string;
  region: string;
  bucket: string;
  key_prefix: string;
  access_key_id: string;
  secret_access_key: string;
};

const EMPTY: Draft = { provider: 'aws', endpoint_url: '', region: '', bucket: '', key_prefix: '', access_key_id: '', secret_access_key: '' };

// An account's choice of file storage: OmniReview's, or its own bucket. Used for a person and for an organization.
export function StoragePanel({ kind, accountId, label }: { kind: 'user' | 'organization'; accountId: number; label?: string }) {
  const { apiRequest } = useAuth();
  const [settings, setSettings] = useState<StorageSettings | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [editing, setEditing] = useState(false);
  const [notice, setNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => setSettings(await apiRequest('GET', storagePath(kind, accountId))), [apiRequest, kind, accountId]);

  useEffect(() => {
    let cancelled = false;
    apiRequest('GET', storagePath(kind, accountId))
      .then((data: StorageSettings) => {
        if (cancelled) return;
        setSettings(data);
        if (data.connection) {
          const { provider, endpoint_url, region, bucket, key_prefix } = data.connection;
          setDraft({ ...EMPTY, provider, endpoint_url, region, bucket, key_prefix });
        }
      })
      .catch(err => {
        if (!cancelled) setNotice({ tone: 'error', text: errorMessage(err, 'Could not load the storage settings.') });
      });
    return () => {
      cancelled = true;
    };
  }, [apiRequest, kind, accountId]);

  const act = async (action: () => Promise<string>) => {
    setBusy(true);
    try {
      setNotice({ tone: 'ok', text: await action() });
      await load();
    } catch (err) {
      setNotice({ tone: 'error', text: errorMessage(err, 'Something went wrong. Please try again.') });
    } finally {
      setBusy(false);
    }
  };

  const connection = settings?.connection ?? null;
  const chosen = STORAGE_PROVIDERS.find(item => item.id === draft.provider);
  const inBucket = connection?.use_for_new_files ?? false;

  const save = () =>
    act(async () => {
      const body: Record<string, unknown> = {
        provider: draft.provider,
        endpoint_url: draft.endpoint_url,
        region: draft.region,
        bucket: draft.bucket,
        key_prefix: draft.key_prefix,
        use_for_new_files: connection ? connection.use_for_new_files : true,
      };
      if (draft.access_key_id || draft.secret_access_key) {
        body.access_key_id = draft.access_key_id;
        body.secret_access_key = draft.secret_access_key;
      }
      await apiRequest('PUT', storagePath(kind, accountId), body);
      setDraft(current => ({ ...current, access_key_id: '', secret_access_key: '' }));
      setEditing(false);
      return 'Your bucket was checked and saved. New files go to it.';
    });

  const field = (name: keyof Draft, labelText: string, placeholder = '', type = 'text') => (
    <label style={{ display: 'flex', flexDirection: 'column', gap: '4px', fontSize: '0.85rem', flex: '1 1 200px' }}>
      {labelText}
      <input
        className="search-input"
        type={type}
        autoComplete="off"
        placeholder={placeholder}
        value={draft[name]}
        onChange={e => setDraft(current => ({ ...current, [name]: e.target.value }))}
      />
    </label>
  );

  return (
    <section aria-labelledby={`storage-heading-${kind}-${accountId}`} style={{ marginTop: '32px' }}>
      <h3 id={`storage-heading-${kind}-${accountId}`} style={{ borderBottom: '1px solid var(--border)', paddingBottom: '12px', marginBottom: '16px' }}>
        File storage{label ? `: ${label}` : ''}
      </h3>
      <p style={{ ...muted, marginBottom: '12px' }}>
        Uploaded and retrieved files, analysis plots, and export packages are kept in OmniReview's storage by default.
        You can keep them in your own S3-compatible bucket instead (Amazon S3, Backblaze B2, Cloudflare R2, Wasabi, or
        MinIO). Files in your own bucket don't count towards your plan's storage, and they aren't included in
        OmniReview's backups.
      </p>
      {settings && !settings.feature_allowed && (
        <p role="status" style={{ color: RED, fontSize: '0.85rem' }}>This plan doesn't include storing files in your own bucket.</p>
      )}
      {settings && (
        <p style={{ ...muted, marginBottom: '12px' }}>
          <span>{`${settings.files.platform} file(s) in OmniReview's storage, ${settings.files.bucket} in your bucket.`}</span>
        </p>
      )}

      {connection && !editing && (
        <div style={{ ...row, justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '12px' }}>
          <div style={{ fontSize: '0.9rem' }}>
            <strong>{chosen?.label ?? connection.provider}</strong>
            <div style={muted}>
              Bucket {connection.bucket}
              {connection.key_prefix ? ` / ${connection.key_prefix}` : ''} · key ending {connection.access_key_last_four}
            </div>
            <div style={{ ...muted, color: inBucket ? GREEN : 'var(--text-secondary)' }}>
              <span>{inBucket ? 'New files go to your bucket' : "New files go to OmniReview's storage"}</span>
              {connection.last_verified_at ? <span>{` · checked ${new Date(connection.last_verified_at).toLocaleDateString()}`}</span> : null}
            </div>
          </div>
          <div style={row}>
            <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => setEditing(true)}>Change details</button>
            <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => act(async () => {
              await apiRequest('POST', storagePath(kind, accountId, '/test'));
              return 'The bucket answered and the test file was written, read, and removed.';
            })}>
              Check connection
            </button>
          </div>
        </div>
      )}

      {(editing || !connection) && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '12px', marginBottom: '12px' }}>
          <label style={{ display: 'flex', flexDirection: 'column', gap: '4px', fontSize: '0.85rem', flex: '1 1 200px' }}>
            Storage provider
            <select className="search-input" value={draft.provider} onChange={e => setDraft(current => ({ ...current, provider: e.target.value as StorageProvider }))}>
              {STORAGE_PROVIDERS.map(item => (
                <option key={item.id} value={item.id}>{item.label}</option>
              ))}
            </select>
          </label>
          {draft.provider !== 'aws' && field('endpoint_url', 'Endpoint', chosen?.endpointHint)}
          {field('region', 'Region', draft.provider === 'aws' ? 'eu-west-1' : 'optional')}
          {field('bucket', 'Bucket name', 'my-review-files')}
          {field('key_prefix', 'Folder in the bucket (optional)', 'omnireview')}
          {field('access_key_id', 'Access key ID', connection ? 'Leave empty to keep the saved key' : '')}
          {field('secret_access_key', 'Secret access key', connection ? 'Leave empty to keep the saved key' : '', 'password')}
          <div style={{ ...row, flex: '1 1 100%' }}>
            <button className="btn-primary" style={smallButton} disabled={busy || !draft.bucket} onClick={save}>
              {connection ? 'Check and save' : 'Connect this bucket'}
            </button>
            {connection && <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => setEditing(false)}>Cancel</button>}
            <span style={muted}>The key needs permission to get, put, delete, and list objects in that folder.</span>
          </div>
        </div>
      )}

      {connection && !editing && (
        <div style={{ ...row, marginBottom: '8px' }}>
          <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => act(async () => {
            await apiRequest('PUT', storagePath(kind, accountId, '/use'), { use_for_new_files: !inBucket });
            return inBucket ? "New files will go to OmniReview's storage." : 'New files will go to your bucket.';
          })}>
            {inBucket ? "Use OmniReview's storage for new files" : 'Use my bucket for new files'}
          </button>
          <button className="btn-glass" style={smallButton} disabled={busy || !!connection.pending_move || settings?.files.platform === 0} onClick={() => act(async () => {
            await apiRequest('POST', storagePath(kind, accountId, '/move'), { direction: 'to_bucket' });
            return 'Moving your existing files into your bucket. This runs in the background.';
          })}>
            Move existing files to my bucket
          </button>
          <button className="btn-glass" style={smallButton} disabled={busy || !!connection.pending_move || settings?.files.bucket === 0} onClick={() => act(async () => {
            await apiRequest('POST', storagePath(kind, accountId, '/move'), { direction: 'to_platform' });
            return "Moving your files back into OmniReview's storage. This runs in the background.";
          })}>
            Move files back to OmniReview
          </button>
          <button className="btn-glass" style={smallButton} disabled={busy || !!connection.pending_move} onClick={() => {
            if (!window.confirm('Remove the bucket details? Files stored in it must be moved back first.')) return;
            act(async () => {
              await apiRequest('DELETE', storagePath(kind, accountId));
              setDraft(EMPTY);
              return 'The bucket details were removed.';
            });
          }}>
            Disconnect
          </button>
        </div>
      )}

      {connection?.pending_move && (
        <p role="status" style={{ ...muted, color: 'var(--accent-primary)' }}>
          {connection.pending_move === 'to_bucket' ? 'Files are being moved into your bucket.' : "Files are being moved back to OmniReview's storage."}
          {' '}Refresh in a moment to see the progress.
        </p>
      )}
      {connection?.move_error && <p role="alert" style={{ color: RED, fontSize: '0.85rem' }}>Moving files stopped: {connection.move_error}</p>}
      {notice && <p role="status" style={{ color: notice.tone === 'ok' ? GREEN : RED, fontSize: '0.85rem' }}>{notice.text}</p>}
    </section>
  );
}
