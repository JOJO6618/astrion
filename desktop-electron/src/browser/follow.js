/**
 * 跟随智能体的唯一判定：AI 动到哪个标签，用户视图就跟到哪个。
 * 跟随关闭时返回 null —— 调用方不得再写 selected，用户看什么完全由自己决定。
 */
export function followAiTarget(tab, selectedId) {
  if (!tab?.profile) return null;
  if (!tab.profile.followAi()) return null;
  if (selectedId === tab.id) return null;
  return tab.id;
}
