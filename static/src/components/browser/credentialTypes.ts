export interface PasswordRecord {
  id: string;
  origin: string;
  username: string;
  updatedAt: number;
  usedAt: number;
}
export interface PasswordSettings {
  save: boolean;
  autofill: boolean;
}
export interface CredentialSnapshot {
  prompt: { id: string; tabId: string; origin: string; username: string; update: boolean } | null;
  fill: {
    id: string;
    tabId: string;
    origin: string;
    accounts: Pick<PasswordRecord, 'id' | 'origin' | 'username'>[];
  } | null;
}
export type ImportKind = 'passwords' | 'cookies' | 'history';
export interface ImportStats {
  read?: number;
  ready?: number;
  imported?: number;
  skipped: number;
  failed: number;
  conflicts?: number;
  reasons: Record<string, number>;
}
export interface ChromePreview {
  id: string;
  stats: Record<ImportKind, ImportStats>;
  conflicts: {
    id: string;
    origin: string;
    username: string;
    hasExisting: boolean;
    candidates: { id: string; sourceURL: string; modified: number }[];
  }[];
}
export interface CredentialResponse {
  records?: PasswordRecord[];
  settings?: PasswordSettings;
  password?: string;
  sources?: { id: string; name: string }[];
  preview?: ChromePreview;
  report?: { stats: Record<ImportKind, ImportStats> };
}
export interface CredentialRequest {
  action: string;
  id?: string;
  tabId?: string;
  fillId?: string;
  value?: unknown;
}
