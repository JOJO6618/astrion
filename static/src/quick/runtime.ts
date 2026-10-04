import { ref, computed } from 'vue';
import { t } from '@/locales';
import { saveDraft, loadDraft } from './drafts';

export interface QuickBridge {
  request(route: string, method?: string, body?: any, workspace?: string): Promise<any>;
  info(): Promise<any>;
  configure(patch: any): Promise<any>;
  hide(): void;
  hidden(): void;
  layout(
    regions: { x: number; y: number; width: number; height: number }[],
    presentation?: { label: string; failure: string; colors: Record<string, string> }
  ): void;
  capturePermission(): Promise<string>;
  onCapture(callback: (url: string) => void): () => void;
  onCaptureError(callback: (message: string) => void): () => void;
  onShow(callback: () => void): () => void;
  onHide(callback: () => void): () => void;
  onPointer(callback: (point: { x: number; y: number }) => void): () => void;
}
declare global {
  interface Window {
    astrionQuick: QuickBridge;
  }
}

export function useQuickRuntime() {
  const bridge = window.astrionQuick;
  const draft = ref(''),
    images = ref<string[]>([]),
    workspace = ref(''),
    model = ref('');
  const conversation = ref(''),
    task = ref(''),
    running = ref(false),
    sending = ref(false);
  const reply = ref(''),
    tools = ref<any[]>([]),
    error = ref('');
  const workspaces = ref<any[]>([]),
    models = ref<any[]>([]),
    sessions = ref<any[]>([]);
  const config = ref<any>({}),
    approvals = ref<any[]>([]);
  const collapsed = ref(false),
    streaming = ref(false),
    thinking = ref(false);
  let offset = 0,
    pollTimer: ReturnType<typeof setTimeout> | undefined,
    generation = 0;
  let presentation = Promise.resolve();
  let finishedAt = 0;
  let hadGap = false;
  let preferencesPromise: Promise<void> | undefined;
  let initialized = false;
  function resetPresentation() {
    generation++;
    presentation = Promise.resolve();
    finishedAt = 0;
    hadGap = false;
    thinking.value = false;
    clearTimeout(pollTimer);
  }
  const request = (route: string, method = 'GET', body?: any) =>
    bridge.request(route, method, body, workspace.value);
  const storageKey = () => `astrion-quick:${workspace.value}`;
  const save = () =>
    saveDraft(storageKey(), {
      conversation: conversation.value,
      task: task.value,
      draft: draft.value,
      images: [...images.value]
    }).catch((exception) => {
      error.value = String(exception);
    });
  const modelLabel = computed(
    () => models.value.find((item) => item.model_key === model.value)?.name || t('quickEntry.model')
  );
  const workspaceLabel = computed(
    () =>
      workspaces.value.find((item) => item.workspace_id === workspace.value)?.label ||
      t('quickEntry.workspace')
  );
  async function initialize() {
    error.value = '';
    try {
      await syncPreferences();
    } catch (exception) {
      error.value = String(exception);
    }
  }
  function syncPreferences(): Promise<void> {
    // Initial mount and a summon can overlap; commit a single preferences load.
    preferencesPromise ||= resolvePreferences().finally(() => {
      preferencesPromise = undefined;
    });
    return preferencesPromise;
  }
  async function resolvePreferences() {
    const ticket = generation;
    const [nextConfig, catalog] = await Promise.all([
      bridge.info(),
      bridge.request('/api/host/workspaces')
    ]);
    const availableWorkspaces = catalog.data?.workspaces || [];
    const desiredWorkspace = nextConfig.workspace || catalog.data?.default_workspace_id || '';
    const targetWorkspace = availableWorkspaces.some(
      (item: any) => item.workspace_id === desiredWorkspace
    )
      ? desiredWorkspace
      : '';
    const [modelResult, personal] = await Promise.all([
      bridge.request('/api/v1/models', 'GET', undefined, targetWorkspace),
      bridge.request('/api/personalization', 'GET', undefined, targetWorkspace)
    ]);
    if (ticket !== generation) return;
    const settings = personal.personalization || personal.data || personal;
    const hidden = new Set(settings.hidden_models || []);
    const availableModels = (modelResult.items || []).filter(
      (item: any) =>
        item.visible !== false &&
        !hidden.has(item.model_key) &&
        ['image', 'image,video'].includes(item.multimodal)
    );
    const desiredModel = nextConfig.model || settings.default_model || '';
    const changeWorkspace = !initialized || workspace.value !== targetWorkspace;
    if (changeWorkspace && initialized && workspace.value) await save();
    if (ticket !== generation) return;
    config.value = nextConfig;
    workspaces.value = availableWorkspaces;
    models.value = availableModels;
    model.value = availableModels.some((item: any) => item.model_key === desiredModel)
      ? desiredModel
      : '';
    if (settings.theme) {
      document.documentElement.dataset.theme = settings.theme;
      document.body.dataset.theme = settings.theme;
    }
    if (changeWorkspace) {
      workspace.value = targetWorkspace;
      sessions.value = [];
      if (targetWorkspace) await restoreWorkspace();
      else {
        resetPresentation();
        conversation.value = '';
        task.value = '';
        running.value = false;
        draft.value = '';
        images.value = [];
        reply.value = '';
        tools.value = [];
        approvals.value = [];
      }
    }
    initialized = true;
    if (desiredWorkspace && !targetWorkspace) error.value = t('quickEntry.workspaceUnavailable');
    else if (!model.value) error.value = t('quickEntry.modelUnavailable');
    else if (
      [t('quickEntry.workspaceUnavailable'), t('quickEntry.modelUnavailable')].includes(error.value)
    )
      error.value = '';
  }
  async function loadSessions() {
    const ticket = generation,
      requestedWorkspace = workspace.value;
    const result = await request('/api/runtime/sessions?limit=20&quick_entry=1&multi_agent_mode=0');
    if (ticket !== generation || requestedWorkspace !== workspace.value) return;
    sessions.value = (result.conversations || result.sessions || result.data?.sessions || [])
      .filter((item: any) => item.quick_entry === true && !item.multi_agent_mode)
      .map((item: any) => ({ ...item, conversation_id: item.conversation_id || item.id }));
  }
  async function restoreWorkspace() {
    resetPresentation();
    const ticket = generation;
    task.value = '';
    running.value = false;
    reply.value = '';
    tools.value = [];
    approvals.value = [];
    offset = 0;
    const saved = await loadDraft(storageKey());
    if (ticket !== generation) return;
    conversation.value = saved.conversation || '';
    draft.value = saved.draft || '';
    images.value = saved.images || [];
    await loadSessions();
    if (ticket !== generation) return;
    if (conversation.value) await restoreConversation(conversation.value);
  }
  async function restoreConversation(id: string) {
    resetPresentation();
    const ticket = generation;
    const history = await request(`/api/runtime/sessions/${encodeURIComponent(id)}/history`);
    if (ticket !== generation) return;
    if (
      history.conversation?.metadata?.quick_entry !== true ||
      history.conversation?.metadata?.multi_agent_mode
    ) {
      conversation.value = '';
      task.value = '';
      running.value = false;
      streaming.value = false;
      reply.value = '';
      error.value = '';
      tools.value = [];
      approvals.value = [];
      offset = 0;
      await save();
      return;
    }
    const messages =
      history.messages || history.data?.messages || history.conversation?.messages || [];
    const last = [...messages]
      .reverse()
      .find(
        (item: any) => item.role === 'assistant' && typeof item.content === 'string' && item.content
      );
    const status = await request(`/api/conversations/${encodeURIComponent(id)}/running-status`);
    if (ticket !== generation) return;
    // Keep the previous panel intact while loading, then commit the new view.
    conversation.value = id;
    error.value = '';
    tools.value = [];
    approvals.value = [];
    collapsed.value = false;
    offset = 0;
    task.value = status.data?.main_task_id || '';
    running.value = Boolean(task.value);
    reply.value = running.value ? '' : last?.content || '';
    save();
    if (running.value) void poll(generation);
  }
  async function setWorkspace(id: string) {
    await save();
    workspace.value = id;
    config.value = { ...config.value, ...(await bridge.configure({ workspace: id })) };
    await restoreWorkspace();
  }
  async function newChat() {
    await save();
    resetPresentation();
    error.value = '';
    conversation.value = '';
    task.value = '';
    running.value = false;
    streaming.value = false;
    draft.value = '';
    images.value = [];
    reply.value = '';
    tools.value = [];
    approvals.value = [];
    collapsed.value = false;
    save();
  }
  async function send() {
    if (sending.value || (!draft.value.trim() && !images.value.length)) return;
    sending.value = true;
    error.value = '';
    const message = draft.value.trim(),
      media = images.value.map((data_url) => ({ data_url, kind: 'image' }));
    try {
      if (!workspace.value) throw new Error(t('quickEntry.noWorkspaces'));
      if (!model.value) throw new Error(t('quickEntry.noModels'));
      if (running.value && task.value) {
        await request(`/api/tasks/${encodeURIComponent(task.value)}/runtime_guidance`, 'POST', {
          message,
          images: media
        });
      } else {
        if (!conversation.value) {
          const created = await request('/api/runtime/sessions', 'POST', {
            work_mode: 'execute',
            permission_mode: 'unrestricted',
            execution_mode: 'sandbox',
            model_key: model.value,
            quick_entry: true
          });
          conversation.value = created.conversation_id || created.data?.conversation_id;
        }
        const result = await request('/api/tasks', 'POST', {
          message,
          images: media,
          conversation_id: conversation.value,
          model_key: model.value,
          message_source: 'quick_entry'
        });
        task.value = result.data.task_id;
        offset = 0;
        resetPresentation();
        reply.value = '';
        tools.value = [];
        collapsed.value = false;
        streaming.value = false;
        running.value = true;
        void poll(generation);
      }
      draft.value = '';
      images.value = [];
      save();
    } catch (exception) {
      error.value = String(exception);
    } finally {
      sending.value = false;
    }
  }
  function handleEvent(type: string, data: any) {
    const ticket = generation;
    presentation = presentation.then(async () => {
      if (ticket !== generation) return;
      if (
        [
          'thinking_start',
          'thinking_chunk',
          'text_start',
          'ai_message_start',
          'text_chunk',
          'tool_preparing',
          'tool_start'
        ].includes(type)
      ) {
        const wait = Math.max(0, finishedAt + 2000 - Date.now());
        if (wait) await new Promise((resolve) => setTimeout(resolve, wait));
      }
      if (ticket === generation) applyEvent(type, data);
    });
  }
  function applyEvent(type: string, data: any) {
    if (type === 'thinking_start' || type === 'thinking_chunk') {
      thinking.value = true;
      reply.value = '';
      tools.value = [];
      streaming.value = false;
    }
    if (
      [
        'thinking_end',
        'text_start',
        'ai_message_start',
        'text_chunk',
        'tool_preparing',
        'tool_start',
        'quick_finished',
        'task_stopped',
        'error'
      ].includes(type)
    )
      thinking.value = false;
    if (type === 'text_start' || type === 'ai_message_start') {
      reply.value = '';
      streaming.value = true;
    }
    if (type === 'text_chunk') {
      streaming.value = true;
      tools.value = [];
      reply.value += String(data.content || '');
    }
    if (type === 'tool_preparing' || type === 'tool_start') {
      const id = data.preparing_id || data.tool_call_id || data.id || data.action_id || data.name;
      const title = data.arguments?.intent || data.intent || data.name;
      const apply = () => {
        if (!tools.value.some((item) => !item.done)) {
          tools.value = [];
          reply.value = '';
          streaming.value = false;
        }
        const old = tools.value.find((item) => item.id === id);
        if (old) Object.assign(old, { title, name: data.name, done: false });
        else tools.value.push({ id, title, name: data.name, done: false });
      };
      apply();
    }
    if (type === 'tool_update_action' || type === 'update_action') {
      if (['completed', 'done', 'success', 'failed', 'error', 'cancelled'].includes(data.status)) {
        const item = tools.value.find(
          (entry) =>
            entry.id === (data.preparing_id || data.tool_call_id || data.id || data.action_id)
        );
        if (item) {
          item.done = true;
          item.failed = ['failed', 'error', 'cancelled'].includes(data.status);
        }
        if (tools.value.length && tools.value.every((entry) => entry.done)) finishedAt = Date.now();
      }
    }
    if (type === 'quick_finished') {
      streaming.value = false;
      if (data.reply) reply.value = data.reply;
      if (data.error && !error.value) {
        error.value = String(data.error);
        collapsed.value = false;
      }
    }
    if (type === 'task_stopped') {
      tools.value.forEach((item) => {
        item.done = true;
        item.failed = true;
      });
    }
    if (type === 'error') {
      error.value = String(data.message || data.error || '');
      streaming.value = false;
      collapsed.value = false;
    }
  }
  async function poll(ticket: number) {
    if (ticket !== generation || !task.value) return;
    try {
      const result = await request(`/api/tasks/${encodeURIComponent(task.value)}?from=${offset}`);
      if (ticket !== generation) return;
      const data = result.data;
      if (data.window_start > offset) {
        hadGap = true;
        offset = data.window_start;
      }
      for (const event of data.events || []) handleEvent(event.type, event.data || {});
      offset = data.next_offset;
      await refreshApprovals();
      if (ticket !== generation) return;
      if (
        [
          'succeeded',
          'completed',
          'failed',
          'cancelled',
          'canceled',
          'stopped',
          'done',
          'error'
        ].includes(data.status)
      ) {
        let finalReply = '';
        if (hadGap && data.status === 'succeeded') {
          const history = await request(
            `/api/runtime/sessions/${encodeURIComponent(conversation.value)}/history`
          );
          const messages = history.conversation?.messages || history.messages || [];
          finalReply =
            [...messages]
              .reverse()
              .find(
                (item: any) =>
                  item.role === 'assistant' && typeof item.content === 'string' && item.content
              )?.content || '';
        }
        if (ticket !== generation) return;
        const failure = ['failed', 'error'].includes(data.status) ? data.error : '';
        running.value = false;
        handleEvent('quick_finished', { reply: finalReply, error: failure });
        task.value = '';
        save();
        await loadSessions();
        return;
      }
    } catch (exception) {
      error.value = String(exception);
    }
    if (ticket === generation) pollTimer = setTimeout(() => void poll(ticket), 350);
  }
  async function refreshApprovals() {
    if (!conversation.value) return;
    const ticket = generation;
    const values = await Promise.all(
      ['tool-approvals', 'plan-approvals', 'user-questions'].map(async (kind) => {
        const result = await request(
          `/api/${kind}/pending?conversation_id=${encodeURIComponent(conversation.value)}`
        );
        return (result.data?.items || result.items || []).map((item: any) => ({ ...item, kind }));
      })
    );
    if (ticket === generation) approvals.value = values.flat();
  }
  async function answer(item: any, allow: boolean, text = '', selectedOptionId = '') {
    const id = item.approval_id || item.question_id || item.id;
    const suffix = item.kind === 'tool-approvals' ? 'decision' : 'answer';
    const body =
      item.kind === 'tool-approvals'
        ? { decision: allow ? 'approved' : 'rejected' }
        : item.kind === 'plan-approvals'
          ? { approved: allow }
          : { text, ...(selectedOptionId ? { selected_option_id: selectedOptionId } : {}) };
    await request(`/api/${item.kind}/${encodeURIComponent(id)}/${suffix}`, 'POST', body);
    await refreshApprovals();
  }
  async function reconcile() {
    if (!conversation.value || !workspace.value || sending.value) return;
    const ticket = generation;
    const id = conversation.value;
    try {
      const status = await request(`/api/conversations/${encodeURIComponent(id)}/running-status`);
      if (ticket !== generation) return;
      const active = status.data?.main_task_id || '';
      if (active && active !== task.value) {
        resetPresentation();
        task.value = active;
        running.value = true;
        offset = 0;
        reply.value = '';
        tools.value = [];
        collapsed.value = false;
        void poll(generation);
      } else if (!active && running.value) await restoreConversation(id);
    } catch (exception) {
      error.value = String(exception);
    }
  }
  const idleTimer = setInterval(() => {
    if (!running.value && !sending.value) void reconcile();
  }, 2000);
  async function stop() {
    try {
      if (task.value)
        await request(`/api/tasks/${encodeURIComponent(task.value)}/cancel`, 'POST', {});
    } catch (exception) {
      error.value = String(exception);
    }
  }
  function dispose() {
    resetPresentation();
    clearInterval(idleTimer);
    void save();
  }
  return {
    bridge,
    draft,
    images,
    workspace,
    model,
    conversation,
    task,
    running,
    sending,
    reply,
    tools,
    error,
    workspaces,
    models,
    sessions,
    config,
    approvals,
    collapsed,
    streaming,
    thinking,
    modelLabel,
    workspaceLabel,
    initialize,
    syncPreferences,
    setWorkspace,
    newChat,
    send,
    stop,
    save,
    loadSessions,
    restoreConversation,
    answer,
    reconcile,
    dispose
  };
}
