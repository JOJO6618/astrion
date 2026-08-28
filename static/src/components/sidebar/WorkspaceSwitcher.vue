<template>
  <Teleport to="body">
    <transition name="ws-switcher">
      <div
        v-if="open"
        ref="popoverEl"
        class="ws-switcher"
        :class="{ 'ws-switcher--project': isProject }"
        :style="popoverStyle"
        role="dialog"
        :aria-label="$t(noun)"
        @click.stop
      >
        <div class="ws-switcher__header">
          <span class="ws-switcher__title">{{ $t(noun) }}</span>
          <span class="ws-switcher__count">{{ $t('sidebar.workspaceCount', { n: workspaces.length }) }}</span>
        </div>

        <div class="ws-switcher__list" ref="listEl" @scroll="closeMenu">
          <div
            v-for="item in workspaces"
            :key="item.workspace_id"
            class="ws-row"
            :data-ws-id="item.workspace_id"
            :class="{
              current: item.workspace_id === currentWorkspaceId,
              'menu-open': menuOpenId === item.workspace_id
            }"
            role="option"
            :aria-selected="item.workspace_id === currentWorkspaceId"
            @click="handleRowClick(item)"
          >
            <!-- 删除确认内联 -->
            <div v-if="deletingId === item.workspace_id" class="ws-confirm">
              <div class="ws-confirm-text">
                {{ $t(isProject ? 'sidebar.deleteProjectConfirm' : 'sidebar.deleteWorkspaceConfirm', { label: item.label }) }}
              </div>
              <div class="ws-confirm-actions">
                <button type="button" class="ws-btn ghost" :disabled="busy" @click.stop="deletingId = null">
                  {{ $t('common.cancel') }}
                </button>
                <button type="button" class="ws-btn danger" :disabled="busy" @click.stop="confirmDelete(item)">
                  {{ $t('common.delete') }}
                </button>
              </div>
            </div>

            <template v-else>
              <span class="ws-row-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z" />
                </svg>
              </span>
              <span class="ws-row-text">
                <input
                  v-if="renamingId === item.workspace_id"
                  ref="renameInputEl"
                  v-model="renameDraft"
                  class="ws-rename-input"
                  :disabled="busy"
                  @click.stop
                  @keydown.enter.prevent="commitRename(item)"
                  @keydown.esc.prevent="renamingId = null"
                />
                <span v-else class="ws-row-name">{{ item.label }}</span>
                <span v-if="!isProject" class="ws-row-path">{{ item.path || $t('sidebar.unconfiguredPath') }}</span>
              </span>
              <span class="ws-row-meta">
                <span v-if="item.workspace_id === defaultWorkspaceId" class="ws-tag">{{ $t('sidebar.default') }}</span>
                <button
                  type="button"
                  class="ws-more"
                  :aria-expanded="menuOpenId === item.workspace_id"
                  :aria-label="$t('common.moreActions')"
                  @click.stop="toggleMenu(item)"
                >
                  <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                    <circle cx="5" cy="12" r="1.6" /><circle cx="12" cy="12" r="1.6" /><circle cx="19" cy="12" r="1.6" />
                  </svg>
                </button>
              </span>
            </template>
          </div>
          <div v-if="!workspaces.length" class="ws-empty">
            {{ isProject ? $t('sidebar.noProjects') : $t('sidebar.noWorkspaces') }}
          </div>
        </div>

        <!-- 底部：新建按钮 ⇄ 新建表单（高度动画容器） -->
        <div class="ws-bottom" ref="bottomEl">
          <div v-if="!createOpen" class="ws-footer" :class="{ fading: bottomFading }">
            <button type="button" class="ws-create-btn" :disabled="busy" @click.stop="openCreateForm">
              <span class="ws-create-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M5 12h14" /><path d="M12 5v14" />
                </svg>
              </span>
              <span>{{ $t('sidebar.createNoun', { noun: $t(noun) }) }}</span>
            </button>
          </div>
          <form v-else class="ws-create-form" :class="{ fading: bottomFading }" @submit.prevent="submitCreate">
            <input
              ref="createNameEl"
              v-model="createLabel"
              type="text"
              :placeholder="isProject ? $t('sidebar.projectNamePlaceholder') : $t('sidebar.workspaceNamePlaceholder')"
              autocomplete="off"
              :disabled="createSubmitting"
            />
            <input
              v-if="!isProject"
              v-model="createPath"
              type="text"
              :placeholder="$t('sidebar.pathPlaceholder')"
              autocomplete="off"
              :disabled="createSubmitting"
            />
            <div v-if="errorMessage" class="ws-create-error">{{ errorMessage }}</div>
            <div class="ws-create-actions">
              <button type="button" class="ws-btn ghost" :disabled="createSubmitting" @click.stop="closeCreateForm">
                {{ $t('common.cancel') }}
              </button>
              <button type="submit" class="ws-btn primary" :disabled="createSubmitting">
                {{ createSubmitting ? $t('sidebar.creating') : $t('sidebar.create') }}
              </button>
            </div>
          </form>
        </div>
      </div>
    </transition>

    <!-- 「…」二级菜单：fixed 浮层，逃逸容器裁切（同个人空间二级菜单方案） -->
    <transition name="ws-menu">
      <div
        v-if="menuOpenId"
        class="ws-menu-floating"
        :style="menuStyle"
        role="menu"
        @click.stop
      >
        <button
          type="button"
          :disabled="menuOpenId === defaultWorkspaceId || busy"
          @click="handleMenuAction('set-default')"
        >
          {{ $t('sidebar.setDefault') }}
        </button>
        <button type="button" :disabled="busy" @click="handleMenuAction('rename')">{{ $t('sidebar.rename') }}</button>
        <button type="button" :disabled="isProject || busy" @click="handleMenuAction('reveal')">
          {{ $t('sidebar.revealInFolder') }}
        </button>
        <div class="ws-menu-sep"></div>
        <button type="button" class="danger" :disabled="busy" @click="handleMenuAction('delete')">{{ $t('common.delete') }}</button>
      </div>
    </transition>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';

