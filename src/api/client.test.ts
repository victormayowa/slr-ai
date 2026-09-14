import { describe, expect, it } from 'vitest';
import { ApiError, errorDetail, errorMessage } from './client';

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
