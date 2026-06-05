/** Human "Xs/m/h/d ago" from an ISO (or SQLite "YYYY-MM-DD HH:MM:SS" UTC) timestamp (#31). */
export function relTime(iso: string | null): string {
  if (!iso) return "never";
  const ms = new Date(iso.includes("T") ? iso : iso.replace(" ", "T") + "Z").getTime();
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}
