// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { WorkflowStageInfo } from '../../api/review';
import { StageGate } from './StageGate';

const openStage: WorkflowStageInfo = {
  stage: 'search',
  label: 'Search and deduplication',
  status: 'open',
  requirements: [
    { label: 'At least one database search or file import', met: true },
    { label: 'Deduplication run on every record', met: false },
  ],
  completed_at: null,
  completed_by: null,
  completion_note: null,
  reopen_rationale: null,
  latest_version: null,
  latest_snapshot_id: null,
  can_manage: true,
};

afterEach(cleanup);

describe('StageGate', () => {
  it('lists requirements and blocks sign-off until all are met', () => {
    render(<StageGate stage={openStage} onComplete={vi.fn()} onReopen={vi.fn()} />);

    expect(screen.getByText(/Deduplication run on every record/)).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Sign off stage' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('signs off a ready stage', () => {
    const onComplete = vi.fn();
    const ready = { ...openStage, requirements: openStage.requirements.map(r => ({ ...r, met: true })) };
    render(<StageGate stage={ready} onComplete={onComplete} onReopen={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: 'Sign off stage' }));

    expect(onComplete).toHaveBeenCalledWith(ready);
  });

  it('offers reopening a signed-off stage only to roles that manage it', () => {
    const completed: WorkflowStageInfo = { ...openStage, status: 'completed', requirements: [], completed_by: 'Liam Lead', completion_note: 'Deduplicated', latest_version: 2 };
    const { rerender } = render(<StageGate stage={completed} onComplete={vi.fn()} onReopen={vi.fn()} />);

    expect(screen.getByText(/version 2/)).toBeTruthy();
    expect(screen.getByText(/Signed off by Liam Lead/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Reopen' })).toBeTruthy();

    rerender(<StageGate stage={{ ...completed, can_manage: false }} onComplete={vi.fn()} onReopen={vi.fn()} />);
    expect(screen.queryByRole('button', { name: 'Reopen' })).toBeNull();
  });

  it('explains that a not-started stage waits on earlier stages', () => {
    render(<StageGate stage={{ ...openStage, status: 'not_started', requirements: [] }} onComplete={vi.fn()} onReopen={vi.fn()} />);

    expect(screen.getByText(/Sign off the earlier stages/)).toBeTruthy();
    expect(screen.queryByRole('button')).toBeNull();
  });
});
