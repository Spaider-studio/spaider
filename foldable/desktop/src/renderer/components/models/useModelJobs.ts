/**
 * Shared model management logic for the setup screen and Settings -> Modelle:
 * catalog + installed lists, download/import/activate/calibrate jobs followed
 * through their SSE streams, insufficient-space handling. Download works for
 * built-in catalog ids and for dynamic ids (`hf:<repo>:<file>`) alike.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage, isApiError } from "../../api/client";
import { isTerminalJob, type Job, type ModelCatalogEntry, type ModelInstalled } from "../../api/types";
import { followJob } from "../../lib/jobs";
import { jobErrorText } from "../../lib/modelErrors";
import { notify } from "../../lib/notify";
import { useAppStore } from "../../store/useAppStore";

export interface SpaceError {
  catalogId: string;
  detail: string;
  requiredBytes: number | null;
  freeBytes: number | null;
}

export interface ModelJobs {
  entries: ModelCatalogEntry[] | null;
  catalogDate: string | null;
  installed: ModelInstalled[] | null;
  jobsByCatalog: Record<string, Job>;
  /** Calibration jobs by installed model id. */
  calibrations: Record<string, Job>;
  activation: Job | null;
  importJob: Job | null;
  deleting: string | null;
  spaceError: SpaceError | null;
  loadError: string | null;
  reload(): Promise<void>;
  download(entry: ModelCatalogEntry): Promise<void>;
  cancelJob(job: Job): Promise<void>;
  resumeJob(job: Job): Promise<void>;
  importFile(): Promise<void>;
  activate(modelId: string): Promise<void>;
  calibrate(modelId: string): Promise<void>;
  deleteModel(model: ModelInstalled): Promise<boolean>;
  dismissSpaceError(): void;
  clearActivation(): void;
  clearImportJob(): void;
  clearCalibration(modelId: string): void;
}

function numberOrNull(v: unknown): number | null {
  return typeof v === "number" ? v : null;
}

