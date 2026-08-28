<template>
  <div class="api-page">
    <SecondaryGate
      v-if="!secondaryVerified"
      :configured="secondaryConfigured"
      :loading="secondaryLoading"
      :error="secondaryError"
      :description="$t('adminApi.gateDescription')"
      @verify="handleVerifySecondary"
      @recheck="checkSecondary"
    />

    <header class="api-header">
      <div>
        <h1>{{ $t('adminApi.title') }}</h1>
        <p>{{ $t('adminApi.subtitle') }}</p>
        <small class="muted">{{ $t('adminApi.dataTime', { time: timeAgo(snapshot?.generated_at) }) }}</small>
      </div>
      <div class="header-actions">
        <label><input type="checkbox" v-model="autoRefresh" /> {{ $t('adminApi.autoRefresh') }}</label>
        <button type="button" :disabled="loading" @click="fetchAll">
          {{ loading ? $t('common.refreshing') : $t('adminApi.manualRefresh') }}
        </button>
      </div>
    </header>

    <section class="panel">
      <div class="metrics-grid">
        <div class="metric-card">
          <p>{{ $t('adminApi.metricUsers') }}</p>
          <strong>{{ overview.totals?.users || 0 }}</strong>
        </div>
        <div class="metric-card">
          <p>{{ $t('adminApi.metricTotalRequests') }}</p>
          <strong>{{ formatNumber(totalRequests) }}</strong>
        </div>
        <div class="metric-card">
          <p>{{ $t('adminApi.metricTotalTokens') }}</p>
          <strong>{{ formatNumber(overview.token_totals?.total_tokens || 0) }}</strong>
          <span class="muted"
            >{{
              $t('adminApi.tokenInOut', {
                input: formatNumber(overview.token_totals?.input_tokens || 0),
                output: formatNumber(overview.token_totals?.output_tokens || 0)
              })
            }}</span
          >
        </div>
        <div class="metric-card">
          <p>{{ $t('adminApi.metricActiveContainers') }}</p>
          <strong>{{ overview.totals?.containers_active || 0 }}</strong>
          <span class="muted"
            >{{
              $t('adminApi.availableSlots', { slots: overview.totals?.available_container_slots ?? '—' })
            }}</span
          >
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="section-head">
        <h2>{{ $t('adminApi.accountManagement') }}</h2>
        <div class="create-form">
          <input v-model="createForm.username" type="text" :placeholder="$t('adminApi.usernamePlaceholder')" />
          <input v-model="createForm.note" type="text" :placeholder="$t('adminApi.notePlaceholder')" />
          <button type="button" :disabled="creating" @click="createUser">
            {{ creating ? $t('adminApi.creating') : $t('adminApi.createAccount') }}
          </button>
        </div>
      </div>
      <div class="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>{{ $t('adminApi.tableUsername') }}</th>
              <th>{{ $t('adminApi.tableNote') }}</th>
              <th>{{ $t('adminApi.tableCreatedAt') }}</th>
              <th>{{ $t('adminApi.tableRequests') }}</th>
              <th>{{ $t('adminApi.tableLastCall') }}</th>
              <th>{{ $t('adminApi.tableTotalTokens') }}</th>
              <th>{{ $t('adminApi.tableStorage') }}</th>
              <th>{{ $t('adminApi.tableContainer') }}</th>
              <th>{{ $t('adminApi.tableToken') }}</th>
              <th>{{ $t('adminApi.tableActions') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!users.length">
              <td colspan="10">{{ $t('adminApi.emptyAccounts') }}</td>
            </tr>
            <tr v-for="u in users" :key="u.username">
              <td>
                <strong>{{ u.username }}</strong>
              </td>
              <td>{{ u.note || '—' }}</td>
              <td>{{ formatTime(u.created_at) }}</td>
              <td>{{ formatNumber(u.usage?.total_requests || 0) }}</td>
              <td>{{ timeAgo(u.usage?.last_request_at) }}</td>
              <td>{{ formatNumber(u.tokens?.total_tokens || 0) }}</td>
              <td>{{ formatBytes(u.storage?.total_bytes) }}</td>
              <td>
                <span v-if="u.container?.running" class="status-badge online">{{ $t('common.running') }}</span>
                <span v-else class="status-badge offline">{{ $t('common.notRunning') }}</span>
              </td>
              <td class="token-cell">
                <span class="token-text">
                  {{
                    revealTokens[u.username]?.visible
                      ? revealTokens[u.username]?.token || '—'
                      : '••••••••••'
                  }}
                </span>
                <button
                  class="icon-btn"
                  @click="toggleReveal(u.username)"
                  :disabled="revealLoading === u.username"
                >
                  <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                    <path
                      d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12Zm11 3a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z"
                      fill="none"
                      stroke="currentColor"
                      stroke-width="1.5"
                    />
                  </svg>
                </button>
                <button
                  class="icon-btn"
                  @click="copyToken(u.username)"
                  :disabled="revealLoading === u.username"
                >
                  📋
                </button>
              </td>
              <td>
                <button
                  class="link danger"
                  @click="deleteUser(u.username)"
                  :disabled="deleting === u.username"
                >
                  {{ $t('common.delete') }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="createError" class="error-text">{{ createError }}</p>
    </section>

    <section class="panel grid">
      <div>
        <div class="section-head">
          <h3>{{ $t('adminApi.containerSection') }}</h3>
          <small class="muted">{{ $t('adminApi.realtimeFetch') }}</small>
        </div>
        <div class="table-wrapper small scrollable">
          <table>
            <thead>
              <tr>
                <th>{{ $t('adminApi.tableKey') }}</th>
                <th>{{ $t('adminApi.tableCpu') }}</th>
                <th>{{ $t('adminApi.tableMemPercent') }}</th>
                <th>{{ $t('adminApi.tableMemory') }}</th>
                <th>{{ $t('adminApi.tableNetIo') }}</th>
                <th>{{ $t('adminApi.tableStatus') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!containerItems.length">
                <td colspan="6">{{ $t('adminApi.emptyContainers') }}</td>
              </tr>
              <tr v-for="c in containerItems" :key="c.key">
                <td>{{ c.key }}</td>
                <td>{{ formatPercentNumber(c.stats?.cpu_percent) }}</td>
                <td>{{ formatPercentNumber(c.stats?.memory?.percent) }}</td>
                <td>{{ formatBytes(c.stats?.memory?.used_bytes) }}</td>
                <td>
                  {{ formatBytes(c.stats?.net_io?.rx_bytes) }} /
                  {{ formatBytes(c.stats?.net_io?.tx_bytes) }}
                </td>
                <td>
                  <span :class="['status-badge', c.running ? 'online' : 'offline']">
                    {{ c.running ? $t('common.running') : $t('common.notRunning') }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <div>
        <div class="section-head">
          <h3>{{ $t('adminApi.uploadSection') }}</h3>
          <small class="muted">{{ $t('adminApi.uploadSourceNote') }}</small>
        </div>
        <ul class="upload-feed scrollable">
          <li v-if="!uploads.recent_events?.length" class="upload-item">{{ $t('adminApi.emptyRecords') }}</li>
          <li v-for="item in uploads.recent_events || []" :key="item.upload_id" class="upload-item">
            <div>
              <strong>{{ item.original_name || $t('adminApi.unnamedFile') }}</strong>
              <div class="muted small">
                {{
                  $t('adminApi.uploadMeta', {
                    username: item.username,
                    size: formatBytes(item.size),
                    source: item.source || 'unknown'
                  })
                }}
              </div>
            </div>
            <div class="muted small">
              {{ timeAgo(item.timestamp) }}
              <span :class="['status-badge', item.accepted ? 'online' : 'danger']">
                {{ item.accepted ? $t('adminApi.accepted') : $t('adminApi.blocked') }}
              </span>
            </div>
          </li>
        </ul>
      </div>
    </section>

  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useSecondaryPass } from './useSecondaryPass';
import SecondaryGate from './SecondaryGate.vue';
import { t } from '@/locales';

const {
  verified: secondaryVerified,
  configured: secondaryConfigured,
  loading: secondaryLoading,
  error: secondaryError,
  check: checkSecondary,
  verify: verifySecondary
} = useSecondaryPass();

const loading = ref(false);
const autoRefresh = ref(true);
let timer: number | undefined;

const snapshot = ref<any>(null);
const users = ref<any[]>([]);
const revealTokens = ref<Record<string, { token: string; visible: boolean }>>({});
const revealLoading = ref('');
const deleting = ref('');
const creating = ref(false);
const createForm = ref({ username: '', note: '' });
const createError = ref('');

const overview = computed(() => snapshot.value?.overview || {});
const uploads = computed(() => snapshot.value?.uploads || {});
const containerItems = computed(() => {
  const items = snapshot.value?.containers || {};
  return Object.keys(items).map((key) => ({ key, ...(items[key] || {}) }));
});
const totalRequests = computed(() =>
  users.value.reduce(
    (sum, u) => sum + (u.usage && u.usage.total_requests ? u.usage.total_requests : 0),
    0
  )
);

const formatNumber = (val: any) => {
  const num = Number(val || 0);
  return Number.isFinite(num) ? num.toLocaleString('zh-CN') : '0';
};
const formatBytes = (v: any) => {
  const num = Number(v || 0);
  if (!num) return '0B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let n = num;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i += 1;
  }
  return `${n.toFixed(1)}${units[i]}`;
};
const formatTime = (iso?: string) => {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
};
const timeAgo = (iso?: string) => {
  if (!iso) return '—';
  const ts = new Date(iso).getTime();
  if (!Number.isFinite(ts)) return '—';
  const diff = Date.now() - ts;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return t('common.justNow');
  if (mins < 60) return t('common.minutesAgo', { n: mins });
  const hours = Math.floor(mins / 60);
  if (hours < 24) return t('common.hoursAgo', { n: hours });
  const days = Math.floor(hours / 24);
  return t('common.daysAgo', { n: days });
};
const formatPercentNumber = (v: any) => {
  if (v === null || v === undefined) return '—';
  const num = Number(v);
  if (!Number.isFinite(num)) return '—';
  return `${num.toFixed(1)}%`;
};

const fetchDashboard = async () => {
  if (!secondaryVerified.value) return;
  loading.value = true;
  try {
    const resp = await fetch('/api/admin/api-dashboard', { credentials: 'same-origin' });
    const data = await resp.json();
    if (!resp.ok || !data.success) throw new Error(data.error || t('common.loadFailed'));
    snapshot.value = data.data;
  } catch (err: any) {
    console.error(err);
  } finally {
    loading.value = false;
  }
};

const fetchUsers = async () => {
  if (!secondaryVerified.value) return;
  try {
    const resp = await fetch('/api/admin/api-users', { credentials: 'same-origin' });
    const data = await resp.json();
    if (!resp.ok || !data.success) throw new Error(data.error || t('common.loadFailed'));
    const map: Record<string, any> = {};
    users.value = (data.data || []).map((u: any) => ({
      ...u,
      usage: u.usage || { total_requests: 0, endpoints: {}, last_request_at: null },
      tokens: u.tokens || { total_tokens: 0, input_tokens: 0, output_tokens: 0 },
      storage: u.storage || { total_bytes: 0 }
    }));
    users.value.forEach((u) => {
      map[u.username] = revealTokens.value[u.username] || { token: '', visible: false };
    });
    revealTokens.value = map;
  } catch (err: any) {
    console.error(err);
  }
};

const fetchAll = async () => {
  await Promise.all([fetchDashboard(), fetchUsers()]);
};

const scheduleAutoRefresh = () => {
  if (timer) {
    clearInterval(timer);
    timer = undefined;
  }
  if (autoRefresh.value && secondaryVerified.value) {
    timer = window.setInterval(fetchAll, 30000);
  }
};

const createUser = async () => {
  if (!secondaryVerified.value) return;
  createError.value = '';
  const username = createForm.value.username.trim().toLowerCase();
  if (!username) {
    createError.value = t('adminApi.usernameRequired');
    return;
  }
  const pattern = /^[a-z0-9_-]{3,32}$/;
  if (!pattern.test(username)) {
    createError.value = t('adminApi.usernameInvalid');
    return;
  }
  creating.value = true;
  try {
    const resp = await fetch('/api/admin/api-users', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, note: createForm.value.note })
    });
    const data = await resp.json();
    if (!resp.ok || !data.success) throw new Error(data.error || t('adminApi.createFailed'));
    await fetchUsers();
    revealTokens.value[username] = { token: data.data.token, visible: true };
    createForm.value = { username: '', note: '' };
  } catch (err: any) {
    createError.value = err.message || t('adminApi.createFailed');
  } finally {
    creating.value = false;
  }
};

const deleteUser = async (username: string) => {
  if (!secondaryVerified.value) return;
  if (!confirm(t('adminApi.deleteConfirm', { username }))) return;
  deleting.value = username;
  try {
    const resp = await fetch(`/api/admin/api-users/${encodeURIComponent(username)}`, {
      method: 'DELETE',
      credentials: 'same-origin'
    });
    const data = await resp.json();
    if (!resp.ok || !data.success) throw new Error(data.error || t('adminApi.deleteFailed'));
    await fetchUsers();
  } catch (err: any) {
    alert(err.message || t('adminApi.deleteFailed'));
  } finally {
    deleting.value = '';
  }
};

const toggleReveal = async (username: string) => {
  const entry = revealTokens.value[username];
  if (entry?.visible) {
    revealTokens.value[username] = { ...(entry || {}), visible: false };
    return;
  }
  revealLoading.value = username;
  try {
    const resp = await fetch(`/api/admin/api-users/${encodeURIComponent(username)}/token`, {
      credentials: 'same-origin'
    });
    const data = await resp.json();
    if (!resp.ok || !data.success) throw new Error(data.error || t('adminApi.fetchFailed'));
    revealTokens.value[username] = { token: data.data.token, visible: true };
  } catch (err: any) {
    alert(err.message || t('adminApi.fetchTokenFailed'));
  } finally {
    revealLoading.value = '';
  }
};

const copyToken = async (username: string) => {
  const entry = revealTokens.value[username];
  if (!entry?.token) {
    await toggleReveal(username);
  }
  const token = revealTokens.value[username]?.token;
  if (!token) return;
  try {
    await navigator.clipboard.writeText(token);
  } catch (err) {
    console.error(err);
  }
};

const handleVerifySecondary = async (password: string) => {
  await verifySecondary(password);
  if (secondaryVerified.value) {
    await fetchAll();
    scheduleAutoRefresh();
  }
};

onMounted(async () => {
  await checkSecondary();
  if (secondaryVerified.value) {
    await fetchAll();
    scheduleAutoRefresh();
  }
});

watch(autoRefresh, scheduleAutoRefresh);
watch(secondaryVerified, (val) => {
  if (val) {
    fetchAll();
    scheduleAutoRefresh();
  } else if (timer) {
    clearInterval(timer);
    timer = undefined;
  }
});

onBeforeUnmount(() => {
  if (timer) clearInterval(timer);
});
</script>

<style scoped>
:global(body) {
  margin: 0;
  background: #f7f3ea;
  font-family: 'Iowan Old Style', ui-serif, Georgia, Cambria, 'Times New Roman', serif;
  color: #2a2013;
}

:global(#admin-api-app) {
  min-height: 100vh;
  padding: 24px 32px 48px;
  box-sizing: border-box;
}

.api-page {
  max-width: 1400px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.api-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.header-actions {
  display: flex;
  gap: 10px;
  align-items: center;
}

.panel {
  background: #f6ecda;
  border-radius: 20px;
  padding: 18px;
  border: 1px solid rgba(118, 103, 84, 0.35);
  box-shadow: 0 10px 22px rgba(0, 0, 0, 0.08);
}

.metrics-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}

.metric-card {
  background: rgba(255, 255, 255, 0.94);
  border-radius: 16px;
  padding: 14px;
  border: 1px solid rgba(44, 32, 19, 0.12);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.6);
}

