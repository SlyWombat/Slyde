import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { FrameStatus } from "../api/types";
import { frameHealth } from "../lib/frames";
import {
  Button,
  Card,
  FrameKindBadge,
  HealthBadge,
  Pill,
  StatusDot,
  Thumb,
  relTime,
  usePoll,
} from "../ui";

/** A frame as a first-class object (#34): kind-aware preview, health, and delivery roll-up. */
export function FrameCard({ frame }: { frame: FrameStatus }) {
  // Only LAN frames expose a live current-image we can proxy; cloud frames (served eFrame, cloud-push
  // SwitchBot) don't — so we never call the LAN endpoint for them (it would 404 -> a false ⚠).
  const live = frame.transport === "lan";
  const refetchInterval = usePoll(10000);
  const current = useQuery({
    queryKey: ["current", frame.id],
    queryFn: () => api.currentImage(frame.id),
    enabled: live,
    refetchInterval,
    retry: 0,
  });
  // The hero image is the photo the frame is showing now — resolved server-side per backend and
  // served from Slyde's own per-asset previews, so it works for every frame (LAN, served, cloud-push).
  const thumb = frame.preview_asset ? api.assetPreviewUrl(frame.preview_asset) : null;
  const online = live ? current.isSuccess : true; // cloud frames are "reachable" (we run their cloud)
  const health = frameHealth(frame);
  const d = frame.deliveries;
  const pending = d.pending > 0;

  return (
    <Card
      className={`flex flex-col overflow-hidden p-0 transition ${
        d.failed > 0 ? "ring-1 ring-red-400/30" : pending ? "ring-1 ring-accent/20" : ""
      }`}
    >
      <Thumb src={thumb} alt={frame.name} className="aspect-[3/2] w-full text-3xl text-slate-600">
        🖼️
      </Thumb>

      <div className="flex flex-1 flex-col gap-3 p-4">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="truncate font-semibold">{frame.name || frame.id}</div>
            <div className="mt-0.5 flex items-center gap-2 text-xs text-slate-400">
              <FrameKindBadge transport={frame.transport} interaction={frame.interaction} />
              <span className="truncate">{frame.backend}</span>
            </div>
          </div>
          <div className="flex items-center gap-1.5 whitespace-nowrap text-xs text-slate-400">
            <StatusDot tone={online ? "ok" : "idle"} />
            {live ? (online ? "online" : "offline") : "cloud"}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          {d.delivered > 0 && <Pill tone="ok">{d.delivered} delivered</Pill>}
          {d.pending > 0 && <Pill tone="pending">{d.pending} pending</Pill>}
          {d.failed > 0 && <Pill tone="fail">{d.failed} failed</Pill>}
          {d.delivered + d.pending + d.failed === 0 && <Pill>no photos yet</Pill>}
        </div>

        <div className="mt-auto flex items-center justify-between gap-2 pt-1">
          <HealthBadge tone={health.tone} label={health.label} pulse={pending} />
          <span className="text-xs text-slate-500">seen {relTime(frame.last_seen)}</span>
        </div>

        <div className="flex gap-2">
          <Link to={`/frames/${encodeURIComponent(frame.id)}`} className="flex-1">
            <Button className="w-full">Open</Button>
          </Link>
          <Link to="/curate" className="flex-1">
            <Button variant="accent" className="w-full">
              Curate +
            </Button>
          </Link>
        </div>
      </div>
    </Card>
  );
}
