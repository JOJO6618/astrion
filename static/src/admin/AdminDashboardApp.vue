<template>
  <div v-if="isInitialLoading" class="admin-loading">
    <div class="spinner" />
    <p>正在加载监控数据...</p>
  </div>
  <div v-else class="admin-page">
    <SecondaryGate
      v-if="!secondaryVerified"
      :configured="secondaryConfigured"
      :loading="secondaryLoading"
      :error="secondaryError"
      description="为保护敏感监控数据，需二级校验后查看。"
      @verify="handleVerifySecondary"
      @recheck="checkSecondary"
    />
    <header class="admin-header">
      <div>
        <h1>管理员监控面板</h1>
        <p>最近更新：{{ timeAgo(snapshot?.generated_at || overview.generated_at) }}</p>
      </div>
      <div class="header-actions">
        <label> <input type="checkbox" v-model="autoRefresh" /> 自动刷新 </label>
        <button type="button" :disabled="refreshing" @click="handleManualRefresh">
          {{ refreshing ? '刷新中...' : '立即刷新' }}
        </button>
        <a class="link-btn" href="/admin/policy" target="_blank" rel="noopener">策略配置</a>
        <a class="link-btn" href="/admin/api" target="_blank" rel="noopener">API 管理</a>
      </div>
    </header>

    <section v-if="bannerError" class="banner-error">
      <strong>刷新失败：</strong>
      <span>{{ bannerError }}</span>
    </section>

    <div class="admin-layout" v-if="snapshot">
      <aside class="admin-sidebar">
        <button
          v-for="tab in sectionTabs"
          :key="tab.id"
          type="button"
          :class="['sidebar-tab', { active: activeSection === tab.id }]"
          @click="activeSection = tab.id"
        >
          {{ tab.label }}
        </button>
      </aside>
      <main class="admin-main">
        <transition name="fade" mode="out-in">
          <section v-if="activeSection === 'overview'" key="overview" class="panel">
            <div class="metrics-grid">
              <div v-for="card in metricCards" :key="card.title" class="metric-card">
                <h3>{{ card.title }}</h3>
                <strong>{{ card.value }}</strong>
                <span>{{ card.sub }}</span>
              </div>
            </div>
          </section>
          <section v-else-if="activeSection === 'usage'" key="usage" class="panel">
            <h2>模型配额与趋势</h2>
            <div class="stats-row">
              <span>快速模式：{{ usageTotals.fast || 0 }}</span>
              <span>思考模式：{{ usageTotals.thinking || 0 }}</span>
              <span>搜索：{{ usageTotals.search || 0 }}</span>
            </div>
            <div class="token-summary">
              <div class="token-card">
                <p>累计输入 Token</p>
                <strong>{{ formatNumber(tokenTotals.input_tokens) }}</strong>
              </div>
              <div class="token-card">
                <p>累计输出 Token</p>
                <strong>{{ formatNumber(tokenTotals.output_tokens) }}</strong>
              </div>
              <div class="token-card">
                <p>总计 Token</p>
                <strong>{{ formatNumber(tokenTotals.total_tokens) }}</strong>
              </div>
            </div>
            <div class="token-breakdown">
              <div class="token-breakdown-header">
                <h3>按用户累计 Token</h3>
                <span>实时拉取 · 按总量降序</span>
              </div>
              <div class="token-table-wrapper" v-if="tokenBreakdown.length">
                <table class="token-table">
                  <thead>
                    <tr>
                      <th>用户</th>
                      <th>角色</th>
                      <th>输入 Token</th>
                      <th>输出 Token</th>
                      <th>累计 Token</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="entry in tokenBreakdown" :key="entry.username">
                      <td>{{ entry.username }}</td>
                      <td>{{ entry.role }}</td>
                      <td>{{ formatNumber(entry.input) }}</td>
                      <td>{{ formatNumber(entry.output) }}</td>
                      <td>
                        <strong>{{ formatNumber(entry.total) }}</strong>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p class="token-empty" v-else>暂无 token 统计数据</p>
            </div>
            <div class="usage-leaders">
              <div v-for="block in leaderBlocks" :key="block.key" class="leader-card">
                <h3>{{ block.label }}</h3>
                <ul>
                  <li v-if="!block.items.length" class="muted">暂无数据</li>
                  <li v-for="item in block.items" :key="item.username">
                    <strong>{{ item.username }}</strong>
                    <span>{{ item.count }} / {{ item.limit ?? '∞' }}</span>
                  </li>
                </ul>
              </div>
            </div>
          </section>
          <section v-else-if="activeSection === 'users'" key="users" class="panel">
            <h2>用户与配额</h2>
            <div class="table-wrapper">
              <table>
                <thead>
                  <tr>
                    <th>用户</th>
                    <th>角色</th>
                    <th>状态</th>
                    <th>项目占用</th>
                    <th>Fast</th>
                    <th>Thinking</th>
                    <th>Search</th>
                    <th>项目体积</th>
                    <th>最近活跃</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-if="!users.length">
                    <td colspan="9">暂无用户</td>
                  </tr>
                  <tr v-for="user in users" :key="user.username">
                    <td>
                      <strong>{{ user.username }}</strong>
                      <div class="subtext">{{ user.email || '—' }}</div>
                    </td>
                    <td>{{ user.role || 'user' }}</td>
                    <td>
                      <span :class="['status-badge', user.status?.online ? 'online' : 'offline']">
                        {{ user.status?.online ? '在线' : '离线' }}
                      </span>
                    </td>
                    <td>
                      <span :class="['status-badge', usageBadge(user.storage?.status)]">
                        {{ formatPercent(user.storage?.usage_percent) }}
                      </span>
                      <div class="progress-bar">
                        <span :style="{ width: percentWidth(user.storage?.usage_percent) }" />
                      </div>
                    </td>
                    <td>{{ user.usage?.fast?.count || 0 }}</td>
                    <td>{{ user.usage?.thinking?.count || 0 }}</td>
                    <td>{{ user.usage?.search?.count || 0 }}</td>
                    <td>{{ formatBytes(user.storage?.project_bytes) }}</td>
                    <td>{{ timeAgo(user.status?.last_active) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
          <section v-else-if="activeSection === 'containers'" key="containers" class="panel">
            <h2>容器运行状态</h2>
            <div class="table-wrapper">
              <table>
                <thead>
                  <tr>
                    <th>用户</th>
                    <th>模式</th>
                    <th>容器名</th>
                    <th>CPU</th>
                    <th>内存%</th>
                    <th>内存使用</th>
                    <th>内存上限</th>
                    <th>网络 RX/TX</th>
                    <th>状态</th>
                    <th>最近活跃</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-if="!containers.length">
                    <td colspan="10">暂无容器运行</td>
                  </tr>
                  <tr v-for="item in containers" :key="item.username">
                    <td>{{ item.username }}</td>
                    <td>{{ item.mode || 'host' }}</td>
                    <td>{{ item.container_name || '—' }}</td>
                    <td>{{ formatPercentNumber(item.stats?.cpu_percent) }}</td>
                    <td>{{ formatPercentNumber(item.stats?.memory?.percent) }}</td>
                    <td>{{ formatBytes(item.stats?.memory?.used_bytes) }}</td>
                    <td>{{ formatBytes(item.stats?.memory?.limit_bytes) }}</td>
                    <td>
                      {{ formatBytes(item.stats?.net_io?.rx_bytes) }} /
                      {{ formatBytes(item.stats?.net_io?.tx_bytes) }}
                    </td>
                    <td>
                      <span :class="['status-badge', containerStatusClass(item)]">
                        {{ item.state?.status || (item.state?.running ? '运行中' : '未知') }}
                      </span>
                    </td>
                    <td>{{ timeAgo(item.last_active) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
          <section v-else-if="activeSection === 'uploads'" key="uploads" class="panel">
            <h2>上传安全审计</h2>
            <div class="stats-row">
              <span>最近24小时：{{ uploadStats.last_24h || 0 }}</span>
              <span>阻断：{{ uploadStats.blocked_last_24h || 0 }}</span>
              <span>跳过扫描：{{ uploadStats.skipped_scan_last_24h || 0 }}</span>
              <span>隔离区占用：{{ formatBytes(uploadStats.quarantine_bytes) }}</span>
            </div>
            <div class="stats-row">
              <span v-if="!uploadSources.length">暂无来源统计</span>
              <span v-for="source in uploadSources" :key="source.source">
                {{ source.source }}：{{ source.count }}
              </span>
            </div>
            <ul class="upload-feed">
              <li v-if="!recentUploads.length" class="upload-item">暂无近期上传</li>
              <li v-for="upload in recentUploads" :key="upload.upload_id" class="upload-item">
                <div>
                  <strong>{{ upload.original_name || '未命名文件' }}</strong>
                  <div class="upload-meta">
                    <span>用户：{{ upload.username }}</span>
                    <span>来源：{{ upload.source || 'unknown' }}</span>
                    <span>大小：{{ formatBytes(upload.size) }}</span>
                  </div>
                </div>
                <div class="upload-meta">
                  <span>{{ timeAgo(upload.timestamp) }}</span>
                  <span :class="['status-badge', upload.accepted ? 'online' : 'danger']">
                    {{ upload.accepted ? '通过' : '阻断' }}
                  </span>
                  <span v-if="upload.error?.message" class="status-badge danger">{{
                    upload.error.message
                  }}</span>
                </div>
              </li>
            </ul>
          </section>
          <section v-else-if="activeSection === 'invites'" key="invites" class="panel">
            <h2>邀请码</h2>
            <div class="stats-row">
              <span>总计：{{ inviteSummary.total || 0 }}</span>
              <span>可用：{{ inviteSummary.active || 0 }}</span>
              <span>已用：{{ inviteSummary.consumed || 0 }}</span>
              <span>无限制：{{ inviteSummary.unlimited || 0 }}</span>
            </div>
            <div class="invite-manage">
              <input
                v-model.trim="newInviteCode"
                class="invite-input"
                type="text"
                placeholder="新邀请码"
                :disabled="inviteSubmitting"
              />
              <input
                v-model="newInviteRemaining"
                class="invite-input short"
                type="number"
                min="0"
                placeholder="剩余次数"
                :disabled="inviteSubmitting || newInviteUnlimited"
              />
              <label class="invite-check">
                <input type="checkbox" v-model="newInviteUnlimited" :disabled="inviteSubmitting" />
                不限次数
              </label>
              <button type="button" :disabled="inviteSubmitting" @click="createInvite">
                {{ inviteSubmitting ? '处理中...' : '新增邀请码' }}
              </button>
            </div>
            <p v-if="inviteError" class="secondary-error">{{ inviteError }}</p>
            <div class="invite-grid">
              <div v-if="!inviteCodes.length" class="invite-card">暂无邀请码数据</div>
              <div v-for="code in inviteCodes" :key="code.code" class="invite-card">
                <h4>{{ code.code }}</h4>
                <span>{{ inviteStatus(code.remaining) }}</span>
                <div class="invite-actions">
                  <button
                    type="button"
                    class="mini-btn"
                    :disabled="inviteSubmitting"
                    @click="editInvite(code)"
                  >
                    调整次数
                  </button>
                  <button
                    type="button"
                    class="mini-btn danger"
                    :disabled="inviteSubmitting"
                    @click="removeInvite(code.code)"
                  >
                    删除
                  </button>
                </div>
              </div>
            </div>
          </section>
          <section v-else-if="activeSection === 'passwords'" key="passwords" class="panel">
            <h2>密码管理</h2>
            <p class="muted">管理员可重置任意用户的登录密码（包括自己）。</p>

            <div class="password-form">
              <div class="password-field">
                <label>选择用户</label>
                <div class="user-search-wrapper" ref="userSearchRef">
                  <div
                    class="user-search-trigger"
                    :class="{ open: passwordDropdownOpen, filled: passwordTargetUser }"
                    @click="togglePasswordDropdown"
                  >
                    <span v-if="passwordTargetUser" class="user-search-selected">
                      <strong>{{ passwordTargetUser }}</strong>
                      <span class="user-search-email">{{ selectedUserEmail }}</span>
                    </span>
                    <span v-else class="user-search-placeholder">搜索用户名或邮箱...</span>
                    <span class="user-search-arrow">▾</span>
                  </div>
                  <div v-if="passwordDropdownOpen" class="user-search-dropdown">
                    <input
                      ref="passwordSearchInputRef"
                      class="user-search-input"
                      type="text"
                      v-model="passwordSearchQuery"
                      placeholder="输入关键词筛选..."
                      @keydown.escape="passwordDropdownOpen = false"
                    />
                    <div class="user-search-list">
                      <div
                        v-for="user in filteredUsers"
                        :key="user.username"
                        class="user-search-item"
                        :class="{ active: passwordTargetUser === user.username }"
                        @click="selectPasswordUser(user)"
                      >
                        <span class="user-search-name">{{ user.username }}</span>
                        <span class="user-search-email">{{ user.email || '—' }}</span>
                        <span v-if="user.role === 'admin'" class="user-search-role">管理员</span>
                      </div>
                      <div v-if="!filteredUsers.length" class="user-search-empty">
                        无匹配用户
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              <div class="password-field">
                <label>新密码（至少 8 位）</label>
                <input
                  class="password-input"
                  type="password"
                  v-model="passwordNewValue"
                  :disabled="passwordSubmitting"
                  placeholder="输入新密码"
                  @keyup.enter="handleResetPassword"
                />
              </div>
              <div class="password-actions">
                <button
                  type="button"
                  :disabled="!passwordTargetUser || !passwordNewValue || passwordSubmitting"
                  @click="handleResetPassword"
                >
                  {{ passwordSubmitting ? '重置中...' : '重置密码' }}
                </button>
                <button
                  type="button"
                  class="ghost-btn"
                  :disabled="passwordSubmitting"
                  @click="passwordTargetUser = ''; passwordNewValue = ''; passwordResult = ''; passwordError = ''"
                >
                  清空
                </button>
              </div>
              <p v-if="passwordResult" class="password-success">{{ passwordResult }}</p>
              <p v-if="passwordError" class="password-error-msg">{{ passwordError }}</p>
            </div>
          </section>
        </transition>
      </main>
    </div>

    <div v-else class="admin-error">
      <strong>加载失败</strong>
      <p>{{ errorMessage || '无法加载监控数据' }}</p>
      <button type="button" @click="handleRetry">重试</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue';
import { useSecondaryPass } from './useSecondaryPass';
import SecondaryGate from './SecondaryGate.vue';

type Snapshot = Record<string, any> | null;

type SectionId = 'overview' | 'usage' | 'users' | 'containers' | 'uploads' | 'invites' | 'passwords';

const {
  verified: secondaryVerified,
  configured: secondaryConfigured,
  loading: secondaryLoading,
  error: secondaryError,
  check: checkSecondary,
  verify: verifySecondary
} = useSecondaryPass();

const loading = ref(true);
const refreshing = ref(false);
const snapshot = ref<Snapshot>(null);
const errorMessage = ref<string | null>(null);
const bannerError = ref<string | null>(null);
const autoRefresh = ref(true);
const activeSection = ref<SectionId>('overview');
let timer: number | undefined;
const inviteSubmitting = ref(false);
const inviteError = ref<string | null>(null);
const newInviteCode = ref('');
const newInviteRemaining = ref('1');
const newInviteUnlimited = ref(false);
const passwordTargetUser = ref('');
const passwordNewValue = ref('');
const passwordSubmitting = ref(false);
const passwordResult = ref('');
const passwordError = ref('');
const passwordSearchQuery = ref('');
const passwordDropdownOpen = ref(false);
const userSearchRef = ref<HTMLElement | null>(null);
const passwordSearchInputRef = ref<HTMLInputElement | null>(null);

const sectionTabs: Array<{ id: SectionId; label: string }> = [
  { id: 'overview', label: '系统总览' },
  { id: 'usage', label: '配额趋势' },
  { id: 'users', label: '用户' },
  { id: 'containers', label: '容器' },
  { id: 'uploads', label: '上传审计' },
  { id: 'invites', label: '邀请码' },
  { id: 'passwords', label: '密码管理' }
];

const isInitialLoading = computed(() => loading.value && !snapshot.value);

const fetchDashboard = async (background = false) => {
  if (!secondaryVerified.value) {
    loading.value = false;
    return;
  }
  if (background) {
    refreshing.value = true;
  } else if (!snapshot.value) {
    loading.value = true;
  }
  try {
    const resp = await fetch('/api/admin/dashboard', { credentials: 'same-origin' });
    if (!resp.ok) {
      throw new Error(`请求失败：${resp.status}`);
    }
    const payload = await resp.json();
    if (!payload.success) {
      throw new Error(payload.error || '未知错误');
    }
    // 额外调试：若后台返回 debug 字段，打印到控制台便于问题定位（仅开发模式会看到）
    if (import.meta.env.DEV && payload.data?.debug) {
      console.info('[admin dashboard debug]', payload.data.debug);
    }
    snapshot.value = payload.data;
    errorMessage.value = null;
    bannerError.value = null;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (!snapshot.value) {
      errorMessage.value = message;
    } else {
      bannerError.value = message;
    }
  } finally {
    if (background) {
      refreshing.value = false;
    }
    if (!snapshot.value) {
      loading.value = false;
    }
  }
};

const scheduleAutoRefresh = () => {
  if (timer) {
    clearInterval(timer);
    timer = undefined;
  }
  if (autoRefresh.value && secondaryVerified.value) {
    timer = window.setInterval(() => fetchDashboard(true), 30000);
  }
};

const handleManualRefresh = () => fetchDashboard(true);
const handleRetry = () => fetchDashboard(false);

const fetchCsrfToken = async (): Promise<string> => {
  const resp = await fetch('/api/csrf-token', { credentials: 'same-origin' });
  if (!resp.ok) {
    throw new Error(`获取 CSRF Token 失败：${resp.status}`);
  }
  const payload = await resp.json();
  const token = payload?.token;
  if (!token || typeof token !== 'string') {
    throw new Error('获取 CSRF Token 失败：响应无 token');
  }
  return token;
};

const parseInviteRemaining = (raw: unknown): number | null => {
  const text = String(raw ?? '')
    .trim()
    .toLowerCase();
  if (
    !text ||
    text === 'unlimited' ||
    text === 'null' ||
    text === 'none' ||
    text === '不限' ||
    text === '无限制'
  ) {
    return null;
  }
  const parsed = Number(text);
  if (!Number.isFinite(parsed) || parsed < 0 || !Number.isInteger(parsed)) {
    throw new Error('剩余次数必须是大于等于 0 的整数，或留空表示不限次数');
  }
  return parsed;
};

const createInvite = async () => {
  const code = newInviteCode.value.trim();
  if (!code) {
    inviteError.value = '请输入邀请码';
    return;
  }
  inviteSubmitting.value = true;
  inviteError.value = null;
  try {
    const remaining = newInviteUnlimited.value
      ? null
      : parseInviteRemaining(newInviteRemaining.value);
    const csrfToken = await fetchCsrfToken();
    const resp = await fetch('/api/admin/invites', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': csrfToken
      },
      credentials: 'same-origin',
      body: JSON.stringify({ code, remaining })
    });
    const payload = await resp.json();
    if (!resp.ok || !payload.success) {
      throw new Error(payload.error || `请求失败：${resp.status}`);
    }
    newInviteCode.value = '';
    newInviteRemaining.value = '1';
    newInviteUnlimited.value = false;
    await fetchDashboard(true);
  } catch (error) {
    inviteError.value = error instanceof Error ? error.message : String(error);
  } finally {
    inviteSubmitting.value = false;
  }
};

const handleResetPassword = async () => {
  const username = passwordTargetUser.value.trim();
  const password = passwordNewValue.value.trim();
  if (!username || !password) return;
  if (password.length < 8) {
    passwordError.value = '密码长度至少 8 位';
    passwordResult.value = '';
    return;
  }
  passwordSubmitting.value = true;
  passwordResult.value = '';
  passwordError.value = '';
  try {
    const csrfToken = await fetchCsrfToken();
    const resp = await fetch(`/api/admin/users/${encodeURIComponent(username)}/reset-password`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': csrfToken
      },
      credentials: 'same-origin',
      body: JSON.stringify({ password })
    });
    const payload = await resp.json();
    if (!resp.ok || !payload.success) {
      throw new Error(payload.error || `请求失败：${resp.status}`);
    }
    passwordResult.value = `用户 ${username} 的密码已成功重置`;
    passwordNewValue.value = '';
  } catch (error) {
    passwordError.value = error instanceof Error ? error.message : String(error);
  } finally {
    passwordSubmitting.value = false;
  }
};

