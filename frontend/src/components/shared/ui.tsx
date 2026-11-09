"use client";

export function Button(props: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...props}
      className={`rounded border border-white/10 bg-white/10 px-2.5 py-1 text-xs hover:bg-white/20 disabled:opacity-40 ${props.className ?? ""}`}
    />
  );
}

export function ErrorNote({ message }: { message: string }) {
  return message ? (
    <p role="alert" className="mb-3 text-sm text-red-400">
      {message}
    </p>
  ) : null;
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="text-sm text-zinc-500">{children}</p>;
}

export function reason(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong.";
}
