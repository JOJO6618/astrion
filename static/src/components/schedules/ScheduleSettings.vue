<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { t } from '@/locales';
import { useModelStore } from '@/stores/model';
import ScheduleIcon from './ScheduleIcon.vue';
import ScheduleSelect from './ScheduleSelect.vue';
import ScheduleModelSelect from './ScheduleModelSelect.vue';
import ScheduleSwitch from './ScheduleSwitch.vue';
import { shortDate } from './scheduleFormat';
import type { ScheduleConversation, ScheduleDefinition, ScheduleOptions } from './useSchedules';

type Draft = ScheduleDefinition;
type Configuration = Draft['configuration'];

const props = defineProps<{
  definition: Draft;
  options: ScheduleOptions;
  conversations: ScheduleConversation[] | null;
  preview: { dates: string[]; error: string };
  advancedOpen: boolean;
  /** 所有修改通过该回调写入页面持有的草稿。 */
  edit: (mutate: (draft: Draft) => void) => void;
}>();
const emit = defineEmits<{ (event: 'toggle-advanced'): void }>();

const d = computed(() => props.definition);
const s = computed(() => props.definition.schedule);
const c = computed(() => props.definition.configuration);
const existing = computed(() => d.value.target.conversation.mode === 'existing');
const inherit = computed(() => c.value.mode === 'inherit');
const goalOn = computed(() => d.value.goal.mode !== 'off');
const option = (value: string, key = value) => ({ value, label: t(`schedules.${key}`) });

const frequencies = computed(() =>
  ['daily', 'weekly', 'monthly', 'interval', 'once', 'cron'].map((value) =>
    option(value, `freq_${value}`)
  )
);
const timezones = computed(() => {
  const list = props.options.timezones.length ? [...props.options.timezones] : [];
  if (s.value.timezone && !list.includes(s.value.timezone)) list.unshift(s.value.timezone);
  return list.map((value) => ({ value, label: value }));
});
const monthDays = computed(() => [
  { value: 'last', label: t('schedules.lastDay') },
  ...Array.from({ length: 31 }, (_, i) => ({
    value: String(i + 1),
    label: t('schedules.dayOfMonth', { day: String(i + 1) })
  }))
]);
const weekdays = [1, 2, 3, 4, 5, 6, 0];
const workspaces = computed(() =>
  props.options.workspaces.map((item) => ({ value: item.id, label: item.label }))
);
const conversationOptions = computed(() => {
  const list = (props.conversations || []).map((item) => ({ value: item.id, label: item.title }));
  const id = d.value.target.conversation.id;
  if (id && !list.some((item) => item.value === id)) list.unshift({ value: id, label: id });
  return list;
});
// 与主页面 EffortSlider 一致：全集 low~max，按所选模型的 supportedReasoningLevels 过滤。
const EFFORT_LEVELS = ['low', 'medium', 'high', 'xhigh', 'max'];
const modelStore = useModelStore();
const efforts = computed(() => {
  const model = (modelStore.allModels || []).find((item) => item.key === c.value.model_key);
  const supported = model?.supportedReasoningLevels;
  const filtered = Array.isArray(supported)
    ? EFFORT_LEVELS.filter((level) => supported.some((item) => item?.effort === level))
    : [];
  const levels = filtered.length ? filtered : EFFORT_LEVELS;
  return [
    { value: '', label: t('schedules.effortDefault') },
    ...levels.map((value) => option(value, `effort_${value}`))
  ];
});
const thinkingModes = computed(() => [
  { value: 'thinking', label: t('schedules.thinking_on') },
  { value: 'fast', label: t('schedules.thinking_off') }
]);
const networkModes = computed(() => [
  { value: 'local', label: t('schedules.network_local') },
  { value: 'online', label: t('schedules.network_online') }
]);
const workModes = computed(() => ['execute', 'ask', 'plan'].map((v) => option(v, `mode_${v}`)));
const permissions = computed(() =>
  ['readonly', 'approval', 'auto_approval', 'unrestricted'].map((v) => option(v, `perm_${v}`))
);
const environments = computed(() => ['sandbox', 'direct'].map((v) => option(v, `env_${v}`)));
const worktreeModes = computed(() => [
  { value: 'main', label: t('schedules.worktreeMain') },
  { value: 'branch', label: t('schedules.worktreeBranch') }
]);
const goalActions = computed(() => [
  { value: 'start', label: t('schedules.goalStart') },
  { value: 'continue', label: t('schedules.goalContinue') }
]);
const busyOptions = computed(() => ['skip', 'merge'].map((v) => option(v, `busy_${v}`)));
const missedOptions = computed(() => ['skip', 'latest'].map((v) => option(v, `missed_${v}`)));

