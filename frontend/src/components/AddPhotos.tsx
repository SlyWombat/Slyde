import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { SyncResult } from "../api/types";

type Tab = "immich" | "upload";

export function AddPhotos({ host, targetAlbum }: { host: string; targetAlbum: string | null }) {
  const [tab, setTab] = useState<Tab>("immich");
  const target = targetAlbum ?? "";
  const label = targetAlbum ? `“${targetAlbum}”` : "the frame (Photos)";

  return (
    <div className="card space-y-4">
      <div className="flex items-center gap-2">
        <span className="font-semibold">Add photos to {label}</span>
        <div className="ml-auto flex gap-1 rounded-lg bg-ink p-1 text-sm">
          <TabButton active={tab === "immich"} onClick={() => setTab("immich")}>
            From Immich
          </TabButton>
          <TabButton active={tab === "upload"} onClick={() => setTab("upload")}>
            Upload files
          </TabButton>
        </div>
      </div>
      {tab === "immich" ? (
        <ImmichPicker host={host} target={target} />
      ) : (
        <DirectUpload host={host} target={target} />
      )}
    </div>
  );
}

function TabButton(props: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={props.onClick}
      className={`rounded px-2 py-1 ${props.active ? "bg-accent text-white" : "text-slate-300"}`}
    >
      {props.children}
    </button>
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

function ImmichPicker({ host, target }: { host: string; target: string }) {
  const qc = useQueryClient();
  const [albumId, setAlbumId] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const albums = useQuery({ queryKey: ["immich-albums"], queryFn: api.immichAlbums });
  const assets = useQuery({
    queryKey: ["immich-assets", albumId],
    queryFn: () => api.immichAssets(albumId),
    enabled: !!albumId,
  });
  const sync = useMutation({
    mutationFn: (body: { album_id?: string; asset_ids?: string[]; target_album?: string }) =>
      api.sync(host, body),
    onSuccess: () => {
      setSelected(new Set());
      qc.invalidateQueries({ queryKey: ["albums", host] });
    },
  });

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  if (albums.isLoading) return <div className="text-sm text-slate-400">Loading Immich…</div>;
  if (albums.error)
    return <div className="text-sm text-red-300">{(albums.error as Error).message}</div>;

  const body = (ids?: string[]) => ({
    album_id: albumId,
    asset_ids: ids,
    target_album: target || undefined,
  });

  return (
    <div className="space-y-3">
      <select
        className="w-full rounded bg-ink px-2 py-1 text-sm"
        value={albumId}
        onChange={(e) => {
          setAlbumId(e.target.value);
          setSelected(new Set());
        }}
      >
        <option value="">Choose an Immich album…</option>
        {albums.data!.map((a) => (
          <option key={a.id} value={a.id}>
            {a.name} ({a.asset_count})
          </option>
        ))}
      </select>

      {albumId && (
        <>
          <div className="flex gap-2">
            <button
              className="btn-accent"
              disabled={sync.isPending}
              onClick={() => sync.mutate(body())}
            >
              {sync.isPending ? "Syncing…" : "Add whole album"}
            </button>
            <button
              className="btn"
              disabled={sync.isPending || selected.size === 0}
              onClick={() => sync.mutate(body([...selected]))}
            >
              Add selected ({selected.size})
            </button>
          </div>
          {sync.data && <SyncSummary result={sync.data} />}
          {sync.isError && (
            <div className="text-sm text-red-300">{(sync.error as Error).message}</div>
          )}
          {assets.isLoading ? (
            <div className="text-sm text-slate-400">Loading photos…</div>
          ) : (
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 md:grid-cols-6">
              {assets.data
                ?.filter((a) => a.type === "IMAGE")
                .map((a) => (
                  <button
                    key={a.id}
                    onClick={() => toggle(a.id)}
                    className={`relative aspect-square overflow-hidden rounded-lg border-2 ${
                      selected.has(a.id) ? "border-accent" : "border-transparent"
                    }`}
                    title={a.file_name}
                  >
                    <img
                      src={api.immichThumbUrl(a.id)}
                      alt={a.file_name}
                      loading="lazy"
                      className="h-full w-full bg-ink object-cover"
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

function DirectUpload({ host, target }: { host: string; target: string }) {
  const qc = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const upload = useMutation({
    mutationFn: () => api.upload(host, files, target || undefined),
    onSuccess: () => {
      setFiles([]);
      if (inputRef.current) inputRef.current.value = "";
      qc.invalidateQueries({ queryKey: ["albums", host] });
    },
  });

  return (
    <div className="space-y-3">
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        multiple
        className="block w-full text-sm text-slate-300 file:mr-3 file:rounded file:border-0
                   file:bg-edge file:px-3 file:py-1.5 file:text-slate-100"
        onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
      />
      <button
        className="btn-accent"
        disabled={upload.isPending || files.length === 0}
        onClick={() => upload.mutate()}
      >
        {upload.isPending ? "Uploading…" : `Upload ${files.length || ""} to frame`}
      </button>
      {upload.data && <SyncSummary result={upload.data} />}
      {upload.isError && (
        <div className="text-sm text-red-300">{(upload.error as Error).message}</div>
      )}
    </div>
  );
}
