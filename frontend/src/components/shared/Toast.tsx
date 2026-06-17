"use client";

import * as ToastPrimitive from "@radix-ui/react-toast";
import { CheckCircle2, AlertCircle, Info, X } from "lucide-react";
import { createContext, useContext, useState, useCallback } from "react";
import { cn } from "@/lib/utils";

type ToastVariant = "success" | "error" | "info";

interface ToastItem {
  id: string;
  message: string;
  variant: ToastVariant;
}

interface ToastContextValue {
  toast: (message: string, variant?: ToastVariant) => void;
}

const ToastContext = createContext<ToastContextValue>({
  toast: () => {},
});

export function useToast() {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const toast = useCallback((message: string, variant: ToastVariant = "info") => {
    const id = Math.random().toString(36).slice(2);
    setToasts((prev) => [...prev, { id, message, variant }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 4000);
  }, []);

  const icons = {
    success: <CheckCircle2 className="w-4 h-4 text-green-400" />,
    error: <AlertCircle className="w-4 h-4 text-red-400" />,
    info: <Info className="w-4 h-4 text-blue-400" />,
  };

  const styles = {
    success: "border-green-500/30 bg-green-500/10",
    error: "border-red-500/30 bg-red-500/10",
    info: "border-blue-500/30 bg-[#12121A]",
  };

  return (
    <ToastContext.Provider value={{ toast }}>
      <ToastPrimitive.Provider swipeDirection="right">
        {children}
        {toasts.map((t) => (
          <ToastPrimitive.Root
            key={t.id}
            className={cn(
              "flex items-center gap-3 px-4 py-3 rounded-xl border shadow-xl",
              "animate-slide-in-right",
              styles[t.variant]
            )}
          >
            {icons[t.variant]}
            <ToastPrimitive.Description className="text-sm text-gray-200 flex-1">
              {t.message}
            </ToastPrimitive.Description>
            <ToastPrimitive.Close asChild>
              <button className="text-gray-500 hover:text-gray-300 transition-colors">
                <X className="w-3.5 h-3.5" />
              </button>
            </ToastPrimitive.Close>
          </ToastPrimitive.Root>
        ))}
        <ToastPrimitive.Viewport className="fixed bottom-6 right-6 flex flex-col gap-2 w-80 max-w-[calc(100vw-3rem)] z-[100]" />
      </ToastPrimitive.Provider>
    </ToastContext.Provider>
  );
}
