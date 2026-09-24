<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { t } from '@/locales';
import { useModelStore } from '@/stores/model';
import { useUiStore } from '@/stores/ui';

defineOptions({ name: 'CodexTab' });

/**
 * Codex 账号管理（自包含 tab：状态获取、OAuth 登录轮询、断开、模型刷新、
 * 代理设置、用量与重置额度均走 /api/codex/* 端点，不经 personalization 表单体系）。
 */

const modelStore = useModelStore();
const uiStore = useUiStore();

const loading = ref(true);
const loadError = ref('');
const status = ref<Record<string, any>>({});
const proxyInput = ref('');
const proxySaving = ref(false);
const proxySaved = ref(false);
const actionBusy = ref(false);
const usage = ref<Record<string, any> | null>(null);
const usageError = ref('');
const credits = ref<Record<string, any> | null>(null);
const creditsError = ref('');
const consumeBusy = ref(false);
let pollTimer: ReturnType<typeof setInterval> | null = null;

const connected = computed(() => !!status.value?.connected);
const loginFlowStatus = computed<string>(
  () => status.value?.login_flow?.status || 'idle'
);
const loginPending = computed(() => loginFlowStatus.value === 'pending');
const loginError = computed(() => status.value?.login_flow?.error || '');
const loginFlowMode = computed<string>(
  () => status.value?.login_flow?.flow_mode || 'browser'
);
const deviceUserCode = computed<string>(
  () => status.value?.login_flow?.user_code || ''
);
const deviceVerificationUri = computed<string>(
  () => status.value?.login_flow?.verification_uri || ''
);
const deviceLoginPending = computed(
  () => loginPending.value && loginFlowMode.value === 'device'
);
const loginPhase = computed<string>(
  () => status.value?.login_flow?.phase || ''
);
/** 设备码流：后端后台线程正在请求授权码（点按钮后立即出现，替代原来的静默等待） */
const deviceLoginStarting = computed(
  () => deviceLoginPending.value && loginPhase.value === 'starting'
);
/** 任一流程：用户已授权，后端后台线程正在换 token（可重试，可能持续数秒） */
const loginExchanging = computed(
  () => loginPending.value && loginPhase.value === 'exchanging'
);
const modelsInfo = computed(() => status.value?.models || {});

const expiresText = computed(() => {
  const seconds = status.value?.expires_in_seconds;
  if (!connected.value || typeof seconds !== 'number') return '';
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  if (days > 0) return t('personalization.codexExpiresInDays', { days, hours });
  return t('personalization.codexExpiresInHours', { hours });
});

const statusLine = computed(() => {
  if (!connected.value) {
    return status.value?.error || t('personalization.codexNotConnectedDesc');
  }
  const parts: string[] = [];
  if (status.value?.account_id) {
    parts.push(
      t('personalization.codexAccountLine', {
        account: String(status.value.account_id).slice(0, 8)
      })
    );
  }
  if (expiresText.value) parts.push(expiresText.value);
  return parts.join(' · ');
});

const modelsLine = computed(() => {
  const info = modelsInfo.value;
  const count = info.model_count || 0;
  const source = info.source || 'empty';
  const sourceKey =
    source === 'online'
      ? 'personalization.codexSourceOnline'
      : source === 'cli_cache'
        ? 'personalization.codexSourceCliCache'
        : source === 'stale'
          ? 'personalization.codexSourceStale'
          : 'personalization.codexSourceEmpty';
  const fetched = info.fetched_at ? String(info.fetched_at).slice(0, 16).replace('T', ' ') : '-';
  return `${t(sourceKey)} · ${t('personalization.codexModelsTotal', { count })} · ${fetched}`;
});

const stopPolling = () => {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
};

const fetchStatus = async () => {
  try {
    const resp = await fetch('/api/codex/status', { cache: 'no-store' });
    const data = await resp.json();
    if (data?.success) {
      status.value = data;
    } else {
      loadError.value = data?.error || t('personalization.codexLoadFailed');
    }
  } catch (e: any) {
    loadError.value = String(e?.message || e);
  } finally {
    loading.value = false;
  }
};

