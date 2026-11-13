// @ts-nocheck
// Only this coordinator consumes desktop targets. The existing snapshot remains the display loader.
import { watch } from 'vue';
import { useConversationTabsStore } from '../../../stores/conversationTabs';
import { NavigationError } from '../../../stores/desktopNavigationTransport';
import { currentConversationSession } from '../conversation/session';
import { t } from '@/locales';

const controllers = new WeakMap();

function pathFor(tab) {
  if (tab.conversationKind === 'chat')
    return tab.kind === 'conv' ? `/chat/${tab.conversationId.replace(/^conv_/, '')}` : '/chat/new';
  const path = tab.kind === 'conv' ? `/${tab.conversationId.replace(/^conv_/, '')}` : '/new';
  return `${path}?workspace_id=${encodeURIComponent(tab.workspaceId)}`;
}

function errorToast(host, error) {
  host.uiPushToast?.({
    title: t('appMessages.loadConversationErrorTitle'),
    message: String(error?.message || error),
    type: 'error'
  });
}

async function applyTarget(host, control) {
  const tabs = useConversationTabsStore();
  const nav = tabs.navigation;
  if (
    control.disposed ||
    !tabs.attached ||
    tabs.rendererId !== tabs.localRendererId ||
    !nav ||
    nav.id === control.handled
  )
    return;
  control.handled = nav.id;
  const owner = { navigationId: nav.id, rendererId: tabs.localRendererId, key: nav.key };
  const owns = () => !control.disposed && tabs.ownsNavigation(owner);
  if (nav.status === 'error') return;
  const tab = tabs.tabs.find((item) => item.key === nav.key);
  if (nav.page) {
    host.leaveConversationView();
    const current = window.location.pathname + window.location.search;
    if (current !== nav.page) {
      void host
        .persistComposerDraftNow?.({ reason: 'desktop-page', keepalive: true })
        .catch(() => {});
      if (owns()) window.location.assign(nav.page);
    } else if (nav.status !== 'committed') {
      await tabs.commitNavigation(owner);
    }
    return;
  }
  if (!tab) return;
  if (tab.conversationKind === 'chat' || host.isChatMode) {
    if (tab.conversationKind === 'chat' && host.isChatMode) {
      await host.handleChatNavigation(tab, owner);
    } else if (owns()) {
      host.leaveConversationView();
      window.location.assign(pathFor(tab));
    }
    return;
  }
  if (host.isConversationIndependentRoute?.()) {
    host.leaveConversationView();
    if (owns()) window.location.assign(pathFor(tab));
    return;
  }
  if (!tab.workspaceId) {
    // 零工作区标签（新建或工作区已被删空）：没有可展示的执行身份，
    // 回空对话引导页，不走工作区切换与失败标记。
    host.leaveConversationView();
    if (owns() && window.location.pathname !== '/new') window.location.assign('/new');
    return;
  }
  // A deleted/closed visible tab must not remain sendable while its successor loads.
  const visible = tabs.tabs.find((item) => item.key === tabs.activeKey);
  if (!visible) host.historyLoading = true;
  try {
    // 目标空视图已提交且当前正在显示（模式切回 / 刷新）：无需重建空页，也不再重复 commit。
    if (
      nav.status === 'committed' &&
      tabs.activeKey === tab.key &&
      tab.kind === 'new' &&
      tab.conversationKind === 'agent' &&
      !host.historyLoading &&
      !host.currentConversationId &&
      String(host.currentHostWorkspaceId || '') === tab.workspaceId
    )
      return;
    if (tab.kind === 'conv') {
      const result = await host.handleSelectWorkspaceConversation(
        {
          conversationId: tab.conversationId,
          workspaceId: tab.workspaceId
        },
        { force: true, desktopNavigation: owner }
      );
      if (!owns() || result?.superseded) return;
      if (!result?.success) throw new Error(result?.error || t('appUi.switchFailed'));
    } else {
      await host.handleHostWorkspaceSwitch(tab.workspaceId, {
        newConversation: true,
        desktopNavigation: owner
      });
    }
  } catch (error) {
    if (!owns()) return;
    await tabs
      .command('fail', {
        renderer_id: owner.rendererId,
        navigation_id: owner.navigationId,
        error: String(error?.message || error)
      })
      .catch(() => {});
    if (!owns()) return;
    // Rebind the previous visible conversation without reopening or mutating its tab.
    const active = tabs.activeTab;
    if (active?.kind === 'conv') {
      await host
        .handleSelectWorkspaceConversation(
          { conversationId: active.conversationId, workspaceId: active.workspaceId },
          { force: true, desktopRecovery: true }
        )
        .catch(() => {});
    } else if (active?.kind === 'new') {
      await host
        .handleHostWorkspaceSwitch(active.workspaceId, {
          newConversation: true,
          desktopRecovery: true
        })
        .catch(() => {});
    } else {
      host.leaveConversationView();
      if (!active) {
        host.currentConversationId = null;
        host.messages = [];
      }
    }
    if (owns()) errorToast(host, error);
  }
}

