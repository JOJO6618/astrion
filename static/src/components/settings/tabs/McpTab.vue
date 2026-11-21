<script setup lang="ts">
import { computed, ref, shallowRef } from 'vue';
import UiIconButton from '@/components/ui/UiIconButton.vue';
import UiMenu from '@/components/ui/UiMenu.vue';
import UiSelect from '@/components/ui/UiSelect.vue';
import { t } from '@/locales';
import { useUiStore } from '@/stores/ui';
import McpAccessList from './mcp/McpAccessList.vue';
import McpServerEditor from './mcp/McpServerEditor.vue';
import { MCP_ACTORS, useMcpSettings } from './mcp/useMcpSettings';
import type { McpActor, McpServerConfig, McpServerSummary } from './mcp/useMcpSettings';

const ui = useUiStore();
const {
  settings,
  workspaceId,
  loading,
  loadError,
  accessError,
  load,
  retryAccess,
  setDefault,
  setWorkspace,
  refreshServices,
  saveServer,
  getServer,
  deleteServer,
  syncServers
} = useMcpSettings();
const workspace = computed(() =>
  settings.value?.workspaces.find((item) => item.id === workspaceId.value)
);
const workspaceOptions = computed(
  () =>
    settings.value?.workspaces.map((item) => ({
      value: item.id,
      label: item.label
    })) ?? []
);
const serverBusy = ref(false);
const editorOpen = ref(false);
const editorError = ref('');
const editorConfig = ref<McpServerConfig | null>(null);
const menuServer = ref<McpServerSummary>();
const menuAnchor = shallowRef<HTMLElement>();
const menuItems = computed(() => [
  { value: 'edit', label: t('settings.mcp.edit'), icon: 'pencil' },
  {
    value: 'toggle',
    label: t(menuServer.value?.enabled ? 'settings.mcp.disable' : 'settings.mcp.enable'),
    icon: menuServer.value?.enabled ? 'circle-minus' : undefined
  },
  {
    value: 'sync',
    label: t('settings.mcp.sync'),
    icon: 'refresh-cw',
    disabled: !menuServer.value?.enabled || !settings.value?.bridge_enabled
  },
  { value: 'delete', label: t('common.delete'), icon: 'trash', danger: true }
]);

function actorTitle(actor: McpActor) {
  return t(actor === 'main' ? 'settings.mcp.mainAgent' : 'settings.mcp.subAgents');
}

function actorDescription(actor: McpActor) {
  return t(actor === 'main' ? 'settings.mcp.mainDesc' : 'settings.mcp.subDesc');
}

function workspaceIds(actor: McpActor) {
  const entry = workspace.value?.access[actor];
  return entry && !entry.inherit ? entry.server_ids : (settings.value?.defaults[actor] ?? []);
}

function changeWorkspaceMode(actor: McpActor, mode: 'inherit' | 'custom') {
  const previous = workspace.value?.access[actor];
  setWorkspace(actor, {
    inherit: mode === 'inherit',
    server_ids: previous?.server_ids ?? workspaceIds(actor)
  });
}

function openAdd() {
  editorError.value = '';
  editorConfig.value = null;
  editorOpen.value = true;
}

function toggleMenu(server: McpServerSummary, event: MouseEvent) {
  if (menuServer.value?.id === server.id) {
    closeMenu();
    return;
  }
  menuAnchor.value = event.currentTarget as HTMLElement;
  menuServer.value = server;
}

function closeMenu(restoreFocus = false) {
  const anchor = menuAnchor.value;
  menuServer.value = undefined;
  menuAnchor.value = undefined;
  if (restoreFocus) anchor?.focus({ preventScroll: true });
}

async function runServerAction(action: () => Promise<void>) {
  serverBusy.value = true;
  try {
    await action();
  } catch (error) {
    ui.pushToast({ type: 'error', message: (error as Error).message });
  } finally {
    serverBusy.value = false;
  }
}

async function saveEditor(config: Partial<McpServerConfig>) {
  await runServerAction(async () => {
    editorError.value = '';
    try {
      await saveServer(config, editorConfig.value?.id);
    } catch (error) {
      editorError.value = (error as Error).message;
      return;
    }
    editorOpen.value = false;
    await refreshServices();
  });
}

async function sync(id?: string) {
  await runServerAction(async () => {
    try {
      await syncServers(id);
    } finally {
      await refreshServices();
    }
  });
}

async function selectAction(action: string) {
  const server = menuServer.value;
  if (!server) return;
  if (action === 'sync') {
    await sync(server.id);
    return;
  }
  if (action === 'delete') {
    const ok = await ui.requestConfirm({
      title: t('settings.mcp.deleteTitle'),
      message: t('settings.mcp.deleteMessage', { name: server.name }),
      confirmText: t('common.delete'),
      confirmVariant: 'danger'
    });
    if (!ok) return;
  }
  await runServerAction(async () => {
    if (action === 'edit') {
      editorConfig.value = await getServer(server.id);
      editorError.value = '';
      editorOpen.value = true;
    } else {
      if (action === 'toggle') await saveServer({ enabled: !server.enabled }, server.id);
      if (action === 'delete') await deleteServer(server.id);
      await refreshServices();
    }
  });
}
</script>

