/**
 * Entry of the overlay window (`#/overlay`): connects to the core through the
 * same bridge, listens for show events / hotkeys, reports the first paint of
 * every show to the main process (two animation frames after the show
 * event) and hides on Esc.
 */
import { useEffect } from "react";
import { ToastProvider, useToast } from "@/components/shared/Toast";
import { onNotify } from "./lib/notify";
import OverlayScreen from "./screens/OverlayScreen";
import { useOverlayStore } from "./store/useOverlayStore";

function OverlayInner() {
  const { toast } = useToast();
  useEffect(() => onNotify((n) => toast(n.message, n.variant)), [toast]);

  const setBackendStatus = useOverlayStore((s) => s.setBackendStatus);
  const connect = useOverlayStore((s) => s.connect);
  const onShown = useOverlayStore((s) => s.onShown);
  const applyHotkey = useOverlayStore((s) => s.applyHotkey);
  const hide = useOverlayStore((s) => s.hide);

  useEffect(() => {
    const bridge = window.spaider;
    if (!bridge) {
      setBackendStatus({ state: "error", message: "Das Live-Fenster läuft nur innerhalb von SpAIder Foldable (Electron)." });
      return;
    }
    const unsubscribes = [
      bridge.onBackendStatus(setBackendStatus),
      bridge.onOverlayShown((e) => {
        onShown(e);
        // First paint of this show: report after two animation frames.
        requestAnimationFrame(() => requestAnimationFrame(() => bridge.reportOverlayPainted(e.showId)));
      }),
      bridge.onHotkey(applyHotkey),
    ];
    let cancelled = false;
    bridge
      .getBackend()
      .then((info) => {
        if (!cancelled) connect(info);
      })
      .catch(() => {
        /* error state arrives through onBackendStatus */
      });
    // Shown before this renderer subscribed (first hotkey right after start)?
    void bridge.getOverlayState().then((st) => {
      if (!cancelled && st.visible) onShown({ sourceHint: st.sourceHint, shownAt: st.shownAt ?? 0, hotkeyAt: null, showId: 0 });
    });
    return () => {
      cancelled = true;
      for (const u of unsubscribes) u();
    };
  }, [setBackendStatus, connect, onShown, applyHotkey]);

  // Esc anywhere hides the overlay (the picker handles its own Esc first).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) {
        e.preventDefault();
        hide();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [hide]);

  return <OverlayScreen />;
}

export default function OverlayApp() {
  return (
    <ToastProvider>
      <OverlayInner />
    </ToastProvider>
  );
}
