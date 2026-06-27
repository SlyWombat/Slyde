import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { LibraryPhoto, LibraryView } from "../api/types";
import { useFrame } from "../lib/frames";
import { useSyncJob } from "../lib/useSyncJob";
import { EmptyState, ErrorState, Pill, Skeleton, StatusDot, useToast, usePoll, type Tone } from "../ui";
import { AlbumsTab } from "./albums/AlbumsTab";

/** Unified "Add photos" menu — the single entry point for filling a frame (#60). From Immich
 *  (curate, any frame) + Import from frame (connected). Folder-scoped uploads + keep-in-sync live in
 *  the Folders section below. */
function AddPhotos({
  frameId,
  connected,
  folder = null,
}: {
  frameId: string;
  connected: boolean;
  folder?: string | null;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const { info, start, running } = useSyncJob(frameId);
  const status = info?.status;
  useEffect(() => {
    if (status && status !== "running")
      qc.invalidateQueries({ queryKey: ["frame-library", frameId] });
  }, [status, frameId, qc]);
  const r = info?.result;
  // Curating from a selected folder pre-targets that folder; "All" leaves it ungrouped (#61).
  const curateTo =
    `/curate?target=${encodeURIComponent(frameId)}` +
    (folder ? `&folder=${encodeURIComponent(folder)}` : "");

  const upload = useMutation({
    mutationFn: (files: File[]) => api.upload(frameId, files, folder || undefined),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["frame-library", frameId] });
      toast(`Uploaded ${res.uploaded} ${res.uploaded === 1 ? "photo" : "photos"}.`);
    },
    onError: (e) => toast((e as Error).message, "fail"),
  });

  return (
    <div className="flex flex-col items-end gap-1">
      <input
        ref={fileRef}
        type="file"
        accept="image/*"
        multiple
        hidden
        onChange={(e) => {
          const files = [...(e.target.files ?? [])];
          if (files.length) upload.mutate(files);
          e.target.value = "";
        }}
      />
      <details className="relative">
        <summary className="flex cursor-pointer list-none items-center gap-1 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white hover:opacity-90">
          + Add photos
          <span aria-hidden className="text-xs opacity-80">
            ▾
          </span>
        </summary>
        <div className="absolute right-0 z-20 mt-1 w-64 overflow-hidden rounded-lg border border-edge bg-ink shadow-xl">
          <Link to={curateTo} className="block px-3 py-2 hover:bg-edge">
            <div className="text-sm font-medium">
              From Immich…{folder ? ` → ${folder}` : ""}
            </div>
            <div className="text-xs text-slate-400">Pick photos to curate to this frame</div>
          </Link>
          <button
            type="button"
            disabled={upload.isPending}
            onClick={() => fileRef.current?.click()}
            className="block w-full px-3 py-2 text-left hover:bg-edge disabled:opacity-50"
          >
            <div className="text-sm font-medium">
              {upload.isPending ? "Uploading…" : "Upload files…"}
              {folder ? ` → ${folder}` : ""}
            </div>
            <div className="text-xs text-slate-400">Add photos from your device</div>
          </button>
          {connected && (
            <button
              type="button"
              disabled={running}
              onClick={() => start(() => api.startFrameImport(frameId))}
              className="block w-full px-3 py-2 text-left hover:bg-edge disabled:opacity-50"
            >
              <div className="text-sm font-medium">{running ? "Importing…" : "Import from frame"}</div>
              <div className="text-xs text-slate-400">Pull the photos already on the frame</div>
            </button>
          )}
          {connected && (
            <div className="border-t border-edge px-3 py-2 text-xs text-slate-500">
              Keep-in-sync from an Immich album is per-folder — in Folders below ↓
            </div>
          )}
        </div>
      </details>
      {running && r && (
        <span className="text-xs text-slate-400">
          Importing {r.uploaded + r.skipped + r.failed}/{r.total}…
        </span>
      )}
      {status === "done" && r && (
        <span className="text-xs text-slate-400">
          Imported {r.uploaded}
          {r.skipped > 0 && ` · ${r.skipped} already had`}
          {r.failed > 0 && ` · ${r.failed} failed`}.
        </span>
      )}
    </div>
  );
}

