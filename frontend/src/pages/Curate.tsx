import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Album, FrameStatus } from "../api/types";
import { useFrames } from "../lib/frames";
import { Banner, Button, Card, EmptyState, FrameKindBadge, Skeleton, useToast } from "../ui";

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
  const [targetFolder, setTargetFolder] = useState(params.get("folder") ?? ""); // #61: dest folder
  const [previewId, setPreviewId] = useState<string | null>(null); // asset shown in the panel preview
  const fileNames = useRef(new Map<string, string>()); // asset id -> Immich filename, for #61 names
  const toast = useToast();

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
  // Bulk add/remove for 'select all' and shift-click range select (#48).
  const setMany = (ids: string[], on: boolean) =>
    setSelected((prev) => {
      const next = new Set(prev);
      ids.forEach((id) => (on ? next.add(id) : next.delete(id)));
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
            ...ids.filter((a) => !have.has(a)).map((a) => ({
              asset_id: a,
              file_name: fileNames.current.get(a),
              folder: targetFolder.trim() || undefined,
            })),
          ];
          await api.setLibrary(fid, merged);
        }),
      );
      return { photos: ids.length, frames: list.length };
    },
    onSuccess: (res) => {
      toast(
        `Added ${res.photos} ${res.photos === 1 ? "photo" : "photos"} to ${res.frames} ${
          res.frames === 1 ? "frame" : "frames"
        } — delivering now.`,
      );
      setSelected(new Set());
      [...targets].forEach((fid) =>
        qc.invalidateQueries({ queryKey: ["frame-library", fid] }),
      );
      qc.invalidateQueries({ queryKey: ["frames-status"] });
    },
    onError: (e) => toast((e as Error).message, "fail"),
  });

  const canCommit = selected.size > 0 && targets.size > 0 && !commit.isPending;
  // Preview the picked photo on the first selected target frame (#39).
  const previewTarget = (frames.data ?? []).find((f) => targets.has(f.id)) ?? null;

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
      <header className="mb-5">
        <h1 className="text-2xl font-bold tracking-tight">Curate</h1>
        <p className="mt-1 text-sm text-slate-400">
          Pick photos from Immich and send them to one or more frames. They deliver automatically.
        </p>
      </header>

      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <ImmichBrowser
          selected={selected}
          onToggle={toggleAsset}
          onBulk={setMany}
          onPick={setPreviewId}
          fileNames={fileNames}
        />

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
            <label className="block text-xs text-slate-400">
              Folder (optional)
              <input
                className="mt-1 w-full rounded bg-ink px-2 py-1.5 text-sm text-slate-200"
                placeholder="e.g. Family — blank = ungrouped"
                value={targetFolder}
                maxLength={64}
                onChange={(e) => setTargetFolder(e.target.value)}
              />
            </label>
          </Card>

          {previewId && previewTarget && (
            <PanelPreview assetId={previewId} target={previewTarget} />
          )}

          <Button
            variant="accent"
            disabled={!canCommit}
            onClick={() => commit.mutate()}
            className="w-full"
          >
            {commit.isPending
              ? "Adding…"
              : `Add ${selected.size || ""} ${selected.size === 1 ? "photo" : "photos"} to ${targets.size || ""} ${targets.size === 1 ? "frame" : "frames"}`}
          </Button>
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
            aria-pressed={on}
            className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 ${
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

/** Shows how the picked photo will actually render on the target frame's panel (#30/#39): the
 *  server-side prepared image (e-ink palette+dither or LCD JPEG), toggleable against the original. */
function PanelPreview({ assetId, target }: { assetId: string; target: FrameStatus }) {
  const [mode, setMode] = useState<"panel" | "original">("panel");
  const detail = useQuery({
    queryKey: ["frame-detail", target.id],
    queryFn: () => api.frameDetail(target.id),
  });
  const epaper = detail.data?.capabilities.color_model === "epaper";
  const src =
    mode === "panel" ? api.framePreviewUrl(target.id, assetId) : api.immichThumbUrl(assetId);

  return (
    <Card className="space-y-2 p-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold">Preview</span>
        <div className="flex gap-0.5 rounded-lg bg-ink p-0.5 text-xs">
          {(["panel", "original"] as const).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`rounded px-2 py-0.5 ${
                mode === m ? "bg-accent text-white" : "text-slate-300"
              }`}
            >
              {m === "panel" ? "On panel" : "Original"}
            </button>
          ))}
        </div>
      </div>
      <img
        key={src}
        src={src}
        alt="panel preview"
        className="max-h-64 w-full rounded-lg bg-ink object-contain"
      />
      <p className="text-xs text-slate-500">
        On <span className="text-slate-300">{target.name || target.id}</span> —{" "}
        {epaper ? "e-ink Spectra-6 (6-colour + dither)" : "full-colour LCD"}.
      </p>
    </Card>
  );
}

