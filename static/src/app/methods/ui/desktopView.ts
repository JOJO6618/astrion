// @ts-nocheck
// 桌面多视图：每个内容视图固定绑定一个标签（key 由壳层按 sender 告知），
// 启动后 attach 登记 renderer 身份，此后本文档不再切换到其他标签（切换 = 另一个视图）。
import { useConversationTabsStore } from '../../../stores/conversationTabs';
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
  const control = { disposed: false, retryTimer: 0, shown: false, unsubscribe: null };
  controllers.set(host, control);
  // 重新可见：被暂停的辅助刷新各补一次（任务事件轮询从未停止，只是降频）。
  control.unsubscribe = onViewVisibilityChange((visible) => {
    if (!visible || control.disposed) return;
    void host.fetchStatusSnapshot?.();
    void host.refreshRunningWorkspaceTasks?.();
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
      if (!control.shown) errorToast(host, error);
      control.shown = true;
      control.retryTimer = window.setTimeout(attach, 2000);
    }
  };
  await attach();
}

export function stopDesktopView(host) {
  const control = controllers.get(host);
  if (control) {
    control.disposed = true;
    control.unsubscribe?.();
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
