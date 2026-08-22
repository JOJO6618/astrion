import { defineStore } from 'pinia';
import { useConversationStore } from './conversation';

interface SubAgent {
  task_id: string;
  agent_id?: string | number;
  status?: string;
  summary?: string;
  last_tool?: string;
  conversation_id?: string;
  notice_pending?: boolean;
  display_name?: string;
  current_context_tokens?: number;
  created_at?: number | string;
}

interface SubAgentActivityEntry {
  id?: string;
  tool?: string;
  status?: string;
  args?: Record<string, any>;
  ts?: number;
  error?: string;
}

interface SubAgentState {
  subAgents: SubAgent[];
  activityTimer: ReturnType<typeof setInterval> | null;
  activeAgent: SubAgent | null;
  /** QuickDock 静默查看的 task_id（不设 activeAgent，避免弹出旧进度窗口） */
  silentActivityTaskId: string | null;
  stoppingTaskIds: Record<string, boolean>;
  activityEntries: SubAgentActivityEntry[];
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
    activityEntries: [],
    activityLoading: false,
    activityError: null
  }),
  actions: {
    async fetchSubAgents() {
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
        let resp;
        if (conversationStore.multiAgentMode) {
          resp = await fetch(`/api/multiagent/active_sub_agents?conversation_id=${encodeURIComponent(convId)}`);
        } else {
          // 必须携带 conversation_id：后端按 terminal 的当前对话过滤，无参数时落到
          // 工作区级服务 terminal，其 current_conversation_id 与用户查看的对话不一致。
          resp = await fetch(`/api/sub_agents?conversation_id=${encodeURIComponent(convId)}`);
        }
        if (!resp.ok) {
          throw new Error(await resp.text());
        }
        const data = await resp.json();
        if (data.success) {
          const agents = conversationStore.multiAgentMode
            ? (Array.isArray(data.agents) ? data.agents : [])
            : (Array.isArray(data.data) ? data.data : []);
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
        console.error('获取子智能体列表失败:', error);
      }
    },
    openSubAgent(agent: SubAgent, options?: { silent?: boolean }) {
      if (!agent || !agent.task_id) {
        return;
      }
      if (options?.silent) {
        this.silentActivityTaskId = agent.task_id;
      } else {
        this.activeAgent = agent;
        this.silentActivityTaskId = null;
      }
      this.activityEntries = [];
      this.activityError = null;
      this.fetchSubAgentActivity(agent.task_id);
      this.startActivityPolling();
    },
    closeSubAgent() {
      this.stopActivityPolling();
      this.activeAgent = null;
      this.silentActivityTaskId = null;
      this.activityEntries = [];
      this.activityError = null;
      this.activityLoading = false;
    },
    startActivityPolling() {
      if (this.activityTimer) {
        return;
      }
      this.activityTimer = setInterval(() => {
        const taskId = this.activeAgent?.task_id || this.silentActivityTaskId;
        if (taskId) {
          this.fetchSubAgentActivity(taskId);
        }
      }, 2000);
    },
    stopActivityPolling() {
      if (this.activityTimer) {
        clearInterval(this.activityTimer);
        this.activityTimer = null;
      }
    },
    async fetchSubAgentActivity(taskId: string) {
      if (!taskId) return;
      this.activityLoading = true;
      try {
        // 携带 conversation_id：子智能体跑在对话级 terminal，服务 terminal 的
        // sub_agent_manager.tasks 里查不到该任务（404 → 前端永远「暂无进度」）。
        const convId = useConversationStore().currentConversationId;
        const query = convId ? `&conversation_id=${encodeURIComponent(convId)}` : '';
        const resp = await fetch(`/api/sub_agents/${taskId}/activity?limit=100000${query}`);
        if (!resp.ok) {
          throw new Error(await resp.text());
        }
        const data = await resp.json();
        if (data && data.success && data.data) {
          const entries = Array.isArray(data.data.entries) ? data.data.entries : [];
          this.activityEntries = entries;
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
          if (TERMINAL_STATUSES.has(status)) {
            this.stopActivityPolling();
          }
        }
      } catch (error: any) {
        this.activityError = error?.message || String(error);
        console.error('获取子智能体活动失败:', error);
      } finally {
        this.activityLoading = false;
      }
    },
    async terminateSubAgent(taskId: string) {
      const normalizedId = (taskId || '').toString().trim();
      if (!normalizedId) {
        return { success: false, error: 'task_id 不能为空' };
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
        const resp = await fetch(`/api/sub_agents/${encodeURIComponent(normalizedId)}/terminate${query}`, {
          method: 'POST'
        });
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
    async stopAllAgents(mode: 'terminate' | 'soft_stop'): Promise<{ success: boolean; stoppedCount?: number; error?: string }> {
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
