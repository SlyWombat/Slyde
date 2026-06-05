import { useEffect, useState } from "react";

/**
 * Returns a TanStack-Query `refetchInterval` value (#31): the given ms while the tab is visible,
 * or `false` (paused) when hidden — so polling doesn't burn CPU/network on a backgrounded tab.
 */
export function usePoll(ms: number): number | false {
  const [hidden, setHidden] = useState(() => typeof document !== "undefined" && document.hidden);
  useEffect(() => {
    const onChange = () => setHidden(document.hidden);
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, []);
  return hidden ? false : ms;
}
