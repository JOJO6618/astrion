<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { t } from '@/locales';
import { useUiStore } from '@/stores/ui';
import { useConversationTabsStore } from '@/stores/conversationTabs';
import UiMenu from '@/components/ui/UiMenu.vue';
import UiSearchField from '@/components/ui/UiSearchField.vue';
import UiSelect from '@/components/ui/UiSelect.vue';
import UiSwitch from '@/components/ui/UiSwitch.vue';
import ScheduleIcon from './ScheduleIcon.vue';
import ScheduleDialog from './ScheduleDialog.vue';
import ScheduleSettings from './ScheduleSettings.vue';
import ScheduleHistory from './ScheduleHistory.vue';
import ScheduleYaml from './ScheduleYaml.vue';
import {
  describeSchedule,
  isActiveRun,
  joinWide,
  runDuration,
  runTitle,
  shortDate
} from './scheduleFormat';
import {
  cloneDefinition,
  downloadText,
  fetchConversations,
  newDefinition,
  sameDefinition,
  scheduleApi,
  yamlFilename,
  type ScheduleConversation,
  type ScheduleDefinition,
  type ScheduleMenuItem,
  type ScheduleOptions,
  type SchedulePlan,
  type ScheduleRun
} from './useSchedules';
import './schedules.scss';

const emit = defineEmits<{ (event: 'close'): void }>();
const ui = useUiStore();
const toast = (message: string, type = 'info') => ui.pushToast({ message, type });
const fail = (error: unknown) => toast((error as Error)?.message || String(error), 'error');

// ---------------- 状态 ----------------
const plans = ref<SchedulePlan[]>([]);
const options = ref<ScheduleOptions>({
  workspaces: [],
  models: [],
  timezones: [],
  host_admin: false
});
const loading = ref(true);
const loadError = ref('');
const drafts = ref<Record<string, ScheduleDefinition>>({});
const localKeys = ref<string[]>([]);
const yamlEdits = ref<Record<string, string>>({});
const selectedKey = ref('');
const view = ref<'settings' | 'history' | 'yaml'>('settings');
const collapsed = ref<Record<string, boolean>>({ enabled: false, stopped: false });
const workspaceFilter = ref('all');
const search = ref('');
const advancedOpen = ref(false);
const saving = ref(false);
const acting = ref(false);
const conversations = ref<Record<string, ScheduleConversation[] | null>>({});
const preview = ref<{ dates: string[]; error: string }>({ dates: [], error: '' });
const runs = ref<ScheduleRun[] | null>(null);
const yamlText = ref<string | null>(null);
const yamlError = ref('');
const yamlApplying = ref(false);
let retired = false;

const searchInput = ref<InstanceType<typeof UiSearchField>>();
const createButton = ref<HTMLButtonElement>();
const filterButton = ref<HTMLButtonElement>();
const moreButton = ref<HTMLButtonElement>();
const importInput = ref<HTMLInputElement>();
const detailScroll = ref<HTMLElement>();
const menu = ref<'' | 'create' | 'filter' | 'more'>('');

const planById = computed(() => {
  const map = new Map<string, SchedulePlan>();
  for (const plan of plans.value) if (plan.definition.id) map.set(plan.definition.id, plan);
  return map;
});
const workspaceName = (id: string) =>
  options.value.workspaces.find((item) => item.id === id)?.label || id;
const isLocal = (key: string) => localKeys.value.includes(key);
const definitionFor = (key: string) => drafts.value[key] || planById.value.get(key)?.definition;
const isDirty = (key: string) =>
  isLocal(key) ||
  yamlEdits.value[key] !== undefined ||
  (!!drafts.value[key] && !sameDefinition(drafts.value[key], planById.value.get(key)?.definition));

const current = computed(() => (selectedKey.value ? drafts.value[selectedKey.value] : undefined));
const currentPlan = computed(() => planById.value.get(selectedKey.value));
const currentDirty = computed(() => !!current.value && isDirty(selectedKey.value));
const currentLocal = computed(() => isLocal(selectedKey.value));
const activeRun = computed(() =>
  isActiveRun(currentPlan.value?.active_run) ? currentPlan.value?.active_run : null
);

