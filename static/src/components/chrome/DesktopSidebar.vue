<template>
  <div ref="rail" class="desktop-sidebar-rail">
    <ConversationSidebar
      :chat-mode="chatMode"
      :icon-style="sidebarIcon"
      :format-time="formatTime"
      :running-tasks="runningTasks"
      :current-conversation-id="conversationId"
      :current-task-in-progress="tabs.activeTab?.running"
      :current-workspace-id="chatMode ? '' : workspaceId"
      :default-workspace-id="defaultWorkspace"
      :workspaces="taskIndex.hostWorkspaces"
      :host-workspace-enabled="!chatMode"
      :versioning-host-mode="!chatMode"
      :group-by-workspace="grouped"
      :workspace-busy="workspaceBusy"
      :workspace-create-submitting="creatingWorkspace"
      :workspace-error="workspaceError"
      :desktop-browser="browserAvailable ? browserState : undefined"
      @toggle="ui.toggleSidebar()"
      @toggle-browser="toggleBrowser"
      @create="create()"
      @search="updateSearch"
      @search-submit="submitSearch"
      @load-more="loadMore"
      @select-running-task="openRunningTask"
      @select-workspace-conversation="selectConversation"
      @personal="openPersonalPage"
      @delete="deleteConversation"
      @duplicate="duplicateConversation"
      @switch-workspace="switchWorkspace"
      @create-workspace-conversation="create($event, false)"
      @create-workspace="createWorkspace"
      @delete-workspace="deleteWorkspace"
      @set-default-workspace="setDefaultWorkspace"
      @reveal-workspace="revealWorkspace"
      @rename-workspace="renameWorkspace"
      @open-workflows="openPage('/workflows')"
      @open-schedules="openPage('/schedules')"
      @open-git="openGit"
    />
  </div>
  <PersonalizationDrawer />
  <ConfirmDialog />
  <ToastStack />
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { resourceMethods } from '@/app/methods/resources';
import ConversationSidebar from '@/components/sidebar/ConversationSidebar.vue';
import PersonalizationDrawer from '@/components/personalization/PersonalizationDrawer.vue';
import ConfirmDialog from '@/components/shell/ConfirmDialog.vue';
import ToastStack from '@/components/shell/ToastStack.vue';
import { useDesktopUpdateStore } from '@/stores/desktopUpdate';
import { SIDEBAR_COLLAPSED_STORAGE_KEY, useUiStore } from '@/stores/ui';
import type { PersonalDrawerTab } from '@/stores/personalization';
import { iconSrcStyle, type IconKey } from '@/utils/icons';
import { desktopSidebarBridge, hasDesktopBrowserPanel } from './desktopSidebarBridge';
import { useDesktopSidebarActions } from './useDesktopSidebarActions';
import { useDesktopSidebarData } from './useDesktopSidebarData';

const rail = ref<HTMLElement>();
const ui = useUiStore();
try {
  ui.setSidebarCollapsed(localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) !== '0');
} catch {
  /* Keep the default width when storage is unavailable. */
}
const bridge = desktopSidebarBridge();
const data = useDesktopSidebarData();
const {
  tabs,
  personalization,
  taskIndex,
  chatMode,
  workspaceId,
  conversationId,
  defaultWorkspace,
  grouped,
  updateSearch,
  submitSearch,
  loadMore
} = data;
const {
  create,
  switchWorkspace,
  openConversation,
  openRunningTask,
  openPage,
  openGit,
  createWorkspace,
  renameWorkspace,
  setDefaultWorkspace,
  revealWorkspace,
  deleteWorkspace,
  deleteConversation,
  duplicateConversation,
  workspaceError,
  workspaceBusy,
  creatingWorkspace
} = useDesktopSidebarActions(data);
const runningTasks = computed(() =>
  taskIndex.runningWorkspaceTasks.filter((task) =>
    chatMode.value ? !task.workspace_id : Boolean(task.workspace_id)
  )
);
const browserAvailable = hasDesktopBrowserPanel();
const browserPanels = ref<Record<string, boolean>>({});
const browserState = computed(() => ({
  enabled: tabs.activeTab?.kind === 'conv' || tabs.activeTab?.kind === 'new',
  open: Boolean(browserPanels.value[tabs.activeKey])
}));
const sidebarIcon = (key: string) => iconSrcStyle(key as IconKey);
const formatTime = resourceMethods.formatTime;
let resize: ResizeObserver | undefined;
let mutations: MutationObserver | undefined;
let frame = 0;
let overlayRaised = false;
let stopBrowserState: (() => void) | undefined;

function toggleBrowser() {
  if (browserState.value.enabled) bridge?.toggleBrowserPanel?.(tabs.activeKey);
}
function openPersonalPage(tab?: string) {
  void personalization.openDrawer(tab as PersonalDrawerTab | undefined);
}
function selectConversation(target: { conversationId: string; workspaceId: string }) {
  void openConversation(target);
}

// These elements are teleported out of the sidebar. Keep the native content and
// browser underneath them until the closing animation removes the element.
const overlays = [
  '.ws-switcher',
  '.personal-menu',
  '.mode-switcher__menu',
  '.workspace-actions-menu.open',
  '.conversation-actions-menu--fixed',
  '.ui-dialog-scrim',
  '.personal-page-overlay',
  '.update-dialog-overlay'
].join(', ');
function syncOverlay() {
  if (frame) return;
  frame = requestAnimationFrame(() => {
    frame = 0;
    const raised = Array.from(document.querySelectorAll<HTMLElement>(overlays)).some(
      (element) => element.getClientRects().length > 0
    );
    if (raised !== overlayRaised) {
      overlayRaised = raised;
      bridge?.setChromeOverlay?.(raised);
    }
  });
}

onMounted(() => {
  useDesktopUpdateStore().checkStartupUpdate();
  if (rail.value) {
    resize = new ResizeObserver(([entry]) => bridge?.setSidebarWidth?.(entry.contentRect.width));
    resize.observe(rail.value);
  }
  mutations = new MutationObserver(syncOverlay);
  mutations.observe(document.body, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ['class', 'style', 'hidden']
  });
  stopBrowserState = bridge?.onBrowserPanelState?.(({ key, open }) => {
    browserPanels.value[key] = open;
  });
  void bridge
    ?.browserPanels?.()
    .then((states) => {
      browserPanels.value = { ...states, ...browserPanels.value };
    })
    .catch(() => {});
});
onBeforeUnmount(() => {
  resize?.disconnect();
  mutations?.disconnect();
  cancelAnimationFrame(frame);
  if (overlayRaised) bridge?.setChromeOverlay?.(false);
  stopBrowserState?.();
});
</script>
