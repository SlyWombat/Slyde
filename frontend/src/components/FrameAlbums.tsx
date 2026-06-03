import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { FrameAlbum } from "../api/types";

export function FrameAlbums({
  host,
  selected,
  onSelect,
}: {
  host: string;
  selected: string | null;
  onSelect: (album: string | null) => void;
}) {
  const qc = useQueryClient();
  const [newName, setNewName] = useState("");
  const albums = useQuery({ queryKey: ["albums", host], queryFn: () => api.albums(host) });

  const create = useMutation({
    mutationFn: (name: string) => api.createAlbum(host, name),
    onSuccess: (data) => {
      qc.setQueryData(["albums", host], data);
      setNewName("");
    },
  });
  const remove = useMutation({
    mutationFn: (filename: string) => api.deletePhoto(host, filename),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["albums", host] }),
  });

  const current: FrameAlbum | undefined = albums.data?.find((a) => a.name === selected);

  return (
    <div className="card space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">On the frame</span>
        <form
          className="ml-auto flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (newName.trim()) create.mutate(newName.trim());
          }}
        >
          <input
            className="rounded bg-ink px-2 py-1 text-sm"
            placeholder="New album…"
            value={newName}
            maxLength={64}
            onChange={(e) => setNewName(e.target.value)}
          />
          <button className="btn" disabled={create.isPending || !newName.trim()}>
            Create
          </button>
        </form>
      </div>

      {albums.isLoading && <div className="text-sm text-slate-400">Loading albums…</div>}
      {albums.error && (
        <div className="text-sm text-red-300">{(albums.error as Error).message}</div>
      )}

      <div className="flex flex-wrap gap-2">
        {albums.data?.map((a) => (
          <button
            key={a.name}
            onClick={() => onSelect(a.name === selected ? null : a.name)}
            className={`rounded-full px-3 py-1 text-sm ${
              a.name === selected ? "bg-accent text-white" : "bg-edge text-slate-200"
            }`}
            title={a.reserved ? "Reserved album" : a.name}
          >
            {a.display_name} <span className="opacity-60">({a.image_count})</span>
          </button>
        ))}
      </div>

      {current && (
        <div>
          <div className="mb-2 text-sm text-slate-400">
            {current.display_name} — {current.image_count} photos
          </div>
          <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 md:grid-cols-6">
            {current.images.map((img) => (
              <div key={img} className="group relative aspect-square overflow-hidden rounded-lg">
                <img
                  src={api.frameThumbUrl(host, img)}
                  alt={img}
                  loading="lazy"
                  className="h-full w-full bg-ink object-cover"
                />
                <button
                  onClick={() => remove.mutate(img)}
                  disabled={remove.isPending}
                  className="absolute right-1 top-1 hidden rounded bg-black/70 px-1.5 text-xs
                             text-red-300 group-hover:block"
                  title="Remove from frame"
                >
                  ✕
                </button>
              </div>
            ))}
            {current.images.length === 0 && (
              <div className="col-span-full text-sm text-slate-500">Empty album.</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