const fetchSettings = async () => {
  try {
    const resp = await fetch('/api/codex/settings', { cache: 'no-store' });
    const data = await resp.json();
    if (data?.success) {
      proxyInput.value = data.proxy || '';
    }
  } catch (_) {
    /* 代理设置读取失败不阻塞主界面 */
  }
};

const fetchUsage = async () => {
  if (!connected.value) {
    usage.value = null;
    return;
  }
  try {
    const resp = await fetch('/api/codex/usage', { cache: 'no-store' });
    const data = await resp.json();
    if (data?.success) {
      usage.value = data.usage || null;
      usageError.value = '';
    } else {
      usageError.value = data?.error || t('personalization.codexUsageLoadFailed');
    }
  } catch (e: any) {
    usageError.value = String(e?.message || e);
  }
};

const formatResetDuration = (seconds: number): string => {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  const days = Math.floor(s / 86400);
  const hours = Math.floor((s % 86400) / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  if (days > 0) return t('personalization.codexUsageDaysHours', { days, hours });
  return t('personalization.codexUsageHoursMinutes', { hours, minutes });
};

interface UsageWindowView {
  labelKey: string;
  percent: number;
  resetText: string;
  level: 'normal' | 'warning' | 'danger';
}

const usageWindows = computed<UsageWindowView[]>(() => {
  const rl = usage.value?.rate_limit;
  if (!rl) return [];
  const out: UsageWindowView[] = [];
  const push = (key: string, labelKey: string) => {
    const w = rl[key];
    if (!w || typeof w.used_percent !== 'number') return;
    const percent = Math.min(100, Math.max(0, Math.round(w.used_percent)));
    out.push({
      labelKey,
      percent,
      resetText: formatResetDuration(w.reset_after_seconds),
      level: percent >= 95 ? 'danger' : percent >= 80 ? 'warning' : 'normal'
    });
  };
  push('primary_window', 'personalization.codexUsagePrimaryWindow');
  push('secondary_window', 'personalization.codexUsageSecondaryWindow');
  return out;
});

const usagePlanText = computed(() => {
  const plan = usage.value?.plan_type;
  return plan ? t('personalization.codexUsagePlan', { plan: String(plan) }) : '';
});

// ── 重置额度（banked rate-limit resets）──

const fetchCredits = async () => {
  if (!connected.value) {
    credits.value = null;
    return;
  }
  try {
    const resp = await fetch('/api/codex/reset-credits', { cache: 'no-store' });
    const data = await resp.json();
    if (data?.success) {
      credits.value = data;
      creditsError.value = '';
    } else {
      creditsError.value = data?.error || t('personalization.codexUsageLoadFailed');
    }
  } catch (e: any) {
    creditsError.value = String(e?.message || e);
  }
};

const creditList = computed<Record<string, any>[]>(() =>
  Array.isArray(credits.value?.credits) ? credits.value.credits : []
);

/** 可用的重置额度（status=available 且未过期） */
const availableCredits = computed(() => {
  const now = Date.now();
  return creditList.value.filter((c) => {
    if (c?.status !== 'available') return false;
    const exp = Date.parse(c?.expires_at || '');
    return Number.isNaN(exp) || exp > now;
  });
});

/** 历史列表：全部额度按获得时间倒序 */
const creditHistory = computed(() =>
  [...creditList.value].sort((a, b) =>
    String(b?.granted_at || '').localeCompare(String(a?.granted_at || ''))
  )
);

const formatCreditDate = (iso: unknown): string => {
  // 兼容 ISO 字符串与毫秒时间戳（nextExpiryText 传入 Math.min 的数字）
  const ms = typeof iso === 'number' ? iso : Date.parse(String(iso || ''));
  if (Number.isNaN(ms)) return '-';
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};

const nextExpiryText = computed(() => {
  const times = availableCredits.value
    .map((c) => Date.parse(c?.expires_at || ''))
    .filter((ms) => !Number.isNaN(ms));
  if (!times.length) return '';
  return formatCreditDate(Math.min(...times));
});

const creditStatusKey = (credit: Record<string, any>): string => {
  const status = String(credit?.status || '');
  if (status === 'available') {
    const exp = Date.parse(credit?.expires_at || '');
    if (!Number.isNaN(exp) && exp <= Date.now()) return 'personalization.codexResetStatusExpired';
    return 'personalization.codexResetStatusAvailable';
  }
  if (status === 'redeemed') return 'personalization.codexResetStatusRedeemed';
  return 'personalization.codexResetStatusExpired';
};

const consumeReset = async (credit: Record<string, any>) => {
  if (!credit?.id || consumeBusy.value) return;
  const ok = await uiStore.requestConfirm({
    title: t('personalization.codexResetConfirmTitle'),
    message: t('personalization.codexResetConfirmMessage', {
      title: credit.title || t('personalization.codexResetsTitle')
    }),
    warningText: t('personalization.codexResetConfirmWarning'),
    confirmText: t('personalization.codexResetsUseNow')
  });
  if (!ok) return;
  consumeBusy.value = true;
  try {
    const resp = await fetch('/api/codex/reset-credits/consume', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ credit_id: credit.id })
    });
    const data = await resp.json();
    if (data?.success) {
      uiStore.pushToast({
        message: t('personalization.codexResetSuccess'),
        type: 'success'
      });
      await Promise.all([fetchUsage(), fetchCredits()]);
    } else {
      uiStore.pushToast({
        message: t('personalization.codexResetFailed', { error: data?.error || 'unknown' }),
        type: 'error'
      });
    }
  } catch (e: any) {
    uiStore.pushToast({
      message: t('personalization.codexResetFailed', { error: String(e?.message || e) }),
      type: 'error'
    });
  } finally {
    consumeBusy.value = false;
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
        await fetchStatus();
        if (flow === 'completed') {
          // 登录成功：模型列表已在后台刷新，稍等后同步前端模型选择器
          setTimeout(() => modelStore.fetchModels().catch(() => {}), 1500);
          fetchUsage();
          fetchCredits();
        }
      } else {
        status.value = { ...status.value, login_flow: data };
      }
    } catch (_) {
      /* 轮询失败下轮再说 */
    }
  }, 1500);
};

