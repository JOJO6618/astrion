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
          <button
            type="button"
            class="sandbox-setup-close"
            :title="$t('common.close')"
            :aria-label="$t('common.close')"
            @click="onClose"
          >
            <svg viewBox="0 0 20 20" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M5 5l10 10M15 5L5 15" />
            </svg>
          </button>
        </header>

        <div class="sandbox-setup-body">
          <!-- ── 未开始：说明 + 状态描述 ── -->
          <template v-if="!progress">
            <div class="sandbox-setup-hero">
              <svg viewBox="0 0 24 24" width="34" height="34" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M12 3l7 3v5c0 4.5-3 8.5-7 10-4-1.5-7-5.5-7-10V6l7-3z" />
                <path d="M9 12l2 2 4-4" />
              </svg>
            </div>
            <p v-if="stateDescription" class="sandbox-setup-state-desc">{{ stateDescription }}</p>
            <p v-else class="sandbox-setup-state-desc">{{ $t('sandbox.stateChecking') }}</p>
            <ul class="sandbox-setup-facts">
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
                  <svg v-if="stepStatus(i + 1) === 'done'" viewBox="0 0 20 20" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M4 10.5l4 4 8-9" />
                  </svg>
                  <span v-else-if="stepStatus(i + 1) === 'active'" class="sandbox-setup-spinner small"></span>
                  <svg v-else-if="stepStatus(i + 1) === 'error'" viewBox="0 0 20 20" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round">
                    <path d="M5 5l10 10M15 5L5 15" />
                  </svg>
                  <span v-else class="sandbox-setup-step-dot"></span>
                </span>
                <span class="sandbox-setup-step-title">{{ step }}</span>
                <span v-if="i + 1 === 3 && progress.step_index === 3 && downloadText" class="sandbox-setup-step-extra">
                  {{ downloadText }}
                </span>
              </li>
            </ul>

            <div class="sandbox-setup-progress">
              <div class="sandbox-setup-progress-fill" :style="{ width: progressPercent + '%' }"></div>
            </div>

            <div v-if="progress.phase === 'done'" class="sandbox-setup-result ok">
              {{ $t('sandbox.phaseDone') }}
            </div>
            <div v-else-if="progress.phase === 'needs_reboot'" class="sandbox-setup-result warn">
              {{ $t('sandbox.phaseNeedsReboot') }}
            </div>
            <div v-else-if="progress.phase === 'error'" class="sandbox-setup-result error">
              <p class="sandbox-setup-error-line">{{ progress.error || $t('sandbox.phaseError') }}</p>
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
              <input type="checkbox" :checked="neverChecked" @change="neverChecked = ($event.target as HTMLInputElement).checked" />
              <FancyCheck :checked="neverChecked" :size="16" />
              <span>{{ $t('sandbox.neverAgain') }}</span>
            </label>
            <div class="sandbox-setup-spacer"></div>
            <button type="button" class="sandbox-setup-btn ghost" @click="onClose">
              {{ $t('sandbox.later') }}
            </button>
            <button
              type="button"
              class="sandbox-setup-btn primary"
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
              {{ progress.phase === 'needs_reboot' ? $t('sandbox.rebootDone') : $t('common.retry') }}
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
const distroName = computed(() => store.status?.distro_name || 'astrion-sandbox');
// 安装目录与 scripts/setup-wsl-sandbox.ps1 的默认 InstallDir 保持一致
const installPathHint = computed(() => '%USERPROFILE%\\.astrion\\wsl-sandbox');

const stateDescription = computed(() => {
  const s = store.status;
  if (!s || !s.applicable) return '';
  if (s.state === 'wsl_missing') return t('sandbox.stateWslMissing');
  if (s.state === 'distro_missing') return t('sandbox.stateDistroMissing');
  if (s.state === 'bwrap_missing') return t('sandbox.stateBwrapMissing');
  return '';
});

const steps = computed(() => [
  t('sandbox.stepCheckWsl'),
  t('sandbox.stepCheckDistro'),
  t('sandbox.stepDownloadRootfs'),
  t('sandbox.stepImportDistro'),
  t('sandbox.stepWriteConfig'),
  t('sandbox.stepInstallTools')
]);

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

<style scoped>
.sandbox-setup-overlay {
  position: fixed;
  inset: 0;
  z-index: 1400;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: var(--overlay-scrim);
}

.sandbox-setup-card {
  position: relative;
  width: min(560px, calc(100vw - 36px));
  max-height: min(720px, calc(100vh - 36px));
  max-height: min(720px, calc(100dvh - 36px));
  display: flex;
  flex-direction: column;
  color: var(--text-primary);
  background: var(--surface-card);
  border: 1px solid var(--border-strong);
  border-radius: 16px;
  overflow: hidden;
}

.sandbox-setup-windowbar {
  position: relative;
  height: 40px;
  flex: 0 0 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-bottom: 1px solid var(--border-default);
}

.sandbox-setup-window-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
  user-select: none;
}

.sandbox-setup-close {
  position: absolute;
  right: 10px;
  top: 50%;
  transform: translateY(-50%);
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
}

