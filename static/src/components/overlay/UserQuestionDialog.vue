<template>
  <transition name="user-question-fade" appear>
    <div v-if="visible && questions.length" class="user-question-overlay" @click.self="emit('minimize')">
      <section class="user-question-card" role="dialog" aria-modal="true" :aria-label="$t('overlay.userQuestionAriaLabel')">
        <header class="user-question-windowbar">
          <div class="user-question-traffic">
            <CloseButton :label="$t('overlay.minimizeAria')" @click="emit('minimize')" />
          </div>
          <div class="user-question-window-title">{{ $t('overlay.userQuestionWindowTitle') }}</div>
        </header>

        <header class="user-question-header">
          <div class="user-question-title-block">
            <div v-if="questions.length > 1" class="user-question-kicker-row">
              <span class="user-question-kicker">{{ $t('overlay.questionIndex', { current: currentIndex + 1, total: questions.length }) }}</span>
              <span class="user-question-nav" :aria-label="$t('overlay.questionNavAria')">
                <button type="button" :disabled="currentIndex <= 0" @click="go(-1)" :aria-label="$t('overlay.prevQuestionAria')">
                  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5l-5 5 5 5" /></svg>
                </button>
                <button type="button" :disabled="currentIndex >= questions.length - 1" @click="go(1)" :aria-label="$t('overlay.nextQuestionAria')">
                  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M8 5l5 5-5 5" /></svg>
                </button>
              </span>
            </div>
            <h2>{{ currentQuestion.question }}</h2>
          </div>
        </header>

        <div class="user-question-body" :class="{ 'user-question-body--no-options': !currentOptions.length }">
          <p v-if="currentQuestion.context" class="user-question-context">{{ currentQuestion.context }}</p>

          <div v-if="currentOptions.length" class="user-question-options">
            <button
              v-for="option in currentOptions"
              :key="option.id"
              type="button"
              class="user-question-option"
              :class="{ active: currentDraft.selected_option_id === option.id }"
              @click="selectOption(option.id)"
            >
              <span class="user-question-option__label">{{ option.label }}</span>
              <span v-if="option.description" class="user-question-option__desc">{{ option.description }}</span>
            </button>
          </div>

          <div class="user-question-textarea-wrap">
            <textarea
              v-model="currentDraft.text"
              class="user-question-textarea"
              rows="3"
              :placeholder="$t('overlay.answerPlaceholder')"
              spellcheck="false"
              @keydown.meta.enter.prevent="submit"
              @keydown.ctrl.enter.prevent="submit"
            ></textarea>
          </div>
        </div>

        <footer class="user-question-footer">
          <button
            type="button"
            class="user-question-btn ghost"
            :disabled="submitting"
            :title="$t('overlay.dismissTitle')"
            @click="dismiss"
          >
            {{ $t('overlay.dismiss') }}
          </button>
          <div class="user-question-spacer"></div>
          <div class="user-question-actions">
            <button type="button" class="user-question-btn primary" :disabled="!canSubmit || submitting" @click="submit">
              {{ submitting ? $t('overlay.submittingAnswer') : $t('common.ok') }}
            </button>
          </div>
        </footer>
      </section>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { computed, reactive, watch } from 'vue';
import CloseButton from '@/components/common/CloseButton.vue';

const props = defineProps<{
  visible: boolean;
  questions: Array<any>;
  activeIndex: number;
  submittingIds?: string[];
}>();

const emit = defineEmits<{
  (event: 'minimize'): void;
  (event: 'update:active-index', value: number): void;
  (event: 'submit', answers: Array<any>): void;
  (event: 'dismiss', questionId: string): void;
}>();

const drafts = reactive<Record<string, { selected_option_id: string; text: string }>>({});

const questionKey = (question: any, idx: number) => String(question?.question_id || `idx_${idx}`);

watch(
  () => props.questions,
  (items) => {
    (items || []).forEach((question, idx) => {
      const key = questionKey(question, idx);
      if (!drafts[key]) drafts[key] = { selected_option_id: '', text: '' };
    });
  },
  { immediate: true, deep: true }
);

