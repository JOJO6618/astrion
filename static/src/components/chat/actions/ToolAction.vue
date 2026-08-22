<template>
  <div
    class="collapsible-block tool-block"
    :class="{
      expanded,
      processing: action.tool.status === 'preparing' || action.tool.status === 'running',
      completed: action.tool.status === 'completed'
    }"
    :data-block-id="blockId || collapseKey || action.tool.id || action.id || 'tool'"
  >
    <div class="collapsible-header" @click="$emit('toggle')">
      <div class="arrow"></div>
      <div class="status-icon">
        <span
          class="tool-icon icon icon-md"
          :class="getToolAnimationClass(action.tool)"
          :style="iconStyle(getToolIcon(action.tool))"
          aria-hidden="true"
        ></span>
      </div>
      <span class="status-text">{{ getToolStatusText(action.tool) }}</span>
      <span class="tool-desc">{{ getToolDescription(action.tool) }}</span>
    </div>
    <div
      class="collapsible-content"
      :ref="
        (el) =>
          registerCollapseContent &&
          registerCollapseContent(collapseKey || action.tool.id || action.id || 'tool', el)
      "
    >
      <div class="content-inner">
        <div v-html="renderToolResult()"></div>
      </div>
    </div>
    <div
      v-if="action.tool.status === 'preparing' || action.tool.status === 'running'"
      class="progress-indicator"
    ></div>
  </div>
</template>

<script setup lang="ts">
defineOptions({ name: 'ToolAction' });

const props = defineProps<{
  action: any;
  expanded: boolean;
  blockId?: string;
  iconStyle: (key: string) => Record<string, string>;
  getToolAnimationClass: (tool: any) => Record<string, unknown>;
  getToolIcon: (tool: any) => string;
  getToolStatusText: (tool: any) => string;
  getToolDescription: (tool: any) => string;
  formatSearchTopic: (filters: Record<string, any>) => string;
  formatSearchTime: (filters: Record<string, any>) => string;
  formatSearchDomains: (filters: Record<string, any>) => string;
  streamingMessage: boolean;
  registerCollapseContent?: (key: string, el: Element | null) => void;
  collapseKey?: string;
}>();

defineEmits<{ (event: 'toggle'): void }>();

import { renderEnhancedToolResult } from './toolRenderers';

// 渲染逻辑统一收敛到 toolRenderers.ts（与 StackedBlocks/MinimalBlocks 同一实现），
// 本组件只保留模板与样式，不再维护第二份渲染函数副本。
function renderToolResult(): string {
  return renderEnhancedToolResult(
    props.action,
    props.formatSearchTopic,
    props.formatSearchTime,
    props.formatSearchDomains
  );
}
</script>

<style>
/* 工具增强显示样式 - 不使用 scoped 以便应用到 v-html 内容 */
.tool-result-meta {
  margin-bottom: 12px;
  padding: 8px 12px;
  background: var(--hover-bg);
  border-radius: 6px;
  font-size: 13px;
  line-height: 1.6;
}

.tool-result-meta > div {
  margin: 4px 0;
  /* 参数值中的换行符（如多行 command/content）需要真实渲染，
     否则多行内容会挤成一行仅靠容器宽度被动折行 */
  white-space: pre-wrap;
  word-break: break-word;
}

.tool-result-content {
  margin-top: 12px;
}

.tool-result-content.scrollable {
  max-height: 400px;
  overflow-y: auto;
  border: 1px solid var(--border-default);
  border-radius: 6px;
  padding: 8px;
  background: color-mix(in srgb, transparent 99%, var(--text-primary));
}

/* 结构化显示滚动条样式（参考 git 状态侧边栏文件列表，滚动槽透明贴合灰色底色） */
.tool-result-content.scrollable,
.tool-result-diff.scrollable,
.code-block pre,
.output-block pre {
  scrollbar-width: thin; /* Firefox */
  scrollbar-color: color-mix(in srgb, var(--text-secondary) 55%, transparent) transparent;
}

.tool-result-content.scrollable::-webkit-scrollbar,
.tool-result-diff.scrollable::-webkit-scrollbar,
.code-block pre::-webkit-scrollbar,
.output-block pre::-webkit-scrollbar {
  width: 10px;
  height: 10px;
}

.tool-result-content.scrollable::-webkit-scrollbar-track,
.tool-result-diff.scrollable::-webkit-scrollbar-track,
.code-block pre::-webkit-scrollbar-track,
.output-block pre::-webkit-scrollbar-track {
  background: transparent;
  border-radius: 999px;
  margin: 8px;
}

