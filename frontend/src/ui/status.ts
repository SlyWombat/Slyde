// Status colour semantics — one source of truth (#31). Used by every status surface so a glance
// reads the same everywhere: delivered/healthy, pending/attention-soon, failed, idle, active.
export type Tone = "ok" | "pending" | "fail" | "idle" | "active";

export const TONE: Record<Tone, { dot: string; text: string; bg: string; ring: string }> = {
  ok: { dot: "bg-emerald-400", text: "text-emerald-300", bg: "bg-emerald-500/15", ring: "ring-emerald-400/40" },
  pending: { dot: "bg-amber-400", text: "text-amber-300", bg: "bg-amber-500/15", ring: "ring-amber-400/40" },
  fail: { dot: "bg-red-400", text: "text-red-300", bg: "bg-red-500/15", ring: "ring-red-400/40" },
  idle: { dot: "bg-slate-500", text: "text-slate-400", bg: "bg-slate-500/10", ring: "ring-slate-500/30" },
  active: { dot: "bg-accent", text: "text-accent", bg: "bg-accent/15", ring: "ring-accent/40" },
};
