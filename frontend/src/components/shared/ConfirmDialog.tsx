"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { AlertTriangle, X } from "lucide-react";
import { cn } from "@/lib/utils";

export type ConfirmVariant = "default" | "destructive" | "warning";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  confirmLabel?: string;
  cancelLabel?: string;
  onConfirm: () => void;
  /** @deprecated use `variant="destructive"` */
  destructive?: boolean;
  variant?: ConfirmVariant;
  loading?: boolean;
}

const VARIANT_STYLES: Record<
  ConfirmVariant,
  { iconBg: string; iconColor: string; confirmBtn: string }
> = {
  default: {
    iconBg: "",
    iconColor: "",
    confirmBtn: "bg-accent-blue hover:bg-blue-500 text-white",
  },
  destructive: {
    iconBg: "bg-red-500/10",
    iconColor: "text-red-400",
    confirmBtn: "bg-red-600 hover:bg-red-500 text-white",
  },
  warning: {
    iconBg: "bg-amber-500/10",
    iconColor: "text-amber-400",
    confirmBtn: "bg-amber-600 hover:bg-amber-500 text-white",
  },
};

export default function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  onConfirm,
  destructive = false,
  variant,
  loading = false,
}: Props) {
  const resolved: ConfirmVariant =
    variant ?? (destructive ? "destructive" : "default");
  const style = VARIANT_STYLES[resolved];
  const showIcon = resolved !== "default";

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 data-[state=open]:animate-fade-in" />
        <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-full max-w-md bg-[#12121A] border border-[#2A2A35] rounded-xl p-6 shadow-2xl data-[state=open]:animate-fade-in">
          <div className="flex items-start gap-3 mb-4">
            {showIcon && (
              <div
                className={cn(
                  "flex-shrink-0 w-10 h-10 rounded-full flex items-center justify-center",
                  style.iconBg
                )}
              >
                <AlertTriangle className={cn("w-5 h-5", style.iconColor)} />
              </div>
            )}
            <div className="flex-1">
              <Dialog.Title className="text-base font-semibold text-gray-100">
                {title}
              </Dialog.Title>
              <Dialog.Description className="mt-1 text-sm text-gray-400 leading-relaxed">
                {description}
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <button className="text-gray-500 hover:text-gray-300 transition-colors">
                <X className="w-4 h-4" />
              </button>
            </Dialog.Close>
          </div>

          <div className="flex gap-3 justify-end mt-6">
            <Dialog.Close asChild>
              <button
                className="px-4 py-2 text-sm font-medium text-gray-300 bg-[#1A1A25] hover:bg-[#2A2A35] border border-[#2A2A35] rounded-lg transition-colors"
                disabled={loading}
              >
                {cancelLabel}
              </button>
            </Dialog.Close>
            <button
              onClick={onConfirm}
              disabled={loading}
              className={cn(
                "px-4 py-2 text-sm font-medium rounded-lg transition-colors disabled:opacity-50",
                style.confirmBtn
              )}
            >
              {loading ? "Processing..." : confirmLabel}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
