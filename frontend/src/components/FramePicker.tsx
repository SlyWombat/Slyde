import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

export function FramePicker({ onSelect }: { onSelect: (host: string) => void }) {
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["frames"],
    queryFn: api.frames,
  });

  return (
    <div className="mx-auto max-w-2xl px-4 py-12">
      <h1 className="mb-1 text-2xl font-bold tracking-tight">
        Memento <span className="text-accent">Manager</span>
      </h1>
      <p className="mb-6 text-sm text-slate-400">Select a frame to manage.</p>

      {isLoading && <div className="card">Scanning the network for frames…</div>}
      {error && (
        <div className="card border-red-500/40 text-sm text-red-300">
          {(error as Error).message}
        </div>
      )}

      <div className="space-y-3">
        {data?.map((f) => (
          <button
            key={f.ip}
            onClick={() => onSelect(f.ip)}
            className="card flex w-full items-center justify-between text-left hover:border-accent"
          >
            <div>
              <div className="text-lg font-semibold">{f.name || f.ip}</div>
              <div className="text-xs text-slate-400">
                {f.ip} · {f.size ? `${f.size}" ` : ""}
                {f.orientation} {f.softver ? `· fw ${f.softver}` : ""}
              </div>
            </div>
            <span className="text-accent">Manage ›</span>
          </button>
        ))}
        {data && data.length === 0 && (
          <div className="card text-sm text-slate-400">
            No frames found. Ensure the frame is on and on this network (or set FRAME_HOST).
          </div>
        )}
      </div>

      <button className="btn mt-6" onClick={() => refetch()} disabled={isFetching}>
        {isFetching ? "Scanning…" : "Rescan"}
      </button>
    </div>
  );
}
