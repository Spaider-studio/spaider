export const ROLES = [
  { id: "reader", label: "Reader", can: "Search and ask questions." },
  { id: "contributor", label: "Contributor", can: "Everything a reader can, plus add new knowledge." },
  { id: "editor", label: "Editor", can: "Everything a contributor can, plus correct or delete knowledge and change settings." },
  { id: "steward", label: "Steward", can: "Full control: members, sharing, training, export and deleting the knowledge base." },
  { id: "auditor", label: "Auditor", can: "Read the audit log only. Cannot see the knowledge itself." },
] as const;

export const CLEARANCE_LEVELS = [
  { level: 1, label: "1 · Public" },
  { level: 2, label: "2 · Internal" },
  { level: 3, label: "3 · Confidential" },
  { level: 4, label: "4 · Secret" },
  { level: 5, label: "5 · Top secret" },
] as const;

export function roleLabel(id: string): string {
  return ROLES.find((role) => role.id === id)?.label ?? id;
}
