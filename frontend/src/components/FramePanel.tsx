import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

export function FramePanel({ host }: { host: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["frame", host],
    queryFn: () => api.frame(host),
  });
  const next = useMutation({ mutationFn: () => api.next(host) });
  const prev = useMutation({ mutationFn: () => api.previous(host) });

  if (isLoading) return <div className="card">Loading frame…</div>;
  if (error)
    return (
      <div className="card border-red-500/40">
        <div className="font-semibold text-red-300">Frame unavailable</div>
        <div className="text-sm text-slate-400">{(error as Error).message}</div>
      </div>
    );

  const cfg = data!.config;
  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-lg font-semibold">{cfg.Name ?? "Memento Frame"}</div>
          <div className="text-xs text-slate-400">{data!.host}</div>
        </div>
        <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-xs text-emerald-300">
          online
        </span>
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
        <Stat label="Firmware" value={cfg.SoftwareVersion} />
        <Stat label="Screen" value={cfg.ScreenSize ? `${cfg.ScreenSize}"` : undefined} />
        <Stat label="Orientation" value={cfg.Orientation} />
        <Stat label="Slide time" value={cfg.DisplayTime ? `${cfg.DisplayTime}s` : undefined} />
      </dl>
      <div className="flex gap-2 pt-1">
        <button className="btn" onClick={() => prev.mutate()} disabled={prev.isPending}>
          ‹ Previous
        </button>
        <button className="btn" onClick={() => next.mutate()} disabled={next.isPending}>
          Next ›
        </button>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: unknown }) {
  return (
    <>
      <dt className="text-slate-400">{label}</dt>
      <dd className="text-right text-slate-200">{value != null ? String(value) : "—"}</dd>
    </>
  );
}
