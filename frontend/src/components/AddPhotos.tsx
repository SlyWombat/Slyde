import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Album, SyncResult } from "../api/types";

type Tab = "immich" | "upload";

const NEW_FOLDER = "__new__";

export function AddPhotos({ host, targetAlbum }: { host: string; targetAlbum: string | null }) {
  const [tab, setTab] = useState<Tab>("immich");
  const [choice, setChoice] = useState<string>(targetAlbum ?? "");
  const [newName, setNewName] = useState("");
  const albums = useQuery({ queryKey: ["albums", host], queryFn: () => api.albums(host) });

  // Destination folder for uploads/sync: a chosen frame folder, a new one, or Photos (empty).
  const destination = choice === NEW_FOLDER ? newName.trim() : choice;

  return (
    <div className="card space-y-3">
      <div className="flex items-center gap-2">
        <span className="font-semibold">Add photos</span>
        <div className="ml-auto flex gap-1 rounded-lg bg-ink p-1 text-sm">
          <TabButton active={tab === "immich"} onClick={() => setTab("immich")}>
            From Immich
          </TabButton>
          <TabButton active={tab === "upload"} onClick={() => setTab("upload")}>
            Upload files
          </TabButton>
        </div>
      </div>

      <label className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-slate-400">Destination folder</span>
        <select
          className="rounded bg-ink px-2 py-1"
          value={choice}
          onChange={(e) => setChoice(e.target.value)}
        >
          <option value="">Photos (all)</option>
          {albums.data
            ?.filter((a) => !a.reserved)
            .map((a) => (
              <option key={a.name} value={a.name}>
                {a.display_name}
              </option>
            ))}
          <option value={NEW_FOLDER}>New folder…</option>
        </select>
        {choice === NEW_FOLDER && (
          <input
            className="rounded bg-ink px-2 py-1"
            placeholder="Folder name"
            value={newName}
            maxLength={64}
            onChange={(e) => setNewName(e.target.value)}
          />
        )}
      </label>

      {tab === "immich" ? (
        <ImmichPicker host={host} target={destination} />
      ) : (
        <DirectUpload host={host} target={destination} />
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
      <span className="text-emerald-300">{result.uploaded} added</span> ·{" "}
      <span className="text-slate-400">{result.skipped} kept</span>
      {result.removed > 0 && <span className="text-amber-300"> · {result.removed} removed</span>}
      {result.failed > 0 && <span className="text-red-300"> · {result.failed} failed</span>}
    </div>
  );
}

// ----- Immich: searchable, sorted album list -----------------------------------
function ImmichPicker({ host, target }: { host: string; target: string }) {
  const qc = useQueryClient();
  const [query, setQuery] = useState("");
  const [album, setAlbum] = useState<Album | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const albums = useQuery({ queryKey: ["immich-albums"], queryFn: api.immichAlbums });
  const assets = useQuery({
    queryKey: ["immich-assets", album?.id],
    queryFn: () => api.immichAssets(album!.id),
    enabled: !!album,
  });
  const sync = useMutation({
    mutationFn: (body: { album_id?: string; asset_ids?: string[]; target_album?: string }) =>
      api.sync(host, body),
    onSuccess: () => {
      setSelected(new Set());
      qc.invalidateQueries({ queryKey: ["albums", host] });
    },
  });
  const subscribe = useMutation({
    mutationFn: () => api.subscribe(host, album!.id, target || album!.name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["albums", host] });
      qc.invalidateQueries({ queryKey: ["subscriptions", host] });
    },
  });

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

  if (albums.isLoading) return <div className="text-sm text-slate-400">Loading Immich…</div>;
  if (albums.error)
    return <div className="text-sm text-red-300">{(albums.error as Error).message}</div>;

  const body = (ids?: string[]) => ({
    album_id: album?.id,
    asset_ids: ids,
    target_album: target || undefined,
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
        <button className="btn px-2 py-0.5" onClick={() => setAlbum(null)}>
          ‹ Albums
        </button>
        <span className="font-medium">{album.name}</span>
        <span className="text-slate-500">({album.asset_count})</span>
      </div>
      <div className="flex flex-wrap gap-2">
        <button
          className="btn-accent"
          disabled={subscribe.isPending}
          onClick={() => subscribe.mutate()}
          title="Mirror this album to a frame folder and keep it updated automatically"
        >
          {subscribe.isPending ? "Syncing…" : "Sync & keep updated"}
        </button>
        <button
          className="btn"
          disabled={sync.isPending}
          onClick={() => sync.mutate(body())}
          title="Copy the album's photos once (no automatic updates)"
        >
          {sync.isPending ? "Adding…" : "Add once"}
        </button>
        <button
          className="btn"
          disabled={sync.isPending || selected.size === 0}
          onClick={() => sync.mutate(body([...selected]))}
        >
          Add selected ({selected.size})
        </button>
      </div>
      {subscribe.data && <SyncSummary result={subscribe.data} />}
      {sync.data && <SyncSummary result={sync.data} />}
      {(sync.isError || subscribe.isError) && (
        <div className="text-sm text-red-300">
          {((sync.error ?? subscribe.error) as Error).message}
        </div>
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
    </div>
  );
}

// ----- Direct upload: per-file progress + retry --------------------------------
type UploadState = "pending" | "uploading" | "done" | "failed";
interface Item {
  file: File;
  status: UploadState;
  error?: string;
}

function DirectUpload({ host, target }: { host: string; target: string }) {
  const qc = useQueryClient();
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
        await api.upload(host, [items[idx].file], target || undefined);
        setStatus(idx, "done");
      } catch (e) {
        setStatus(idx, "failed", (e as Error).message);
      }
    }
    setRunning(false);
    qc.invalidateQueries({ queryKey: ["albums", host] });
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
        <button
          className="btn-accent"
          disabled={running || items.length === 0}
          onClick={() => run(items.map((_, i) => i))}
        >
          {running ? "Uploading…" : `Upload ${items.length || ""} to frame`}
        </button>
        {!running && failed > 0 && (
          <button
            className="btn"
            onClick={() => run(items.map((it, i) => (it.status === "failed" ? i : -1)).filter((i) => i >= 0))}
          >
            Retry {failed} failed
          </button>
        )}
      </div>

      {items.length > 0 && (
        <div className="space-y-2">
          <div className="h-2 overflow-hidden rounded-full bg-ink">
            <div className="h-full bg-accent transition-all" style={{ width: `${pct}%` }} />
          </div>
          <div className="text-xs text-slate-400">
            {done}/{items.length} uploaded
            {failed > 0 && <span className="text-red-300"> · {failed} failed</span>}
          </div>
          <ul className="max-h-40 space-y-1 overflow-auto text-xs">
            {items.map((it, i) => (
              <li key={i} className="flex items-center justify-between rounded bg-ink px-2 py-1">
                <span className="truncate" title={it.file.name}>
                  {it.file.name}
                </span>
                <span className={statusClass(it.status)}>{statusLabel(it)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function statusClass(s: UploadState): string {
  return s === "done"
    ? "text-emerald-300"
    : s === "failed"
      ? "text-red-300"
      : s === "uploading"
        ? "text-accent"
        : "text-slate-500";
}

function statusLabel(it: Item): string {
  if (it.status === "failed") return it.error ? `failed: ${it.error}` : "failed";
  return it.status;
}
