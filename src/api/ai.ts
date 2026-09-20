// Mirrors backend/ai_catalog.py and backend/ai_routes.py.

export type AiModelInfo = {
  id: number;
  provider: string;
  provider_label: string;
  model_id: string;
  label: string;
  purpose: 'chat' | 'embedding';
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
  own_key_required: boolean;
  user_key: SavedApiKey | null;
};

// `valid` is null when the provider failed for a reason other than the key.
export type KeyTestResult = { valid: boolean | null; message: string; key: SavedApiKey };

export const modelDisplayName = (model: AiModelInfo) => `${model.provider_label} ${model.label}`;

// Which API keys the person's AI tasks run on.
export type AiKeyMode = 'auto' | 'own' | 'platform';

export type AiPreferences = { ai_key_mode: AiKeyMode; platform_keys_enabled: boolean };

export const KEY_MODE_LABELS: { id: AiKeyMode; title: string; detail: string }[] = [
  { id: 'auto', title: 'Automatic', detail: "Your own key when you've added one for the provider, otherwise the AI included in your plan." },
  { id: 'own', title: 'Only my own keys', detail: 'AI tasks always run on your keys, and stop if you have no key for the provider.' },
  { id: 'platform', title: "Only the plan's included AI", detail: 'Your saved keys are left unused, and AI work counts against your plan credits.' },
];
