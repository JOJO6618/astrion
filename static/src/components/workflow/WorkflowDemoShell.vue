<template>
  <div class="wf-shell">
    <WorkflowEditorView
      v-if="view === 'editor' && currentWorkflow"
      :workflow="currentWorkflow"
      @back="backToLibrary"
      @save="onSaved"
    />
    <WorkflowLibraryView
      v-else-if="view === 'library' && listLoaded"
      :workflows="workflows"
      @open="openEditor"
      @create="createWorkflow"
      @duplicate="duplicateWorkflow"
      @delete="deleteWorkflowByName"
      @exit="exitDemo"
    />
    <div v-else class="wf-shell__loading">加载中…</div>
    <div v-if="errorMessage" class="wf-shell__error" role="alert">
      <span>{{ errorMessage }}</span>
      <button type="button" class="wf-shell__error-close" aria-label="关闭" @click="errorMessage = ''">×</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import WorkflowLibraryView from './WorkflowLibraryView.vue';
import WorkflowEditorView from './WorkflowEditorView.vue';
import { createEmptyWorkflow, type WorkflowDef } from './workflowModel';
import {
  deleteWorkflow,
  listWorkflows,
  loadWorkflow,
  saveWorkflow,
  type WorkflowListItem,
} from './api';

/**
 * 工作流编辑器外壳：全屏覆盖层，在工作流库 / 编辑器间切换。
 * 数据通过 /api/workflows 落盘（WORKFLOW.md），列表页只持元信息，
 * 进入编辑器时按需加载完整定义。
 * 导航通过本地状态 + history.replaceState 同步地址栏，不触发整页刷新。
 */

const props = defineProps<{
  routePath: string;
}>();

// 路由解析：'workflows' → 库；'workflow/<name>' 或 'workflow/<name>/edit' → 编辑器
function parseEditingName(path: string): string {
  if (!path.startsWith('workflow/')) return '';
  const rest = path.slice('workflow/'.length).replace(/\/+$/, '');
  return rest.endsWith('/edit') ? rest.slice(0, -'/edit'.length) : rest;
}

const workflows = ref<WorkflowListItem[]>([]);
const listLoaded = ref(false);
const editingName = ref(parseEditingName(props.routePath));
const currentWorkflow = ref<WorkflowDef | null>(null);
const errorMessage = ref('');

const view = computed<'library' | 'editor'>(() => (editingName.value ? 'editor' : 'library'));

function showError(err: unknown, fallback: string) {
  errorMessage.value = err instanceof Error ? err.message : fallback;
}

async function refreshList() {
  try {
    workflows.value = await listWorkflows();
  } catch (err) {
    showError(err, '加载工作流列表失败');
  } finally {
    listLoaded.value = true;
  }
}

async function openEditor(name: string) {
  try {
    currentWorkflow.value = await loadWorkflow(name);
    editingName.value = name;
    history.replaceState({}, '', `/workflow/${name}`);
  } catch (err) {
    showError(err, '加载工作流失败');
  }
}

function backToLibrary() {
  editingName.value = '';
  currentWorkflow.value = null;
  history.replaceState({}, '', '/workflows');
  void refreshList();
}

function uniqueName(base: string): string {
  const existing = new Set(workflows.value.map((w) => w.name));
  if (!existing.has(base)) return base;
  let n = 2;
  while (existing.has(`${base}-${n}`)) n += 1;
  return `${base}-${n}`;
}

async function createWorkflow() {
  const def = createEmptyWorkflow(uniqueName(`new-workflow-${Date.now().toString(36)}`));
  try {
    await saveWorkflow(def);
    await refreshList();
    await openEditor(def.name);
  } catch (err) {
    showError(err, '新建工作流失败');
  }
}

async function duplicateWorkflow(name: string) {
  try {
    const source = await loadWorkflow(name);
    const copy: WorkflowDef = JSON.parse(JSON.stringify(source));
    copy.name = uniqueName(`${name}-copy`);
    copy.source = 'user';
    await saveWorkflow(copy);
    await refreshList();
  } catch (err) {
    showError(err, '复制工作流失败');
  }
}

async function deleteWorkflowByName(name: string) {
  try {
    await deleteWorkflow(name);
    if (editingName.value === name) {
      backToLibrary();
      return;
    }
    await refreshList();
  } catch (err) {
    showError(err, '删除工作流失败');
  }
}

async function onSaved() {
  const wf = currentWorkflow.value;
  if (!wf) return;
  // 改名 = 另存新名 + 删除旧文件（内置示例不可删，降级为保留原件）
  if (wf.name !== editingName.value) {
    const oldName = editingName.value;
    editingName.value = wf.name;
    history.replaceState({}, '', `/workflow/${wf.name}`);
    try {
      await deleteWorkflow(oldName);
    } catch {
      // 旧文件删除失败（如内置示例）不阻断保存结果
    }
  }
  void refreshList();
}

function exitDemo() {
  // 与对话体系完全隔离，直接整页跳回新对话，避免状态残留
  window.location.assign('/new');
}

onMounted(async () => {
  await refreshList();
  // 直接以 /workflow/<name> 进入时加载目标工作流；失败退回库页
  if (editingName.value) {
    try {
      currentWorkflow.value = await loadWorkflow(editingName.value);
    } catch (err) {
      showError(err, '加载工作流失败');
      backToLibrary();
    }
  }
});
</script>

<style scoped lang="scss">
.wf-shell {
  position: fixed;
  inset: 0;
  z-index: 200;
  background: var(--surface-base);
  display: flex;
  flex-direction: column;

  &__loading {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--text-secondary);
    font-size: 13px;
  }

  &__error {
    position: absolute;
    left: 50%;
    bottom: 24px;
    transform: translateX(-50%);
    display: flex;
    align-items: center;
    gap: 10px;
    max-width: min(560px, calc(100% - 48px));
    padding: 8px 12px;
    border: 1px solid var(--state-danger);
    border-radius: 8px;
    background: var(--surface-raised);
    color: var(--state-danger);
    font-size: 13px;
    line-height: 1.4;
  }

  &__error-close {
    flex-shrink: 0;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 20px;
    height: 20px;
    padding: 0;
    border: none;
    border-radius: 4px;
    background: transparent;
    color: inherit;
    font-size: 14px;
    font-family: inherit;
    cursor: pointer;

    &:hover {
      background: var(--hover-bg);
    }
  }
}
</style>
