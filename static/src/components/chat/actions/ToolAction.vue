<template>
  <div
    class="collapsible-block tool-block"
    :class="{
      expanded,
      collapsing,
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
  collapsing?: boolean;
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
/* 工具增强显示样式 - 不使用 scoped 以便应用到 v-html 内容。
   结构（见 toolResultHtml.ts）：
   - .tool-result-body 纵向排列，子块间距统一由 gap 控制，子块自身不带外边距；
   - .tool-result-meta 只放参数，长参数进 .tool-param-text 限高小块；
   - .tool-result-content.scrollable 是一个工具结果里唯一的大块滚动区域；
   - 所有滚动区域 overscroll-behavior: contain，滚到底不会带动外层；
   - 容器层级保持扁平：工具块内部不再有任何底色/描边/圆角框，参数、结果、错误
     全部从同一左边缘起排，参数与结果之间只用一条分隔线区分；结果内的列表项同样
     只用分隔线。新增结果元素不得自带内边距框，否则会与其余内容左右错位。 */
.tool-result-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
}

.tool-result-body > * {
  margin: 0;
  min-width: 0;
}

/* 说明区（参数）：平铺，后面还有结果时用分隔线隔开 */
.tool-result-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 13px;
  line-height: 1.6;
}

.tool-result-body > .tool-result-meta:not(:last-child) {
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border-default);
}

.tool-param {
  min-width: 0;
  overflow-wrap: anywhere;
}

.tool-param-label {
  font-weight: 600;
  color: var(--text-primary);
}

.tool-param--mono .tool-param-value,
.tool-param--mono .tool-param-text,
.tool-result-content pre,
.tool-result-diff,
.search-match-snippet {
  font-family: 'Monaco', 'Menlo', 'Consolas', monospace;
}

.tool-param--mono .tool-param-value {
  font-size: 12px;
}

.tool-param--block .tool-param-text {
  margin-top: 2px;
  max-height: calc(1.6em * 6);
  overflow-y: auto;
  overscroll-behavior: contain;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.tool-param--mono .tool-param-text {
  font-size: 12px;
  line-height: 1.5;
  max-height: calc(1.5em * 8);
}

/* 结果框 */
.tool-result-section {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.tool-result-section-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
}

.tool-result-content.scrollable,
.tool-result-diff.scrollable {
  max-height: 400px;
  overflow-y: auto;
  overscroll-behavior: contain;
}

/* 结构化显示滚动条样式（参考 git 状态侧边栏文件列表，滚动槽透明贴合灰色底色） */
.tool-result-content.scrollable,
.tool-result-diff.scrollable,
.tool-param-text,
.code-block pre,
.output-block pre {
  scrollbar-width: thin; /* Firefox */
  scrollbar-color: color-mix(in srgb, var(--text-secondary) 55%, transparent) transparent;
}

.tool-result-content.scrollable::-webkit-scrollbar,
.tool-result-diff.scrollable::-webkit-scrollbar,
.tool-param-text::-webkit-scrollbar,
.code-block pre::-webkit-scrollbar,
.output-block pre::-webkit-scrollbar {
  width: 10px;
  height: 10px;
}

.tool-result-content.scrollable::-webkit-scrollbar-track,
.tool-result-diff.scrollable::-webkit-scrollbar-track,
.tool-param-text::-webkit-scrollbar-track,
.code-block pre::-webkit-scrollbar-track,
.output-block pre::-webkit-scrollbar-track {
  background: transparent;
  border-radius: 999px;
  margin: 6px;
}

.tool-result-content.scrollable::-webkit-scrollbar-thumb,
.tool-result-diff.scrollable::-webkit-scrollbar-thumb,
.tool-param-text::-webkit-scrollbar-thumb,
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
  overflow-wrap: anywhere;
  font-size: 12px;
  line-height: 1.5;
}

/* 摘要行（执行统计）、纯文本结果、空结果 */
.tool-result-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  column-gap: 6px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-secondary);
  overflow-wrap: anywhere;
}

.tool-result-summary-sep {
  color: var(--text-tertiary);
}