.tool-result-content.scrollable::-webkit-scrollbar-thumb,
.tool-result-diff.scrollable::-webkit-scrollbar-thumb,
.code-block pre::-webkit-scrollbar-thumb,
.output-block pre::-webkit-scrollbar-thumb {
  background: color-mix(in srgb, var(--text-secondary) 55%, transparent);
  border-radius: 999px;
  border: 2px solid transparent;
  background-clip: padding-box;
}

.tool-result-content pre {
  margin: 0;
  white-space: pre-wrap;
  word-wrap: break-word;
  font-family: 'Monaco', 'Menlo', 'Consolas', monospace;
  font-size: 12px;
  line-height: 1.5;
}

.content-label {
  font-weight: 600;
  margin-bottom: 8px;
  font-size: 13px;
}

.tool-result-empty {
  color: var(--text-secondary);
  font-style: italic;
  padding: 8px;
}

/* 搜索结果 */
.search-result-list {
  margin-top: 12px;
}

.search-result-item {
  padding: 10px;
  margin-bottom: 8px;
  border: 1px solid var(--border-default);
  border-radius: 6px;
  background: color-mix(in srgb, transparent 99%, var(--text-primary));
}

/* 暗色模式下的搜索结果 */
:root[data-theme='dark'] .search-result-item {
  background: var(--badge-bg);
  border: 1px solid var(--border-default);
}

.search-result-title {
  font-weight: 600;
  margin-bottom: 4px;
  font-size: 14px;
}

.search-result-url {
  font-size: 12px;
  color: var(--text-secondary);
}

:root[data-theme='dark'] .search-result-url {
  color: var(--text-secondary);
}

.search-result-url a {
  color: var(--state-info);
  text-decoration: none;
}

:root[data-theme='dark'] .search-result-url a {
  color: var(--state-info);
}

.search-result-url a:hover {
  text-decoration: underline;
}

/* 文件差异 */
.tool-result-diff {
  margin-top: 12px;
  font-family: 'Monaco', 'Menlo', 'Consolas', monospace;
  font-size: 12px;
  line-height: 1.5;
}

.tool-result-diff.scrollable {
  max-height: 400px;
  overflow-y: auto;
  border: 1px solid var(--border-default);
  border-radius: 6px;
  padding: 8px;
  background: color-mix(in srgb, transparent 99%, var(--text-primary));
}

.diff-line {
  display: grid;
  grid-template-columns: 5ch 1ch minmax(0, 1fr);
  column-gap: 8px;
  padding: 2px 4px;
  margin: 0;
  align-items: start;
}

.diff-line-number {
  color: var(--text-tertiary);
  text-align: right;
  user-select: none;
}

.diff-marker {
  color: var(--text-secondary);
  text-align: center;
  user-select: none;
}

.diff-content {
  min-width: 0;
  white-space: pre-wrap;
  word-wrap: break-word;
}

.diff-remove {
  background: var(--diff-del-bg);
  color: var(--state-danger);
}

.diff-add {
  background: var(--diff-add-bg);
  color: var(--state-success);
}

.diff-separator {
  text-align: left;
  color: var(--text-tertiary);
  margin: 8px 0;
}

.diff-separator .diff-content {
  font-size: 16px;
  font-weight: 700;
  line-height: 1.1;
}

.diff-operation {
  font-weight: 600;
  margin: 8px 0 4px;
  color: var(--text-secondary);
}

/* 图片 */
.tool-result-image {
  margin-top: 12px;
  text-align: center;
}

.tool-result-image img {
  max-width: 100%;
  /* 限高防止长截图占满对话区；点击可经外层链接打开原图 */
  max-height: 480px;
  height: auto;
  border-radius: 6px;
  border: 1px solid var(--border-default);
}

.tool-result-image a {
  cursor: zoom-in;
}

/* 代码块 */
.code-block {
  margin-top: 12px;
}

.code-label,
.output-label {
  font-weight: 600;
  margin-bottom: 6px;
  font-size: 13px;
}

.code-block pre,
.output-block pre {
  margin: 0;
  padding: 12px;
  background: var(--hover-bg);
  border: 1px solid var(--border-default);
  border-radius: 6px;
  overflow-x: auto;
  font-family: 'Monaco', 'Menlo', 'Consolas', monospace;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-wrap: break-word;
}

