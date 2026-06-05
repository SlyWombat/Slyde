import { Link } from "react-router-dom";
import { FrameCard } from "../components/FrameCard";
import { useFrames } from "../lib/frames";
import { api } from "../api/client";
import { useQuery } from "@tanstack/react-query";
import { Banner, Button, EmptyState, ErrorState, Skeleton } from "../ui";

/** Fleet dashboard (#34) — the app home: every frame as a card, from /api/frames/status. */
export function FleetDashboard() {
  const { data, isLoading, error, refetch } = useFrames();
  const immich = useQuery({ queryKey: ["health"], queryFn: api.health });
  const frames = data ?? [];
  const attention = frames.filter((f) => f.deliveries.failed > 0);
  const healthy = frames.length - attention.length;

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <header className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Picture Frames</h1>
          <p className="mt-1 text-sm text-slate-400">
            {frames.length} {frames.length === 1 ? "frame" : "frames"} · {healthy} healthy
            {attention.length > 0 && ` · ${attention.length} need${attention.length === 1 ? "s" : ""} attention`}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span className="hidden items-center gap-1.5 text-xs text-slate-400 sm:flex">
            <span className={`h-2 w-2 rounded-full ${immich.data?.immich_configured ? "bg-emerald-400" : "bg-slate-500"}`} />
            Immich {immich.data?.immich_configured ? "connected" : "not configured"}
          </span>
          {/* Single Add affordance per context: header primary only once frames exist; when empty,
              the empty-state CTA below is the one hero action. */}
          {frames.length > 0 && (
            <Link to="/frames">
              <Button variant="accent">+ Add frame</Button>
            </Link>
          )}
        </div>
      </header>

      {error && <div className="mb-4"><ErrorState message={(error as Error).message} onRetry={() => refetch()} /></div>}

      {attention.length > 0 && (
        <div className="mb-4">
          <Banner
            tone="fail"
            actions={
              <Link to="/activity">
                <Button>View</Button>
              </Link>
            }
          >
            {attention.length === 1
              ? `"${attention[0].name || attention[0].id}" has ${attention[0].deliveries.failed} failed deliver${attention[0].deliveries.failed === 1 ? "y" : "ies"}.`
              : `${attention.length} frames have failed deliveries.`}
          </Banner>
        </div>
      )}

      {isLoading && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-72 w-full" />
          ))}
        </div>
      )}

      {!isLoading && !error && frames.length === 0 && (
        <EmptyState
          title="No frames yet"
          desc="Add your first frame — a Memento or Pi soft-frame on your LAN, or a cloud frame — to start curating photos from Immich."
          action={
            <Link to="/frames">
              <Button variant="accent">Add a frame</Button>
            </Link>
          }
        />
      )}

      {frames.length > 0 && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {frames.map((f) => (
            <FrameCard key={f.id} frame={f} />
          ))}
        </div>
      )}
    </div>
  );
}
