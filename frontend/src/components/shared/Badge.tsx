"use client";

import { NODE_TYPE_COLORS } from "@/lib/constants";
import { cn } from "@/lib/utils";

interface Props {
  type: string;
  className?: string;
  small?: boolean;
}

export default function Badge({ type, className, small }: Props) {
  const color = NODE_TYPE_COLORS[type] ?? NODE_TYPE_COLORS.OTHER;

  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full font-medium",
        small ? "px-1.5 py-0.5 text-xs" : "px-2.5 py-0.5 text-xs",
        className
      )}
      style={{
        backgroundColor: `${color}22`,
        color,
        border: `1px solid ${color}44`,
      }}
    >
      {type}
    </span>
  );
}
