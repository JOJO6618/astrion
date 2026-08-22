/**
 * 流式渲染 HTML 顶层分块工具。
 *
 * 背景：流式输出期间，MarkdownRenderer 每个 token 都会重算整段 HTML 字符串，
 * 若用单个 v-html 承载，innerHTML 每次全量重设，导致已增强的 DOM 被整段销毁重建：
 * - show_file 卡片（动态挂载的 Vue app）被反复卸载重挂、重新 fetch 内容；
 * - 表格/图片/KaTeX 公式/已绑定事件的下载链接全部重置（滚动位置、加载态丢失）。
 *
 * 思路：把渲染后的 HTML 按顶层节点切成小块，配合 v-for 稳定 key 逐块 v-html。
 * Markdown 渲染对同一前缀是确定性的，流式期间只有末尾块（正在书写的段落/表格）
 * 的 HTML 字符串会变化；已完成的前缀块字符串保持不变，Vue 的 v-html 比对到相同
 * 字符串就不触碰 DOM——挂载的卡片 app、表格滚动位置、KaTeX 渲染结果自然存活。
 *
 * 注意：比较的是「两次渲染产出的字符串」，而不是 DOM 本身，因此后续 JS 对 DOM 的
 * 增强（data-rendered、KaTeX 填充、事件绑定）不会干扰分块稳定性。
 */

export interface HtmlChunk {
  key: string;
  html: string;
}

/**
 * 将完整 HTML 字符串切分为顶层块。索引即 key：流式内容为 append-only，
 * 前缀块的下标与字符串在 token 间保持稳定。
 */
export function chunkRenderedHtml(html: string): HtmlChunk[] {
  if (!html) return [];
  if (typeof document === 'undefined') {
    return [{ key: 'c0', html }];
  }
  const template = document.createElement('template');
  template.innerHTML = html;
  const chunks: HtmlChunk[] = [];
  for (const node of Array.from(template.content.childNodes)) {
    if (node.nodeType === Node.TEXT_NODE) {
      // 块间纯空白节点丢弃（markdown 输出的块间换行符），避免无意义分块
      const text = node.textContent || '';
      if (!text.trim()) continue;
      // textContent 是解码后的文本，回写 v-html 前必须重新转义
      chunks.push({
        key: `c${chunks.length}`,
        html: text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      });
      continue;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) continue;
    chunks.push({ key: `c${chunks.length}`, html: (node as Element).outerHTML });
  }
  return chunks;
}