const startLogin = async () => {
  actionBusy.value = true;
  try {
    const resp = await fetch('/api/codex/login/start', { method: 'POST' });
    const data = await resp.json();
    if (!data?.success) {
      loadError.value = data?.error || t('personalization.codexLoginStartFailed');
      return;
    }
    if (data.flow_mode === 'device') {
      // 两流互斥：已有设备码流程进行中，直接展示其状态，不开浏览器
      status.value = { ...status.value, login_flow: data };
      startPolling();
      return;
    }
    if (!data?.authorize_url) {
      loadError.value = data?.error || t('personalization.codexLoginStartFailed');
      return;
    }
    window.open(data.authorize_url, '_blank', 'noopener');
    status.value = {
      ...status.value,
      login_flow: { status: 'pending', flow_mode: 'browser' }
    };
    startPolling();
  } catch (e: any) {
    loadError.value = String(e?.message || e);
  } finally {
    actionBusy.value = false;
  }
};

const startDeviceLogin = async () => {
  actionBusy.value = true;
  try {
    const resp = await fetch('/api/codex/login/device/start', { method: 'POST' });
    const data = await resp.json();
    if (!data?.success) {
      loadError.value = data?.error || t('personalization.codexLoginStartFailed');
      return;
    }
    status.value = { ...status.value, login_flow: data };
    startPolling();
    // 不自动打开授权页：设备码由后端后台线程异步获取（phase=starting），
    // 拿到码后由用户手动点「打开授权页」（openDevicePage），避免弹窗被拦截
    // 或在无头环境错误跳转；互斥命中浏览器流时同样只展示其进行中的状态。
  } catch (e: any) {
    loadError.value = String(e?.message || e);
  } finally {
    actionBusy.value = false;
  }
};

const openDevicePage = () => {
  if (deviceVerificationUri.value) {
    window.open(deviceVerificationUri.value, '_blank', 'noopener');
  }
};

