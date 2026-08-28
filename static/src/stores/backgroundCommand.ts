import { defineStore } from 'pinia';
import { t } from '@/locales';
import { useConversationStore } from './conversation';

interface BackgroundCommand {
  command_id: string;
  status?: string;
  command?: string;
  conversation_id?: string;
  notice_pending?: boolean;
  created_at?: number;
  updated_at?: number;
  finished_at?: number | null;
  timeout?: number;
  return_code?: number | null;
}

interface BackgroundCommandDetail extends BackgroundCommand {
  output?: string;
  message?: string;
}

interface BackgroundCommandState {
  commands: BackgroundCommand[];
  detailPollTimer: ReturnType<typeof setInterval> | null;
  activeCommand: BackgroundCommand | null;
  /** QuickDock 静默查看的 command_id：拉详情/轮询但不设 activeCommand（避免弹出旧详情窗口） */
  silentDetailCommandId: string | null;
  stoppingCommandIds: Record<string, boolean>;
  activeDetail: BackgroundCommandDetail | null;
  detailLoading: boolean;
  detailError: string | null;
}

/** 必须携带 conversation_id：with_terminal 无会话参数时会落到工作区级服务 terminal，
 *  其 manager / current_conversation_id 与对话级 terminal 不一致，会查不到数据。 */
function convQuery(): string {
  const convId = useConversationStore().currentConversationId;
  return convId ? `conversation_id=${encodeURIComponent(convId)}` : '';
}

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'timeout', 'cancelled']);

export const useBackgroundCommandStore = defineStore('backgroundCommand', {
  state: (): BackgroundCommandState => ({
    commands: [],
    detailPollTimer: null,
    activeCommand: null,
    silentDetailCommandId: null,
    stoppingCommandIds: {},
    activeDetail: null,
    detailLoading: false,
    detailError: null
  }),
  actions: {
    async fetchCommands() {
      try {
        // /new 等无对话场景：不请求后端，直接置空（服务 terminal 可能命中其他对话的记录）
        const convId = useConversationStore().currentConversationId;
        if (!convId) {
          this.commands = [];
          return;
        }
        const query = convQuery();
        // 同步剔除不属于当前对话的残留记录：切对话/工作区后旧对话数据
        // 会先渲染一拍（「闪一下」）再被 fetch 覆盖清空，造成“指令被停了”的误解。
        // 属于当前对话的记录保留，避免布局「先收起再展开」闪烁。
        this.commands = this.commands.filter(
          (item) => !item.conversation_id || item.conversation_id === convId
        );
        const resp = await fetch(
          `/api/background_commands?limit=200${query ? `&${query}` : ''}`
        );
        if (!resp.ok) {
          throw new Error(await resp.text());
        }
        const data = await resp.json();
        if (data.success) {
          const commands: BackgroundCommand[] = Array.isArray(data.data) ? data.data : [];
          // 统一按创建时间升序（最新在末尾）：后端返回倒序，
          // 快捷窗口设计要求最新条目出现在列表底部（与待办/子智能体/文件一致）。
          commands.sort((a, b) => (Number(a?.created_at) || 0) - (Number(b?.created_at) || 0));
          this.commands = commands;
          const activeId = this.activeCommand?.command_id;
          if (activeId) {
            const latest = this.commands.find((item) => item.command_id === activeId);
            if (latest) {
              this.activeCommand = { ...latest };
            }
          }
        }
      } catch (error) {
        console.error('获取后台指令列表失败:', error);
      }
    },
    openCommand(command: BackgroundCommand, options?: { silent?: boolean }) {
      if (!command || !command.command_id) {
        return;
      }
      if (options?.silent) {
        this.silentDetailCommandId = command.command_id;
      } else {
        this.activeCommand = command;
        this.silentDetailCommandId = null;
      }
      this.activeDetail = null;
      this.detailError = null;
      this.fetchCommandDetail(command.command_id);
      this.startDetailPolling();
    },
    closeCommand() {
      this.stopDetailPolling();
      this.activeCommand = null;
      this.silentDetailCommandId = null;
      this.activeDetail = null;
      this.detailError = null;
      this.detailLoading = false;
    },
    startDetailPolling() {
      if (this.detailPollTimer) {
        return;
      }
      this.detailPollTimer = setInterval(() => {
        const commandId = this.activeCommand?.command_id || this.silentDetailCommandId;
        if (commandId) {
          this.fetchCommandDetail(commandId);
        }
      }, 2000);
    },
    stopDetailPolling() {
      if (this.detailPollTimer) {
        clearInterval(this.detailPollTimer);
        this.detailPollTimer = null;
      }
    },
    async fetchCommandDetail(commandId: string) {
      if (!commandId) return;
      this.detailLoading = true;
      try {
        const query = convQuery();
        const resp = await fetch(
          `/api/background_commands/${encodeURIComponent(commandId)}${query ? `?${query}` : ''}`
        );
        if (!resp.ok) {
          throw new Error(await resp.text());
        }
        const data = await resp.json();
        if (data && data.success && data.data) {
          this.activeDetail = data.data;
          const current = this.commands.find((item) => item.command_id === commandId);
          if (current) {
            Object.assign(current, data.data);
          }
          if (this.activeCommand && this.activeCommand.command_id === commandId) {
            this.activeCommand = {
              ...this.activeCommand,
              ...data.data
            };
          }
          const status = (data.data.status || '').toString();
          if (TERMINAL_STATUSES.has(status)) {
            this.stopDetailPolling();
          }
        }
      } catch (error: any) {
        this.detailError = error?.message || String(error);
        console.error('获取后台指令详情失败:', error);
      } finally {
        this.detailLoading = false;
      }
    },
    async cancelCommand(commandId: string) {
      const normalizedId = (commandId || '').toString().trim();
      if (!normalizedId) {
        return { success: false, error: t('stores.commandIdRequired') };
      }
      this.stoppingCommandIds = {
        ...this.stoppingCommandIds,
        [normalizedId]: true
      };
      try {
        const query = convQuery();
        const resp = await fetch(
          `/api/background_commands/${encodeURIComponent(normalizedId)}/cancel${query ? `?${query}` : ''}`,
          { method: 'POST' }
        );
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || !data?.success) {
          throw new Error(data?.error || `HTTP ${resp.status}`);
        }
        await this.fetchCommands();
        if (this.activeCommand?.command_id === normalizedId) {
          await this.fetchCommandDetail(normalizedId);
        }
        return { success: true, data: data?.data || null };
      } catch (error: any) {
        const message = error?.message || String(error);
        return { success: false, error: message };
      } finally {
        const next = { ...this.stoppingCommandIds };
        delete next[normalizedId];
        this.stoppingCommandIds = next;
      }
    },
    async stopAllCommands(conversationId?: string): Promise<{ success: boolean; stoppedCount?: number; error?: string }> {
      try {
        const resp = await fetch('/api/background_commands/stop_all', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(conversationId ? { conversation_id: conversationId } : {})
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || !data?.success) {
          throw new Error(data?.error || `HTTP ${resp.status}`);
        }
        await this.fetchCommands();
        return { success: true, stoppedCount: data?.data?.stopped_count || 0 };
      } catch (error: any) {
        return { success: false, error: error?.message || String(error) };
      }
    }
  }
});