export function useModelJobs(options: { autoActivate: boolean; onActivated?: (model: ModelInstalled | null) => void }): ModelJobs {
  const client = useAppStore((s) => s.client);
  const refreshSetup = useAppStore((s) => s.refreshSetup);
  const { autoActivate } = options;
  const onActivatedRef = useRef(options.onActivated);
  onActivatedRef.current = options.onActivated;

  const [entries, setEntries] = useState<ModelCatalogEntry[] | null>(null);
  const [catalogDate, setCatalogDate] = useState<string | null>(null);
  const [installed, setInstalled] = useState<ModelInstalled[] | null>(null);
  const [jobsByCatalog, setJobsByCatalog] = useState<Record<string, Job>>({});
  const [calibrations, setCalibrations] = useState<Record<string, Job>>({});
  const [activation, setActivation] = useState<Job | null>(null);
  const [importJob, setImportJob] = useState<Job | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [spaceError, setSpaceError] = useState<SpaceError | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const abortRef = useRef<AbortController>(new AbortController());
  const followed = useRef<Set<string>>(new Set());
  useEffect(() => {
    const controller = new AbortController();
    abortRef.current = controller;
    return () => controller.abort();
  }, []);

  const reload = useCallback(async () => {
    if (!client) return;
    try {
      const [catalog, models] = await Promise.all([client.catalogResponse(), client.installedModels()]);
      setEntries(catalog.entries);
      setCatalogDate(catalog.catalog_date ?? null);
      setInstalled(models);
      setLoadError(null);
    } catch (e) {
      setLoadError(errorMessage(e));
    }
  }, [client]);

  const activate = useCallback(
    async (modelId: string) => {
      if (!client) return;
      try {
        const job = await client.activateModel(modelId);
        setActivation(job);
        if (followed.current.has(job.id)) return;
        followed.current.add(job.id);
        const final = await followJob(client, job.id, setActivation, abortRef.current.signal);
        const setup = await refreshSetup();
        await reload();
        if (final.state === "done") {
          const model =
            setup?.active_chat_model?.id === modelId
              ? setup.active_chat_model
              : setup?.active_embedding_model?.id === modelId
                ? setup.active_embedding_model
                : null;
          notify(model ? `Modell aktiv: ${model.name}` : "Modell aktiviert.", "success");
          onActivatedRef.current?.(model);
        } else if (final.state === "failed") {
          notify(`${final.error?.detail ?? "Aktivierung fehlgeschlagen."} Vorheriges Modell bleibt aktiv.`, "error");
        }
      } catch (e) {
        notify(`Aktivierung fehlgeschlagen: ${errorMessage(e)}`, "error");
      }
    },
    [client, refreshSetup, reload],
  );

  const followDownload = useCallback(
    async (job: Job, entry: ModelCatalogEntry | null) => {
      if (!client) return;
      const catalogId = job.catalog_id ?? entry?.id;
      if (!catalogId) return;
      setJobsByCatalog((m) => ({ ...m, [catalogId]: job }));
      if (followed.current.has(job.id) || isTerminalJob(job)) return;
      followed.current.add(job.id);
      const final = await followJob(
        client,
        job.id,
        (j) => setJobsByCatalog((m) => ({ ...m, [catalogId]: j })),
        abortRef.current.signal,
      );
      await reload();
      if (final.state === "done") {
        setJobsByCatalog((m) => {
          const next = { ...m };
          delete next[catalogId];
          return next;
        });
        notify(`${entry?.name ?? "Modell"} heruntergeladen.`, "success");
        if (autoActivate && final.result_model_id) await activate(final.result_model_id);
      } else if (final.state === "failed") {
        notify(jobErrorText(final.error, "Download fehlgeschlagen."), "error");
      }
    },
    [client, reload, autoActivate, activate],
  );

  const followImport = useCallback(
    async (job: Job) => {
      if (!client) return;
      setImportJob(job);
      if (followed.current.has(job.id) || isTerminalJob(job)) return;
      followed.current.add(job.id);
      const final = await followJob(client, job.id, setImportJob, abortRef.current.signal);
      await reload();
      if (final.state === "done") {
        notify("GGUF-Datei importiert.", "success");
        if (autoActivate && final.result_model_id) await activate(final.result_model_id);
      } else if (final.state === "failed") {
        notify(jobErrorText(final.error, "Import fehlgeschlagen."), "error");
      }
    },
    [client, reload, autoActivate, activate],
  );

  const followCalibration = useCallback(
    async (job: Job) => {
      if (!client || !job.model_id) return;
      const modelId = job.model_id;
      setCalibrations((m) => ({ ...m, [modelId]: job }));
      if (followed.current.has(job.id) || isTerminalJob(job)) return;
      followed.current.add(job.id);
      const final = await followJob(
        client,
        job.id,
        (j) => setCalibrations((m) => ({ ...m, [modelId]: j })),
        abortRef.current.signal,
      );
      await reload();
      if (final.state === "done") {
        setCalibrations((m) => {
          const next = { ...m };
          delete next[modelId];
          return next;
        });
        notify("Messung abgeschlossen.", "success");
      } else if (final.state === "failed") {
        notify(jobErrorText(final.error, "Messung fehlgeschlagen."), "error");
      }
    },
    [client, reload],
  );

  // Initial load + pick up jobs that are still running (e.g. after a restart).
  useEffect(() => {
    if (!client) return;
    let cancelled = false;
    (async () => {
      await reload();
      try {
        const jobs = await client.jobs();
        if (cancelled) return;
        const latestByCatalog = new Map<string, Job>();
        for (const job of jobs) {
          if (job.kind === "download" && job.catalog_id) {
            const prev = latestByCatalog.get(job.catalog_id);
            if (!prev || job.created_at > prev.created_at) latestByCatalog.set(job.catalog_id, job);
          } else if (job.kind === "activate" && !isTerminalJob(job)) {
            void (async () => {
              setActivation(job);
              followed.current.add(job.id);
              await followJob(client, job.id, setActivation, abortRef.current.signal);
              await refreshSetup();
              await reload();
            })();
          } else if (job.kind === "import" && !isTerminalJob(job)) {
            void followImport(job);
          } else if (job.kind === "calibrate" && !isTerminalJob(job)) {
            void followCalibration(job);
          }
        }
        for (const job of latestByCatalog.values()) {
          const resumable = job.state === "paused" || job.state === "failed";
          if (!isTerminalJob(job) || resumable) void followDownload(job, null);
        }
      } catch {
        /* jobs list is optional */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client, reload, followDownload, followImport, followCalibration, refreshSetup]);

  const download = useCallback(
    async (entry: ModelCatalogEntry) => {
      if (!client) return;
      setSpaceError(null);
      try {
        const job = await client.downloadModel(entry.id);
        void followDownload(job.catalog_id ? job : { ...job, catalog_id: entry.id }, entry);
      } catch (e) {
        if (isApiError(e) && e.code === "insufficient_space") {
          setSpaceError({
            catalogId: entry.id,
            detail: e.detail,
            requiredBytes: numberOrNull(e.extra.required_bytes),
            freeBytes: numberOrNull(e.extra.free_bytes),
          });
          return;
        }
        notify(errorMessage(e), "error");
      }
    },
    [client, followDownload],
  );

  const cancelJob = useCallback(
    async (job: Job) => {
      if (!client) return;
      try {
        const updated = await client.cancelJob(job.id);
        if (updated.kind === "download" && updated.catalog_id) setJobsByCatalog((m) => ({ ...m, [updated.catalog_id as string]: updated }));
        if (updated.kind === "import") setImportJob(updated);
        if (updated.kind === "calibrate" && updated.model_id) setCalibrations((m) => ({ ...m, [updated.model_id as string]: updated }));
      } catch (e) {
        notify(`Abbrechen fehlgeschlagen: ${errorMessage(e)}`, "error");
      }
    },
    [client],
  );

  const resumeJob = useCallback(
    async (job: Job) => {
      if (!client) return;
      try {
        const updated = await client.resumeJob(job.id);
        followed.current.delete(updated.id);
        const entry = entries?.find((en) => en.id === updated.catalog_id) ?? null;
        void followDownload(updated, entry);
      } catch (e) {
        notify(`Fortsetzen fehlgeschlagen: ${errorMessage(e)}`, "error");
      }
    },
    [client, entries, followDownload],
  );

  const importFile = useCallback(async () => {
    if (!client) return;
    const file = await window.spaider.pickModelFile();
    if (!file) return;
    try {
      const job = await client.importModel(file);
      void followImport(job);
    } catch (e) {
      notify(`Import fehlgeschlagen: ${errorMessage(e)}`, "error");
    }
  }, [client, followImport]);

  const calibrate = useCallback(
    async (modelId: string) => {
      if (!client) return;
      try {
        const job = await client.calibrateModel(modelId);
        void followCalibration(job.model_id ? job : { ...job, model_id: modelId });
      } catch (e) {
        notify(`Messung konnte nicht gestartet werden: ${errorMessage(e)}`, "error");
      }
    },
    [client, followCalibration],
  );

  const deleteModel = useCallback(
    async (model: ModelInstalled) => {
      if (!client) return false;
      setDeleting(model.id);
      try {
        await client.deleteModel(model.id);
        notify(`„${model.name}“ gelöscht.`, "success");
        await reload();
        return true;
      } catch (e) {
        notify(errorMessage(e), "error");
        return false;
      } finally {
        setDeleting(null);
      }
    },
    [client, reload],
  );

  return {
    entries,
    catalogDate,
    installed,
    jobsByCatalog,
    calibrations,
    activation,
    importJob,
    deleting,
    spaceError,
    loadError,
    reload,
    download,
    cancelJob,
    resumeJob,
    importFile,
    activate,
    calibrate,
    deleteModel,
    dismissSpaceError: () => setSpaceError(null),
    clearActivation: () => setActivation(null),
    clearImportJob: () => setImportJob(null),
    clearCalibration: (modelId: string) =>
      setCalibrations((m) => {
        const next = { ...m };
        delete next[modelId];
        return next;
      }),
  };
}
