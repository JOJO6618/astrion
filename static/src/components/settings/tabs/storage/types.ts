/** /api/storage-stats 载荷（后端 modules/storage_stats + server/storage_stats.py） */

export interface StorageCategory {
  id: string;
  bytes: number;
  files: number;
}

export interface BackupEntry {
  id: string;
  bytes: number;
  updated_at?: string | null;
}

export interface StorageData {
  available: boolean;
  reason?: string;
  platform?: string | null;
  root?: string;
  logs_root?: string;
  scanned_at?: string | null;
  duration_ms: number;
  total_bytes: number;
  categories: StorageCategory[];
  backups: BackupEntry[];
  scanning: boolean;
}

export interface CleanFailure {
  path: string;
  error: string;
}

export interface CleanLogsResult {
  freed_bytes: number;
  removed: number;
  truncated: number;
  failed: CleanFailure[];
}

export interface CleanBackupsResult {
  freed_bytes: number;
  removed_count: number;
  removed: string[];
  failed: CleanFailure[];
  keep_days: number;
}
