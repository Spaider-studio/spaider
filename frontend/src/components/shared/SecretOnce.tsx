"use client";

import { useState } from "react";
import { Button } from "@/components/shared/ui";

/** Shows a newly created API key. The server cannot show it again. */
export default function SecretOnce({ title, value, onClose }: { title: string; value: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false);

  return (
    <div role="status" className="mb-4 rounded border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
      <p className="font-medium">{title}</p>
      <p className="mb-2 text-xs text-zinc-300">Copy it now. It cannot be shown again.</p>
      <code className="mb-2 block break-all rounded bg-black/40 p-2 text-xs">{value}</code>
      <span className="flex gap-2">
        <Button
          onClick={async () => {
            await navigator.clipboard.writeText(value);
            setCopied(true);
          }}
        >
          {copied ? "Copied" : "Copy"}
        </Button>
        <Button onClick={onClose}>Done</Button>
      </span>
    </div>
  );
}
