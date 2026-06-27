import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { FrameAlbum } from "../../api/types";
import {
  Banner,
  Button,
  Card,
  ConfirmButton,
  EmptyState,
  Skeleton,
  useToast,
} from "../../ui";
import { AddToFolder } from "./AddToFolder";
import { FolderSyncStatus } from "./FolderSyncStatus";

/**
 * Connected-frame FOLDER model — folders on the LAN frame + filling them from Immich (add once /
 * selected / keep-in-sync) + upload. As of #60 this is no longer a top-level tab; it renders inside
 * the Library tab's "Folders on the frame" section (still Engine B under the hood until the unify
 * phases #61-#63). Folder data is live from the device, so when the frame is asleep this errors →
 * we show an honest offline state (not red).
 */
export function AlbumsTab({ host }: { host: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [selected, setSelected] = useState<string | null>(null);
  const [newName, setNewName] = useState("");

  const albums = useQuery({ queryKey: ["albums", host], queryFn: () => api.albums(host), retry: 0 });
  const detail = useQuery({ queryKey: ["frame-detail", host], queryFn: () => api.frameDetail(host) });
  const canUpload = detail.data?.capabilities.upload ?? true;

  const create = useMutation({
    mutationFn: (name: string) => api.createAlbum(host, name),
    onSuccess: (data) => {
      qc.setQueryData(["albums", host], data);
      setNewName("");
      toast("Folder created.");
    },
    onError: (e) => toast((e as Error).message, "fail"),
  });
  const deleteFolder = useMutation({
    mutationFn: (name: string) => api.deleteAlbum(host, name),
    onSuccess: (data) => {
      qc.setQueryData(["albums", host], data);
      setSelected(null);
      toast("Folder deleted (photos remain on the frame).");
    },
  });
  const removeFromFolder = useMutation({
    mutationFn: (file: string) => api.removeFromAlbum(host, selected!, file),
    onSuccess: (data) => qc.setQueryData(["albums", host], data),
  });
  const deletePhoto = useMutation({
    mutationFn: (file: string) => api.deletePhoto(host, file),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["albums", host] }),
  });

  if (albums.isLoading && !albums.data) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }
  // Folder data is read live from the device; an unreachable frame errors — render an honest,
  // kind-aware offline state (slate, not red), never "Frame unavailable".
  if (albums.error) {
    return (
      <Banner
        tone="idle"
        actions={
          <Button onClick={() => albums.refetch()} disabled={albums.isFetching}>
            {albums.isFetching ? "Retrying…" : "Retry"}
          </Button>
        }
      >
        This frame is asleep or off the LAN — folder management needs it reachable. The curated set
        above still delivers when it's back.
      </Banner>
    );
  }

  const folders = albums.data ?? [];
  const current: FrameAlbum | undefined = folders.find((a) => a.name === selected);
  const userFolders = folders.filter((a) => !a.reserved);

  return (
    <div className="space-y-4">
      {/* Folder rail + create */}
      <Card className="space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-semibold">Folders</span>
          <form
            className="ml-auto flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (newName.trim()) create.mutate(newName.trim());
            }}
          >
            <input
              className="rounded bg-ink px-2 py-1 text-sm"
              placeholder="New folder…"
              value={newName}
              maxLength={64}
              onChange={(e) => setNewName(e.target.value)}
            />
            <Button disabled={create.isPending || !newName.trim()}>+ New</Button>
          </form>
        </div>

        {folders.length === 0 ? (
          <EmptyState
            icon="🗂️"
            title="No folders yet"
            desc="Create a folder, then fill it from your Immich library."
          />
        ) : (
          <div className="flex flex-wrap gap-2">
            {folders.map((a) => (
              <button
                key={a.name}
                onClick={() => setSelected(a.name === selected ? null : a.name)}
                aria-pressed={a.name === selected}
                title={a.reserved ? "Reserved folder" : a.name}
                className={`rounded-full px-3 py-1 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 ${
                  a.name === selected ? "bg-accent text-white" : "bg-edge text-slate-200"
                }`}
              >
                {a.display_name} <span className="opacity-60">({a.image_count})</span>
              </button>
            ))}
          </div>
        )}
        {userFolders.length === 0 && folders.length > 0 && (
          <p className="text-xs text-slate-500">
            Create a folder above to copy or mirror an Immich album into it.
          </p>
        )}
      </Card>

      {/* Selected folder: contents + ingest + sync */}
      {current && (
        <Card className="space-y-4 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold">{current.display_name}</span>
            <span className="text-sm text-slate-400">· {current.image_count} photos</span>
            {!current.reserved && (
              <ConfirmButton
                className="ml-auto px-2 py-0.5 text-xs"
                confirmLabel="Delete folder"
                disabled={deleteFolder.isPending}
                onConfirm={() => deleteFolder.mutate(current.name)}
                title="Delete this folder (photos remain on the frame)"
              >
                Delete folder
              </ConfirmButton>
            )}
          </div>

          <FolderSyncStatus host={host} folder={current.name} />

          {current.images.length === 0 ? (
            <p className="text-sm text-slate-500">No photos in this folder yet — add some below.</p>
          ) : (
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 md:grid-cols-6">
              {current.images.map((img) => (
                <figure key={img} className="group relative aspect-square overflow-hidden rounded-lg">
                  <img
                    src={api.frameThumbUrl(host, img)}
                    alt={img}
                    loading="lazy"
                    className="h-full w-full bg-ink object-cover"
                  />
                  {!current.reserved && (
                    <button
                      onClick={() => removeFromFolder.mutate(img)}
                      disabled={removeFromFolder.isPending}
                      aria-label={`Remove ${img} from this folder`}
                      className="absolute left-1 top-1 hidden rounded bg-black/70 px-1.5 text-xs text-amber-300 group-hover:block"
                      title="Remove from this folder (keep on frame)"
                    >
                      −
                    </button>
                  )}
                  <ConfirmButton
                    className="absolute right-1 top-1 hidden px-1.5 py-0 text-xs group-hover:block"
                    confirmLabel="Delete"
                    disabled={deletePhoto.isPending}
                    onConfirm={() => deletePhoto.mutate(img)}
                    title="Delete from the frame entirely"
                  >
                    ✕
                  </ConfirmButton>
                </figure>
              ))}
            </div>
          )}

          <div className="border-t border-edge pt-4">
            <AddToFolder host={host} folder={current.reserved ? "" : current.name} canUpload={canUpload} />
          </div>
        </Card>
      )}
    </div>
  );
}