const filteredUsers = computed(() => {
  const query = passwordSearchQuery.value.trim().toLowerCase();
  if (!query) return users.value;
  return users.value.filter(
    (u: any) =>
      u.username.toLowerCase().includes(query) ||
      (u.email || '').toLowerCase().includes(query)
  );
});

const selectedUserEmail = computed(() => {
  const user = users.value.find((u: any) => u.username === passwordTargetUser.value);
  return user?.email || '';
});

const togglePasswordDropdown = () => {
  passwordDropdownOpen.value = !passwordDropdownOpen.value;
  if (passwordDropdownOpen.value) {
    passwordSearchQuery.value = '';
    // 聚焦搜索框
    setTimeout(() => {
      passwordSearchInputRef.value?.focus();
    }, 50);
  }
};

const selectPasswordUser = (user: any) => {
  passwordTargetUser.value = user.username;
  passwordSearchQuery.value = '';
  passwordDropdownOpen.value = false;
};

const handleClickOutside = (event: MouseEvent) => {
  if (userSearchRef.value && !userSearchRef.value.contains(event.target as Node)) {
    passwordDropdownOpen.value = false;
  }
};

const editInvite = async (item: any) => {
  const current = item?.remaining;
  const seed = current === null || typeof current === 'undefined' ? 'unlimited' : String(current);
  const value = window.prompt('请输入新的剩余次数（留空或输入 unlimited 表示不限次数）', seed);
  if (value === null) return;
  inviteSubmitting.value = true;
  inviteError.value = null;
  try {
    const remaining = parseInviteRemaining(value);
    const csrfToken = await fetchCsrfToken();
    const resp = await fetch('/api/admin/invites', {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': csrfToken
      },
      credentials: 'same-origin',
      body: JSON.stringify({ code: item?.code, remaining })
    });
    const payload = await resp.json();
    if (!resp.ok || !payload.success) {
      throw new Error(payload.error || `请求失败：${resp.status}`);
    }
    await fetchDashboard(true);
  } catch (error) {
    inviteError.value = error instanceof Error ? error.message : String(error);
  } finally {
    inviteSubmitting.value = false;
  }
};

