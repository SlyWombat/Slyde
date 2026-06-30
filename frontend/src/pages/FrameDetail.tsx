import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { LibraryTab } from "../components/LibraryTab";
import { SettingsTab } from "../components/SettingsTab";
import { frameHealth, isConnected, useFrame } from "../lib/frames";
import type { FrameStatus } from "../api/types";
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
  Thumb,
  relTime,
  usePoll,
} from "../ui";

type TabKey = "overview" | "library" | "settings" | "firmware";

/** Tabbed, capability-gated frame detail (#36). Connected frames show every tab; served frames
 *  show only the transport-agnostic ones (no live albums/settings/firmware). Never a dead-end. */
export function FrameDetail() {
  const { id = "" } = useParams();
  const frameId = decodeURIComponent(id);
  const { frame, isLoading } = useFrame(frameId);
  const [tab, setTab] = useState<TabKey>("overview");
  // Capabilities for tab gating (#56); declared before any early return to keep hook order stable.
  const detail = useQuery({
    queryKey: ["frame-detail", frameId],
    queryFn: () => api.frameDetail(frameId),
    retry: 0,
  });

  if (isLoading && !frame) {
    return (
      <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="mt-4 h-72 w-full" />
      </div>
    );
  }
  if (!frame) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-16">
        <EmptyState
          title="Frame not found"
          desc="It may have been removed, or never registered."
          action={
            <Link to="/">
              <Button variant="accent">Back to Picture Frames</Button>
            </Link>
          }
        />
      </div>
    );
  }

  const conn = isConnected(frame);
  // Capabilities drive tab gating more honestly than interaction alone (#56); fall back to `conn`.
  const caps = detail.data?.capabilities;
  const tabs: { key: TabKey; label: string; show: boolean }[] = [
    { key: "overview", label: "Overview", show: true },
    { key: "library", label: "Library", show: true },
    { key: "settings", label: "Settings", show: true },
    { key: "firmware", label: "Firmware", show: caps?.ota ?? conn },
  ];
  const visible = tabs.filter((t) => t.show);
  const active = visible.some((t) => t.key === tab) ? tab : "overview";
  const health = frameHealth(frame);

  return (
    <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6">
      <Link to="/" className="text-sm text-slate-400 hover:text-slate-200">
        ‹ Picture Frames
      </Link>
      <header className="mb-5 mt-2 flex flex-wrap items-center gap-x-3 gap-y-2">
        <h1 className="text-2xl font-bold tracking-tight">{frame.name || frame.id}</h1>
        <FrameKindBadge transport={frame.transport} interaction={frame.interaction} />
        <HealthBadge tone={health.tone} label={health.label} pulse={frame.deliveries.pending > 0} />
        <span className="text-sm text-slate-500">
          {frame.backend} · seen {relTime(frame.last_seen)}
        </span>
      </header>

      <div
        role="tablist"
        aria-label="Frame sections"
        className="flex flex-wrap gap-1 border-b border-edge"
        onKeyDown={(e) => {
          // Arrow/Home/End move between tabs (WAI-ARIA tabs pattern) (#43).
          const keys = visible.map((t) => t.key);
          const i = keys.indexOf(active);
          const to =
            e.key === "ArrowRight"
              ? (i + 1) % keys.length
              : e.key === "ArrowLeft"
                ? (i - 1 + keys.length) % keys.length
                : e.key === "Home"
                  ? 0
                  : e.key === "End"
                    ? keys.length - 1
                    : -1;
          if (to < 0) return;
          e.preventDefault();
          setTab(keys[to]);
          document.getElementById(`tab-${keys[to]}`)?.focus();
        }}
      >
        {visible.map((t) => (
          <button
            key={t.key}
            id={`tab-${t.key}`}
            role="tab"
            aria-selected={active === t.key}
            aria-controls="frame-tabpanel"
            tabIndex={active === t.key ? 0 : -1}
            onClick={() => setTab(t.key)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 ${
              active === t.key
                ? "border-accent text-accent"
                : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div id="frame-tabpanel" role="tabpanel" className="mt-4">
        {active === "overview" && <OverviewTab frame={frame} />}
        {active === "library" && <LibraryTab frameId={frame.id} />}
        {active === "settings" && <SettingsTab frame={frame} />}
        {active === "firmware" && conn && <FirmwareTab host={frame.id} />}
      </div>
    </div>
  );
}

function OverviewTab({ frame }: { frame: FrameStatus }) {
  const conn = isConnected(frame);
  const cfgInterval = usePoll(30000);
  const curInterval = usePoll(10000);
  const qc = useQueryClient();
  const cfgQ = useQuery({
    queryKey: ["frame", frame.id],
    queryFn: () => api.frame(frame.id),
    enabled: conn,
    retry: 0,
    refetchInterval: cfgInterval,
  });
  const curQ = useQuery({
    queryKey: ["frame-current", frame.id],
    queryFn: () => api.currentImage(frame.id),
    enabled: conn && cfgQ.isSuccess,
    retry: 0,
    refetchInterval: curInterval,
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["frame-current", frame.id] });
  const next = useMutation({ mutationFn: () => api.next(frame.id), onSettled: refresh });
  const prev = useMutation({ mutationFn: () => api.previous(frame.id), onSettled: refresh });

  const d = frame.deliveries;
  const cfg = cfgQ.data?.config;
  const showing = curQ.data?.image ?? null;
  const offline = conn && cfgQ.isError;

  return (
    <div className="grid gap-4 md:grid-cols-[1fr_280px]">
      <Card className="p-3">
        <Thumb
          src={conn && showing ? api.frameThumbUrl(frame.id, showing) : null}
          alt={showing ?? frame.name}
          className="aspect-[3/2] w-full rounded-lg text-4xl text-slate-600"
        >
          {conn ? (offline ? "⚠" : cfgQ.isLoading ? "" : "—") : "☁"}
        </Thumb>
        {conn ? (
          offline ? (
            <p className="mt-3 text-center text-sm text-slate-400">
              Offline · last seen {relTime(frame.last_seen)}.
              {d.pending > 0 && " Queued photos will deliver when it's back."}
            </p>
          ) : (
            <div className="mt-3 flex items-center justify-center gap-2">
              <Button onClick={() => prev.mutate()} disabled={prev.isPending}>
                ‹ Previous
              </Button>
              <Button onClick={() => next.mutate()} disabled={next.isPending}>
                Next ›
              </Button>
            </div>
          )
        ) : (
          <p className="mt-3 text-center text-sm text-slate-400">
            Cloud frame — it pulls its photos on its own schedule. Last seen {relTime(frame.last_seen)}.
          </p>
        )}
      </Card>

      <Card className="space-y-3 p-4 text-sm">
        <div className="font-semibold text-slate-200">At a glance</div>
        <Row label="Status">
          <span className="inline-flex items-center gap-1.5">
            <StatusDot tone={offline ? "idle" : "ok"} />
            {conn ? (offline ? "offline" : "online") : "cloud"}
          </span>
        </Row>
        <Row label="Delivery">
          <span className="flex flex-wrap justify-end gap-1">
            {d.delivered > 0 && <Pill tone="ok">{d.delivered}✓</Pill>}
            {d.pending > 0 && <Pill tone="pending">{d.pending}◐</Pill>}
            {d.failed > 0 && <Pill tone="fail">{d.failed}⚠</Pill>}
            {d.delivered + d.pending + d.failed === 0 && <span className="text-slate-500">none</span>}
          </span>
        </Row>
        {conn && cfg && (
          <>
            <Row label="Firmware">{fmt(cfg.SoftwareVersion)}</Row>
            <Row label="Screen">{cfg.ScreenSize ? `${cfg.ScreenSize}"` : "—"}</Row>
            <Row label="Orientation">{fmt(cfg.Orientation)}</Row>
            <Row label="Slide time">{cfg.DisplayTime ? `${cfg.DisplayTime}s` : "—"}</Row>
          </>
        )}
        <Row label="Showing">{conn ? fmt(showing) : "—"}</Row>
      </Card>
    </div>
  );
}

function FirmwareTab({ host }: { host: string }) {
  const qc = useQueryClient();
  const fw = useQuery({ queryKey: ["firmware"], queryFn: api.firmware });
  const cfg = useQuery({ queryKey: ["frame", host], queryFn: () => api.frame(host), retry: 0 });
  const check = useMutation({
    mutationFn: api.checkFirmware,
    onSuccess: (info) => qc.setQueryData(["firmware"], info),
  });
  const update = useMutation({ mutationFn: () => api.updateFrame(host) });
  const avail = fw.data?.tracks.find((t) => t.track === fw.data?.track);
  const current = cfg.data?.config.AppVersion ?? cfg.data?.config.SoftwareVersion; // app/bundle ver (#54)
  const err = (check.error ?? update.error) as Error | undefined;

  return (
    <Card className="space-y-3 p-4 text-sm">
      <div className="flex items-center gap-2">
        <span className="text-slate-400">Current</span>
        <span className="font-medium text-slate-200">{current != null ? String(current) : "—"}</span>
        {avail && <Pill tone="pending">v{avail.version} available</Pill>}
        <Button className="ml-auto px-2 py-0.5 text-xs" disabled={check.isPending} onClick={() => check.mutate()}>
          {check.isPending ? "Checking…" : "Check for updates"}
        </Button>
        {avail && (
          <Button
            variant="accent"
            className="px-2 py-0.5 text-xs"
            disabled={update.isPending}
            onClick={() => {
              if (confirm(`Update this frame to v${avail.version}?`)) update.mutate();
            }}
          >
            {update.isPending ? "Sending…" : "Update"}
          </Button>
        )}
      </div>
      {update.isSuccess && (
        <div className="text-xs text-emerald-300">Update sent — the frame will fetch and apply it.</div>
      )}
      {err && <div className="text-xs text-red-300">{err.message}</div>}
      {!fw.data?.repo && (
        <Banner tone="idle">No firmware source configured (set FIRMWARE_REPO). Fleet-wide OTA lands in #42.</Banner>
      )}
    </Card>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-slate-400">{label}</span>
      <span className="truncate text-right text-slate-200">{children}</span>
    </div>
  );
}

const fmt = (v: unknown) => (v != null && v !== "" ? String(v) : "—");
