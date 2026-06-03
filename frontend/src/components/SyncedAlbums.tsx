import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

export function SyncedAlbums({ host }: { host: string }) {
  const qc = useQueryClient();
  const subs = useQuery({ queryKey: ["subscriptions", host], queryFn: () => api.subscriptions(host) });
  const stop = useMutation({
    mutationFn: (albumId: string) => api.unsubscribe(host, albumId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["subscriptions", host] }),
  });

  if (!subs.data || subs.data.length === 0) return null;

  return (
    <div className="card space-y-2">
      <div className="font-semibold">Kept in sync</div>
      <ul className="space-y-1">
        {subs.data.map((s) => (
          <li
            key={s.immich_album_id}
            className="flex items-center justify-between rounded bg-ink px-2 py-1.5 text-sm"
          >
            <div className="min-w-0">
              <div className="truncate font-medium">{s.target_album}</div>
              <div className="truncate text-xs text-slate-500">
                {s.last_result ?? "pending"}
                {s.last_synced_at ? ` · ${s.last_synced_at}` : ""}
              </div>
            </div>
            <button
              className="btn ml-2 shrink-0 px-2 py-0.5 text-xs"
              disabled={stop.isPending}
              onClick={() => stop.mutate(s.immich_album_id)}
              title="Stop keeping this album in sync (leaves the frame folder as-is)"
            >
              Stop
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
