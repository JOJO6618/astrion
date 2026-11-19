<template>
  <UiDialog
    :open="store.dialogVisible"
    :title="$t('sandbox.setupTitle')"
    size="wide"
    @close="onClose"
  >
    <!-- 未开始：状态说明 + 要点 -->
    <template v-if="!progress">
      <p class="ui-dialog-text">{{ stateDescription || $t('sandbox.stateChecking') }}</p>
      <LinuxSandboxInstallInfo v-if="isLinux" :status="store.status" />
      <ul v-else class="sandbox-facts">
        <li>{{ $t('sandbox.introWhat') }}</li>
        <li>{{ $t('sandbox.introEffect') }}</li>
        <li>{{ $t('sandbox.introDisk', { path: installPathHint }) }}</li>
        <li>{{ $t('sandbox.introUninstall', { distro: distroName }) }}</li>
        <li>{{ $t('sandbox.introSecure') }}</li>
      </ul>
      <label class="ui-dialog-switch-row">
        <span>{{ $t('sandbox.neverAgain') }}</span>
        <input v-model="neverChecked" type="checkbox" />
        <UiSwitch decorative :checked="neverChecked" />
      </label>
    </template>

    <!-- 安装中 / 终态：阶段 + 进度 + 步骤 + 日志 -->
    <template v-else>
      <div class="sandbox-progress-head">
        <span class="sandbox-headline" :class="progress.phase" role="status">{{ headline }}</span>
        <span class="sandbox-percent">{{ progressPercent }}%</span>
      </div>
      <div class="ui-dialog-progress sandbox-progress">
        <span :style="{ width: progressPercent + '%' }" />
      </div>
      <p
        v-if="progress.phase === 'error' && progress.error_kind === 'uac_cancelled'"
        class="ui-dialog-note"
      >
        {{ $t('sandbox.uacCancelledHint') }}
      </p>
      <ol class="sandbox-steps">
        <li v-for="(step, i) in steps" :key="i" :class="stepStatus(i + 1)">
          <span class="sandbox-step-icon" aria-hidden="true">
            <UiIcon v-if="stepStatus(i + 1) === 'done'" name="check" />
            <UiIcon v-else-if="stepStatus(i + 1) === 'error'" name="x" />
            <span v-else-if="stepStatus(i + 1) === 'active'" class="sandbox-spinner" />
            <span v-else class="sandbox-dot" />
          </span>
          <span class="sandbox-step-title">{{ step }}</span>
          <span
            v-if="i + 1 === 3 && progress.step_index === 3 && downloadText"
            class="sandbox-step-extra"
          >
            {{ downloadText }}
          </span>
        </li>
      </ol>
      <div v-if="progress.log_tail.length" class="ui-dialog-field">
        <span class="ui-dialog-label">{{ $t('sandbox.logLabel') }}</span>
        <pre ref="logEl" class="ui-dialog-code sandbox-log">{{ progress.log_tail.join('\n') }}</pre>
      </div>
    </template>

    <template v-if="!progress || !progress.active" #actions>
      <template v-if="!progress">
        <UiDialogButton v-if="isLinux" :disabled="store.checking" @click="store.recheck()">
          {{ $t('sandbox.recheck') }}
        </UiDialogButton>
        <UiDialogButton @click="onClose">{{ $t('sandbox.later') }}</UiDialogButton>
        <UiDialogButton
          v-if="!isLinux || store.status?.can_install"
          variant="primary"
          :disabled="store.starting || store.checking || !store.status"
          @click="store.startSetup()"
        >
          {{ store.starting ? $t('common.loading') : $t('sandbox.installNow') }}
        </UiDialogButton>
      </template>
      <template v-else-if="progress.phase === 'error' || progress.phase === 'needs_reboot'">
        <UiDialogButton @click="onClose">{{ $t('common.close') }}</UiDialogButton>
        <UiDialogButton variant="primary" :disabled="store.starting" @click="store.retrySetup()">
          {{ progress.phase === 'needs_reboot' ? $t('sandbox.rebootDone') : $t('common.retry') }}
        </UiDialogButton>
      </template>
      <UiDialogButton v-else variant="primary" @click="onClose">
        {{ $t('common.close') }}
      </UiDialogButton>
    </template>
  </UiDialog>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import UiDialog from '@/components/ui/UiDialog.vue';
