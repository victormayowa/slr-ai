import type { WorkflowStageInfo } from '../../api/review';

type StageGateProps = {
  stage?: WorkflowStageInfo;
  busy?: boolean;
  onComplete: (stage: WorkflowStageInfo) => void;
  onReopen: (stage: WorkflowStageInfo) => void;
};

const STATUS_DISPLAY = {
  open: { label: 'Open', color: '#3b82f6' },
  completed: { label: 'Signed off', color: '#10b981' },
  not_started: { label: 'Not started', color: '#9ca3af' },
} as const;

// Shows a workflow stage's sign-off state and requirements, with the actions the viewer's role allows.
export function StageGate({ stage, busy = false, onComplete, onReopen }: StageGateProps) {
  if (!stage) return null;
  const display = STATUS_DISPLAY[stage.status];
  const ready = stage.requirements.every(requirement => requirement.met);
  const secondaryText = { margin: '8px 0 0', color: 'var(--text-secondary)', fontSize: '0.9rem' };

  return (
    <section aria-label={`${stage.label} sign-off`} style={{ border: `1px solid ${display.color}66`, background: 'rgba(0,0,0,0.2)', borderRadius: '12px', padding: '16px', marginBottom: '24px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
        <div>
          <strong>{stage.label}</strong>
          <span style={{ color: display.color, fontSize: '0.85rem', marginLeft: '8px' }}>
            {display.label}{stage.latest_version ? ` · version ${stage.latest_version}` : ''}
          </span>
        </div>
        {stage.can_manage && stage.status === 'open' && (
          <button className="btn-primary" disabled={busy || !ready} onClick={() => onComplete(stage)} style={{ padding: '8px 16px' }}>
            Sign off stage
          </button>
        )}
        {stage.can_manage && stage.status === 'completed' && (
          <button className="btn-glass" disabled={busy} onClick={() => onReopen(stage)} style={{ padding: '8px 16px' }}>
            Reopen
          </button>
        )}
      </div>

      {stage.status === 'completed' && (
        <p style={secondaryText}>
          Signed off{stage.completed_by ? ` by ${stage.completed_by}` : ''}
          {stage.completed_at ? ` on ${new Date(stage.completed_at).toLocaleString()}` : ''}
          {stage.completion_note ? `: "${stage.completion_note}"` : ''}. Changes are locked until the stage is reopened.
        </p>
      )}
      {stage.status === 'not_started' && <p style={secondaryText}>Sign off the earlier stages before working here.</p>}
      {stage.status === 'open' && stage.reopen_rationale && <p style={secondaryText}>Reopened: {stage.reopen_rationale}</p>}
      {stage.status === 'open' && stage.requirements.length > 0 && (
        <ul style={{ listStyle: 'none', padding: 0, margin: '12px 0 0' }}>
          {stage.requirements.map(requirement => (
            <li key={requirement.label} style={{ color: requirement.met ? '#10b981' : 'var(--text-secondary)', fontSize: '0.9rem' }}>
              {requirement.met ? '✓' : '○'} {requirement.label}
            </li>
          ))}
        </ul>
      )}
      {stage.status === 'open' && !stage.can_manage && (
        <p style={secondaryText}>Your role can work in this stage, but a lead reviewer or methodologist signs it off.</p>
      )}
    </section>
  );
}
