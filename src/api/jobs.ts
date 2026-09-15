// Mirrors jobs_out in backend/jobs.py.
export type AiJob = {
  id: number;
  task: 'screening' | 'fulltext_screening' | 'extraction' | 'appraisal' | 'reporting' | 'statistics' | 'embedding' | 'fulltext';
  // A completed job can still include records that failed; see `failed`.
  status: 'queued' | 'running' | 'completed' | 'failed';
  total: number;
  processed: number;
  failed: number;
  error: string | null;
  created_by: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export const isFinished = (job: AiJob) => job.status === 'completed' || job.status === 'failed';

export const jobProgress = (job: AiJob) => {
  if (job.total > 0) return Math.round((job.processed / job.total) * 100);
  return isFinished(job) ? 100 : 0;
};

// A message for the user when a job failed or some of its records did, or null when everything succeeded.
export function jobProblem(job: AiJob, label: string): string | null {
  if (job.status === 'failed') return `${label} failed: ${job.error ?? 'unknown error'}`;
  if (job.failed > 0) {
    const reason = job.error ? ` (${job.error})` : '';
    return `${label} finished, but ${job.failed} of ${job.total} record(s) failed${reason}. Those records need manual review.`;
  }
  return null;
}

const sleep = (ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms));

type WaitOptions = { intervalMs?: number; stopped?: () => boolean; wait?: (ms: number) => Promise<void> };

// Polls a background job until it finishes, reporting each state. Returns early if `stopped` becomes true.
export async function waitForJob(
  job: AiJob,
  fetchJob: (id: number) => Promise<AiJob>,
  onUpdate: (job: AiJob) => void,
  { intervalMs = 1500, stopped = () => false, wait = sleep }: WaitOptions = {},
): Promise<AiJob> {
  let current = job;
  onUpdate(current);
  while (!isFinished(current) && !stopped()) {
    await wait(intervalMs);
    current = await fetchJob(current.id);
    onUpdate(current);
  }
  return current;
}
