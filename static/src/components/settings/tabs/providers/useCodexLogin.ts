import { computed, onBeforeUnmount, ref } from 'vue';
import { t } from '@/locales';
import { useUiStore } from '@/stores/ui';

/**
 * Codex OAuth 登录流程（浏览器授权 / 无头设备码）的唯一实现（同一组 /api/codex/login/* 端点）。
 *
 * 唯一使用方 = 「提供商」分区 openai-codex（auth=codex_oauth）条目的连接对话框
 * （CodexConnectDialog.vue）；Codex 分区（CodexTab.vue）只保留状态展示与
 * 高级管理（用量/重置/代理），不再内联登录 / 断开 / 刷新模型。
 */
export function useCodexLogin(options: { onCompleted?: () => void } = {}) {
  const uiStore = useUiStore();

  const busy = ref(false);
  const error = ref('');
  const loginFlow = ref<Record<string, any>>({ status: 'idle' });
  let pollTimer: ReturnType<typeof setInterval> | null = null;

  const flowStatus = computed<string>(() => loginFlow.value?.status || 'idle');
  const pending = computed(() => flowStatus.value === 'pending');
  const flowMode = computed<string>(() => loginFlow.value?.flow_mode || 'browser');
  const phase = computed<string>(() => loginFlow.value?.phase || '');
  const errorText = computed<string>(() => loginFlow.value?.error || error.value || '');
  const userCode = computed<string>(() => loginFlow.value?.user_code || '');
  const verificationUri = computed<string>(() => loginFlow.value?.verification_uri || '');
  const devicePending = computed(() => pending.value && flowMode.value === 'device');
  /** 设备码流：后端后台线程正在请求授权码（点按钮后立即出现，替代静默等待） */
  const deviceStarting = computed(() => devicePending.value && phase.value === 'starting');
  /** 任一流程：用户已授权，后端正在换 token（可重试，可能持续数秒） */
  const exchanging = computed(() => pending.value && phase.value === 'exchanging');

  const stopPolling = () => {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  };

  const startPolling = () => {
    stopPolling();
    pollTimer = setInterval(async () => {
      try {
        const resp = await fetch('/api/codex/login/poll', { cache: 'no-store' });
        const data = await resp.json();
        const flow = data?.status;
        if (flow && flow !== 'pending') {
          stopPolling();
          loginFlow.value = { ...loginFlow.value, ...data };
          if (flow === 'completed') {
            options.onCompleted?.();
          }
        } else {
          loginFlow.value = { ...loginFlow.value, ...data };
        }
      } catch {
        /* 轮询失败下轮再说 */
      }
    }, 1500);
  };

  /** 浏览器授权：打开授权页 + 开始轮询（已有设备码流程时直接展示其状态） */
  const startBrowserLogin = async () => {
    busy.value = true;
    error.value = '';
    try {
      const resp = await fetch('/api/codex/login/start', { method: 'POST' });
      const data = await resp.json();
      if (!data?.success) {
        error.value = data?.error || t('personalization.codexLoginStartFailed');
        return;
      }
      if (data.flow_mode === 'device') {
        // 两流互斥：已有设备码流程进行中，直接展示其状态，不开浏览器
        loginFlow.value = data;
        startPolling();
        return;
      }
      if (!data?.authorize_url) {
        error.value = data?.error || t('personalization.codexLoginStartFailed');
        return;
      }
      window.open(data.authorize_url, '_blank', 'noopener');
      loginFlow.value = { status: 'pending', flow_mode: 'browser' };
      startPolling();
    } catch (e: any) {
      error.value = String(e?.message || e);
    } finally {
      busy.value = false;
    }
  };

  /** 无头设备码：不自动打开授权页，拿到码后由用户手动「打开授权页」 */
  const startDeviceLogin = async () => {
    busy.value = true;
    error.value = '';
    try {
      const resp = await fetch('/api/codex/login/device/start', { method: 'POST' });
      const data = await resp.json();
      if (!data?.success) {
        error.value = data?.error || t('personalization.codexLoginStartFailed');
        return;
      }
      loginFlow.value = data;
      startPolling();
    } catch (e: any) {
      error.value = String(e?.message || e);
    } finally {
      busy.value = false;
    }
  };

  const cancelLogin = async () => {
    stopPolling();
    try {
      await fetch('/api/codex/login/cancel', { method: 'POST' });
    } catch {
      /* ignore */
    }
    loginFlow.value = { status: 'idle' };
  };

  const openDevicePage = () => {
    if (verificationUri.value) {
      window.open(verificationUri.value, '_blank', 'noopener');
    }
  };

  const copyUserCode = async () => {
    try {
      await navigator.clipboard.writeText(userCode.value);
      uiStore.pushToast({ message: t('common.copied'), type: 'success' });
    } catch {
      uiStore.pushToast({ message: t('common.copyFailed'), type: 'error' });
    }
  };

  onBeforeUnmount(stopPolling);

  return {
    busy,
    error,
    pending,
    flowMode,
    devicePending,
    deviceStarting,
    exchanging,
    errorText,
    userCode,
    verificationUri,
    startBrowserLogin,
    startDeviceLogin,
    cancelLogin,
    openDevicePage,
    copyUserCode
  };
}
