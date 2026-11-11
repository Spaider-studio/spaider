/**
 * SpAIder spider-web logo. SVG copied from
 * frontend/src/components/layout/Sidebar.tsx (SpAIder, AGPL-3.0).
 */
import { cn } from "@/lib/utils";

export default function Logo({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={cn("w-7 h-7 flex-shrink-0 text-accent-blue", className)}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="10" strokeOpacity="0.2" />
      <circle cx="12" cy="12" r="6" strokeOpacity="0.35" />
      <circle cx="12" cy="12" r="2" fill="currentColor" stroke="none" />
      <line x1="12" y1="2" x2="12" y2="22" strokeOpacity="0.5" />
      <line x1="2" y1="12" x2="22" y2="12" strokeOpacity="0.5" />
      <line x1="4.9" y1="4.9" x2="19.1" y2="19.1" strokeOpacity="0.5" />
      <line x1="19.1" y1="4.9" x2="4.9" y2="19.1" strokeOpacity="0.5" />
    </svg>
  );
}
