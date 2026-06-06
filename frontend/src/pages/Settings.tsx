import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { FleetFirmware } from "../components/FleetFirmware";
import { useFrames } from "../lib/frames";
import { Banner, Card, Skeleton, StatusDot, usePoll } from "../ui";

/**
 * App settings & status (#44). Config is env/12-factor, so this is read-mostly: it reflects the
 * Immich connection, scheduled-sync health, the firmware registry, and app info. The one action is
 * "Check for updates", which refreshes the firmware registry from the configured repo.
 */
export function Settings() {
  const refetch = usePoll(10000);
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: refetch });
  const sync = useQuery({ queryKey: ["sync-health"], queryFn: api.syncHealth, refetchInterval: refetch });
  const frames = useFrames();

  return (
    <div className="mx-auto max-w-3xl px-4 py-6 sm:px-6">
      <header className="mb-5">
        <h1 className="text-2xl font-bold tracking-tight">Settings</h1>
        <p className="mt-1 text-sm text-slate-400">
          Slyde is configured by environment variables (12-factor). This page reflects current
          status; change values in your deployment's env and restart.
        </p>
      </header>

      <div className="space-y-4">
        <ImmichSection configured={health.data?.immich_configured} loading={health.isLoading} />
        <SyncSection text={sync.data} loading={sync.isLoading} />
        <FleetFirmware />
        <AppSection version={health.data?.version} frameCount={(frames.data ?? []).length} />
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card className="space-y-3 p-4">
      <div className="font-semibold">{title}</div>
      {children}
    </Card>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 text-sm">
      <span className="text-slate-400">{label}</span>
      <span className="text-right text-slate-200">{children}</span>
    </div>
  );
}

function ImmichSection({ configured, loading }: { configured?: boolean; loading: boolean }) {
  return (
    <Section title="Immich">
      {loading && configured === undefined ? (
        <Skeleton className="h-6 w-40" />
      ) : configured ? (
        <Row label="Connection">
          <span className="inline-flex items-center gap-1.5">
            <StatusDot tone="ok" /> Connected
          </span>
        </Row>
      ) : (
        <Banner tone="fail">
          Immich isn't configured. Set <code className="text-slate-200">IMMICH_BASE_URL</code> and{" "}
          <code className="text-slate-200">IMMICH_API_KEY</code> in your env, then restart — Slyde
          reads from Immich one-way (it never writes back).
        </Banner>
      )}
    </Section>
  );
}

function SyncSection({ text, loading }: { text?: string; loading: boolean }) {
  const failed = text?.startsWith("FAIL");
  return (
    <Section title="Scheduled sync">
      {loading && !text ? (
        <Skeleton className="h-6 w-56" />
      ) : (
        <div className="flex items-start gap-2 text-sm">
          <StatusDot tone={failed ? "fail" : "ok"} />
          <span className="text-slate-300">{text || "no status"}</span>
        </div>
      )}
      <p className="text-xs text-slate-500">
        Kept-in-sync albums re-mirror periodically and delivery drains continuously — both run in the
        backend. This is the KPI exposed at <code>/api/health/sync</code>.
      </p>
    </Section>
  );
}

function AppSection({ version, frameCount }: { version?: string; frameCount: number }) {
  return (
    <Section title="About Slyde">
      <Row label="Version">{version ? `v${version}` : "—"}</Row>
      <Row label="Frames registered">{frameCount}</Row>
      <Row label="Project">
        <a
          className="text-accent hover:underline"
          href="https://github.com/SlyWombat/slyde"
          target="_blank"
          rel="noreferrer"
        >
          SlyWombat/slyde ↗
        </a>
      </Row>
    </Section>
  );
}
