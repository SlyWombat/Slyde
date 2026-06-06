import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { FrameStatus } from "../api/types";
import { isConnected, useFrames } from "../lib/frames";
import { Banner, Button, Card, Pill, Skeleton, useToast } from "../ui";

/**
 * Fleet firmware / OTA (#42): the registry's latest version per track, plus every OTA-capable frame
 * with its current-vs-available version and an Update action — and 'Update all on track'. Only
 * connected, OTA-capable frames are shown (served/cloud frames update themselves); each row is
 * capability-gated on `capabilities.ota`. Updates are fire-and-forget (the frame pulls + applies).
 */
export function FleetFirmware() {
  const qc = useQueryClient();
  const toast = useToast();
  const frames = useFrames();
  const fw = useQuery({ queryKey: ["firmware"], queryFn: api.firmware });
  const check = useMutation({
    mutationFn: api.checkFirmware,
    onSuccess: (info) => {
      qc.setQueryData(["firmware"], info);
      toast("Firmware registry refreshed.");
    },
    onError: (e) => toast((e as Error).message, "fail"),
  });

  const track = fw.data?.track ?? "";
  const available = fw.data?.tracks.find((t) => t.track === track)?.version ?? null;
  const connected = (frames.data ?? []).filter(isConnected);

  const updateAll = useMutation({
    mutationFn: () => Promise.allSettled(connected.map((f) => api.updateFrame(f.id))).then(() => {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["frames-status"] });
      toast(`Update sent to ${connected.length} frames — they'll pull and apply it.`);
    },
  });

  return (
    <Card className="space-y-3 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-semibold">Firmware / OTA</span>
        <div className="flex items-center gap-2">
          {available && <Pill tone="ok">v{available} available</Pill>}
          <Button
            className="px-2 py-1 text-xs"
            disabled={check.isPending || !fw.data?.repo}
            onClick={() => check.mutate()}
          >
            {check.isPending ? "Checking…" : "Check for updates"}
          </Button>
        </div>
      </div>

      {!fw.data?.repo ? (
        <Banner tone="idle">
          No firmware source configured. Set <code className="text-slate-200">FIRMWARE_REPO</code> to
          enable OTA.
        </Banner>
      ) : (
        <>
          <div className="text-xs text-slate-500">
            Source <code className="text-slate-400">{fw.data.repo}</code> · track {track}
          </div>

          {frames.isLoading && !frames.data ? (
            <Skeleton className="h-16 w-full" />
          ) : connected.length === 0 ? (
            <p className="text-sm text-slate-500">
              No OTA-capable frames. Cloud frames update themselves.
            </p>
          ) : (
            <>
              <ul className="divide-y divide-edge overflow-hidden rounded-lg border border-edge">
                {connected.map((f) => (
                  <FirmwareRow key={f.id} frame={f} available={available} />
                ))}
              </ul>
              {available && connected.length > 1 && (
                <Button
                  disabled={updateAll.isPending}
                  onClick={() => {
                    if (confirm(`Update all ${connected.length} frames to v${available}?`))
                      updateAll.mutate();
                  }}
                >
                  {updateAll.isPending ? "Sending…" : `Update all on ${track}`}
                </Button>
              )}
            </>
          )}
          {check.error && <Banner tone="fail">{(check.error as Error).message}</Banner>}
        </>
      )}
    </Card>
  );
}

function FirmwareRow({ frame, available }: { frame: FrameStatus; available: string | null }) {
  const qc = useQueryClient();
  const toast = useToast();
  const detail = useQuery({
    queryKey: ["frame-detail", frame.id],
    queryFn: () => api.frameDetail(frame.id),
  });
  const cfg = useQuery({
    queryKey: ["frame", frame.id],
    queryFn: () => api.frame(frame.id),
    retry: 0,
  });
  const update = useMutation({
    mutationFn: () => api.updateFrame(frame.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["frames-status"] });
      toast(`Update sent to ${frame.name || frame.id}.`);
    },
    onError: (e) => toast((e as Error).message, "fail"),
  });

  if (detail.data && !detail.data.capabilities.ota) return null; // capability-gated to OTA frames
  const current = cfg.data?.config.AppVersion ?? cfg.data?.config.SoftwareVersion;

  return (
    <li className="flex items-center gap-2 px-3 py-2 text-sm">
      <span className="min-w-0 flex-1 truncate">{frame.name || frame.id}</span>
      <span className="whitespace-nowrap text-xs text-slate-400">
        current {current != null ? String(current) : "—"}
      </span>
      {available && <Pill tone="pending">v{available}</Pill>}
      <Button
        className="px-2 py-0.5 text-xs"
        disabled={update.isPending || !available}
        onClick={() => update.mutate()}
      >
        {update.isPending ? "Sending…" : update.isSuccess ? "Sent ✓" : "Update"}
      </Button>
    </li>
  );
}
