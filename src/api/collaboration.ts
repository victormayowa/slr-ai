// Mirrors backend/collaboration_routes.py, backend/governance_routes.py, and backend/tokens_routes.py.

export type InvitationInfo = {
  id: number;
  email: string;
  role: string;
  status: 'open' | 'accepted' | 'revoked' | 'expired';
  invited_by: string | null;
  expires_at: string;
  accepted_at: string | null;
  created_at: string;
  // Returned once, when the invitation is created.
  link?: string;
};

export type DeclarationInfo = {
  user_id: number;
  name: string;
  role: string;
  has_competing_interests: boolean | null;
  statement: string;
  funding: string;
  updated_at: string | null;
};

export type TaskInfo = {
  id: number;
  title: string;
  description: string;
  stage: string;
  assignee_id: number | null;
  assignee: string | null;
  due_on: string | null;
  status: 'open' | 'in_progress' | 'done';
  priority: 'low' | 'normal' | 'high';
  anchor_key: string;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type CommentInfo = {
  id: number;
  anchor_key: string;
  anchor_label: string;
  parent_id: number | null;
  body: string;
  deleted: boolean;
  mentions: number[];
  author_id: number | null;
  author: string | null;
  resolved: boolean;
  resolved_at: string | null;
  edited_at: string | null;
  created_at: string;
};

export type CommentCounts = Record<string, { comments: number; unresolved: number }>;

export type NotificationInfo = {
  id: number;
  project_id: number | null;
  kind: string;
  title: string;
  body: string;
  link: string;
  read_at: string | null;
  created_at: string;
};

export type NotificationList = { unread: number; notifications: NotificationInfo[] };

export type WorkloadRow = {
  user_id: number;
  name: string;
  role: string;
  title_abstract_decisions: number;
  full_text_decisions: number;
  extraction_values: number;
  appraisals_signed_off: number;
  open_tasks: number;
  overdue_tasks: number;
};

export type ReviewerRow = {
  user_id: number;
  name: string;
  decisions: number;
  agreed_with_final: number;
  compared_with_final: number;
  differed_from_ai: number;
  ai_seen: number;
  agreement_with_final: number | null;
  override_rate: number | null;
};

export type PairAgreement = {
  reviewers: string[];
  n: number;
  observed: number | null;
  kappa: number | null;
  pabak: number | null;
};

export type TeamMetrics = { reviewers: ReviewerRow[]; pairwise_agreement: PairAgreement[] };

export type HelpSectionInfo = { id: string; page: string; title: string; heading: string; anchor: string; text?: string };

// --- Governance ---

export type CalibrationItem = {
  record_id: number;
  human: string;
  ai: string | null;
  confidence?: number | null;
  error?: string | null;
};

export type CalibrationReportInfo = {
  id: number;
  stage: string;
  ai_model_id: number | null;
  model: string;
  prompt_version: string;
  sample_size: number;
  seed: number;
  status: 'running' | 'completed' | 'failed';
  metrics: {
    compared: number;
    failed: number;
    includes: number;
    excludes: number;
    recall: number | null;
    recall_ci: [number, number] | null;
    specificity: number | null;
    agreement: number | null;
  };
  thresholds: Record<string, number>;
  passed: boolean | null;
  error: string | null;
  created_at: string;
  accepted_at: string | null;
  acceptance_note: string;
  items?: CalibrationItem[];
};

export type CalibrationList = {
  reports: CalibrationReportInfo[];
  require_ai_calibration: boolean;
  recall_target: number;
  current_prompt_version: string;
};

export type BiasGroup = {
  field: string;
  retrieved: { value: string; count: number; share: number }[];
  included: { value: string; count: number; share: number }[];
};

export type BiasReport = {
  records: number;
  included: number;
  groups: BiasGroup[];
  ai_errors_by_year: { year: string; compared: number; missed: number; over_included: number }[];
  mean_ai_confidence: number | null;
  note: string;
};

export type ReproducibilityCheckInfo = {
  id: number;
  run_id: number;
  status: 'identical' | 'within_tolerance' | 'different' | 'failed';
  max_abs_difference: number | null;
  differences: { path: string; original: number; repeated: number | null; difference?: number }[];
  r_version: string;
  error: string | null;
  created_at: string;
};

export type SopReport = {
  project: { id: number; title: string };
  generated_at: string;
  settings: Record<string, unknown>;
  stages: { stage: string; label: string; completed_at: string | null; completed_by: string | null; note: string | null }[];
};

export type BenchmarkRunInfo = {
  id: number;
  task: string;
  dataset_key: string;
  dataset_name: string;
  ai_model_id: number | null;
  model: string;
  prompt_version: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  items: number;
  processed: number;
  metrics: Record<string, number | null>;
  thresholds: Record<string, number>;
  passed: boolean | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
};

export type BenchmarkDataset = {
  key: string;
  name: string;
  task: string;
  description: string;
  source: string;
  license: string;
  items: number;
  sha256: string;
};

// --- API tokens ---

export type ApiTokenInfo = {
  id: number;
  name: string;
  prefix: string;
  scopes: string[];
  project_ids: number[];
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  created_at: string;
  // Returned once, when the token is created.
  token?: string;
};

export type WebhookInfo = {
  id: number;
  url: string;
  events: string[];
  active: boolean;
  failure_count: number;
  last_delivery_at: string | null;
  created_at: string;
  // Returned once, when the webhook is created.
  secret?: string;
};

export type WebhookDeliveryInfo = {
  id: number;
  subscription_id: number;
  action: string;
  status: 'pending' | 'delivered' | 'failed';
  attempts: number;
  response_status: number | null;
  error: string | null;
  next_attempt_at: string;
  delivered_at: string | null;
  created_at: string;
};
