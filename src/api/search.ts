import type { RecordBrief } from './review';

// Mirrors backend/search_sources.py and the search, import, and duplicate routes in backend/records_routes.py.

export type ConnectorInfo = {
  key: string;
  label: string;
  interface: string;
  kind: 'database' | 'register';
  aliases: string[];
  syntax_note: string;
};

export type ImportOnlySource = { label: string; interface: string; export_hint: string; aliases: string[] };

export type SearchSources = {
  connectors: ConnectorInfo[];
  import_only: ImportOnlySource[];
  import_formats: string;
  max_results: number;
  grey_literature_types?: { key: string; label: string }[];
};

export type SearchRunInfo = {
  id: number;
  kind: 'database' | 'register' | 'other' | 'import' | 'citation';
  database: string;
  source: string;
  query: string | null;
  result_count: number;
  total_available: number | null;
  connector: string | null;
  interface: string | null;
  searched_on: string | null;
  file_format: string | null;
  filters: Record<string, unknown>;
  executed_at: string;
};

export type DuplicateCandidate = { record: RecordBrief; other: RecordBrief; score: number; reasons: string[] };

export const normalizeDatabaseName = (name: string) => name.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();

export const matchConnector = (sources: SearchSources | null, database: string) =>
  sources?.connectors.find(connector => connector.aliases.includes(normalizeDatabaseName(database))) ?? null;

export const matchImportOnly = (sources: SearchSources | null, database: string) =>
  sources?.import_only.find(source => source.aliases.includes(normalizeDatabaseName(database))) ?? null;

// Possible duplicates: `pairs` is the batch this plan reviews at a time, `total` is everything still waiting.
export type DuplicateCandidateList = { pairs: DuplicateCandidate[]; total: number; batch: number | null };
