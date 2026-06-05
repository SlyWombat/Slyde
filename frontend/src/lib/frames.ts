// Frame identity layer (#33). `frame.id` (from /api/frames/status) is canonical in the UI. A
// connected frame's id IS its host, so id-keyed code can call the existing host endpoints; served
// (cloud) frames carry only an id and never call host endpoints — capability-gated by `interaction`.
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { FrameStatus } from "../api/types";
import { usePoll, type Tone } from "../ui";

/** Every known frame across backends (read-only registry + delivery state), polled while visible. */
export function useFrames() {
  const refetchInterval = usePoll(5000);
  return useQuery({ queryKey: ["frames-status"], queryFn: api.framesStatus, refetchInterval });
}

/** A single frame by id, resolved from the fleet status (no extra request). */
export function useFrame(id: string) {
  const q = useFrames();
  return { ...q, frame: q.data?.find((f) => f.id === id) };
}

export const isConnected = (f: FrameStatus) => f.interaction === "connected";
export const isServed = (f: FrameStatus) => f.interaction === "served";

/** Kind-aware health for the fleet (status colour + label). Offline/asleep is NOT a failure. */
export function frameHealth(f: FrameStatus): { tone: Tone; label: string } {
  const d = f.deliveries;
  if (d.failed > 0) return { tone: "fail", label: "needs attention" };
  if (d.pending > 0) return { tone: "pending", label: isServed(f) ? "queued" : "delivering" };
  return { tone: "ok", label: "healthy" };
}
