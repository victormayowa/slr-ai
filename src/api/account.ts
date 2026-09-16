// Mirrors backend/billing_routes.py, backend/account_routes.py, and backend/admin_console_routes.py.

export type PlanLimits = {
  projects?: number | null;
  members_per_project?: number | null;
  records_per_month?: number | null;
  ai_credits_per_month?: number | null;
  storage_mb?: number | null;
  living_schedules?: number | null;
  compute_minutes_per_month?: number | null;
  api_access?: boolean;
  webhooks?: boolean;
};

export type PlanInfo = {
  code: string;
  name: string;
  description: string;
  monthly_price_cents: number | null;
  yearly_price_cents: number | null;
  currency: string;
  limits: PlanLimits;
  online_intervals: string[];
};

export type BillingConfig = {
  enabled: boolean;
  provider: string;
  provider_label: string;
  checkout_available: boolean;
  limit_labels: Record<string, string>;
  feature_labels: Record<string, string>;
};

export type PublicPlans = BillingConfig & { plans: PlanInfo[] };

export type BillingAccountSummary = { kind: 'user' | 'organization'; id: number; label: string; plan: string | null };

export type BillingAccount = BillingConfig & {
  kind: 'user' | 'organization';
  id: number;
  label: string;
  plan: PlanInfo | null;
  subscription: {
    plan_code: string;
    status: string;
    interval: string;
    current_period_end: string | null;
    cancel_at_period_end: boolean;
    provider: string;
  } | null;
  usage: Record<string, number>;
  usage_resets_on: string;
};

export type SecuritySummary = {
  email: string;
  email_verified: boolean;
  mfa_enabled: boolean;
  recovery_codes_left: number;
  password_changed_at: string | null;
  terms_version: string;
  terms_accepted_at: string | null;
  current_terms_version: string;
  deletion_requested_at: string | null;
  deletion_due_at: string | null;
};

export type MeInfo = {
  id: number;
  email: string;
  name: string;
  is_platform_admin: boolean;
  email_verified: boolean;
  mfa_enabled: boolean;
  terms_version: string;
  current_terms_version: string;
  organizations: { id: number; name: string; role: string }[];
};

export type LegalDocument = { slug: string; title: string; version: string; content?: string };

export type HealthCheck = { name: string; status: 'ok' | 'warn' | 'fail'; detail: string };

export const LIMIT_ORDER = [
  'projects',
  'members_per_project',
  'records_per_month',
  'ai_credits_per_month',
  'storage_mb',
  'living_schedules',
  'compute_minutes_per_month',
] as const;

export const price = (cents: number | null, currency: string) =>
  cents === null ? null : cents === 0 ? 'Free' : new Intl.NumberFormat(undefined, { style: 'currency', currency, maximumFractionDigits: 0 }).format(cents / 100);

export const limitText = (value: number | null | undefined) => (value === null || value === undefined ? 'Unlimited' : value.toLocaleString());
