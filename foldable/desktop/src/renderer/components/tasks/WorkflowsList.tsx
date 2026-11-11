/**
 * Workflows of the execution core (GET /api/agent/workflows, v1.5): status,
 * origin, steps, dependency summary, bindings, runs, known limits. "Starten"
 * prefills the task form (the user still clicks "Starten" there), "Prüfen"
 * runs the reuse gate dry on this PC and shows the plan inline,
 * "Negativprüfung" asks the core to prove the workflow is not reused without
 * its preconditions, "Zurückstufen" sets status demoted, "Löschen" asks first.
 */
import { AlertTriangle, ArrowDownToLine, Play, SearchCheck, ShieldCheck, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import ConfirmDialog from "@/components/shared/ConfirmDialog";
import LoadingSpinner from "@/components/shared/LoadingSpinner";
import type { Dependency, Workflow, WorkflowCheckResult } from "../../api/types";
import { formatDateTime } from "../../lib/format";
import {
  BINDING_KIND_LABEL,
  DEPENDENCY_KIND_LABEL,
  dependencySummary,
  formatCoverage,
  isStaleWorkflow,
  REUSE_MODE_LABEL,
  REUSE_MODE_TONE,
  scopeSummary,
  stepCountText,
  WORKFLOW_ORIGIN_LABEL,
  WORKFLOW_STATUS_LABEL,
  WORKFLOW_STATUS_TONE,
} from "../../store/tasks";
import { useAppStore } from "../../store/useAppStore";
import { Badge, Button, EmptyState } from "../ui";

function DependencyRow({ dep }: { dep: Dependency }) {
  return (
    <li className="flex items-start gap-2 text-[11px]">
      <Badge tone={dep.valid ? "green" : "red"}>{dep.valid ? "gültig" : "ungültig"}</Badge>
      <span className="text-text-secondary">{DEPENDENCY_KIND_LABEL[dep.kind] ?? dep.kind}</span>
      <span className="font-mono truncate" title={dep.key}>
        {dep.key}
      </span>
      {dep.origin === "inferred" && <span className="text-text-secondary">(vermutet)</span>}
      {!dep.valid && dep.reason && <span className="text-red-300">– {dep.reason}</span>}
    </li>
  );
}

/** Result of "Prüfen": the plan the reuse gate would choose right now on this PC. */
function CheckResult({ result }: { result: WorkflowCheckResult }) {
  const { plan } = result;
  const invalid = result.dependencies.filter((d) => !d.valid);
  return (
    <div className="mt-3 rounded-lg border border-border bg-bg-primary/60 px-3 py-2 text-xs flex flex-col gap-1.5" data-testid="workflow-check-result">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="font-medium">Prüfung auf diesem Rechner:</span>
        <Badge tone={REUSE_MODE_TONE[plan.mode] ?? "neutral"}>{REUSE_MODE_LABEL[plan.mode] ?? plan.mode}</Badge>
        <span className="text-text-secondary">Abdeckung {formatCoverage(plan.coverage)}</span>
        {plan.strict && <span className="text-text-secondary">· streng</span>}
        {plan.unknown_dependencies && <span className="text-amber-200/90">· unbekannte Abhängigkeiten</span>}
        <span className="text-text-secondary tabular-nums">· Prüfung {Math.round(plan.gate_ms)} ms</span>
      </div>
      {plan.reasons_de.length > 0 && (
        <ul className="list-disc pl-4 text-text-secondary">
          {plan.reasons_de.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      )}
      {plan.repair_steps.length > 0 && <div className="text-text-secondary">Zu reparierende Schritte: {plan.repair_steps.map((s) => `#${s}`).join(", ")}</div>}
      {result.missing_bindings.length > 0 && (
        <div className="text-amber-200/90" data-testid="workflow-missing-bindings">
          Fehlende Zuordnungen: {result.missing_bindings.map((b) => `${b.role} (${BINDING_KIND_LABEL[b.kind] ?? b.kind})`).join(", ")} – werden beim Start erfragt.
        </div>
      )}
      {Object.keys(result.bindings).length > 0 && (
        <div className="text-text-secondary">
          Zuordnungen:{" "}
          {Object.entries(result.bindings).map(([role, target]) => (
            <span key={role} className="mr-2">
              {role} → <span className="font-mono">{target}</span>
            </span>
          ))}
        </div>
      )}
      {invalid.length > 0 && (
        <ul className="flex flex-col gap-0.5">
          {invalid.map((d) => (
            <DependencyRow key={d.id} dep={d} />
          ))}
        </ul>
      )}
    </div>
  );
}

function WorkflowCard({ workflow, onDelete }: { workflow: Workflow; onDelete: (w: Workflow) => void }) {
  const startWithWorkflow = useAppStore((s) => s.startWithWorkflow);
  const checkWorkflow = useAppStore((s) => s.checkWorkflow);
  const negativeCheck = useAppStore((s) => s.negativeCheck);
  const demoteWorkflow = useAppStore((s) => s.demoteWorkflow);
  const check = useAppStore((s) => s.workflowChecks[workflow.id]);
  const negative = useAppStore((s) => s.workflowNegativeChecks[workflow.id]);
  const busy = useAppStore((s) => s.workflowBusy[workflow.id]);
  const stats = workflow.stats;
  const stale = isStaleWorkflow(workflow);
  const runs = (stats?.success_runs ?? 0) + (stats?.failed_runs ?? 0);
  const usable = workflow.status !== "retired";

  return (
    <article className="panel p-4" data-testid="workflow-card" data-workflow-id={workflow.id} data-status={workflow.status}>
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold flex items-center gap-2 flex-wrap">
            {workflow.name} <span className="text-text-secondary font-normal text-xs">v{workflow.version}</span>
            <Badge tone={WORKFLOW_STATUS_TONE[workflow.status] ?? "neutral"}>{WORKFLOW_STATUS_LABEL[workflow.status] ?? workflow.status}</Badge>
            {stats?.origin_kind && <span className="text-[11px] text-text-secondary font-normal">{WORKFLOW_ORIGIN_LABEL[stats.origin_kind] ?? stats.origin_kind}</span>}
          </h3>
          <p className="mt-1 text-sm text-text-secondary">{workflow.purpose_de}</p>
          <div className="mt-2 flex items-center gap-1.5 flex-wrap text-[11px] text-text-secondary">
            <Badge tone="blue">{stepCountText(workflow)}</Badge>
            {workflow.external_effects && <Badge tone="red">externe Wirkung</Badge>}
            {workflow.requires?.vision && <Badge tone="purple">Bildmodell nötig</Badge>}
            {(workflow.requires?.apps ?? []).map((a) => (
              <Badge key={a} tone="cyan">
                {a}
              </Badge>
            ))}
            <span>· {scopeSummary(workflow.scope)}</span>
          </div>
          <div className="mt-1.5 text-[11px] text-text-secondary flex items-center gap-1.5 flex-wrap" data-testid="workflow-dependencies">
            <span>Abhängigkeiten: {dependencySummary(stats?.dependencies)}</span>
            <span>· {runs === 0 ? "noch kein Lauf" : `${runs} ${runs === 1 ? "Lauf" : "Läufe"} (${stats.success_runs} erfolgreich, ${stats.failed_runs} fehlgeschlagen)`}</span>
            <span>· {workflow.last_verified_at ? `geprüft ${formatDateTime(workflow.last_verified_at)}` : "noch nicht geprüft"}</span>
            {stats?.negative_checked && <Badge tone="green">Negativprüfung bestanden</Badge>}
          </div>
          {stale && (
            <div className="mt-2 flex items-start gap-1.5 text-[11px] text-amber-200/90" data-testid="workflow-stale">
              <AlertTriangle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
              <span>Abhängigkeiten geändert – wird bei Start geprüft/repariert.</span>
            </div>
          )}
          {workflow.bindings.length > 0 && (
            <div className="mt-2 text-[11px] text-text-secondary" data-testid="workflow-bindings">
              Zuordnungen auf diesem Rechner:{" "}
              {workflow.bindings.map((b) => (
                <span key={b.role} className="mr-2">
                  <span className="text-text-primary">{b.role}</span> ({BINDING_KIND_LABEL[b.kind] ?? b.kind}
                  {b.required ? "" : ", optional"}){b.hint ? ` – ${b.hint}` : ""}
                  {b.original && (
                    <span className="font-mono" title="Wert auf dem Rechner der Aufnahme">
                      {" "}
                      [{b.original}]
                    </span>
                  )}
                </span>
              ))}
            </div>
          )}
          {workflow.parameters.length > 0 && (
            <div className="mt-2 text-[11px] text-text-secondary">
              Parameter: {workflow.parameters.map((p) => `${p.name}${p.required ? "*" : ""} (${p.description_de || p.example})`).join(", ")}
            </div>
          )}
          {workflow.known_limits_de.length > 0 && (
            <ul className="mt-2 text-[11px] text-amber-200/90 list-disc pl-4">
              {workflow.known_limits_de.map((l, i) => (
                <li key={i}>{l}</li>
              ))}
            </ul>
          )}
          {check && <CheckResult result={check} />}
          {negative && (
            <div className={`mt-2 text-[11px] ${negative.ok ? "text-emerald-300" : "text-red-300"}`} data-testid="workflow-negative-result">
              Negativprüfung: {negative.ok ? "bestanden" : "nicht bestanden"} ({REUSE_MODE_LABEL[negative.mode as keyof typeof REUSE_MODE_LABEL] ?? negative.mode})
              {negative.reasons_de.length > 0 && ` – ${negative.reasons_de.join("; ")}`}
            </div>
          )}
        </div>
        <div className="flex flex-col gap-1.5 flex-shrink-0">
          <Button size="sm" variant="primary" onClick={() => startWithWorkflow(workflow)} disabled={!usable} icon={<Play className="w-3.5 h-3.5" />} data-testid="workflow-start">
            Starten
          </Button>
          <Button size="sm" onClick={() => void checkWorkflow(workflow.id)} disabled={Boolean(busy)} loading={busy === "check"} icon={<SearchCheck className="w-3.5 h-3.5" />} data-testid="workflow-check">
            Prüfen
          </Button>
          <Button size="sm" onClick={() => void negativeCheck(workflow.id)} disabled={Boolean(busy)} loading={busy === "negative"} icon={<ShieldCheck className="w-3.5 h-3.5" />} data-testid="workflow-negative">
            Negativprüfung
          </Button>
          {workflow.status !== "demoted" && workflow.status !== "retired" && (
            <Button size="sm" variant="ghost" onClick={() => void demoteWorkflow(workflow.id)} disabled={Boolean(busy)} loading={busy === "demote"} icon={<ArrowDownToLine className="w-3.5 h-3.5" />} data-testid="workflow-demote">
              Zurückstufen
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={() => onDelete(workflow)} disabled={Boolean(busy)} loading={busy === "delete"} icon={<Trash2 className="w-3.5 h-3.5" />} data-testid="workflow-delete">
            Löschen
          </Button>
        </div>
      </div>
    </article>
  );
}

export default function WorkflowsList() {
  const workflows = useAppStore((s) => s.workflows);
  const loaded = useAppStore((s) => s.workflowsLoaded);
  const loadWorkflows = useAppStore((s) => s.loadWorkflows);
  const deleteWorkflow = useAppStore((s) => s.deleteWorkflow);
  const [target, setTarget] = useState<Workflow | null>(null);

  useEffect(() => {
    if (!loaded) void loadWorkflows();
  }, [loaded, loadWorkflows]);

  if (!loaded) {
    return (
      <div className="flex justify-center py-10">
        <LoadingSpinner />
      </div>
    );
  }
  if (workflows.length === 0) {
    return (
      <EmptyState
        title="Noch keine Abläufe"
        description="Eine erfolgreich abgeschlossene Aufgabe lässt sich mit „Als Ablauf speichern“ wiederverwendbar machen – oder du bringst SpAIder einen Ablauf direkt bei („Ablauf beibringen“)."
      />
    );
  }

  return (
    <div className="flex flex-col gap-3 max-w-3xl" data-testid="workflows-list">
      {workflows.map((w) => (
        <WorkflowCard key={w.id} workflow={w} onDelete={setTarget} />
      ))}
      <ConfirmDialog
        open={target !== null}
        onOpenChange={(open) => !open && setTarget(null)}
        title="Ablauf löschen?"
        description={`„${target?.name ?? ""}“ wird mit allen Abhängigkeiten und Laufdaten dauerhaft gelöscht.`}
        confirmLabel="Löschen"
        cancelLabel="Abbrechen"
        variant="destructive"
        onConfirm={() => {
          const w = target;
          setTarget(null);
          if (w) void deleteWorkflow(w.id);
        }}
      />
    </div>
  );
}