defineOptions({ name: 'WorkspaceSwitcher' });

type WorkspaceItem = {
  workspace_id: string;
  label: string;
  path?: string;
  running_task_count?: number;
};

type AnchorRect = { left: number; top: number; right: number; bottom: number };

const props = withDefaults(
  defineProps<{
    open: boolean;
    anchorRect?: AnchorRect | null;
    workspaces?: WorkspaceItem[];
    currentWorkspaceId?: string;
    defaultWorkspaceId?: string;
    workspaceKind?: 'workspace' | 'project';
    busy?: boolean;
    createSubmitting?: boolean;
    errorMessage?: string;
  }>(),
  {
    anchorRect: null,
    workspaces: () => [],
    currentWorkspaceId: '',
    defaultWorkspaceId: '',
    workspaceKind: 'workspace',
    busy: false,
    createSubmitting: false,
    errorMessage: ''
  }
);

const emit = defineEmits<{
  (event: 'close'): void;
  (event: 'switch', workspaceId: string): void;
  (event: 'create', payload: { path: string; label: string }): void;
  (event: 'rename', payload: { workspace_id: string; label: string }): void;
  (event: 'delete', item: WorkspaceItem): void;
  (event: 'set-default', workspaceId: string): void;
  (event: 'reveal', workspaceId: string): void;
}>();

const isProject = computed(() => props.workspaceKind === 'project');
const noun = computed(() => (isProject.value ? 'sidebar.project' : 'sidebar.workspace'));

const popoverEl = ref<HTMLElement | null>(null);
const listEl = ref<HTMLElement | null>(null);
const bottomEl = ref<HTMLElement | null>(null);
const createNameEl = ref<HTMLInputElement | null>(null);
const renameInputEl = ref<HTMLInputElement[] | null>(null);

const menuOpenId = ref<string | null>(null);
const renamingId = ref<string | null>(null);
const deletingId = ref<string | null>(null);
const renameDraft = ref('');
const createOpen = ref(false);
const createPath = ref('');
const createLabel = ref('');
const bottomFading = ref(false);

