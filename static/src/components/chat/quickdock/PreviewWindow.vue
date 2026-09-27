<template>
  <section v-if="targets.length" class="qd-window">
    <header class="qd-window__header">
      <svg class="qd-window__icon" viewBox="0 0 16 16" fill="none">
        <path
          d="M8 3.5c-3 0-5.5 2.4-6.5 4.5 1 2.1 3.5 4.5 6.5 4.5s5.5-2.4 6.5-4.5c-1-2.1-3.5-4.5-6.5-4.5z"
          stroke="currentColor"
          stroke-width="1.3"
          stroke-linejoin="round"
        />
        <circle cx="8" cy="8" r="1.7" stroke="currentColor" stroke-width="1.3" />
      </svg>
      <span class="qd-window__title">{{ $t('quickdock.previewWindowTitle') }}</span>
      <span class="qd-window__counter">{{ targets.length }}</span>
    </header>
    <ul class="qd-list">
      <li
        v-for="row in targets"
        :key="keyOf(row)"
        class="qd-file-item"
        :class="{ 'is-active': activeKey === keyOf(row) }"
        :title="row.type === 'server' ? row.url : row.path"
        @click="openRow(row)"
      >
        <button
          class="qd-row-menu-btn"
          :title="$t('quickdock.previewRemove')"
          @click.stop="removeRow(row)"
        >
          <svg viewBox="0 0 16 16">
            <path
              d="M4.5 4.5l7 7M11.5 4.5l-7 7"
              stroke="currentColor"
              stroke-width="1.3"
              stroke-linecap="round"
            />
          </svg>
        </button>
        <svg v-if="row.type === 'server'" class="qd-row-type-icon" viewBox="0 0 16 16" fill="none">
          <circle cx="8" cy="8" r="5.5" stroke="currentColor" stroke-width="1.3" />
          <path
            d="M2.5 8h11M8 2.5c-3.5 3.5-3.5 7.5 0 11M8 2.5c3.5 3.5 3.5 7.5 0 11"
            stroke="currentColor"
            stroke-width="1.3"
          />
        </svg>
        <svg v-else class="qd-row-type-icon" viewBox="0 0 16 16" fill="none">
          <path
            d="M4.5 2.5h4.5l2.5 2.5v8.5h-7z"
            stroke="currentColor"
            stroke-width="1.3"
            stroke-linejoin="round"
          />
          <path
            d="M9 2.5v2.5h2.5"
            stroke="currentColor"
            stroke-width="1.3"
            stroke-linejoin="round"
          />
        </svg>
        <span class="qd-file-name">{{ row.label }}</span>
      </li>
    </ul>
  </section>
</template>

<script setup lang="ts">
import { storeToRefs } from 'pinia';
import { usePreviewStore, previewTargetKey, type PreviewTarget } from '@/stores/preview';

/**
 * 预览窗口（Quick Dock 第六项）
 * 本对话检测到的可预览目标：dev server（命令/输出/模型输出捕获）+ HTML 文件。
 * 行结构：[×] 标签；点击行 → 右侧预览面板；× → 从列表移除（同步后端）。
 */

const preview = usePreviewStore();
const { targets, activeKey } = storeToRefs(preview);

function keyOf(row: PreviewTarget): string {
  return previewTargetKey(row);
}

function openRow(row: PreviewTarget) {
  preview.openTarget(keyOf(row));
}

function removeRow(row: PreviewTarget) {
  void preview.removeTarget(keyOf(row));
}
</script>

<style scoped>
.qd-row-type-icon {
  width: 14px;
  height: 14px;
  flex: none;
  color: var(--text-secondary);
}
</style>