<template>
  <section class="stg-page">
    <div v-if="loadError" class="stg-card">
      <div class="stg-action-row" role="alert">
        <span class="stg-row-title">{{ loadError }}</span>
        <button type="button" class="stg-secondary-button" @click="load">
          {{ $t('common.retry') }}
        </button>
      </div>
    </div>
    <p v-else-if="loading" class="stg-card-empty" role="status">{{ $t('common.loading') }}</p>
    <template v-else-if="settings">
      <div class="stg-card">
        <div class="stg-action-row">
          <span class="stg-row-copy">
            <span class="stg-row-title">{{ $t('settings.mcp.services') }}</span>
            <span class="stg-row-desc">{{
              $t(
                settings.bridge_enabled
                  ? 'settings.mcp.servicesDesc'
                  : 'settings.mcp.bridgeDisabled'
              )
            }}</span>
          </span>
          <button
            type="button"
            class="stg-secondary-button"
            :disabled="serverBusy"
            @click="openAdd"
          >
            {{ $t('settings.mcp.add') }}
          </button>
        </div>
        <div v-for="server in settings.servers" :key="server.id" class="stg-action-row">
          <span class="stg-row-copy">
            <span class="stg-row-title">{{ server.name }}</span>
            <span class="stg-row-desc">
              {{ $t(server.transport === 'stdio' ? 'settings.mcp.stdio' : 'settings.mcp.http') }} ·
              {{
                !server.enabled
                  ? $t('settings.mcp.globallyDisabled')
                  : server.last_error
                    ? $t('settings.mcp.syncFailed')
                    : server.tools_cache_updated_at
                      ? $t('settings.mcp.toolCount', { count: server.tools_cache_count })
                      : $t('settings.mcp.notSynced')
              }}
            </span>
            <span v-if="server.last_error" class="stg-row-desc mcp-service-error">{{
              server.last_error
            }}</span>
          </span>
          <UiIconButton
            icon="ellipsis"
            :label="$t('settings.mcp.actions', { name: server.name })"
            :disabled="serverBusy"
            :aria-expanded="menuServer?.id === server.id"
            aria-haspopup="menu"
            @click="toggleMenu(server, $event)"
          />
        </div>
        <div v-if="settings.servers.some((server) => server.enabled)" class="stg-action-row">
          <span class="stg-row-copy">
            <span class="stg-row-title">{{ $t('settings.mcp.syncAll') }}</span>
            <span class="stg-row-desc">{{ $t('settings.mcp.syncAllDesc') }}</span>
          </span>
          <button
            type="button"
            class="stg-secondary-button"
            :disabled="serverBusy || !settings.bridge_enabled"
            @click="sync()"
          >
            {{ $t('settings.mcp.sync') }}
          </button>
        </div>
      </div>

      <div v-if="settings.servers.length" class="stg-section-divider">
        <span class="stg-section-divider__label">{{ $t('settings.mcp.defaults') }}</span>
      </div>
      <div v-for="actor in settings.servers.length ? MCP_ACTORS : []" :key="actor" class="stg-card">
        <McpAccessList
          :title="actorTitle(actor)"
          :description="actorDescription(actor)"
          :servers="settings.servers"
          :server-ids="settings.defaults[actor]"
          @selection="setDefault(actor, $event)"
        />
      </div>

      <template v-if="settings.servers.length && settings.workspaces.length">
        <div class="stg-section-divider">
          <span class="stg-section-divider__label">{{ $t('settings.mcp.workspace') }}</span>
        </div>
        <div class="stg-card">
          <div class="stg-select-row">
            <span class="stg-row-copy">
              <span class="stg-row-title">{{ $t('settings.mcp.workspace') }}</span>
              <span class="stg-row-desc">{{ $t('settings.mcp.workspaceDesc') }}</span>
            </span>
            <UiSelect
              v-model="workspaceId"
              :options="workspaceOptions"
              :label="$t('settings.mcp.workspace')"
              align-end
            />
          </div>
          <McpAccessList
            v-for="actor in MCP_ACTORS"
            :key="actor"
            :title="actorTitle(actor)"
            :description="actorDescription(actor)"
            :servers="settings.servers"
            :server-ids="workspaceIds(actor)"
            :mode="workspace?.access[actor]?.inherit === false ? 'custom' : 'inherit'"
            @selection="setWorkspace(actor, { inherit: false, server_ids: $event })"
            @mode="changeWorkspaceMode(actor, $event)"
          />
        </div>
      </template>

      <div v-if="accessError" class="stg-card">
        <div class="stg-action-row" role="alert">
          <span class="stg-row-title">{{ accessError }}</span>
          <button type="button" class="stg-secondary-button" @click="retryAccess">
            {{ $t('common.retry') }}
          </button>
        </div>
      </div>
    </template>

    <UiMenu
      :open="!!menuServer"
      :anchor="menuAnchor"
      :items="menuItems"
      actions
      align-end
      @select="selectAction"
      @close="closeMenu"
    />
    <McpServerEditor
      :open="editorOpen"
      :config="editorConfig"
      :busy="serverBusy"
      :error="editorError"
      @close="editorOpen = false"
      @save="saveEditor"
    />
  </section>
</template>

<style scoped>
.mcp-service-error {
  color: var(--state-danger);
}
</style>