import UiDialogButton from '@/components/ui/UiDialogButton.vue';
import UiIcon from '@/components/ui/UiIcon.vue';
import UiSwitch from '@/components/ui/UiSwitch.vue';
import LinuxSandboxInstallInfo from './LinuxSandboxInstallInfo.vue';
import { useSandboxSetupStore } from '@/stores/sandboxSetup';

defineOptions({ name: 'SandboxSetupDialog' });

const { t } = useI18n();
const store = useSandboxSetupStore();

const neverChecked = ref(false);
const logEl = ref<HTMLElement | null>(null);

// 安装日志常显且自动滚动到底部（新行到达时）
watch(
  () => store.progress?.log_tail.length,
  async () => {
    await nextTick();
    if (logEl.value) logEl.value.scrollTop = logEl.value.scrollHeight;
  }
);

const progress = computed(() => store.progress);
const isLinux = computed(() => store.status?.platform === 'linux');
const distroName = computed(() => store.status?.distro_name || 'astrion-sandbox');
// 安装目录与 scripts/setup-wsl-sandbox.ps1 的默认 InstallDir 保持一致
const installPathHint = computed(() => '%USERPROFILE%\\.astrion\\wsl-sandbox');

const stateDescription = computed(() => {
  const s = store.status;
  if (!s || !s.applicable) return '';
  if (isLinux.value)
    return s.state === 'ready' ? t('sandbox.sectionReady') : s.detail || t('sandbox.linuxMissing');
  if (s.state === 'wsl_missing') return t('sandbox.stateWslMissing');
  if (s.state === 'vm_platform_missing') return t('sandbox.stateVmPlatformMissing');
  if (s.state === 'distro_missing') return t('sandbox.stateDistroMissing');
  if (s.state === 'bwrap_missing') return t('sandbox.stateBwrapMissing');
  return '';
});

const steps = computed(() =>
  isLinux.value
    ? [
        t('sandbox.linuxCheckSystem'),
        t('sandbox.linuxDependencies'),
        t('sandbox.linuxBuildHelper'),
        t('sandbox.linuxPolicies'),
        t('sandbox.linuxService'),
        t('sandbox.linuxVerify')
      ]
    : [
        t('sandbox.stepCheckWsl'),
        t('sandbox.stepCheckDistro'),
        t('sandbox.stepDownloadRootfs'),
        t('sandbox.stepImportDistro'),
        t('sandbox.stepWriteConfig'),
        t('sandbox.stepInstallTools')
      ]
);

function stepStatus(step1Based: number): 'pending' | 'active' | 'done' | 'error' {
  const p = progress.value;
  if (!p) return 'pending';
  if (p.phase === 'verifying' || p.phase === 'done') return 'done';
  if (p.phase === 'enabling_wsl' || p.step_index === 0) return 'pending';
  if (step1Based < p.step_index) return 'done';
  if (step1Based === p.step_index) return p.phase === 'error' ? 'error' : 'active';
  return 'pending';
}

const downloadFraction = computed(() => {
  const p = progress.value;
  if (!p || p.step_index !== 3 || !p.download_bytes || !p.download_total) return 0;
  return Math.min(1, p.download_bytes / p.download_total);
});

const progressPercent = computed(() => {
  const p = progress.value;
  if (!p) return 0;
  switch (p.phase) {
    case 'enabling_wsl':
      return 4;
    case 'installing_wsl':
      return 6;
    case 'installing': {
      const total = p.step_total || 6;
      const doneSteps = Math.max(0, p.step_index - 1);
      return Math.min(96, Math.round(((doneSteps + downloadFraction.value) / total) * 100));
    }
    case 'verifying':
      return 96;
    case 'done':
      return 100;
    case 'needs_reboot':
      return 8;
    case 'error': {
      const total = p.step_total || 6;
      return Math.round((Math.max(0, p.step_index - 1) / total) * 100);
    }
    default:
      return 0;
  }
});

