export type ProjectSummary = {
  id: number;
  title: string;
  description: string | null;
  role: string;
  member_count: number;
  organization: { id: number; name: string } | null;
};

export type ProjectMemberInfo = { user_id: number; name: string; email: string; role: string };

// Mirrors backend/permissions.py. The server enforces permissions; these only shape the interface.
export const PROJECT_ROLE_LABELS: Record<string, string> = {
  owner: 'Owner',
  lead_reviewer: 'Lead reviewer',
  methodologist: 'Methodologist',
  statistician: 'Statistician',
  clinical_expert: 'Clinical expert',
  screener: 'Screener',
  extractor: 'Extractor',
  auditor: 'Auditor',
  viewer: 'Viewer',
};

export const canManageMembers = (role: string) => role === 'owner' || role === 'lead_reviewer';
