import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from "react";
import { TONE, type Tone } from "./status";

export function Card({ className = "", children, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={`rounded-xl border border-edge bg-panel ${className}`} {...rest}>
      {children}
    </div>
  );
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "default" | "accent" };
export function Button({ variant = "default", className = "", ...rest }: ButtonProps) {
  return <button className={`${variant === "accent" ? "btn-accent" : "btn"} ${className}`} {...rest} />;
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-xl bg-edge/60 ${className}`} aria-hidden />;
}

export function Thumb({
  src,
  alt = "",
  className = "",
  children,
}: {
  src?: string | null;
  alt?: string;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <div className={`flex items-center justify-center overflow-hidden bg-ink ${className}`}>
      {src ? (
        <img src={src} alt={alt} loading="lazy" className="h-full w-full object-cover" />
      ) : (
        children
      )}
    </div>
  );
}

export function EmptyState({
  title,
  desc,
  icon = "🖼️",
  action,
}: {
  title: string;
  desc?: string;
  icon?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="card flex flex-col items-center gap-3 py-14 text-center">
      <div className="text-4xl opacity-80">{icon}</div>
      <div className="text-lg font-semibold">{title}</div>
      {desc && <div className="max-w-sm text-sm text-slate-400">{desc}</div>}
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="card border-red-500/40">
      <div className="mb-2 text-sm text-red-300">{message}</div>
      {onRetry && <Button onClick={onRetry}>Retry</Button>}
    </div>
  );
}

export function Banner({
  tone = "pending",
  children,
  actions,
}: {
  tone?: Tone;
  children: ReactNode;
  actions?: ReactNode;
}) {
  const t = TONE[tone];
  return (
    <div className={`flex items-center justify-between gap-3 rounded-xl border border-edge px-4 py-3 ${t.bg}`}>
      <div className={`flex items-center gap-2 text-sm ${t.text}`}>
        <span className={`h-2 w-2 shrink-0 rounded-full ${t.dot}`} />
        <span>{children}</span>
      </div>
      {actions && <div className="flex shrink-0 gap-2">{actions}</div>}
    </div>
  );
}
