import { useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { Album, SyncJobInfo, SyncResult } from "../../api/types";
import { useSyncJob } from "../../lib/useSyncJob";
import { Banner, Button, Skeleton, useToast } from "../../ui";

/**
 * Folder-scoped ingest (#56): fill the already-selected frame folder from Immich (add once / add
 * selected / keep in sync) or by direct upload. Reuses the legacy AddPhotos flow, restyled to the
 * design-system primitives, with the redundant destination picker dropped (the folder is the
 * context). Live progress is announced via an aria-live region.
 */
export function AddToFolder({
  host,
  folder,
  canUpload,
}: {
  host: string;
  folder: string; // the selected frame folder (target_album); "" = Photos (all)
  canUpload: boolean;
}) {
  const [tab, setTab] = useState<"immich" | "upload">("immich");
  const label = folder || "Photos";
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-sm font-semibold">Add to “{label}”</span>
        <div className="ml-auto flex gap-1 rounded-lg bg-ink p-1 text-sm">
          <TabBtn active={tab === "immich"} onClick={() => setTab("immich")}>
            From Immich
          </TabBtn>
          {canUpload && (
            <TabBtn active={tab === "upload"} onClick={() => setTab("upload")}>
              Upload files
            </TabBtn>
          )}
        </div>
      </div>
      {tab === "immich" ? (
        <ImmichPicker host={host} folder={folder} />
      ) : (
        <DirectUpload host={host} folder={folder} />
      )}
    </div>
  );
}

function TabBtn(props: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={props.onClick}
      aria-pressed={props.active}
      className={`rounded px-2 py-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 ${
        props.active ? "bg-accent text-white" : "text-slate-300"
      }`}
    >
      {props.children}
    </button>
  );
}

function SyncSummary({ result }: { result: SyncResult }) {
  return (
    <div className="rounded-lg bg-ink px-3 py-2 text-sm">
      <span className="text-emerald-300">{result.uploaded} added</span> ·{" "}
      <span className="text-slate-400">{result.skipped} kept</span>
      {result.removed > 0 && <span className="text-amber-300"> · {result.removed} removed</span>}
      {result.failed > 0 && <span className="text-red-300"> · {result.failed} failed</span>}
    </div>
  );
}

function JobProgress({ info }: { info: SyncJobInfo }) {
  const r = info.result;
  const processed = r.uploaded + r.skipped + r.failed;
  const pct = r.total ? Math.round((processed / r.total) * 100) : 0;
  return (
    <div className="space-y-1">
      <div className="h-2 overflow-hidden rounded-full bg-ink">
        <div className="h-full bg-accent motion-safe:transition-all" style={{ width: `${pct}%` }} />
      </div>
      <div className="text-xs text-slate-400">
        Syncing… {r.uploaded} added{r.skipped > 0 && ` · ${r.skipped} kept`}
        {r.total > 0 && ` · ${processed}/${r.total}`}
      </div>
    </div>
  );
}

