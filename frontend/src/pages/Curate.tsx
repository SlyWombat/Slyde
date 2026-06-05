import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Album, FrameStatus } from "../api/types";
import { useFrames } from "../lib/frames";
import { Banner, Button, Card, EmptyState, FrameKindBadge, Skeleton } from "../ui";

/**
 * Immich-first, multi-target curation (#38). Browse Immich → build a selection (preserved across
 * album switches) → pick one or more target frames → commit `PUT /frames/{id}/library` per target,
 * fire-and-forget. Each target's set is *merged* (existing photos kept). Strictly non-blocking: the
 * backend queues + delivers in the background, so this returns immediately for every target.
 */
export function Curate() {
  const [params] = useSearchParams();
  const preTarget = params.get("target");

  const frames = useFrames();
  const [selected, setSelected] = useState<Set<string>>(new Set()); // Immich asset ids
  const [targets, setTargets] = useState<Set<string>>(
    () => new Set(preTarget ? [preTarget] : []),
  );
  const [done, setDone] = useState<{ photos: number; frames: number } | null>(null);

  const toggleAsset = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const toggleTarget = (id: string) =>
    setTargets((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const qc = useQueryClient();
  const commit = useMutation({
    mutationFn: async () => {
      const ids = [...selected];
      const list = [...targets];
      // Per target: read its current set, merge in the new asset ids, re-PUT. Independent + parallel.
      await Promise.all(
        list.map(async (fid) => {
          const cur = await api.frameLibrary(fid);
          const have = new Set(cur.items.map((i) => i.asset_id));
          const merged = [
            ...cur.items.map((i) => ({ asset_id: i.asset_id, dest_name: i.dest_name })),
            ...ids.filter((a) => !have.has(a)).map((a) => ({ asset_id: a })),
          ];
          await api.setLibrary(fid, merged);
        }),
      );
      return { photos: ids.length, frames: list.length };
    },
    onSuccess: (res) => {
      setDone(res);
      setSelected(new Set());
      [...targets].forEach((fid) =>
        qc.invalidateQueries({ queryKey: ["frame-library", fid] }),
      );
      qc.invalidateQueries({ queryKey: ["frames-status"] });
    },
  });

  const canCommit = selected.size > 0 && targets.size > 0 && !commit.isPending;

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
      <header className="mb-5">
        <h1 className="text-2xl font-bold tracking-tight">Curate</h1>
        <p className="mt-1 text-sm text-slate-400">
          Pick photos from Immich and send them to one or more frames. They deliver automatically.
        </p>
      </header>

      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <ImmichBrowser selected={selected} onToggle={toggleAsset} />

        {/* Selection + targets + commit (sticky on desktop) */}
        <aside className="space-y-4 lg:sticky lg:top-6 lg:self-start">
          <Card className="space-y-3 p-4">
            <div className="flex items-center justify-between">
              <span className="font-semibold">Selection</span>
              <span className="text-sm text-slate-400">{selected.size} photos</span>
            </div>
            {selected.size > 0 && (
              <button
                onClick={() => setSelected(new Set())}
                className="text-xs text-slate-400 hover:text-slate-200"
              >
                Clear selection
              </button>
            )}
          </Card>

          <Card className="space-y-3 p-4">
            <span className="font-semibold">Send to</span>
            <TargetPicker frames={frames.data} loading={frames.isLoading} selected={targets} onToggle={toggleTarget} />
          </Card>

          <Button
            variant="accent"
            disabled={!canCommit}
            onClick={() => {
              setDone(null);
              commit.mutate();
            }}
            className="w-full"
          >
            {commit.isPending
              ? "Adding…"
              : `Add ${selected.size || ""} ${selected.size === 1 ? "photo" : "photos"} to ${targets.size || ""} ${targets.size === 1 ? "frame" : "frames"}`}
          </Button>

          {commit.error && (
            <Banner tone="fail">{(commit.error as Error).message}</Banner>
          )}
          {done && (
            <Banner tone="ok">
              Added {done.photos} {done.photos === 1 ? "photo" : "photos"} to {done.frames}{" "}
              {done.frames === 1 ? "frame" : "frames"} — delivering now.
            </Banner>
          )}
        </aside>
      </div>
    </div>
  );
}

function TargetPicker({
  frames,
  loading,
  selected,
  onToggle,
}: {
  frames: FrameStatus[] | undefined;
  loading: boolean;
  selected: Set<string>;
  onToggle: (id: string) => void;
}) {
  if (loading && !frames) return <Skeleton className="h-20 w-full" />;
  if (!frames || frames.length === 0)
    return <p className="text-sm text-slate-500">No frames registered yet.</p>;

  return (
    <div className="flex flex-col gap-1.5">
      {frames.map((f) => {
        const on = selected.has(f.id);
        return (
          <button
            key={f.id}
            onClick={() => onToggle(f.id)}
            className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm transition ${
              on ? "border-accent bg-accent/10" : "border-edge hover:bg-edge"
            }`}
          >
            <span
              className={`flex h-4 w-4 items-center justify-center rounded border text-[10px] ${
                on ? "border-accent bg-accent text-white" : "border-slate-500"
              }`}
            >
              {on ? "✓" : ""}
            </span>
            <span className="min-w-0 flex-1 truncate">{f.name || f.id}</span>
            <FrameKindBadge interaction={f.interaction} />
          </button>
        );
      })}
    </div>
  );
}

// ----- Immich browse: searchable album list -> asset grid, selection preserved ------------------
function ImmichBrowser({
  selected,
  onToggle,
}: {
  selected: Set<string>;
  onToggle: (id: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [album, setAlbum] = useState<Album | null>(null);

  const albums = useQuery({ queryKey: ["immich-albums"], queryFn: api.immichAlbums });
  const assets = useQuery({
    queryKey: ["immich-assets", album?.id],
    queryFn: () => api.immichAssets(album!.id),
    enabled: !!album,
  });

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (albums.data ?? [])
      .filter((a) => !q || a.name.toLowerCase().includes(q))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [albums.data, query]);

  if (albums.isLoading) return <Skeleton className="h-96 w-full" />;
  if (albums.error)
    return (
      <Card className="p-4">
        <Banner tone="fail">{(albums.error as Error).message}</Banner>
      </Card>
    );
  if ((albums.data ?? []).length === 0)
    return (
      <EmptyState
        title="No Immich albums"
        desc="Connect Immich in Settings, then your albums will show up here to curate from."
      />
    );

  return (
    <Card className="grid gap-4 p-4 md:grid-cols-[240px_1fr]">
      {/* Album list */}
      <div className="space-y-2">
        <input
          className="w-full rounded bg-ink px-3 py-2 text-sm"
          placeholder={`Search ${albums.data?.length ?? 0} albums…`}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <ul className="max-h-[28rem] divide-y divide-edge overflow-auto rounded-lg border border-edge">
          {filtered.map((a) => (
            <li key={a.id}>
              <button
                onClick={() => setAlbum(a)}
                className={`flex w-full items-center justify-between px-3 py-2 text-left text-sm ${
                  album?.id === a.id ? "bg-accent/10 text-accent" : "hover:bg-edge"
                }`}
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

      {/* Asset grid for the open album */}
      <div className="min-h-[20rem]">
        {!album ? (
          <div className="flex h-full items-center justify-center text-sm text-slate-500">
            Pick an album to browse its photos.
          </div>
        ) : assets.isLoading ? (
          <Skeleton className="h-80 w-full" />
        ) : (
          <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 lg:grid-cols-5">
            {assets.data
              ?.filter((a) => a.type === "IMAGE")
              .map((a) => {
                const on = selected.has(a.id);
                return (
                  <button
                    key={a.id}
                    onClick={() => onToggle(a.id)}
                    title={a.file_name}
                    className={`relative aspect-square overflow-hidden rounded-lg border-2 ${
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
    </Card>
  );
}
