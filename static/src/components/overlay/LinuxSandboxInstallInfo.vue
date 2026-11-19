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
  </div>
  <div v-if="status?.install_command" class="ui-dialog-field">
    <div class="ui-dialog-label">
      <span>{{ $t('sandbox.linuxCommand') }}</span>
      <button type="button" class="ui-dialog-link" @click="copyCommand(status.install_command)">
        {{ $t(`common.${copyState}`) }}
      </button>
    </div>
    <pre ref="commandEl" class="ui-dialog-code" tabindex="0">{{ status.install_command }}</pre>
  </div>
</template>

<style scoped>
/* 放在 UiDialog 正文里：与弹窗正文同为 14/22，段落间 6 */
.linux-sandbox-info {
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: var(--text-secondary);
  font-size: 14px;
  line-height: 22px;
}

.linux-sandbox-info p {
  margin: 0;
}
</style>