function ImmichPicker({ host, folder }: { host: string; folder: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [query, setQuery] = useState("");
  const [album, setAlbum] = useState<Album | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const albums = useQuery({ queryKey: ["immich-albums"], queryFn: api.immichAlbums });
  const assets = useQuery({
    queryKey: ["immich-assets", album?.id],
    queryFn: () => api.immichAssets(album!.id),
    enabled: !!album,
  });
  const { info: job, start, running, startError } = useSyncJob(host);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (albums.data ?? [])
      .filter((a) => !q || a.name.toLowerCase().includes(q))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [albums.data, query]);

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  if (albums.isLoading) return <Skeleton className="h-40 w-full" />;
  if (albums.error) return <Banner tone="fail">{(albums.error as Error).message}</Banner>;

  const body = (ids?: string[]) => ({
    album_id: album?.id,
    asset_ids: ids,
    target_album: folder || undefined,
  });

  if (!album) {
    return (
      <div className="space-y-2">
        <input
          autoFocus
          className="w-full rounded bg-ink px-3 py-2 text-sm"
          placeholder={`Search ${albums.data?.length ?? 0} Immich albums…`}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <ul className="max-h-72 divide-y divide-edge overflow-auto rounded-lg border border-edge">
          {filtered.map((a) => (
            <li key={a.id}>
              <button
                onClick={() => {
                  setAlbum(a);
                  setSelected(new Set());
                }}
                className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-edge"
              >
                <span className="truncate">{a.name}</span>
                <span className="ml-2 shrink-0 text-slate-500">{a.asset_count}</span>
              </button>
            </li>
          ))}
          {filtered.length === 0 && (
            <li className="px-3 py-2 text-sm text-slate-500">No matching albums.</li>
          )}
        </ul>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-sm">
        <Button className="px-2 py-0.5 text-xs" onClick={() => setAlbum(null)}>
          ‹ Albums
        </Button>
        <span className="font-medium">{album.name}</span>
        <span className="text-slate-500">({album.asset_count})</span>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          variant="accent"
          disabled={running}
          onClick={() => {
            start(() => api.subscribe(host, album.id, folder || album.name));
            qc.invalidateQueries({ queryKey: ["subscriptions", host] });
            toast(`Keeping “${folder || album.name}” in sync with Immich “${album.name}”.`);
          }}
          title="Mirror this album into the folder and keep it updated automatically"
        >
          Keep in sync
        </Button>
        <Button
          disabled={running}
          onClick={() => start(() => api.startSyncJob(host, body()))}
          title="Copy the album's photos once (no automatic updates)"
        >
          Add once
        </Button>
        <Button
          disabled={running || selected.size === 0}
          onClick={() => {
            const ids = [...selected];
            setSelected(new Set());
            start(() => api.startSyncJob(host, body(ids)));
          }}
        >
          Add selected ({selected.size})
        </Button>
      </div>

      <div aria-live="polite" role="status">
        {job?.status === "running" && <JobProgress info={job} />}
        {job?.status === "done" && <SyncSummary result={job.result} />}
        {(job?.status === "error" || startError) && (
          <Banner tone="fail">{job?.error ?? startError}</Banner>
        )}
      </div>

      {assets.isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : (
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 md:grid-cols-6">
          {assets.data
            ?.filter((a) => a.type === "IMAGE")
            .map((a) => {
              const on = selected.has(a.id);
              return (
                <button
                  key={a.id}
                  onClick={() => toggle(a.id)}
                  title={a.file_name}
                  aria-pressed={on}
                  className={`relative aspect-square overflow-hidden rounded-lg border-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 ${
                    on ? "border-accent" : "border-transparent"
                  }`}
                >
                  <img
                    src={api.immichThumbUrl(a.id)}
                    alt={a.file_name}
                    loading="lazy"
                    className="h-full w-full bg-ink object-cover"
                  />
                  {on && (
                    <span className="absolute right-1 top-1 rounded-full bg-accent px-1.5 text-xs text-white">
                      ✓
                    </span>
                  )}
                </button>
              );
            })}
        </div>
      )}
    </div>
  );
}

type UploadState = "pending" | "uploading" | "done" | "failed";
interface Item {
  file: File;
  status: UploadState;
  error?: string;
}

function DirectUpload({ host, folder }: { host: string; folder: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const inputRef = useRef<HTMLInputElement>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [running, setRunning] = useState(false);

  const done = items.filter((i) => i.status === "done").length;
  const failed = items.filter((i) => i.status === "failed").length;
  const pct = items.length ? Math.round((done / items.length) * 100) : 0;

  function setStatus(idx: number, status: UploadState, error?: string) {
    setItems((prev) => prev.map((it, i) => (i === idx ? { ...it, status, error } : it)));
  }

  async function run(indices: number[]) {
    setRunning(true);
    for (const idx of indices) {
      setStatus(idx, "uploading");
      try {
        await api.upload(host, [items[idx].file], folder || undefined);
        setStatus(idx, "done");
      } catch (e) {
        setStatus(idx, "failed", (e as Error).message);
      }
    }
    setRunning(false);
    qc.invalidateQueries({ queryKey: ["albums", host] });
    toast(`Uploaded to “${folder || "Photos"}”.`);
  }

  return (
    <div className="space-y-3">
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        multiple
        disabled={running}
        className="block w-full text-sm text-slate-300 file:mr-3 file:rounded file:border-0
                   file:bg-edge file:px-3 file:py-1.5 file:text-slate-100"
        onChange={(e) =>
          setItems(Array.from(e.target.files ?? []).map((file) => ({ file, status: "pending" })))
        }
      />
      <div className="flex gap-2">
        <Button
          variant="accent"
          disabled={running || items.length === 0}
          onClick={() => run(items.map((_, i) => i))}
        >
          {running ? "Uploading…" : `Upload ${items.length || ""} to “${folder || "Photos"}”`}
        </Button>
        {!running && failed > 0 && (
          <Button
            onClick={() =>
              run(items.map((it, i) => (it.status === "failed" ? i : -1)).filter((i) => i >= 0))
            }
          >
            Retry {failed} failed
          </Button>
        )}
      </div>
      {items.length > 0 && (
        <div className="space-y-2" aria-live="polite">
          <div className="h-2 overflow-hidden rounded-full bg-ink">
            <div className="h-full bg-accent motion-safe:transition-all" style={{ width: `${pct}%` }} />
          </div>
          <div className="text-xs text-slate-400">
            {done}/{items.length} uploaded
            {failed > 0 && <span className="text-red-300"> · {failed} failed</span>}
          </div>
        </div>
      )}
    </div>
  );
}
