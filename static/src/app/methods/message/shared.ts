// @ts-nocheck
// @ts-nocheck
import { debugLog } from '../common';
import { useModelStore } from '../../../stores/model';

// 兼容 Windows 反斜杠路径：skills 目录与 SKILL.md 前的分隔符同时接受 / 和 \
// 路径前缀可选：后端自 cedef87d 起返回相对工作区路径（.astrion/skills/...），历史消息仍可能是绝对路径
export const SKILL_MARKDOWN_LINK_RE = /\[\$([^\]\n]+)\]\(((?:[^)\n]*[\\/])?\.astrion[\\/]skills[\\/][^)\n]+[\\/]SKILL\.md)\)/g;

export function extractSkillRefsFromMessage(message = '') {
  const refs = [];
  const seen = new Set();
  const text = String(message || '');
  let match;
  SKILL_MARKDOWN_LINK_RE.lastIndex = 0;
  while ((match = SKILL_MARKDOWN_LINK_RE.exec(text))) {
    const name = String(match[1] || '').trim().replace(/^\$/, '');
    const path = String(match[2] || '').trim();
    if (!path || seen.has(path)) {
      continue;
    }
    seen.add(path);
    refs.push({ name, path });
  }
  return refs;
}

