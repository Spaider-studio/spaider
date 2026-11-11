/**
 * Validation of Electron accelerator strings (https://www.electronjs.org/docs/latest/api/accelerator)
 * for the hotkey settings. Pure, shared by the main process (before
 * globalShortcut.register), the settings UI and the tests.
 *
 * A global hotkey must carry at least one of Ctrl/Alt/Super/Cmd; Shift alone
 * would swallow ordinary typing in every application.
 */

const MODIFIERS = new Map<string, string>([
  ["command", "Command"],
  ["cmd", "Command"],
  ["control", "Control"],
  ["ctrl", "Control"],
  ["commandorcontrol", "CommandOrControl"],
  ["cmdorctrl", "CommandOrControl"],
  ["alt", "Alt"],
  ["option", "Option"],
  ["altgr", "AltGr"],
  ["shift", "Shift"],
  ["super", "Super"],
  ["meta", "Meta"],
]);

const NAMED_KEYS = new Map<string, string>(
  [
    "Plus",
    "Space",
    "Tab",
    "Capslock",
    "Numlock",
    "Scrolllock",
    "Backspace",
    "Delete",
    "Insert",
    "Return",
    "Enter",
    "Up",
    "Down",
    "Left",
    "Right",
    "Home",
    "End",
    "PageUp",
    "PageDown",
    "Escape",
    "Esc",
    "VolumeUp",
    "VolumeDown",
    "VolumeMute",
    "MediaNextTrack",
    "MediaPreviousTrack",
    "MediaStop",
    "MediaPlayPause",
    "PrintScreen",
    "numdec",
    "numadd",
    "numsub",
    "nummult",
    "numdiv",
  ].map((k) => [k.toLowerCase(), k]),
);

const PUNCTUATION = new Set([")", "!", "@", "#", "$", "%", "^", "&", "*", "(", ":", ";", "+", "=", "<", ",", "_", "-", ">", ".", "?", "/", "~", "`", "{", "]", "[", "|", "\\", "}", '"', "'"]);

export interface AcceleratorCheck {
  ok: boolean;
  /** Canonical spelling (e.g. "Control+Alt+Space"); the input when invalid. */
  normalized: string;
  /** German reason when `ok` is false. */
  error: string | null;
}

/** Splits on "+" but keeps a trailing/leading literal "+" key ("Ctrl++" -> ["Ctrl", "+"]). */
function tokenize(input: string): string[] {
  const parts: string[] = [];
  let current = "";
  for (let i = 0; i < input.length; i++) {
    const ch = input[i];
    if (ch === "+" && current !== "") {
      parts.push(current);
      current = "";
    } else if (ch === "+" && current === "") {
      // "++" -> literal plus key
      parts.push("+");
      i += 1; // skip the separator that follows a literal plus, if any
    } else {
      current += ch;
    }
  }
  if (current !== "") parts.push(current);
  return parts.map((p) => p.trim()).filter((p) => p.length > 0);
}

function normalizeKey(raw: string): string | null {
  if (raw.length === 1) {
    if (/^[0-9]$/.test(raw)) return raw;
    if (/^[a-z]$/i.test(raw)) return raw.toUpperCase();
    if (PUNCTUATION.has(raw)) return raw;
    return null;
  }
  const lower = raw.toLowerCase();
  const fn = /^f([1-9]|1[0-9]|2[0-4])$/.exec(lower);
  if (fn) return `F${fn[1]}`;
  const num = /^num([0-9])$/.exec(lower);
  if (num) return `num${num[1]}`;
  return NAMED_KEYS.get(lower) ?? null;
}

export function validateAccelerator(input: string): AcceleratorCheck {
  const text = (input ?? "").trim();
  if (!text) return { ok: false, normalized: input, error: "Bitte eine Tastenkombination eingeben." };
  const tokens = tokenize(text);
  if (tokens.length === 0) return { ok: false, normalized: input, error: "Bitte eine Tastenkombination eingeben." };

  const modifiers: string[] = [];
  let key: string | null = null;
  for (const token of tokens) {
    const mod = MODIFIERS.get(token.toLowerCase());
    if (mod) {
      if (key !== null) return { ok: false, normalized: input, error: `Modifizierer „${token}“ muss vor der Taste stehen.` };
      if (!modifiers.includes(mod)) modifiers.push(mod);
      continue;
    }
    if (key !== null) return { ok: false, normalized: input, error: "Nur eine Taste pro Kombination (z. B. Ctrl+Alt+Space)." };
    key = normalizeKey(token);
    if (key === null) return { ok: false, normalized: input, error: `Unbekannte Taste „${token}“.` };
  }
  if (key === null) return { ok: false, normalized: input, error: "Es fehlt die eigentliche Taste (z. B. Space, F9, K)." };
  const strong = modifiers.some((m) => m !== "Shift");
  if (!strong) {
    return { ok: false, normalized: input, error: "Globale Hotkeys brauchen Ctrl, Alt oder Super (Shift allein reicht nicht)." };
  }
  return { ok: true, normalized: [...modifiers, key].join("+"), error: null };
}

/** True when both strings describe the same shortcut (ignores spelling/case/order). */
export function sameAccelerator(a: string, b: string): boolean {
  const ca = validateAccelerator(a);
  const cb = validateAccelerator(b);
  if (!ca.ok || !cb.ok) return false;
  const norm = (s: string) => {
    const parts = s.split("+");
    const key = parts.pop();
    return [...parts.sort(), key].join("+");
  };
  return norm(ca.normalized) === norm(cb.normalized);
}