const removeInvite = async (code: string) => {
  if (!code) return;
  if (!window.confirm(`确认删除邀请码 ${code}？`)) return;
  inviteSubmitting.value = true;
  inviteError.value = null;
  try {
    const csrfToken = await fetchCsrfToken();
    const resp = await fetch('/api/admin/invites', {
      method: 'DELETE',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': csrfToken
      },
      credentials: 'same-origin',
      body: JSON.stringify({ code })
    });
    const payload = await resp.json();
    if (!resp.ok || !payload.success) {
      throw new Error(payload.error || `请求失败：${resp.status}`);
    }
    await fetchDashboard(true);
  } catch (error) {
    inviteError.value = error instanceof Error ? error.message : String(error);
  } finally {
    inviteSubmitting.value = false;
  }
};

const handleVerifySecondary = async (password: string) => {
  await verifySecondary(password);
  if (secondaryVerified.value) {
    fetchDashboard(false);
    scheduleAutoRefresh();
  }
};

watch(autoRefresh, () => {
  scheduleAutoRefresh();
});

onMounted(async () => {
  document.addEventListener('click', handleClickOutside);
  await checkSecondary();
  if (secondaryVerified.value) {
    fetchDashboard(false);
    scheduleAutoRefresh();
  } else {
    loading.value = false;
  }
});

