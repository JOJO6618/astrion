<template>
  <MessageActions
    :class="{ 'message-inline-actions': inline }"
    :token-speed="inline ? null : tokenSpeed"
    tabindex="0"
    @click.stop
  >
    <MessageActionButton
      v-if="!inline"
      :icon="copySucceeded ? ICONS.check : ICONS.copy"
      :copied="copySucceeded"
      :label="copySucceeded ? $t('common.copied') : $t('common.copy')"
      @click="copy"
    />
    <MessageActionButton
      v-if="!message.live && textOf(message)"
      :icon="ICONS.pencil"
      :label="$t('chatMode.edit')"
      @click="$emit('edit', message)"
    />
    <MessageActionButton
      v-if="final && !inline"
      :icon="ICONS.refreshCw"
      :disabled="busy"
      :label="$t('chatMode.regenerate')"
      @click="$emit('regenerate', message)"
    />
    <template #after-speed>
      <template v-if="versions.length > 1 && !inline">
        <MessageActionButton
          :disabled="index <= 0"
          :label="$t('chatMode.previous')"
          @click="$emit('browse', versions[index - 1].id)"
        >
          ‹
        </MessageActionButton>
        <span class="assistant-token-speed-value">{{ index + 1 }}/{{ versions.length }}</span>
        <MessageActionButton
          :disabled="index >= versions.length - 1"
          :label="$t('chatMode.next')"
          @click="$emit('browse', versions[index + 1].id)"
        >
          ›
        </MessageActionButton>
      </template>
    </template>
  </MessageActions>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue';
import type { ChatMessage } from '@/chat/types';
import { textOf } from '@/chat/types';
import { ICONS } from '@/utils/icons';
import MessageActions from '@/components/chat/MessageActions.vue';
import MessageActionButton from '@/components/chat/MessageActionButton.vue';
const props = defineProps<{
  message: ChatMessage;
  final?: boolean;
  inline?: boolean;
  busy: boolean;
  versions: Array<{ id: string }>;
  index: number;
  tokenSpeed?: string | null;
  copyText?: () => Promise<void>;
  copied?: boolean;
}>();
const emit = defineEmits(['edit', 'regenerate', 'browse', 'error']);
const locallyCopied = ref(false);
const copySucceeded = computed(() => props.copyText ? !!props.copied : locallyCopied.value);
let copyTimer: ReturnType<typeof setTimeout> | undefined;
async function copy() {
  try {
    if (props.copyText) {
      await props.copyText();
      return;
    }
    await navigator.clipboard.writeText(textOf(props.message));
    locallyCopied.value = true;
    clearTimeout(copyTimer);
    copyTimer = setTimeout(() => { locallyCopied.value = false; }, 5000);
  } catch (cause) {
    emit('error', cause);
  }
}
onBeforeUnmount(() => clearTimeout(copyTimer));
</script>
