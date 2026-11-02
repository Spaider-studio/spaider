/**
 * Small UI primitives in the SpAIder look (dark, calm).
 */
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/utils";
import LoadingSpinner from "@/components/shared/LoadingSpinner";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
type ButtonSize = "sm" | "md";

const BUTTON_VARIANT: Record<ButtonVariant, string> = {
  primary: "bg-accent-blue hover:bg-blue-500 text-white border border-transparent disabled:hover:bg-accent-blue",
  secondary:
    "bg-bg-tertiary hover:bg-[#22222e] text-text-primary border border-border disabled:hover:bg-bg-tertiary",
  ghost: "bg-transparent hover:bg-white/5 text-text-secondary hover:text-text-primary border border-transparent",
  danger: "bg-red-600/90 hover:bg-red-500 text-white border border-transparent disabled:hover:bg-red-600/90",
};
const BUTTON_SIZE: Record<ButtonSize, string> = {
  sm: "px-2.5 py-1.5 text-xs gap-1.5 rounded-md",
  md: "px-4 py-2 text-sm gap-2 rounded-lg",
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  icon?: ReactNode;
}

export function Button({
  variant = "secondary",
  size = "md",
  loading = false,
  icon,
  className,
  children,
  disabled,
  type = "button",
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      disabled={disabled || loading}
      className={cn(
        "inline-flex items-center justify-center font-medium transition-colors whitespace-nowrap disabled:opacity-50",
        BUTTON_VARIANT[variant],
        BUTTON_SIZE[size],
        className,
      )}
      {...rest}
    >
      {loading ? <LoadingSpinner size="sm" className="border-white/30 border-t-white" /> : icon}
      {children}
    </button>
  );
}

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string;
  danger?: boolean;
}

export function IconButton({ label, danger, className, children, type = "button", ...rest }: IconButtonProps) {
  return (
    <button
      type={type}
      aria-label={label}
      title={label}
      className={cn(
        "inline-flex items-center justify-center w-7 h-7 rounded-md text-text-secondary transition-colors disabled:opacity-40",
        danger ? "hover:bg-red-500/10 hover:text-red-400" : "hover:bg-white/5 hover:text-text-primary",
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}

type BadgeTone = "neutral" | "blue" | "green" | "yellow" | "red" | "purple" | "cyan";
const BADGE_TONE: Record<BadgeTone, string> = {
  neutral: "bg-white/5 text-text-secondary border-border",
  blue: "bg-blue-500/10 text-blue-300 border-blue-500/30",
  green: "bg-emerald-500/10 text-emerald-300 border-emerald-500/30",
  yellow: "bg-amber-500/10 text-amber-300 border-amber-500/30",
  red: "bg-red-500/10 text-red-300 border-red-500/30",
  purple: "bg-violet-500/10 text-violet-300 border-violet-500/30",
  cyan: "bg-cyan-500/10 text-cyan-300 border-cyan-500/30",
};

export function Badge({
  tone = "neutral",
  className,
  children,
  title,
}: {
  tone?: BadgeTone;
  className?: string;
  children: ReactNode;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md border text-[11px] font-medium leading-tight whitespace-nowrap",
        BADGE_TONE[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  description,
  disabled,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  description?: string;
  disabled?: boolean;
}) {
  return (
    <label className={cn("flex items-start justify-between gap-4 py-3", disabled && "opacity-50")}>
      <span className="min-w-0">
        <span className="block text-sm font-medium text-text-primary">{label}</span>
        {description && <span className="block text-xs text-text-secondary mt-0.5">{description}</span>}
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative flex-shrink-0 w-10 h-6 rounded-full border transition-colors",
          checked ? "bg-accent-blue border-accent-blue" : "bg-bg-tertiary border-border",
        )}
      >
        <span
          className={cn(
            "absolute top-0.5 left-0.5 w-[18px] h-[18px] rounded-full bg-white transition-transform",
            checked && "translate-x-4",
          )}
        />
      </button>
    </label>
  );
}

export function Field({
  label,
  description,
  children,
  className,
}: {
  label: string;
  description?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-start justify-between gap-4 py-3", className)}>
      <div className="min-w-0">
        <div className="text-sm font-medium text-text-primary">{label}</div>
        {description && <div className="text-xs text-text-secondary mt-0.5">{description}</div>}
      </div>
      <div className="flex-shrink-0">{children}</div>
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center text-center gap-2 py-10 px-6", className)}>
      {icon && <div className="text-text-secondary/60 mb-1">{icon}</div>}
      <div className="text-sm font-medium text-text-primary">{title}</div>
      {description && <div className="text-xs text-text-secondary max-w-sm leading-relaxed">{description}</div>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}

export function ProgressBar({ value, tone = "blue", className }: { value: number; tone?: "blue" | "green" | "red"; className?: string }) {
  const pct = Math.max(0, Math.min(100, value));
  const color = tone === "green" ? "bg-accent-green" : tone === "red" ? "bg-accent-red" : "bg-accent-blue";
  return (
    <div className={cn("h-1.5 w-full rounded-full bg-white/5 overflow-hidden", className)} role="progressbar" aria-valuenow={pct}>
      <div className={cn("h-full rounded-full transition-[width] duration-300", color)} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function SectionTitle({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 mb-3">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-text-secondary">{children}</h3>
      {action}
    </div>
  );
}

export function KeyValue({ label, value, mono }: { label: string; value: ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-start justify-between gap-4 py-2 border-b border-border/60 last:border-b-0">
      <span className="text-xs text-text-secondary flex-shrink-0">{label}</span>
      <span className={cn("text-xs text-text-primary text-right break-all", mono && "font-mono")}>{value}</span>
    </div>
  );
}

export function Select({
  value,
  onChange,
  options,
  disabled,
  ariaLabel,
}: {
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
  disabled?: boolean;
  ariaLabel?: string;
}) {
  return (
    <select
      value={value}
      disabled={disabled}
      aria-label={ariaLabel}
      onChange={(e) => onChange(e.target.value)}
      className="bg-bg-tertiary border border-border rounded-lg px-3 py-1.5 text-sm text-text-primary disabled:opacity-50"
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

export const inputClass =
  "w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-sm text-text-primary placeholder:text-text-secondary/60 focus:border-accent-blue focus:outline-none";