// ---------------- 列表 ----------------
const entries = computed(() => [
  ...localKeys.value.map((key) => ({ key, definition: drafts.value[key], plan: undefined })),
  ...plans.value.map((plan) => ({
    key: plan.definition.id || '',
    definition: drafts.value[plan.definition.id || ''] || plan.definition,
    plan
  }))
]);
const visible = computed(() => {
  const query = search.value.trim().toLowerCase();
  return entries.value.filter(
    ({ definition }) =>
      definition &&
      (workspaceFilter.value === 'all' ||
        definition.target.workspace_id === workspaceFilter.value) &&
      `${definition.name} ${definition.message} ${workspaceName(definition.target.workspace_id)}`
        .toLowerCase()
        .includes(query)
  );
});
const groups = computed(() =>
  [
    { id: 'enabled', label: t('schedules.groupEnabled'), enabled: true },
    { id: 'stopped', label: t('schedules.groupStopped'), enabled: false }
  ].map((group) => ({
    ...group,
    items: visible.value.filter((entry) => entry.definition.enabled === group.enabled)
  }))
);
function cadence(definition: ScheduleDefinition) {
  try {
    return describeSchedule(definition.schedule);
  } catch {
    return t('schedules.ruleIncomplete');
  }
}
function rowStatus(plan: SchedulePlan | undefined) {
  if (isActiveRun(plan?.active_run)) return { text: t('schedules.rowRunning'), tone: 'is-running' };
  if (plan?.last_run?.state === 'failed')
    return { text: t('schedules.rowFailed'), tone: 'is-error' };
  return null;
}
const filterItems = computed<ScheduleMenuItem[]>(() => [
  { value: 'all', label: t('schedules.allWorkspaces') },
  ...options.value.workspaces.map((item) => ({ value: item.id, label: item.label }))
]);
const filterLabel = computed(() =>
  workspaceFilter.value === 'all' ? t('schedules.workspace') : workspaceName(workspaceFilter.value)
);

// ---------------- 同步后端 ----------------
function syncDrafts(previous: Map<string, SchedulePlan>) {
  for (const plan of plans.value) {
    const id = plan.definition.id || '';
    const draft = drafts.value[id];
    const old = previous.get(id)?.definition;
    // 未修改的草稿跟随后端最新版本；有修改的草稿保留，保存时由 revision 判断冲突。
    if (draft && old && !sameDefinition(old, plan.definition) && sameDefinition(draft, old))
      drafts.value[id] = cloneDefinition(plan.definition);
  }
  for (const key of Object.keys(drafts.value))
    if (!isLocal(key) && !planById.value.has(key)) {
      delete drafts.value[key];
      delete yamlEdits.value[key];
    }
  if (selectedKey.value && !drafts.value[selectedKey.value]) select(firstKey());
}
let generation = 0;
async function refresh() {
  const request = ++generation;
  try {
    const data = await scheduleApi<{ plans: SchedulePlan[]; scheduler_error: string | null }>();
    if (retired || request !== generation) return;
    const previous = planById.value;
    plans.value = data.plans;
    loadError.value = data.scheduler_error || '';
    syncDrafts(previous);
    if (loading.value && !selectedKey.value) select(firstKey());
  } catch (error) {
    if (!retired && request === generation) loadError.value = (error as Error).message;
  } finally {
    if (!retired && request === generation) loading.value = false;
  }
}
let pollTimer: ReturnType<typeof setTimeout> | undefined;
async function poll() {
  await refresh();
  if (!retired) pollTimer = setTimeout(poll, 3000);
}
const firstKey = () => localKeys.value[0] || plans.value[0]?.definition.id || '';

// ---------------- 选择与编辑 ----------------
function select(key: string) {
  if (key && !drafts.value[key]) {
    const definition = planById.value.get(key)?.definition;
    if (definition) drafts.value[key] = cloneDefinition(definition);
  }
  selectedKey.value = key;
  view.value = 'settings';
}
function edit(mutate: (draft: ScheduleDefinition) => void) {
  if (current.value) mutate(current.value);
}
watch([selectedKey, view], () => {
  if (detailScroll.value) detailScroll.value.scrollTop = 0;
});

// 下一次运行预览：未改动的计划直接用后端结果；草稿只校验时间规则部分。
let previewTimer: ReturnType<typeof setTimeout> | undefined;
let previewGeneration = 0;
watch(
  // 以字符串作为依赖签名：轮询刷新列表但时间规则未变时不重复请求。
  () =>
    JSON.stringify([
      selectedKey.value,
      current.value?.schedule,
      current.value?.target.workspace_id,
      currentPlan.value?.definition.schedule,
      currentPlan.value?.next_dates
    ]),
  () => {
    clearTimeout(previewTimer);
    const request = ++previewGeneration;
    const draft = current.value;
    if (!draft) return;
    const plan = currentPlan.value;
    if (plan && JSON.stringify(plan.definition.schedule) === JSON.stringify(draft.schedule)) {
      preview.value = { dates: plan.next_dates || [], error: '' };
      return;
    }
    previewTimer = setTimeout(async () => {
      try {
        const data = await scheduleApi<{ next_dates: string[] }>('/validate', {
          definition: {
            schema_version: 1,
            name: 'preview',
            message: 'preview',
            enabled: false,
            schedule: draft.schedule,
            target: { workspace_id: draft.target.workspace_id, conversation: { mode: 'new' } },
            configuration: { mode: 'inherit' },
            goal: { mode: 'off' }
          }
        });
        if (!retired && request === previewGeneration)
          preview.value = { dates: data.next_dates, error: '' };
      } catch (error) {
        if (!retired && request === previewGeneration)
          preview.value = { dates: [], error: (error as Error).message };
      }
    }, 400);
  },
  { immediate: true }
);

