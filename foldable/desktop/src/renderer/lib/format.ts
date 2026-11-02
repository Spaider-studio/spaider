/**
 * German number / byte / time formatting helpers.
 */

const UNITS = ["B", "KB", "MB", "GB", "TB"];

/** 1024-based, German decimal comma: 1834426016 -> "1,7 GB". */
export function formatBytes(bytes: number | null | undefined, digits = 1): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes) || bytes < 0) return "–";
  if (bytes < 1024) return `${Math.round(bytes)} B`;
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const d = unit === 0 ? 0 : digits;
  return `${value.toLocaleString("de-DE", { minimumFractionDigits: d, maximumFractionDigits: d })} ${UNITS[unit]}`;
}

export function formatSpeed(bps: number | null | undefined): string {
  if (!bps || bps <= 0) return "–";
  return `${formatBytes(bps)}/s`;
}

/** "–", "< 1 Min.", "~3 Min.", "~1 Std. 20 Min." */
export function formatEta(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return "–";
  if (seconds < 60) return "< 1 Min.";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `~${minutes} Min.`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest === 0 ? `~${hours} Std.` : `~${hours} Std. ${rest} Min.`;
}

export function formatPercent(done: number, total: number): string {
  if (!total || total <= 0) return "0 %";
  return `${Math.min(100, Math.floor((done / total) * 100))} %`;
}

export function formatNumber(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "–";
  return n.toLocaleString("de-DE");
}

export function formatTokens(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "–";
  if (n >= 1024 && n % 1024 === 0) return `${n / 1024}k`;
  return n.toLocaleString("de-DE");
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "–";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "–";
  return d.toLocaleString("de-DE", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "–";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "–";
  return d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" });
}

/** Short relative label for the conversation list ("Heute", "Gestern", "12.03.2026"). */
export function formatRelativeDay(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const startOfThat = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const diffDays = Math.round((startOfToday - startOfThat) / 86_400_000);
  if (diffDays <= 0) return "Heute";
  if (diffDays === 1) return "Gestern";
  if (diffDays < 7) return `Vor ${diffDays} Tagen`;
  return formatDate(iso);
}

export function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return `${text.slice(0, Math.max(0, max - 1)).trimEnd()}…`;
}
