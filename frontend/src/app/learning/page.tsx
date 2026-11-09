"use client";

import { useEffect, useState } from "react";
import KbPage from "@/components/shared/KbPage";
import { Button, Empty, ErrorNote, reason } from "@/components/shared/ui";
import { getSleepReport, runSleep, type SleepReport } from "@/lib/api";

function Learning({ agentId }: { agentId: string }) {
  const [report, setReport] = useState<SleepReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    getSleepReport(agentId)
      .then(setReport)
      .catch(() => setReport(null));
  }, [agentId]);

  async function run() {
    setBusy(true);
    setError("");
    try {
      setReport(await runSleep(agentId));
    } catch (e) {
      setError(reason(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="mb-3">
        <Button onClick={run} disabled={busy}>
          {busy ? "Sleeping…" : "Run sleep cycle now"}
        </Button>
      </div>
      <ErrorNote message={error} />
      {!report && <Empty>This knowledge base has not slept yet.</Empty>}
      {report && (
        <>
          <p className="mb-2 text-sm text-zinc-400">
            Last run {new Date(report.finished_at).toLocaleString()}: {report.status}
          </p>
          <table className="w-full text-left text-sm">
            <thead className="text-zinc-500">
              <tr>
                <th className="py-1 font-normal">Pass</th>
                <th className="py-1 font-normal">Status</th>
                <th className="py-1 font-normal">Result</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(report.passes).map(([name, pass]) => {
                const { status, ms: _ms, ...counts } = pass;
                return (
                  <tr key={name} className="border-t border-white/10">
                    <td className="py-1">{name}</td>
                    <td className={status === "ok" ? "py-1 text-emerald-400" : "py-1 text-red-400"}>{status}</td>
                    <td className="py-1 text-zinc-400">
                      {Object.entries(counts)
                        .map(([key, value]) => `${key}: ${value}`)
                        .join(", ")}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}
    </>
  );
}

export default function LearningPage() {
  return <KbPage title="Learning">{(agentId) => <Learning key={agentId} agentId={agentId} />}</KbPage>;
}