/* ---------- 浮层定位：锚定入口按钮，夹紧在视口内 ---------- */
const POPOVER_WIDTH = 300;
const popoverStyle = ref<Record<string, string>>({});

const updatePosition = () => {
  const anchor = props.anchorRect;
  if (!anchor || typeof window === 'undefined') return;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const popH = popoverEl.value?.offsetHeight || 360;

  let left = anchor.right + 8;
  let top = anchor.top - 6;
  /* 右侧空间不足（如移动端全屏侧边栏）→ 改为在入口下方展开 */
  if (left + POPOVER_WIDTH > vw - 8) {
    left = Math.max(8, Math.min(anchor.left, vw - POPOVER_WIDTH - 8));
    top = anchor.bottom + 6;
  }
  if (top + popH > vh - 8) top = Math.max(8, vh - popH - 8);
  popoverStyle.value = {
    left: `${Math.round(left)}px`,
    top: `${Math.round(top)}px`
  };
};

/* ---------- 「…」浮动菜单定位 ---------- */
const menuStyle = ref<Record<string, string>>({});

const closeMenu = () => {
  menuOpenId.value = null;
};

const toggleMenu = async (item: WorkspaceItem) => {
  if (props.busy) return;
  if (menuOpenId.value === item.workspace_id) {
    closeMenu();
    return;
  }
  renamingId.value = null;
  deletingId.value = null;
  menuOpenId.value = item.workspace_id;
  await nextTick();
  const id = item.workspace_id;
  const rowEl = listEl.value?.querySelector(`.ws-row[data-ws-id="${CSS.escape(id)}"]`);
  const btn = rowEl?.querySelector('.ws-more') as HTMLElement | null;
  if (!btn) {
    closeMenu();
    return;
  }
  const rect = btn.getBoundingClientRect();
  const menuEl = document.querySelector('.ws-menu-floating') as HTMLElement | null;
  const mw = menuEl?.offsetWidth || 150;
  const mh = menuEl?.offsetHeight || 150;
  let left = rect.right - mw;
  let top = rect.bottom + 4;
  left = Math.max(8, Math.min(left, window.innerWidth - mw - 8));
  if (top + mh > window.innerHeight - 8) top = rect.top - mh - 4;
  menuStyle.value = { left: `${Math.round(left)}px`, top: `${Math.round(top)}px` };
};

const handleMenuAction = (act: 'set-default' | 'rename' | 'reveal' | 'delete') => {
  const id = menuOpenId.value;
  const item = props.workspaces.find((w) => w.workspace_id === id);
  closeMenu();
  if (!item) return;
  if (act === 'set-default') {
    emit('set-default', item.workspace_id);
  } else if (act === 'rename') {
    renamingId.value = item.workspace_id;
    renameDraft.value = item.label;
    nextTick(() => {
      const el = renameInputEl.value?.[0];
      el?.focus();
      el?.select();
    });
  } else if (act === 'reveal') {
    emit('reveal', item.workspace_id);
  } else if (act === 'delete') {
    deletingId.value = item.workspace_id;
  }
};

/* ---------- 行行为 ---------- */
const handleRowClick = (item: WorkspaceItem) => {
  if (props.busy) return;
  if (renamingId.value || deletingId.value) return;
  if (menuOpenId.value) {
    closeMenu();
    return;
  }
  if (item.workspace_id !== props.currentWorkspaceId) {
    emit('switch', item.workspace_id);
  }
};

const commitRename = (item: WorkspaceItem) => {
  const label = renameDraft.value.trim();
  if (label && label !== item.label) {
    emit('rename', { workspace_id: item.workspace_id, label });
  }
  renamingId.value = null;
};

const confirmDelete = (item: WorkspaceItem) => {
  deletingId.value = null;
  emit('delete', item);
};

/* ---------- 新建表单：底部高度动画 + 交叉淡入淡出 ---------- */
let bottomAnimating = false;

