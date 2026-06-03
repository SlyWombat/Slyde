import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

export function FramePanel({ host }: { host: string }) {
  const qc = useQueryClient();
  const { data, isLoading, error, isFetching } = useQuery({
    queryKey: ["frame", host],
    queryFn: () => api.frame(host),
    refetchInterval: 30000, // keep the "online" state honest if the frame drops
  });
  const current = useQuery({
    queryKey: ["frame-current", host],
    queryFn: () => api.currentImage(host),
    enabled: Boolean(data),
    refetchInterval: 10000, // reflect the slideshow advancing on its own
  });
  const refreshCurrent = () => qc.invalidateQueries({ queryKey: ["frame-current", host] });
  const next = useMutation({ mutationFn: () => api.next(host), onSettled: refreshCurrent });
  const prev = useMutation({ mutationFn: () => api.previous(host), onSettled: refreshCurrent });

  if (isLoading) return <div className="card">Loading frame…</div>;
  if (error)
    return (
      <div className="card border-red-500/40">
        <div className="font-semibold text-red-300">Frame unavailable</div>
        <div className="text-sm text-slate-400">{(error as Error).message}</div>
      </div>
    );

  const cfg = data!.config;
  const showing = current.data?.image ?? null;
  const moveError = (next.error ?? prev.error) as Error | undefined;
  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-lg font-semibold">{cfg.Name ?? "Memento Frame"}</div>
          <div className="text-xs text-slate-400">{data!.host}</div>
        </div>
        <span
          className={`rounded-full px-2 py-0.5 text-xs ${
            isFetching ? "bg-slate-500/15 text-slate-300" : "bg-emerald-500/15 text-emerald-300"
          }`}
        >
          {isFetching ? "checking…" : "online"}
        </span>
      </div>

      <div className="flex aspect-[3/2] items-center justify-center overflow-hidden rounded-lg bg-ink">
        {showing ? (
          <img
            src={api.frameThumbUrl(host, showing)}
            alt={showing}
            className="h-full w-full object-contain"
          />
        ) : (
          <span className="text-sm text-slate-500">
            {current.isLoading ? "Loading…" : "No image on screen"}
          </span>
        )}
      </div>
      {showing && <div className="truncate text-center text-xs text-slate-500">{showing}</div>}

      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
        <Stat label="Firmware" value={cfg.SoftwareVersion} />
        <Stat label="Screen" value={cfg.ScreenSize ? `${cfg.ScreenSize}"` : undefined} />
        <Stat label="Orientation" value={cfg.Orientation} />
        <Stat label="Slide time" value={cfg.DisplayTime ? `${cfg.DisplayTime}s` : undefined} />
      </dl>
      <div className="flex items-center gap-2 pt-1">
        <button className="btn" onClick={() => prev.mutate()} disabled={prev.isPending}>
          ‹ Previous
        </button>
        <button className="btn" onClick={() => next.mutate()} disabled={next.isPending}>
          Next ›
        </button>
        {moveError && <span className="text-xs text-red-300">{moveError.message}</span>}
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