watch(secondaryVerified, (val) => {
  if (val) {
    fetchDashboard(false);
    scheduleAutoRefresh();
  } else if (timer) {
    clearInterval(timer);
    timer = undefined;
    loading.value = false;
  }
});

onBeforeUnmount(() => {
  document.removeEventListener('click', handleClickOutside);
  if (timer) {
    clearInterval(timer);
  }
});

const snapshotData = computed(() => snapshot.value || null);
const overview = computed(() => snapshotData.value?.overview ?? {});
const usageTotals = computed(() => overview.value.usage_totals ?? {});
const tokenTotals = computed(() => overview.value.token_totals ?? {});
const leaderBlocks = computed(() => {
  const leaders = overview.value.usage_leaders ?? {};
  return [
    { key: 'fast', label: '快速模式 TOP', items: leaders.fast || [] },
    { key: 'thinking', label: '思考模式 TOP', items: leaders.thinking || [] },
    { key: 'search', label: '搜索调用 TOP', items: leaders.search || [] }
  ];
});
const storage = computed(() => overview.value.storage ?? {});
const uploadStats = computed(() => snapshotData.value?.uploads?.stats ?? {});
const uploadSources = computed(() => snapshotData.value?.uploads?.sources ?? []);
const recentUploads = computed(() => snapshotData.value?.uploads?.recent_events ?? []);
const users = computed(() => snapshotData.value?.users ?? []);
const containers = computed(() => snapshotData.value?.containers ?? []);
const inviteSummary = computed(() => snapshotData.value?.invites?.summary ?? {});
const inviteCodes = computed(() => snapshotData.value?.invites?.codes ?? []);
const totals = computed(() => overview.value.totals ?? {});
const containerSummary = computed(() => overview.value.containers ?? {});
const invitesOverview = computed(() => overview.value.invites ?? {});
const tokenBreakdown = computed(() => {
  const list = users.value.map((user: any) => ({
    username: user.username,
    role: user.role || 'user',
    input: Number(user.tokens?.input_tokens || 0),
    output: Number(user.tokens?.output_tokens || 0),
    total: Number(user.tokens?.total_tokens || 0)
  }));
  return list.sort((a, b) => b.total - a.total);
});

