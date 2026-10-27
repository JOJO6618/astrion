<template>
  <section
    v-if="!handledHandoff(state)"
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
      <span v-if="counting">{{ $t('chatMode.expires', { seconds }) }}</span>
    </div>
    <p v-if="state.error" role="status">{{ state.error }}</p>
    <p v-if="state.status === 'preparing'">{{ $t('chatMode.handoffBusy') }}</p>
  </section>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { chatApi, command, operationId } from '@/chat/api';
import { handledHandoff } from '@/chat/handoff';
import type { Handoff } from '@/chat/handoff';
import { useConversationTabsStore } from '@/stores/conversationTabs';

const props = defineProps<{
  conversationId: string;
  handoffId: string;
  presentationId: string;
  initialState: Handoff;
}>();
const emit = defineEmits<{ (event: 'changed'): void; (event: 'error', cause: unknown): void }>();
const state = ref<Handoff>({ ...props.initialState });
const working = ref(false);
const now = ref(Date.now());
let timer = 0;
let pollTimer = 0;
let offset = 0;
let disposed = false;
let suspended = false;
let requestEpoch = 0;
let awaitingAgent = false;
const base = `/conversations/${props.conversationId}/handoff`;
const counting = computed(
  () =>
    !suspended &&
    state.value.status === 'pending' &&
    state.value.countdown_enabled &&
    state.value.presentation_id === props.presentationId &&
    !!state.value.deadline
);
const seconds = computed(() =>
  Math.max(0, Math.ceil(((state.value.deadline || 0) * 1000 - now.value - offset) / 1000))
);
async function apply(value: Handoff) {
  if (disposed) return;
  state.value = value;
  offset = (value.server_time || Date.now() / 1000) * 1000 - Date.now();
  if (value.status === 'completed' && awaitingAgent && value.agent_url) {
    awaitingAgent = false;
    try {
      const tabs = useConversationTabsStore();
      if (tabs.enabled)
        await tabs.openConversationTab({
          conversationId: value.agent_conversation_id!,
          workspaceId: value.workspace_id!
        });
      else window.location.assign(value.agent_url);
    } catch (cause) {
      if (!disposed) emit('error', cause);
    }
  }
  if (!disposed && handledHandoff(value)) emit('changed');
}
async function poll() {
  window.clearTimeout(pollTimer);
  const owner = requestEpoch;
  try {
    const value = counting.value
      ? await command<Handoff>(base, {
          operation_id: operationId(),
          handoff_id: props.handoffId,
          presentation_id: props.presentationId,
          decision: 'observe'
        })
      : await chatApi<Handoff>(`${base}?handoff_id=${encodeURIComponent(props.handoffId)}`);
    if (owner !== requestEpoch) return;
    await apply(value);
  } catch (cause) {
    if (!disposed && owner === requestEpoch) emit('error', cause);
  }
  if (!disposed && owner === requestEpoch && !handledHandoff(state.value))
    pollTimer = window.setTimeout(() => void poll(), counting.value || awaitingAgent ? 700 : 2000);
}
async function decide(decision: string) {
  if (working.value || handledHandoff(state.value)) return;
  working.value = true;
  requestEpoch++;
  window.clearTimeout(pollTimer);
  awaitingAgent = decision === 'accept';
  try {
    await apply(
      await command<Handoff>(base, {
        operation_id: operationId(),
        handoff_id: props.handoffId,
        decision
      })
    );
  } catch (cause) {
    awaitingAgent = false;
    if (!disposed) emit('error', cause);
  } finally {
    if (!disposed) {
      working.value = false;
      if (!handledHandoff(state.value)) pollTimer = window.setTimeout(() => void poll(), 700);
    }
  }
}
function suspend() {
  if (!counting.value) return;
  suspended = true;
  state.value = { ...state.value, countdown_enabled: false, deadline: null };
  void chatApi(base, {
    method: 'POST',
    keepalive: true,
    body: JSON.stringify({
      operation_id: operationId(),
      handoff_id: props.handoffId,
      presentation_id: props.presentationId,
      decision: 'pause'
    })
  }).catch(() => {});
}
function visibilityChanged() {
  if (window.document.hidden) suspend();
}
const decline = () => decide('decline');
const accept = () => decide('accept');
onMounted(() => {
  visibilityChanged();
  void poll();
  window.addEventListener('pagehide', suspend);
  window.document.addEventListener('visibilitychange', visibilityChanged);
  timer = window.setInterval(() => {
    now.value = Date.now();
  }, 250);
});
onBeforeUnmount(() => {
  suspend();
  disposed = true;
  window.clearInterval(timer);
  window.clearTimeout(pollTimer);
  window.removeEventListener('pagehide', suspend);
  window.document.removeEventListener('visibilitychange', visibilityChanged);
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
