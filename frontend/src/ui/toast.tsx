import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { StatusDot } from "./badges";
import { TONE, type Tone } from "./status";

/**
 * App-wide toasts (#43). The container is an `aria-live="polite"` region, so every toast is also
 * announced to screen readers — this is the app's announcement channel for delivery/sync/OTA
 * changes, not just a visual nicety. Toasts auto-dismiss and are reduced-motion-safe (see index.css).
 */
interface Toast {
  id: number;
  tone: Tone;
  message: string;
}

type Notify = (message: string, tone?: Tone) => void;

const ToastContext = createContext<Notify>(() => {});

let _nextId = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((t) => t.id !== id));
  }, []);

  const notify = useCallback<Notify>(
    (message, tone = "ok") => {
      const id = (_nextId += 1);
      setToasts((current) => [...current, { id, tone, message }]);
      setTimeout(() => dismiss(id), 4500);
    },
    [dismiss],
  );

  return (
    <ToastContext.Provider value={notify}>
      {children}
      <div
        aria-live="polite"
        role="status"
        className="pointer-events-none fixed inset-x-0 bottom-4 z-50 flex flex-col items-center gap-2 px-4"
      >
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`pointer-events-auto flex max-w-md items-center gap-2 rounded-lg border border-edge bg-panel px-3 py-2 text-sm text-slate-100 shadow-lg shadow-black/30 motion-safe:animate-[fadein_.2s_ease-out] ${TONE[t.tone].ring} ring-1`}
          >
            <StatusDot tone={t.tone} />
            <span className="min-w-0 flex-1">{t.message}</span>
            <button
              onClick={() => dismiss(t.id)}
              aria-label="Dismiss notification"
              className="text-slate-500 transition-colors hover:text-slate-200"
            >
              ✕
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

/** Push a toast (and screen-reader announcement): `const toast = useToast(); toast("Saved")`. */
// eslint-disable-next-line react-refresh/only-export-components -- provider + its hook co-located
export function useToast(): Notify {
  return useContext(ToastContext);
}
