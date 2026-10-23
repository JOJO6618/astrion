const { compile, deferred, noop, locale } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');
const base = 'static/src/app/methods/';
const flush = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };

function fixture({ realSnapshot = true, automaticSelection = false, desktopTabs = null } = {}) {
  const requests = [], attachments = [], toasts = [], ticks = [], calls = [], openedTabs = [];
  const fetch = (url, options = {}) => {
    const pending = deferred(); requests.push({ url, options, ...pending });
    if (automaticSelection && url.includes('/select?')) pending.resolve({ ok: true, json: async () => ({
      success: true, data: { current_workspace_id: new URL(url, 'http://localhost').searchParams.get('workspace_id') }
    }) });
    return pending.promise;
  };
  const globals = { fetch, URLSearchParams, window: { setTimeout: noop },
    history: { pushState: noop, replaceState: noop }, requestAnimationFrame: noop };
  const session = compile(base + 'conversation/session.ts');
  const caching = compile('static/src/stores/workspaceConversationCache.ts', {}, globals);
  const store = { conversationsByWorkspace: {}, conversationsCache: {}, conversationCacheWorkspaceId: '',
    workspaceGroups: [], conversationsOffset: 0, hasMoreConversations: false,
    $patch(value) { Object.assign(this, value); },
    setCurrentConversationId(id) { this.currentConversationId = id; },
    ...caching.workspaceCacheActions };
  store.activateWorkspaceCache('ws1');
  const task = { clearTask: () => calls.push('clearTask'), attachSnapshot: (...args) => attachments.push(args) };
  const tabs = desktopTabs || { enabled: false };
  const selection = compile(base + 'ui/workspaceSelection.ts', {}, globals);
  const refresh = compile(base + 'ui/navigationRefresh.ts', {
    '../conversation/session': session,
  }, globals);
  const newConversation = compile(base + 'ui/newConversation.ts', {
    '@/locales': locale, '../conversation/session': session,
  }, globals);
  const imports = {
    './newConversation': newConversation,
    '@/locales': locale, '../../../stores/conversation': { useConversationStore: () => store },
    '../../../stores/conversationTabs': { useConversationTabsStore: () => tabs },
    '../../../stores/task': { useTaskStore: () => task }, '../conversation/session': session,
    './workspaceSelection': selection, './navigationRefresh': refresh,
  };
  const navigation = compile(base + 'ui/workspaceNavigation.ts', imports, globals);
  const drafts = compile(base + 'ui/composerDrafts.ts', {
    '@/locales': locale, '../conversation/session': session,
  }, globals);
  const host = {
    ...navigation.workspaceNavigationMethods, ...drafts.composerDraftMethods,
    ...newConversation.newConversationMethods,
    composerDraftOwner: 'synthetic-owner',
    versioningHostMode: true, currentHostWorkspaceId: 'ws1', currentConversationId: 'A',
    hostWorkspaces: [{ workspace_id: 'ws1', label: 'one' }, { workspace_id: 'ws2', label: 'two' },
      { workspace_id: 'ws3', label: 'three' }], runningWorkspaceTasks: [], conversations: [], inputMessage: '',
    preparingTools: new Map(), imageEntries: [], videoEntries: [], messages: [], titleReady: true,
    flushReasoningEffortSave: noop, getInputComposerRef: () => ({ getComposerDraftMeta: () => ({}),
      restoreComposerDraftMeta: () => calls.push('draft-meta') }),
    inputSetMessage(value) { this.inputMessage = value; }, inputClearMessage() { this.inputMessage = ''; },
    $nextTick: (fn) => ticks.push(fn), uiPushToast: (toast) => toasts.push(toast),
    clearLocalTaskUiState: noop, startTitleTyping: noop, modelSet: noop, autoResizeInput: noop,
    applyRuntimeQueuedMessages: noop, handleCompressionState: noop, resetTokenStatistics: noop,
    promoteConversationToTop: noop, refreshBlankHeroState: noop, startRunningStateReconcile: noop,
    stopRunningStateReconcile: noop, scrollHistoryToBottomInstant: noop, conditionalScrollToBottom: noop,
    cloneToolArguments: (value) => ({ ...value }), buildToolLabel: () => 'label', toolRegisterAction: noop,
    toolTrackAction: noop, chatSetThinkingLock: noop, logMessageState: noop,
    stripConversationPrefix: (id) => id.replace(/^conv_/, ''), applyConversationTitleUpdate: noop,
    loadConversationsList: async () => calls.push('list'),
    refreshRunningWorkspaceTasks: async () => calls.push('tasks'),
    subAgentFetch: () => calls.push('subAgent'), fetchTerminalCount: () => calls.push('terminals'),
    fetchConversationTokenStatistics: () => calls.push('tokens'), updateCurrentContextTokens: () => calls.push('context'),
    fetchVersioningStatus: (id) => calls.push(`versioning:${id}`), refreshProjectGitSummary: () => calls.push('git'),
    getVisibleWorkspaceTaskForConversation: () => null, acknowledgeCompletedWorkspaceTask: noop,
  };
  if (realSnapshot) {
    const display = compile(base + 'conversation/display.ts');
    const approval = compile('static/src/components/input/approvalModel.ts');
    const history = compile(base + 'history.ts', { './common': { debugLog: noop },
      './conversation/display': display, '@/locales': locale });
    const bootstrap = compile(base + 'conversation/bootstrap.ts', {
      './display': display, './session': session, '../common': { debugLog: noop },
      '../ui/shared': { parseSystemNoticeLabel: (value) => value },
      '../auxiliaryOwnership': { bindAuxiliaryConversationHost: noop },
      '../ui/workspaceNavigation': navigation, '../../../stores/task': { useTaskStore: () => task },
      '../../../stores/conversation': { useConversationStore: () => store },
      '../../../stores/conversationTabs': { useConversationTabsStore: () => tabs },
      '../../../stores/file': { useFileStore: () => ({ setTodoList: noop }) },
      '../../../stores/resource': { useResourceStore: () => ({ setCurrentContextTokens: noop }) },
      '../../../stores/quickDock': { useQuickDockStore: () => ({ setEditedFiles: noop }) },
      '../../../stores/preview': { usePreviewStore: () => ({ setRuntime: noop, setTargets: noop }) },
      '../../../stores/workflow': { useWorkflowStore: () => ({ setWorkflow: noop }) },
      '../../../stores/personalization': { usePersonalizationStore: () => ({ form: {} }) },
      '@/components/input/approvalModel': approval,
    }, globals);
    Object.assign(host, history.historyMethods, bootstrap.bootstrapMethods);
  } else {
    host.beginConversationView = (id, ws) => {
      host.historyLoading = true;
      return session.beginConversationSession(host, id, ws);
    };
    host.leaveConversationView = () => session.leaveConversationSession(host);
  }
  session.beginConversationSession(host, 'A', 'ws1');
  return { host, requests, attachments, toasts, ticks, calls, openedTabs, store, session, selection, caching, navigation, fetch };
}
function respond(request, body, ok = true) { request.resolve({ ok, json: async () => body }); }
function bootstrap(id, workspaceId) {
  return { success: true, data: { conversation_id: id, workspace_id: workspaceId,
    workspace: { project_path: '/' + workspaceId }, meta: { title: 'T:' + id, work_mode: 'execute' },
    messages: [{ role: 'user', message_id: id + '-u', content: 'history:' + id }],
    display: { messages: [], state: {}, task: { task_id: 'T-' + id, status: 'running', task_type: 'chat' },
      next_event_idx: 512 }, running: { is_main_running: true, is_truly_active: true } } };
}
async function drain(f, target = {}) {
  const seen = new Set();
  f.remoteDrafts ||= new Map();
  for (let turn = 0; turn < 20; turn++) {
    await flush();
    for (const request of f.requests) {
      if (seen.has(request)) continue;
      seen.add(request);
      const url = new URL(request.url, 'http://localhost');
      if (url.pathname === '/api/input-draft') {
        if (request.options.method === 'POST') {
          const body = JSON.parse(request.options.body);
          f.remoteDrafts.set(request.url, { ...body, revision: (body.base_revision || 0) + 1 });
          respond(request, { success: true, data: { saved: true, revision: (body.base_revision || 0) + 1 } });
        } else respond(request, { success: true, data: f.remoteDrafts.get(request.url) || {
          content: 'draft:' + url.searchParams.get('workspace_id'), revision: 0 } });
      }
      else if (url.pathname.endsWith('/select')) respond(request, { success: true, data: {
        current_workspace_id: url.searchParams.get('workspace_id') } });
      else if (url.pathname.endsWith('/bootstrap')) {
        const id = url.pathname.split('/')[3];
        if (target[id] === null) continue;
        respond(request, target[id] || bootstrap(id, url.searchParams.get('workspace_id')));
      } else if (url.pathname === '/api/workspace-view') respond(request, { success: true,
        data: target.workspaceView || { modes: { work_mode: 'execute' }, reasoning_effort: 'high' } });
      else if (url.pathname === '/api/new-conversation/settings') respond(request, { success: true,
        data: { settings: { model_key: 'synthetic', run_mode: 'thinking', thinking_mode: true,
          reasoning_effort: 'high', work_mode: 'execute', permission_mode: 'unrestricted',
          execution_mode: 'sandbox', network_permission: 'restricted', restore_modes: {} } } });
      else respond(request, { success: true, data: {}, snapshot: null });
    }
  }
}
module.exports = { fixture, flush, respond, bootstrap, drain };
