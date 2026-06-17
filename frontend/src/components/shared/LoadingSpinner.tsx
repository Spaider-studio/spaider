"use client";

import { cn } from "@/lib/utils";

interface Props {
  size?: "sm" | "md" | "lg";
  className?: string;
}

const sizes = { sm: "w-4 h-4", md: "w-6 h-6", lg: "w-10 h-10" };
const borders = { sm: "border-2", md: "border-2", lg: "border-4" };

export default function LoadingSpinner({ size = "md", className }: Props) {
  return (
    <div
      className={cn(
        sizes[size],
        borders[size],
        "rounded-full border-gray-700 border-t-accent-blue animate-spin",
        className
      )}
    />
  );
}
