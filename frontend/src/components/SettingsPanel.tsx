import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ConfigPatch, FrameConfig } from "../api/types";

const TOGGLES: { key: keyof ConfigPatch; label: string }[] = [
  { key: "DisplayOn", label: "Display on" },
  { key: "ShuffleOn", label: "Shuffle" },
  { key: "NightModeOn", label: "Night mode" },
  { key: "PortraitMode", label: "Portrait" },
];

export function SettingsPanel({ host }: { host: string }) {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["frame", host], queryFn: () => api.frame(host) });
  const update = useMutation({
    mutationFn: (patch: ConfigPatch) => api.updateConfig(host, patch),
    onSuccess: (info) => qc.setQueryData(["frame", host], info),
  });

  if (!data) return null;
  const cfg: FrameConfig = data.config;

  return (
    <div className="card space-y-3">
      <div className="font-semibold">Settings</div>
      <div className="space-y-2">
        {TOGGLES.map(({ key, label }) => (
          <label key={key} className="flex items-center justify-between text-sm">
            <span className="text-slate-300">{label}</span>
            <input
              type="checkbox"
              className="h-4 w-4 accent-accent"
              checked={Boolean(cfg[key])}
              disabled={update.isPending}
              onChange={(e) => update.mutate({ [key]: e.target.checked })}
            />
          </label>
        ))}
        <label className="flex items-center justify-between text-sm">
          <span className="text-slate-300">Slide time (s)</span>
          <input
            // Keyed to the live value so an external/clamped update re-seeds the field
            // (an uncontrolled input only reads defaultValue once, on mount).
            key={cfg.DisplayTime ?? 60}
            type="number"
            min={1}
            className="w-20 rounded bg-ink px-2 py-1 text-right"
            defaultValue={cfg.DisplayTime ?? 60}
            disabled={update.isPending}
            onBlur={(e) => {
              const value = Number(e.target.value);
              if (value >= 1 && value !== cfg.DisplayTime) update.mutate({ DisplayTime: value });
            }}
          />
        </label>
      </div>
      {update.isError && (
        <div className="text-xs text-red-300">{(update.error as Error).message}</div>
      )}
    </div>
  );
}