// 已有对话模式按工作区读取对话列表。
watch(
  () =>
    current.value?.target.conversation.mode === 'existing' ? current.value.target.workspace_id : '',
  async (workspace) => {
    if (!workspace || conversations.value[workspace]) return;
    conversations.value[workspace] = null;
    try {
      const list = await fetchConversations(workspace);
      if (!retired) conversations.value[workspace] = list;
    } catch (error) {
      if (!retired) {
        conversations.value[workspace] = [];
        fail(error);
      }
    }
  },
  { immediate: true }
);
const currentConversations = computed(() => {
  const workspace = current.value?.target.workspace_id || '';
  return workspace in conversations.value ? conversations.value[workspace] : null;
});

const nextLabel = computed(() => {
  if (activeRun.value) return t('schedules.nextRunning');
  if (!current.value?.enabled) return t('schedules.nextPaused');
  const next = currentDirty.value ? preview.value.dates[0] : currentPlan.value?.next_at;
  return next
    ? t('schedules.nextAt', { time: shortDate(next, current.value?.schedule.timezone) })
    : t('schedules.nextNone');
});
const saveState = computed(() => {
  if (!current.value) return '';
  if (yamlEdits.value[selectedKey.value] !== undefined) return t('schedules.yamlPending');
  return currentDirty.value ? t('schedules.unsaved') : t('schedules.allSaved');
});

// ---------------- 保存 / 运行 ----------------
async function saveCurrent(showMessage = true): Promise<string | null> {
  const key = selectedKey.value;
  if (!current.value || saving.value) return null;
  if (yamlEdits.value[key] !== undefined && !(await applyYaml(false))) return null;
  const draft = drafts.value[key];
  saving.value = true;
  try {
    const saved = await scheduleApi<SchedulePlan>('', {
      definition: draft,
      overwrite: !!draft.id
    });
    if (retired) return null;
    const id = saved.definition.id || '';
    upsertPlan(saved);
    if (isLocal(key)) {
      localKeys.value = localKeys.value.filter((item) => item !== key);
      delete drafts.value[key];
    }
    drafts.value[id] = cloneDefinition(saved.definition);
    delete yamlEdits.value[key];
    if (selectedKey.value === key) selectedKey.value = id;
    if (showMessage) toast(t('schedules.saved'), 'success');
    return id;
  } catch (error) {
    fail(error);
    return null;
  } finally {
    saving.value = false;
  }
}
function upsertPlan(plan: SchedulePlan) {
  generation++; // 丢弃保存前发出的列表请求，避免旧列表把新计划刷掉
  const index = plans.value.findIndex((item) => item.definition.id === plan.definition.id);
  if (index >= 0) plans.value.splice(index, 1, plan);
  else plans.value.unshift(plan);
}
async function act(id: string, action: string, runId?: string) {
  const plan = planById.value.get(id);
  if (!plan || acting.value) return false;
  acting.value = true;
  try {
    await scheduleApi(`/${id}/actions`, {
      action,
      revision: plan.definition.revision,
      ...(runId ? { run_id: runId } : {}),
      request_id: crypto.randomUUID()
    });
    await refresh();
    return true;
  } catch (error) {
    fail(error);
    return false;
  } finally {
    acting.value = false;
  }
}
async function runNow() {
  if (!current.value || activeRun.value) return;
  if (currentDirty.value) {
    dialog.value = { kind: 'saveRun' };
    return;
  }
  if (await act(selectedKey.value, 'run')) toast(t('schedules.runStarted'), 'success');
}
async function confirmSaveRun() {
  dialog.value = null;
  const id = await saveCurrent(false);
  if (id && (await act(id, 'run'))) toast(t('schedules.runStarted'), 'success');
}
async function stopRun() {
  if (activeRun.value && (await act(selectedKey.value, 'stop', activeRun.value.id)))
    toast(t('schedules.runStopped'), 'success');
}

