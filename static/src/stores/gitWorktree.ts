import { defineStore } from 'pinia';
import { workspaceScopedFetch as fetch } from '@/app/methods/workspaceRequestScope';

export type WorktreeTarget =
  | { action: 'create'; branch_name: string; start_ref: string; include_uncommitted: boolean }
  | { action: 'enter'; branch_name: string };
interface Tree {
  branch: string;
  directory: string;
  is_main: boolean;
  current: boolean;
  unavailable?: boolean;
}

export const useGitWorktreeStore = defineStore('gitWorktree', {
  state: () => ({
    enabled: false,
    directory: '',
    branch: '',
    active: false,
    scope: '',
    generation: 0,
    busy: false,
    error: '',
    revision: 0,
    worktrees: [] as Tree[],
    refs: [] as string[],
    draftTarget: null as WorktreeTarget | null
  }),
  actions: {
    hydrate(meta: Record<string, any>, cid: string | null, workspaceId: string) {
      this.generation++;
      this.scope = `${workspaceId}:${cid || 'new'}`;
      this.enabled = meta.git_worktree_enabled === true;
      this.directory = this.enabled ? meta.git_worktree?.directory || '' : '';
      this.branch = this.enabled ? meta.git_worktree?.branch || '' : '';
      this.active = this.enabled && meta.git_worktree?.active === true;
      this.draftTarget = null;
      this.worktrees = [];
      this.refs = [];
      this.busy = false;
      this.error = '';
    },
    async refresh(cid: string | null, workspaceId: string) {
      const scope = `${workspaceId}:${cid || 'new'}`;
      if (scope !== this.scope || !this.enabled || this.busy) return;
      const generation = ++this.generation;
      const query = cid ? `?conversation_id=${encodeURIComponent(cid)}` : '';
      try {
        const response = await fetch(`/api/git-worktree${query}`, {
          headers: { 'X-Astrion-Workspace-Id': workspaceId }
        });
        const data = await response.json();
        if (this.scope !== scope || generation !== this.generation) return;
        if (!response.ok || !data.success) throw new Error(data.error || 'Git worktree');
        this.worktrees = data.data.worktrees || [];
        this.refs = data.data.refs || [];
        this.error = data.data.unavailable || '';
        if (cid || !this.draftTarget) {
          this.directory = data.data.directory || '';
          this.branch = data.data.branch || '';
          this.active = data.data.active === true;
        }
      } catch (error) {
        if (this.scope === scope && generation === this.generation)
          this.error = String((error as Error).message);
      }
    },
    async select(target: WorktreeTarget | null, cid: string | null, workspaceId: string) {
      if (this.busy || !this.enabled || this.scope !== `${workspaceId}:${cid || 'new'}`)
        return false;
      this.error = '';
      if (!cid) {
        this.draftTarget = target;
        this.branch =
          target?.branch_name || this.worktrees.find((tree) => tree.is_main)?.branch || '';
        this.active = target !== null;
        this.revision++;
        return true;
      }
      const scope = this.scope;
      const generation = ++this.generation;
      this.busy = true;
      try {
        const response = await fetch('/api/git-worktree', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Astrion-Workspace-Id': workspaceId },
          body: JSON.stringify({ conversation_id: cid, action: target ? 'enter' : 'exit', target })
        });
        const data = await response.json();
        if (this.scope !== scope || generation !== this.generation) return false;
        if (!response.ok || !data.success) throw new Error(data.error || 'Git worktree');
        this.directory = data.state.directory;
        this.branch = data.state.branch;
        this.active = data.state.active === true;
        this.revision++;
        return true;
      } catch (error) {
        if (this.scope === scope && generation === this.generation)
          this.error = String((error as Error).message);
        return false;
      } finally {
        if (this.scope === scope && generation === this.generation) this.busy = false;
      }
    }
  }
});
