// @ts-nocheck
import { debugLog } from '../common';
import { usePolicyStore } from '../../../stores/policy';
import { useModelStore } from '../../../stores/model';
import { usePersonalizationStore } from '../../../stores/personalization';
import { useTutorialStore } from '../../../stores/tutorial';
import { renderMarkdown as renderMarkdownHelper } from '../../../composables/useMarkdownRenderer';
import { scrollToBottom as scrollToBottomHelper, conditionalScrollToBottom as conditionalScrollToBottomHelper, scrollThinkingToBottom as scrollThinkingToBottomHelper } from '../../../composables/useScrollControl';
import { startResize as startPanelResize, handleResize as handlePanelResize, stopResize as stopPanelResize } from '../../../composables/usePanelResize';
import {
  SUB_AGENT_DONE_PREFIX_RE,
  BG_RUN_COMMAND_DONE_PREFIX_RE,
  userMDebug,
  UI_BOUNCE_TRACE_MAX,
  uiBounceTraceLastTsByKey,
  isUiBounceTraceEnabled,
  uiBounceTrace,
  isConnectionDiagEnabled,
  pushConnectionDiagRecord,
  connectionDiag,
  parseSubAgentDoneLabel,
  parseBackgroundRunCommandDoneLabel,
  parseSystemNoticeLabel,
} from './shared';

export const terminalMethods = {
  handleRealtimeTerminalClick() {
    if (!this.isConnected) {
      return;
    }
    if (this.isPolicyBlocked('block_realtime_terminal', '实时终端已被管理员禁用')) {
      return;
    }
    this.toggleTerminalPanel();
  },
  subscribeTerminalEvents() {
    const socket = this.socket;
    if (!socket) return;
    socket.emit('terminal_subscribe', { all: true, conversation_id: this.currentConversationId || undefined });
  },
  setTerminalSessions(sessions: Record<string, { working_dir?: string; shell?: string }>) {
    this.terminalSessions = sessions;
  },
  setTerminalActiveSession(session: string) {
    this.terminalActiveSession = session;
  },
  switchTerminalSession(name: string) {
    this.terminalActiveSession = name;
    if (this.socket) {
      this.socket.emit('get_terminal_output', { session: name, lines: 0, conversation_id: this.currentConversationId || undefined });
    }
  },
  async fetchTerminalCount() {
    try {
      const cid = encodeURIComponent(this.currentConversationId || '');
      const res = await fetch(`/api/terminals?conversation_id=${cid}`);
      if (!res.ok) return;
      const data = await res.json();
      if (data?.sessions) {
        const sessions: Record<string, any> = {};
        for (const s of data.sessions) {
          const name = s.session_name || s.name || s.session || s.id;
          if (name) sessions[name] = {};
        }
        this.terminalSessions = sessions;
        // 自动打开/关闭终端面板（仅在数量变化时触发一次）
        const personalizationStore = usePersonalizationStore();
        const autoOpen = personalizationStore.form.auto_open_terminal_panel !== false;
        const count = Object.keys(sessions).length;
        const prevCount = this.previousTerminalCount ?? 0;
        if (autoOpen) {
          // 终端从无到有 → 如果面板没开，打开一次
          if (prevCount === 0 && count > 0 && !this.terminalPanelOpen) {
            this.toggleTerminalPanel();
          }
          // 终端从有到无 → 如果面板开着，关闭一次
          else if (prevCount > 0 && count === 0 && this.terminalPanelOpen) {
            this.closeTerminalPanel();
          }
        }
        this.previousTerminalCount = count;
      }
    } catch {}
  }
};