// ---------------- 更多菜单 ----------------
const moreItems = computed<ScheduleMenuItem[]>(() =>
  currentLocal.value
    ? [
        { value: 'yaml', label: t('schedules.yaml'), icon: 'file' },
        { value: 'delete', label: t('schedules.delete'), icon: 'trash', danger: true }
      ]
    : [
        { value: 'history', label: t('schedules.history'), icon: 'clock' },
        { value: 'yaml', label: t('schedules.yaml'), icon: 'file' },
        { value: 'duplicate', label: t('schedules.duplicate'), icon: 'copy' },
        { value: 'copy-id', label: t('schedules.copyId'), icon: 'hash' },
        { value: 'download', label: t('schedules.download'), icon: 'download' },
        { value: 'delete', label: t('schedules.delete'), icon: 'trash', danger: true }
      ]
);
async function onMore(action: string) {
  if (action === 'history' || action === 'yaml') view.value = action;
  else if (action === 'duplicate') await duplicate();
  else if (action === 'copy-id') await copyText(current.value?.id || '');
  else if (action === 'download') await downloadYaml();
  else if (action === 'delete') dialog.value = { kind: 'delete' };
}
async function duplicate() {
  const source = current.value;
  if (!source) return;
  const copy = cloneDefinition(source);
  delete copy.id;
  delete copy.revision;
  copy.name = t('schedules.copyName', { name: source.name }).slice(0, 80);
  copy.enabled = false;
  try {
    const saved = await scheduleApi<SchedulePlan>('', { definition: copy, overwrite: false });
    if (retired) return;
    upsertPlan(saved);
    collapsed.value.stopped = false;
    search.value = '';
    select(saved.definition.id || '');
    toast(t('schedules.duplicated'), 'success');
  } catch (error) {
    fail(error);
  }
}
async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast(t('schedules.copied'), 'success');
  } catch (error) {
    fail(error);
  }
}
async function yamlFor(key: string): Promise<string> {
  if (yamlEdits.value[key] !== undefined) return yamlEdits.value[key];
  const definition = definitionFor(key);
  if (!definition) return '';
  if (!isDirty(key) && definition.id)
    return (await scheduleApi<{ yaml: string }>(`/${definition.id}/yaml`)).yaml;
  return (await scheduleApi<{ yaml: string }>('/validate', { definition })).yaml;
}
async function downloadYaml() {
  try {
    downloadText(await yamlFor(selectedKey.value), yamlFilename(current.value?.name || ''));
  } catch (error) {
    fail(error);
  }
}
async function confirmDelete() {
  const key = selectedKey.value;
  if (isLocal(key)) {
    localKeys.value = localKeys.value.filter((item) => item !== key);
    delete drafts.value[key];
    delete yamlEdits.value[key];
    dialog.value = null;
    select(firstKey());
    return;
  }
  dialog.value = { kind: 'delete', busy: true };
  const done = await act(key, 'delete');
  dialog.value = null;
  if (done) {
    delete drafts.value[key];
    delete yamlEdits.value[key];
    generation++;
    plans.value = plans.value.filter((item) => item.definition.id !== key);
    select(firstKey());
    toast(t('schedules.deleted'), 'success');
  }
}

// ---------------- 子页面：运行记录 / 配置文件 ----------------
let runsTimer: ReturnType<typeof setTimeout> | undefined;
let runsGeneration = 0;
async function loadRuns(id: string, request: number) {
  try {
    const data = await scheduleApi<ScheduleRun[]>(`/${id}/runs`);
    if (!retired && request === runsGeneration) runs.value = data;
  } catch (error) {
    if (!retired && request === runsGeneration) {
      runs.value = runs.value || [];
      fail(error);
      return;
    }
  }
  if (!retired && request === runsGeneration)
    runsTimer = setTimeout(() => loadRuns(id, request), 2500);
}
let yamlGeneration = 0;
watch([view, selectedKey], async ([mode, key]) => {
  clearTimeout(runsTimer);
  const runsRequest = ++runsGeneration;
  const yamlRequest = ++yamlGeneration;
  yamlError.value = '';
  if (mode === 'history') {
    runs.value = null;
    if (!isLocal(key)) void loadRuns(key, runsRequest);
    else runs.value = [];
  } else if (mode === 'yaml') {
    yamlText.value = null;
    try {
      const text = await yamlFor(key);
      if (!retired && yamlRequest === yamlGeneration) yamlText.value = text;
    } catch (error) {
      if (!retired && yamlRequest === yamlGeneration) {
        yamlText.value = '';
        yamlError.value = (error as Error).message;
      }
    }
  }
});
function onYamlInput(text: string) {
  yamlText.value = text;
  yamlEdits.value[selectedKey.value] = text;
}
async function applyYaml(showMessage = true): Promise<boolean> {
  const key = selectedKey.value;
  const draft = drafts.value[key];
  const text = yamlEdits.value[key] ?? yamlText.value;
  if (!draft || text === null || text === undefined) return true;
  yamlApplying.value = true;
  yamlError.value = '';
  try {
    const data = await scheduleApi<{ definition: ScheduleDefinition; yaml: string }>('/validate', {
      yaml: text,
      workspace_id: draft.target.workspace_id
    });
    const next = data.definition;
    if (
      (next.id !== undefined && next.id !== draft.id) ||
      (next.revision !== undefined && next.revision !== draft.revision)
    )
      throw new Error(t('schedules.yamlMismatch'));
    if (draft.id) {
      next.id = draft.id;
      next.revision = draft.revision;
    }
    drafts.value[key] = next;
    delete yamlEdits.value[key];
    if (view.value === 'yaml' && selectedKey.value === key) yamlText.value = data.yaml;
    if (showMessage) toast(t('schedules.yamlApplied'), 'success');
    return true;
  } catch (error) {
    yamlError.value = (error as Error).message;
    if (view.value !== 'yaml') fail(error);
    return false;
  } finally {
    yamlApplying.value = false;
  }
}
function openConversation(run: ScheduleRun) {
  const workspace =
    definitionFor(run.schedule_id)?.target.workspace_id || current.value?.target.workspace_id;
  if (!run.conversation_id || !workspace) return;
  dialog.value = null;
  const tabs = useConversationTabsStore();
  if (tabs.enabled)
    void tabs.openConversationTab({ conversationId: run.conversation_id, workspaceId: workspace });
  else
    window.location.assign(`/${run.conversation_id}?workspace_id=${encodeURIComponent(workspace)}`);
}