const currentIndex = computed(() => {
  const max = Math.max(0, (props.questions || []).length - 1);
  return Math.min(Math.max(0, Number(props.activeIndex || 0)), max);
});

const currentQuestion = computed(() => props.questions[currentIndex.value] || {});
const currentOptions = computed(() => Array.isArray(currentQuestion.value.options) ? currentQuestion.value.options : []);
const currentKey = computed(() => questionKey(currentQuestion.value, currentIndex.value));
const currentDraft = computed(() => {
  if (!drafts[currentKey.value]) drafts[currentKey.value] = { selected_option_id: '', text: '' };
  return drafts[currentKey.value];
});
const submitting = computed(() => (props.submittingIds || []).length > 0);

const isAnswered = (idx: number) => {
  const q = props.questions[idx];
  const d = drafts[questionKey(q, idx)];
  return !!(d && (d.selected_option_id || d.text.trim()));
};

const canSubmit = computed(() => (props.questions || []).every((_q, idx) => isAnswered(idx)));

function go(delta: number) {
  emit('update:active-index', currentIndex.value + delta);
}

function selectOption(optionId: string) {
  currentDraft.value.selected_option_id = currentDraft.value.selected_option_id === optionId ? '' : optionId;
}

function submit() {
  if (!canSubmit.value || submitting.value) return;
  const answers = (props.questions || []).map((question, idx) => {
    const d = drafts[questionKey(question, idx)] || { selected_option_id: '', text: '' };
    return {
      question_id: question.question_id,
      selected_option_id: d.selected_option_id || undefined,
      text: d.text.trim()
    };
  });
  emit('submit', answers);
}

// 只跳过当前查看的问题，其余问题保持待回答
function dismiss() {
  if (submitting.value) return;
  const questionId = String(currentQuestion.value?.question_id || '').trim();
  if (!questionId) return;
  emit('dismiss', questionId);
}
</script>

<style scoped>
.user-question-overlay {
  position: fixed;
  inset: 0;
  z-index: 1300;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: var(--theme-overlay-scrim);
  backdrop-filter: blur(10px);
}

.user-question-card {
  position: relative;
  width: min(800px, calc(100vw - 36px));
  height: min(520px, calc(100vh - 36px));
  height: min(520px, calc(100dvh - 36px));
  max-height: min(520px, calc(100vh - 36px));
  max-height: min(520px, calc(100dvh - 36px));
  display: flex;
  flex-direction: column;
  color: var(--text-primary);
  background: var(--theme-surface-card);
  border: 1px solid var(--theme-control-border);
  border-radius: 20px;
  overflow: hidden;
}

.user-question-windowbar {
  height: 42px;
  flex: 0 0 42px;
  display: grid;
  grid-template-columns: 56px 1fr 56px;
  align-items: center;
  border-bottom: 1px solid var(--theme-control-border);
  background: color-mix(in srgb, var(--theme-surface-soft) 92%, var(--surface-rail) 8%);
}

.user-question-traffic {
  display: inline-flex;
  gap: 8px;
  padding-left: 16px;
  align-items: center;
}

/* 关闭按钮本体样式在 common/CloseButton.vue（boxed 变体），traffic-dot 红点样式已随统一下线 */

.user-question-window-title {
  text-align: center;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
  user-select: none;
}

.user-question-header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  padding: 20px 22px 0;
  flex-shrink: 0;
}

.user-question-kicker-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 5px;
  width: 100%;
}

.user-question-kicker {
  color: var(--text-secondary);
  font-size: 12px;
}

.user-question-title-block {
  min-width: 0;
  width: 100%;
}

.user-question-header h2 {
  margin: 0;
  font-size: 16px;
  line-height: 1.55;
  font-weight: 650;
  letter-spacing: -0.01em;
  white-space: pre-wrap;
  max-height: min(180px, 30vh);
  overflow-y: auto;
}

.user-question-nav {
  display: inline-flex;
  gap: 4px;
}

