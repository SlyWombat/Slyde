import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { FrameStatus, FrameSummary } from "../api/types";
import { frameHealth, useFrames } from "../lib/frames";
import {
  Banner,
  Button,
  Card,
  EmptyState,
  FrameKindBadge,
  HealthBadge,
  Pill,
  Skeleton,
  StatusDot,
  relTime,
  useToast,
} from "../ui";

type KindFilter = "all" | "connected" | "served";

/**
 * Searchable, filterable list of every registered frame + onboarding (#35). Two add paths:
 * connected (scan the LAN, then manage a discovered/configured host) and served (register a cloud
 * frame by its frame-code so it appears in status before its first poll). Scales to 10+ frames.
 */
export function FramesList() {
  const [params] = useSearchParams();
  const frames = useFrames();
  const [kind, setKind] = useState<KindFilter>("all");
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState(params.get("add") != null);
  const qc = useQueryClient();
  const toast = useToast();
  // Manual active LAN scan — finds connected frames (and relocates one after a DHCP change) where
  // UDP discovery can't run. User-triggered only (#58).
  const scan = useMutation({
    mutationFn: api.scanFrames,
    onSuccess: (found) => {
      qc.invalidateQueries({ queryKey: ["frames-status"] });
      toast(
        found.length
          ? `Found ${found.length} frame${found.length === 1 ? "" : "s"} on the network.`
          : "No frames found on the network.",
      );
    },
    onError: (e) => toast((e as Error).message, "fail"),
  });

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (frames.data ?? [])
      .filter((f) => (kind === "all" ? true : f.interaction === kind))
      .filter((f) => !q || (f.name || f.id).toLowerCase().includes(q) || f.backend.includes(q))
      .sort((a, b) => (a.name || a.id).localeCompare(b.name || b.id));
  }, [frames.data, kind, query]);

  const total = (frames.data ?? []).length;

  return (
    <div className="mx-auto max-w-4xl px-4 py-6 sm:px-6">
      <header className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">All frames</h1>
          <p className="mt-1 text-sm text-slate-400">
            {total} registered · search, filter, and add frames.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            onClick={() => scan.mutate()}
            disabled={scan.isPending}
            title="Actively probe the LAN for frames (finds them at any IP)"
          >
            {scan.isPending ? "Scanning…" : "Scan LAN"}
          </Button>
          <Button variant={adding ? "default" : "accent"} onClick={() => setAdding((v) => !v)}>
            {adding ? "Close" : "+ Add frame"}
          </Button>
        </div>
      </header>

      {adding && <AddFrame initial={params.get("add")} onClose={() => setAdding(false)} />}

      {/* Filters */}
      <div className="mb-4 mt-4 flex flex-wrap items-center gap-2">
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
          className="ml-auto w-48 rounded bg-ink px-3 py-1.5 text-sm"
          placeholder="Search frames…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      {frames.isLoading && !frames.data ? (
        <Skeleton className="h-40 w-full" />
      ) : rows.length === 0 ? (
        <EmptyState
          icon="▦"
          title={total === 0 ? "No frames yet" : "No frames match"}
          desc={
            total === 0
              ? "Add a frame on your network or a cloud frame to get started."
              : "Try a different filter or search term."
          }
          action={
            total === 0 ? (
              <Button variant="accent" onClick={() => setAdding(true)}>
                Add a frame
              </Button>
            ) : undefined
          }
        />
      ) : (
        <ul className="space-y-2">
          {rows.map((f) => (
            <FrameRow key={f.id} frame={f} />
          ))}
        </ul>
      )}
    </div>
  );
}

function FrameRow({ frame }: { frame: FrameStatus }) {
  const health = frameHealth(frame);
  const d = frame.deliveries;
  return (
    <li>
      <Link to={`/frames/${encodeURIComponent(frame.id)}`}>
        <Card className="flex items-center gap-3 p-3 transition hover:border-accent">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="truncate font-semibold">{frame.name || frame.id}</span>
              <FrameKindBadge interaction={frame.interaction} />
            </div>
            <div className="mt-0.5 truncate text-xs text-slate-400">
              {frame.backend} · seen {relTime(frame.last_seen)}
            </div>
          </div>
          <div className="hidden flex-wrap items-center justify-end gap-1.5 sm:flex">
            {d.delivered > 0 && <Pill tone="ok">{d.delivered}</Pill>}
            {d.pending > 0 && <Pill tone="pending">{d.pending}</Pill>}
            {d.failed > 0 && <Pill tone="fail">{d.failed}</Pill>}
          </div>
          <HealthBadge tone={health.tone} label={health.label} pulse={d.pending > 0} />
          <span className="text-accent" aria-hidden>
            ›
          </span>
        </Card>
      </Link>
    </li>
  );
}

