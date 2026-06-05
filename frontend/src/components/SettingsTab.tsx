import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { CapabilitiesInfo, FrameStatus } from "../api/types";
import { isConnected } from "../lib/frames";
import { SettingsPanel } from "./SettingsPanel";
import { SyncedAlbums } from "./SyncedAlbums";
import { Banner, Button, Card, Skeleton } from "../ui";

/**
 * Per-frame Settings (#41), capability-gated. Connected frames get rename (writes the device's own
 * Name), the live display toggles + slide time, processing summary, and kept-in-sync albums. Served
 * (cloud) frames manage their own schedule, so they show only rename (registry) + how their images
 * are prepared — no Prev/Next or live toggles.
 */
export function SettingsTab({ frame }: { frame: FrameStatus }) {
  const conn = isConnected(frame);
  const detail = useQuery({
    queryKey: ["frame-detail", frame.id],
    queryFn: () => api.frameDetail(frame.id),
  });

  return (
    <div className="space-y-4">
      <RenameCard frame={frame} />
      {conn && <SettingsPanel host={frame.id} />}
      <ProcessingCard caps={detail.data?.capabilities} loading={detail.isLoading} />
      {conn ? (
        <SyncedAlbums host={frame.id} />
      ) : (
        <Banner tone="idle">
          This is a cloud frame — it manages its own display schedule on the device. Curate its
          photos from the Library tab; they deliver when it next checks in.
        </Banner>
      )}
    </div>
  );
}

/** Rename a frame. Connected → PATCH the device config (Name shows on the frame); served → re-register
 *  (idempotent) to update the registry name. */
function RenameCard({ frame }: { frame: FrameStatus }) {
  const qc = useQueryClient();
  const conn = isConnected(frame);
  const [name, setName] = useState(frame.name ?? "");

  const rename = useMutation({
    mutationFn: async (n: string): Promise<void> => {
      if (conn) await api.updateConfig(frame.id, { Name: n });
      else await api.registerFrame({ frame_code: frame.id, name: n });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["frames-status"] });
      qc.invalidateQueries({ queryKey: ["frame", frame.id] });
    },
  });

  const trimmed = name.trim();
  const dirty = trimmed.length > 0 && trimmed !== (frame.name ?? "");

  return (
    <Card className="space-y-2 p-4">
      <div className="font-semibold">Name</div>
      <div className="flex gap-2">
        <input
          className="flex-1 rounded bg-ink px-3 py-2 text-sm"
          value={name}
          maxLength={64}
          placeholder={frame.id}
          onChange={(e) => setName(e.target.value)}
        />
        <Button
          variant="accent"
          disabled={!dirty || rename.isPending}
          onClick={() => rename.mutate(trimmed)}
        >
          {rename.isPending ? "Saving…" : "Save"}
        </Button>
      </div>
      <p className="text-xs text-slate-500">
        {conn ? "Shown on the frame itself." : "Shown in Slyde for this cloud frame."}
      </p>
      {rename.error && <Banner tone="fail">{(rename.error as Error).message}</Banner>}
      {rename.isSuccess && !dirty && <p className="text-xs text-emerald-300">Saved.</p>}
    </Card>
  );
}

/** Read-only summary of how images are prepared for this frame (from backend capabilities, #28). */
function ProcessingCard({ caps, loading }: { caps?: CapabilitiesInfo; loading: boolean }) {
  if (loading && !caps) return <Skeleton className="h-28 w-full" />;
  if (!caps) return null;
  const epaper = caps.color_model === "epaper";
  return (
    <Card className="space-y-2 p-4 text-sm">
      <div className="font-semibold">Image processing</div>
      <Row label="Panel">{epaper ? "E-ink (Spectra-6)" : "Full-colour LCD"}</Row>
      <Row label="Prepared as">
        {epaper ? "6-colour palette + dithering" : "Fitted, full-colour JPEG"}
      </Row>
      <Row label="Delivery">
        {caps.transport === "cloud" ? "Cloud — the frame pulls" : "LAN — pushed to the frame"}
      </Row>
      <p className="text-xs text-slate-500">Per-frame fit tuning and a render preview land in #39.</p>
    </Card>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-slate-400">{label}</span>
      <span className="text-right text-slate-200">{children}</span>
    </div>
  );
}
