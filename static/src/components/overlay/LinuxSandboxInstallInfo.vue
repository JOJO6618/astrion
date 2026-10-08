<script setup lang="ts">
import { ref } from 'vue';
import type { SandboxStatus } from '@/stores/sandboxSetup';

defineProps<{ status: SandboxStatus | null }>();
const copyState = ref<'copy' | 'copied' | 'copyFailed'>('copy');
const commandEl = ref<HTMLElement | null>(null);

async function copyCommand(command: string) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(command);
    } else if (commandEl.value) {
      const selection = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(commandEl.value);
      selection?.removeAllRanges();
      selection?.addRange(range);
      if (!document.execCommand('copy')) throw new Error('copy');
    } else {
      throw new Error('copy');
    }
    copyState.value = 'copied';
  } catch {
    copyState.value = 'copyFailed';
  }
}
</script>

<template>
  <div class="linux-sandbox-info">
    <p>{{ $t('sandbox.linuxIntro') }}</p>
    <p>{{ $t('sandbox.linuxNetwork') }}</p>
    <p>
      {{
        $t('sandbox.linuxLocation', {
          path: status?.install_path || '/usr/local/libexec/astrion-sandbox'
        })
      }}
    </p>
    <p v-if="status?.install_command">
      {{ status?.can_install ? $t('sandbox.linuxAdmin') : $t('sandbox.linuxTerminal') }}
    </p>
    <template v-if="status?.install_command">
      <div class="linux-sandbox-command-label">
        <span>{{ $t('sandbox.linuxCommand') }}</span>
        <button type="button" @click="copyCommand(status.install_command)">
          {{ $t(`common.${copyState}`) }}
        </button>
      </div>
      <pre ref="commandEl" class="linux-sandbox-command" tabindex="0">{{
        status.install_command
      }}</pre>
    </template>
  </div>
</template>

<style scoped>
.linux-sandbox-info {
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.6;
}
.linux-sandbox-info p {
  margin: 0 0 10px;
}
.linux-sandbox-command-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 32px;
}
.linux-sandbox-command-label button {
  height: 28px;
  padding: 0 10px;
  color: var(--text-primary);
  background: var(--surface-card);
  border: 1px solid var(--border-default);
  border-radius: 4px;
  cursor: pointer;
}
.linux-sandbox-command-label button:hover {
  background: var(--hover-bg);
}
.linux-sandbox-command {
  max-height: 96px;
  margin: 4px 0 0;
  padding: 10px 0;
  border-top: 1px solid var(--border-default);
  border-bottom: 1px solid var(--border-default);
  color: var(--text-primary);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  overflow: auto;
  user-select: text;
  scrollbar-width: thin;
}
</style>
