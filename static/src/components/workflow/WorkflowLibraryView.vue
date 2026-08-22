<template>
  <div class="wf-library">
    <header class="wf-library__topbar">
      <button type="button" class="wf-btn wf-btn--ghost" @click="$emit('exit')">
        <span class="icon" :style="iconSrc(ICONS.arrowLeft)" aria-hidden="true"></span>
        <span>返回对话</span>
      </button>
    </header>

    <div class="wf-library__container">
      <div class="wf-library__heading">
        <div class="wf-library__title-block">
          <h1 class="wf-library__title">工作流</h1>
          <p class="wf-library__subtitle">
            把一套既定的工作方式、验证方式与结束方式存为流程，在对话中激活复用。
          </p>
        </div>
        <button type="button" class="wf-btn wf-btn--primary" @click="$emit('create')">
          <span class="icon" :style="iconSrc(ICONS.plus)" aria-hidden="true"></span>
          <span>新建工作流</span>
        </button>
      </div>

      <div v-if="workflows.length" class="wf-library__list">
        <div
          v-for="wf in workflows"
          :key="wf.name"
          class="wf-row"
          @click="$emit('open', wf.name)"
        >
          <div class="wf-row__icon">
            <span class="icon icon-md" :style="iconSrc(ICONS.workflow)" aria-hidden="true"></span>
          </div>
          <div class="wf-row__main">
            <div class="wf-row__name">{{ wf.name }}</div>
            <div class="wf-row__desc">{{ wf.description || '（无描述）' }}</div>
          </div>
          <div class="wf-row__meta">
            <span>{{ wf.nodeCount }} 个节点</span>
            <span class="wf-row__meta-sep">·</span>
            <span>{{ wf.source === 'builtin' ? '内置' : '用户' }}</span>
            <span class="wf-row__meta-sep">·</span>
            <span>{{ wf.updatedAt }}</span>
          </div>
          <div class="wf-row__actions" @click.stop>
            <button
              type="button"
              class="wf-icon-btn"
              aria-label="编辑"
              title="编辑"
              @click="$emit('open', wf.name)"
            >
              <span class="icon" :style="iconSrc(ICONS.pencil)" aria-hidden="true"></span>
            </button>
            <button
              type="button"
              class="wf-icon-btn"
              aria-label="复制"
              title="复制"
              @click="$emit('duplicate', wf.name)"
            >
              <span class="icon" :style="iconSrc(ICONS.copy)" aria-hidden="true"></span>
            </button>
            <button
              v-if="confirmingDelete !== wf.name"
              type="button"
              class="wf-icon-btn wf-icon-btn--danger"
              aria-label="删除"
              title="删除"
              @click="confirmingDelete = wf.name"
            >
              <span class="icon" :style="iconSrc(ICONS.trash)" aria-hidden="true"></span>
            </button>
            <button
              v-else
              type="button"
              class="wf-btn wf-btn--danger-confirm"
              @click="onConfirmDelete(wf.name)"
            >
              确认删除
            </button>
          </div>
        </div>
      </div>

      <div v-else class="wf-library__empty">
        <span class="icon icon-xl" :style="iconSrc(ICONS.workflow)" aria-hidden="true"></span>
        <p class="wf-library__empty-title">还没有工作流</p>
        <p class="wf-library__empty-hint">新建一个，或在对话中让 AI 帮你生成后归档。</p>
        <button type="button" class="wf-btn wf-btn--primary" @click="$emit('create')">
          <span class="icon" :style="iconSrc(ICONS.plus)" aria-hidden="true"></span>
          <span>新建工作流</span>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { ICONS } from '@/utils/icons';
import type { WorkflowListItem } from './api';

defineProps<{
  workflows: WorkflowListItem[];
}>();

const emit = defineEmits<{
  (event: 'open', name: string): void;
  (event: 'create'): void;
  (event: 'duplicate', name: string): void;
  (event: 'delete', name: string): void;
  (event: 'exit'): void;
}>();

const confirmingDelete = ref('');

function onConfirmDelete(name: string) {
  emit('delete', name);
  confirmingDelete.value = '';
}

function iconSrc(url: string) {
  return { '--icon-src': `url(${url})` } as Record<string, string>;
}
</script>

<style scoped lang="scss">
.wf-library {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--surface-base);
  overflow: hidden;

  &__topbar {
    display: flex;
    align-items: center;
    height: 48px;
    padding: 0 16px;
    flex-shrink: 0;
  }

  &__container {
    flex: 1;
    width: 100%;
    max-width: 860px;
    margin: 0 auto;
    padding: 24px 24px 48px;
    overflow-y: auto;
  }

  &__heading {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 24px;
  }

  &__title {
    margin: 0;
    font-size: 22px;
    font-weight: 650;
    color: var(--text-primary);
    letter-spacing: 0.01em;
  }

  &__subtitle {
    margin: 6px 0 0;
    font-size: 13px;
    color: var(--text-tertiary);
  }

  &__list {
    border-top: 1px solid var(--border-default);
  }

  &__empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 10px;
    padding: 96px 0;
    color: var(--text-tertiary);
  }

  &__empty-title {
    margin: 0;
    font-size: 15px;
    font-weight: 600;
    color: var(--text-secondary);
  }

  &__empty-hint {
    margin: 0 0 8px;
    font-size: 13px;
  }
}

.wf-row {
  display: flex;
  align-items: center;
  gap: 14px;
  height: 68px;
  padding: 0 12px;
  border-bottom: 1px solid var(--border-default);
  cursor: pointer;
  transition: background-color 0.12s ease;

  &:hover {
    background: var(--hover-bg);
  }

  &__icon {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 38px;
    height: 38px;
    border-radius: 9px;
    background: var(--surface-soft);
    color: var(--text-secondary);
    flex-shrink: 0;
  }

  &__main {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 3px;
  }

  &__name {
    height: 20px;
    line-height: 20px;
    font-size: 14px;
    font-weight: 600;
    color: var(--text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  &__desc {
    height: 17px;
    line-height: 17px;
    font-size: 12px;
    color: var(--text-tertiary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  &__meta {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--text-tertiary);
    flex-shrink: 0;
  }

  &__meta-sep {
    color: var(--text-muted);
  }

  &__actions {
    display: flex;
    align-items: center;
    gap: 2px;
    flex-shrink: 0;
  }
}

/* demo 内共享按钮体系（编辑器页复用同款类名，但 scoped 各自定义） */
.wf-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 12px;
  border: 1px solid var(--border-default);
  border-radius: 8px;
  background: var(--surface-raised);
  color: var(--text-secondary);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
  transition: background-color 0.12s ease, color 0.12s ease;

  .icon {
    --icon-size: 14px;
  }

  &:hover {
    background: var(--hover-bg);
    color: var(--text-primary);
  }

  &--ghost {
    border-color: transparent;
    background: transparent;

    &:hover {
      background: var(--hover-bg);
    }
  }

  &--primary {
    border-color: var(--accent);
    background: var(--accent);
    color: var(--on-accent);

    &:hover {
      background: var(--accent-hover);
      color: var(--on-accent);
    }
  }

  &--danger-confirm {
    border-color: var(--state-danger);
    background: var(--state-danger);
    color: var(--on-accent);

    &:hover {
      background: var(--state-danger-strong);
      color: var(--on-accent);
    }
  }
}

.wf-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  transition: background-color 0.12s ease, color 0.12s ease;

  .icon {
    --icon-size: 15px;
  }

  &:hover {
    background: var(--hover-bg);
    color: var(--text-primary);
  }

  &--danger:hover {
    color: var(--state-danger);
  }
}
</style>
