import { computed, onBeforeUnmount, ref } from 'vue';
import { chatApi, command, loadView, operationId } from './api';
import { activeStatuses, pathOf, textOf } from './types';
import type { ChatDocument, ChatMessage, ChatSettings, ChatView, MediaRef } from './types';
import { prepareGeneratingLabels } from '@/utils/generatingLabel';

export function useChat(onTaskError: (data: Record<string, any>) => void = () => {}) {
  const settings = ref<ChatSettings | null>(null);
  const document = ref<ChatDocument | null>(null);
  const display = ref<ChatView['display'] | null>(null);
  const history = ref<Array<{ id: string; title: string }>>([]);
  const historyQuery = ref('');
  const viewingLeaf = ref<string | null>(null);
  const error = ref('');
  const loading = ref(false);
  const sending = ref(false);
  const model = ref('');
  const thinking = ref(true);
  const effort = ref<string | null>(null);
  const network = ref(true);
  let epoch = 0;
  let listSequence = 0;
  let navigationOwns = () => true;
  let disposed = false;
  let pollTimer = 0;
  let fatalNotifiedTaskId = '';
  const task = computed(() => display.value?.task);
  const running = computed(() => !!task.value && activeStatuses.has(task.value.status));
  const busy = computed(() => running.value || sending.value || loading.value);
  const cid = computed(() => document.value?.id || '');
  const path = computed(() => (document.value ? pathOf(document.value, viewingLeaf.value) : []));
  const messages = computed(() => {
    const result = path.value.filter((message) => message.metadata?.visibility !== 'hidden');
    if (!viewingLeaf.value && running.value) {
      const saved = new Set(result.map((message) => message.message_id));
      const live = new Map<string, ChatMessage>();
      for (const projected of display.value?.messages || []) {
        for (const action of projected.actions || []) {
          const id = action.responseMessageId;
          if (!id || saved.has(id) || !['text', 'thinking'].includes(action.type)) continue;
          const item: ChatMessage = live.get(id) || {
            message_id: id,
            role: 'assistant',
            content: '',
            live: true
          };
          if (action.type === 'thinking')
            item.reasoning_content = (item.reasoning_content || '') + action.content;
          else item.content = String(item.content) + action.content;
          live.set(id, item);
        }
      }
      result.push(...live.values());
    }
    return result;
  });
  const owns = (token: number) => !disposed && token === epoch && navigationOwns();
  const report = (cause: unknown) => {
    error.value = String((cause as Error)?.message || cause);
  };

  async function list(query = '') {
    const sequence = ++listSequence;
    const items = await chatApi<Array<{ id: string; title: string }>>(
      `/conversations?q=${encodeURIComponent(query)}`
    );
    if (!disposed && sequence === listSequence) {
      historyQuery.value = query;
      history.value = items;
    }
  }
  function cancelList() {
    listSequence++;
  }
  async function initialize() {
    settings.value = await chatApi<ChatSettings>('/settings');
    resetSettings();
    await list();
  }
  function resetSettings() {
    const defaults = settings.value?.settings;
    model.value = defaults?.chat_default_model || '';
    thinking.value = defaults?.chat_default_thinking_mode ?? true;
    effort.value = defaults?.chat_default_reasoning_effort || null;
    network.value = defaults?.chat_network_enabled ?? true;
  }
  function apply(view: ChatView, token: number) {
    if (!owns(token)) return;
    prepareGeneratingLabels(view.display.messages || [], display.value?.messages);
    document.value = view.document;
    display.value = view.display;
    const metadata = view.document.metadata;
    model.value = metadata.model_key || model.value;
    thinking.value = metadata.thinking_mode ?? thinking.value;
    effort.value = metadata.reasoning_effort ?? null;
    network.value = metadata.network_enabled;
  }
  function schedule(token: number, initial = false) {
    window.clearTimeout(pollTimer);
    if (owns(token) && (running.value || (initial && task.value?.status === 'failed')))
      pollTimer = window.setTimeout(() => void poll(token), 250);
  }
  async function poll(token: number) {
    const current = task.value;
    if (!owns(token) || !current) return;
    const taskId = current.task_id;
    try {
      const cursor = display.value?.next_event_idx || 0;
      const result = await chatApi(`/tasks/${taskId}?from=${cursor}`);
      if (!owns(token) || task.value?.task_id !== taskId) return;
      if (result.view) apply(result.view, token);
      for (const event of result.events || []) {
        if (event.idx < cursor || !['error', 'quota_exceeded'].includes(event.type)) continue;
        const data =
          event.type === 'quota_exceeded'
            ? { ...event.data, error_type: 'quota_exceeded' }
            : event.data;
        onTaskError(data);
        if (data.fatal || event.type === 'quota_exceeded') fatalNotifiedTaskId = taskId;
      }
      if (!activeStatuses.has(result.status)) {
        if (result.error && fatalNotifiedTaskId !== taskId) {
          onTaskError({ message: result.error });
          fatalNotifiedTaskId = taskId;
        }
        void list().catch(report);
      }
    } catch (cause) {
      if (owns(token)) report(cause);
    }
    schedule(token);
  }
  async function load(id: string, owner: () => boolean = () => true) {
    const token = ++epoch;
    navigationOwns = owner;
    window.clearTimeout(pollTimer);
    loading.value = true;
    sending.value = false;
    error.value = '';
    viewingLeaf.value = null;
    try {
      if (id) apply(await loadView(id), token);
      else if (owns(token)) {
        document.value = null;
        display.value = null;
        resetSettings();
      }
    } finally {
      if (owns(token)) loading.value = false;
    }
    schedule(token, true);
    return owns(token);
  }
  function leave() {
    epoch++;
    window.clearTimeout(pollTimer);
  }
  async function reload() {
    const token = epoch;
    if (cid.value) apply(await loadView(cid.value), token);
  }
  async function send(text: string, media: MediaRef[], extra: Record<string, unknown> = {}) {
    if (busy.value) return null;
    const token = epoch;
    sending.value = true;
    error.value = '';
    const payload = {
      operation_id: operationId(),
      conversation_id: cid.value || null,
      message: text,
      parent_id: path.value[path.value.length - 1]?.message_id || null,
      model_key: model.value,
      thinking_mode: thinking.value,
      settings: { reasoning_effort: effort.value, network_enabled: network.value },
      images: media.filter((item) => item.kind === 'image'),
      videos: media.filter((item) => item.kind === 'video'),
      ...extra
    };
    try {
      const result = await command('/tasks', payload);
      // Navigation during acceptance must not make the new task own this page.
      if (!owns(token)) return { ...result, ownsPage: false };
      viewingLeaf.value = null;
      apply(await loadView(result.conversation_id), token);
      if (owns(token)) {
        const url = `/chat/${result.conversation_id.replace(/^conv_/, '')}`;
        window.history.replaceState({}, '', url);
        schedule(token, true);
        void list().catch(report);
      }
      return { ...result, ownsPage: owns(token) };
    } catch (cause) {
      if (owns(token)) report(cause);
      throw cause;
    } finally {
      if (owns(token)) sending.value = false;
    }
  }
  async function save(message: ChatMessage, content: string) {
    const token = epoch;
    await command(`/conversations/${cid.value}/edit`, {
      operation_id: operationId(),
      message_id: message.message_id,
      content,
      revision: document.value?.metadata.chat.revision
    });
    if (owns(token)) await reload();
  }
  async function regenerate(message: ChatMessage) {
    const turn = document.value?.metadata.chat.turns.find(
      (item) => item.id === message.metadata?.chat?.turn_id
    );
    if (!turn) return;
    return send('', [], { regenerate_turn_id: turn.id, parent_id: null });
  }
  function siblings(message: ChatMessage): ChatMessage[] {
    return (
      document.value?.messages.filter(
        (item) =>
          item.role === message.role &&
          item.metadata?.chat?.parent_id === message.metadata?.chat?.parent_id
      ) || []
    );
  }
  function browse(messageId: string) {
    if (!document.value) return;
    let id = messageId;
    const visited = new Set<string>();
    while (!visited.has(id)) {
      visited.add(id);
      const children = document.value.messages.filter(
        (item) => item.metadata?.chat?.parent_id === id
      );
      if (!children.length) break;
      id = children[children.length - 1].message_id;
    }
    viewingLeaf.value = id;
  }
  function turnVersions(message: ChatMessage) {
    const turn = document.value?.metadata.chat.turns.find(
      (item) => item.id === message.metadata?.chat?.turn_id
    );
    return turn
      ? document.value!.metadata.chat.turns.filter((item) => item.parent_id === turn.parent_id)
      : [];
  }
  function finalMessage(message: ChatMessage) {
    const turn = document.value?.metadata.chat.turns.find(
      (item) => item.id === message.metadata?.chat?.turn_id
    );
    return (
      !!turn &&
      turn.status !== 'running' &&
      turn.tip_id === message.message_id &&
      !message.tool_calls?.length
    );
  }
  async function toggleNetwork() {
    const token = epoch;
    const enabled = !network.value;
    if (cid.value)
      await command(`/conversations/${cid.value}/network`, {
        operation_id: operationId(),
        enabled
      });
    if (owns(token)) network.value = enabled;
  }
  async function configure(patch: Record<string, unknown>) {
    const token = epoch;
    if (cid.value) await command(`/conversations/${cid.value}/settings`, patch, 'PATCH');
    if (!owns(token)) return;
    if ('model_key' in patch) model.value = String(patch.model_key);
    if ('thinking_mode' in patch) thinking.value = Boolean(patch.thinking_mode);
    if ('reasoning_effort' in patch) effort.value = patch.reasoning_effort as string | null;
  }
  async function stop() {
    if (task.value) await command(`/tasks/${task.value.task_id}/cancel`, {});
  }
  onBeforeUnmount(() => {
    disposed = true;
    leave();
  });
  return {
    settings,
    document,
    display,
    history,
    historyQuery,
    cancelList,
    viewingLeaf,
    error,
    loading,
    busy,
    running,
    model,
    thinking,
    effort,
    network,
    cid,
    path,
    messages,
    task,
    initialize,
    list,
    load,
    leave,
    reload,
    send,
    save,
    regenerate,
    siblings,
    browse,
    turnVersions,
    finalMessage,
    toggleNetwork,
    configure,
    stop,
    report,
    textOf
  };
}