export async function startDesktopNavigation(host) {
  const tabs = useConversationTabsStore();
  if (!tabs.enabled || controllers.has(host)) return;
  const control = {
    disposed: false,
    handled: '',
    stop: null,
    retryTimer: 0,
    attachErrorShown: false,
    // 零工作区（新装 host）：标签导航依赖工作区身份，此时不发任何命令，
    // 避免标签条常驻「同步失败」，等工作区就绪再由 watch 重新启动。
    awaitingWorkspace: false,
    workspaceStop: null
  };
  controllers.set(host, control);
  // 判断零工作区只能用导航快照（host.currentHostWorkspaceId 在设置页等独立路由上
  // 恒为空，但实际有工作区）。快照里的 workspace_id 由后端维护。
  const workspaceReady = () =>
    Boolean(
      host.isChatMode || host.currentHostWorkspaceId || useConversationTabsStore().workspaceId
    );
  const observe = () => {
    void applyTarget(host, control).catch((error) => {
      if (!control.disposed) errorToast(host, error);
    });
  };
  // Pinia patches nested fields individually. Consume only after the complete patch,
  // otherwise a new navigation id can still be paired with the previous target key.
  control.stop = watch(() => [tabs.navigation?.id, tabs.rendererId], observe, { flush: 'post' });
  const attach = async () => {
    if (control.disposed) return;
    try {
      await tabs.hydrate();
      if (control.disposed) return;
      const page = host.isConversationIndependentRoute?.()
        ? window.location.pathname + window.location.search
        : '';
      await tabs.attach({
        workspace_id: host.isChatMode ? '' : host.currentHostWorkspaceId || tabs.workspaceId || '',
        conversation_kind: host.isChatMode ? 'chat' : 'agent',
        conversation_id: page ? '' : host.currentConversationId || '',
        page
      });
      if (control.disposed) return;
      // Explicit URL bootstrap already committed this view; do not load it twice.
      const active = tabs.activeTab;
      if (
        tabs.navigation?.status === 'committed' &&
        (page ||
          (active?.conversationId === host.currentConversationId &&
            active?.workspaceId === host.currentHostWorkspaceId))
      ) {
        control.handled = tabs.navigation.id;
      }
      observe();
    } catch (error) {
      if (control.disposed) return;
      // 零工作区是正常空态（新装 host）：不弹错、不显示同步失败，
      // 只标记等待工作区，创建后由下方 watch 重新 attach。
      if (error instanceof NavigationError && error.code === 'no_workspace') {
        control.awaitingWorkspace = true;
        return;
      }
      control.awaitingWorkspace = false;
      if (!control.attachErrorShown) errorToast(host, error);
      control.attachErrorShown = true;
      control.retryTimer = window.setTimeout(attach, 2000);
    }
  };
  control.workspaceStop = watch(
    () => [Boolean(host.isChatMode), String(host.currentHostWorkspaceId || ''), tabs.workspaceId],
    () => {
      if (control.disposed || !control.awaitingWorkspace || !workspaceReady()) return;
      control.awaitingWorkspace = false;
      void attach()
        .then(() => {
          if (!control.disposed) tabs.startPolling();
        })
        .catch(() => {});
    },
    { flush: 'post' }
  );
  await attach();
  if (!control.disposed && !control.awaitingWorkspace) tabs.startPolling();
}

export function stopDesktopNavigation(host) {
  const control = controllers.get(host);
  if (control) {
    control.disposed = true;
    control.stop?.();
    control.workspaceStop?.();
    if (control.retryTimer) window.clearTimeout(control.retryTimer);
    controllers.delete(host);
  }
  useConversationTabsStore().stopPolling();
}

export function desktopCreationTab(host) {
  const tabs = useConversationTabsStore();
  if (!tabs.enabled) return null;
  const tab = tabs.activeTab;
  const session = currentConversationSession(host);
  if (!tab || tab.kind !== 'new' || tab.workspaceId !== session?.workspaceId) return null;
  return { key: tab.key, workspaceId: tab.workspaceId };
}