// ---- 执行时间 ----
const joinTimes = (times: string[] | undefined) => (times || []).join(', ');
const splitTimes = (text: string) => text.split(',').map((item) => item.trim());
const timeText = ref(joinTimes(s.value.times));
watch(
  () => s.value.times,
  (times) => {
    if (JSON.stringify(splitTimes(timeText.value)) !== JSON.stringify(times || []))
      timeText.value = joinTimes(times);
  },
  { deep: true }
);
function onTimes(text: string) {
  timeText.value = text;
  props.edit((draft) => {
    draft.schedule.times = splitTimes(text);
  });
}
function tomorrow() {
  const date = new Date(Date.now() + 86400000);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}
function setFrequency(type: string) {
  props.edit((draft) => {
    const old = draft.schedule;
    const times = old.times?.length ? old.times : ['09:00'];
    const base = { type, timezone: old.timezone };
    draft.schedule =
      type === 'interval'
        ? { ...base, every: old.every || 2, unit: old.unit || 'hours' }
        : type === 'cron'
          ? { ...base, expression: old.expression || '0 9 * * 1-5' }
          : {
              ...base,
              times,
              ...(type === 'weekly' ? { days: old.days?.length ? old.days : [1] } : {}),
              ...(type === 'monthly' ? { day: old.day ?? 1 } : {}),
              ...(type === 'once' ? { date: old.date || tomorrow() } : {})
            };
  });
}
function toggleWeekday(day: number) {
  props.edit((draft) => {
    const days = draft.schedule.days || [];
    draft.schedule.days = days.includes(day) ? days.filter((item) => item !== day) : [...days, day];
  });
}
function numberOrUndefined(text: string) {
  const digits = text.replace(/\D/g, '');
  return digits ? Number(digits) : undefined;
}

// ---- 执行位置 ----
function setWorkspace(id: string) {
  props.edit((draft) => {
    draft.target.workspace_id = id;
    if (draft.target.conversation.mode === 'existing') draft.target.conversation.id = '';
  });
}
function setConversationMode(mode: string) {
  props.edit((draft) => {
    draft.target.conversation = mode === 'existing' ? { mode, id: '' } : { mode };
    if (mode === 'new' && draft.goal.mode === 'continue') draft.goal.mode = 'start';
  });
}

// ---- 任务指令：纯文本 contenteditable，无原生 textarea 拖拽角 ----
const editor = ref<HTMLElement>();
let composing = false;
function onCompositionStart() {
  composing = true;
}
function onCompositionEnd() {
  composing = false;
  onMessageInput();
}
function onMessageInput() {
  if (composing || !editor.value) return;
  const text = editor.value.innerText.replace(/\r\n?/g, '\n');
  props.edit((draft) => {
    draft.message = text === '\n' ? '' : text;
  });
}
onMounted(() => {
  if (editor.value) editor.value.textContent = d.value.message;
});
watch(
  () => d.value.message,
  (message) => {
    const el = editor.value;
    if (el && document.activeElement !== el && el.innerText !== message) el.textContent = message;
  }
);

