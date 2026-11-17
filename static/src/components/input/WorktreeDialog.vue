<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import CloseButton from '@/components/common/CloseButton.vue';
import FancyCheck from '@/components/common/FancyCheck.vue';
import UiSelect from '@/components/ui/UiSelect.vue';
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
const manualPath = ref('');
const picking = ref(false);
const pickError = ref('');
const pathInfo = ref<{ valid: boolean; branch?: string; head?: string; error?: string } | null>(
  null
);
const pathChecking = ref(false);
let pathTimer: ReturnType<typeof setTimeout> | undefined;

/** 可作为切换目标的工作树：排除主目录和已失效记录。 */
const treeOptions = computed(() =>
  store.worktrees.filter((tree) => !tree.is_main && !tree.unavailable)
);

const treeLabel = (tree: { branch: string; head?: string }) =>
  tree.branch || t('input.worktreeDetached', { sha: (tree.head || '').slice(0, 7) });

const options = computed(() => [
  { value: 'main', label: t('input.worktreeMain') },
  { value: 'create', label: t('input.worktreeCreate') },
  ...treeOptions.value.map((tree, index) => ({ value: `tree:${index}`, label: treeLabel(tree) })),
  { value: 'path', label: t('input.worktreePickPath') }
]);

const selectedTree = computed(() => {
  const match = /^tree:(\d+)$/.exec(mode.value);
  return match ? treeOptions.value[Number(match[1])] || null : null;
});
const disabled = computed(() => props.busy || store.busy);
const valid = computed(() => {
  if (mode.value === 'create') return !!branch.value.trim() && !!startRef.value.trim();
  if (mode.value === 'path') return !!manualPath.value.trim() && pathInfo.value?.valid === true;
  return true;
});

/** 切换前先问后端：这个路径到底能不能进（不改绑定、不创建任何东西）。 */
async function inspectPath(path: string) {
  pathChecking.value = true;
  try {
    const resp = await fetch('/api/git-worktree/inspect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Astrion-Workspace-Id': props.workspaceId },
      body: JSON.stringify({ path })
    });
    const data = await resp.json().catch(() => ({}));
    pathInfo.value = data?.success
      ? data.data
      : { valid: false, error: String(data?.error || t('input.worktreePickFolderFailed')) };
  } catch (error) {
    pathInfo.value = { valid: false, error: String((error as Error).message || error) };
  } finally {
    pathChecking.value = false;
  }
}

watch(manualPath, (value) => {
  pathInfo.value = null;
  if (mode.value !== 'path') return;
  if (pathTimer) clearTimeout(pathTimer);
  const trimmed = value.trim();
  if (!trimmed) return;
  pathTimer = setTimeout(() => void inspectPath(trimmed), 400);
});

function syncMode() {
  // 工作树列表在 refresh 之后才可用；刷新前后各对一次，避免把当前工作树误显示为主目录。
  if (store.draftTarget?.action === 'create') {
    mode.value = 'create';
    branch.value = store.draftTarget.branch_name;
    startRef.value = store.draftTarget.start_ref;
    includeUncommitted.value = store.draftTarget.include_uncommitted;
    return;
  }
  if (mode.value === 'path' || mode.value === 'create') return;
  if (!store.active || !store.directory) {
    mode.value = 'main';
    return;
  }
  const index = treeOptions.value.findIndex((tree) => tree.directory === store.directory);
  mode.value = index >= 0 ? `tree:${index}` : 'main';
}

watch(
  () => props.open,
  async (open) => {
    if (!open) return;
    store.error = '';
    manualPath.value = '';
    pickError.value = '';
    pathInfo.value = null;
    pathChecking.value = false;
    mode.value = 'main';
    syncMode();
    if (store.draftTarget?.action === 'create') {
      mode.value = 'create';
      branch.value = store.draftTarget.branch_name;
      startRef.value = store.draftTarget.start_ref;
      includeUncommitted.value = store.draftTarget.include_uncommitted;
    }
    await nextTick();
    panel.value?.focus();
    await store.refresh(props.conversationId, props.workspaceId);
    syncMode();
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

async function pickFolder() {
  if (picking.value || disabled.value) return;
  picking.value = true;
  pickError.value = '';
  try {
    const resp = await fetch('/api/project/pick-folder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}'
    });
    const result = await resp.json().catch(() => ({}));
    if (!resp.ok || !result?.success)
      throw new Error(result?.error || t('input.worktreePickFolderFailed'));
    const picked = String(result?.data?.path || '').trim();
    if (picked) manualPath.value = picked;
  } catch (error) {
    pickError.value =
      error instanceof Error ? error.message : String(error || t('input.worktreePickFolderFailed'));
  } finally {
    picking.value = false;
  }
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
  else if (selectedTree.value)
    target = selectedTree.value.branch
      ? { action: 'enter', branch_name: selectedTree.value.branch }
      : { action: 'enter', directory: selectedTree.value.directory };
  else if (mode.value === 'path') target = { action: 'enter', directory: manualPath.value.trim() };
  // 手输路径可能带符号链接（/tmp 与 /private/tmp），以预检结果作为分支／HEAD 权威值。
  const hint =
    mode.value === 'path' && pathInfo.value?.valid
      ? { branch: pathInfo.value.branch || '', head: pathInfo.value.head || '' }
      : undefined;
  if (await store.select(target, props.conversationId, props.workspaceId, hint)) {
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
              <UiSelect
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
            <template v-if="mode === 'path'">
              <label class="worktree-field">
                <span class="worktree-label">{{ $t('input.worktreePath') }}</span>
                <input
                  v-model="manualPath"
                  type="text"
                  :disabled="disabled"
                  :placeholder="$t('input.worktreePathPlaceholder')"
                  autocomplete="off"
                  spellcheck="false"
                  required
                />
              </label>
              <button
                type="button"
                class="worktree-carry"
                :disabled="disabled || picking"
                @click="pickFolder"
              >
                <span>{{
                  picking ? $t('input.worktreePickingFolder') : $t('input.worktreePickFolder')
                }}</span>
              </button>
              <p v-if="pickError" class="worktree-error" role="alert">{{ pickError }}</p>
              <p v-else-if="pathChecking" class="worktree-hint">
                {{ $t('input.worktreeCheckingPath') }}
              </p>
              <p v-else-if="pathInfo && !pathInfo.valid" class="worktree-error" role="alert">
                {{ pathInfo.error }}
              </p>
              <p v-else-if="pathInfo?.valid" class="worktree-hint">
                {{
                  $t('input.worktreePathReady', {
                    target:
                      pathInfo.branch ||
                      $t('input.worktreeDetached', { sha: (pathInfo.head || '').slice(0, 7) })
                  })
                }}
              </p>
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

/* 弹窗本身不滚动：内容固定且短 */
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
.worktree-dialog :deep(.ui-select-trigger) {
  width: 100%;
  max-width: none;
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

.worktree-hint {
  margin: 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--text-secondary);
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