const swapBottom = (toForm: boolean, done?: () => void) => {
  if (bottomAnimating) return;
  bottomAnimating = true;
  const bottom = bottomEl.value;
  if (!bottom) {
    createOpen.value = toForm;
    bottomAnimating = false;
    if (done) done();
    return;
  }
  const h0 = bottom.offsetHeight;
  bottom.style.height = `${h0}px`;
  bottomFading.value = true;
  window.setTimeout(() => {
    createOpen.value = toForm;
    nextTick(() => {
      /* scrollHeight 至少等于 clientHeight，固定高度下会被旧高度污染；
         切 auto 测真实目标高度（此时新内容仍在淡出态，视觉无跳变） */
      bottom.style.height = 'auto';
      const h1 = bottom.offsetHeight;
      bottom.style.height = `${h0}px`;
      void bottom.offsetHeight; /* 强制 reflow，确保过渡从 h0 开始 */
      bottom.style.height = `${h1}px`;
      bottomFading.value = false;
      window.setTimeout(() => {
        bottom.style.height = '';
        bottomAnimating = false;
        if (done) done();
      }, 210);
    });
  }, 130);
};

const openCreateForm = () => {
  if (createOpen.value || props.busy) return;
  closeMenu();
  createPath.value = '';
  createLabel.value = '';
  swapBottom(true, () => createNameEl.value?.focus());
};

const closeCreateForm = () => {
  if (!createOpen.value) return;
  swapBottom(false);
};

const submitCreate = () => {
  emit('create', {
    path: createPath.value.trim(),
    label: createLabel.value.trim()
  });
};

/* 创建成功后由父级刷新列表；成功后收起表单 */
watch(
  () => props.createSubmitting,
  (submitting, prev) => {
    if (prev && !submitting && createOpen.value && !props.errorMessage) {
      closeCreateForm();
    }
  }
);

/* ---------- 全局关闭 ---------- */
const onDocClick = (event: MouseEvent) => {
  const target = event.target as Node | null;
  if (!target) return;
  /* 二级菜单 teleport 到 body、与 popoverEl 是兄弟节点；capture 阶段会先于此处
     触发，若不排除会导致点击菜单项（删除/重命名）时整个浮层先被关闭，
     菜单动作设置的内联状态（deletingId/renamingId）永远不可见。 */
  if ((target as Element).closest?.('.ws-menu-floating')) return;
  if (popoverEl.value && !popoverEl.value.contains(target)) {
    /* 点击入口按钮：交给按钮自身的 toggle 处理（已展开则收起），
       避免 capture 阶段先 close、按钮 click 又重新打开导致重播动画 */
    const anchor = props.anchorRect;
    if (anchor) {
      const x = event.clientX;
      const y = event.clientY;
      if (x >= anchor.left && x <= anchor.right && y >= anchor.top && y <= anchor.bottom) {
        return;
      }
    }
    emit('close');
  }
};

const onDocKeydown = (event: KeyboardEvent) => {
  if (event.key !== 'Escape') return;
  if (menuOpenId.value) {
    closeMenu();
    return;
  }
  if (renamingId.value || deletingId.value) {
    renamingId.value = null;
    deletingId.value = null;
    return;
  }
  if (createOpen.value) {
    closeCreateForm();
    return;
  }
  emit('close');
};

const onWindowResize = () => {
  closeMenu();
  updatePosition();
};

watch(
  () => props.open,
  async (open) => {
    if (open) {
      menuOpenId.value = null;
      renamingId.value = null;
      deletingId.value = null;
      await nextTick();
      updatePosition();
      document.addEventListener('click', onDocClick, true);
      document.addEventListener('keydown', onDocKeydown);
      window.addEventListener('resize', onWindowResize);
    } else {
      if (createOpen.value) createOpen.value = false;
      document.removeEventListener('click', onDocClick, true);
      document.removeEventListener('keydown', onDocKeydown);
      window.removeEventListener('resize', onWindowResize);
    }
  }
);

onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick, true);
  document.removeEventListener('keydown', onDocKeydown);
  window.removeEventListener('resize', onWindowResize);
});
</script>

<style scoped>
/* ---------- 浮层主体：单层扁平容器 ---------- */
.ws-switcher {
  --ws-row-h: 50px;
  position: fixed;
  z-index: 1500;
  width: 300px;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--border-default);
  border-radius: 12px;
  background: var(--surface-raised);
  box-shadow: var(--theme-shadow-soft);
  overflow: hidden;
}

