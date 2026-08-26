import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { InterludeStatus } from "../api/types";
import { Banner, Button, Card, Pill, usePoll, useToast } from "../ui";

/**
 * Interlude (#70): show one recurring non-photo image between this frame's photos.
 *
 * Slyde doesn't draw that image — another program does, by writing a file (or PUTting bytes). So
 * this panel's job is mostly to tell the user *where to write* and *what the conductor is doing*,
 * not to offer knobs. The important state to make legible is `standby`: enabled, but no usable
 * image, which is the deliberate, healthy resting state where the frame runs its own slideshow.
 */
export function InterludePanel({ frameId }: { frameId: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const interval = usePoll(10000);
  const fileInput = useRef<HTMLInputElement>(null);
  const [copied, setCopied] = useState(false);

  const q = useQuery({
    queryKey: ["interlude", frameId],
    queryFn: () => api.interlude(frameId),
    refetchInterval: interval,
    retry: 0,
  });
  const seed = (next: InterludeStatus) => qc.setQueryData(["interlude", frameId], next);
  const fail = (e: unknown) => toast((e as Error).message, "fail");

  const save = useMutation({
    mutationFn: (patch: Partial<InterludeStatus["config"]>) =>
      api.setInterlude(frameId, { ...q.data!.config, ...patch }),
    onSuccess: seed,
    onError: fail,
  });
  const publish = useMutation({
    mutationFn: (file: File) => api.putInterludeImage(frameId, file),
    onSuccess: (next) => {
      seed(next);
      toast("Interlude image published.");
    },
    onError: fail,
  });
  const withdraw = useMutation({
    mutationFn: () => api.deleteInterludeImage(frameId),
    onSuccess: (next) => {
      seed(next);
      toast("Image removed — the frame is back to its normal slideshow.");
    },
    onError: fail,
  });

  const status = q.data;
  if (!status) return null;

  if (!status.supported) {
    return (
      <Card className="space-y-2 p-4">
        <div className="font-semibold text-slate-200">Interlude</div>
        <Banner tone="idle">
          This frame can't show an interlude. Its panel is e-paper — every refresh is ~15–30s of
          visible flashing and part of a finite lifetime budget — and it fetches photos on its own
          schedule rather than being told what to show.
        </Banner>
      </Card>
    );
  }

  const { config, state } = status;
  const tone = state === "engaged" ? "ok" : state === "standby" ? "pending" : "idle";
  const label =
    state === "engaged"
      ? "showing between photos"
      : state === "standby"
        ? "waiting for an image"
        : "off";

  return (
    <Card className="space-y-3 p-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold text-slate-200">Interlude</span>
        <Pill tone={tone}>{label}</Pill>
        <label className="ml-auto flex items-center gap-2">
          <span className="text-slate-300">Enabled</span>
          <input
            type="checkbox"
            className="h-4 w-4 accent-accent"
            checked={config.enabled}
            disabled={save.isPending}
            onChange={(e) => save.mutate({ enabled: e.target.checked })}
          />
        </label>
      </div>

      <p className="text-xs text-slate-400">
        Shows one image of your own between this frame's photos. Slyde doesn't draw it — another
        program does, by writing the file below. Change the file and the frame shows the new
        picture; <strong>delete it and the frame goes back to its ordinary slideshow</strong>.
      </p>

      {config.enabled && state === "standby" && status.detail && (
        <Banner tone="idle">
          No image right now, so this frame is running its own slideshow. {status.detail}
        </Banner>
      )}

      <div className="space-y-1">
        <div className="text-xs text-slate-400">Write your image here</div>
        <div className="flex items-center gap-2">
          <code className="flex-1 truncate rounded bg-ink px-2 py-1 text-xs text-slate-300">
            {status.image_path}
          </code>
          <Button
            className="px-2 py-0.5 text-xs"
            onClick={() => {
              navigator.clipboard?.writeText(status.image_path);
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            }}
          >
            {copied ? "Copied" : "Copy"}
          </Button>
        </div>
        <div className="text-xs text-slate-500">
          {status.image_present ? "An image is there now." : "No image there yet."} Write to a temp
          file and rename it over this path, so Slyde never reads a half-written picture.
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <input
          ref={fileInput}
          type="file"
          accept="image/*"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) publish.mutate(file);
            e.target.value = "";
          }}
        />
        <Button className="px-2 py-0.5 text-xs" onClick={() => fileInput.current?.click()} disabled={publish.isPending}>
          {publish.isPending ? "Uploading…" : "Upload an image"}
        </Button>
        {status.image_present && (
          <Button
            className="px-2 py-0.5 text-xs"
            disabled={withdraw.isPending}
            onClick={() => withdraw.mutate()}
          >
            Remove image
          </Button>
        )}
      </div>

      {status.image_present && (
        <img
          src={api.interludePreviewUrl(frameId, status.detail + String(status.image_present))}
          alt="How the interlude will look on this frame"
          className="w-full rounded-lg border border-edge"
        />
      )}

      <div className="grid gap-2 sm:grid-cols-3">
        <Field label="Every N photos">
          <input
            key={config.every_n_photos}
            type="number"
            min={1}
            className="w-full rounded bg-ink px-2 py-1 text-right"
            defaultValue={config.every_n_photos}
            onBlur={(e) => {
              const value = Number(e.target.value);
              if (value >= 1 && value !== config.every_n_photos)
                save.mutate({ every_n_photos: value });
            }}
          />
        </Field>
        <Field label="Hold for (s)">
          <input
            key={config.dwell_seconds}
            type="number"
            min={0}
            className="w-full rounded bg-ink px-2 py-1 text-right"
            defaultValue={config.dwell_seconds}
            onBlur={(e) => {
              const value = Number(e.target.value);
              if (value >= 0 && value !== config.dwell_seconds)
                save.mutate({ dwell_seconds: value });
            }}
          />
        </Field>
        <Field label="Fit">
          <select
            className="w-full rounded bg-ink px-2 py-1"
            value={config.fit}
            onChange={(e) => save.mutate({ fit: e.target.value as typeof config.fit })}
          >
            <option value="contain">Contain (never crops)</option>
            <option value="cover">Cover (fills, crops)</option>
            <option value="blur">Blur fill</option>
            <option value="smart">Smart</option>
          </select>
        </Field>
      </div>
      <div className="text-xs text-slate-500">Hold for 0 = the same as a normal photo.</div>
    </Card>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="space-y-1 text-xs">
      <span className="block text-slate-400">{label}</span>
      {children}
    </label>
  );
}
