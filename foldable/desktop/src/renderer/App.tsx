import { useEffect } from "react";
import { ToastProvider, useToast } from "@/components/shared/Toast";
import QuitOverlay from "./components/QuitOverlay";
import { onNotify } from "./lib/notify";
import BootScreen from "./screens/BootScreen";
import MainScreen from "./screens/MainScreen";
import SetupScreen from "./screens/SetupScreen";
import { useAppStore } from "./store/useAppStore";

const SETUP_POLL_MS = 15000;

function useBackendBootstrap() {
  const setBackendStatus = useAppStore((s) => s.setBackendStatus);
  const connect = useAppStore((s) => s.connect);
  const setAppInfo = useAppStore((s) => s.setAppInfo);
  const refreshSetup = useAppStore((s) => s.refreshSetup);

  useEffect(() => {
    const bridge = window.spaider;
    if (!bridge) {
      setBackendStatus({ state: "error", message: "Die Oberfläche läuft nur innerhalb von SpAIder Foldable (Electron)." });
      return;
    }
    const unsubscribe = bridge.onBackendStatus(setBackendStatus);
    bridge.getAppInfo().then(setAppInfo).catch(() => undefined);
    let cancelled = false;
    bridge
      .getBackend()
      .then(async (info) => {
        if (cancelled) return;
        connect(info);
        await refreshSetup();
      })
      .catch(() => {
        /* error state arrives through onBackendStatus */
      });
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [setBackendStatus, connect, setAppInfo, refreshSetup]);
}

/** Loads the main-screen data once the setup is ready and keeps runtime state fresh. */
function useMainData(ready: boolean) {
  const client = useAppStore((s) => s.client);
  const refreshSetup = useAppStore((s) => s.refreshSetup);
  const loadConversations = useAppStore((s) => s.loadConversations);
  const loadSettings = useAppStore((s) => s.loadSettings);
  const loadDocuments = useAppStore((s) => s.loadDocuments);

  useEffect(() => {
    if (!client || !ready) return;
    void loadConversations();
    void loadSettings();
    void loadDocuments();
  }, [client, ready, loadConversations, loadSettings, loadDocuments]);

  useEffect(() => {
    if (!client) return;
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") void refreshSetup();
    }, SETUP_POLL_MS);
    return () => clearInterval(timer);
  }, [client, refreshSetup]);
}

function useKeyboardShortcuts(enabled: boolean) {
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      const state = useAppStore.getState();
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "n") {
        e.preventDefault();
        if (!state.settingsOpen) void state.newConversation();
      } else if (e.key === "Escape" && state.streaming && !state.settingsOpen) {
        void state.stopGeneration();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled]);
}

function AppInner() {
  const { toast } = useToast();
  useEffect(() => onNotify((n) => toast(n.message, n.variant)), [toast]);

  const backend = useAppStore((s) => s.backend);
  const client = useAppStore((s) => s.client);
  const setup = useAppStore((s) => s.setup);
  const setupError = useAppStore((s) => s.setupError);
  const quitting = useAppStore((s) => s.quitting);
  const requestQuit = useAppStore((s) => s.requestQuit);

  useBackendBootstrap();
  const ready = Boolean(client && setup?.state === "ready");
  useMainData(ready);
  useKeyboardShortcuts(ready && !quitting);

  let screen: React.ReactNode;
  if (backend.state === "error") {
    screen = <BootScreen status={backend} onQuit={() => void requestQuit()} />;
  } else if (!client) {
    screen = <BootScreen status={backend} onQuit={() => void requestQuit()} />;
  } else if (!setup) {
    screen = (
      <BootScreen
        status={
          setupError
            ? { state: "error", message: `Der Kern antwortet nicht wie erwartet.\n${setupError}` }
            : { state: "starting", message: "Verbindung zum Kern wird hergestellt …" }
        }
        onQuit={() => void requestQuit()}
      />
    );
  } else if (setup.state === "needs_model") {
    screen = <SetupScreen />;
  } else {
    screen = <MainScreen />;
  }

  return (
    <div className="h-full w-full">
      {screen}
      {quitting && <QuitOverlay />}
    </div>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <AppInner />
    </ToastProvider>
  );
}