// ---------------- 新建 / 导入 / 重命名 ----------------
type Dialog =
  | { kind: 'create'; name: string; workspace: string }
  | { kind: 'rename'; name: string }
  | { kind: 'saveRun' }
  | { kind: 'delete'; busy?: boolean }
  | { kind: 'overwrite'; yaml: string; name: string; workspace: string; busy?: boolean }
  | { kind: 'importError'; message: string }
  | { kind: 'run'; run: ScheduleRun };
const dialog = ref<Dialog | null>(null);
const dialogInput = ref<HTMLInputElement>();
const defaultWorkspace = () =>
  workspaceFilter.value !== 'all' ? workspaceFilter.value : options.value.workspaces[0]?.id || '';

function onCreateMenu(value: string) {
  if (value === 'import') importInput.value?.click();
  else openCreate();
}
function openCreate() {
  dialog.value = { kind: 'create', name: '', workspace: defaultWorkspace() };
}
function confirmCreate() {
  const state = dialog.value;
  if (state?.kind !== 'create') return;
  const name = state.name.trim();
  if (!name) {
    dialogInput.value?.focus();
    return;
  }
  const key = `local_${crypto.randomUUID()}`;
  drafts.value[key] = newDefinition(name.slice(0, 80), state.workspace);
  localKeys.value = [key, ...localKeys.value];
  collapsed.value.stopped = false;
  search.value = '';
  dialog.value = null;
  select(key);
  void nextTick(() => document.querySelector<HTMLElement>('.sch-prompt-input')?.focus());
}
function openRename() {
  if (current.value) dialog.value = { kind: 'rename', name: current.value.name };
}
function confirmRename() {
  const state = dialog.value;
  if (state?.kind !== 'rename') return;
  const name = state.name.trim();
  if (!name) return;
  edit((draft) => (draft.name = name.slice(0, 80)));
  dialog.value = null;
}
async function importFile(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = '';
  if (!file) return;
  if (file.size > 256 * 1024) {
    dialog.value = { kind: 'importError', message: t('schedules.fileTooLarge') };
    return;
  }
  const workspace = defaultWorkspace();
  try {
    const yaml = await file.text();
    const data = await scheduleApi<{ definition: ScheduleDefinition }>('/validate', {
      yaml,
      workspace_id: workspace
    });
    const existing = data.definition.id ? planById.value.get(data.definition.id) : undefined;
    if (existing) {
      if (isActiveRun(existing.active_run)) throw new Error(t('schedules.importRunning'));
      dialog.value = { kind: 'overwrite', yaml, workspace, name: existing.definition.name };
    } else await submitImport(yaml, workspace, false);
  } catch (error) {
    dialog.value = { kind: 'importError', message: (error as Error).message };
  }
}
async function submitImport(yaml: string, workspace: string, overwrite: boolean) {
  try {
    const saved = await scheduleApi<SchedulePlan>('', { yaml, overwrite, workspace_id: workspace });
    if (retired) return;
    const id = saved.definition.id || '';
    upsertPlan(saved);
    drafts.value[id] = cloneDefinition(saved.definition);
    delete yamlEdits.value[id];
    collapsed.value[saved.definition.enabled ? 'enabled' : 'stopped'] = false;
    workspaceFilter.value = 'all';
    search.value = '';
    dialog.value = null;
    select(id);
    toast(t('schedules.imported'), 'success');
  } catch (error) {
    dialog.value = { kind: 'importError', message: (error as Error).message };
  }
}
async function confirmOverwrite() {
  const state = dialog.value;
  if (state?.kind !== 'overwrite') return;
  dialog.value = { ...state, busy: true };
  await submitImport(state.yaml, state.workspace, true);
}

// ---------------- 键盘 ----------------
function onKeydown(event: KeyboardEvent) {
  const active = document.activeElement as HTMLElement | null;
  const typing =
    !!active && (active.isContentEditable || ['INPUT', 'TEXTAREA'].includes(active.tagName));
  if (event.key === '/' && !event.ctrlKey && !event.metaKey && !typing && !dialog.value) {
    event.preventDefault();
    searchInput.value?.focus();
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
    event.preventDefault();
    if (currentDirty.value && !dialog.value) void saveCurrent();
  }
}
function onSearchKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') search.value = '';
}
function closeMenu(restoreFocus: boolean) {
  const anchor = { create: createButton, filter: filterButton, more: moreButton, '': null }[
    menu.value
  ];
  menu.value = '';
  if (restoreFocus) anchor?.value?.focus({ preventScroll: true });
}

