// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { StorageSettings } from '../../api/storage';
import { AuthContext } from '../../auth/authContext';
import { StoragePanel } from './StoragePanel';

const connected: StorageSettings = {
  feature_allowed: true,
  files: { platform: 2, bucket: 5 },
  connection: {
    provider: 'b2', endpoint_url: 'https://s3.eu-central-003.backblazeb2.com', region: 'eu-central-003',
    bucket: 'review-files', key_prefix: 'omnireview', access_key_last_four: '7788', use_for_new_files: true,
    pending_move: null, move_error: '', last_verified_at: '2026-09-17T10:00:00Z', updated_at: '2026-09-17T10:00:00Z',
  },
};

function renderPanel(apiRequest: (method: string, path: string, body?: unknown) => Promise<unknown>) {
  const auth = { token: 't', userName: 'Liam Lead', signIn: vi.fn(), signOut: vi.fn(), apiRequest: vi.fn(apiRequest) };
  render(
    <AuthContext.Provider value={auth}>
      <StoragePanel kind="user" accountId={12} />
    </AuthContext.Provider>,
  );
  return auth.apiRequest;
}

afterEach(cleanup);

describe('StoragePanel', () => {
  it('connects a bucket without showing the secret again', async () => {
    const empty: StorageSettings = { feature_allowed: true, files: { platform: 3, bucket: 0 }, connection: null };
    let saved: Record<string, unknown> | null = null;
    const apiRequest = renderPanel(async (method, _path, body) => {
      if (method === 'PUT') {
        saved = body as Record<string, unknown>;
        return connected;
      }
      return saved ? connected : empty;
    });

    await screen.findByText(/3 file\(s\) in OmniReview's storage/);
    fireEvent.change(screen.getByLabelText('Bucket name'), { target: { value: 'review-files' } });
    fireEvent.change(screen.getByLabelText('Access key ID'), { target: { value: 'AKIAEXAMPLE' } });
    fireEvent.change(screen.getByLabelText('Secret access key'), { target: { value: 'secret-value-1234' } });
    fireEvent.click(screen.getByRole('button', { name: 'Connect this bucket' }));

    await waitFor(() => expect(saved).toEqual({
      provider: 'aws', endpoint_url: '', region: '', bucket: 'review-files', key_prefix: '',
      use_for_new_files: true, access_key_id: 'AKIAEXAMPLE', secret_access_key: 'secret-value-1234',
    }));
    expect(apiRequest).toHaveBeenCalledWith('GET', '/api/storage/user/12');
    await screen.findByText(/Bucket review-files/);
    expect(screen.queryByDisplayValue('secret-value-1234')).toBeNull();
  });

  it('shows where files are and switches where new ones go', async () => {
    const apiRequest = renderPanel(async () => connected);

    expect(await screen.findByText(/2 file\(s\) in OmniReview's storage, 5 in your bucket/)).toBeTruthy();
    expect(screen.getByText('New files go to your bucket')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: "Use OmniReview's storage for new files" }));

    await waitFor(() =>
      expect(apiRequest).toHaveBeenCalledWith('PUT', '/api/storage/user/12/use', { use_for_new_files: false }),
    );
  });

  it('says when the plan does not include it', async () => {
    renderPanel(async () => ({ feature_allowed: false, files: { platform: 0, bucket: 0 }, connection: null }));

    expect(await screen.findByText(/plan doesn't include storing files in your own bucket/)).toBeTruthy();
  });
});
