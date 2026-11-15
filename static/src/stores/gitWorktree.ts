import { defineStore } from 'pinia';
import { workspaceScopedFetch as fetch } from '@/app/methods/workspaceRequestScope';

export type WorktreeTarget =
  | { action: 'create'; branch_name: string; start_ref: string; include_uncommitted: boolean }
  | { action: 'enter'; branch_name: string }
  | { action: 'enter'; directory: string };
interface Tree {
  branch: string;
  directory: string;
  is_main: boolean;
  current: boolean;
  unavailable?: boolean;
  /** detached 工作树没有分支名，用 HEAD 标识。 */
  head?: string;
  detached?: boolean;
  inside_workspace?: boolean;
  authorized?: boolean;
}

export const useGitWorktreeStore = defineStore('gitWorktree', {
  state: () => ({
    enabled: false,
    directory: '',
    branch: '',
    head: '',
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
    /** 未创建对话时，用已选目标反推展示用的目录／分支／HEAD。 */
    applyDraftDisplay(hint?: { branch?: string; head?: string }) {
      const target = this.draftTarget;
      this.active = target !== null;
      if (!target) {
        this.branch = '';
        this.head = '';
        this.directory = '';
        return;
      }
      if ('branch_name' in target) {
        const tree = this.worktrees.find((item) => item.branch === target.branch_name);
        this.branch = target.branch_name;
        this.directory = tree?.directory || '';
        this.head = tree?.head || '';
        return;
      }
      const tree = this.worktrees.find((item) => item.directory === target.directory);
      this.directory = target.directory;
      // 手输路径可能带符号链接（/tmp ←→ /private/tmp），以预检结果为准。
      this.branch = hint?.branch ?? tree?.branch ?? '';
      this.head = hint?.head ?? tree?.head ?? '';
    },
    hydrate(meta: Record<string, any>, cid: string | null, workspaceId: string) {
      this.generation++;
      this.scope = `${workspaceId}:${cid || 'new'}`;
      this.enabled = meta.git_worktree_enabled === true;
      this.directory = this.enabled ? meta.git_worktree?.directory || '' : '';
      this.branch = this.enabled ? meta.git_worktree?.branch || '' : '';
      this.head = this.enabled ? meta.git_worktree?.head || '' : '';
      this.active = this.enabled && meta.git_worktree?.active === true;
      // 新建页的目标是本地草稿，模式/模型等设置刷新不能把它清掉；已有对话以服务端为准。
      if (cid !== null) this.draftTarget = null;
      this.worktrees = [];
      this.refs = [];
      this.busy = false;
      this.error = '';
      if (cid === null && this.draftTarget) this.applyDraftDisplay();
    },
    async refresh(cid: string | null, workspaceId: string) {
      const scope = `${workspaceId}:${cid || 'new'}`;
      if (!this.enabled || this.busy) return;
      // 切换工作树/刷新列表的一方（输入栏弹窗）拿着真实对话身份，比可能滞后的 hydrate 更可靠。
      if (scope !== this.scope) this.scope = scope;
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
        if (!cid && this.draftTarget) {
          this.applyDraftDisplay();
        } else if (cid || !this.draftTarget) {
          this.directory = data.data.directory || '';
          this.branch = data.data.branch || '';
          this.head = data.data.head || '';
          this.active = data.data.active === true;
        }
      } catch (error) {
        if (this.scope === scope && generation === this.generation)
          this.error = String((error as Error).message);
      }
    },
    async select(
      target: WorktreeTarget | null,
      cid: string | null,
      workspaceId: string,
      hint?: { branch?: string; head?: string }
    ) {
      if (this.busy || !this.enabled) return false;
      const scope = `${workspaceId}:${cid || 'new'}`;
      // 同上：对话身份由发起切换的弹窗决定，避免 hydrate 竞态让切换静默失效。
      if (scope !== this.scope) this.scope = scope;
      this.error = '';
      if (!cid) {
        this.draftTarget = target;
        this.applyDraftDisplay(hint);
        this.revision++;
        return true;
      }
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
        this.head = data.state.head || '';
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
