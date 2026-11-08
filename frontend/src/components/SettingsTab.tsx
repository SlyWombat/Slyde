import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type {
  CapabilitiesInfo,
  FrameStatus,
  SlideshowSettings,
  SlideshowSettingsUpdate,
} from "../api/types";
import { isConnected } from "../lib/frames";
import { InterludePanel } from "./InterludePanel";
import { SettingsPanel } from "./SettingsPanel";
import { Banner, Button, Card, Skeleton, useToast } from "../ui";

/**
 * Per-frame Settings (#41), capability-gated. Connected frames get rename (writes the device's own
 * Name), the live display toggles + slide time, and processing summary. Served (cloud) frames
 * manage their own schedule, so they show only rename (registry) + how their images are prepared.
 * Folder keep-in-sync is managed on the Albums tab (#56), not here.
 */
export function SettingsTab({ frame }: { frame: FrameStatus }) {
  const conn = isConnected(frame);
  const switchbot = frame.backend === "switchbot";
  const lan = conn && !switchbot;
  const detail = useQuery({
    queryKey: ["frame-detail", frame.id],
    queryFn: () => api.frameDetail(frame.id),
  });

  return (
    <div className="space-y-4">
      <RenameCard frame={frame} />
      {/* A SwitchBot frame is pushed to through a vendor cloud: no device settings to read. */}
      {lan && <SettingsPanel host={frame.id} />}
      {lan && <InterludePanel frameId={frame.id} />}
      {switchbot && <SlideshowCard frameId={frame.id} />}
      <ProcessingCard
        caps={detail.data?.capabilities}
        loading={detail.isLoading}
        switchbot={switchbot}
      />
      {!conn && (
        <Banner tone="idle">
          This is a cloud frame — it manages its own display schedule on the device. Curate its
          photos from the Library tab; they deliver when it next checks in.
        </Banner>
      )}
      <DetachCard frame={frame} />
    </div>
  );
}

/** Detach (deregister) a frame: removes it from Slyde + clears its sync history; device untouched (#52). */
function DetachCard({ frame }: { frame: FrameStatus }) {
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const detach = useMutation({
    mutationFn: () => api.deregisterFrame(frame.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["frames-status"] });
      toast(`Removed ${frame.name || frame.id} from Slyde.`);
      navigate("/frames");
    },
    onError: (e) => toast((e as Error).message, "fail"),
  });
  return (
    <Card className="space-y-2 border-red-400/30 p-4">
      <div className="font-semibold text-red-300">Detach frame</div>
      <p className="text-xs text-slate-400">
        Removes this frame from Slyde and clears its sync history (delivery queue, curated set, cached
        images). The device itself is <strong>not</strong> wiped. A frame still on your network (or
        pinned via <code className="text-slate-300">FRAME_HOSTS</code>) may reappear and can be
        re-added.
      </p>
      <Button
        className="bg-red-500/15 text-red-200 hover:bg-red-500/25"
        disabled={detach.isPending}
        onClick={() => {
          if (confirm(`Detach "${frame.name || frame.id}" from Slyde? The device is not wiped.`))
            detach.mutate();
        }}
      >
        {detach.isPending ? "Detaching…" : "Detach frame"}
      </Button>
    </Card>
  );
}

/** Rename a frame. Connected → PATCH the device config (Name shows on the frame); served → re-register
 *  (idempotent) to update the registry name. */