/** 进度区标题：终态写结果，过程中写当前阶段或步骤 */
const headline = computed(() => {
  const p = progress.value;
  if (!p) return '';
  if (p.phase === 'done') return t('sandbox.phaseDone');
  if (p.phase === 'needs_reboot') return t('sandbox.phaseNeedsReboot');
  if (p.phase === 'error') return p.error || t('sandbox.phaseError');
  return phaseLineText.value || steps.value[Math.max(0, p.step_index - 1)] || '';
});

const phaseLineText = computed(() => {
  const p = progress.value;
  if (!p) return '';
  if (p.phase === 'enabling_wsl') return t('sandbox.phaseEnablingWsl');
  if (p.phase === 'installing_wsl') return t('sandbox.phaseInstallingWsl');
  if (p.phase === 'verifying') return t('sandbox.phaseVerifying');
  return '';
});

function formatMB(bytes: number): string {
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

const downloadText = computed(() => {
  const p = progress.value;
  if (!p || !p.download_bytes) return '';
  if (p.download_total) {
    return t('sandbox.downloadProgress', {
      size: `${formatMB(p.download_bytes)} / ${formatMB(p.download_total)}`
    });
  }
  return t('sandbox.downloadProgress', { size: formatMB(p.download_bytes) });
});

function onClose() {
  // 安装中关闭仅收起弹窗（安装继续在后台进行，可从个人空间重新打开）
  if (store.progress?.active) {
    store.dialogVisible = false;
    return;
  }
  store.closeDialog(neverChecked.value);
}
</script>
<style scoped>
.sandbox-facts {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: var(--text-secondary);
  font-size: 14px;
  line-height: 22px;
}

.sandbox-facts li {
  position: relative;
  padding-left: 14px;
}

.sandbox-facts li::before {
  content: '';
  position: absolute;
  left: 2px;
  top: 9px;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--text-tertiary);
}

.sandbox-progress-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 16px;
  font-size: 14px;
  line-height: 22px;
}

.sandbox-headline {
  min-width: 0;
}

.sandbox-headline.error {
  color: var(--state-danger);
}

.sandbox-percent {
  flex: none;
  color: var(--text-tertiary);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}

/* 进度条紧贴阶段行：同一组信息，间距 8 而非区块间距 20 */
.ui-dialog-body > .sandbox-progress {
  margin-top: 8px;
}

.sandbox-steps {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.sandbox-steps li {
  height: 28px;
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--text-tertiary);
  font-size: 14px;
}

.sandbox-steps li.done {
  color: var(--text-secondary);
}

.sandbox-steps li.active {
  color: var(--text-primary);
}

.sandbox-steps li.error {
  color: var(--state-danger);
}

.sandbox-step-icon {
  width: 16px;
  height: 16px;
  flex: none;
  display: grid;
  place-items: center;
}

.sandbox-step-icon .ui-icon {
  width: 14px;
  height: 14px;
}

.sandbox-step-title {
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.sandbox-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--border-strong);
}

.sandbox-spinner {
  width: 13px;
  height: 13px;
  border: 1.5px solid var(--border-strong);
  border-top-color: var(--text-primary);
  border-radius: 50%;
  animation: sandbox-spin 800ms linear infinite;
}

.sandbox-step-extra {
  flex: none;
  margin-left: auto;
  color: var(--text-tertiary);
  font-size: 12px;
}

.sandbox-log {
  max-height: 160px;
  overflow-y: auto;
  overscroll-behavior: none;
}

.sandbox-log::-webkit-scrollbar {
  width: 4px;
}

.sandbox-log::-webkit-scrollbar-thumb {
  background: var(--border-strong);
  border-radius: 2px;
}

@keyframes sandbox-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .sandbox-spinner {
    animation-duration: 2s;
  }
}
</style>
