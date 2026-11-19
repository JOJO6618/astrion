<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import UiDialog from '@/components/ui/UiDialog.vue';
import UiDialogButton from '@/components/ui/UiDialogButton.vue';
import UiSelect from '@/components/ui/UiSelect.vue';
import UiSwitch from '@/components/ui/UiSwitch.vue';
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
</script>

<template>
  <UiDialog :open="open" :title="$t('input.worktreeSwitch')" :busy="store.busy" @close="close">
    <form id="worktree-form" class="worktree-form" @submit.prevent="save">
      <div class="ui-dialog-field">
        <span class="ui-dialog-label">{{ $t('input.worktreeTarget') }}</span>
        <UiSelect
          v-model="mode"
          :options="options"
          :label="$t('input.worktreeTarget')"
          :disabled="disabled"
        />
      </div>
      <template v-if="mode === 'create'">
        <label class="ui-dialog-field">
          <span class="ui-dialog-label">{{ $t('input.worktreeBranch') }}</span>
          <input
            v-model="branch"
            class="ui-dialog-input"
            type="text"
            :disabled="disabled"
            autocomplete="off"
            spellcheck="false"
            required
          />
        </label>
        <label class="ui-dialog-field">
          <span class="ui-dialog-label">{{ $t('input.worktreeStart') }}</span>
          <input
            v-model="startRef"
            class="ui-dialog-input"
            type="text"
            :disabled="disabled"
            autocomplete="off"
            spellcheck="false"
            required
          />
        </label>
        <label class="ui-dialog-switch-row">
          <span>{{ $t('input.worktreeCarry') }}</span>
          <input v-model="includeUncommitted" type="checkbox" :disabled="disabled" />
          <UiSwitch decorative :checked="includeUncommitted" :disabled="disabled" />
        </label>
      </template>
      <div v-if="mode === 'path'" class="ui-dialog-field">
        <label class="ui-dialog-label" for="worktree-path-input">{{
          $t('input.worktreePath')
        }}</label>
        <!-- 「在文件管理器中选择」属于输入框本身，放在框内尾部（DESIGN.md §7 下方长输入框） -->
        <div class="worktree-path" :class="{ 'is-disabled': disabled }">
          <input
            id="worktree-path-input"
            v-model="manualPath"
            type="text"
            :disabled="disabled"
            :placeholder="$t('input.worktreePathPlaceholder')"
            autocomplete="off"
            spellcheck="false"
            required
          />
          <button
            type="button"
            class="worktree-pick"
            :disabled="disabled || picking"
            @click="pickFolder"
          >
            {{ picking ? $t('input.worktreePickingFolder') : $t('input.worktreePickFolder') }}
          </button>
        </div>
        <p v-if="pickError" class="ui-dialog-error" role="alert">{{ pickError }}</p>
        <p v-else-if="pathChecking" class="ui-dialog-note">
          {{ $t('input.worktreeCheckingPath') }}
        </p>
        <p v-else-if="pathInfo && !pathInfo.valid" class="ui-dialog-error" role="alert">
          {{ pathInfo.error }}
        </p>
        <p v-else-if="pathInfo?.valid" class="ui-dialog-note">
          {{
            $t('input.worktreePathReady', {
              target:
                pathInfo.branch ||
                $t('input.worktreeDetached', { sha: (pathInfo.head || '').slice(0, 7) })
            })
          }}
        </p>
      </div>
      <p v-if="store.error" class="ui-dialog-error" role="alert">{{ store.error }}</p>
    </form>
    <template #actions>
      <UiDialogButton :disabled="store.busy" @click="close">
        {{ $t('common.cancel') }}
      </UiDialogButton>
      <UiDialogButton
        type="submit"
        form="worktree-form"
        variant="primary"
        :disabled="disabled || !valid"
      >
        {{ $t('common.save') }}
      </UiDialogButton>
    </template>
  </UiDialog>
</template>

<style scoped>
.worktree-form {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* 输入框外观在外层：内部 input 透明，尾部文字操作随文案变宽 */
.worktree-path {
  height: 40px;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 0 6px 0 12px;
  border: 1px solid transparent;
  border-radius: 10px;
  background: var(--surface-soft);
  transition: border-color 140ms ease;
}

.worktree-path:focus-within {
  border-color: var(--border-strong);
}

.worktree-path.is-disabled {
  opacity: 0.5;
}

.worktree-path input {
  min-width: 0;
  flex: 1;
  height: 100%;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--text-primary);
  font: inherit;
  font-size: 14px;
  outline: none;
}

.worktree-path input::placeholder {
  color: var(--text-tertiary);
}

/* 28 高，距框上下右各 6 */
.worktree-pick {
  height: 28px;
  flex: none;
  padding: 0 8px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--text-secondary);
  font: inherit;
  font-size: 13px;
  white-space: nowrap;
  cursor: pointer;
  transition:
    background-color 140ms ease,
    color 140ms ease;
}

.worktree-pick:not(:disabled):hover {
  background: var(--hover-bg);
  color: var(--text-primary);
}

.worktree-pick:disabled {
  cursor: default;
}
</style>
