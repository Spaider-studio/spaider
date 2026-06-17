"use client";

import Sidebar from "@/components/layout/Sidebar";
import NeuralMultiverse from "@/components/studio/NeuralMultiverse";

export default function MultiversePage() {
  return (
    <div className="w-screen h-screen flex overflow-hidden">
      <Sidebar />
      <div className="flex-1 min-w-0 h-full">
        <NeuralMultiverse />
      </div>
    </div>
  );
}
