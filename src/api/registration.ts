// Mirrors backend/registration_routes.py and backend/protocol_export.py.

export type ProsperoField = { field: string; value: string; source: string; ready: boolean };

export type ProtocolRegistration = {
  id: number;
  registry: string;
  status: 'deposited' | 'submitted' | 'registered' | 'waived';
  registration_id: string;
  url: string | null;
  protocol_version: number;
  latest_protocol_version: number;
  waiver_reason: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
};

export const REGISTRATION_STATUS_LABELS: Record<ProtocolRegistration['status'], string> = {
  deposited: 'Files deposited, not yet registered',
  submitted: 'Submitted',
  registered: 'Registered',
  waived: 'Registration waived',
};
