// @ts-nocheck
// Only this coordinator consumes desktop targets. The existing snapshot remains the display loader.
import { watch } from 'vue';
import { useConversationTabsStore } from '../../../stores/conversationTabs';
import { currentConversationSession } from '../conversation/session';
import { t } from '@/locales';

const controllers = new WeakMap();

function pathFor(tab) {
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
  if (host.isConversationIndependentRoute?.()) {
    host.leaveConversationView();
    if (owns()) window.location.assign(pathFor(tab));
    return;
  }
  // A deleted/closed visible tab must not remain sendable while its successor loads.
  const visible = tabs.tabs.find((item) => item.key === tabs.activeKey);
  if (!visible) host.historyLoading = true;
  try {
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
    attachErrorShown: false
  };
  controllers.set(host, control);
  const observe = () => {
    void applyTarget(host, control).catch((error) => {
      if (!control.disposed) errorToast(host, error);
    });
  };
  // Pinia patches nested fields individually. Consume only after the complete patch,
  // otherwise a new navigation id can still be paired with the previous target key.
  control.stop = watch(() => [tabs.navigation?.id, tabs.rendererId], observe, { flush: 'post' });
  const attach = async () => {
    try {
      await tabs.hydrate();
      if (control.disposed) return;
      const page = host.isConversationIndependentRoute?.()
        ? window.location.pathname + window.location.search
        : '';
      await tabs.attach({
        workspace_id: host.currentHostWorkspaceId || '',
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
      if (!control.disposed) {
        if (!control.attachErrorShown) errorToast(host, error);
        control.attachErrorShown = true;
        control.retryTimer = window.setTimeout(attach, 2000);
      }
    }
  };
  await attach();
  if (!control.disposed) tabs.startPolling();
}

export function stopDesktopNavigation(host) {
  const control = controllers.get(host);
  if (control) {
    control.disposed = true;
    control.stop?.();
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
