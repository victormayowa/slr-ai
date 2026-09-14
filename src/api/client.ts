export const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

// Carries a message from the API that is safe to show the user, plus the HTTP status.
export class ApiError extends Error {
  status: number;

  constructor(message: string, status = 0) {
    super(message);
    this.status = status;
  }
}

export const errorDetail = (data: any, status: number): string => {
  if (typeof data?.detail === 'string') return data.detail;
  if (Array.isArray(data?.detail)) return data.detail.map((d: any) => d.msg).join('; ');
  return `Request failed (${status})`;
};

export const errorMessage = (err: unknown, fallback: string) => (err instanceof ApiError ? err.message : fallback);

// Sends a JSON request to the API and returns the parsed response, throwing ApiError for non-2xx responses.
export async function requestJson(method: string, path: string, body?: unknown, token?: string | null): Promise<any> {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = res.status === 204 ? null : await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(errorDetail(data, res.status), res.status);
  return data;
}
