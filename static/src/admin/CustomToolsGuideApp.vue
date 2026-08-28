<template>
  <div class="guide-page">
    <header class="guide-header">
      <div class="title-block">
        <p class="eyebrow">Custom Tools</p>
        <h1>{{ $t('adminCustomTools.guide.title') }}</h1>
        <p class="desc">{{ $t('adminCustomTools.guide.subtitle') }}</p>
      </div>
      <div class="actions">
        <a class="ghost" href="/admin/custom-tools" target="_blank" rel="noopener"
          >{{ $t('adminCustomTools.backToList') }}</a
        >
      </div>
    </header>

    <section class="panel">
      <div v-if="error" class="status error">{{ error }}</div>
      <div v-else-if="loading" class="status">{{ $t('common.loading') }}</div>
      <article v-else class="markdown" v-html="html"></article>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { t } from '@/locales';
import { renderMarkdown } from '@/composables/useMarkdownRenderer';
import guideMd from './custom_tools_guide.md?raw';

const html = ref('');
const loading = ref(true);
const error = ref('');

onMounted(() => {
  try {
    html.value = renderMarkdown(guideMd);
  } catch (e: any) {
    error.value = e.message || t('adminCustomTools.renderFailed');
  } finally {
    loading.value = false;
  }
});
</script>

<style scoped>
:global(html, body) {
  min-height: 100%;
  background:
    radial-gradient(140% 120% at 20% 20%, rgba(239, 229, 214, 0.9), transparent),
    radial-gradient(120% 120% at 80% 0%, rgba(255, 255, 255, 0.9), transparent), #f8f3e8;
}
:global(#custom-tools-guide) {
  min-height: 100vh;
}
.guide-page {
  --paper: #ffffff;
  --border: #e3d8c8;
  --ink: #1a120b;
  --muted: #6f635a;
  --accent: var(--accent);
  --accent-strong: var(--accent-strong);
  --shadow: 0 16px 40px rgba(47, 32, 21, 0.12);

  max-width: 1080px;
  margin: 0 auto;
  padding: 24px 16px 32px;
  font-family:
    'Space Grotesk',
    'Noto Sans SC',
    'Inter',
    system-ui,
    -apple-system,
    BlinkMacSystemFont,
    'Segoe UI',
    sans-serif;
  color: var(--ink);
}
.guide-header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
  margin-bottom: 18px;
}
.title-block h1 {
  margin: 4px 0;
  color: #2f2015;
}
.title-block .eyebrow {
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--muted);
  font-size: 12px;
  margin: 0;
}
.title-block .desc {
  margin: 6px 0 0;
  color: var(--muted);
}
.actions .ghost {
  padding: 10px 16px;
  border-radius: 12px;
  border: 1px solid var(--border);
  background: #fffdf8;
  color: #4b3626;
  text-decoration: none;
  font-weight: 600;
}
.panel {
  background: var(--paper);
  border: 1px solid var(--border);
  border-radius: 16px;
  box-shadow: var(--shadow);
  padding: 18px;
}
.status {
  color: var(--muted);
}
.status.error {
  color: var(--state-danger);
}
.markdown :global(h1),
.markdown :global(h2),
.markdown :global(h3),
.markdown :global(h4) {
  color: #2f2015;
  margin: 20px 0 10px;
}
.markdown :global(p),
.markdown :global(li),
.markdown :global(code) {
  color: var(--ink);
}
.markdown :global(code) {
  background: #fdf8ef;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 2px 6px;
  font-size: 13px;
}
.markdown :global(pre) {
  background: #0f172a;
  color: #f8fafc;
  border-radius: 10px;
  padding: 14px;
  overflow: auto;
}
.markdown :global(a) {
  color: var(--accent-strong);
}
.markdown :global(blockquote) {
  border-left: 3px solid var(--border);
  padding-left: 12px;
  color: var(--muted);
}
.markdown :global(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 12px 0;
}
.markdown :global(th),
.markdown :global(td) {
  border: 1px solid var(--border);
  padding: 8px 10px;
}
.markdown :global(ul) {
  padding-left: 20px;
}
</style>
