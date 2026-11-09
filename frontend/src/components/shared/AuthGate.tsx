"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { getApiKey } from "@/lib/auth";

export default function AuthGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (pathname === "/login" || getApiKey()) {
      setReady(true);
    } else {
      setReady(false);
      router.replace("/login");
    }
  }, [pathname, router]);

  return ready ? <>{children}</> : null;
}
