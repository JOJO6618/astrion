<template>
  <section
    v-if="state && !['declined', 'superseded'].includes(state.status)"
    class="handoff-card"
    :aria-label="$t('chatMode.switchAgent')"
  >
    <div class="handoff-card__heading">{{ $t('chatMode.suggestedAgent') }}</div>
    <p v-if="state.reason">{{ state.reason }}</p>
    <div
      v-if="['pending', 'selecting', 'failed'].includes(state.status)"
      class="handoff-card__actions"
    >
      <button type="button" :disabled="working" @click="accept">
        {{ $t('chatMode.switchAgent') }}
      </button>
      <button type="button" :disabled="working" @click="decline">
        {{ $t('chatMode.stayChat') }}
      </button>
      <span v-if="state.status === 'pending'">{{ $t('chatMode.expires', { seconds }) }}</span>
    </div>
    <p v-if="state.error" role="status">{{ state.error }}</p>
    <p v-if="state.status === 'preparing'">{{ $t('chatMode.handoffBusy') }}</p>
    <button v-else-if="state.status === 'completed'" type="button" @click="openAgent(state)">
      {{ $t('chatMode.switchAgent') }}
    </button>
  </section>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { chatApi, command, operationId } from '@/chat/api';
import { useConversationTabsStore } from '@/stores/conversationTabs';
interface Handoff {
  id: string;
  status: string;
  reason: string;
  deadline: number;
  server_time: number;
  error?: string;
  agent_conversation_id?: string;
  workspace_id?: string;
  agent_url?: string;
}
const props = defineProps<{ conversationId: string; messageId: string }>();
const emit = defineEmits<{ (event: 'changed'): void; (event: 'error', cause: unknown): void }>();
const state = ref<Handoff | null>(null);
const working = ref(false);
const now = ref(Date.now());
let timer = 0;
let pollTimer = 0;
let offset = 0;
let disposed = false;
let navigating = false;
let awaitingAgent = false;
const base = `/conversations/${props.conversationId}/handoff`;
const seconds = computed(() =>
  Math.max(0, Math.ceil(((state.value?.deadline || 0) * 1000 - now.value - offset) / 1000))
);
async function apply(value: Handoff) {
  if (disposed) return;
  state.value = value;
  offset = value.server_time * 1000 - Date.now();
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
  window.clearTimeout(pollTimer);
  try {
    await apply(await chatApi<Handoff>(`${base}?message_id=${props.messageId}`));
  } catch (cause) {
    if (!disposed) emit('error', cause);
  }
  if (!disposed && state.value && ['pending', 'preparing'].includes(state.value.status))
    pollTimer = window.setTimeout(() => void poll(), 700);
}
async function decide(decision: string) {
  if (working.value) return;
  working.value = true;
  try {
    await apply(
      await command<Handoff>(base, {
        operation_id: operationId(),
        message_id: props.messageId,
        decision
      })
    );
    if (!disposed && state.value?.status === 'preparing') {
      window.clearTimeout(pollTimer);
      pollTimer = window.setTimeout(() => void poll(), 700);
    }
  } catch (cause) {
    if (!disposed) emit('error', cause);
  } finally {
    if (!disposed) working.value = false;
  }
}
const decline = () => decide('decline');
const accept = () => decide('accept');
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
.handoff-card button:disabled {
  opacity: 0.5;
  cursor: default;
}
.handoff-card__actions > span {
  font-size: 12px;
  color: var(--text-secondary);
}
</style>
