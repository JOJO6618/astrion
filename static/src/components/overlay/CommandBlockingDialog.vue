<template>
  <transition name="command-blocking-fade">
    <div v-if="store.dialogOpen" class="overlay-backdrop" @click.self="store.closeDialog()">
      <div
        class="overlay-card"
        role="dialog"
        aria-modal="true"
        :aria-label="$t('commandBlocking.title')"
        @keydown.esc.stop.prevent="store.closeDialog()"
      >
        <div class="overlay-header">
          <h3>{{ $t('commandBlocking.title') }}</h3>
          <CloseButton :label="$t('common.close')" @click="store.closeDialog()" />
        </div>

        <p class="status-line">
          <span class="status-dot" :class="{ on: store.enabled }" aria-hidden="true"></span>
          <span>{{
            store.enabled ? $t('commandBlocking.statusOn') : $t('commandBlocking.statusOff')
          }}</span>
        </p>

        <p class="hint">{{ $t('commandBlocking.sharedHint') }}</p>
        <p class="hint">{{ $t('commandBlocking.matchHint') }}</p>

        <template v-if="store.loading">
          <p class="hint loading-line">{{ $t('commandBlocking.loading') }}</p>
        </template>
        <template v-else>
          <label class="rules-label" for="command-blocking-rules">{{
            $t('commandBlocking.rulesLabel')
          }}</label>
          <textarea
            id="command-blocking-rules"
            v-model="store.draft"
            class="rules-input"
            :placeholder="$t('commandBlocking.rulesPlaceholder')"
            :disabled="store.loadFailed || store.saving"
            spellcheck="false"
          ></textarea>

          <div class="recommend-row">
            <button
              type="button"
              class="btn btn-muted"
              :disabled="store.loadFailed || store.saving || !store.pendingRecommended.length"
              @click="store.applyRecommended()"
            >
              {{ $t('commandBlocking.useRecommended') }}
            </button>
            <span class="hint recommend-hint">
              {{
                store.recommendedRules.length
                  ? $t('commandBlocking.recommendedHint')
                  : $t('commandBlocking.noRecommended')
              }}
            </span>
          </div>
        </template>

        <p v-if="store.loadFailed" class="error-line" role="alert">
          {{ $t('commandBlocking.loadFailedHint') }}
          <span v-if="store.error" class="error-detail">{{ store.error }}</span>
        </p>
        <p v-else-if="store.error" class="error-line" role="alert">
          {{ $t('commandBlocking.saveFailed') }}
          <span class="error-detail">{{ store.error }}</span>
        </p>

        <div class="actions">
          <button
            type="button"
            class="btn btn-muted"
            :disabled="store.saving"
            @click="store.closeDialog()"
          >
            {{ $t('common.cancel') }}
          </button>
          <button
            type="button"
            class="btn"
            :disabled="store.loading || store.saving || store.loadFailed"
            @click="store.saveRules()"
          >
            {{ $t('common.save') }}
          </button>
        </div>
      </div>
    </div>
  </transition>
</template>

<script setup lang="ts">
import CloseButton from '@/components/common/CloseButton.vue';
import { useCommandBlockingStore } from '@/stores/commandBlocking';

defineOptions({ name: 'CommandBlockingDialog' });

// 状态全部由 store 驱动：窗口打开/草稿/加载/保存/错误，App.vue 统一挂载，
// 设置路由（settingsRoute）与聊天界面共用同一实例。
const store = useCommandBlockingStore();
</script>

<style scoped>
.overlay-backdrop {
  position: fixed;
  inset: 0;
  background: var(--overlay-scrim);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 24px;
}

/* 不透明实体卡片：中性语义 token，无发光/光晕阴影 */
.overlay-card {
  width: min(560px, 92vw);
  max-width: 560px;
  max-height: min(80vh, 640px);
  overflow-y: auto;
  scrollbar-width: none;
  background: var(--surface-card);
  border: 1px solid var(--border-default);
  border-radius: 12px;
  padding: 16px;
  color: var(--text-primary);
  box-shadow: none;
}

.overlay-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.overlay-header h3 {
  margin: 0;
  font-size: 16px;
  color: var(--text-primary);
}

.status-line {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 10px 0;
  font-size: 13px;
  color: var(--text-primary);
}

.status-dot {
  width: 8px;
  height: 8px;
  flex: 0 0 8px;
  border-radius: 50%;
  background: var(--text-muted);
}

.status-dot.on {
  background: var(--state-success);
}

.hint {
  font-size: 12px;
  color: var(--text-secondary);
  margin: 0 0 8px 0;
  line-height: 1.5;
}

.loading-line {
  padding: 24px 0;
  text-align: center;
}

.rules-label {
  display: block;
  font-size: 13px;
  font-weight: 550;
  color: var(--text-primary);
  margin: 4px 0 6px;
}

.rules-input {
  width: 100%;
  height: 200px;
  min-height: 180px;
  max-height: 320px;
  scrollbar-width: thin;
  resize: vertical;
  overflow-y: auto;
  border: 1px solid var(--border-default);
  border-radius: 8px;
  background: var(--surface-base);
  color: var(--text-primary);
  padding: 8px 10px;
  font-size: 13px;
  line-height: 1.6;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.rules-input:focus {
  outline: none;
  border-color: var(--text-secondary);
}

.rules-input:disabled {
  opacity: 0.6;
}

.recommend-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 10px;
}

.recommend-hint {
  margin: 0;
  flex: 1 1 auto;
  min-width: 0;
}

.error-line {
  margin: 10px 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--state-danger);
}

.error-detail {
  display: block;
  color: var(--text-secondary);
  word-break: break-all;
}

.actions {
  margin-top: 14px;
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}

.btn {
  border: 1px solid var(--border-default);
  background: var(--accent);
  border-color: var(--accent);
  color: var(--on-accent);
  height: 34px;
  padding: 6px 14px;
  border-radius: 8px;
  font-size: 13px;
  cursor: pointer;
}

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-muted {
  background: transparent;
  border-color: var(--border-default);
  color: var(--text-primary);
}

@media (hover: hover) {
  .btn-muted:hover:not(:disabled) {
    background: var(--hover-bg);
  }
}

.command-blocking-fade-enter-active,
.command-blocking-fade-leave-active {
  transition: opacity 0.2s ease;
}

.command-blocking-fade-enter-from,
.command-blocking-fade-leave-to {
  opacity: 0;
}
</style>