.ws-switcher--project {
  --ws-row-h: 38px;
}

.ws-switcher-enter-active,
.ws-switcher-leave-active {
  transition: opacity 120ms ease, transform 150ms cubic-bezier(0.22, 1, 0.36, 1);
}

.ws-switcher-enter-from,
.ws-switcher-leave-to {
  opacity: 0;
  transform: translateY(-4px) scale(0.98);
}

/* 头部 */
.ws-switcher__header {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 42px;
  padding: 0 14px;
  border-bottom: 1px solid var(--border-default);
}

.ws-switcher__title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-secondary);
}

.ws-switcher__count {
  font-size: 11px;
  color: var(--text-tertiary);
}

/* 列表：刚好 7 行（7h + 6gap + 上下padding），超出内部滚动 */
.ws-switcher__list {
  flex: none;
  max-height: calc(var(--ws-row-h) * 7 + 18px);
  overflow-y: auto;
  padding: 6px;
  display: flex;
  flex-direction: column;
  gap: 1px;
  scrollbar-width: thin;
  scrollbar-color: var(--border-strong) transparent;
}

.ws-switcher__list::-webkit-scrollbar {
  width: 8px;
}

.ws-switcher__list::-webkit-scrollbar-thumb {
  background: var(--border-strong);
  border-radius: 4px;
  border: 2px solid var(--surface-raised);
}

.ws-switcher__list::-webkit-scrollbar-track {
  background: transparent;
}

/* 工作区行 */
.ws-row {
  position: relative;
  /* flex 子项默认 shrink:1，内容超出容器 max-height 时会被压到 min-content
     （双行文字 35px），导致行高失效、可见行数虚多；必须禁止压缩 */
  flex: none;
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  height: var(--ws-row-h);
  padding: 0 8px;
  border-radius: 8px;
  text-align: left;
  cursor: pointer;
}

.ws-row:hover {
  background: color-mix(in srgb, var(--text-primary) 4%, transparent);
}

.ws-row.current {
  background: color-mix(in srgb, var(--text-primary) 6%, transparent);
}

.ws-row.current:hover {
  background: color-mix(in srgb, var(--text-primary) 7%, transparent);
}

.ws-row-icon {
  flex: none;
  width: 17px;
  height: 17px; /* 行容器 align-items: center，整行上下居中 */
  color: var(--text-tertiary);
}

.ws-row-icon svg {
  width: 100%;
  height: 100%;
  display: block;
}

.ws-row.current .ws-row-icon {
  color: var(--text-secondary);
}

.ws-row-text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.ws-row-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.ws-row-path {
  font-size: 11.5px;
  color: var(--text-tertiary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.ws-row-meta {
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
}

.ws-tag {
  font-size: 11px;
  color: var(--text-tertiary);
  white-space: nowrap;
}

.ws-more {
  flex: none;
  width: 24px;
  height: 24px;
  display: none;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
}

.ws-more svg {
  width: 15px;
  height: 15px;
  display: block;
}

.ws-more:hover {
  color: var(--text-primary);
  background: color-mix(in srgb, var(--text-primary) 7%, transparent);
}

.ws-row:hover .ws-more,
.ws-row.menu-open .ws-more {
  display: flex;
}

.ws-empty {
  padding: 18px 8px;
  font-size: 12px;
  color: var(--text-tertiary);
  text-align: center;
}

/* 重命名内联输入 */
.ws-rename-input {
  font: inherit;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  background: transparent;
  border: 0;
  border-bottom: 1px solid var(--accent);
  outline: none;
  padding: 1px 0;
  width: 100%;
}

/* 删除确认内联 */
.ws-confirm {
  display: flex;
  flex-direction: column;
  gap: 5px;
  width: 100%;
  padding: 2px 0;
}

.ws-confirm-text {
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.4;
}

.ws-confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

/* 通用小按钮 */
.ws-btn {
  height: 26px;
  padding: 0 10px;
  border: 1px solid var(--border-strong);
  border-radius: 6px;
  background: transparent;
  font-size: 12px;
  color: var(--text-secondary);
  cursor: pointer;
  white-space: nowrap;
}

.ws-btn:hover:not(:disabled) {
  background: color-mix(in srgb, var(--text-primary) 5%, transparent);
}

.ws-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.ws-btn.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--on-accent);
}