// ----- Onboarding: connected (LAN scan) + served (frame-code) -----------------------------------
function AddFrame({ initial, onClose }: { initial: string | null; onClose: () => void }) {
  const [path, setPath] = useState<"connected" | "served">(
    initial === "served" ? "served" : "connected",
  );
  return (
    <Card className="space-y-4 p-4">
      <div className="flex gap-1 rounded-lg bg-ink p-1 text-sm">
        <PathTab active={path === "connected"} onClick={() => setPath("connected")}>
          On this network
        </PathTab>
        <PathTab active={path === "served"} onClick={() => setPath("served")}>
          Cloud frame
        </PathTab>
      </div>
      {path === "connected" ? <ConnectedOnboard /> : <ServedOnboard onAdded={onClose} />}
    </Card>
  );
}

function PathTab({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex-1 rounded px-3 py-1.5 ${active ? "bg-accent text-white" : "text-slate-300"}`}
    >
      {children}
    </button>
  );
}

/** Connected path: scan the LAN (+ configured hosts) and open a host to manage it (FramePicker parity). */
function ConnectedOnboard() {
  const navigate = useNavigate();
  const scan = useQuery({ queryKey: ["frames"], queryFn: api.frames });
  const found: FrameSummary[] = scan.data ?? [];

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <p className="text-sm text-slate-400">
          Memento / Pi soft-frames on your network announce themselves.
        </p>
        <Button
          className="ml-auto px-2 py-1 text-xs"
          disabled={scan.isFetching}
          onClick={() => scan.refetch()}
        >
          {scan.isFetching ? "Scanning…" : "Rescan"}
        </Button>
      </div>

      {scan.isLoading && <Skeleton className="h-20 w-full" />}
      {scan.error && <Banner tone="fail">{(scan.error as Error).message}</Banner>}

      {scan.data && found.length === 0 && (
        <Banner tone="idle">
          No frames found on the LAN. Make sure the frame is powered on and on this network (or set
          FRAME_HOST).
        </Banner>
      )}

      <ul className="space-y-2">
        {found.map((f) => (
          <li key={f.ip}>
            <button
              onClick={() => navigate(`/frames/${encodeURIComponent(f.ip)}`)}
              className="card flex w-full items-center justify-between text-left hover:border-accent"
            >
              <div className="min-w-0">
                <div className="truncate font-semibold">{f.name || f.ip}</div>
                <div className="text-xs text-slate-400">
                  {f.ip}
                  {f.size ? ` · ${f.size}"` : ""}
                  {f.orientation ? ` · ${f.orientation}` : ""}
                  {f.softver ? ` · fw ${f.softver}` : ""}
                </div>
              </div>
              <span className="shrink-0 text-accent">Manage ›</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Served path: register a cloud frame by its frame-code so it shows in status before first poll. */
function ServedOnboard({ onAdded }: { onAdded: () => void }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [code, setCode] = useState("");
  const [name, setName] = useState("");

  const register = useMutation({
    mutationFn: () => api.registerFrame({ frame_code: code.trim(), name: name.trim() || undefined }),
    onSuccess: (frame) => {
      qc.invalidateQueries({ queryKey: ["frames-status"] });
      onAdded();
      navigate(`/frames/${encodeURIComponent(frame.id)}`);
    },
  });

  return (
    <form
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (code.trim()) register.mutate();
      }}
    >
      <p className="text-sm text-slate-400">
        A cloud frame polls a server we run. Enter its frame-code to add it now — you can curate to
        it before it next checks in.
      </p>
      <label className="block text-sm">
        <span className="text-slate-300">Frame code</span>
        <input
          autoFocus
          className="mt-1 w-full rounded bg-ink px-3 py-2"
          placeholder="e.g. EFRAME-1234"
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
      </label>
      <label className="block text-sm">
        <span className="text-slate-300">Name (optional)</span>
        <input
          className="mt-1 w-full rounded bg-ink px-3 py-2"
          placeholder="Kitchen frame"
          value={name}
          maxLength={64}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      {register.error && <Banner tone="fail">{(register.error as Error).message}</Banner>}
      <div className="flex items-center gap-2">
        <Button type="submit" variant="accent" disabled={!code.trim() || register.isPending}>
          {register.isPending ? "Adding…" : "Add frame"}
        </Button>
        <span className="inline-flex items-center gap-1.5 text-xs text-slate-500">
          <StatusDot tone="idle" /> Appears immediately; delivers when it polls.
        </span>
      </div>
    </form>
  );
}
