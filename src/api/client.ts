export const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

// Carries a message from the API that is safe to show the user.
export class ApiError extends Error {}

export const errorDetail = (data: any, status: number): string => {
  if (typeof data?.detail === 'string') return data.detail;
  if (Array.isArray(data?.detail)) return data.detail.map((d: any) => d.msg).join('; ');
  return `Request failed (${status})`;
};

export const errorMessage = (err: unknown, fallback: string) => (err instanceof ApiError ? err.message : fallback);
