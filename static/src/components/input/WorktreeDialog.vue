<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import CloseButton from '@/components/common/CloseButton.vue';
import FancyCheck from '@/components/common/FancyCheck.vue';
import GitSelect from '@/components/git/GitSelect.vue';
import { t } from '@/locales';
import { useGitWorktreeStore, type WorktreeTarget } from '@/stores/gitWorktree';

/**
 * 对话级 Git worktree 切换弹窗（屏幕居中）。
 * 常驻挂在输入栏根节点，用 open 控制显隐；不依赖项目/分支弹出菜单的存活，
 * 因此菜单因其它原因收起时弹窗不会跟着消失。
 */
const props = defineProps<{
  open: boolean;
  conversationId: string | null;
  workspaceId: string;
  busy: boolean;
}>();
const emit = defineEmits<{ (event: 'close'): void; (event: 'changed'): void }>();

const store = useGitWorktreeStore();
const panel = ref<HTMLElement | null>(null);
const mode = ref('main');
const branch = ref('');
const startRef = ref('HEAD');
const includeUncommitted = ref(false);

const options = computed(() => [
  { value: 'main', label: t('input.worktreeMain') },
  { value: 'create', label: t('input.worktreeCreate') },
  ...store.worktrees
    .filter((tree) => !tree.is_main && !tree.unavailable && tree.branch)
    .map((tree) => ({ value: `enter:${tree.branch}`, label: tree.branch }))
]);
const disabled = computed(() => props.busy || store.busy);
const valid = computed(
  () => mode.value !== 'create' || (!!branch.value.trim() && !!startRef.value.trim())
);

watch(
  () => props.open,
  async (open) => {
    if (!open) return;
    store.error = '';
    mode.value = store.active && store.branch ? `enter:${store.branch}` : 'main';
    if (store.draftTarget?.action === 'create') {
      mode.value = 'create';
      branch.value = store.draftTarget.branch_name;
      startRef.value = store.draftTarget.start_ref;
      includeUncommitted.value = store.draftTarget.include_uncommitted;
    }
    await nextTick();
    panel.value?.focus();
    await store.refresh(props.conversationId, props.workspaceId);
  }
);

watch(
  () => [props.conversationId, props.workspaceId],
  () => {
    if (props.open) emit('close');
  }
);

function close() {
  if (store.busy) return;
  emit('close');
}

async function save() {
  if (disabled.value || !valid.value) return;
  let target: WorktreeTarget | null = null;
  if (mode.value === 'create')
    target = {
      action: 'create',
      branch_name: branch.value.trim(),
      start_ref: startRef.value.trim(),
      include_uncommitted: includeUncommitted.value
    };
  else if (mode.value.startsWith('enter:'))
    target = { action: 'enter', branch_name: mode.value.slice(6) };
  if (await store.select(target, props.conversationId, props.workspaceId)) {
    emit('close');
    emit('changed');
  }
}

function trapFocus(event: KeyboardEvent) {
  if (event.key !== 'Tab') return;
  const nodes = Array.from(
    panel.value?.querySelectorAll<HTMLElement>(
      'button:not(:disabled), input:not(:disabled), [tabindex="0"]'
    ) || []
  );
  const first = nodes[0];
  const last = nodes[nodes.length - 1];
  if (
    event.shiftKey &&
    (document.activeElement === first || document.activeElement === panel.value)
  ) {
    event.preventDefault();
    last?.focus();
  } else if (
    !event.shiftKey &&
    (document.activeElement === last || document.activeElement === panel.value)
  ) {
    event.preventDefault();
    first?.focus();
  }
}
</script>

