import {
  beginAuxiliaryStoreRequest,
  invalidateAuxiliaryRequest
} from '../app/methods/auxiliaryOwnership';
import { defineStore } from 'pinia';
import { t } from '@/locales';
import { useConversationStore } from './conversation';
import { workspaceScopedFetch as fetch } from '../app/methods/workspaceRequestScope';

interface SubAgent {
  task_id: string;
  agent_id?: string | number;
  status?: string;
  summary?: string;
  last_tool?: string;
  conversation_id?: string;
  notice_pending?: boolean;
  display_name?: string;
  name?: string;
  working_dir?: string;
  current_context_tokens?: number;
  created_at?: number | string;
}

interface SubAgentActivityEntry {
  activityKey: string;
  id?: string;
  tool?: string;
  status?: string;
  args?: Record<string, any>;
  ts?: number;
  error?: string;
}

interface ActivityPage {
  start: number;
  entries: SubAgentActivityEntry[];
}

const PAGE_SIZE = 200;
/** 详情面板可见条目上限：实时页最多两页，兼容旧后端一次性返回全量时也只保留尾部一页。 */
const VISIBLE_ENTRY_LIMIT = PAGE_SIZE * 2;
const activityRequests = new WeakMap<object, AbortController>();
type ActivityDirection = 'live' | 'older';

interface SubAgentState {
  subAgents: SubAgent[];
  activityTimer: ReturnType<typeof setInterval> | null;
  activeAgent: SubAgent | null;
  /** QuickDock 静默查看的 task_id（不设 activeAgent，避免弹出旧进度窗口） */
  silentActivityTaskId: string | null;
  stoppingTaskIds: Record<string, boolean>;
  livePages: ActivityPage[];
  liveOffset: number | null;
  readingHistory: boolean;
  activityLoading: boolean;
  activityError: string | null;
}

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'timeout', 'terminated']);

