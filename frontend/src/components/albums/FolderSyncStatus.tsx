import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { Button, StatusDot, relTime, usePoll, useToast } from "../../ui";

/**
 * Per-folder keep-in-sync state + Stop (#56). The Albums tab is the management surface for
 * subscriptions (Activity stays a read-only fleet log). Matched to the folder by ``target_album``.
 */
export function FolderSyncStatus({ host, folder }: { host: string; folder: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const refetchInterval = usePoll(30000); // keep "x ago" fresh; pauses on hidden tabs
  const subs = useQuery({
    queryKey: ["subscriptions", host],
    queryFn: () => api.subscriptions(host),
    refetchInterval,
  });
  const sub = (subs.data ?? []).find((s) => s.target_album === folder);

  const stop = useMutation({
    mutationFn: () => api.unsubscribe(host, sub!.immich_album_id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["subscriptions", host] });
      toast("Stopped keeping the folder in sync.");
    },
    onError: (e) => toast((e as Error).message, "fail"),
  });

  if (!sub) return null;
  return (
    <div className="flex items-center gap-2 rounded-lg border border-accent/20 bg-accent/5 px-3 py-2 text-sm">
      <StatusDot tone="active" />
      <span className="min-w-0 flex-1 truncate text-slate-300">
        Kept in sync from Immich · {sub.last_result ?? "pending"} · {relTime(sub.last_synced_at)}
      </span>
      <Button
        className="shrink-0 px-2 py-0.5 text-xs"
        disabled={stop.isPending}
        onClick={() => stop.mutate()}
      >
        {stop.isPending ? "Stopping…" : "Stop sync"}
      </Button>
    </div>
  );
}