const copyUserCode = async () => {
  try {
    await navigator.clipboard.writeText(deviceUserCode.value);
    uiStore.pushToast({ message: t('common.copied'), type: 'success' });
  } catch (_) {
    uiStore.pushToast({ message: t('common.copyFailed'), type: 'error' });
  }
};

const cancelLogin = async () => {
  stopPolling();
  try {
    await fetch('/api/codex/login/cancel', { method: 'POST' });
  } catch (_) {
    /* ignore */
  }
  await fetchStatus();
};

const logout = async () => {
  actionBusy.value = true;
  try {
    await fetch('/api/codex/logout', { method: 'POST' });
    await fetchStatus();
    await modelStore.fetchModels().catch(() => {});
  } finally {
    actionBusy.value = false;
  }
};

const refreshModels = async () => {
  actionBusy.value = true;
  try {
    await fetch('/api/codex/models/refresh', { method: 'POST' });
    await fetchStatus();
    await modelStore.fetchModels().catch(() => {});
  } finally {
    actionBusy.value = false;
  }
};

const saveProxy = async () => {
  proxySaving.value = true;
  proxySaved.value = false;
  try {
    const resp = await fetch('/api/codex/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ proxy: proxyInput.value.trim() })
    });
    const data = await resp.json();
    if (data?.success) {
      proxyInput.value = data.proxy || '';
      proxySaved.value = true;
      setTimeout(() => (proxySaved.value = false), 2000);
    }
  } finally {
    proxySaving.value = false;
  }
};

onMounted(() => {
  fetchStatus().then(() => {
    if (connected.value) {
      fetchUsage();
      fetchCredits();
    }
  });
  fetchSettings();
});
onBeforeUnmount(stopPolling);
</script>