onMounted(async () => {
  document.addEventListener('keydown', onKeydown);
  try {
    const data = await scheduleApi<ScheduleOptions>('/options');
    if (!retired) options.value = data;
  } catch (error) {
    if (!retired) loadError.value = (error as Error).message;
  }
  if (!retired) void poll();
});
onBeforeUnmount(() => {
  retired = true;
  generation++;
  previewGeneration++;
  runsGeneration++;
  yamlGeneration++;
  clearTimeout(pollTimer);
  clearTimeout(previewTimer);
  clearTimeout(runsTimer);
  document.removeEventListener('keydown', onKeydown);
});
</script>
<template>
  <div class="schedules-page">
    <div class="sch-planner">
      <section class="sch-list-pane" :aria-label="t('schedules.listAria')">
        <div class="sch-panel-heading">
          <div class="sch-panel-title">
            <button
              type="button"
              class="sch-icon-button sch-back-trigger"
              :aria-label="t('schedules.backToConversation')"
              @click="emit('close')"
            >
              <ScheduleIcon name="arrow-left" />
            </button>
            <h1>{{ t('schedules.title') }}</h1>
          </div>
          <button
            ref="createButton"
            type="button"
            class="sch-icon-button sch-create-trigger"
            :aria-label="t('schedules.createOrImport')"
            aria-haspopup="menu"
            :aria-expanded="menu === 'create'"
            @click="menu = menu === 'create' ? '' : 'create'"
          >
            <ScheduleIcon name="plus" />
          </button>
          <input
            ref="importInput"
            type="file"
            accept=".yaml,.yml,.json"
            hidden
            @change="importFile"
          />
        </div>
        <div class="sch-list-toolbar">
          <UiSearchField
            ref="searchInput"
            v-model="search"
            :placeholder="t('schedules.search')"
            :clear-label="t('schedules.clearSearch')"
            @keydown="onSearchKeydown"
          />
          <button
            ref="filterButton"
            type="button"
            class="sch-list-tool"
            :class="{ 'is-active': workspaceFilter !== 'all' }"
            :aria-label="t('schedules.filterWorkspace', { name: filterLabel })"
            aria-haspopup="listbox"
            :aria-expanded="menu === 'filter'"
            @click="menu = menu === 'filter' ? '' : 'filter'"
          >
            <ScheduleIcon name="folder" /><span>{{ filterLabel }}</span
            ><ScheduleIcon name="chevron-down" class="sch-tool-chevron" />
          </button>
        </div>
        <p v-if="loadError" class="sch-list-error" role="alert">{{ loadError }}</p>
        <div class="sch-plan-list">
          <template v-if="!loading">
            <section v-for="group in groups" :key="group.id" class="sch-plan-group">
              <button
                type="button"
                class="sch-group-heading"
                :aria-expanded="!collapsed[group.id]"
                :aria-controls="`sch-group-${group.id}`"
                @click="collapsed[group.id] = !collapsed[group.id]"
              >
                <span>{{ group.label }}</span>
                <ScheduleIcon name="chevron-down" class="sch-group-chevron" />
                <span class="sch-group-count">{{ group.items.length }}</span>
              </button>
              <div
                :id="`sch-group-${group.id}`"
                class="sch-group-items"
                :class="{ 'is-collapsed': collapsed[group.id] }"
                :aria-hidden="collapsed[group.id]"
                :inert="collapsed[group.id] || undefined"
              >
                <div class="sch-group-items-clip">
                  <div class="sch-group-items-inner">
                    <button
                      v-for="entry in group.items"
                      :key="entry.key"
                      type="button"
                      class="sch-plan-row"
                      :class="{ 'is-selected': entry.key === selectedKey }"
                      :aria-pressed="entry.key === selectedKey"
                      @click="select(entry.key)"
                    >
                      <span class="sch-plan-row-top">
                        <span class="sch-plan-row-title">{{
                          entry.definition.name || t('schedules.untitled')
                        }}</span>
                        <span
                          v-if="rowStatus(entry.plan)"
                          class="sch-row-status"
                          :class="rowStatus(entry.plan)?.tone"
                          >{{ rowStatus(entry.plan)?.text }}</span
                        >
                      </span>
                      <span class="sch-plan-row-schedule">{{ cadence(entry.definition) }}</span>
                    </button>
                    <div v-if="!group.items.length" class="sch-group-empty">
                      {{
                        search || workspaceFilter !== 'all'
                          ? t('schedules.noMatch')
                          : t('schedules.empty')
                      }}
                    </div>
                  </div>
                </div>
              </div>
            </section>
          </template>
        </div>
      </section>

      <section class="sch-detail-pane" :aria-label="t('schedules.detailAria')">
        <div v-if="current" class="sch-detail-heading">
          <div class="sch-detail-title">
            <h2 class="sch-detail-name">
              <span>{{ current.name || t('schedules.untitled') }}</span>
              <button
                type="button"
                class="sch-icon-button"
                :aria-label="t('schedules.rename')"
                @click="openRename"
              >
                <ScheduleIcon name="pencil" />
              </button>
            </h2>
            <div class="sch-detail-meta">
              <ScheduleIcon name="folder" /><span>{{
                workspaceName(current.target.workspace_id)
              }}</span
              ><span>·</span><span>{{ nextLabel }}</span>
            </div>
          </div>
          <div class="sch-detail-actions">
            <span class="sch-enabled-control">
              <span>{{
                current.enabled ? t('schedules.enabledOn') : t('schedules.enabledOff')
              }}</span>
              <UiSwitch
                :checked="current.enabled"
                :label="t('schedules.enablePlan')"
                @toggle="edit((draft) => (draft.enabled = !draft.enabled))"
              />
            </span>
            <button
              type="button"
              class="sch-button sch-button-quiet"
              :disabled="acting || saving || activeRun?.state === 'stopping'"
              @click="activeRun ? stopRun() : runNow()"
            >
              <ScheduleIcon :name="activeRun ? 'x' : 'play'" /><span>{{
                activeRun ? t('schedules.stopRun') : t('schedules.run')
              }}</span>
            </button>
            <button
              ref="moreButton"
              type="button"
              class="sch-icon-button"
              :aria-label="t('schedules.more')"
              aria-haspopup="menu"
              :aria-expanded="menu === 'more'"
              @click="menu = menu === 'more' ? '' : 'more'"
            >
              <ScheduleIcon name="ellipsis" />
            </button>
          </div>
        </div>
        <div
          ref="detailScroll"
          class="sch-detail-scroll"
          role="region"
          :aria-label="t(`schedules.region_${view}`)"
        >
          <template v-if="current">
            <ScheduleSettings
              v-if="view === 'settings'"
              :key="selectedKey"
              :definition="current"
              :options="options"
              :conversations="currentConversations"
              :preview="preview"
              :advanced-open="advancedOpen"
              :edit="edit"
              @toggle-advanced="advancedOpen = !advancedOpen"
            />
            <ScheduleHistory
              v-else-if="view === 'history'"
              :runs="runs"
              :timezone="current.schedule.timezone"
              :can-run="!acting && !activeRun"
              @back="view = 'settings'"
              @open="dialog = { kind: 'run', run: $event }"
              @run="runNow"
            />
            <ScheduleYaml
              v-else
              :text="yamlText"
              :revision="current.revision"
              :error="yamlError"
              :applying="yamlApplying"
              @back="view = 'settings'"
              @input="onYamlInput"
              @copy="copyText(yamlText || '')"
              @download="downloadText(yamlText || '', yamlFilename(current.name))"
              @apply="applyYaml()"
            />
          </template>
          <div v-else-if="!loading" class="sch-empty-state">
            <ScheduleIcon name="timer" />
            <p>{{ t('schedules.emptyCreate') }}</p>
            <button type="button" class="sch-button sch-button-primary" @click="openCreate">
              {{ t('schedules.create') }}
            </button>
          </div>
        </div>
        <footer class="sch-detail-footer">
          <span :class="{ 'is-dirty': currentDirty }" role="status">{{ saveState }}</span>
          <button
            type="button"
            class="sch-button sch-button-primary"
            :disabled="!currentDirty || saving"
            @click="saveCurrent()"
          >
            {{ t('schedules.save') }}
          </button>
        </footer>
      </section>
    </div>

    <UiMenu
      :open="menu === 'create'"
      :anchor="createButton"
      :items="[
        { value: 'import', label: t('schedules.importConfig'), icon: 'file' },
        { value: 'create', label: t('schedules.create'), icon: 'plus' }
      ]"
      actions
      align-end
      :label="t('schedules.createOrImport')"
      @close="closeMenu"
      @select="onCreateMenu"
    />
    <UiMenu
      :open="menu === 'filter'"
      :anchor="filterButton"
      :items="filterItems"
      :value="workspaceFilter"
      :label="t('schedules.filterWorkspace', { name: filterLabel })"
      @close="closeMenu"
      @select="workspaceFilter = $event"
    />
    <UiMenu
      :open="menu === 'more'"
      :anchor="moreButton"
      :items="moreItems"
      actions
      :label="t('schedules.more')"
      @close="closeMenu"
      @select="onMore"
    />

    <Teleport to="body">
      <Transition name="sch-modal" mode="out-in">
        <ScheduleDialog
          v-if="dialog?.kind === 'create'"
          :title="t('schedules.create')"
          @close="dialog = null"
        >
          <div class="sch-field">
            <label for="sch-new-name">{{ t('schedules.planName') }}</label>
            <input
              id="sch-new-name"
              ref="dialogInput"
              v-model="dialog.name"
              class="sch-input"
              maxlength="80"
              :placeholder="t('schedules.namePlaceholder')"
              @keydown.enter.prevent="confirmCreate"
            />
          </div>
          <div class="sch-field">
            <span class="sch-field-label">{{ t('schedules.workspace') }}</span>
            <UiSelect
              v-model="dialog.workspace"
              :options="options.workspaces.map((item) => ({ value: item.id, label: item.label }))"
              :label="t('schedules.workspace')"
              icon="folder"
            />
          </div>
          <template #actions>
            <button
              type="button"
              class="sch-button sch-button-quiet sch-g3"
              data-g3="5"
              @click="dialog = null"
            >
              {{ t('common.cancel') }}
            </button>
            <button
              type="button"
              class="sch-button sch-button-primary sch-g3"
              data-g3="6"
              @click="confirmCreate"
            >
              {{ t('schedules.createConfirm') }}
            </button>
          </template>
        </ScheduleDialog>

        <ScheduleDialog
          v-else-if="dialog?.kind === 'rename'"
          :title="t('schedules.rename')"
          @close="dialog = null"
        >
          <div class="sch-field">
            <label for="sch-rename">{{ t('schedules.planName') }}</label>
            <input
              id="sch-rename"
              v-model="dialog.name"
              class="sch-input"
              maxlength="80"
              @keydown.enter.prevent="confirmRename"
            />
          </div>
          <template #actions>
            <button
              type="button"
              class="sch-button sch-button-quiet sch-g3"
              data-g3="5"
              @click="dialog = null"
            >
              {{ t('common.cancel') }}
            </button>
            <button
              type="button"
              class="sch-button sch-button-primary sch-g3"
              data-g3="6"
              @click="confirmRename"
            >
              {{ t('common.confirm') }}
            </button>
          </template>
        </ScheduleDialog>

        <ScheduleDialog
          v-else-if="dialog?.kind === 'saveRun'"
          :title="t('schedules.saveRunTitle')"
          @close="dialog = null"
        >
          <p>{{ t('schedules.saveRunBody') }}</p>
          <template #actions>
            <button
              type="button"
              class="sch-button sch-button-quiet sch-g3"
              data-g3="5"
              @click="dialog = null"
            >
              {{ t('common.cancel') }}
            </button>
            <button
              type="button"
              class="sch-button sch-button-primary sch-g3"
              data-g3="6"
              @click="confirmSaveRun"
            >
              {{ t('schedules.saveRun') }}
            </button>
          </template>
        </ScheduleDialog>

        <ScheduleDialog
          v-else-if="dialog?.kind === 'delete'"
          :title="t('schedules.deleteTitle')"
          :busy="dialog.busy"
          @close="dialog = null"
        >
          <p>{{ t('schedules.deleteBody', { name: current?.name || t('schedules.untitled') }) }}</p>
          <template #actions>
            <button
              type="button"
              class="sch-button sch-button-quiet sch-g3"
              data-g3="5"
              :disabled="dialog.busy"
              @click="dialog = null"
            >
              {{ t('common.cancel') }}
            </button>
            <button
              type="button"
              class="sch-button sch-button-danger sch-g3"
              data-g3="6"
              :disabled="dialog.busy"
              @click="confirmDelete"
            >
              {{ t('schedules.delete') }}
            </button>
          </template>
        </ScheduleDialog>

        <ScheduleDialog
          v-else-if="dialog?.kind === 'overwrite'"
          :title="t('schedules.overwriteTitle')"
          :busy="dialog.busy"
          @close="dialog = null"
        >
          <p>{{ t('schedules.overwriteBody', { name: dialog.name }) }}</p>
          <template #actions>
            <button
              type="button"
              class="sch-button sch-button-quiet sch-g3"
              data-g3="5"
              :disabled="dialog.busy"
              @click="dialog = null"
            >
              {{ t('common.cancel') }}
            </button>
            <button
              type="button"
              class="sch-button sch-button-primary sch-g3"
              data-g3="6"
              :disabled="dialog.busy"
              @click="confirmOverwrite"
            >
              {{ t('schedules.overwriteConfirm') }}
            </button>
          </template>
        </ScheduleDialog>

        <ScheduleDialog
          v-else-if="dialog?.kind === 'importError'"
          :title="t('schedules.importFailed')"
          @close="dialog = null"
        >
          <p>{{ dialog.message }}</p>
          <template #actions>
            <button
              type="button"
              class="sch-button sch-button-quiet sch-g3"
              data-g3="5"
              @click="dialog = null"
            >
              {{ t('common.close') }}
            </button>
          </template>
        </ScheduleDialog>

        <ScheduleDialog
          v-else-if="dialog?.kind === 'run'"
          :title="runTitle(dialog.run)"
          wide
          @close="dialog = null"
        >
          <p>
            {{
              joinWide(
                shortDate(
                  dialog.run.scheduled_for || dialog.run.created_at,
                  current?.schedule.timezone
                ),
                t(`schedules.state_${dialog.run.state}`),
                runDuration(dialog.run)
              )
            }}
          </p>
          <div v-if="dialog.run.summary" class="sch-run-summary">
            <h3>{{ t('schedules.runSummary') }}</h3>
            <p>{{ dialog.run.summary }}</p>
          </div>
          <div v-if="dialog.run.reason" class="sch-run-summary">
            <h3>{{ t('schedules.runReason') }}</h3>
            <p>{{ dialog.run.reason }}</p>
          </div>
          <template #actions>
            <button
              type="button"
              class="sch-button sch-button-quiet sch-g3"
              data-g3="5"
              @click="dialog = null"
            >
              {{ t('common.close') }}
            </button>
            <button
              type="button"
              class="sch-button sch-button-primary sch-g3"
              data-g3="6"
              :disabled="!dialog.run.conversation_id"
              @click="openConversation(dialog.run)"
            >
              {{ t('schedules.openConversation') }}
            </button>
          </template>
        </ScheduleDialog>
      </Transition>
    </Teleport>
  </div>
</template>
