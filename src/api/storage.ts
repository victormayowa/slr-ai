// Where an account's files are kept: OmniReview's storage, or the account's own S3-compatible bucket.

export type StorageProvider = 'aws' | 'b2' | 'r2' | 'wasabi' | 'minio' | 'other';

export type StorageConnection = {
  provider: StorageProvider;
  endpoint_url: string;
  region: string;
  bucket: string;
  key_prefix: string;
  access_key_last_four: string;
  use_for_new_files: boolean;
  pending_move: 'to_bucket' | 'to_platform' | null;
  move_error: string;
  last_verified_at: string | null;
  updated_at: string;
};

export type StorageSettings = {
  feature_allowed: boolean;
  files: { platform: number; bucket: number };
  connection: StorageConnection | null;
};

// What each provider calls itself, and the endpoint it expects. AWS S3 is addressed by region instead.
export const STORAGE_PROVIDERS: { id: StorageProvider; label: string; endpointHint: string }[] = [
  { id: 'aws', label: 'Amazon S3', endpointHint: '' },
  { id: 'b2', label: 'Backblaze B2', endpointHint: 'https://s3.<region>.backblazeb2.com' },
  { id: 'r2', label: 'Cloudflare R2', endpointHint: 'https://<account id>.r2.cloudflarestorage.com' },
  { id: 'wasabi', label: 'Wasabi', endpointHint: 'https://s3.<region>.wasabisys.com' },
  { id: 'minio', label: 'MinIO', endpointHint: 'https://minio.example.org' },
  { id: 'other', label: 'Other S3-compatible storage', endpointHint: 'https://storage.example.org' },
];

export const storagePath = (kind: string, id: number, suffix = '') => `/api/storage/${kind}/${id}${suffix}`;
