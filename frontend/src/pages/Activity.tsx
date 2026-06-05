import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { FrameStatus, Subscription } from "../api/types";
import { frameHealth, isConnected, useFrames } from "../lib/frames";
import {
  Banner,
  Button,
  Card,
  EmptyState,
  FrameKindBadge,
  HealthBadge,
  Pill,
  Skeleton,
  relTime,
  usePoll,
} from "../ui";

type KindFilter = "all" | "connected" | "served";

/**
 * Global Activity — a pure read-only mirror of backend state (#40/#25). Delivery queue per frame
 * (`/frames/status`, 5s), scheduled-sync health (`/health/sync`), and per-frame subscriptions.
 * The one write allowed is Retry, which re-PUTs a frame's library to nudge delivery (non-blocking).
 * Closing this view changes nothing.
 */
export function Activity() {
  const refetchInterval = usePoll(5000);
  const frames = useFrames();
  const sync = useQuery({ queryKey: ["sync-health"], queryFn: api.syncHealth, refetchInterval });

  const [kind, setKind] = useState<KindFilter>("all");
  const [query, setQuery] = useState("");

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (frames.data ?? [])
      .filter((f) => (kind === "all" ? true : f.interaction === kind))
      .filter((f) => !q || (f.name || f.id).toLowerCase().includes(q) || f.backend.includes(q));
  }, [frames.data, kind, query]);

  const totals = useMemo(() => {
    const acc = { pending: 0, delivered: 0, failed: 0 };
    for (const f of frames.data ?? []) {
      acc.pending += f.deliveries.pending;
      acc.delivered += f.deliveries.delivered;
      acc.failed += f.deliveries.failed;
    }
    return acc;
  }, [frames.data]);

  return (
    <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6">
      <header className="mb-5">
        <h1 className="text-2xl font-bold tracking-tight">Activity</h1>
        <p className="mt-1 text-sm text-slate-400">
          Live state from the backend — read-only. Delivery and sync run on their own; this just
          reflects them.
        </p>
      </header>

      <SyncBanner text={sync.data} loading={sync.isLoading} />

      {/* Fleet-wide delivery roll-up */}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <span className="text-sm text-slate-300">Across all frames:</span>
        <Pill tone="ok">{totals.delivered} delivered</Pill>
        <Pill tone="pending">{totals.pending} pending</Pill>
        <Pill tone="fail">{totals.failed} failed</Pill>
      </div>

      {/* Filters */}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <div className="flex gap-1 rounded-lg bg-ink p-1 text-sm">
          {(["all", "connected", "served"] as KindFilter[]).map((k) => (
            <button
              key={k}
              onClick={() => setKind(k)}
              className={`rounded px-2.5 py-1 capitalize ${
                kind === k ? "bg-accent text-white" : "text-slate-300"
              }`}
            >
              {k}
            </button>
          ))}
        </div>
        <input
          className="ml-auto w-44 rounded bg-ink px-3 py-1.5 text-sm"
          placeholder="Filter frames…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      <div className="mt-4 space-y-2">
        {frames.isLoading && !frames.data ? (
          <Skeleton className="h-40 w-full" />
        ) : rows.length === 0 ? (
          <EmptyState
            icon="↻"
            title="Nothing to show"
            desc={
              (frames.data ?? []).length === 0
                ? "No frames registered yet — activity appears as soon as a frame is added."
                : "No frames match this filter."
            }
          />
        ) : (
          rows.map((f) => <ActivityRow key={f.id} frame={f} />)
        )}
      </div>
    </div>
  );
}

function SyncBanner({ text, loading }: { text: string | undefined; loading: boolean }) {
  if (loading && !text) return <Skeleton className="h-12 w-full" />;
  if (!text) return null;
  const failed = text.startsWith("FAIL");
  return (
    <Banner tone={failed ? "fail" : "ok"}>
      <span className="font-medium">Scheduled sync:</span> {text}
    </Banner>
  );
}

function ActivityRow({ frame }: { frame: FrameStatus }) {
  const qc = useQueryClient();
  const conn = isConnected(frame);
  const health = frameHealth(frame);
  const d = frame.deliveries;

  // Retry: re-PUT the frame's library unchanged to re-queue + reconcile failed deliveries. The
  // backend handles backoff; this just nudges. Non-blocking — returns as soon as it's queued.
  const retry = useMutation({
    mutationFn: async () => {
      const lib = await api.frameLibrary(frame.id);
      await api.setLibrary(
        frame.id,
        lib.items.map((p) => ({ asset_id: p.asset_id, dest_name: p.dest_name })),
      );
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["frames-status"] });
      qc.invalidateQueries({ queryKey: ["frame-library", frame.id] });
    },
  });

  return (
    <Card className={`p-4 ${d.failed > 0 ? "ring-1 ring-red-400/30" : ""}`}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <Link to={`/frames/${encodeURIComponent(frame.id)}`} className="min-w-0 flex-1">
          <span className="truncate font-semibold hover:text-accent">{frame.name || frame.id}</span>
        </Link>
        <FrameKindBadge interaction={frame.interaction} />
        <HealthBadge tone={health.tone} label={health.label} pulse={d.pending > 0} />
        <span className="text-xs text-slate-500">seen {relTime(frame.last_seen)}</span>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        {d.delivered > 0 && <Pill tone="ok">{d.delivered} delivered</Pill>}
        {d.pending > 0 && <Pill tone="pending">{d.pending} pending</Pill>}
        {d.failed > 0 && <Pill tone="fail">{d.failed} failed</Pill>}
        {d.delivered + d.pending + d.failed === 0 && <Pill>no deliveries</Pill>}

        {d.failed > 0 && (
          <Button
            className="ml-auto px-2 py-0.5 text-xs"
            disabled={retry.isPending}
            onClick={() => retry.mutate()}
          >
            {retry.isPending ? "Retrying…" : "Retry"}
          </Button>
        )}
      </div>
      {retry.error && (
        <p className="mt-2 text-xs text-red-300">{(retry.error as Error).message}</p>
      )}

      {conn && <Subscriptions host={frame.id} />}
    </Card>
  );
}

/** Per-connected-frame album subscriptions (kept-in-sync mirrors). Served frames have none. */
function Subscriptions({ host }: { host: string }) {
  const subs = useQuery({
    queryKey: ["subscriptions", host],
    queryFn: () => api.subscriptions(host),
  });
  const list: Subscription[] = subs.data ?? [];
  if (list.length === 0) return null;
  return (
    <div className="mt-3 border-t border-edge pt-3">
      <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
        Kept in sync
      </div>
      <ul className="space-y-1 text-xs text-slate-400">
        {list.map((s) => (
          <li key={s.immich_album_id} className="flex items-center justify-between gap-2">
            <span className="truncate">→ {s.target_album}</span>
            <span className="shrink-0 text-slate-500">
              {s.last_result ?? "pending"} · {relTime(s.last_synced_at)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
