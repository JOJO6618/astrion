import { defineStore } from 'pinia';
import { getMessageVisibility, messageStartsWork } from '@/utils/messageVisibility';

interface ScrollStatePayload {
  autoScrollEnabled?: boolean;
  userScrolling?: boolean;
}

interface ChatState {
  messages: Array<any>;
  currentMessageIndex: number;
  streamingMessage: boolean;
  expandedBlocks: Set<string>;
  autoScrollEnabled: boolean;
  userScrolling: boolean;
  thinkingScrollLocks: Map<string, boolean>;
}

const GENERATING_LABELS = [
  '正在构思…',
  '稍候，AI 正在准备',
  '准备工具中',
  '容我三思…',
  '答案马上就来',
  '灵感加载中',
  '思路拼装中',
  '琢磨最佳方案',
  '脑内开会中',
  '整理资料中',
  '润色回复中',
  '调配上下文',
  '搜刮记忆中',
  '快敲完了，别急',
  '领域展开',
  '工具链装配中…',
  '句子正在成形…',
  '让我再捋一捋…',
  '知识库检索中…'
];

const SHOW_HTML_COMPLETE_BLOCK_RE = /<show_html\b[\s\S]*?<\/show_html>/i;

function hasCompletedShowHtmlBlock(content: string | null | undefined) {
  if (!content) return false;
  return SHOW_HTML_COMPLETE_BLOCK_RE.test(content);
}

function randomGeneratingLabel() {
  if (!GENERATING_LABELS.length) {
    return '';
  }
  const index = Math.floor(Math.random() * GENERATING_LABELS.length);
  return GENERATING_LABELS[index];
}

function createAssistantMessage() {
  return {
    role: 'assistant',
    actions: [],
    streamingThinking: '',
    streamingText: '',
    currentStreamingType: null,
    activeThinkingId: null,
    awaitingFirstContent: false,
    generatingLabel: randomGeneratingLabel()
  };
}

