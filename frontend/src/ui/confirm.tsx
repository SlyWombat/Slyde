import { useEffect, useState, type ReactNode } from "react";
import { Button } from "./primitives";

/**
 * A destructive action button with inline two-step confirmation (#56) — replaces window.confirm()
 * without a modal. First click arms it (label → confirm copy); a second click within a few seconds
 * fires; otherwise it disarms. Reduced-motion-safe (no animation).
 */
export function ConfirmButton({
  onConfirm,
  children,
  confirmLabel = "Confirm",
  className = "",
  disabled,
  title,
}: {
  onConfirm: () => void;
  children: ReactNode;
  confirmLabel?: string;
  className?: string;
  disabled?: boolean;
  title?: string;
}) {
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(false), 3500);
    return () => clearTimeout(t);
  }, [armed]);

  if (armed) {
    return (
      <Button
        className={`bg-red-500/20 text-red-200 hover:bg-red-500/30 ${className}`}
        disabled={disabled}
        onClick={() => {
          setArmed(false);
          onConfirm();
        }}
      >
        {confirmLabel}?
      </Button>
    );
  }
  return (
    <Button className={className} disabled={disabled} title={title} onClick={() => setArmed(true)}>
      {children}
    </Button>
  );
}
