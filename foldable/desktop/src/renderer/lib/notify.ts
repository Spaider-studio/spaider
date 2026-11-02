/**
 * Tiny event bus so non-React code (store actions) can raise toasts.
 * App.tsx subscribes once and forwards to the SpAIder ToastProvider.
 */
export type NotifyVariant = "success" | "error" | "info";

export interface Notification {
  message: string;
  variant: NotifyVariant;
}

type Listener = (n: Notification) => void;
const listeners = new Set<Listener>();

export function notify(message: string, variant: NotifyVariant = "info"): void {
  for (const l of listeners) l({ message, variant });
}

export function onNotify(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
