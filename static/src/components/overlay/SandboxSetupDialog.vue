<template>
  <transition name="sandbox-setup-fade" appear>
    <div v-if="store.dialogVisible" class="sandbox-setup-overlay">
      <section
        class="sandbox-setup-card"
        role="dialog"
        aria-modal="true"
        :aria-label="$t('sandbox.setupAriaLabel')"
      >
        <header class="sandbox-setup-windowbar">
          <div class="sandbox-setup-window-title">{{ $t('sandbox.setupTitle') }}</div>
          <span class="sandbox-setup-close">
            <CloseButton :label="$t('common.close')" :title="$t('common.close')" @click="onClose" />
          </span>
        </header>

        <div class="sandbox-setup-body">
          <!-- ── 未开始：说明 + 状态描述 ── -->
          <template v-if="!progress">
            <div class="sandbox-setup-hero">
              <svg
                viewBox="0 0 24 24"
                width="34"
                height="34"
                fill="none"
                stroke="currentColor"
                stroke-width="1.6"
                stroke-linecap="round"
                stroke-linejoin="round"
                aria-hidden="true"
              >
                <path d="M12 3l7 3v5c0 4.5-3 8.5-7 10-4-1.5-7-5.5-7-10V6l7-3z" />
                <path d="M9 12l2 2 4-4" />
              </svg>
            </div>
            <p v-if="stateDescription" class="sandbox-setup-state-desc">{{ stateDescription }}</p>
            <p v-else class="sandbox-setup-state-desc">{{ $t('sandbox.stateChecking') }}</p>
            <LinuxSandboxInstallInfo v-if="isLinux" :status="store.status" />
            <ul v-else class="sandbox-setup-facts">
              <li>{{ $t('sandbox.introWhat') }}</li>
              <li>{{ $t('sandbox.introEffect') }}</li>
              <li>{{ $t('sandbox.introDisk', { path: installPathHint }) }}</li>
              <li>{{ $t('sandbox.introUninstall', { distro: distroName }) }}</li>
              <li>{{ $t('sandbox.introSecure') }}</li>
            </ul>
          </template>

          <!-- ── 安装中 / 终态：阶段 + 步骤进度 ── -->
          <template v-else>
            <div v-if="phaseLineText" class="sandbox-setup-phase-line">
              <span class="sandbox-setup-spinner" aria-hidden="true"></span>
              {{ phaseLineText }}
            </div>

            <ul class="sandbox-setup-steps">
              <li
                v-for="(step, i) in steps"
                :key="i"
                class="sandbox-setup-step"
                :class="stepStatus(i + 1)"
              >
                <span class="sandbox-setup-step-icon" aria-hidden="true">
                  <svg
                    v-if="stepStatus(i + 1) === 'done'"
                    viewBox="0 0 20 20"
                    width="13"
                    height="13"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2.4"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <path d="M4 10.5l4 4 8-9" />
                  </svg>
                  <span
                    v-else-if="stepStatus(i + 1) === 'active'"
                    class="sandbox-setup-spinner small"
                  ></span>
                  <svg
                    v-else-if="stepStatus(i + 1) === 'error'"
                    viewBox="0 0 20 20"
                    width="13"
                    height="13"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2.4"
                    stroke-linecap="round"
                  >
                    <path d="M5 5l10 10M15 5L5 15" />
                  </svg>
                  <span v-else class="sandbox-setup-step-dot"></span>
                </span>
                <span class="sandbox-setup-step-title">{{ step }}</span>
                <span
                  v-if="i + 1 === 3 && progress.step_index === 3 && downloadText"
                  class="sandbox-setup-step-extra"
                >
                  {{ downloadText }}
                </span>
              </li>
            </ul>

            <div class="sandbox-setup-progress">
              <div
                class="sandbox-setup-progress-fill"
                :style="{ width: progressPercent + '%' }"
              ></div>
            </div>

            <div v-if="progress.phase === 'done'" class="sandbox-setup-result ok">
              {{ $t('sandbox.phaseDone') }}
            </div>
            <div v-else-if="progress.phase === 'needs_reboot'" class="sandbox-setup-result warn">
              {{ $t('sandbox.phaseNeedsReboot') }}
            </div>
            <div v-else-if="progress.phase === 'error'" class="sandbox-setup-result error">
              <p class="sandbox-setup-error-line">
                {{ progress.error || $t('sandbox.phaseError') }}
              </p>
              <p v-if="progress.error_kind === 'uac_cancelled'" class="sandbox-setup-error-hint">
                {{ $t('sandbox.uacCancelledHint') }}
              </p>
            </div>

            <div v-if="progress.log_tail.length" class="sandbox-setup-log-block">
              <div class="sandbox-setup-log-label">{{ $t('sandbox.logLabel') }}</div>
              <pre ref="logEl" class="sandbox-setup-log">{{ progress.log_tail.join('\n') }}</pre>
            </div>
          </template>
        </div>

        <footer v-if="!progress || !progress.active" class="sandbox-setup-footer">
          <!-- 未开始 -->
          <template v-if="!progress">
            <label class="sandbox-setup-never">
              <input
                type="checkbox"
                :checked="neverChecked"
                @change="neverChecked = ($event.target as HTMLInputElement).checked"
              />
              <FancyCheck :checked="neverChecked" :size="16" />
              <span>{{ $t('sandbox.neverAgain') }}</span>
            </label>
            <div class="sandbox-setup-spacer"></div>
            <button
              v-if="isLinux"
              type="button"
              class="sandbox-setup-btn ghost"
              :disabled="store.checking"
              @click="store.recheck()"
            >
              {{ $t('sandbox.recheck') }}
            </button>
            <button type="button" class="sandbox-setup-btn ghost" @click="onClose">
              {{ $t('sandbox.later') }}
            </button>
            <button
              type="button"
              class="sandbox-setup-btn primary"
              v-if="!isLinux || store.status?.can_install"
              :disabled="store.starting || store.checking || !store.status"
              @click="store.startSetup()"
            >
              {{ store.starting ? $t('common.loading') : $t('sandbox.installNow') }}
            </button>
          </template>
          <!-- 终态 -->
          <template v-else>
            <div class="sandbox-setup-spacer"></div>
            <button
              v-if="progress.phase === 'error' || progress.phase === 'needs_reboot'"
              type="button"
              class="sandbox-setup-btn primary"
              :disabled="store.starting"
              @click="store.retrySetup()"
            >
              {{
                progress.phase === 'needs_reboot' ? $t('sandbox.rebootDone') : $t('common.retry')
              }}
            </button>
            <button type="button" class="sandbox-setup-btn ghost" @click="onClose">
              {{ $t('common.close') }}
            </button>
          </template>
        </footer>
      </section>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import FancyCheck from '@/components/common/FancyCheck.vue';
import CloseButton from '@/components/common/CloseButton.vue';
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

<style scoped src="./SandboxSetupDialog.scss"></style>
