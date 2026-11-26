// @ts-nocheck
// 桌面多视图：每个内容视图固定绑定一个标签（key 由壳层按 sender 告知），
// 启动后 attach 登记 renderer 身份，此后本文档不再切换到其他标签（切换 = 另一个视图）。
import { useConversationTabsStore } from '../../../stores/conversationTabs';
import { navigationId } from '../../../stores/desktopNavigationTransport';
import { watch } from 'vue';
import { currentConversationSession } from '../conversation/session';
import { t } from '@/locales';
import { onViewVisibilityChange } from './viewVisibility';

const controllers = new WeakMap();

function errorToast(host, error) {
  host.uiPushToast?.({
    title: t('appMessages.loadConversationErrorTitle'),
    message: String(error?.message || error),
    type: 'error'
  });
}

export async function startDesktopView(host) {
  const tabs = useConversationTabsStore();
  if (!tabs.enabled || controllers.has(host)) return;
  const control = {
    disposed: false,
    retryTimer: 0,
    shown: false,
    unsubscribe: null,
    stopRelocationWatch: null
  };
  controllers.set(host, control);
  // 重新可见：被暂停的辅助刷新各补一次（任务事件轮询从未停止，只是降频）。
  control.unsubscribe = onViewVisibilityChange((visible) => {
    if (!visible || control.disposed) return;
    void host.fetchStatusSnapshot?.();
    void host.refreshRunningWorkspaceTasks?.();
    void host.refreshConversationListSilently?.('visible');
    void host.reconcileRunningStateOnce?.();
    void host.refreshProjectGitSummary?.({ idleFallback: true });
    void host.fetchTerminalCount?.();
  });
  const attach = async () => {
    if (control.disposed) return;
    try {
      await tabs.hydrate();
      // legacy 兜底页（无 key）在 hydrate 内被降级为网页行为。
      if (control.disposed || !tabs.enabled || !tabs.viewKey) return;
      await tabs.attach();
    } catch (error) {
      if (control.disposed) return;
      // 标签尚未出现在后端快照或网络瞬断：静默重试，只提示一次。
      // 401 = 会话尚未建立（桌面免密登录还在进行），属于启动过程，不提示。
      if (!control.shown && error?.status !== 401) errorToast(host, error);
      control.shown = true;
      control.retryTimer = window.setTimeout(attach, 2000);
    }
  };
  control.stopRelocationWatch = watch(
    () => tabs.viewTab,
    (next, previous) => {
      if (
        tabs.attached &&
        next?.kind === 'conv' &&
        previous?.kind === 'conv' &&
        next.key === previous.key &&
        next.conversationId === previous.conversationId &&
        next.workspaceId !== previous.workspaceId &&
        !tabs.renderers[next.key]
      ) {
        window.clearTimeout(control.retryTimer);
        tabs.localRendererId = navigationId();
        tabs.pendingFail = '';
        void attach();
      }
    },
    { flush: 'post' }
  );
  await attach();
}

export function stopDesktopView(host) {
  const control = controllers.get(host);
  if (control) {
    control.disposed = true;
    control.unsubscribe?.();
    control.stopRelocationWatch?.();
    if (control.retryTimer) window.clearTimeout(control.retryTimer);
    controllers.delete(host);
  }
}

/** 本视图为空白新对话标签时，发送前捕获其 key 用于首条消息后的 bind。 */
export function desktopCreationTab(host) {
  const tabs = useConversationTabsStore();
  if (!tabs.enabled) return null;
  const tab = tabs.viewTab;
  const session = currentConversationSession(host);
  const workspaceId = session ? session.workspaceId : String(host.currentHostWorkspaceId || '');
  if (!tab || tab.kind !== 'new' || tab.workspaceId !== workspaceId) return null;
  return { key: tab.key, workspaceId: tab.workspaceId };
}