.user-question-nav button {
  width: 20px;
  height: 20px;
  border-radius: 6px;
  border: 1px solid var(--theme-control-border);
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  display: grid;
  place-items: center;
}

.user-question-nav svg {
  width: 13px;
  height: 13px;
  stroke-width: 2;
}

.user-question-nav button:hover:not(:disabled) {
  background: var(--hover-bg);
  color: var(--text-primary);
}

.user-question-nav button:disabled {
  opacity: 0.38;
  cursor: not-allowed;
}

.user-question-body {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  scrollbar-width: none;
  -ms-overflow-style: none;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px 22px 18px;
}

.user-question-body::-webkit-scrollbar,
.user-question-textarea::-webkit-scrollbar {
  display: none;
}

.user-question-context {
  margin: 0;
  color: var(--text-secondary);
  line-height: 1.6;
  font-size: 13px;
  white-space: pre-wrap;
  max-height: min(160px, 25vh);
  overflow-y: auto;
}

.user-question-options {
  display: grid;
  gap: 7px;
}

.user-question-option {
  border: 1px solid var(--theme-control-border);
  border-radius: 12px;
  background: transparent;
  color: var(--text-primary);
  padding: 9px 11px;
  text-align: left;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.user-question-option:hover {
  background: var(--hover-bg);
}

.user-question-option.active {
  border-color: var(--theme-control-border-strong);
  background: var(--theme-tab-active);
}

.user-question-option__label {
  font-weight: 600;
  font-size: 13px;
}

.user-question-option__desc {
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.42;
  white-space: pre-wrap;
}

.user-question-textarea-wrap {
  display: flex;
}

.user-question-body--no-options .user-question-textarea-wrap {
  margin-top: auto;
}

.user-question-textarea {
  width: 100%;
  resize: none;
  min-height: 76px;
  max-height: 150px;
  overflow: auto;
  scrollbar-width: none;
  -ms-overflow-style: none;
  border: 1px solid var(--theme-control-border);
  border-radius: 12px;
  padding: 10px 11px;
  color: var(--text-primary);
  background: var(--theme-surface-soft);
  outline: none;
  font: inherit;
  font-size: 13px;
  line-height: 1.5;
  appearance: none;
  -webkit-appearance: none;
}

.user-question-textarea:focus {
  border-color: var(--theme-control-border-strong);
  background: var(--theme-surface-strong);
}

.user-question-footer {
  height: 54px;
  flex: 0 0 54px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 0 18px;
  border-top: 1px solid var(--theme-control-border);
  background: color-mix(in srgb, var(--theme-surface-soft) 92%, var(--surface-rail) 8%);
}

.user-question-spacer {
  flex: 1 1 auto;
}

.user-question-actions {
  display: flex;
  gap: 8px;
}

.user-question-btn {
  min-width: 72px;
  border-radius: 9px;
  padding: 7px 12px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  border: 1px solid var(--theme-control-border-strong);
}

.user-question-btn.ghost {
  background: transparent;
  color: var(--text-secondary);
}

.user-question-btn.ghost:hover {
  background: var(--hover-bg);
  color: var(--text-primary);
}

.user-question-btn.primary {
  background: var(--accent);
  border-color: var(--accent-strong);
  color: var(--on-accent);
}

.user-question-btn.primary:hover:not(:disabled) {
  background: var(--accent-hover);
}

.user-question-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.user-question-fade-enter-active,
.user-question-fade-leave-active {
  transition: opacity 0.16s ease;
}

.user-question-fade-enter-active .user-question-card,
.user-question-fade-leave-active .user-question-card {
  transition: transform 0.16s ease;
}

.user-question-fade-enter-from,
.user-question-fade-leave-to {
  opacity: 0;
}

.user-question-fade-enter-from .user-question-card,
.user-question-fade-leave-to .user-question-card {
  transform: translateY(6px) scale(0.99);
}

@media (max-width: 620px) {
  .user-question-overlay {
    padding: 12px;
  }

  .user-question-card {
    width: 100%;
    max-height: calc(100vh - 24px);
    max-height: calc(100dvh - 24px);
  }
}
</style>