// ---- 模型与执行 ----
function customDefaults(draft: Draft): Configuration {
  const model = props.options.models[0];
  return {
    mode: 'custom',
    model_key: model?.value || '',
    thinking: true,
    reasoning_effort: null,
    work_mode: 'execute',
    permission_mode: draft.goal.mode !== 'off' ? 'auto_approval' : 'approval',
    execution_mode: 'sandbox',
    ...(props.options.host_admin ? { network_enabled: false } : {}),
    worktree: 'main'
  };
}
function toggleInherit() {
  props.edit((draft) => {
    draft.configuration =
      draft.configuration.mode === 'inherit' ? customDefaults(draft) : { mode: 'inherit' };
  });
}
function setConfig<K extends keyof Configuration>(key: K, value: Configuration[K]) {
  props.edit((draft) => {
    const config = draft.configuration;
    config[key] = value;
    if (key === 'work_mode' && value === 'plan') {
      config.permission_mode = 'readonly';
      config.execution_mode = 'sandbox';
      draft.goal = { mode: 'off' };
    }
    if (key === 'permission_mode' && value === 'readonly') config.execution_mode = 'sandbox';
  });
}
const worktreeMode = computed(() => (typeof c.value.worktree === 'object' ? 'branch' : 'main'));
const branchName = computed(() =>
  typeof c.value.worktree === 'object' ? c.value.worktree.branch_name : ''
);
function setWorktreeMode(mode: string) {
  setConfig('worktree', mode === 'branch' ? { branch_name: branchName.value } : 'main');
}
// 询问模式由后端禁用 ask_user，目标模式的注入语已禁止交互：仅执行/计划模式提供该开关。
const showAskUser = computed(
  () => !goalOn.value && ['execute', 'plan'].includes(c.value.work_mode || 'execute')
);
const lockedWorkMode = computed(() => (goalOn.value ? t('schedules.lockedExecute') : ''));
const lockedPermission = computed(() =>
  c.value.work_mode === 'plan' ? t('schedules.lockedReadonly') : ''
);
const lockedEnvironment = computed(() =>
  c.value.permission_mode === 'readonly' || c.value.work_mode === 'plan'
    ? t('schedules.lockedSandbox')
    : ''
);

