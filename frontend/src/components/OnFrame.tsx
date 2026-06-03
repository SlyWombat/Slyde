import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

export function OnFrame() {
  const qc = useQueryClient();
  const photos = useQuery({ queryKey: ["photos"], queryFn: api.photos });
  const remove = useMutation({
    mutationFn: (assetId: string) => api.deletePhoto(assetId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["photos"] }),
  });

  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between">
        <span className="font-semibold">On the frame</span>
        <span className="text-xs text-slate-400">{photos.data?.length ?? 0} photos</span>
      </div>
      {photos.isLoading ? (
        <div className="text-sm text-slate-400">Loading…</div>
      ) : photos.data && photos.data.length > 0 ? (
        <ul className="max-h-72 space-y-1 overflow-auto pr-1">
          {photos.data.map((p) => (
            <li
              key={p.asset_id}
              className="flex items-center justify-between rounded bg-ink px-2 py-1.5 text-sm"
            >
              <span className="truncate" title={p.dest_name}>
                {p.dest_name}
              </span>
              <button
                className="btn ml-2 shrink-0 px-2 py-0.5 text-xs"
                disabled={remove.isPending}
                onClick={() => remove.mutate(p.asset_id)}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <div className="text-sm text-slate-400">
          Nothing synced yet. Pick an album and press “Sync”.
        </div>
      )}
    </div>
  );
}