.ws-btn.primary:hover:not(:disabled) {
  background: var(--accent-hover);
}

.ws-btn.danger {
  background: var(--state-danger);
  border-color: var(--state-danger);
  color: var(--on-accent);
}

.ws-btn.danger:hover:not(:disabled) {
  background: var(--state-danger-strong);
}

.ws-btn.ghost {
  border-color: var(--border-default);
}

/* 底部区域：高度动画容器 */
.ws-bottom {
  flex: none;
  border-top: 1px solid var(--border-default);
  overflow: hidden;
  transition: height 200ms cubic-bezier(0.22, 1, 0.36, 1);
}

.ws-footer {
  padding: 6px;
  transition: opacity 130ms ease;
}

.ws-footer.fading {
  opacity: 0;
}

.ws-create-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  height: 34px;
  padding: 0 8px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  font-size: 12.5px;
  color: var(--text-secondary);
  cursor: pointer;
}

.ws-create-btn:hover:not(:disabled) {
  background: color-mix(in srgb, var(--text-primary) 4%, transparent);
  color: var(--text-primary);
}

.ws-create-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.ws-create-icon {
  flex: none;
  width: 15px;
  height: 15px;
  color: var(--text-tertiary);
}

.ws-create-icon svg {
  width: 100%;
  height: 100%;
  display: block;
}

.ws-create-btn:hover:not(:disabled) .ws-create-icon {
  color: var(--text-secondary);
}

/* 新建表单 */
.ws-create-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px;
  transition: opacity 150ms ease;
}

.ws-create-form.fading {
  opacity: 0;
}

.ws-create-form input {
  height: 32px;
  padding: 0 10px;
  border: 1px solid var(--border-default);
  border-radius: 8px;
  background: var(--surface-soft);
  color: var(--text-primary);
  font: inherit;
  font-size: 12.5px;
  outline: none;
}

.ws-create-form input:focus {
  border-color: var(--accent);
}

.ws-create-form input::placeholder {
  color: var(--text-muted);
}

.ws-create-error {
  font-size: 12px;
  color: var(--state-danger);
}

.ws-create-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

/* 「…」浮动菜单（Teleport 到 body，fixed 定位逃逸裁切） */
.ws-menu-floating {
  position: fixed;
  z-index: 1600;
  min-width: 136px;
  padding: 4px;
  border: 1px solid var(--border-default);
  border-radius: 10px;
  background: var(--surface-raised);
  box-shadow: var(--theme-shadow-soft);
  display: flex;
  flex-direction: column;
}

.ws-menu-enter-active,
.ws-menu-leave-active {
  transition: opacity 110ms ease, transform 140ms cubic-bezier(0.22, 1, 0.36, 1);
}

.ws-menu-enter-from,
.ws-menu-leave-to {
  opacity: 0;
  transform: translateY(-3px) scale(0.98);
}

.ws-menu-floating button {
  display: flex;
  align-items: center;
  width: 100%;
  height: 30px;
  padding: 0 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  font-size: 12.5px;
  color: var(--text-secondary);
  text-align: left;
  white-space: nowrap;
  cursor: pointer;
}

.ws-menu-floating button:hover:not(:disabled) {
  background: color-mix(in srgb, var(--text-primary) 5%, transparent);
  color: var(--text-primary);
}

.ws-menu-floating button.danger {
  color: var(--state-danger);
}

.ws-menu-floating button.danger:hover:not(:disabled) {
  background: color-mix(in srgb, var(--state-danger) 8%, transparent);
}

.ws-menu-floating button:disabled {
  color: var(--text-muted);
  cursor: default;
}

.ws-menu-sep {
  height: 1px;
  margin: 4px 6px;
  background: var(--border-default);
}
</style>
