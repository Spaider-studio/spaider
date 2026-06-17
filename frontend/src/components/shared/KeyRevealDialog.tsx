"use client";

import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Copy, CheckCircle2, ShieldCheck, AlertTriangle } from "lucide-react";

interface Props {
  open: boolean;
  onClose: () => void;
  title: string;
  apiKey: string;
  /** Name/label of the agent the key belongs to. */
  agentName?: string;
}

export default function KeyRevealDialog({
  open,
  onClose,
  title,
  apiKey,
  agentName,
}: Props) {
  const [copied, setCopied] = useState(false);

  function copy() {
    navigator.clipboard.writeText(apiKey).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  // Intentionally: no onOpenChange + no backdrop-close so the user can't
  // accidentally dismiss before copying — this value is only surfaced once.
  return (
    <Dialog.Root open={open}>
      <Dialog.Portal>
        <Dialog.Overlay
          className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 data-[state=open]:animate-fade-in"
          onClick={(e) => e.preventDefault()}
        />
        <Dialog.Content
          className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-full max-w-lg bg-[#12121A] border border-[#2A2A35] rounded-xl p-6 shadow-2xl data-[state=open]:animate-fade-in"
          onEscapeKeyDown={(e) => e.preventDefault()}
          onPointerDownOutside={(e) => e.preventDefault()}
          onInteractOutside={(e) => e.preventDefault()}
        >
          <div className="flex items-start gap-3 mb-4">
            <div className="flex-shrink-0 w-10 h-10 rounded-full bg-emerald-500/10 flex items-center justify-center">
              <ShieldCheck className="w-5 h-5 text-emerald-400" />
            </div>
            <div className="flex-1 min-w-0">
              <Dialog.Title className="text-base font-semibold text-gray-100">
                {title}
              </Dialog.Title>
              {agentName && (
                <Dialog.Description className="mt-1 text-sm text-gray-400">
                  Agent: <span className="text-gray-200">{agentName}</span>
                </Dialog.Description>
              )}
            </div>
          </div>

          <div className="mb-4 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 text-amber-400 flex-shrink-0 mt-0.5" />
            <p className="text-xs text-amber-200 leading-relaxed">
              This is the only time this key will be shown. Copy it now and
              store it securely — it cannot be retrieved later.
            </p>
          </div>

          <div className="flex items-center gap-2 bg-[#0A0A0F] border border-[#2A2A35] rounded-lg p-3 mb-5">
            <code className="flex-1 text-xs text-[#E4E4E7] font-mono break-all select-all">
              {apiKey}
            </code>
            <button
              onClick={copy}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-[#E4E4E7] bg-[#1A1A25] hover:bg-[#2A2A35] border border-[#2A2A35] rounded-md transition-colors flex-shrink-0"
            >
              {copied ? (
                <>
                  <CheckCircle2 className="w-3.5 h-3.5 text-[#10B981]" />
                  Copied
                </>
              ) : (
                <>
                  <Copy className="w-3.5 h-3.5" />
                  Copy
                </>
              )}
            </button>
          </div>

          <div className="flex justify-end">
            <button
              onClick={onClose}
              className="px-4 py-2 text-sm font-medium text-white bg-accent-blue hover:bg-blue-500 rounded-lg transition-colors"
            >
              I&apos;ve saved it
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
