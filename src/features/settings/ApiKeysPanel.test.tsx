// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { AiProviderInfo } from '../../api/ai';
import { AuthContext } from '../../auth/authContext';
import { ApiKeysPanel } from './ApiKeysPanel';

const provider = (id: string, label: string, overrides: Partial<AiProviderInfo> = {}): AiProviderInfo => ({
  id, label, data_location: `${label} Inc., United States`, platform_key_configured: false, user_key: null, ...overrides,
});

function renderPanel(apiRequest: (method: string, path: string, body?: unknown) => Promise<unknown>) {
  const auth = { token: 't', userName: 'Liam Lead', signIn: vi.fn(), signOut: vi.fn(), apiRequest: vi.fn(apiRequest) };
  render(
    <AuthContext.Provider value={auth}>
      <ApiKeysPanel />
    </AuthContext.Provider>,
  );
  return auth.apiRequest;
}

afterEach(cleanup);

describe('ApiKeysPanel', () => {
  it('shows where each provider processes data and which key is used', async () => {
    renderPanel(async () => [
      provider('gemini', 'Google Gemini', { platform_key_configured: true }),
      provider('openai', 'OpenAI', { user_key: { provider: 'openai', last_four: 'a1b2', updated_at: '', last_verified_at: null } }),
      provider('mistral', 'Mistral AI'),
    ]);

    const gemini = await screen.findByRole('group', { name: 'Google Gemini' });
    expect(within(gemini).getByText('Using the server key')).toBeTruthy();
    expect(within(gemini).getByText(/processed by Google Gemini Inc\., United States/)).toBeTruthy();
    expect(within(screen.getByRole('group', { name: 'OpenAI' })).getByText('Your key ending in a1b2')).toBeTruthy();
    expect(within(screen.getByRole('group', { name: 'Mistral AI' })).getByText('No key available')).toBeTruthy();
  });

  it('saves a key without ever displaying it', async () => {
    let saved = false;
    const apiRequest = renderPanel(async (method, path) => {
      if (method === 'PUT') {
        saved = true;
        return { provider: 'mistral', last_four: '9999', updated_at: '', last_verified_at: null };
      }
      if (path === '/api/ai/providers') {
        const user_key = saved ? { provider: 'mistral', last_four: '9999', updated_at: '', last_verified_at: null } : null;
        return [provider('mistral', 'Mistral AI', { user_key })];
      }
      throw new Error(`Unexpected ${method} ${path}`);
    });

    const input = await screen.findByLabelText('Mistral AI API key');
    fireEvent.change(input, { target: { value: 'mistral-secret-key-9999' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByText('Your key ending in 9999')).toBeTruthy();
    expect(apiRequest).toHaveBeenCalledWith('PUT', '/api/me/api-keys/mistral', { api_key: 'mistral-secret-key-9999' });
    expect((input as HTMLInputElement).value).toBe('');
    expect(document.body.textContent).not.toContain('mistral-secret-key-9999');
  });

  it('reports a key the provider rejects', async () => {
    const key = { provider: 'kimi', last_four: '0001', updated_at: '', last_verified_at: null };
    renderPanel(async (method, path) => {
      if (method === 'POST') return { valid: false, message: 'Moonshot Kimi rejected the API key.', key };
      if (path === '/api/ai/providers') return [provider('kimi', 'Moonshot Kimi', { user_key: key })];
      throw new Error(`Unexpected ${method} ${path}`);
    });

    fireEvent.click(await screen.findByRole('button', { name: 'Check key' }));

    expect((await screen.findByRole('status')).textContent).toBe('Moonshot Kimi rejected the API key.');
  });
});
