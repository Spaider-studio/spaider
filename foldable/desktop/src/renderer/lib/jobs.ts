/**
 * Follows a model job through its SSE event stream until a terminal state.
 * Resolves with the final Job snapshot. If the stream ends early (or the
 * connection drops), the job is re-fetched and re-subscribed.
 */
import type { ApiClient } from "../api/client";
import { isTerminalJob, type Job } from "../api/types";

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const t = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(t);
      resolve();
    });
  });
}

export async function followJob(
  client: ApiClient,
  jobId: string,
  onUpdate: (job: Job) => void,
  signal?: AbortSignal,
): Promise<Job> {
  let last: Job | null = null;
  for (let attempt = 0; attempt < 50; attempt++) {
    if (signal?.aborted) break;
    try {
      await client.jobEvents(
        jobId,
        (job) => {
          last = job;
          onUpdate(job);
        },
        signal,
      );
    } catch {
      /* stream failed, fall through to a plain fetch */
    }
    if (signal?.aborted) break;
    if (last && isTerminalJob(last)) return last;
    try {
      const job = await client.job(jobId);
      last = job;
      onUpdate(job);
      if (isTerminalJob(job)) return job;
    } catch {
      /* retry */
    }
    await sleep(1000, signal);
  }
  if (last) return last;
  throw new Error("Job konnte nicht verfolgt werden.");
}