// ----- Immich browse: searchable album list -> asset grid, selection preserved ------------------
function ImmichBrowser({
  selected,
  onToggle,
  onBulk,
  onPick,
  fileNames,
}: {
  selected: Set<string>;
  onToggle: (id: string) => void;
  onBulk: (ids: string[], on: boolean) => void;
  onPick: (id: string) => void;
  fileNames: React.MutableRefObject<Map<string, string>>;
}) {
  const [query, setQuery] = useState("");
  const [album, setAlbum] = useState<Album | null>(null);
  const [anchor, setAnchor] = useState<number | null>(null); // for shift-click range select

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

  const images = useMemo(
    () => (assets.data ?? []).filter((a) => a.type === "IMAGE"),
    [assets.data],
  );
  // Remember each loaded asset's filename so a commit can send it for the canonical name (#61).
  useEffect(() => {
    images.forEach((a) => fileNames.current.set(a.id, a.file_name));
  }, [images, fileNames]);
  const imageIds = images.map((a) => a.id);
  const allSelected = imageIds.length > 0 && imageIds.every((id) => selected.has(id));

  // Click: plain = toggle one + set the range anchor; shift = select from the anchor to here.
  // Either way the clicked photo becomes the one shown in the panel preview (#39).
  const onAssetClick = (e: React.MouseEvent, index: number, id: string) => {
    onPick(id);
    if (e.shiftKey && anchor !== null) {
      const [lo, hi] = anchor < index ? [anchor, index] : [index, anchor];
      onBulk(
        images.slice(lo, hi + 1).map((a) => a.id),
        true,
      );
    } else {
      onToggle(id);
      setAnchor(index);
    }
  };

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
          <div className="space-y-2">
            {/* Album-level bulk controls: send a whole album without clicking each photo (#48). */}
            <div className="flex items-center gap-2 text-sm">
              <Button
                className="px-2 py-1 text-xs"
                disabled={imageIds.length === 0}
                onClick={() => onBulk(imageIds, !allSelected)}
              >
                {allSelected ? "Deselect all" : `Select all (${imageIds.length})`}
              </Button>
              <span className="text-xs text-slate-500">Tip: shift-click to select a range.</span>
            </div>
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 lg:grid-cols-5">
              {images.map((a, i) => {
                const on = selected.has(a.id);
                return (
                  <button
                    key={a.id}
                    onClick={(e) => onAssetClick(e, i, a.id)}
                    title={a.file_name}
                    aria-pressed={on}
                    aria-label={`${on ? "Selected" : "Select"} ${a.file_name}`}
                    className={`relative aspect-square overflow-hidden rounded-lg border-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 ${
                      on ? "border-accent" : "border-transparent"
                    }`}
                  >
                    <img
                      src={api.immichThumbUrl(a.id)}
                      alt={a.file_name}
                      loading="lazy"
                      className="h-full w-full select-none bg-ink object-cover"
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
          </div>
        )}
      </div>
    </Card>
  );
}