/* 暗色模式：代码/输出统一白字，去除任何描边/阴影观感 */
:root[data-theme='dark'] .code-block pre,
:root[data-theme='dark'] .output-block pre {
  color: var(--text-primary) !important;
  background: var(--hover-bg);
  border-color: var(--border-strong);
  text-shadow: none !important;
}

:root[data-theme='dark'] .code-block pre code,
:root[data-theme='dark'] .output-block pre code {
  color: var(--text-primary) !important;
  text-shadow: none !important;
  -webkit-text-fill-color: var(--text-primary);
}

.output-block {
  margin-top: 12px;
}

/* 记忆 */
.memory-section {
  margin-bottom: 12px;
}

.memory-item {
  padding: 6px 8px;
  margin: 4px 0;
  border-radius: 4px;
  font-size: 13px;
  line-height: 1.5;
}

.memory-add {
  background: var(--diff-add-bg);
  color: var(--state-success);
}

.memory-update {
  background: color-mix(in srgb, var(--state-warning) 10%, transparent);
  color: var(--state-warning);
}

.memory-delete {
  background: var(--diff-del-bg);
  color: var(--state-danger);
}

/* 待办事项 */
.todo-list {
  padding: 8px;
}

.todo-item {
  padding: 6px 0;
  font-size: 13px;
  line-height: 1.5;
  display: flex;
  align-items: center;
  gap: 8px;
}

.todo-item input[type='checkbox'] {
  margin: 0;
}

/* 彩蛋 */
.easter-egg-content {
  padding: 12px;
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--decorative-gold) 10%, transparent),
    color-mix(in srgb, var(--decorative-pink) 10%, transparent)
  );
  border: 1px solid color-mix(in srgb, var(--decorative-gold) 30%, transparent);
  border-radius: 8px;
  font-size: 14px;
  line-height: 1.6;
}

/* 搜索匹配项 */
.search-match-separator {
  text-align: center;
  color: var(--text-tertiary);
  margin: 12px 0;
  font-size: 16px;
}

.search-match-item {
  margin-bottom: 12px;
  padding: 8px;
  background: var(--hover-bg);
  border-radius: 6px;
}

:root[data-theme='dark'] .search-match-item {
  background: var(--hover-bg);
}

.search-match-line {
  font-size: 11px;
  color: var(--text-secondary);
  margin-bottom: 4px;
  font-weight: 600;
}

:root[data-theme='dark'] .search-match-line {
  color: var(--text-secondary);
}

.search-match-item pre {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
}

/* 记忆搜索匹配片段：单行展示，过长不折行直接省略号截断（hover title 看全文） */
.search-match-snippet {
  font-family: 'Monaco', 'Menlo', 'Consolas', monospace;
  font-size: 12px;
  line-height: 1.5;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 提取片段 */
.segment-separator {
  text-align: center;
  color: var(--text-tertiary);
  margin: 12px 0;
  font-size: 16px;
}

.segment-item {
  margin-bottom: 12px;
  padding: 8px;
  background: var(--hover-bg);
  border-radius: 6px;
}

:root[data-theme='dark'] .segment-item {
  background: var(--hover-bg);
}

.segment-range {
  font-size: 11px;
  color: var(--text-secondary);
  margin-bottom: 4px;
  font-weight: 600;
}

:root[data-theme='dark'] .segment-range {
  color: var(--text-secondary);
}

.segment-item pre {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
}

/* 个性化管理 */
.personalization-message {
  padding: 12px;
  background: var(--hover-bg);
  border-radius: 6px;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
}

:root[data-theme='dark'] .personalization-message {
  background: var(--hover-bg);
}

.personalization-config-title {
  font-weight: 600;
  margin-bottom: 8px;
  color: var(--text-primary);
}

:root[data-theme='dark'] .personalization-config-title {
  color: var(--text-primary);
}

.personalization-config-list {
  display: grid;
  gap: 6px;
}

.config-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 10px;
  background: var(--hover-bg);
  border-radius: 4px;
  font-size: 12px;
}

:root[data-theme='dark'] .config-item {
  background: var(--hover-bg);
}

.config-label {
  color: var(--text-secondary);
  font-weight: 500;
}

:root[data-theme='dark'] .config-label {
  color: var(--text-secondary);
}

.config-value {
  color: var(--text-primary);
  font-weight: 400;
}

:root[data-theme='dark'] .config-value {
  color: var(--text-primary);
}
</style>
