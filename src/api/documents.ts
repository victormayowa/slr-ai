import { API_BASE, ApiError, errorDetail } from './client';
import type { RecordBrief } from './review';

// Mirrors backend/documents_routes.py.

export type DocumentInfo = {
  id: number;
  record_id: number;
  role: 'full_text' | 'supplement';
  origin: 'europepmc' | 'unpaywall' | 'upload';
  source_url: string;
  file_name: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
  license: string;
  oa_status: string;
  version: string;
  // "failed" files (for example scanned PDFs) and "unsupported" ones are kept, with the reason in parse_error.
  parse_status: 'pending' | 'parsed' | 'failed' | 'unsupported';
  parse_error: string | null;
  parser: string;
  page_count: number | null;
  span_count: number | null;
  uploaded_by: string | null;
  created_at: string;
  parsed_at: string | null;
};

export type DocumentSpan = {
  id: number;
  position: number;
  kind: 'title' | 'abstract' | 'heading' | 'paragraph' | 'table' | 'caption' | 'reference';
  section: string;
  page: number | null;
  label: string;
  text: string;
  start: number;
  end: number;
};

export type DocumentDetail = DocumentInfo & { spans: DocumentSpan[] };

export type RetrievalAttempt = {
  source: 'europepmc' | 'unpaywall';
  outcome: 'found' | 'not_found' | 'skipped' | 'error';
  detail: string;
};

export type RetrievalInfo = {
  id: number;
  record_id: number;
  status: 'found' | 'already_stored' | 'not_found';
  attempts: RetrievalAttempt[];
  document_id: number | null;
  requested_by: string | null;
  created_at: string;
};

export type FullTextRow = {
  record: RecordBrief;
  final_decision: string | null;
  documents: DocumentInfo[];
  latest_retrieval: RetrievalInfo | null;
};

export type FullTextOverview = {
  records: FullTextRow[];
  counts: { sought: number; retrieved: number; not_retrieved: number };
  max_document_bytes: number;
  unpaywall_configured: boolean;
};

export const ORIGIN_LABELS: Record<string, string> = { europepmc: 'Europe PMC', unpaywall: 'Unpaywall', upload: 'Uploaded' };

export const hasFullText = (row: FullTextRow) => row.documents.some(doc => doc.role === 'full_text');

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// Downloads a stored file with the user's token and hands it to the browser to save.
export async function downloadDocument(token: string | null, projectId: number, doc: DocumentInfo): Promise<void> {
  const res = await fetch(`${API_BASE}/api/projects/${projectId}/documents/${doc.id}/file`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new ApiError(errorDetail(data, res.status), res.status);
  }
  const href = URL.createObjectURL(await res.blob());
  const link = Object.assign(document.createElement('a'), { href, download: doc.file_name });
  link.click();
  setTimeout(() => URL.revokeObjectURL(href), 60_000);
}