const metricCards = computed(() => [
  {
    title: '注册用户',
    value: totals.value.users || 0,
    sub: `${totals.value.active_users || 0} 个活跃`
  },
  {
    title: '容器使用',
    value: `${containerSummary.value.active || 0}/${containerSummary.value.max_containers ?? '∞'}`,
    sub:
      containerSummary.value.available_slots === null ||
      containerSummary.value.available_slots === undefined
        ? '无限制'
        : `${containerSummary.value.available_slots} 个空闲`
  },
  {
    title: '资源告警',
    value: (storage.value.warning_users || []).length,
    sub: '达到预警阈值的工作区'
  },
  {
    title: '上传审计 (24h)',
    value: uploadStats.value.last_24h || 0,
    sub: `${uploadStats.value.blocked_last_24h || 0} 条被阻断`
  },
  {
    title: '配额调用 (fast / thinking / search)',
    value: `${usageTotals.value.fast || 0} / ${usageTotals.value.thinking || 0} / ${usageTotals.value.search || 0}`,
    sub: '窗口内累计调用'
  },
  {
    title: '邀请码',
    value: invitesOverview.value.total || 0,
    sub: `${invitesOverview.value.active || 0} 个可用 · ${invitesOverview.value.consumed || 0} 已用`
  },
  {
    title: '存储占用',
    value: formatBytes(storage.value.total_bytes || 0),
    sub: storage.value.per_user_limit_bytes
      ? `单用户上限 ${formatBytes(storage.value.per_user_limit_bytes)}`
      : '未设置上限'
  }
]);

