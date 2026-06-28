import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { SyncJobInfo } from "../api/types";

/**
 * Drive a background job (frame-import / keep-in-sync bind) to completion. Start it via
 * `start(() => api.startFrameImport(...))` (or `api.subscribe(...)`), then it polls every 1s while
 * running and, on completion, invalidates the frame's library + bindings so the UI reflects it.
 */
export function useSyncJob(host: string) {
  const qc = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);
  const [startError, setStartError] = useState<string | null>(null);

  const job = useQuery({
    queryKey: ["sync-job", host, jobId],
    queryFn: () => api.syncJob(host, jobId!),
    enabled: !!jobId,
    refetchInterval: (q) => (!q.state.data || q.state.data.status === "running" ? 1000 : false),
  });

  const status = job.data?.status;
  useEffect(() => {
    if (status && status !== "running") {
      qc.invalidateQueries({ queryKey: ["frame-library", host] });
      qc.invalidateQueries({ queryKey: ["subscriptions", host] });
    }
  }, [status, host, qc]);

  const start = async (starter: () => Promise<SyncJobInfo>) => {
    setStartError(null);
    setJobId(null);
    try {
      setJobId((await starter()).id);
    } catch (e) {
      setStartError((e as Error).message);
    }
  };

  const running = (!!jobId && !job.data) || status === "running";
  return { info: job.data, start, running, startError };
}
