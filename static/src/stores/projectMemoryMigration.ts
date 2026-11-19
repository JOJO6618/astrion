import { defineStore } from 'pinia';

const endpoint = '/api/project-memory-migration';

export interface MigrationError {
  workspace: string;
  reason: string;
}

interface MigrationOverview {
  mode: 'host' | 'docker';
  data_root: string | null;
  users: number;
  total: number;
  workspaces: Array<{ label: string; target: string; count: number }>;
  errors: MigrationError[];
}

interface MigrationJob {
  id: string;
  phase: 'copying' | 'verifying' | 'done' | 'failed';
  current: string;
  total: number;
  copied: number;
  verified: number;
  migrated: number;
  completed_workspaces: number;
  errors: MigrationError[];
  conflicts: Array<{ workspace: string; source: string; target: string }>;
}

interface MigrationResponse {
  overview?: MigrationOverview;
  job?: MigrationJob;
  error?: string;
}

async function requestMigration(url = endpoint, method = 'GET'): Promise<MigrationResponse> {
  const response = await fetch(url, { method, credentials: 'same-origin' });
  const payload: MigrationResponse = await response.json();
  if (!response.ok) throw new Error(payload.error || '');
  return payload;
}

export const useProjectMemoryMigrationStore = defineStore('projectMemoryMigration', {
  state: () => ({
    open: false,
    loading: false,
    startupChecked: false,
    overview: null as MigrationOverview | null,
    job: null as MigrationJob | null,
    error: null as string | null
  }),
  getters: {
    running(state) {
      return state.job?.phase === 'copying' || state.job?.phase === 'verifying';
    }
  },
  actions: {
    async checkStartup() {
      if (this.startupChecked || this.open) return;
      this.startupChecked = true;
      try {
        const { overview } = await requestMigration(`${endpoint}/startup`, 'POST');
        if (!this.open && overview?.total) {
          this.overview = overview;
          this.open = true;
        }
      } catch {
        this.startupChecked = false;
      }
    },
    async show() {
      if (this.open) return;
      this.open = true;
      this.overview = null;
      this.job = null;
      await this.load();
    },
    dismiss() {
      if (!this.running && !this.loading) this.open = false;
    },
    async load(method = 'GET') {
      if (this.loading) return;
      this.loading = true;
      this.error = null;
      try {
        const { overview, job } = await requestMigration(endpoint, method);
        this.overview = overview ?? null;
        this.job = job ?? null;
        if (job && this.running) void this.poll(job.id);
      } catch (error) {
        this.error = error instanceof Error ? error.message : '';
      } finally {
        this.loading = false;
      }
    },
    async poll(id: string) {
      while (this.job?.id === id && this.running) {
        await new Promise<void>((resolve) => window.setTimeout(resolve, 450));
        if (this.job?.id !== id) return;
        try {
          const { job } = await requestMigration(`${endpoint}?job=${encodeURIComponent(id)}`);
          if (this.job?.id !== id) return;
          this.job = job ?? null;
        } catch (error) {
          this.error = error instanceof Error ? error.message : '';
          this.job = null;
        }
      }
    }
  }
});
