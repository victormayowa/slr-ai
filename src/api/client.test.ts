import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, errorDetail, errorMessage, requestJson } from './client';

afterEach(() => vi.unstubAllGlobals());

describe('errorDetail', () => {
  it('uses a plain detail message from the API', () => {
    expect(errorDetail({ detail: 'Invalid credentials' }, 401)).toBe('Invalid credentials');
  });

  it('joins validation error messages', () => {
    expect(errorDetail({ detail: [{ msg: 'Field required' }, { msg: 'Too long' }] }, 422)).toBe('Field required; Too long');
  });

  it('falls back to the status code when there is no detail', () => {
    expect(errorDetail({}, 500)).toBe('Request failed (500)');
  });
});

describe('errorMessage', () => {
  it('shows messages from the API', () => {
    expect(errorMessage(new ApiError('OPENAI_API_KEY is not configured on the server'), 'fallback')).toBe(
      'OPENAI_API_KEY is not configured on the server',
    );
  });

  it('uses the fallback for unexpected errors', () => {
    expect(errorMessage(new TypeError('Failed to fetch'), 'Network error')).toBe('Network error');
  });
});

describe('requestJson', () => {
  it('sends the token and JSON body, and returns the parsed response', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ id: 7 }), { status: 201 }));
    vi.stubGlobal('fetch', fetchMock);

    const data = await requestJson('POST', '/api/projects', { title: 'Aspirin review' }, 'secret-token');

    expect(data).toEqual({ id: 7 });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/api\/projects$/);
    expect(init.headers).toMatchObject({ Authorization: 'Bearer secret-token' });
    expect(init.body).toBe('{"title":"Aspirin review"}');
  });

  it('throws ApiError with the status and the API message', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'Project not found' }), { status: 404 })));

    const error = await requestJson('GET', '/api/projects/9').catch(err => err);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(404);
    expect((error as ApiError).message).toBe('Project not found');
  });

  it('returns null for empty 204 responses', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 204 })));

    expect(await requestJson('DELETE', '/api/projects/9/records', undefined, 'token')).toBeNull();
  });
});