.tool-result-text {
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.tool-result-empty {
  font-size: 13px;
  color: var(--text-tertiary);
}

/* 回答等结果侧的键值行（不带说明区底色） */
.tool-result-kv {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 13px;
  line-height: 1.6;
}

/* 错误与提醒：只用文字颜色区分，不加底色块（保持与其余内容同一左边缘） */
.tool-result-error,
.tool-result-warning {
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: calc(1.6em * 8);
  overflow-y: auto;
  overscroll-behavior: contain;
}

.tool-result-error {
  color: var(--state-danger);
}

.tool-result-warning {
  color: var(--state-warning);
}

/* 列表型结果（搜索、记忆、对话、工作流、加载的工具）：扁平条目 + 分隔线 */
.search-result-item {
  padding: 8px 0;
  border-top: 1px solid var(--border-default);
  min-width: 0;
}

.search-result-item:first-child {
  padding-top: 0;
  border-top: none;
}

.search-result-item:last-child {
  padding-bottom: 0;
}

.search-result-title {
  font-weight: 600;
  font-size: 13px;
  color: var(--text-primary);
  overflow-wrap: anywhere;
}

.search-result-url,
.search-result-detail {
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-secondary);
  overflow-wrap: anywhere;
}

.search-result-detail--clamp {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.search-result-url a {
  color: var(--state-info);
  text-decoration: none;
}

.search-result-url a:hover {
  text-decoration: underline;
}

/* 记忆搜索匹配片段：单行展示，过长不折行直接省略号截断 */
.search-match-snippet {
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 文件内搜索命中 / 提取片段：扁平条目，用 ⋯ 分隔 */
.search-match-line,
.segment-range {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
  margin-bottom: 2px;
}

.search-match-separator,
.segment-separator {
  text-align: center;
  color: var(--text-tertiary);
  margin: 6px 0;
  line-height: 1;
}

/* 文件差异 */
.tool-result-diff {
  font-size: 12px;
  line-height: 1.5;
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
  overflow-wrap: anywhere;
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

/* 图片 */
.tool-result-image {
  text-align: left;
}

.tool-result-image img {
  max-width: 100%;
  /* 限高防止长截图占满对话区；点击由聊天区事件委托打开灯箱 */
  max-height: 480px;
  height: auto;
  border-radius: 6px;
  border: 1px solid var(--border-default);
}

/* 子智能体状态列表：扁平条目 + 分隔线 */
.sub-agent-status-item {
  padding: 8px 0;
  border-top: 1px solid var(--border-default);
  min-width: 0;
}

.sub-agent-status-item:first-child {
  padding-top: 0;
  border-top: none;
}

.sub-agent-status-item:last-child {
  padding-bottom: 0;
}

.sub-agent-status-header {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}

.sub-agent-status-name {
  font-weight: 600;
  font-size: 13px;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.sub-agent-status-state {
  flex-shrink: 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.sub-agent-status-detail {
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-secondary);
  overflow-wrap: anywhere;
}

.tool-result-content pre.sub-agent-status-result {
  margin-top: 4px;
}

.tool-result-content pre.sub-agent-status-result.is-error {
  color: var(--state-danger);
}

/* 待办事项：自绘复选标记（禁止原生 checkbox） */
.todo-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.todo-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 13px;
  line-height: 1.6;
}

.todo-check {
  position: relative;
  flex: 0 0 14px;
  width: 14px;
  height: 14px;
  /* 与首行文字视觉居中：(行高 1.6em - 14px) / 2 */
  margin-top: calc((1.6em - 14px) / 2);
  border: 1.5px solid var(--border-strong);
  border-radius: 3px;
  box-sizing: border-box;
}

.todo-item.is-done .todo-check {
  background: var(--accent);
  border-color: var(--accent);
}

.todo-item.is-done .todo-check::after {
  content: '';
  position: absolute;
  left: 3.5px;
  top: 0.5px;
  width: 4px;
  height: 8px;
  border: solid var(--on-accent);
  border-width: 0 1.5px 1.5px 0;
  transform: rotate(45deg);
}

.todo-item.is-done .todo-text {
  color: var(--text-tertiary);
  text-decoration: line-through;
}

.todo-text {
  min-width: 0;
  overflow-wrap: anywhere;
}

/* 代码块（Markdown 代码块共用，保持原样） */
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

/* 记忆条目（虚拟监视器 .memory-item 依赖这组全局样式，保持原样） */
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

/* 彩蛋 */
.easter-egg-content {
  font-size: 14px;
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