const formatBytes = (value?: number | null) => {
  if (value === null || value === undefined || Number(value) <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let val = Number(value);
  const index = Math.min(Math.floor(Math.log(val) / Math.log(1024)), units.length - 1);
  val /= 1024 ** index;
  const digits = val >= 100 ? 0 : 1;
  return `${val.toFixed(digits)} ${units[index]}`;
};

const percentWidth = (value?: number | null) => {
  if (value === null || value === undefined) return '0%';
  return `${Math.min(100, Math.max(0, Number(value)))}%`;
};

const formatPercent = (value?: number | null) => {
  if (value === null || value === undefined || isNaN(Number(value))) return '—';
  return `${Number(value).toFixed(1)}%`;
};

const formatNumber = (value?: number | null) => {
  if (value === null || value === undefined) return '0';
  const parsed = Number(value);
  if (Number.isNaN(parsed)) return '0';
  return parsed.toLocaleString();
};

const formatPercentNumber = (value?: number | null) => {
  if (value === null || value === undefined || isNaN(Number(value))) return '—';
  return `${Number(value).toFixed(1)}%`;
};

const timeAgo = (input?: string | number | null) => {
  if (!input) return '未知时间';
  const date = new Date(input);
  if (Number.isNaN(date.getTime())) return String(input);
  const diff = Date.now() - date.getTime();
  if (diff < 60_000) return '刚刚';
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  return `${days} 天前`;
};

const usageBadge = (status?: string) => {
  if (status === 'critical') return 'danger';
  if (status === 'warning') return 'warning';
  return 'online';
};

const containerStatusClass = (item: any) => {
  if (item?.error) return 'danger';
  if (item?.state && item.state.running === false) return 'warning';
  return 'online';
};

const inviteStatus = (remaining: number | null | undefined) => {
  if (remaining === null || remaining === undefined) return '无限制';
  if (remaining > 0) return `剩余 ${remaining}`;
  return '已用完';
};
</script>

<style scoped>
:global(body) {
  margin: 0;
  background: #f7f3ea;
  font-family: 'Iowan Old Style', ui-serif, Georgia, Cambria, 'Times New Roman', serif;
  color: #2a2013;
  overflow: hidden;
}

:global(#admin-app) {
  min-height: 100vh;
  height: 100vh;
  padding: 24px 32px;
  background: #f7f3ea;
  color: #2a2013;
  box-sizing: border-box;
}

.admin-page {
  max-width: 1400px;
  margin: 0 auto;
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.admin-header {
  display: flex;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
}

.admin-header h1 {
  margin: 0;
  font-size: 28px;
}

.admin-header p {
  margin: 4px 0 0;
  color: #6b5b44;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-actions label {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #5b4b35;
}

.link-btn {
  padding: 10px 14px;
  border-radius: 12px;
  border: 1px solid rgba(0, 0, 0, 0.12);
  background: #fff;
  color: #2a2013;
  text-decoration: none;
  font-weight: 600;
}

.link-btn:hover {
  background: rgba(0, 0, 0, 0.04);
}

button {
  border: none;
  border-radius: 999px;
  padding: 10px 18px;
  background: var(--highlight);
  color: #3b2f1d;
  font-weight: 600;
  cursor: pointer;
  box-shadow: inset 0 -2px 0 rgba(189, 93, 58, 0.25);
  transition:
    transform 0.15s ease,
    box-shadow 0.15s ease,
    background 0.2s ease;
}

button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
  box-shadow: none;
}

button:not(:disabled):hover {
  transform: translateY(-1px);
  background: var(--accent);
  box-shadow: 0 8px 16px rgba(189, 93, 58, 0.25);
}

.banner-error {
  padding: 12px 16px;
  border-radius: 14px;
  border: 1px solid rgba(189, 93, 58, 0.5);
  background: rgba(189, 93, 58, 0.15);
  color: #7c3418;
}

.admin-layout {
  display: flex;
  gap: 20px;
  flex: 1;
  min-height: 0;
}

.admin-sidebar {
  width: 220px;
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.85);
  border-radius: 24px;
  border: 1px solid rgba(118, 103, 84, 0.35);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.sidebar-tab {
  width: 100%;
  padding: 10px 12px;
  border-radius: 14px;
  border: 1px solid transparent;
  background: rgba(255, 255, 255, 0.7);
  color: #3c2f1d;
  text-align: left;
}

.sidebar-tab.active {
  background: rgba(218, 119, 86, 0.2);
  color: #3c2f1d;
  border-color: rgba(189, 93, 58, 0.55);
  box-shadow: 0 10px 22px rgba(189, 93, 58, 0.18);
  font-weight: 600;
}

.admin-main {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding-right: 8px;
}

.panel {
  background: #f6ecda;
  border-radius: 28px;
  padding: 24px;
  border: 1px solid rgba(118, 103, 84, 0.35);
  box-shadow: var(--shadow-card);
  color: #23170b;
}

.metrics-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 16px;
}

