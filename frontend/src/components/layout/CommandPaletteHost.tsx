"use client";

import { useCallback, useState } from "react";
import CommandPalette from "./CommandPalette";
import { useCommandShortcut } from "./useCommandShortcut";

/**
 * The command palette with its Cmd+K shortcut, for pages that have their own top bar and so
 * do not carry the shared header (the Studio and the Multiverse).
 */
export default function CommandPaletteHost() {
  const [open, setOpen] = useState(false);
  useCommandShortcut(useCallback(() => setOpen((value) => !value), []));
  return <CommandPalette open={open} onClose={() => setOpen(false)} />;
}
