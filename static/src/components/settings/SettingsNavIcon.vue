<script setup lang="ts">
/**
 * 设置页左导航图标：统一使用 static/icons/ 项目正式图标（Lucide 风格，经 utils/icons 内嵌），
 * CSS mask 渲染（alpha 通道取形），颜色 = currentColor 随主题/选中态自动适配。
 * 命中外框 15×15，与文字基线对齐由导航行 flex 布局保证。
 */
import { computed } from 'vue';
import { iconVar } from '@/utils/icons';

defineOptions({ name: 'SettingsNavIcon' });

const props = defineProps<{ name: string }>();

/** 分区 id → /static/icons/ 文件名（不含扩展名） */
const ICON_FILES: Record<string, string> = {
  usage: 'chart-column',
  storage: 'hard-drive',
  providers: 'cloud',
  models: 'atom',
  'model-pref': 'brain-cog',
  codex: 'codex',
  general: 'settings',
  chat: 'message-circle-more',
  'quick-chat': 'zap',
  workspace: 'folder-open',
  search: 'globe',
  tools: 'wrench',
  mcp: 'mcp-logo',
  context: 'notebook',
  files: 'file',
  voice: 'mic',
  browser: 'browser',
  'sub-agents': 'bot',
  'review-agents': 'eye-check',
  appearance: 'palette',
  admin: 'shield'
};

const iconStyle = computed(() => {
  const src = iconVar(ICON_FILES[props.name] || 'settings');
  return {
    '-webkit-mask-image': src,
    'mask-image': src
  };
});
</script>

<template>
  <span class="settings-nav-icon" :style="iconStyle" aria-hidden="true"></span>
</template>

<style scoped>
.settings-nav-icon {
  width: 15px;
  height: 15px;
  flex-shrink: 0;
  display: block;
  background-color: currentColor;
  -webkit-mask-repeat: no-repeat;
  mask-repeat: no-repeat;
  -webkit-mask-position: center;
  mask-position: center;
  -webkit-mask-size: contain;
  mask-size: contain;
}
</style>
