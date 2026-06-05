import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { FrameStatus } from "../api/types";

function relTime(iso: string | null): string {
  if (!iso) return "never";
  const then = new Date(iso.includes("T") ? iso : iso.replace(" ", "T") + "Z").getTime();
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.round(secs / 3600)}h ago`;
  return `${Math.round(secs / 86400)}d ago`;
}

/**
 * Read-only, frame-agnostic state view (#24/#25). Reflects current backend state from
 * /api/frames/status — it never drives sync. Polls every 5s; closing it affects nothing.
 */
export function StatusPanel() {
  const { data, error } = useQuery({
    queryKey: ["frames-status"],
    queryFn: api.framesStatus,
    refetchInterval: 5000,
  });

  if (error || !data || data.length === 0) return null;

  return (
    <div className="mt-8">
      <h2 className="mb-1 text-sm font-semibold uppercase tracking-wide text-slate-400">
        Frame status
      </h2>
      <p className="mb-3 text-xs text-slate-500">
        Live state from the backend (read-only). Sync runs in the background — this just reflects it.
      </p>
      <div className="space-y-2">
        {data.map((f: FrameStatus) => {
          const d = f.deliveries;
          return (
            <div key={f.id} className="card flex items-center justify-between py-3 text-sm">
              <div>
                <div className="font-semibold">{f.name || f.id}</div>
                <div className="text-xs text-slate-400">
                  {f.backend} · {f.interaction} · seen {relTime(f.last_seen)}
                </div>
              </div>
              <div className="flex gap-2 text-xs">
                {d.pending > 0 && (
                  <span className="rounded-full bg-amber-500/15 px-2 py-1 text-amber-300">
                    {d.pending} pending
                  </span>
                )}
                {d.delivered > 0 && (
                  <span className="rounded-full bg-emerald-500/15 px-2 py-1 text-emerald-300">
                    {d.delivered} delivered
                  </span>
                )}
                {d.failed > 0 && (
                  <span className="rounded-full bg-red-500/15 px-2 py-1 text-red-300">
                    {d.failed} failed
                  </span>
                )}
                {d.pending + d.delivered + d.failed === 0 && (
                  <span className="text-slate-500">idle</span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