.metric-card {
  background: rgba(255, 255, 255, 0.95);
  border-radius: 22px;
  padding: 18px;
  border: 1px solid rgba(118, 103, 84, 0.4);
}

.metric-card h3 {
  margin: 0;
  font-size: 13px;
  color: var(--text-secondary);
  letter-spacing: 0.08em;
}

.metric-card strong {
  font-size: 28px;
  display: block;
  margin-top: 8px;
}

.metric-card span {
  color: #574630;
  font-size: 13px;
}

.stats-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 18px;
  color: #5c4a32;
}

.stats-row span {
  background: rgba(218, 119, 86, 0.2);
  padding: 6px 12px;
  border-radius: 999px;
}

.token-summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin-bottom: 18px;
}

.token-card {
  border-radius: 18px;
  padding: 16px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid rgba(44, 32, 19, 0.1);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.6);
}

.token-card p {
  margin: 0;
  font-size: 12px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text-secondary);
}

.token-card strong {
  display: block;
  margin-top: 10px;
  font-size: 22px;
  color: #1f160c;
}

.token-breakdown {
  border: 1px solid rgba(118, 103, 84, 0.25);
  border-radius: 20px;
  padding: 18px;
  background: rgba(255, 255, 255, 0.86);
  margin-bottom: 18px;
}

.token-breakdown-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.token-breakdown-header h3 {
  margin: 0;
  font-size: 18px;
}

.token-breakdown-header span {
  font-size: 12px;
  color: var(--text-secondary);
}

.token-table-wrapper {
  max-height: 320px;
  overflow: auto;
}

.token-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.token-table th,
.token-table td {
  padding: 10px 8px;
  border-bottom: 1px solid rgba(118, 103, 84, 0.15);
  text-align: left;
}

.token-table th {
  font-size: 12px;
  color: #4c3a24;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.token-table td strong {
  font-size: 14px;
  color: #2c1f12;
}

.token-empty {
  margin: 0;
  color: var(--text-secondary);
  font-size: 13px;
}

.usage-leaders {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 14px;
}

.leader-card {
  border: 1px solid rgba(118, 103, 84, 0.18);
  border-radius: 18px;
  padding: 14px;
  background: rgba(255, 255, 255, 0.75);
}

.leader-card h3 {
  margin: 0 0 10px;
  color: var(--text-secondary);
  font-size: 14px;
}

.leader-card ul {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.leader-card li {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
}

.leader-card .muted {
  color: var(--text-muted);
}

.table-wrapper {
  overflow-x: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

thead {
  background: rgba(255, 248, 238, 0.85);
}

th,
td {
  padding: 12px;
  border-bottom: 1px solid rgba(118, 103, 84, 0.18);
  text-align: left;
}

th {
  color: #4f3f2a;
  font-weight: 600;
  font-size: 12px;
  letter-spacing: 0.05em;
}

.status-badge {
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
}

.status-badge.online {
  background: rgba(118, 176, 134, 0.18);
  color: var(--state-success);
}

.status-badge.offline {
  background: rgba(121, 109, 94, 0.2);
  color: #5b4d3b;
}

.status-badge.warning {
  background: rgba(217, 152, 69, 0.2);
  color: var(--state-warning);
}

.status-badge.danger {
  background: rgba(189, 93, 58, 0.18);
  color: var(--accent-strong);
}

.progress-bar {
  height: 6px;
  border-radius: 999px;
  background: rgba(121, 109, 94, 0.12);
  margin-top: 6px;
}

.progress-bar span {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, var(--highlight), var(--accent));
}

.upload-feed {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.upload-item {
  padding: 12px 16px;
  border-radius: 16px;
  border: 1px solid rgba(118, 103, 84, 0.3);
  background: rgba(255, 255, 255, 0.88);
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  justify-content: space-between;
}

.upload-meta {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  color: var(--text-secondary);
  font-size: 12px;
}

.invite-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
}

.invite-manage {
  margin: 10px 0 14px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}

.invite-input {
  min-width: 180px;
  height: 38px;
  border-radius: 10px;
  border: 1px solid rgba(118, 103, 84, 0.35);
  background: rgba(255, 255, 255, 0.92);
  padding: 0 10px;
  box-sizing: border-box;
}

.invite-input.short {
  min-width: 120px;
  width: 120px;
}

.invite-check {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--text-secondary);
  font-size: 13px;
}

.invite-card {
  border: 1px solid rgba(118, 103, 84, 0.3);
  border-radius: 16px;
  padding: 14px;
  background: rgba(255, 255, 255, 0.9);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.5);
}

.invite-card h4 {
  margin: 0 0 6px;
}

.invite-card span {
  color: var(--text-secondary);
  font-size: 13px;
}

