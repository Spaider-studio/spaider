import { Check, Copy } from "lucide-react";
import { useState, type ReactNode } from "react";
import { notify } from "../../lib/notify";

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    notify("Kopieren nicht möglich.", "error");
    return false;
  }
}

export default function CodeBlock({
  language,
  text,
  children,
}: {
  language: string | null;
  text: string;
  children: ReactNode;
}) {
  const [copied, setCopied] = useState(false);
  const onCopy = async () => {
    if (await copyText(text)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };
  return (
    <div className="my-2 rounded-lg border border-border bg-bg-primary overflow-hidden" data-testid="code-block">
      <div className="flex items-center justify-between px-3 py-1 border-b border-border bg-bg-tertiary/60">
        <span className="font-mono text-[11px] uppercase tracking-wide text-text-secondary">{language ?? "Code"}</span>
        <button
          type="button"
          onClick={() => void onCopy()}
          aria-label="Code kopieren"
          className="inline-flex items-center gap-1 text-[11px] text-text-secondary hover:text-text-primary px-1.5 py-0.5 rounded transition-colors"
        >
          {copied ? <Check className="w-3 h-3 text-accent-green" /> : <Copy className="w-3 h-3" />}
          {copied ? "Kopiert" : "Kopieren"}
        </button>
      </div>
      <pre className="hljs">{children}</pre>
    </div>
  );
}
