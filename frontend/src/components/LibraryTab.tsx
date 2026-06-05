import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { LibraryPhoto, LibraryView } from "../api/types";
import { Button, EmptyState, ErrorState, Pill, Skeleton, StatusDot, usePoll, type Tone } from "../ui";

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

  if (lib.isLoading && !lib.data) return <Skeleton className="h-64 w-full" />;
  if (lib.error)
    return <ErrorState message={(lib.error as Error).message} onRetry={() => lib.refetch()} />;

  const items = lib.data?.items ?? [];
  const d = lib.data?.deliveries;

  if (items.length === 0) {
    return (
      <EmptyState
        icon="✦"
        title="No photos curated yet"
        desc="Pick photos from Immich to build this frame's set. They deliver automatically — even if the frame is asleep."
        action={
          <Link to={`/curate?target=${encodeURIComponent(frameId)}`}>
            <Button variant="accent">Curate photos</Button>
          </Link>
        }
      />
    );
  }

  const move = (i: number, delta: number) => {
    const j = i + delta;
    if (j < 0 || j >= items.length) return;
    const next = items.slice();
    [next[i], next[j]] = [next[j], next[i]];
    write.mutate(next);
  };
  const remove = (assetId: string) =>
    write.mutate(items.filter((p) => p.asset_id !== assetId));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-slate-300">{items.length} photos</span>
        {d && d.delivered > 0 && <Pill tone="ok">{d.delivered} on frame</Pill>}
        {d && d.pending > 0 && <Pill tone="pending">{d.pending} delivering</Pill>}
        {d && d.failed > 0 && <Pill tone="fail">{d.failed} failed</Pill>}
        <Link to={`/curate?target=${encodeURIComponent(frameId)}`} className="ml-auto">
          <Button variant="accent">+ Add photos</Button>
        </Link>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4">
        {items.map((p, i) => (
          <figure
            key={p.asset_id}
            className="group relative overflow-hidden rounded-lg border border-edge bg-ink"
          >
            <img
              src={api.immichThumbUrl(p.asset_id)}
              alt={p.dest_name}
              loading="lazy"
              className="aspect-square w-full object-cover"
            />
            <figcaption className="absolute inset-x-0 bottom-0 flex items-center gap-1.5 bg-gradient-to-t from-ink/90 to-transparent px-2 py-1.5 text-[11px] text-slate-200">
              <StatusDot tone={STATE_TONE[p.state]} />
              {STATE_LABEL[p.state]}
            </figcaption>

            {/* Reorder + remove controls (hover on desktop, always-visible on touch). */}
            <div className="absolute inset-x-0 top-0 flex items-center justify-between p-1.5 opacity-0 transition group-hover:opacity-100 [@media(hover:none)]:opacity-100">
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
              <IconBtn label="Remove from frame" onClick={() => remove(p.asset_id)}>
                ✕
              </IconBtn>
            </div>
          </figure>
        ))}
      </div>
    </div>
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