.invite-actions {
  margin-top: 10px;
  display: flex;
  gap: 8px;
}

.mini-btn {
  border-radius: 10px;
  padding: 6px 10px;
  font-size: 12px;
}

.mini-btn.danger {
  background: rgba(189, 93, 58, 0.2);
  color: #7c3418;
  box-shadow: none;
}

.small {
  font-size: 13px;
}

.admin-loading {
  min-height: 60vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  color: var(--text-secondary);
}

.spinner {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  border: 4px solid rgba(0, 0, 0, 0.12);
  border-top-color: #050505;
  border-right-color: #050505;
  border-bottom-color: #f4f4f4;
  border-left-color: #f4f4f4;
  background: transparent;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.admin-error {
  background: rgba(189, 93, 58, 0.15);
  border: 1px solid rgba(189, 93, 58, 0.45);
  border-radius: 16px;
  padding: 18px;
  color: #7d341a;
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: flex-start;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.15s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

@media (max-width: 900px) {
  :global(#admin-app) {
    padding: 16px;
  }

  .admin-layout {
    flex-direction: column;
  }

  .admin-sidebar {
    width: 100%;
    flex-direction: row;
    flex-wrap: wrap;
  }

  .sidebar-tab {
    flex: 1;
    text-align: center;
  }
}

.password-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-width: 480px;
  margin-top: 12px;
}

.password-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.password-field label {
  font-size: 13px;
  color: var(--text-secondary);
  font-weight: 600;
}

.password-select,
.password-input {
  width: 100%;
  box-sizing: border-box;
  padding: 12px 14px;
  border-radius: 12px;
  border: 1px solid rgba(44, 32, 19, 0.2);
  font-size: 14px;
  background: rgba(255, 255, 255, 0.92);
}

.password-input {
  width: 100%;
  box-sizing: border-box;
  padding: 12px 14px;
  border-radius: 12px;
  border: 1px solid rgba(44, 32, 19, 0.2);
  font-size: 14px;
  background: rgba(255, 255, 255, 0.92);
}

/* 自定义用户搜索下拉 */
.user-search-wrapper {
  position: relative;
}

.user-search-trigger {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 12px 14px;
  border-radius: 12px;
  border: 1px solid rgba(44, 32, 19, 0.2);
  background: rgba(255, 255, 255, 0.92);
  cursor: pointer;
  min-height: 44px;
  box-sizing: border-box;
  transition: border-color 0.15s ease;
}

.user-search-trigger:hover {
  border-color: rgba(44, 32, 19, 0.4);
}

.user-search-trigger.open {
  border-color: rgba(189, 93, 58, 0.5);
  border-radius: 12px 12px 0 0;
}

.user-search-trigger.filled {
  border-color: rgba(44, 32, 19, 0.35);
}

.user-search-selected {
  display: flex;
  align-items: center;
  gap: 8px;
  overflow: hidden;
}

.user-search-selected strong {
  font-size: 14px;
  flex-shrink: 0;
}

.user-search-selected .user-search-email {
  font-size: 12px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-search-placeholder {
  color: #9a8b7a;
  font-size: 14px;
}

.user-search-arrow {
  font-size: 12px;
  color: var(--text-secondary);
  transition: transform 0.15s ease;
  flex-shrink: 0;
}

.user-search-trigger.open .user-search-arrow {
  transform: rotate(180deg);
}

.user-search-dropdown {
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  background: #fff;
  border: 1px solid rgba(189, 93, 58, 0.5);
  border-top: none;
  border-radius: 0 0 12px 12px;
  z-index: 100;
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.12);
  overflow: hidden;
}

.user-search-input {
  width: 100%;
  box-sizing: border-box;
  padding: 10px 14px;
  border: none;
  border-bottom: 1px solid rgba(118, 103, 84, 0.15);
  font-size: 13px;
  outline: none;
  background: rgba(255, 255, 255, 0.95);
}

.user-search-input::placeholder {
  color: #b0a392;
}

.user-search-list {
  max-height: 220px;
  overflow-y: auto;
}

.user-search-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  cursor: pointer;
  transition: background 0.1s ease;
}

.user-search-item:hover {
  background: rgba(218, 119, 86, 0.08);
}

.user-search-item.active {
  background: rgba(218, 119, 86, 0.14);
}

.user-search-name {
  font-size: 14px;
  font-weight: 600;
  flex-shrink: 0;
}

.user-search-item .user-search-email {
  font-size: 12px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-search-role {
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 6px;
  background: rgba(189, 93, 58, 0.15);
  color: #7c3418;
  flex-shrink: 0;
  margin-left: auto;
}

.user-search-empty {
  padding: 16px;
  text-align: center;
  color: var(--text-secondary);
  font-size: 13px;
}

.password-actions {
  display: flex;
  gap: 10px;
  align-items: center;
}

.password-actions .ghost-btn {
  background: transparent;
  border: 1px dashed rgba(44, 32, 19, 0.35);
  padding: 10px 14px;
  border-radius: 12px;
  cursor: pointer;
  box-shadow: none;
  color: var(--text-secondary);
}

.password-actions .ghost-btn:hover:not(:disabled) {
  background: rgba(0, 0, 0, 0.04);
  transform: none;
}

.password-success {
  color: var(--state-success);
  margin: 0;
  font-weight: 600;
}

.password-error-msg {
  color: #b5473d;
  margin: 0;
}
</style>