function randomId(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function cloneSet<T>(source: Set<T>) {
  return new Set<T>(Array.from(source));
}

function cloneMap<K, V>(source: Map<K, V>) {
  return new Map<K, V>(Array.from(source.entries()));
}

function clearAwaitingFirstContent(message: any) {
  if (message && message.awaitingFirstContent) {
    message.awaitingFirstContent = false;
  }
}
const userMDebug = (...args: any[]) => {
};

export const useChatStore = defineStore('chat', {
  state: (): ChatState => ({
    messages: [],
    currentMessageIndex: -1,
    streamingMessage: false,
    expandedBlocks: new Set<string>(),
    autoScrollEnabled: true,
    userScrolling: false,
    thinkingScrollLocks: new Map<string, boolean>()
  }),
  getters: {
    isScrollLocked: (state) => state.autoScrollEnabled && !state.userScrolling
  },
  actions: {
    setStreamingMessage(active: boolean) {
      this.streamingMessage = !!active;
    },
    setCurrentMessageIndex(index: number) {
      this.currentMessageIndex = index;
    },
    setMessages(messages: Array<any>) {
      this.messages = messages;
    },
    clearMessages() {
      this.messages = [];
      this.currentMessageIndex = -1;
    },
    updateEditSummaryByMessageId(messageId: string, summary: any) {
      // 编辑摘要实时事件：按 message_id 定位发起工作的 user 消息并写入
      // metadata.edit_summary。运行中/历史 user 消息均透传后端 message_id；
      // 本地乐观插入的消息暂未携带 id 时，兜底落到当前工作段锚（最后一条
      // starts_work 的 user 消息）。
      const applyTo = (msg: any) => {
        msg.metadata = { ...(msg.metadata || {}), edit_summary: summary };
      };
      if (messageId) {
        for (let i = this.messages.length - 1; i >= 0; i -= 1) {
          const m = this.messages[i];
          if (m?.role === 'user' && m?.message_id === messageId) {
            applyTo(m);
            return;
          }
        }
      }
      for (let i = this.messages.length - 1; i >= 0; i -= 1) {
        const m = this.messages[i];
        if (m?.role === 'user' && m?.metadata?.starts_work === true) {
          applyTo(m);
          return;
        }
      }
    },
    toggleBlock(blockId: string) {
      const next = cloneSet(this.expandedBlocks);
      if (next.has(blockId)) {
        next.delete(blockId);
      } else {
        next.add(blockId);
      }
      this.expandedBlocks = next;
    },
    expandBlock(blockId: string) {
      const next = cloneSet(this.expandedBlocks);
      next.add(blockId);
      this.expandedBlocks = next;
    },
    collapseBlock(blockId: string) {
      const next = cloneSet(this.expandedBlocks);
      next.delete(blockId);
      this.expandedBlocks = next;
    },
    clearExpandedBlocks() {
      this.expandedBlocks = new Set<string>();
    },
    setThinkingLock(blockId: string, locked: boolean) {
      const next = cloneMap(this.thinkingScrollLocks);
      if (locked) {
        next.set(blockId, true);
      } else {
        next.delete(blockId);
      }
      this.thinkingScrollLocks = next;
    },
    clearThinkingLocks() {
      this.thinkingScrollLocks = new Map<string, boolean>();
    },
    setScrollState(payload: ScrollStatePayload) {
      if (typeof payload.autoScrollEnabled !== 'undefined') {
        this.autoScrollEnabled = !!payload.autoScrollEnabled;
      }
      if (typeof payload.userScrolling !== 'undefined') {
        this.userScrolling = !!payload.userScrolling;
      }
    },
    enableAutoScroll() {
      this.setScrollState({ autoScrollEnabled: true, userScrolling: false });
    },
    disableAutoScroll() {
      this.setScrollState({ autoScrollEnabled: false, userScrolling: false });
    },
    toggleScrollLockState() {
      const locked = this.isScrollLocked;
      if (locked) {
        this.disableAutoScroll();
        return false;
      }
      this.enableAutoScroll();
      return true;
    },
    ensureAssistantMessage() {
      if (this.currentMessageIndex >= 0) {
        return this.messages[this.currentMessageIndex];
      }
      const message = createAssistantMessage();
      this.messages.push(message);
      this.currentMessageIndex = this.messages.length - 1;
      return message;
    },
    addUserMessage(
      content: string,
      images: string[] = [],
      videos: string[] = [],
      mediaRefs: Array<Record<string, any>> = [],
      source: string = 'user',
      extraMetadata: Record<string, any> = {}
    ) {
      const startedAt = new Date().toISOString();
      const normalizedSource = String(source || 'user').trim().toLowerCase();
      const metadata: Record<string, any> = {
        media_refs: Array.isArray(mediaRefs) ? mediaRefs : [],
        message_source: normalizedSource,
        ...extraMetadata
      };
      metadata.visibility = getMessageVisibility({ role: 'user', content, metadata });
      metadata.starts_work = messageStartsWork({ role: 'user', content, metadata });
      const shouldTrackWorkTimer = metadata.starts_work === true;
      if (shouldTrackWorkTimer) {
        metadata.work_timer = {
          status: 'working',
          started_at: startedAt
        };
      }
      this.messages.push({
        role: 'user',
        content,
        images,
        videos,
        media_refs: Array.isArray(mediaRefs) ? mediaRefs : [],
        metadata,
        created_at: startedAt
      });
      this.currentMessageIndex = -1;
    },
    startAssistantMessage() {
      const message = createAssistantMessage();
      this.messages.push(message);
      this.currentMessageIndex = this.messages.length - 1;
      this.streamingMessage = true;
      message.awaitingFirstContent = true;

      return message;
    },
    startThinkingAction() {
      const msg = this.ensureAssistantMessage();
      clearAwaitingFirstContent(msg);
      // 幂等兜底：已有「流式中」的思考块时复用而非新建。正常流程
      // thinking_start/thinking_end 成对出现，end 会把 streaming 置 false，
      // 因此命中流式块只可能来自重复/重放事件（去重集合被清后的最后防线）。
      const existingThinking = this.getActiveThinkingAction(msg);
      if (existingThinking && existingThinking.streaming === true) {
        return { action: existingThinking, blockId: existingThinking.blockId || existingThinking.id };
      }
      msg.streamingThinking = '';
      msg.currentStreamingType = 'thinking';
      const actionId = randomId('thinking');
      const blockId = actionId;
      const action = {
        id: actionId,
        type: 'thinking',
        content: '',
        streaming: true,
        timestamp: Date.now(),
        blockId
      };
      msg.actions.push(action);
      msg.activeThinkingId = actionId;
      return { action, blockId };
    },
    appendThinkingChunk(content: string) {
      if (this.currentMessageIndex < 0) return null;
      const msg = this.messages[this.currentMessageIndex];
      msg.streamingThinking += content;
      const thinkingAction = this.getActiveThinkingAction(msg);
      if (thinkingAction) {
        thinkingAction.content += content;
        return thinkingAction;
      }
      return null;
    },
    completeThinking(fullContent: string) {
      if (this.currentMessageIndex < 0) return null;
      const msg = this.messages[this.currentMessageIndex];
      const thinkingAction = this.getActiveThinkingAction(msg);
      if (thinkingAction) {
        thinkingAction.streaming = false;
        thinkingAction.content = fullContent;
        msg.streamingThinking = '';
        msg.currentStreamingType = null;
        msg.activeThinkingId = null;
        return thinkingAction.blockId || thinkingAction.id;
      }
      return null;
    },
    startTextAction() {
      const msg = this.ensureAssistantMessage();
      if (!msg) {
        return null;
      }
      clearAwaitingFirstContent(msg);
      // 幂等兜底：末尾已有「流式中」的文本块时复用而非新建。正常流程
      // text_start/text_end 成对出现，end 会把 streaming 置 false，
      // 因此命中流式块只可能来自重复/重放事件（去重集合被清后的最后防线）。
      const lastAction = msg.actions[msg.actions.length - 1];
      if (lastAction && lastAction.type === 'text' && lastAction.streaming === true) {
        return lastAction;
      }
      msg.streamingText = '';
      msg.currentStreamingType = 'text';
      (msg as any).__splitByShowHtml = false;
      const action = {
        id: randomId('text'),
        type: 'text',
        content: '',
        streaming: true,
        timestamp: Date.now()
      };
      msg.actions.push(action);
      return action;
    },
    appendTextChunk(content: string) {
      if (this.currentMessageIndex < 0) return null;
      const msg = this.messages[this.currentMessageIndex];
      if (!msg) {
        return null;
      }
      if (typeof msg.streamingText !== 'string') {
        msg.streamingText = '';
      }
      msg.streamingText += content;
      let lastAction = msg.actions[msg.actions.length - 1];
      if (!(lastAction && lastAction.type === 'text' && lastAction.streaming)) {
        lastAction = {
          id: randomId('text'),
          type: 'text',
          content: '',
          streaming: true,
          timestamp: Date.now(),
          continuation: true
        };
        msg.actions.push(lastAction);
      }
      lastAction.content += content;

      // show_html 一旦闭合，当前卡片应“定格”，后续 chunk 进入新的 text action，
      // 避免 streaming 阶段每个新 chunk 都重建同一 show_html 卡片。
      if (lastAction.streaming && hasCompletedShowHtmlBlock(lastAction.content)) {
        lastAction.streaming = false;
        lastAction.frozenByShowHtml = true;
        (msg as any).__splitByShowHtml = true;
      }

      return lastAction;
    },
    completeText(fullContent: string) {
      if (this.currentMessageIndex < 0) return;
      const msg = this.messages[this.currentMessageIndex];
      const splitByShowHtml = !!(msg as any).__splitByShowHtml;
      let completedStreamingAction = false;
      for (let i = msg.actions.length - 1; i >= 0; i--) {
        const action = msg.actions[i];
        if (action.type === 'text' && action.streaming) {
          action.streaming = false;
          completedStreamingAction = true;
          if (!splitByShowHtml && typeof fullContent === 'string' && fullContent.length) {
            action.content = fullContent;
          }
          break;
        }
      }
      if (!completedStreamingAction && !splitByShowHtml && typeof fullContent === 'string') {
        for (let i = msg.actions.length - 1; i >= 0; i--) {
          const action = msg.actions[i];
          if (action.type === 'text') {
            action.content = fullContent;
            break;
          }
        }
      }
      msg.streamingText = '';
      msg.currentStreamingType = null;
      delete (msg as any).__splitByShowHtml;
    },
    // 清理异常中断（断网 / API 错误 / 任务停止）残留的流式状态。
    // 正常流程由 completeThinking/completeText 收尾；异常路径不会有
    // thinking_end/text_end，残留的 currentStreamingType/activeThinkingId
    // 会让状态头像（avatarStatus）的 isThinking 永久为真，卡在「思考中」。
    // 注意：若需同时清理空占位 assistant 消息，必须先调
    // cleanupTrailingEmptyAssistantPlaceholder（它依赖 awaitingFirstContent
    // 判定占位），再调本方法。
    clearStreamingResidualState() {
      for (const msg of this.messages) {
        if (!msg || msg.role !== 'assistant') continue;
        msg.currentStreamingType = null;
        msg.activeThinkingId = null;
        msg.streamingThinking = '';
        msg.streamingText = '';
        msg.awaitingFirstContent = false;
        msg.generatingLabel = '';
        if (Array.isArray(msg.actions)) {
          for (const action of msg.actions) {
            if (
              action &&
              (action.type === 'thinking' || action.type === 'text') &&
              action.streaming === true
            ) {
              action.streaming = false;
            }
          }
        }
      }
    },
    addSystemMessage(content: string, meta: any = null) {
      // 与历史重建保持一致：子智能体/后台完成通知作为独立 assistant 消息渲染，
      // 避免运行时与刷新后在垂直间距上不一致。
      const useStandaloneMessage = meta?.variant === 'sub_agent_done';
      if (useStandaloneMessage && Array.isArray(this.messages) && this.messages.length > 0) {
        const last = this.messages[this.messages.length - 1];
        const lastActions = Array.isArray(last?.actions) ? last.actions : [];
        const isEmptyAssistantPlaceholder =
          last?.role === 'assistant' && lastActions.length === 0 && !!last?.awaitingFirstContent;
        if (isEmptyAssistantPlaceholder) {
          userMDebug('chat.addSystemMessage:cleanup-empty-assistant-placeholder', {
            currentMessageIndex: this.currentMessageIndex,
            messagesLengthBeforeCleanup: this.messages.length
          });
          this.messages.pop();
          this.currentMessageIndex = this.messages.length - 1;
        }
      }
      const msg = useStandaloneMessage ? createAssistantMessage() : this.ensureAssistantMessage();
      clearAwaitingFirstContent(msg);
      userMDebug('chat.addSystemMessage:before', {
        content,
        meta,
        useStandaloneMessage,
        messagesLengthBefore: this.messages.length,
        currentMessageIndex: this.currentMessageIndex,
        actionsBefore: Array.isArray(msg?.actions) ? msg.actions.length : -1
      });
      const action = {
        id: randomId('system'),
        type: 'system',
        content,
        variant: meta?.variant || null,
        timestamp: Date.now()
      };
      msg.actions.push(action);
      if (useStandaloneMessage) {
        this.messages.push(msg);
        this.currentMessageIndex = this.messages.length - 1;
      }
      userMDebug('chat.addSystemMessage:after', {
        messagesLengthAfter: this.messages.length,
        currentMessageIndex: this.currentMessageIndex,
        actionsAfter: Array.isArray(msg?.actions) ? msg.actions.length : -1
      });
    },
    getActiveThinkingAction(msg: any) {
      if (!msg || !Array.isArray(msg.actions)) {
        return null;
      }
      if (msg.activeThinkingId) {
        const found = msg.actions.find(
          (action: any) =>
            action && action.id === msg.activeThinkingId && action.type === 'thinking'
        );
        if (found) {
          return found;
        }
      }
      for (let i = msg.actions.length - 1; i >= 0; i--) {
        const action = msg.actions[i];
        if (action && action.type === 'thinking' && action.streaming !== false) {
          return action;
        }
      }
      return null;
    },
    resetChatState() {
      this.messages = [];
      this.currentMessageIndex = -1;
      this.streamingMessage = false;
      this.clearExpandedBlocks();
      this.clearThinkingLocks();
    }
  }
});
