export type MessageVisibility = 'chat' | 'compact' | 'hidden';

const VALID_VISIBILITY = new Set(['chat', 'compact', 'hidden']);
// 仅这些来源在缺少显式 starts_work 元数据时，回退判定为「开启新一轮工作」。
// 后台完成通知（sub_agent/background_command）属于当前工作的延续，不在此列。
const LEGACY_STARTS_WORK_SOURCES = new Set(['user', 'presend']);
const COMPACT_FALLBACK_SOURCES = new Set([
  'guidance',
  'goal_review',
  'compression',
  'compression_handoff',
  'sub_agent',
  'background_command',
  // 工作流运行期通知（inline 注入）：紧凑通知；闲时任务入口派发的激活/退出
  // 消息带显式 visibility:'chat'，不受本兜底影响
  'workflow'
]);

function normalizeSource(value: any): string {
  return String(value || '').trim().toLowerCase();
}

function messageContent(message: any): string {
  return String(message?.content || message?.message || '').trim();
}

function isLegacyGoalPrompt(message: any): boolean {
  const content = messageContent(message);
  return content.includes('【目标模式已开启】') || content.includes('[系统通知|goal]\n【目标模式已开启】');
}

function isLegacyGoalReview(message: any): boolean {
  const content = messageContent(message);
  return content.includes('审核智能体对于你的工作结束给出了以下内容');
}

export function getMessageVisibility(message: any): MessageVisibility {
  const meta = message?.metadata || {};
  const explicit = normalizeSource(meta.visibility || meta.ui?.visibility || message?.visibility);
  if (VALID_VISIBILITY.has(explicit)) {
    return explicit as MessageVisibility;
  }

  if (meta.hidden === true || meta.system_injected_image || meta.system_injected_video) {
    return 'hidden';
  }

  const source = normalizeSource(meta.message_source || meta.source || message?.message_source || message?.source);
  if (source === 'skill' || source === 'goal_prompt' || isLegacyGoalPrompt(message)) {
    return 'hidden';
  }
  if (source === 'notify' && (meta.runtime_mode_notice || message?.runtime_mode_notice)) {
    return 'hidden';
  }
  if (COMPACT_FALLBACK_SOURCES.has(source) || isLegacyGoalReview(message)) {
    return 'compact';
  }
  return 'chat';
}

export function messageStartsWork(message: any): boolean {
  const meta = message?.metadata || {};
  if (typeof meta.starts_work === 'boolean') {
    return meta.starts_work;
  }
  if (typeof meta.ui?.starts_work === 'boolean') {
    return meta.ui.starts_work;
  }
  if (typeof message?.starts_work === 'boolean') {
    return message.starts_work;
  }

  if (getMessageVisibility(message) === 'hidden') {
    return false;
  }

  const source = normalizeSource(meta.message_source || meta.source || message?.message_source || message?.source);
  if (source === 'guidance' || source === 'goal_prompt' || source === 'skill') {
    return false;
  }
  if (source === 'goal_review' || isLegacyGoalReview(message)) {
    return true;
  }
  // 压缩消息应该继续前一个 work segment，不开启新的回复头与计时器
  if (source === 'compression' || source === 'compression_handoff') {
    return false;
  }
  return LEGACY_STARTS_WORK_SOURCES.has(source || 'user');
}