<template>
  <Teleport to="body">
    <!-- @click.stop：阻断到 document 的冒泡，避免点弹窗时收起背后的项目/分支菜单 -->
    <div v-if="open && store.enabled" class="worktree-overlay" @click.stop @click.self="close">
      <section
        ref="panel"
        class="worktree-dialog"
        role="dialog"
        aria-modal="true"
        :aria-label="$t('input.worktreeSwitch')"
        tabindex="-1"
        @keydown.esc.stop="close"
        @keydown="trapFocus"
      >
        <div class="worktree-header">
          <span class="worktree-title">{{ $t('input.worktreeSwitch') }}</span>
          <CloseButton :label="$t('common.close')" :disabled="store.busy" @click="close" />
        </div>
        <form class="worktree-form" @submit.prevent="save">
          <div class="worktree-body">
            <!-- 不能用 label 包裹：label 会把点击再转发给内部按钮，下拉会开了立刻又关 -->
            <div class="worktree-field">
              <span class="worktree-label">{{ $t('input.worktreeTarget') }}</span>
              <GitSelect
                v-model="mode"
                :options="options"
                :label="$t('input.worktreeTarget')"
                :disabled="disabled"
              />
            </div>
            <template v-if="mode === 'create'">
              <label class="worktree-field">
                <span class="worktree-label">{{ $t('input.worktreeBranch') }}</span>
                <input
                  v-model="branch"
                  type="text"
                  :disabled="disabled"
                  autocomplete="off"
                  spellcheck="false"
                  required
                />
              </label>
              <label class="worktree-field">
                <span class="worktree-label">{{ $t('input.worktreeStart') }}</span>
                <input
                  v-model="startRef"
                  type="text"
                  :disabled="disabled"
                  autocomplete="off"
                  spellcheck="false"
                  required
                />
              </label>
              <button
                type="button"
                class="worktree-carry"
                role="switch"
                :aria-checked="includeUncommitted"
                :disabled="disabled"
                @click="includeUncommitted = !includeUncommitted"
              >
                <FancyCheck :checked="includeUncommitted" :size="18" />
                <span>{{ $t('input.worktreeCarry') }}</span>
              </button>
            </template>
            <p v-if="store.error" class="worktree-error" role="alert">{{ store.error }}</p>
          </div>
          <div class="worktree-actions">
            <button type="button" class="worktree-btn" :disabled="store.busy" @click="close">
              {{ $t('common.cancel') }}
            </button>
            <button
              type="submit"
              class="worktree-btn worktree-btn--primary"
              :disabled="disabled || !valid"
            >
              {{ $t('common.save') }}
            </button>
          </div>
        </form>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.worktree-overlay {
  position: fixed;
  inset: 0;
  z-index: 1400;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: var(--overlay-scrim);
}

/* 弹窗本身不滚动：内容固定且短，内嵌 GitSelect 下拉需要能溢出到弹窗外 */
.worktree-dialog {
  width: 100%;
  max-width: 420px;
  display: flex;
  flex-direction: column;
  background: var(--surface-raised);
  border: 1px solid var(--border-default);
  border-radius: 10px;
  box-shadow: var(--shadow-card);
  outline: none;
}

.worktree-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  height: 48px;
  padding: 0 16px;
  border-bottom: 1px solid var(--border-default);
}

.worktree-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.worktree-form {
  display: flex;
  flex-direction: column;
}

.worktree-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 16px;
}

.worktree-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.worktree-label {
  font-size: 12px;
  line-height: 18px;
  color: var(--text-secondary);
}

.worktree-field input[type='text'],
.worktree-dialog :deep(.git-select__trigger) {
  height: 34px;
  min-width: 0;
  padding: 0 10px;
  border: 1px solid var(--border-strong);
  border-radius: 7px;
  background: var(--surface-soft);
  color: var(--text-primary);
  font: inherit;
  font-size: 13px;
}

.worktree-field input[type='text']:focus-visible {
  outline: none;
  border-color: var(--accent);
}

.worktree-carry {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 32px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--text-secondary);
  font: inherit;
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}

.worktree-carry:disabled {
  opacity: 0.5;
  cursor: default;
}

.worktree-carry span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.worktree-error {
  margin: 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--state-danger);
  overflow-wrap: anywhere;
}

.worktree-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid var(--border-default);
}

.worktree-btn {
  height: 32px;
  padding: 0 14px;
  border: 1px solid var(--border-default);
  border-radius: 7px;
  background: var(--surface-soft);
  color: var(--text-primary);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.worktree-btn:hover:not(:disabled) {
  background: var(--hover-bg);
}

.worktree-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.worktree-btn--primary {
  border-color: var(--accent);
  background: var(--accent);
  color: var(--on-accent);
}

.worktree-btn--primary:hover:not(:disabled) {
  background: var(--accent-hover);
  border-color: var(--accent-hover);
}
</style>
