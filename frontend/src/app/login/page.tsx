"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { clearApiKey, setApiKey } from "@/lib/auth";
import { whoAmI } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [key, setKey] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setApiKey(key.trim());
    try {
      await whoAmI();
      router.replace("/studio");
    } catch {
      clearApiKey();
      setError("That key was not accepted.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-[#0A0A0F] p-4">
      <form onSubmit={submit} className="w-full max-w-sm rounded-lg border border-white/10 bg-white/5 p-6">
        <h1 className="mb-1 text-lg font-semibold">Sign in to SpAIder</h1>
        <p className="mb-4 text-sm text-zinc-400">Use an API key. It stays in this tab only.</p>
        <label htmlFor="api-key" className="mb-1 block text-sm text-zinc-300">
          API key
        </label>
        <input
          id="api-key"
          type="password"
          autoComplete="off"
          value={key}
          onChange={(event) => setKey(event.target.value)}
          className="mb-3 w-full rounded border border-white/10 bg-black/30 px-3 py-2 text-sm"
        />
        {error && (
          <p role="alert" className="mb-3 text-sm text-red-400">
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={busy || !key.trim()}
          className="w-full rounded bg-blue-600 px-3 py-2 text-sm font-medium disabled:opacity-50"
        >
          {busy ? "Checking…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
