import { t, currentLocale } from '@/locales';
import { toolFaceKey } from './avatarFace';
import { messageStartsWork } from './messageVisibility';

export interface AvatarRuntimeState {
  messages: any[];
  taskInProgress?: boolean;
  streamingMessage?: boolean;
  waitingForSubAgent?: boolean;
  waitingForBackgroundCommand?: boolean;
  stopRequested?: boolean;
  apiRequestPending?: boolean;
  preparingTools?: Map<string, any>;
  activeTools?: Map<string, any>;
  subAgents?: any[];
  backgroundCommands?: any[];
  toolIntentEnabled?: boolean;
}

export interface AvatarStatus {
  mode: 'idle' | 'work' | 'think' | 'tool';
  toolKeys: string[];
  toolTexts: string[];
  text: string;
  tracking: boolean;
  apiWaiting?: boolean;
}

function getLatestToolSegment(actions: any[]) {
  if (!Array.isArray(actions)) return [];
  let last = -1;
  for (let i = actions.length - 1; i >= 0; i--) {
    if (actions[i]?.type === 'tool') {
      last = i;
      break;
    }
    if (actions[i]?.type === 'thinking') break;
  }
  if (last < 0) return [];
  let start = last;
  while (start > 0 && actions[start - 1]?.type === 'tool') start--;
  return actions.slice(start, last + 1);
}

const RUNNING_TOOL_STATUS = new Set([
  'preparing',
  'running',
  'pending',
  'queued',
  'awaiting_approval',
  'pending_approval',
  'awaiting_user_answer'
]);
const TERMINAL_STATUS = new Set(['completed', 'failed', 'timeout', 'terminated', 'cancelled']);

/** The existing Agent avatar decisions, shared by both conversation modes. */
export function resolveAvatarStatus(state: AvatarRuntimeState): AvatarStatus {
  void currentLocale.value;
  const messages = Array.isArray(state.messages) ? state.messages : [];
  const lastAssistant = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const message = messages[i];
      if (message?.role === 'assistant') return message;
      if (message?.role === 'user' && messageStartsWork(message)) break;
    }
    return null;
  })();
  const countRunning = (items: any[]) =>
    (Array.isArray(items) ? items : []).filter((item) => {
      if (!item) return false;
      const status = String(item?.status || '').toLowerCase();
      return status ? !TERMINAL_STATUS.has(status) : true;
    }).length;
  const agentCount = (Array.isArray(state.subAgents) ? state.subAgents : []).filter(
    (agent) => String(agent?.status || '').toLowerCase() === 'running'
  ).length;
  const cmdCount = countRunning(state.backgroundCommands || []);
  const bgText = (() => {
    const parts = [];
    if (cmdCount > 0)
      parts.push(t('appCore.backgroundCommandCount', { n: cmdCount, count: cmdCount }));
    if (agentCount > 0)
      parts.push(t('appCore.backgroundAgentCount', { n: agentCount, count: agentCount }));
    return parts.length ? `${parts.join('，')}${t('appCore.backgroundRunning')}` : '';
  })();
  const intentEnabled = !!state.toolIntentEnabled;
  const isThinking =
    !!lastAssistant &&
    (state.taskInProgress || state.streamingMessage) &&
    (lastAssistant.currentStreamingType === 'thinking' || lastAssistant.activeThinkingId != null);
  let runningTools = [];
  if (lastAssistant) {
    const segment = getLatestToolSegment(lastAssistant.actions || []);
    runningTools = segment.filter((action) => {
      const status = String(action?.tool?.status || '').toLowerCase();
      return action?.tool?.awaiting_content || !status || RUNNING_TOOL_STATUS.has(status);
    });
  }
  if (runningTools.length === 0) {
    const mapActions = [
      ...Array.from(state.preparingTools?.values?.() || []),
      ...Array.from(state.activeTools?.values?.() || [])
    ];
    runningTools = mapActions
      .map((item) => (item?.type === 'tool' ? item : { type: 'tool', tool: item }))
      .filter((action) => {
        const tool = action?.tool;
        if (!tool) return false;
        const status = String(tool?.status || '').toLowerCase();
        return tool?.awaiting_content || !status || RUNNING_TOOL_STATUS.has(status);
      });
  }
  const hasMapTools =
    (state.preparingTools && state.preparingTools.size > 0) ||
    (state.activeTools && state.activeTools.size > 0);
  const running =
    state.streamingMessage ||
    state.taskInProgress ||
    state.waitingForSubAgent ||
    state.waitingForBackgroundCommand ||
    state.stopRequested ||
    hasMapTools ||
    runningTools.length > 0 ||
    agentCount > 0 ||
    cmdCount > 0;
  if (
    state.apiRequestPending &&
    !state.stopRequested &&
    (state.taskInProgress || state.streamingMessage)
  ) {
    return {
      mode: 'work',
      toolKeys: [],
      toolTexts: [],
      text: t('appCore.waitingApiResponse'),
      tracking: false,
      apiWaiting: true
    };
  }
  if (isThinking) {
    return {
      mode: 'think',
      toolKeys: [],
      toolTexts: [],
      text: t('appCore.thinking'),
      tracking: false
    };
  }
  if (runningTools.length > 0) {
    const keys = runningTools.map((action) => toolFaceKey(action?.tool?.name));
    const getFinalIntentText = (tool: any) => {
      if (!tool) return '';
      return tool.status === 'running' && tool.intent_complete === true
        ? tool.intent_full || ''
        : '';
    };
    const toolTexts = runningTools.map((action) =>
      intentEnabled && !state.stopRequested ? getFinalIntentText(action?.tool) : ''
    );
    const text = toolTexts.find((value) => value) || '';
    return { mode: 'tool', toolKeys: keys, toolTexts, text, tracking: false };
  }
  if (running) {
    if (lastAssistant?.currentStreamingType === 'text') {
      return { mode: 'work', toolKeys: [], toolTexts: [], text: '', tracking: false };
    }
    let text = bgText;
    if (!text && state.apiRequestPending && !state.stopRequested) {
      return {
        mode: 'work',
        toolKeys: [],
        toolTexts: [],
        text: t('appCore.waitingApiResponse'),
        tracking: false,
        apiWaiting: true
      };
    }
    if (!text) {
      const awaitingMsg =
        lastAssistant && lastAssistant.awaitingFirstContent ? lastAssistant : null;
      if (awaitingMsg) {
        text =
          (typeof awaitingMsg.generatingLabel === 'string' && awaitingMsg.generatingLabel.trim()) ||
          '';
      }
    }
    return { mode: 'work', toolKeys: [], toolTexts: [], text, tracking: false };
  }
  return { mode: 'idle', toolKeys: [], toolTexts: [], text: '', tracking: true };
}
