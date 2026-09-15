import { API_BASE, ApiError, errorDetail } from './client';

// Fetches a file from the API with the user's token.
export async function fetchBlob(token: string | null, path: string): Promise<Blob> {
  const res = await fetch(`${API_BASE}${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new ApiError(errorDetail(data, res.status), res.status);
  }
  return res.blob();
}

// Hands a blob to the browser to save under the file name.
export function saveBlob(blob: Blob, fileName: string) {
  const href = URL.createObjectURL(blob);
  const link = Object.assign(document.createElement('a'), { href, download: fileName });
  link.click();
  setTimeout(() => URL.revokeObjectURL(href), 60_000);
}

export async function downloadFile(token: string | null, path: string, fileName: string) {
  saveBlob(await fetchBlob(token, path), fileName);
}
