<template>
  <transition name="plan-approval-fade" appear>
    <div v-if="visible && current" class="plan-approval-overlay">
      <section class="plan-approval-card" role="dialog" aria-modal="true" aria-label="计划待批准">
        <header class="plan-approval-windowbar">
          <div class="plan-approval-window-title">计划待批准</div>
        </header>

        <header class="plan-approval-header">
          <div class="plan-approval-title-block">
            <p v-if="current.summary" class="plan-approval-summary">{{ current.summary }}</p>
            <p class="plan-approval-file" :title="current.plan_file">
              <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <path d="M14 2v6h6" />
              </svg>
              <span>{{ current.plan_file }}</span>
              <span v-if="current.plan_content_truncated" class="plan-approval-truncated">（内容过长，已截断预览，完整内容见文件）</span>
            </p>
          </div>
        </header>

        <div class="plan-approval-body">
          <MarkdownRenderer :content="current.plan_content || ''" />
        </div>

        <div class="plan-approval-comment">
          <textarea
            v-model="comment"
            class="plan-approval-textarea"
            rows="2"
            placeholder="意见（可选）：批准时作为补充要求，拒绝时说明需要调整的方向…"
            spellcheck="false"
            :disabled="submitting"
            @keydown.meta.enter.prevent="approve"
            @keydown.ctrl.enter.prevent="approve"
          ></textarea>
        </div>

        <footer class="plan-approval-footer">
          <button
            type="button"
            class="plan-approval-btn ghost"
            :disabled="submitting"
            title="拒绝这份计划，AI 将根据你的意见修订后重新提交"
            @click="reject"
          >
            拒绝
          </button>
          <div class="plan-approval-spacer"></div>
          <button
            type="button"
            class="plan-approval-btn primary"
            :disabled="submitting"
            title="批准计划并切换到执行模式开始实施"
            @click="approve"
          >
            {{ submitting ? '提交中…' : '批准并执行' }}
          </button>
        </footer>
      </section>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import MarkdownRenderer from '../chat/MarkdownRenderer.vue';

const props = defineProps<{
  visible: boolean;
  approvals: Array<any>;
  submittingIds?: string[];
}>();

const emit = defineEmits<{
  (event: 'submit', payload: { approval_id: string; approved: boolean; comment: string }): void;
}>();

// 计划批准一次只处理一份（plan 模式下模型阻塞等待，天然串行）；
// 若并发出现多份，按创建时间取最早的一份，解决后 resolved 事件会推进下一份。
const current = computed(() => {
  const list = Array.isArray(props.approvals) ? props.approvals : [];
  return list.length ? list[0] : null;
});

const comment = ref('');

watch(
  () => current.value?.approval_id,
  () => {
    comment.value = '';
  }
);

const submitting = computed(() => {
  const ids = props.submittingIds || [];
  const id = String(current.value?.approval_id || '');
  return ids.length > 0 && (!id || ids.includes(id));
});

function approve() {
  const id = String(current.value?.approval_id || '').trim();
  if (!id || submitting.value) return;
  emit('submit', { approval_id: id, approved: true, comment: comment.value.trim() });
}

function reject() {
  const id = String(current.value?.approval_id || '').trim();
  if (!id || submitting.value) return;
  emit('submit', { approval_id: id, approved: false, comment: comment.value.trim() });
}
</script>

<style scoped>
.plan-approval-overlay {
  position: fixed;
  inset: 0;
  z-index: 1300;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: var(--overlay-scrim);
}

.plan-approval-card {
  position: relative;
  width: min(860px, calc(100vw - 36px));
  max-height: min(680px, calc(100vh - 36px));
  max-height: min(680px, calc(100dvh - 36px));
  display: flex;
  flex-direction: column;
  color: var(--text-primary);
  background: var(--surface-card);
  border: 1px solid var(--border-strong);
  border-radius: 16px;
  overflow: hidden;
}

.plan-approval-windowbar {
  height: 40px;
  flex: 0 0 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-bottom: 1px solid var(--border-default);
}

.plan-approval-window-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
  user-select: none;
}

.plan-approval-header {
  padding: 16px 22px 0;
  flex-shrink: 0;
}

.plan-approval-title-block {
  min-width: 0;
}

.plan-approval-summary {
  margin: 0 0 8px;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.5;
}

.plan-approval-file {
  margin: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.plan-approval-file svg {
  flex: 0 0 auto;
}

.plan-approval-truncated {
  flex: 0 0 auto;
}

.plan-approval-body {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 14px 22px 18px;
  font-size: 13px;
  line-height: 1.65;
}

.plan-approval-comment {
  flex-shrink: 0;
  padding: 0 22px 12px;
}

.plan-approval-textarea {
  width: 100%;
  resize: none;
  min-height: 56px;
  max-height: 120px;
  overflow-y: auto;
  border: 1px solid var(--border-default);
  border-radius: 10px;
  padding: 9px 11px;
  color: var(--text-primary);
  background: var(--surface-soft);
  outline: none;
  font: inherit;
  font-size: 13px;
  line-height: 1.5;
  box-sizing: border-box;
}

.plan-approval-textarea:focus {
  border-color: var(--border-strong);
}

.plan-approval-footer {
  height: 56px;
  flex: 0 0 56px;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 18px;
  border-top: 1px solid var(--border-default);
}

.plan-approval-spacer {
  flex: 1 1 auto;
}

.plan-approval-btn {
  min-width: 72px;
  height: 34px;
  border-radius: 9px;
  padding: 0 14px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  border: 1px solid var(--border-strong);
}

.plan-approval-btn.ghost {
  background: transparent;
  color: var(--text-secondary);
}

.plan-approval-btn.ghost:hover:not(:disabled) {
  background: var(--hover-bg);
  color: var(--text-primary);
}

.plan-approval-btn.primary {
  background: var(--accent);
  border-color: var(--accent-strong);
  color: var(--on-accent);
}

.plan-approval-btn.primary:hover:not(:disabled) {
  background: var(--accent-hover);
}

.plan-approval-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.plan-approval-fade-enter-active,
.plan-approval-fade-leave-active {
  transition: opacity 0.16s ease;
}

.plan-approval-fade-enter-active .plan-approval-card,
.plan-approval-fade-leave-active .plan-approval-card {
  transition: transform 0.16s ease;
}

.plan-approval-fade-enter-from,
.plan-approval-fade-leave-to {
  opacity: 0;
}

.plan-approval-fade-enter-from .plan-approval-card,
.plan-approval-fade-leave-to .plan-approval-card {
  transform: translateY(6px) scale(0.99);
}

@media (max-width: 620px) {
  .plan-approval-overlay {
    padding: 12px;
  }

  .plan-approval-card {
    width: 100%;
    max-height: calc(100vh - 24px);
    max-height: calc(100dvh - 24px);
  }
}
</style>