.sandbox-setup-close:hover {
  background: var(--hover-bg);
  color: var(--text-primary);
}

.sandbox-setup-body {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 20px 24px 8px;
  scrollbar-width: thin;
}

.sandbox-setup-hero {
  display: flex;
  justify-content: center;
  color: var(--accent);
  margin-bottom: 12px;
}

.sandbox-setup-state-desc {
  margin: 0 0 12px;
  font-size: 13.5px;
  font-weight: 600;
  line-height: 1.6;
  color: var(--text-primary);
}

.sandbox-setup-facts {
  margin: 0;
  padding: 0 0 0 18px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.sandbox-setup-facts li {
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--text-secondary);
}

.sandbox-setup-phase-line {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 30px;
  margin-bottom: 10px;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-secondary);
}

.sandbox-setup-steps {
  list-style: none;
  margin: 0 0 14px;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.sandbox-setup-step {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 30px;
  font-size: 13px;
  color: var(--text-tertiary);
}

.sandbox-setup-step.active {
  color: var(--text-primary);
  font-weight: 600;
}

.sandbox-setup-step.done {
  color: var(--text-secondary);
}

.sandbox-setup-step.error {
  color: var(--state-danger);
  font-weight: 600;
}

.sandbox-setup-step-icon {
  width: 18px;
  height: 18px;
  flex: 0 0 18px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.sandbox-setup-step.done .sandbox-setup-step-icon {
  color: var(--state-success);
}

.sandbox-setup-step-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--border-strong);
}

.sandbox-setup-step-extra {
  margin-left: auto;
  font-size: 11.5px;
  font-weight: 400;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}

.sandbox-setup-spinner {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  border: 2px solid var(--border-default);
  border-top-color: var(--accent);
  animation: sandbox-setup-spin 0.9s linear infinite;
}

.sandbox-setup-spinner.small {
  width: 11px;
  height: 11px;
  border-width: 1.6px;
}

@keyframes sandbox-setup-spin {
  to {
    transform: rotate(360deg);
  }
}

.sandbox-setup-progress {
  height: 6px;
  border-radius: 3px;
  background: var(--surface-muted);
  overflow: hidden;
  margin-bottom: 14px;
}

.sandbox-setup-progress-fill {
  height: 100%;
  border-radius: 3px;
  background: var(--accent);
  transition: width 0.4s ease;
}

.sandbox-setup-result {
  border-radius: 10px;
  padding: 10px 12px;
  font-size: 12.5px;
  line-height: 1.6;
  margin-bottom: 12px;
}

.sandbox-setup-result.ok {
  background: color-mix(in srgb, var(--state-success) 12%, transparent);
  color: var(--text-primary);
}

.sandbox-setup-result.warn {
  background: color-mix(in srgb, var(--state-warning) 14%, transparent);
  color: var(--text-primary);
}

.sandbox-setup-result.error {
  background: color-mix(in srgb, var(--state-danger) 12%, transparent);
  color: var(--text-primary);
}

.sandbox-setup-error-line {
  margin: 0;
  font-weight: 600;
}

.sandbox-setup-error-hint {
  margin: 6px 0 0;
  color: var(--text-secondary);
}

.sandbox-setup-log-block {
  margin-bottom: 12px;
}

.sandbox-setup-log-label {
  height: 22px;
  line-height: 22px;
  font-size: 12px;
  color: var(--text-tertiary);
  user-select: none;
}

.sandbox-setup-log {
  margin: 2px 0 0;
  max-height: 160px;
  overflow-y: auto;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--surface-muted);
  font-size: 11.5px;
  line-height: 1.55;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-all;
  scrollbar-width: thin;
}

.sandbox-setup-footer {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 24px 16px;
  border-top: 1px solid var(--border-default);
}

.sandbox-setup-never {
  display: flex;
  align-items: center;
  gap: 7px;
  height: 30px;
  font-size: 12.5px;
  color: var(--text-secondary);
  cursor: pointer;
  user-select: none;
}

.sandbox-setup-never input {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}

.sandbox-setup-spacer {
  flex: 1 1 auto;
}


.sandbox-setup-btn {
  height: 32px;
  padding: 0 16px;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  border: 1px solid transparent;
  white-space: nowrap;
}

.sandbox-setup-btn:disabled {
  opacity: 0.55;
  cursor: default;
}

.sandbox-setup-btn.ghost {
  background: transparent;
  border-color: var(--border-default);
  color: var(--text-secondary);
}

.sandbox-setup-btn.ghost:hover:not(:disabled) {
  background: var(--hover-bg);
  color: var(--text-primary);
}

.sandbox-setup-btn.primary {
  background: var(--accent);
  color: var(--on-accent);
}

.sandbox-setup-btn.primary:hover:not(:disabled) {
  background: var(--accent-hover);
}

.sandbox-setup-fade-enter-active,
.sandbox-setup-fade-leave-active {
  transition: opacity 0.18s ease;
}

.sandbox-setup-fade-enter-from,
.sandbox-setup-fade-leave-to {
  opacity: 0;
}
</style>
