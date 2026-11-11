/**
 * Saved recipes (GET /api/agent/recipes): purpose, requirements, steps,
 * verification date, known limits; "Mit diesem Ablauf starten" prefills the
 * task form with `recipe_id` + scopes (the user still clicks "Starten").
 */
import { Play, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import ConfirmDialog from "@/components/shared/ConfirmDialog";
import LoadingSpinner from "@/components/shared/LoadingSpinner";
import type { Recipe } from "../../api/types";
import { formatDateTime } from "../../lib/format";
import { scopeSummary } from "../../store/tasks";
import { useAppStore } from "../../store/useAppStore";
import { Badge, Button, EmptyState } from "../ui";

export default function RecipesList() {
  const recipes = useAppStore((s) => s.recipes);
  const loaded = useAppStore((s) => s.recipesLoaded);
  const loadRecipes = useAppStore((s) => s.loadRecipes);
  const deleteRecipe = useAppStore((s) => s.deleteRecipe);
  const startWithRecipe = useAppStore((s) => s.startWithRecipe);
  const [target, setTarget] = useState<Recipe | null>(null);

  useEffect(() => {
    if (!loaded) void loadRecipes();
  }, [loaded, loadRecipes]);

  if (!loaded) {
    return (
      <div className="flex justify-center py-10">
        <LoadingSpinner />
      </div>
    );
  }
  if (recipes.length === 0) {
    return <EmptyState title="Noch keine Abläufe" description="Eine abgeschlossene Aufgabe lässt sich mit „Als Ablauf speichern“ wiederverwendbar machen." />;
  }

  return (
    <div className="flex flex-col gap-3 max-w-3xl" data-testid="recipes-list">
      {recipes.map((r) => (
        <article key={r.id} className="panel p-4" data-testid="recipe-card">
          <div className="flex items-start gap-3">
            <div className="min-w-0 flex-1">
              <h3 className="text-sm font-semibold">
                {r.name} <span className="text-text-secondary font-normal text-xs">v{r.version}</span>
              </h3>
              <p className="mt-1 text-sm text-text-secondary">{r.purpose_de}</p>
              <div className="mt-2 flex items-center gap-1.5 flex-wrap text-[11px] text-text-secondary">
                <Badge tone="blue">{r.steps.length} Schritte</Badge>
                {r.requires.vision && <Badge tone="purple">Bildmodell nötig</Badge>}
                {r.requires.apps.map((a) => (
                  <Badge key={a} tone="cyan">
                    {a}
                  </Badge>
                ))}
                <span>· {scopeSummary(r.scope)}</span>
                <span>· {r.last_verified_at ? `geprüft ${formatDateTime(r.last_verified_at)}` : "noch nicht erneut geprüft"}</span>
              </div>
              {r.parameters.length > 0 && (
                <div className="mt-2 text-[11px] text-text-secondary">
                  Parameter: {r.parameters.map((p) => `${p.name} (${p.description_de || p.example})`).join(", ")}
                </div>
              )}
              {r.known_limits_de.length > 0 && (
                <ul className="mt-2 text-[11px] text-amber-200/90 list-disc pl-4">
                  {r.known_limits_de.map((l, i) => (
                    <li key={i}>{l}</li>
                  ))}
                </ul>
              )}
            </div>
            <div className="flex flex-col gap-1.5 flex-shrink-0">
              <Button size="sm" variant="primary" onClick={() => startWithRecipe(r)} icon={<Play className="w-3.5 h-3.5" />} data-testid="recipe-start">
                Mit diesem Ablauf starten
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setTarget(r)} icon={<Trash2 className="w-3.5 h-3.5" />}>
                Löschen
              </Button>
            </div>
          </div>
        </article>
      ))}
      <ConfirmDialog
        open={target !== null}
        onOpenChange={(open) => !open && setTarget(null)}
        title="Ablauf löschen?"
        description={`„${target?.name ?? ""}“ wird dauerhaft gelöscht.`}
        confirmLabel="Löschen"
        cancelLabel="Abbrechen"
        variant="destructive"
        onConfirm={() => {
          const r = target;
          setTarget(null);
          if (r) void deleteRecipe(r.id);
        }}
      />
    </div>
  );
}
