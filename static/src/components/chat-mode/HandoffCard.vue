<template>
  <section
    v-if="state && !['declined', 'superseded'].includes(state.status)"
    class="handoff-card"
    :aria-label="$t('chatMode.switchAgent')"
  >
    <div class="handoff-card__heading">{{ $t('chatMode.suggestedAgent') }}</div>
    <p v-if="state.reason">{{ state.reason }}</p>
    <div v-if="state.status === 'pending'" class="handoff-card__actions">
      <button type="button" @click="select">{{ $t('chatMode.switchAgent') }}</button>
      <button type="button" @click="decline">{{ $t('chatMode.stayChat') }}</button>
      <span>{{ $t('chatMode.expires', { seconds }) }}</span>
    </div>
    <form v-else-if="['selecting', 'failed'].includes(state.status)" @submit.prevent="accept">
      <ChatChoice
        v-model="workspace"
        :disabled="!!state.target_workspace_id"
        :label="$t('chatMode.workspace')"
        :options="options"
      />
      <template v-if="workspace === 'new'">
        <input
          v-model="name"
          :readonly="!!state.target_workspace_id"
          :aria-label="$t('chatMode.projectName')"
          :placeholder="$t('chatMode.projectName')"
          maxlength="80"
        />
        <input
          v-if="state.host"
          v-model="path"
          :readonly="!!state.target_workspace_id"
          :aria-label="$t('chatMode.projectPath')"
          :placeholder="$t('chatMode.projectPath')"
        />
      </template>
      <p v-if="state.error" role="status">{{ state.error }}</p>
      <div class="handoff-card__actions">
        <button type="submit" :disabled="working">{{ $t('chatMode.switchAgent') }}</button
        ><button type="button" @click="decline">{{ $t('chatMode.stayChat') }}</button>
      </div>
    </form>
    <p v-else-if="state.status === 'preparing'">{{ $t('chatMode.handoffBusy') }}</p>
    <button v-else-if="state.status === 'completed'" type="button" @click="openAgent(state)">
      {{ $t('chatMode.switchAgent') }}
    </button>
  </section>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { t } from '@/locales';
import { chatApi, command, operationId } from '@/chat/api';
import { useConversationTabsStore } from '@/stores/conversationTabs';
import ChatChoice from './ChatChoice.vue';
interface Handoff {
  id: string;
  status: string;
  reason: string;
  deadline: number;
  server_time: number;
  host: boolean;
  default_path: string;
  projects: Array<{ id: string; label: string }>;
  error?: string;
  agent_conversation_id?: string;
  workspace_id?: string;
  agent_url?: string;
  target_workspace_id?: string;
  selection?: { workspace_id?: string; project_name?: string; project_path?: string };
}
const props = defineProps<{ conversationId: string; messageId: string }>();
const emit = defineEmits<{ (event: 'changed'): void; (event: 'error', cause: unknown): void }>();
const state = ref<Handoff | null>(null);
const workspace = ref<string | null>('new');
const name = ref('');
const path = ref('');
const working = ref(false);
const now = ref(Date.now());
let timer = 0;
let pollTimer = 0;
let offset = 0;
let disposed = false;
let navigating = false;
let selectionRestored = false;
let awaitingAgent = false;
const base = `/conversations/${props.conversationId}/handoff`;
const seconds = computed(() =>
  Math.max(0, Math.ceil(((state.value?.deadline || 0) * 1000 - now.value - offset) / 1000))
);
const options = computed(() => [
  ...(state.value?.projects || []).map((item) => ({ value: item.id, label: item.label })),
  { value: 'new', label: t('chatMode.createProject') }
]);
async function apply(value: Handoff) {
  if (disposed) return;
  state.value = value;
  offset = value.server_time * 1000 - Date.now();
  if (!path.value) path.value = value.default_path;
  if (!selectionRestored && value.selection) {
    workspace.value = value.selection.workspace_id || 'new';
    name.value = value.selection.project_name || '';
    path.value = value.selection.project_path || value.default_path;
    selectionRestored = true;
  }
  if (value.status === 'preparing') awaitingAgent = true;
  if (value.status === 'completed' && awaitingAgent) await openAgent(value);
  if (['declined', 'superseded'].includes(value.status)) emit('changed');
}
async function openAgent(value: Handoff) {
  if (disposed || navigating || !value.agent_url) return;
  navigating = true;
  try {
    const tabs = useConversationTabsStore();
    if (tabs.enabled)
      await tabs.openConversationTab({
        conversationId: value.agent_conversation_id!,
        workspaceId: value.workspace_id!
      });
    else window.location.assign(value.agent_url);
  } catch (cause) {
    navigating = false;
    emit('error', cause);
  }
}
async function poll() {
  try {
    await apply(await chatApi<Handoff>(`${base}?message_id=${props.messageId}`));
  } catch (cause) {
    if (!disposed) emit('error', cause);
  }
  if (!disposed && state.value && ['pending', 'preparing'].includes(state.value.status))
    pollTimer = window.setTimeout(() => void poll(), 700);
}
async function decide(decision: string, payload = {}) {
  working.value = true;
  try {
    await apply(
      await command<Handoff>(base, {
        operation_id: operationId(),
        message_id: props.messageId,
        decision,
        ...payload
      })
    );
  } catch (cause) {
    emit('error', cause);
  } finally {
    working.value = false;
  }
}
async function select() {
  await decide('select');
}
async function decline() {
  await decide('decline');
}
async function accept() {
  await decide('accept', {
    workspace_id: workspace.value === 'new' ? null : workspace.value,
    project_name: name.value,
    project_path: path.value
  });
  if (state.value?.status === 'preparing') void poll();
}
onMounted(() => {
  void poll();
  timer = window.setInterval(() => {
    now.value = Date.now();
  }, 250);
});
onBeforeUnmount(() => {
  disposed = true;
  window.clearInterval(timer);
  window.clearTimeout(pollTimer);
});
</script>
<style scoped lang="scss">
.handoff-card {
  margin: 20px 0 32px;
  padding: 18px 0;
  border-top: 1px solid var(--border-default);
  border-bottom: 1px solid var(--border-default);
  color: var(--text-primary);
}
.handoff-card__heading {
  font-weight: 600;
}
.handoff-card p {
  font-size: 13px;
  color: var(--text-secondary);
  margin: 8px 0 12px;
}
.handoff-card__actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 9px;
}
.handoff-card button {
  height: 36px;
  padding: 0 12px;
  border: 1px solid var(--border-default);
  background: var(--surface-card);
  color: var(--text-primary);
  border-radius: 6px;
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
.handoff-card__actions > span {
  font-size: 12px;
  color: var(--text-secondary);
}
.handoff-card input {
  box-sizing: border-box;
  display: block;
  width: min(100%, 520px);
  height: 38px;
  margin: 8px 0;
  padding: 0 10px;
  border: 1px solid var(--border-default);
  color: var(--text-primary);
  background: var(--surface-soft);
  border-radius: 5px;
  font: inherit;
}
</style>
