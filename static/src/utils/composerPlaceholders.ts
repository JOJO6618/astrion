/**
 * 主输入框占位文案池（Chat / Agent 共用一套，Astrion 对用户说话的口吻）。
 *
 * 只存 i18n key，在使用处 t(key) 求值（切换语言即时生效）。
 * 抽取时机由 InputComposer 决定：切换对话、发送后输入框清空；不定时轮换。
 */
export const COMPOSER_PLACEHOLDER_KEYS = [
  'input.placeholderAskAstrion',
  'input.placeholderSpeakFreely',
  'input.placeholderAskAnything',
  'input.placeholderGoAhead',
  'input.placeholderYourThoughts',
  'input.placeholderStartIdea',
  'input.placeholderWhateverComes',
  'input.placeholderListening',
  'input.placeholderCopy',
  'input.placeholderWriting',
  'input.placeholderCode',
  'input.placeholderTodo',
  'input.placeholderStuck',
  'input.placeholderRoughIdea',
  'input.placeholderPolish',
  'input.placeholderOneSentence'
] as const;

/** 随机抽一条，尽量不与上一条重复。 */
export function pickComposerPlaceholderKey(previous?: string): string {
  const pool = COMPOSER_PLACEHOLDER_KEYS.filter((key) => key !== previous);
  return pool[Math.floor(Math.random() * pool.length)] ?? COMPOSER_PLACEHOLDER_KEYS[0];
}
