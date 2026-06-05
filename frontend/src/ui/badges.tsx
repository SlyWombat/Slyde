import type { ReactNode } from "react";
import { TONE, type Tone } from "./status";

export function StatusDot({ tone, pulse = false }: { tone: Tone; pulse?: boolean }) {
  return (
    <span className="relative inline-flex h-2.5 w-2.5 shrink-0">
      {pulse && (
        <span className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-60 ${TONE[tone].dot}`} />
      )}
      <span className={`relative inline-flex h-2.5 w-2.5 rounded-full ${TONE[tone].dot}`} />
    </span>
  );
}

export function Pill({
  tone,
  children,
  className = "",
}: {
  tone?: Tone;
  children: ReactNode;
  className?: string;
}) {
  const t = tone ? TONE[tone] : null;
  const base = t ? `${t.bg} ${t.text}` : "border border-edge bg-ink text-slate-400";
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${base} ${className}`}>
      {children}
    </span>
  );
}

/** Health pill: coloured dot + label (status never colour-only, for a11y). */
export function HealthBadge({ tone, label, pulse }: { tone: Tone; label: string; pulse?: boolean }) {
  return (
    <Pill tone={tone}>
      <StatusDot tone={tone} pulse={pulse} />
      {label}
    </Pill>
  );
}

/** "LAN" (connected) vs "Cloud" (served) — never just grey text. */
export function FrameKindBadge({ interaction }: { interaction: string }) {
  const lan = interaction === "connected";
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-edge bg-ink px-2 py-0.5 text-[11px] font-medium text-slate-300">
      <span aria-hidden>{lan ? "📡" : "☁"}</span>
      {lan ? "LAN" : "Cloud"}
    </span>
  );
}
