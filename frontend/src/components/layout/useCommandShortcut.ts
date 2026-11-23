import { useEffect } from "react";

/** Calls `toggle` when the user presses Cmd+K or Ctrl+K, wherever the focus is. */
export function useCommandShortcut(toggle: () => void) {
  useEffect(() => {
    function handler(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key === "k") {
        event.preventDefault();
        toggle();
      }
    }
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [toggle]);
}
