// Mirrors backend/ai_catalog.py and backend/ai_routes.py.

export type AiModelInfo = {
  id: number;
  provider: string;
  provider_label: string;
  model_id: string;
  label: string;
  is_default: boolean;
  enabled: boolean;
  data_location: string | null;
  // Present in the catalog listing: whether the signed-in user has a key (their own or the server's) for it.
  available?: boolean;
};

export type SavedApiKey = { provider: string; last_four: string; updated_at: string; last_verified_at: string | null };

export type AiProviderInfo = {
  id: string;
  label: string;
  data_location: string;
  platform_key_configured: boolean;
  user_key: SavedApiKey | null;
};

// `valid` is null when the provider failed for a reason other than the key.
export type KeyTestResult = { valid: boolean | null; message: string; key: SavedApiKey };

export const modelDisplayName = (model: AiModelInfo) => `${model.provider_label} ${model.label}`;
