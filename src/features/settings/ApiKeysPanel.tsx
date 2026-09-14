import { useEffect, useState } from 'react';
import type { AiProviderInfo, KeyTestResult } from '../../api/ai';
import { errorMessage } from '../../api/client';
import { useAuth } from '../../auth/authContext';

type Notice = { tone: 'ok' | 'error' | 'info'; text: string };

const TONE_COLORS: Record<Notice['tone'], string> = { ok: '#10b981', error: '#ef4444', info: 'var(--text-secondary)' };

// Lets users add their own key per AI provider. Saved keys are encrypted by the server and never sent back.
export function ApiKeysPanel() {
  const { apiRequest } = useAuth();
  const [providers, setProviders] = useState<AiProviderInfo[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [notices, setNotices] = useState<Record<string, Notice>>({});
  const [busyProvider, setBusyProvider] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiRequest('GET', '/api/ai/providers')
      .then(data => {
        if (!cancelled) setProviders(data);
      })
      .catch(err => {
        if (!cancelled) setLoadError(errorMessage(err, 'Could not load the AI providers.'));
      });
    return () => {
      cancelled = true;
    };
  }, [apiRequest]);

  const act = async (provider: AiProviderInfo, action: () => Promise<Notice>) => {
    setBusyProvider(provider.id);
    let notice: Notice;
    try {
      notice = await action();
      setProviders(await apiRequest('GET', '/api/ai/providers'));
    } catch (err) {
      notice = { tone: 'error', text: errorMessage(err, 'Something went wrong. Please try again.') };
    }
    setNotices(prev => ({ ...prev, [provider.id]: notice }));
    setBusyProvider(null);
  };

  const save = (provider: AiProviderInfo) =>
    act(provider, async () => {
      await apiRequest('PUT', `/api/me/api-keys/${provider.id}`, { api_key: (drafts[provider.id] ?? '').trim() });
      setDrafts(prev => ({ ...prev, [provider.id]: '' }));
      return { tone: 'ok', text: 'Key saved. Use "Check key" to confirm the provider accepts it.' };
    });

  const check = (provider: AiProviderInfo) =>
    act(provider, async () => {
      const result: KeyTestResult = await apiRequest('POST', `/api/me/api-keys/${provider.id}/test`);
      return { tone: result.valid ? 'ok' : result.valid === false ? 'error' : 'info', text: result.message };
    });

  const remove = (provider: AiProviderInfo) => {
    if (!window.confirm(`Remove your ${provider.label} key?`)) return;
    return act(provider, async () => {
      await apiRequest('DELETE', `/api/me/api-keys/${provider.id}`);
      return { tone: 'info', text: 'Key removed.' };
    });
  };

  const keyStatus = (provider: AiProviderInfo) => {
    if (provider.user_key) {
      const checked = provider.user_key.last_verified_at ? ', checked' : '';
      return `Your key ending in ${provider.user_key.last_four}${checked}`;
    }
    return provider.platform_key_configured ? 'Using the server key' : 'No key available';
  };

  return (
    <section aria-labelledby="api-keys-heading">
      <h3 id="api-keys-heading" style={{ borderBottom: '1px solid rgba(255,255,255,0.1)', paddingBottom: '12px', marginBottom: '16px' }}>AI Provider Keys</h3>
      <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '8px' }}>
        Add your own key to use a provider this server doesn't offer, or to use your own account and limits. Keys are
        encrypted on the server, used only for AI tasks you start, and never shown again after saving.
      </p>
      {loadError && <p role="alert" style={{ color: '#ef4444' }}>{loadError}</p>}
      {!providers && !loadError && <p style={{ color: 'var(--text-secondary)' }}>Loading providers…</p>}
      {providers?.map(provider => {
        const draft = drafts[provider.id] ?? '';
        const busy = busyProvider === provider.id;
        const notice = notices[provider.id];
        return (
          <div key={provider.id} role="group" aria-label={provider.label} style={{ padding: '16px 0', borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap' }}>
              <strong>{provider.label}</strong>
              <span style={{ fontSize: '0.8rem', color: provider.user_key || provider.platform_key_configured ? '#10b981' : 'var(--text-secondary)' }}>{keyStatus(provider)}</span>
            </div>
            <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', margin: '4px 0 8px' }}>Review content is processed by {provider.data_location}.</div>
            <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
              <input
                type="password"
                autoComplete="off"
                aria-label={`${provider.label} API key`}
                placeholder={provider.user_key ? 'Paste a new key to replace yours' : 'Paste your API key'}
                className="search-input"
                style={{ flex: '1 1 220px', padding: '10px' }}
                value={draft}
                onChange={e => setDrafts(prev => ({ ...prev, [provider.id]: e.target.value }))}
              />
              <button className="btn-primary" disabled={busy || draft.trim().length < 8} onClick={() => save(provider)} style={{ padding: '0 16px' }}>Save</button>
              {provider.user_key && <button className="btn-glass" disabled={busy} onClick={() => check(provider)} style={{ padding: '0 16px' }}>Check key</button>}
              {provider.user_key && <button className="btn-glass" disabled={busy} onClick={() => remove(provider)} style={{ padding: '0 16px' }}>Remove</button>}
            </div>
            {notice && <p role="status" style={{ color: TONE_COLORS[notice.tone], fontSize: '0.85rem', margin: '8px 0 0' }}>{notice.text}</p>}
          </div>
        );
      })}
    </section>
  );
}