<template>
  <section class="settings-page">
    <div
      class="settings-section-desc"
      style="margin: 0 0 16px; color: var(--text-secondary); font-size: 13px; line-height: 1.6"
    >
      {{ $t('personalization.codexIntro') }}
    </div>

    <div v-if="loading" class="settings-section-desc">{{ $t('personalization.loadingPersonalization') }}</div>
    <template v-else>
      <!-- 连接状态 -->
      <div class="settings-section-divider">
        <span class="settings-section-divider__label">{{ $t('personalization.codexStatusTitle') }}</span>
      </div>
      <div class="settings-input-row">
        <span class="settings-row-copy">
          <span class="settings-row-title">
            {{ connected ? $t('personalization.codexConnected') : $t('personalization.codexNotConnected') }}
          </span>
          <span class="settings-row-desc">{{ statusLine }}</span>
        </span>
        <div class="settings-number-row">
          <button
            v-if="loginPending"
            type="button"
            @click="cancelLogin"
          >
            {{ $t('common.cancel') }}
          </button>
          <template v-else-if="!connected">
            <button
              type="button"
              :disabled="actionBusy"
              @click="startLogin"
            >
              {{ $t('personalization.codexLoginBrowser') }}
            </button>
            <button
              type="button"
              :disabled="actionBusy"
              @click="startDeviceLogin"
            >
              {{ $t('personalization.codexLoginDevice') }}
            </button>
          </template>
          <button
            v-else
            type="button"
            :disabled="actionBusy"
            @click="logout"
          >
            {{ $t('personalization.codexDisconnect') }}
          </button>
        </div>
      </div>
      <div v-if="loginPending" class="settings-section-desc codex-login-pending">
        <template v-if="loginExchanging">
          {{ $t('personalization.codexLoginExchanging') }}
        </template>
        <div v-else-if="deviceLoginPending" class="codex-device-panel">
          <span v-if="deviceLoginStarting" class="codex-device-panel__hint">
            {{ $t('personalization.codexLoginStartingDevice') }}
          </span>
          <template v-else-if="deviceUserCode">
            <span class="codex-device-panel__hint">
              {{ $t('personalization.codexLoginDeviceHint') }}
            </span>
            <span class="codex-device-code">
              <span class="codex-device-code__text">{{ deviceUserCode }}</span>
              <button type="button" class="settings-secondary-button" @click="copyUserCode">
                {{ $t('common.copy') }}
              </button>
              <button
                v-if="deviceVerificationUri"
                type="button"
                class="settings-secondary-button"
                @click="openDevicePage"
              >
                {{ $t('personalization.codexLoginDeviceOpen') }}
              </button>
            </span>
            <span class="codex-device-panel__first">
              {{ $t('personalization.codexLoginDeviceFirstTime') }}
            </span>
            <span class="codex-device-panel__waiting">
              {{ $t('personalization.codexLoginPendingDevice') }}
            </span>
          </template>
        </div>
        <template v-else>
          {{ $t('personalization.codexLoginPending') }}
        </template>
      </div>
      <div v-if="loginError" class="settings-section-desc codex-login-error">
        {{ loginError }}
      </div>

      <!-- 模型列表 -->
      <div class="settings-section-divider">
        <span class="settings-section-divider__label">{{ $t('personalization.codexModelsTitle') }}</span>
      </div>
      <div class="settings-input-row">
        <span class="settings-row-copy">
          <span class="settings-row-title">
            {{ $t('personalization.codexModelsRowTitle') }}
          </span>
          <span class="settings-row-desc">{{ modelsLine }}</span>
        </span>
        <div class="settings-number-row">
          <button type="button" :disabled="actionBusy" @click="refreshModels">
            {{ $t('personalization.codexRefreshModels') }}
          </button>
        </div>
      </div>

      <!-- 用量（wham/usage；仅已连接时显示） -->
      <template v-if="connected">
        <div class="settings-section-divider">
          <span class="settings-section-divider__label">{{ $t('personalization.codexUsageTitle') }}</span>
        </div>
        <div class="settings-input-row">
          <span class="settings-row-copy">
            <span class="settings-row-title">{{ usagePlanText || $t('personalization.codexUsageTitle') }}</span>
            <span class="settings-row-desc">
              <template v-if="usageError">{{ usageError }}</template>
              <template v-else-if="!usage">{{ $t('personalization.usageSyncing') }}</template>
            </span>
          </span>
          <div class="settings-number-row">
            <button type="button" :disabled="actionBusy" @click="fetchUsage">
              {{ $t('personalization.usageRefresh') }}
            </button>
          </div>
        </div>
        <div v-if="usageWindows.length" class="codex-usage-windows">
          <div v-for="w in usageWindows" :key="w.labelKey" class="codex-usage-window">
            <div class="codex-usage-window__head">
              <span>{{ $t(w.labelKey) }}</span>
              <span>{{ w.percent }}% · {{ $t('personalization.codexUsageResetIn', { time: w.resetText }) }}</span>
            </div>
            <div class="codex-usage-bar">
              <div
                class="codex-usage-bar__fill"
                :class="`codex-usage-bar__fill--${w.level}`"
                :style="{ width: `${w.percent}%` }"
              ></div>
            </div>
          </div>
        </div>

        <!-- 重置额度（banked resets：可用数 + 兑换 + 历史） -->
        <div class="settings-section-divider">
          <span class="settings-section-divider__label">{{ $t('personalization.codexResetsTitle') }}</span>
        </div>
        <div class="settings-input-row">
          <span class="settings-row-copy">
            <span class="settings-row-title">
              {{ $t('personalization.codexResetsCount', { count: availableCredits.length }) }}
            </span>
            <span class="settings-row-desc">
              <template v-if="creditsError">{{ creditsError }}</template>
              <template v-else-if="!credits">{{ $t('personalization.usageSyncing') }}</template>
              <template v-else-if="nextExpiryText">
                {{ $t('personalization.codexResetsEarliestExpiry', { time: nextExpiryText }) }}
              </template>
              <template v-else>{{ $t('personalization.codexResetsNone') }}</template>
            </span>
          </span>
        </div>
        <div v-if="creditHistory.length" class="codex-credit-history">
          <div v-for="c in creditHistory" :key="c.id" class="codex-credit-item">
            <div class="codex-credit-item__main">
              <span class="codex-credit-item__title">{{ c.title || $t('personalization.codexResetsTitle') }}</span>
              <span class="codex-credit-item__meta">
                {{ $t('personalization.codexResetGrantedAt', { time: formatCreditDate(c.granted_at) }) }}
                <template v-if="c.redeemed_at">
                  · {{ $t('personalization.codexResetRedeemedAt', { time: formatCreditDate(c.redeemed_at) }) }}
                </template>
                <template v-else>
                  · {{ $t('personalization.codexResetExpiresAt', { time: formatCreditDate(c.expires_at) }) }}
                </template>
              </span>
            </div>
            <button
              v-if="creditStatusKey(c) === 'personalization.codexResetStatusAvailable'"
              type="button"
              class="codex-credit-item__action"
              :disabled="consumeBusy"
              @click="consumeReset(c)"
            >
              {{ $t('personalization.codexResetsUseNow') }}
            </button>
            <span v-else class="codex-credit-item__status">{{ $t(creditStatusKey(c)) }}</span>
          </div>
        </div>
      </template>

      <!-- 代理设置 -->
      <div class="settings-section-divider">
        <span class="settings-section-divider__label">{{ $t('personalization.codexProxyTitle') }}</span>
      </div>
      <div class="settings-input-row">
        <span class="settings-row-copy">
          <span class="settings-row-title">{{ $t('personalization.codexProxyRowTitle') }}</span>
          <span class="settings-row-desc">{{ $t('personalization.codexProxyDesc') }}</span>
        </span>
        <div class="settings-number-row codex-proxy-row">
          <input
            v-model="proxyInput"
            type="text"
            :placeholder="$t('personalization.codexProxyPlaceholder')"
            spellcheck="false"
          />
          <button type="button" :disabled="proxySaving" @click="saveProxy">
            {{ proxySaved ? $t('personalization.codexProxySaved') : $t('common.save') }}
          </button>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.codex-device-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 0 4px;
}