function RenameCard({ frame }: { frame: FrameStatus }) {
  const qc = useQueryClient();
  const toast = useToast();
  const conn = isConnected(frame);
  const [name, setName] = useState(frame.name ?? "");

  const rename = useMutation({
    mutationFn: async (n: string): Promise<void> => {
      if (conn) await api.updateConfig(frame.id, { Name: n }); // push the name to the device too
      await api.renameFrame(frame.id, n); // update the registry name shown across Slyde (#55)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["frames-status"] });
      qc.invalidateQueries({ queryKey: ["frame", frame.id] });
      toast("Frame name saved.");
    },
    onError: (e) => toast((e as Error).message, "fail"),
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
function ProcessingCard({
  caps,
  loading,
  switchbot,
}: {
  caps?: CapabilitiesInfo;
  loading: boolean;
  switchbot: boolean;
}) {
  if (loading && !caps) return <Skeleton className="h-28 w-full" />;
  if (!caps) return null;
  const epaper = caps.color_model === "epaper";
  return (
    <Card className="space-y-2 p-4 text-sm">
      <div className="font-semibold">Image processing</div>
      <Row label="Panel">{epaper ? "E-ink (Spectra-6)" : "Full-colour LCD"}</Row>
      <Row label="Prepared as">
        {switchbot
          ? "Fitted JPEG (SwitchBot renders it for the panel)"
          : epaper
            ? "6-colour palette + dithering"
            : "Fitted, full-colour JPEG"}
      </Row>
      <Row label="Delivery">
        {switchbot
          ? "Pushed through SwitchBot Cloud / Home Assistant"
          : caps.transport === "cloud"
            ? "Cloud — the frame pulls"
            : "LAN — pushed to the frame"}
      </Row>
      <p className="text-xs text-slate-500">Per-frame fit tuning and a render preview land in #39.</p>
    </Card>
  );
}

/** How a SwitchBot frame's slideshow runs (SWITCHBOT_SLIDESHOW), with what that means on the
 *  device — the frame's own slideshow setting has to match. Interval, quiet hours and the
 *  "only while someone is home" entity are set here per frame; the add-on options are defaults. */
function SlideshowCard({ frameId }: { frameId: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const status = useQuery({
    queryKey: ["switchbot-status", frameId],
    queryFn: () => api.switchbotStatus(frameId),
    refetchInterval: 60_000,
  });
  const show = useQuery({
    queryKey: ["slideshow", frameId],
    queryFn: () => api.slideshowSettings(frameId),
    refetchInterval: 60_000,
  });
  const next = useMutation({
    mutationFn: () => api.slideshowNext(frameId),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["frame-library", frameId] });
      toast(
        r.queued
          ? `Sending ${r.queued === 1 ? "the next photo" : `${r.queued} photos`} — it shows in about a minute.`
          : "Nothing new to send.",
      );
    },
    onError: (e) => toast((e as Error).message, "fail"),
  });
  if (!show.data) {
    return show.error ? <Banner tone="fail">{(show.error as Error).message}</Banner> : null;
  }
  const s = show.data;
  const mode = s.mode;
  const battery = status.data?.battery;
  return (
    <Card className="space-y-3 p-4 text-sm">
      <div className="font-semibold">Slideshow</div>
      <Row label="Battery">
        {status.isLoading ? "…" : battery == null ? "unknown" : `${battery} %`}
      </Row>
      <Row label="Run by">
        {mode === "slyde" ? "Slyde" : mode === "frame" ? "The frame" : "Off (every photo)"}
      </Row>
      {mode !== "off" && (
        <SlideshowForm
          key={JSON.stringify([s.interval_minutes, s.quiet_hours, s.presence_entity])}
          frameId={frameId}
          settings={s}
        />
      )}
      {mode === "slyde" && (
        <Banner tone="idle">
          Slyde sends one new photo per interval, picked from the frame&apos;s whole library —
          added, uploaded or kept in sync — and shows each photo once before any repeats. Set
          the frame to a <b>static image</b> in the SwitchBot app — otherwise its own slideshow
          also cycles through the photos it still stores.
        </Banner>
      )}
      {mode === "frame" && (
        <Banner tone="idle">
          Every interval Slyde replaces the frame&apos;s 10 photos with 10 more from its library,
          storing them without showing each one; the slideshow you set in the SwitchBot app cycles
          them. Needs ha-switchbot-art-frame with the <code>show</code> option of{" "}
          <code>show_image</code>.
        </Banner>
      )}
      {mode !== "off" && (
        <Button disabled={next.isPending} onClick={() => next.mutate()}>
          {next.isPending ? "Picking…" : mode === "slyde" ? "Next photo now" : "Next batch now"}
        </Button>
      )}
      <p className="text-xs text-slate-500">
        Who runs the slideshow is the add-on option <code>switchbot_slideshow</code>; its other
        slideshow options are the defaults for the settings above.
      </p>
    </Card>
  );
}

const INTERVALS = [15, 30, 60, 120, 180, 360, 720, 1440];

function intervalLabel(minutes: number): string {
  if (minutes % 1440 === 0) return minutes === 1440 ? "1 day" : `${minutes / 1440} days`;
  if (minutes % 60 === 0) return minutes === 60 ? "1 hour" : `${minutes / 60} hours`;
  return `${minutes} min`;
}

/** The editable part of the Slideshow card: interval, quiet hours, presence entity. */
function SlideshowForm({ frameId, settings }: { frameId: string; settings: SlideshowSettings }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [interval, setIntervalMinutes] = useState(settings.interval_minutes);
  const [quietFrom, quietTo] = settings.quiet_hours ? settings.quiet_hours.split("-") : ["", ""];
  const [quiet, setQuiet] = useState(Boolean(settings.quiet_hours));
  const [from, setFrom] = useState(quietFrom || "22:00");
  const [to, setTo] = useState(quietTo || "07:00");
  const [entity, setEntity] = useState(settings.presence_entity);
  const entities = useQuery({
    queryKey: ["presence-entities", frameId],
    queryFn: () => api.presenceEntities(frameId),
    enabled: settings.ha_available,
    staleTime: 5 * 60_000,
  });
  const save = useMutation({
    mutationFn: (body: SlideshowSettingsUpdate) => api.setSlideshowSettings(frameId, body),
    onSuccess: (data) => {
      qc.setQueryData(["slideshow", frameId], data);
      qc.invalidateQueries({ queryKey: ["frame-detail", frameId] });
      toast("Slideshow settings saved.");
    },
    onError: (e) => toast((e as Error).message, "fail"),
  });

  const body: SlideshowSettingsUpdate = {
    interval_minutes: interval,
    quiet_hours: quiet && from && to ? `${from}-${to}` : "",
    presence_entity: entity,
  };
  const dirty =
    body.interval_minutes !== settings.interval_minutes ||
    body.quiet_hours !== settings.quiet_hours ||
    body.presence_entity !== settings.presence_entity;
  const atDefaults =
    settings.interval_minutes === settings.defaults.interval_minutes &&
    settings.quiet_hours === settings.defaults.quiet_hours &&
    settings.presence_entity === settings.defaults.presence_entity;
  const options = INTERVALS.includes(interval)
    ? INTERVALS
    : [...INTERVALS, interval].sort((a, b) => a - b);
  const known = entities.data?.some((e) => e.entity_id === entity) ?? false;

  return (
    <div className="space-y-3 border-t border-edge pt-3">
      <label className="flex items-center justify-between gap-3">
        <span className="text-slate-400">
          {settings.mode === "slyde" ? "New photo every" : "New batch of 10 every"}
        </span>
        <select
          className="rounded bg-ink px-2 py-1"
          value={interval}
          onChange={(e) => setIntervalMinutes(Number(e.target.value))}
        >
          {options.map((m) => (
            <option key={m} value={m}>
              {intervalLabel(m)}
            </option>
          ))}
        </select>
      </label>

      <div className="space-y-2">
        <label className="flex items-center justify-between gap-3">
          <span className="text-slate-400">Pause at night</span>
          <input
            type="checkbox"
            className="h-4 w-4 accent-accent"
            checked={quiet}
            onChange={(e) => setQuiet(e.target.checked)}
          />
        </label>
        {quiet && (
          <div className="flex items-center justify-end gap-2">
            <span className="text-slate-400">from</span>
            <input
              type="time"
              className="rounded bg-ink px-2 py-1"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
            />
            <span className="text-slate-400">to</span>
            <input
              type="time"
              className="rounded bg-ink px-2 py-1"
              value={to}
              onChange={(e) => setTo(e.target.value)}
            />
          </div>
        )}
      </div>

      <div className="space-y-1">
        <label className="flex items-center justify-between gap-3">
          <span className="text-slate-400">Only while someone is home</span>
          {entities.data ? (
            <select
              className="max-w-[60%] rounded bg-ink px-2 py-1"
              value={entity}
              onChange={(e) => setEntity(e.target.value)}
            >
              <option value="">Always (no check)</option>
              {!known && entity && <option value={entity}>{entity}</option>}
              {entities.data.map((e) => (
                <option key={e.entity_id} value={e.entity_id}>
                  {e.name} ({e.entity_id}: {e.state})
                </option>
              ))}
            </select>
          ) : (
            <input
              className="w-1/2 rounded bg-ink px-2 py-1"
              value={entity}
              placeholder="zone.home"
              disabled={!settings.ha_available}
              onChange={(e) => setEntity(e.target.value.trim())}
            />
          )}
        </label>
        <p className="text-xs text-slate-500">
          {!settings.ha_available
            ? "Needs Slyde to reach Home Assistant (runs as add-on or HA_BASE_URL/HA_TOKEN set)."
            : !settings.presence_entity
              ? "Changes photos whenever the interval is up. Pick zone.home, a person, a group " +
                "or a binary_sensor to pause while nobody is home."
              : settings.someone_home === false
                ? "Nobody is home right now — the photo stays until someone is back."
                : settings.someone_home
                  ? "Someone is home — the slideshow runs."
                  : "Home Assistant doesn't say home/away for this entity — the slideshow runs."}
        </p>
      </div>

      <div className="flex gap-2">
        <Button
          variant="accent"
          disabled={!dirty || save.isPending}
          onClick={() => save.mutate(body)}
        >
          {save.isPending ? "Saving…" : "Save"}
        </Button>
        {!atDefaults && (
          <Button disabled={save.isPending} onClick={() => save.mutate(settings.defaults)}>
            Reset to defaults
          </Button>
        )}
      </div>
    </div>
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