.metric-card p {
  margin: 0;
  color: #6b5b44;
  font-size: 13px;
  letter-spacing: 0.05em;
}

.metric-card strong {
  font-size: 26px;
  display: block;
  margin-top: 6px;
}

.muted {
  color: #6b5b44;
}

.muted.small {
  font-size: 12px;
}

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.create-form {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.create-form input {
  padding: 10px 12px;
  border-radius: 12px;
  border: 1px solid rgba(0, 0, 0, 0.12);
}

button {
  border: none;
  border-radius: 12px;
  padding: 10px 14px;
  background: var(--highlight);
  color: #2a2013;
  font-weight: 600;
  cursor: pointer;
}

.ghost-btn {
  background: transparent;
  border: 1px dashed rgba(44, 32, 19, 0.35);
}

.table-wrapper {
  width: 100%;
  overflow: auto;
}

.table-wrapper.scrollable {
  max-height: 320px;
}

table {
  width: 100%;
  border-collapse: collapse;
}

th,
td {
  padding: 10px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.08);
  text-align: left;
  font-size: 14px;
}

.token-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.icon-btn {
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(0, 0, 0, 0.12);
  border-radius: 8px;
  padding: 6px;
  cursor: pointer;
}

.link.danger {
  background: transparent;
  color: #b5473d;
  border: 1px solid rgba(181, 71, 61, 0.4);
}

.status-badge {
  display: inline-flex;
  align-items: center;
  padding: 4px 8px;
  border-radius: 10px;
  font-size: 12px;
}

.status-badge.online {
  background: rgba(73, 160, 120, 0.15);
  color: #2f6b4f;
}

.status-badge.offline {
  background: rgba(0, 0, 0, 0.06);
  color: #5f5140;
}

.status-badge.danger {
  background: rgba(189, 93, 58, 0.15);
  color: #b5473d;
}

.upload-feed {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.upload-feed.scrollable {
  max-height: 280px;
  overflow: auto;
}

.upload-item {
  padding: 10px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid rgba(0, 0, 0, 0.06);
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 14px;
}

.error-text {
  color: #b5473d;
  margin-top: 8px;
}

</style>