.codex-device-panel__hint {
  color: var(--text-secondary);
}

.codex-device-code {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.codex-device-code__text {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 20px;
  font-weight: 600;
  letter-spacing: 2px;
  color: var(--text-primary);
  user-select: all;
}

.codex-device-panel__first {
  color: var(--text-secondary);
  font-size: 12px;
}

.codex-device-panel__waiting {
  color: var(--text-secondary);
}

.codex-login-pending {
  color: var(--text-secondary);
  font-size: 12px;
  margin: -4px 0 12px;
}

.codex-login-error {
  color: var(--state-danger);
  font-size: 12px;
  margin: -4px 0 12px;
}

.codex-proxy-row input {
  min-width: 220px;
}

.codex-usage-windows {
  display: flex;
  flex-direction: column;
  gap: 18px;
  margin: 6px 0 18px;
}

.codex-usage-window__head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-secondary);
  margin-bottom: 10px;
}

.codex-usage-bar {
  height: 6px;
  border-radius: 999px;
  background: var(--surface-muted);
  overflow: hidden;
}

.codex-usage-bar__fill {
  height: 100%;
  border-radius: 999px;
  background: var(--accent);
  transition: width 0.3s ease;
}

.codex-usage-bar__fill--warning {
  background: var(--state-warning);
}

.codex-usage-bar__fill--danger {
  background: var(--state-danger);
}

.codex-credit-history {
  display: flex;
  flex-direction: column;
  margin: -4px 0 12px;
}

.codex-credit-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 10px 0;
  border-top: 1px solid var(--border-default);
}

.codex-credit-item:first-child {
  border-top: none;
}

.codex-credit-item__main {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.codex-credit-item__title {
  font-size: 13px;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.codex-credit-item__meta {
  font-size: 12px;
  color: var(--text-secondary);
}

.codex-credit-item__status {
  flex-shrink: 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.codex-credit-item__action {
  flex-shrink: 0;
  border: 1px solid var(--theme-control-border);
  border-radius: 999px;
  padding: 5px 14px;
  background: transparent;
  color: var(--text-primary);
  font-size: 12px;
  line-height: 1.4;
  cursor: pointer;
}

.codex-credit-item__action:hover:not(:disabled) {
  background: var(--hover-bg);
}

.codex-credit-item__action:disabled {
  opacity: 0.5;
  cursor: default;
}
</style>
