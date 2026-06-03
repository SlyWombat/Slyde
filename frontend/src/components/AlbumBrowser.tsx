import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { SyncResult } from "../api/types";

export function AlbumBrowser() {
  const qc = useQueryClient();
  const [albumId, setAlbumId] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const albums = useQuery({ queryKey: ["albums"], queryFn: api.albums });
  const assets = useQuery({
    queryKey: ["assets", albumId],
    queryFn: () => api.assets(albumId!),
    enabled: albumId !== null,
  });

  const sync = useMutation({
    mutationFn: (body: { album_id?: string; asset_ids?: string[] }) => api.sync(body),
    onSuccess: () => {
      setSelected(new Set());
      qc.invalidateQueries({ queryKey: ["photos"] });
    },
  });

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  if (albums.isLoading) return <div className="card">Loading albums…</div>;
  if (albums.error)
    return (
      <div className="card border-red-500/40 text-sm text-red-300">
        Immich: {(albums.error as Error).message}
      </div>
    );

  return (
    <div className="card space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">Immich albums</span>
        <select
          className="ml-auto rounded bg-ink px-2 py-1 text-sm"
          value={albumId ?? ""}
          onChange={(e) => {
            setAlbumId(e.target.value || null);
            setSelected(new Set());
          }}
        >
          <option value="">Choose an album…</option>
          {albums.data!.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name} ({a.asset_count})
            </option>
          ))}
        </select>
      </div>

      {albumId && (
        <>
          <div className="flex items-center gap-2">
            <button
              className="btn-accent"
              disabled={sync.isPending}
              onClick={() => sync.mutate({ album_id: albumId })}
            >
              {sync.isPending ? "Syncing…" : "Sync whole album"}
            </button>
            <button
              className="btn"
              disabled={sync.isPending || selected.size === 0}
              onClick={() => sync.mutate({ album_id: albumId, asset_ids: [...selected] })}
            >
              Sync selected ({selected.size})
            </button>
          </div>

          {sync.data && <SyncSummary result={sync.data} />}
          {sync.isError && (
            <div className="text-sm text-red-300">{(sync.error as Error).message}</div>
          )}

          {assets.isLoading ? (
            <div className="text-sm text-slate-400">Loading photos…</div>
          ) : (
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 md:grid-cols-5">
              {assets.data
                ?.filter((a) => a.type === "IMAGE")
                .map((a) => (
                  <button
                    key={a.id}
                    onClick={() => toggle(a.id)}
                    className={`group relative aspect-square overflow-hidden rounded-lg border-2 ${
                      selected.has(a.id) ? "border-accent" : "border-transparent"
                    }`}
                    title={a.file_name}
                  >
                    <img
                      src={api.thumbUrl(a.id)}
                      alt={a.file_name}
                      loading="lazy"
                      className="h-full w-full object-cover"
                    />
                    {selected.has(a.id) && (
                      <span className="absolute right-1 top-1 rounded-full bg-accent px-1.5 text-xs text-white">
                        ✓
                      </span>
                    )}
                  </button>
                ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function SyncSummary({ result }: { result: SyncResult }) {
  return (
    <div className="rounded-lg bg-ink px-3 py-2 text-sm">
      <span className="text-emerald-300">{result.uploaded} uploaded</span> ·{" "}
      <span className="text-slate-400">{result.skipped} skipped</span>
      {result.failed > 0 && <span className="text-red-300"> · {result.failed} failed</span>}
    </div>
  );
}
