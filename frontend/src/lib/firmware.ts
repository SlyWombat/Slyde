import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

/** Latest available firmware version for the configured track (null if none / unconfigured) (#42). */
export function useAvailableFirmware() {
  const fw = useQuery({ queryKey: ["firmware"], queryFn: api.firmware });
  const track = fw.data?.track;
  const version = fw.data?.tracks.find((t) => t.track === track)?.version ?? null;
  return { repo: fw.data?.repo ?? "", track: track ?? "", version, isLoading: fw.isLoading };
}
