import { describe, expect, it, vi } from 'vitest';
import { jobProblem, jobProgress, waitForJob, type AiJob } from './jobs';

const job = (overrides: Partial<AiJob> = {}): AiJob => ({
  id: 3, task: 'screening', status: 'queued', total: 4, processed: 0, failed: 0, error: null, created_by: 'Liam Lead',
  created_at: '2026-09-15T00:00:00Z', started_at: null, finished_at: null, ...overrides,
});

const noWait = async () => {};

describe('waitForJob', () => {
  it('polls until the job finishes and reports each state', async () => {
    const states = [job({ status: 'running', processed: 2 }), job({ status: 'completed', processed: 4 })];
    const fetchJob = vi.fn(async () => states.shift()!);
    const progress: number[] = [];

    const finished = await waitForJob(job(), fetchJob, update => progress.push(jobProgress(update)), { wait: noWait });

    expect(finished.status).toBe('completed');
    expect(progress).toEqual([0, 50, 100]);
    expect(fetchJob).toHaveBeenCalledWith(3);
  });

  it('stops polling when the screen is closed', async () => {
    const fetchJob = vi.fn(async () => job({ status: 'running' }));

    const latest = await waitForJob(job(), fetchJob, () => {}, { wait: noWait, stopped: () => true });

    expect(latest.status).toBe('queued');
    expect(fetchJob).not.toHaveBeenCalled();
  });
});

describe('jobProblem', () => {
  it('explains failed jobs and partial failures, and stays quiet on success', () => {
    expect(jobProblem(job({ status: 'failed', error: 'No Google Gemini API key is available.' }), 'AI screening')).toBe(
      'AI screening failed: No Google Gemini API key is available.',
    );
    expect(jobProblem(job({ status: 'completed', processed: 4, failed: 1 }), 'AI extraction')).toBe(
      'AI extraction finished, but 1 of 4 record(s) failed. Those records need manual review.',
    );
    expect(jobProblem(job({ status: 'completed', processed: 4 }), 'AI screening')).toBeNull();
  });
});