// ---- 目标模式 ----
function toggleGoal() {
  props.edit((draft) => {
    if (draft.goal.mode !== 'off') {
      draft.goal = { mode: 'off' };
      return;
    }
    draft.goal = { mode: 'start' };
    const config = draft.configuration;
    if (config.mode === 'custom') {
      config.work_mode = 'execute';
      if (!['auto_approval', 'unrestricted'].includes(config.permission_mode || ''))
        config.permission_mode = 'auto_approval';
    }
  });
}
function setGoalLimit(key: 'max_turns' | 'max_tokens', text: string) {
  props.edit((draft) => {
    const value = numberOrUndefined(text);
    if (value) draft.goal[key] = value;
    else delete draft.goal[key];
  });
}
</script>
<template>
  <section class="sch-form-section">
    <h3 class="sch-section-title">
      <ScheduleIcon name="clock" />{{ t('schedules.sectionTiming') }}
    </h3>
    <div class="sch-schedule-fields">
      <div class="sch-field">
        <span class="sch-field-label">{{ t('schedules.frequency') }}</span>
        <ScheduleSelect
          :model-value="s.type"
          :options="frequencies"
          :label="t('schedules.frequency')"
          @update:model-value="setFrequency"
        />
      </div>
      <div v-if="!['interval', 'cron'].includes(s.type)" class="sch-field is-time">
        <span class="sch-field-label">{{ t('schedules.time') }}</span>
        <input
          class="sch-input"
          :aria-label="t('schedules.time')"
          :value="timeText"
          placeholder="09:00"
          spellcheck="false"
          @input="onTimes(($event.target as HTMLInputElement).value)"
        />
      </div>
      <div class="sch-field">
        <span class="sch-field-label">{{ t('schedules.timezone') }}</span>
        <ScheduleSelect
          :model-value="s.timezone"
          :options="timezones"
          :label="t('schedules.timezone')"
          @update:model-value="edit((draft) => (draft.schedule.timezone = $event))"
        />
      </div>
    </div>
    <div
      v-if="s.type === 'weekly'"
      class="sch-schedule-extra sch-weekday-picker"
      role="group"
      :aria-label="t('schedules.weekdays')"
    >
      <button
        v-for="day in weekdays"
        :key="day"
        type="button"
        :aria-label="t('schedules.weekdayAria', { day: t(`schedules.weekdayShort${day}`) })"
        :aria-pressed="!!s.days?.includes(day)"
        @click="toggleWeekday(day)"
      >
        {{ t(`schedules.weekdayShort${day}`) }}
      </button>
    </div>
    <div v-else-if="s.type === 'monthly'" class="sch-schedule-extra">
      <div class="sch-field">
        <span class="sch-field-label">{{ t('schedules.monthDay') }}</span>
        <ScheduleSelect
          :model-value="String(s.day ?? 1)"
          :options="monthDays"
          :label="t('schedules.monthDay')"
          @update:model-value="
            edit((draft) => (draft.schedule.day = $event === 'last' ? 'last' : Number($event)))
          "
        />
      </div>
    </div>
    <div v-else-if="s.type === 'interval'" class="sch-schedule-extra sch-field-grid">
      <div class="sch-field">
        <span class="sch-field-label">{{ t('schedules.every') }}</span>
        <input
          class="sch-input"
          :aria-label="t('schedules.everyAria')"
          :value="s.every ?? ''"
          placeholder="2"
          inputmode="numeric"
          @input="
            edit(
              (draft) =>
                (draft.schedule.every = numberOrUndefined(
                  ($event.target as HTMLInputElement).value
                ))
            )
          "
        />
      </div>
      <div class="sch-field">
        <span class="sch-field-label">{{ t('schedules.unit') }}</span>
        <ScheduleSelect
          :model-value="s.unit"
          :options="[option('minutes'), option('hours')]"
          :label="t('schedules.unit')"
          @update:model-value="edit((draft) => (draft.schedule.unit = $event))"
        />
      </div>
    </div>
    <div v-else-if="s.type === 'once'" class="sch-schedule-extra">
      <div class="sch-field">
        <span class="sch-field-label">{{ t('schedules.date') }}</span>
        <input
          class="sch-input"
          :aria-label="t('schedules.date')"
          :value="s.date || ''"
          placeholder="YYYY-MM-DD"
          spellcheck="false"
          @input="
            edit((draft) => (draft.schedule.date = ($event.target as HTMLInputElement).value))
          "
        />
      </div>
    </div>
    <div v-else-if="s.type === 'cron'" class="sch-schedule-extra">
      <div class="sch-field">
        <span class="sch-field-label">{{ t('schedules.cronExpression') }}</span>
        <input
          class="sch-input"
          :aria-label="t('schedules.cronExpression')"
          :value="s.expression || ''"
          placeholder="0 9 * * 1-5"
          spellcheck="false"
          @input="
            edit((draft) => (draft.schedule.expression = ($event.target as HTMLInputElement).value))
          "
        />
      </div>
    </div>
    <div v-if="preview.error" class="sch-next-runs sch-field-error">{{ preview.error }}</div>
    <div v-else class="sch-next-runs">
      <ScheduleIcon name="clock" /><span>{{ t('schedules.next') }}</span>
      <template v-if="preview.dates.length">
        <strong v-for="date in preview.dates" :key="date">{{ shortDate(date, s.timezone) }}</strong>
      </template>
      <span v-else>{{ t('schedules.noFuture') }}</span>
    </div>
  </section>

  <section class="sch-form-section">
    <h3 class="sch-section-title">
      <ScheduleIcon name="folder" />{{ t('schedules.sectionTarget') }}
    </h3>
    <div class="sch-field-grid">
      <div class="sch-field">
        <span class="sch-field-label">{{ t('schedules.workspace') }}</span>
        <ScheduleSelect
          :model-value="d.target.workspace_id"
          :options="workspaces"
          :label="t('schedules.workspace')"
          icon="folder"
          @update:model-value="setWorkspace"
        />
      </div>
      <div class="sch-field">
        <span class="sch-field-label">{{ t('schedules.sendTo') }}</span>
        <div
          class="sch-segmented"
          :class="{ 'is-second': existing }"
          role="group"
          :aria-label="t('schedules.conversationStrategy')"
        >
          <span class="sch-segmented-thumb" aria-hidden="true" />
          <button
            type="button"
            :class="{ 'is-active': !existing }"
            :aria-pressed="!existing"
            @click="setConversationMode('new')"
          >
            {{ t('schedules.newConversation') }}
          </button>
          <button
            type="button"
            :class="{ 'is-active': existing }"
            :aria-pressed="existing"
            @click="setConversationMode('existing')"
          >
            {{ t('schedules.existingConversation') }}
          </button>
        </div>
      </div>
    </div>
    <div class="sch-conversation-field" :class="{ 'is-collapsed': !existing }" :inert="!existing">
      <div class="sch-conversation-clip">
        <div class="sch-conversation-inner">
          <ScheduleSelect
            :model-value="d.target.conversation.id || ''"
            :options="conversationOptions"
            :label="t('schedules.chooseConversation')"
            :placeholder="
              conversations === null ? t('schedules.loading') : t('schedules.chooseConversation')
            "
            icon="chat-bubble"
            @update:model-value="edit((draft) => (draft.target.conversation.id = $event))"
          />
        </div>
      </div>
    </div>
  </section>

  <section class="sch-form-section">
    <h3 class="sch-section-title">{{ t('schedules.sectionMessage') }}</h3>
    <div
      ref="editor"
      class="sch-prompt-input"
      role="textbox"
      aria-multiline="true"
      :aria-label="t('schedules.sectionMessage')"
      contenteditable="plaintext-only"
      spellcheck="false"
      :data-placeholder="t('schedules.messagePlaceholder')"
      @input="onMessageInput"
      @compositionstart="onCompositionStart"
      @compositionend="onCompositionEnd"
    />
  </section>

  <section class="sch-form-section">
    <div class="sch-section-inline">
      <h3 class="sch-section-title">
        <ScheduleIcon name="bot" />{{ t('schedules.sectionModel') }}
      </h3>
      <span class="sch-inherit-label">
        {{ existing ? t('schedules.inheritConversation') : t('schedules.inheritDefault') }}
        <ScheduleSwitch
          :checked="inherit"
          :label="existing ? t('schedules.inheritConversation') : t('schedules.inheritDefault')"
          @toggle="toggleInherit"
        />
      </span>
    </div>
    <template v-if="!inherit">
      <div class="sch-configuration-fields sch-field-grid">
        <div class="sch-field">
          <span class="sch-field-label">{{ t('schedules.model') }}</span>
          <ScheduleModelSelect
            :model-value="c.model_key || ''"
            :models="options.models"
            :label="t('schedules.model')"
            @update:model-value="setConfig('model_key', $event)"
          />
        </div>
        <div class="sch-field">
          <span class="sch-field-label">{{ t('schedules.effort') }}</span>
          <ScheduleSelect
            :model-value="c.reasoning_effort || ''"
            :options="efforts"
            :label="t('schedules.effort')"
            @update:model-value="setConfig('reasoning_effort', $event || null)"
          />
        </div>
        <div class="sch-field">
          <span class="sch-field-label">{{ t('schedules.thinking') }}</span>
          <ScheduleSelect
            :model-value="c.thinking === false ? 'fast' : 'thinking'"
            :options="thinkingModes"
            :label="t('schedules.thinking')"
            @update:model-value="setConfig('thinking', $event === 'thinking')"
          />
        </div>
        <div v-if="options.host_admin" class="sch-field">
          <span class="sch-field-label">{{ t('schedules.network') }}</span>
          <ScheduleSelect
            :model-value="c.network_enabled ? 'online' : 'local'"
            :options="networkModes"
            :label="t('schedules.network')"
            @update:model-value="setConfig('network_enabled', $event === 'online')"
          />
        </div>
        <div class="sch-field">
          <span class="sch-field-label">{{ t('schedules.workMode') }}</span>
          <ScheduleSelect
            :model-value="c.work_mode || 'execute'"
            :options="workModes"
            :label="t('schedules.workMode')"
            :locked-reason="lockedWorkMode"
            @update:model-value="setConfig('work_mode', $event)"
          />
        </div>
        <div class="sch-field">
          <span class="sch-field-label">{{ t('schedules.permission') }}</span>
          <ScheduleSelect
            :model-value="c.permission_mode || 'approval'"
            :options="permissions"
            :label="t('schedules.permission')"
            :locked-reason="lockedPermission"
            @update:model-value="setConfig('permission_mode', $event)"
          />
        </div>
        <div v-if="options.host_admin" class="sch-field">
          <span class="sch-field-label">{{ t('schedules.environment') }}</span>
          <ScheduleSelect
            :model-value="c.execution_mode || 'sandbox'"
            :options="environments"
            :label="t('schedules.environment')"
            :locked-reason="lockedEnvironment"
            @update:model-value="setConfig('execution_mode', $event)"
          />
        </div>
        <div class="sch-field">
          <span class="sch-field-label">{{ t('schedules.worktree') }}</span>
          <ScheduleSelect
            :model-value="worktreeMode"
            :options="worktreeModes"
            :label="t('schedules.worktree')"
            @update:model-value="setWorktreeMode"
          />
        </div>
        <div v-if="worktreeMode === 'branch'" class="sch-field">
          <span class="sch-field-label">{{ t('schedules.branchName') }}</span>
          <input
            class="sch-input"
            :aria-label="t('schedules.branchName')"
            :value="branchName"
            :placeholder="t('schedules.branchPlaceholder')"
            spellcheck="false"
            @input="
              setConfig('worktree', { branch_name: ($event.target as HTMLInputElement).value })
            "
          />
        </div>
      </div>
      <div v-if="showAskUser" class="sch-configuration-summary">
        <span class="sch-inherit-label">
          {{ t('schedules.askUser') }}
          <ScheduleSwitch
            :checked="!!c.ask_user"
            :label="t('schedules.askUser')"
            @toggle="setConfig('ask_user', !c.ask_user)"
          />
        </span>
      </div>
    </template>
  </section>

  <section class="sch-form-section">
    <div class="sch-goal-row">
      <div class="sch-goal-title">{{ t('schedules.goal') }}</div>
      <ScheduleSwitch :checked="goalOn" :label="t('schedules.goal')" @toggle="toggleGoal" />
    </div>
    <div v-if="goalOn" class="sch-goal-fields">
      <div v-if="existing" class="sch-field sch-goal-action">
        <span class="sch-field-label">{{ t('schedules.goalAction') }}</span>
        <ScheduleSelect
          :model-value="d.goal.mode"
          :options="goalActions"
          :label="t('schedules.goalAction')"
          @update:model-value="edit((draft) => (draft.goal.mode = $event))"
        />
      </div>
      <div class="sch-field-grid">
        <div class="sch-field">
          <span class="sch-field-label">{{ t('schedules.maxTurns') }}</span>
          <input
            class="sch-input"
            :aria-label="t('schedules.maxTurns')"
            :value="d.goal.max_turns ?? ''"
            :placeholder="t('schedules.unlimited')"
            inputmode="numeric"
            @input="setGoalLimit('max_turns', ($event.target as HTMLInputElement).value)"
          />
        </div>
        <div class="sch-field">
          <span class="sch-field-label">{{ t('schedules.maxTokens') }}</span>
          <input
            class="sch-input"
            :aria-label="t('schedules.maxTokens')"
            :value="d.goal.max_tokens ?? ''"
            :placeholder="t('schedules.unlimited')"
            inputmode="numeric"
            @input="setGoalLimit('max_tokens', ($event.target as HTMLInputElement).value)"
          />
        </div>
      </div>
    </div>
  </section>

  <section class="sch-form-section">
    <button
      type="button"
      class="sch-accordion-trigger"
      :aria-expanded="advancedOpen"
      @click="emit('toggle-advanced')"
    >
      <span>{{ t('schedules.advanced') }}</span
      ><ScheduleIcon name="chevron-down" />
    </button>
    <div v-if="advancedOpen" class="sch-advanced-fields">
      <div class="sch-field-grid">
        <div class="sch-field">
          <span class="sch-field-label">{{ t('schedules.busy') }}</span>
          <ScheduleSelect
            :model-value="d.policies.busy"
            :options="busyOptions"
            :label="t('schedules.busy')"
            @update:model-value="edit((draft) => (draft.policies.busy = $event))"
          />
        </div>
        <div class="sch-field">
          <span class="sch-field-label">{{ t('schedules.missed') }}</span>
          <ScheduleSelect
            :model-value="d.policies.missed"
            :options="missedOptions"
            :label="t('schedules.missed')"
            @update:model-value="edit((draft) => (draft.policies.missed = $event))"
          />
        </div>
      </div>
      <div class="sch-notification-row">
        <span>{{ t('schedules.notify') }}</span>
        <ScheduleSwitch
          :checked="d.notifications.enabled"
          :label="t('schedules.notifyAria')"
          @toggle="edit((draft) => (draft.notifications.enabled = !draft.notifications.enabled))"
        />
      </div>
    </div>
  </section>
</template>