export const useSubAgentStore = defineStore('subAgent', {
  state: (): SubAgentState => ({
    subAgents: [],
    activityTimer: null,
    activeAgent: null,
    /** QuickDock 静默查看的 task_id：拉 activity/轮询但不设 activeAgent（避免弹出旧进度窗口） */
    silentActivityTaskId: null,
    stoppingTaskIds: {},
    livePages: [],
    liveOffset: null,
    readingHistory: false,
    activityLoading: false,
    activityError: null
  }),
  getters: {
    activityEntries(state): SubAgentActivityEntry[] {
      return state.livePages.flatMap((page) => page.entries);
    },
    activityStart(state): number {
      return state.livePages[0]?.start ?? 0;
    }
  },
  actions: {
    async fetchSubAgents() {
      const owns = beginAuxiliaryStoreRequest(this, 'sub-agents');
      try {
        const conversationStore = useConversationStore();
        const convId = conversationStore.currentConversationId;
        // /new 等无对话场景：不请求后端，直接置空。
        // 否则后端服务 terminal 的「当前对话」可能是最近加载的对话（或命中全局
        // running 兜底），把其他对话的子智能体显示到新对话页。
        if (!convId) {
          this.subAgents = [];
          return;
        }
        // 同步剔除不属于当前对话的残留记录：切对话/工作区后旧对话数据
        // 会先渲染一拍再被 fetch 覆盖，造成“任务被停了”的误解。
        // 属于当前对话的记录保留，避免布局「先收起再展开」闪烁。
        this.subAgents = this.subAgents.filter(
          (item) => !item.conversation_id || item.conversation_id === convId
        );
        const resp = await fetch(`/api/sub_agents?conversation_id=${encodeURIComponent(convId)}`);
        if (!resp.ok) {
          throw new Error(await resp.text());
        }
        const data = await resp.json();
        if (!owns()) return;
        if (data.success) {
          const agents = Array.isArray(data.data) ? data.data : [];
          // 统一按创建时间升序（最新在末尾）：传统模式后端返回倒序，
          // 快捷窗口设计要求最新条目出现在列表底部（与待办/后台指令/文件一致）。
          agents.sort(
            (a: SubAgent, b: SubAgent) =>
              (Number(a?.created_at) || 0) - (Number(b?.created_at) || 0)
          );
          this.subAgents = agents;
          const activeTaskId = this.activeAgent?.task_id;
          if (activeTaskId) {
            const latest = this.subAgents.find((item) => item.task_id === activeTaskId);
            if (latest) {
              this.activeAgent = { ...latest };
            }
          }
        }
      } catch (error) {
        if (!owns()) return;
        console.error('获取子智能体列表失败:', error);
      }
    },
    openSubAgent(agent: SubAgent, options?: { silent?: boolean }) {
      if (!agent || !agent.task_id) {
        return;
      }
      this.closeSubAgent();
      if (options?.silent) {
        this.silentActivityTaskId = agent.task_id;
      } else {
        this.activeAgent = agent;
        this.silentActivityTaskId = null;
      }
      void this.fetchSubAgentActivity(agent.task_id);
    },
    closeSubAgent() {
      this.stopActivityPolling();
      this.activeAgent = null;
      this.silentActivityTaskId = null;
      this.livePages = [];
      this.liveOffset = null;
      this.readingHistory = false;
      this.activityError = null;
      this.activityLoading = false;
    },
    stopActivityPolling() {
      activityRequests.get(this)?.abort();
      activityRequests.delete(this);
      invalidateAuxiliaryRequest(this, 'sub-activity');
      this.activityLoading = false;
      if (this.activityTimer) {
        clearTimeout(this.activityTimer);
        this.activityTimer = null;
      }
    },
    async fetchSubAgentActivity(taskId: string, direction: ActivityDirection = 'live') {
      const selected = () => (this.activeAgent?.task_id || this.silentActivityTaskId) === taskId;
      if (!taskId || !selected() || activityRequests.has(this)) return;
      if (direction === 'older' && !this.activityStart) return;
      if (this.activityTimer) clearTimeout(this.activityTimer);
      this.activityTimer = null;
      const controller = new AbortController();
      activityRequests.set(this, controller);
      const owns = beginAuxiliaryStoreRequest(this, 'sub-activity', undefined, selected);
      const cursor = direction === 'older' ? this.activityStart : this.liveOffset;
      this.activityLoading = true;
      let delay = 2000;
      try {
        // 携带 conversation_id：子智能体跑在对话级 terminal，服务 terminal 的
        // sub_agent_manager.tasks 里查不到该任务（404 → 前端永远「暂无进度」）。
        const convId = useConversationStore().currentConversationId;
        const params = new URLSearchParams();
        if (convId) params.set('conversation_id', convId);
        params.set('limit', String(PAGE_SIZE));
        if (cursor !== null)
          params.set(direction === 'older' ? 'before' : 'offset', String(cursor));
        const resp = await fetch(
          `/api/sub_agents/${encodeURIComponent(taskId)}/activity?${params}`,
          {
            signal: controller.signal
          }
        );
        if (!resp.ok) {
          throw new Error(await resp.text());
        }
        const data = await resp.json();
        if (!owns()) return;
        if (data && data.success && data.data) {
          const received = Array.isArray(data.data.entries) ? data.data.entries : [];
          const end = data.data.next_offset;
          const safeEnd = Number.isSafeInteger(end);
          // 旧后端响应没有 start_offset，也不支持 offset/before 游标。它即使忽略
          // 游标仍会按 limit 返回尾部页，因此每次实时轮询都用尾部页替换而不是追加，
          // 避免同一批旧数据每 2 秒重复堆进 livePages。
          const legacyPayload = !Object.prototype.hasOwnProperty.call(data.data, 'start_offset');
          const start = Number(data.data.start_offset ?? (legacyPayload ? 0 : (cursor ?? 0)));
          // 单页响应一律只保留尾部 PAGE_SIZE 条。新后端按 200 分页时等价于原数组；
          // 旧后端/异常返回一次给出 20 万条时，只保留最新 200 条，DOM 不再无界。
          const entries = received.length > PAGE_SIZE ? received.slice(-PAGE_SIZE) : received;
          const page = {
            start,
            entries: entries.map((entry: SubAgentActivityEntry, index: number) => ({
              ...entry,
              activityKey: `${start}:${index}`
            }))
          };
          if (data.data.reset === true || cursor === null || !safeEnd) {
            this.livePages = [page];
            this.liveOffset = safeEnd ? end : null;
            this.readingHistory = false;
          } else if (direction === 'live') {
            if (legacyPayload) {
              this.livePages = [page];
            } else if (entries.length && end > (this.liveOffset ?? -1)) {
              this.livePages.push(page);
              if (!this.readingHistory) {
                let count = this.livePages.reduce((n, p) => n + p.entries.length, 0);
                while (count > VISIBLE_ENTRY_LIMIT) count -= this.livePages.shift()!.entries.length;
              }
            }
            this.liveOffset = end;
          } else if (start < cursor && end === cursor) {
            this.readingHistory = true;
            this.livePages.unshift(page);
          }
          this.activityError = null;
          if (direction === 'live' && data.data.has_more_after && end > (cursor ?? 0)) delay = 0;
          if (this.activeAgent && this.activeAgent.task_id === taskId) {
            this.activeAgent = {
              ...this.activeAgent,
              status: data.data.status || this.activeAgent.status
            };
          }
          // 静默查看时同步列表项状态，保证 QuickDock 详情面板的状态角标及时更新
          const listItem = this.subAgents.find((item) => item.task_id === taskId);
          if (listItem && data.data.status) {
            listItem.status = data.data.status;
          }
          const status = (data.data.status || '').toString();
          if (delay && (TERMINAL_STATUSES.has(status) || status === 'idle')) {
            delay = 5000;
          }
        }
      } catch (error: any) {
        if (!owns()) return;
        this.activityError = error?.message || String(error);
        console.error('获取子智能体活动失败:', error);
      } finally {
        if (activityRequests.get(this) === controller) {
          activityRequests.delete(this);
          if (owns()) {
            this.activityLoading = false;
            this.activityTimer = setTimeout(() => void this.fetchSubAgentActivity(taskId), delay);
          }
        }
      }
    },
    async terminateSubAgent(taskId: string) {
      const normalizedId = (taskId || '').toString().trim();
      if (!normalizedId) {
        return { success: false, error: t('stores.taskIdRequired') };
      }
      this.stoppingTaskIds = {
        ...this.stoppingTaskIds,
        [normalizedId]: true
      };
      try {
        // 必须携带 conversation_id：with_terminal 无会话参数时会落到工作区级服务
        // terminal，其 current_conversation_id 可能是别的对话，导致后端 403
        const conversationStore = useConversationStore();
        const convId = conversationStore.currentConversationId;
        const query = convId ? `?conversation_id=${encodeURIComponent(convId)}` : '';
        const resp = await fetch(
          `/api/sub_agents/${encodeURIComponent(normalizedId)}/terminate${query}`,
          {
            method: 'POST'
          }
        );
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || !data?.success) {
          throw new Error(data?.error || `HTTP ${resp.status}`);
        }
        await this.fetchSubAgents();
        if (this.activeAgent?.task_id === normalizedId) {
          await this.fetchSubAgentActivity(normalizedId);
          this.activeAgent = {
            ...this.activeAgent,
            status: 'terminated'
          };
        }
        return { success: true, data: data?.data || null };
      } catch (error: any) {
        const message = error?.message || String(error);
        return { success: false, error: message };
      } finally {
        const next = { ...this.stoppingTaskIds };
        delete next[normalizedId];
        this.stoppingTaskIds = next;
      }
    },
    /** 软停止单个子智能体：取消当前轮次、保留实例与上下文（对应 stop_sub_agent，来源=用户）。
     *  后端会把“被用户取消”作为本轮结果通知主智能体。 */
    async stopSubAgent(taskId: string) {
      const normalizedId = (taskId || '').toString().trim();
      if (!normalizedId) {
        return { success: false, error: t('stores.taskIdRequired') };
      }
      this.stoppingTaskIds = { ...this.stoppingTaskIds, [normalizedId]: true };
      try {
        const conversationStore = useConversationStore();
        const convId = conversationStore.currentConversationId;
        const query = convId ? `?conversation_id=${encodeURIComponent(convId)}` : '';
        const resp = await fetch(
          `/api/sub_agents/${encodeURIComponent(normalizedId)}/stop${query}`,
          {
            method: 'POST'
          }
        );
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || !data?.success) {
          throw new Error(data?.error || `HTTP ${resp.status}`);
        }
        await this.fetchSubAgents();
        return { success: true, data: data?.data || null };
      } catch (error: any) {
        return { success: false, error: error?.message || String(error) };
      } finally {
        const next = { ...this.stoppingTaskIds };
        delete next[normalizedId];
        this.stoppingTaskIds = next;
      }
    },
    async stopAllAgents(
      mode: 'terminate' | 'soft_stop'
    ): Promise<{ success: boolean; stoppedCount?: number; error?: string }> {
      try {
        // 同 terminateSubAgent：携带 conversation_id 确保命中对话级 terminal
        const conversationStore = useConversationStore();
        const convId = conversationStore.currentConversationId;
        const query = convId ? `?conversation_id=${encodeURIComponent(convId)}` : '';
        const resp = await fetch(`/api/sub_agents/stop_all${query}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode })
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || !data?.success) {
          throw new Error(data?.error || `HTTP ${resp.status}`);
        }
        await this.fetchSubAgents();
        return { success: true, stoppedCount: data?.data?.stopped_count || 0 };
      } catch (error: any) {
        const message = error?.message || String(error);
        return { success: false, error: message };
      }
    },
    stripConversationPrefix(conversationId: string) {
      if (!conversationId) return '';
      return conversationId.startsWith('conv_') ? conversationId.slice(5) : conversationId;
    },
    getBaseUrl() {
      const override = (window as any).SUB_AGENT_BASE_URL || (window as any).__SUB_AGENT_BASE_URL__;
      if (override && typeof override === 'string') {
        return override.replace(/\/$/, '');
      }
      const { protocol, hostname } = window.location;
      if (hostname && hostname.includes('agent.')) {
        const mappedHost = hostname.replace('agent.', 'subagent.');
        return `${protocol}//${mappedHost}`;
      }
      return `${protocol}//${hostname}:8092`;
    }
  }
});
