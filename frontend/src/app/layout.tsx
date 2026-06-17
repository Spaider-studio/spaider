import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { ToastProvider } from "@/components/shared/Toast";
import { EngineProvider } from "@/context/EngineContext";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
});

export const metadata: Metadata = {
  title: "SpAIder Neural Studio",
  description: "Memory Infrastructure for AI Agents — 3D Knowledge Graph Visualization",
  keywords: ["knowledge graph", "AI", "agents", "Neo4j", "neural", "memory"],
};

export const viewport: Viewport = {
  themeColor: "#0A0A0F",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          rel="preconnect"
          href="https://fonts.gstatic.com"
          crossOrigin="anonymous"
        />
      </head>
      <body
        className={`${inter.variable} font-sans bg-[#0A0A0F] text-[#E4E4E7] min-h-screen antialiased`}
      >
        <ToastProvider>
          <EngineProvider>
            {children}
          </EngineProvider>
        </ToastProvider>
      </body>
    </html>
  );
}
