<template>
  <div ref="containerRef" class="markdown-renderer">
    <template v-for="segment in segments" :key="segment.key">
      <!-- 文本段按顶层块分片渲染：流式期间只有末尾块字符串变化，
           已完成的前缀块 v-html 字符串不变、DOM 不被重设，
           保护 show_file 卡片/表格/KaTeX/图片等已增强内容不被反复重建 -->
      <div v-if="segment.type === 'text'" class="markdown-text-segment">
        <div
          v-for="chunk in chunksForSegment(segment)"
          :key="chunk.key"
          class="md-html-chunk"
          v-html="chunk.html"
        ></div>
      </div>
      <CodeBlock
        v-else
        :content="segment.content"
        :language="segment.language"
        :is-streaming="!segment.closed"
      />
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, inject, nextTick, onMounted, onUpdated, ref, watch } from 'vue';
import CodeBlock from './CodeBlock.vue';
import {
  parseMarkdownSegments,
  renderMarkdownText,
  renderMathBlocks,
  type MarkdownSegment
} from '@/composables/useMarkdownRenderer';
import { chunkRenderedHtml, type HtmlChunk } from '@/utils/htmlChunks';
import { enhanceCitationChips, type CitationAnnotation } from './citationChips';

defineOptions({ name: 'MarkdownRenderer' });

const props = defineProps<{
  content: string;
  isStreaming?: boolean;
  citations?: CitationAnnotation[];
  /** citations 是否为后端裁决后的权威版本（message.metadata.citations 已到达） */
  citationsFinal?: boolean;
  enableCitations?: boolean;
}>();

const containerRef = ref<HTMLElement | null>(null);
const onMathRendered = inject<() => void>('mathRenderedCallback', () => {});

const segments = computed(() => parseMarkdownSegments(props.content || '', props.isStreaming));

function renderText(text: string) {
  // 透传流式标志：show_html 卡片在流式期间需要 partial 渲染（实时渲染/渲染中占位）
  // enableCitations：仅 assistant 正文开启引用渲染，其他场景【cite:】按原文显示
  return renderMarkdownText(text, props.isStreaming, props.enableCitations);
}

// 分段级分块缓存：segments 每个 token 都是新对象，按 key 缓存避免对
// 已完成分段重复做 HTML 解析/分块；内容变化时才重新分块。
const segmentChunkCache = new Map<string, { content: string; chunks: HtmlChunk[] }>();
const SEGMENT_CHUNK_CACHE_LIMIT = 40;

function chunksForSegment(segment: MarkdownSegment): HtmlChunk[] {
  const cached = segmentChunkCache.get(segment.key);
  if (cached && cached.content === segment.content) {
    return cached.chunks;
  }
  const chunks = chunkRenderedHtml(renderText(segment.content));
  segmentChunkCache.set(segment.key, { content: segment.content, chunks });
  if (segmentChunkCache.size > SEGMENT_CHUNK_CACHE_LIMIT) {
    const firstKey = segmentChunkCache.keys().next().value;
    if (firstKey !== undefined) {
      segmentChunkCache.delete(firstKey);
    }
  }
  return chunks;
}

function renderMath() {
  nextTick(() => {
    if (containerRef.value) {
      renderMathBlocks(containerRef.value);
      onMathRendered();
    }
  });
}

onMounted(renderMath);
onUpdated(renderMath);
watch(() => props.content, renderMath, { immediate: true });

// 行内引用：渲染后扫描 chip 占位 span，填充内容并接管交互。
// chip 在输出瞬间即解析（工具结果查表 / file token 自解析）；
// citationsFinal 到达时做权威裁决（移除无效 chip）与富化。
function enhanceCitations() {
  if (!props.enableCitations) return;
  nextTick(() => {
    if (containerRef.value) {
      enhanceCitationChips(containerRef.value, props.citations, { final: props.citationsFinal });
    }
  });
}

onMounted(enhanceCitations);
onUpdated(enhanceCitations);
watch(() => [props.citations, props.citationsFinal], enhanceCitations);
</script>

<style scoped>
.markdown-renderer {
  display: contents;
}

.markdown-text-segment {
  display: block;
}

/* 分块容器只作 v-html 载体，不产生任何布局盒，
   保证分块不改变原有排版（内外边距/相邻选择器表现与单 v-html 一致） */
.md-html-chunk {
  display: contents;
}

.markdown-text-segment > *:first-child {
  margin-top: 0;
}

.markdown-text-segment > *:last-child {
  margin-bottom: 0;
}

/* 列表基础样式（渲染器自有，不依赖外部容器类名）：
   全局 reset 清掉了 ul/ol 默认 padding，此处置回标准缩进，
   保证 MarkdownRenderer 在任何容器内（聊天/批准弹窗/预览等）表现一致：
   标记在左、文字缩进靠右 */
.markdown-text-segment :deep(ul),
.markdown-text-segment :deep(ol) {
  padding-left: 24px;
  margin-bottom: 12px;
}

.markdown-text-segment :deep(ul) {
  list-style-type: disc;
}

.markdown-text-segment :deep(ul ul) {
  list-style-type: circle;
  margin-top: 6px;
}

.markdown-text-segment :deep(ol) {
  list-style-type: decimal;
}

.markdown-text-segment :deep(li) {
  margin-bottom: 6px;
  line-height: 1.6;
}
</style>