/** Connected-frame device-folder surface, re-homed from the retired Albums tab into Library (#60):
 *  folders + per-folder From-Immich (once / keep-in-sync) + upload. Reads Engine B live, so it's
 *  unavailable when the frame is asleep — the curated set above is unaffected. */
function FrameFolders({ frameId, connected }: { frameId: string; connected: boolean }) {
  if (!connected) return null;
  return (
    <section className="space-y-3 border-t border-edge pt-5">
      <div>
        <h3 className="text-sm font-semibold text-slate-200">Folders on the frame</h3>
        <p className="text-xs text-slate-400">
          Organise photos into folders on the device and fill them from Immich (once or kept in
          sync) or by upload. Needs the frame reachable.
        </p>
      </div>
      <AlbumsTab host={frameId} />
    </section>
  );
}

/** Per-photo delivery state → status tone. Pending/unknown are not failures. */
const STATE_TONE: Record<LibraryPhoto["state"], Tone> = {
  delivered: "ok",
  pending: "pending",
  failed: "fail",
  unknown: "idle",
};
const STATE_LABEL: Record<LibraryPhoto["state"], string> = {
  delivered: "on frame",
  pending: "delivering",
  failed: "failed",
  unknown: "queued",
};

/**
 * The frame's curated set with per-photo delivery state (#37). Transport-agnostic: reads the
 * library/delivery registry (works for served + offline frames, no host calls). Remove and reorder
 * re-`PUT` the desired set — strictly non-blocking (the backend queues + reconciles in the
 * background; closing this view changes nothing).
 */
export function LibraryTab({ frameId }: { frameId: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { frame } = useFrame(frameId);
  const connected = frame?.interaction === "connected";
  const [folder, setFolder] = useState<string | null>(null); // null = the "All" view (#61)
  const refetchInterval = usePoll(5000);
  const key = ["frame-library", frameId];
  const lib = useQuery({ queryKey: key, queryFn: () => api.frameLibrary(frameId), refetchInterval });

  // Re-PUT the whole ordered set; dest_name is preserved so positions/names stay stable.
  const write = useMutation({
    mutationFn: (items: LibraryPhoto[]) =>
      api.setLibrary(
        frameId,
        items.map((p) => ({ asset_id: p.asset_id, dest_name: p.dest_name })),
      ),
    onMutate: async (items) => {
      await qc.cancelQueries({ queryKey: key });
      const prev = qc.getQueryData<LibraryView>(key);
      if (prev) qc.setQueryData<LibraryView>(key, { ...prev, items }); // optimistic reorder/remove
      return { prev };
    },
    onError: (_e, _v, ctx) => ctx?.prev && qc.setQueryData(key, ctx.prev),
    onSettled: () => qc.invalidateQueries({ queryKey: key }),
  });
  // Remove a photo from the library (any source); on a connected frame also delete the device file
  // (best-effort — a sleeping frame keeps it, but it's no longer curated so it won't re-deliver).
  const remove = useMutation({
    mutationFn: async (p: LibraryPhoto) => {
      await api.removeLibraryItem(frameId, p.asset_id);
      if (connected) await api.deletePhoto(frameId, p.dest_name).catch(() => undefined);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["frame-library", frameId] }),
    onError: (e) => toast((e as Error).message, "fail"),
  });

  if (lib.isLoading && !lib.data) return <Skeleton className="h-64 w-full" />;
  if (lib.error)
    return <ErrorState message={(lib.error as Error).message} onRetry={() => lib.refetch()} />;

  const items = lib.data?.items ?? [];
  const d = lib.data?.deliveries;

  if (items.length === 0) {
    return (
      <div className="space-y-4">
        <EmptyState
          icon="✦"
          title="No photos curated yet"
          desc="Add photos from Immich, or pull in the photos already on the frame. They deliver automatically — even if the frame is asleep."
          action={<AddPhotos frameId={frameId} connected={connected} />}
        />
        <FrameFolders frameId={frameId} connected={connected} />
      </div>
    );
  }

  const move = (i: number, delta: number) => {
    const j = i + delta;
    if (j < 0 || j >= items.length) return;
    const next = items.slice();
    [next[i], next[j]] = [next[j], next[i]];
    write.mutate(next);
  };

  // Folder grouping (#61): chips filter the grid; reorder is only offered in the flat "All" view.
  const folders = [...new Set(items.map((p) => p.folder))].sort();
  const hasFolders = folders.some((f) => f !== "");
  const shown = folder === null ? items : items.filter((p) => p.folder === folder);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-slate-300">{items.length} photos</span>
        {d && d.delivered > 0 && <Pill tone="ok">{d.delivered} on frame</Pill>}
        {d && d.pending > 0 && <Pill tone="pending">{d.pending} delivering</Pill>}
        {d && d.failed > 0 && <Pill tone="fail">{d.failed} failed</Pill>}
        <div className="ml-auto">
          <AddPhotos frameId={frameId} connected={connected} folder={folder} />
        </div>
      </div>

      {hasFolders && (
        <div className="flex flex-wrap gap-1.5">
          <FolderChip active={folder === null} onClick={() => setFolder(null)}>
            All {items.length}
          </FolderChip>
          {folders.map((f) => (
            <FolderChip key={f} active={folder === f} onClick={() => setFolder(f)}>
              {f || "Ungrouped"} {items.filter((p) => p.folder === f).length}
            </FolderChip>
          ))}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4">
        {shown.map((p, i) => (
          <figure
            key={p.asset_id}
            className="group relative overflow-hidden rounded-lg border border-edge bg-ink"
          >
            <img
              src={api.assetPreviewUrl(p.asset_id)}
              alt={p.dest_name}
              loading="lazy"
              className="aspect-square w-full object-cover"
            />
            <figcaption className="absolute inset-x-0 bottom-0 flex items-center gap-1.5 bg-gradient-to-t from-ink/90 to-transparent px-2 py-1.5 text-[11px] text-slate-200">
              <StatusDot tone={STATE_TONE[p.state]} />
              {STATE_LABEL[p.state]}
            </figcaption>

            {/* Reorder + remove controls (hover on desktop, always-visible on touch). Reorder is
                offered only in the flat "All" view, where the index maps to the stored order. */}
            <div className="absolute inset-x-0 top-0 flex items-center justify-between p-1.5 opacity-0 transition group-hover:opacity-100 [@media(hover:none)]:opacity-100">
              {folder === null ? (
                <div className="flex gap-1">
                  <IconBtn label="Move earlier" disabled={i === 0} onClick={() => move(i, -1)}>
                    ‹
                  </IconBtn>
                  <IconBtn
                    label="Move later"
                    disabled={i === items.length - 1}
                    onClick={() => move(i, 1)}
                  >
                    ›
                  </IconBtn>
                </div>
              ) : (
                <span />
              )}
              <IconBtn
                label={connected ? "Remove from frame" : "Remove from library"}
                disabled={remove.isPending}
                onClick={() => remove.mutate(p)}
              >
                ✕
              </IconBtn>
            </div>
          </figure>
        ))}
      </div>

      <FrameFolders frameId={frameId} connected={connected} />
    </div>
  );
}

function FolderChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={`rounded-full px-3 py-1 text-xs transition ${
        active ? "bg-accent text-white" : "bg-edge text-slate-200 hover:text-white"
      }`}
    >
      {children}
    </button>
  );
}

function IconBtn({
  children,
  label,
  onClick,
  disabled,
}: {
  children: React.ReactNode;
  label: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className="flex h-6 w-6 items-center justify-center rounded-md bg-ink/80 text-sm text-slate-200 backdrop-blur hover:bg-edge disabled:opacity-30"
    >
      {children}
    </button>
  );
}
